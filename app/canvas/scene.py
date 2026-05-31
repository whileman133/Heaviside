"""
SchematicScene — the interactive QGraphicsScene (spec §6).

The scene owns the authoritative :class:`Schematic` model and an
:class:`UndoStack`. Every visible :class:`ComponentItem` / :class:`WireItem` is
a *view* of a model object; the scene keeps the two in sync. The UI layer
(phase 9) never mutates the model directly — it pushes commands through this
scene and listens to the scene's signals.

Interaction modes (spec §6.1), mutually exclusive:

    SELECT  – default; click to select, drag to move, rubber-band select.
    PLACE   – a ghost component follows the cursor; click places it.
    WIRE    – click pins/points to route a Manhattan wire.
    PAN     – handled by the view (space/middle-drag); the scene only tracks it.

Coordinate systems
------------------
* **Schematic coords** (GU): what the model stores. Snap granularity 0.5 GU.
* **Scene/pixel coords**: schematic coords × ``GRID_PX``. All items live here.

Helpers :meth:`scene_to_gu` / :meth:`gu_to_scene` convert between them, and
:meth:`snap_gu` rounds to the nearest 0.5 GU.
"""

from __future__ import annotations

import uuid
from enum import Enum, auto

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
)

from app.canvas.commands import (
    DeleteCommand,
    MacroCommand,
    MirrorCommand,
    MoveCommand,
    MoveWireVertexCommand,
    PlaceCommand,
    RotateCommand,
    SplitWireCommand,
    UndoStack,
    WireCommand,
    reshape_wire_points,
)
from app.canvas.items import (
    ComponentItem,
    JunctionItem,
    OpenCircleItem,
    WireItem,
    WirePreviewItem,
)
from app.canvas.style import GRID_PX
from app.components.registry import ITEM_CLASSES, REGISTRY
from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    junction_points,
    open_endpoints,
    simplify_points,
    wire_splits_at,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SNAP_GU: float = 0.5
"""Half-grid snap granularity (spec §3.1)."""

PIN_SNAP_GU: float = 0.25
"""Wire endpoints snap to a pin within this radius (spec §6.4)."""

VERTEX_HIT_GU: float = 0.3
"""A wire vertex is grabbable for dragging within this radius of the cursor."""

PIN_GRAB_GU: float = 0.3
"""Auto-start a wire only when the click is within this radius of a free pin.

Tighter than a component's body half-extent, so a press near a component's
centre falls through to selection/drag while a press right on a pin wires.
"""

_GRID_NORMAL = QColor("#FFD0D0D0")   # integer grid lines
_GRID_SUB = QColor("#22808080")      # 0.5 GU sub-grid lines (reduced opacity)


class Mode(Enum):
    """Canvas interaction mode (spec §6.1)."""

    SELECT = auto()
    PLACE = auto()
    WIRE = auto()
    PAN = auto()


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

class SchematicScene(QGraphicsScene):
    """Interactive scene holding the live schematic and undo history."""

    # -- signals (UI layer connects to these) ------------------------------
    schematic_changed = Signal()
    """Emitted after any command mutates the model."""

    mode_changed = Signal(object)
    """Emitted with the new :class:`Mode` when the interaction mode changes."""

    cursor_moved = Signal(float, float)
    """Emitted with the snapped (x, y) cursor position in GU."""

    selection_changed_gu = Signal(list)
    """Emitted with the list of selected Component ids when selection changes."""

    def __init__(self, schematic: Schematic | None = None, parent=None):
        super().__init__(parent)
        self._schematic = schematic or Schematic(version="0.1", name="untitled")
        self._stack = UndoStack(self._schematic)

        self._mode = Mode.SELECT
        self._panning = False

        # kind -> item maps for sync
        self._comp_items: dict[str, ComponentItem] = {}
        self._wire_items: dict[str, WireItem] = {}
        # junction coordinate (gu) -> dot item
        self._junction_items: dict[tuple[float, float], JunctionItem] = {}
        # open-endpoint coordinate (gu) -> open-circle item
        self._open_circle_items: dict[tuple[float, float], OpenCircleItem] = {}

        # Placement state
        self._place_kind: str | None = None
        self._place_rotation: int = 0
        self._place_mirror: bool = False
        self._ghost: ComponentItem | None = None

        # Wire-routing state
        self._wire_pts: list[tuple[float, float]] = []
        self._wire_vfirst = False  # Shift toggles vertical-first (read live)
        self._wire_preview: WirePreviewItem | None = None

        # Drag-move bookkeeping: id -> position at drag start (GU)
        self._drag_start: dict[str, tuple[float, float]] = {}

        # Wire-vertex drag: (wire_id, index, original_point_gu) or None.
        self._vertex_drag: tuple[str, int, tuple[float, float]] | None = None

        # Wire ids currently showing a drag-preview (during a component drag),
        # so they can be cleared precisely on release.
        self._previewed_wire_ids: set[str] = set()

        self.setSceneRect(-20 * GRID_PX, -20 * GRID_PX, 200 * GRID_PX, 200 * GRID_PX)
        self._rebuild_items()

        self.selectionChanged.connect(self._on_selection_changed)

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def schematic(self) -> Schematic:
        return self._schematic

    @property
    def undo_stack(self) -> UndoStack:
        return self._stack

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def is_gesture_in_progress(self) -> bool:
        """True while a drag or in-progress wire is active.

        The preview pipeline checks this to avoid compiling mid-gesture.
        Placement mode is intentionally excluded — schematic_changed only fires
        on actual commits there, not on ghost movement, so previews should still
        trigger after each placement.
        """
        return (
            bool(self._drag_start)          # component drag in progress
            or self._vertex_drag is not None  # wire vertex drag in progress
            or bool(self._wire_pts)           # wire being drawn (has anchored points)
        )


    def set_schematic(self, schematic: Schematic) -> None:
        """Replace the document (e.g. after File ▸ Open). Clears undo history."""
        self._schematic = schematic
        self._stack = UndoStack(schematic)
        self.set_mode(Mode.SELECT)
        self._rebuild_items()
        self.schematic_changed.emit()

    # ------------------------------------------------------------------
    # Coordinate helpers
    # ------------------------------------------------------------------

    @staticmethod
    def snap_gu(value: float) -> float:
        """Round a GU value to the nearest 0.5 GU."""
        return round(value / SNAP_GU) * SNAP_GU

    @staticmethod
    def scene_to_gu(pt: QPointF) -> tuple[float, float]:
        return (pt.x() / GRID_PX, pt.y() / GRID_PX)

    @staticmethod
    def gu_to_scene(x: float, y: float) -> QPointF:
        return QPointF(x * GRID_PX, y * GRID_PX)

    def snap_point_gu(self, pt: QPointF) -> tuple[float, float]:
        x, y = self.scene_to_gu(pt)
        return (self.snap_gu(x), self.snap_gu(y))

    # ------------------------------------------------------------------
    # Mode management
    # ------------------------------------------------------------------

    def set_mode(self, mode: Mode) -> None:
        if mode == self._mode:
            return
        # Tear down any in-progress interaction state.
        self._cancel_placement()
        self._cancel_wire()
        self._mode = mode
        self._apply_item_flags()
        self.mode_changed.emit(mode)

    def start_placement(self, kind: str) -> None:
        """Enter PLACE mode for *kind* (called when a palette entry is clicked)."""
        if kind not in REGISTRY:
            raise KeyError(f"unknown component kind {kind!r}")
        self._cancel_wire()
        self._place_kind = kind
        self._place_rotation = 0
        self._place_mirror = False
        self._mode = Mode.PLACE
        self._apply_item_flags()
        self._spawn_ghost(kind)
        self.mode_changed.emit(Mode.PLACE)

    def enter_wire_mode(self) -> None:
        self.set_mode(Mode.WIRE)

    def enter_select_mode(self) -> None:
        self.set_mode(Mode.SELECT)

    def set_panning(self, panning: bool) -> None:
        """The view calls this while a pan gesture is active."""
        self._panning = panning

    # ------------------------------------------------------------------
    # Undo / redo passthrough
    # ------------------------------------------------------------------

    def undo(self) -> None:
        if self._stack.undo() is not None:
            self._rebuild_items()
            self.schematic_changed.emit()

    def redo(self) -> None:
        if self._stack.redo() is not None:
            self._rebuild_items()
            self.schematic_changed.emit()

    def _push(self, command) -> None:
        self._stack.push(command)
        self._rebuild_items()
        self.schematic_changed.emit()

    # ------------------------------------------------------------------
    # Commands triggered by UI/keyboard
    # ------------------------------------------------------------------

    def place_component(
        self,
        kind: str,
        position: tuple[float, float],
        rotation: int = 0,
        mirror: bool = False,
        labels: dict[str, str] | None = None,
    ) -> Component:
        """Place a component at *position* (GU) via an undoable PlaceCommand."""
        comp = Component(
            id=str(uuid.uuid4()),
            kind=kind,
            position=(self.snap_gu(position[0]), self.snap_gu(position[1])),
            rotation=rotation,
            labels=labels or {},
            mirror=mirror,
        )
        self._push(PlaceCommand(comp))
        return comp

    def _split_commands_for(
        self,
        points: set[tuple[float, float]],
        exclude_wire_id: str | None = None,
    ) -> list[SplitWireCommand]:
        """Build SplitWireCommands for any *points* that land mid-segment.

        For each point that lies strictly inside an existing wire's segment
        (per :func:`wire_splits_at`), produce a command inserting a vertex
        there so the connection becomes real topology (and a junction dot
        appears). *exclude_wire_id* skips a wire that must not split itself
        (e.g. the wire whose own vertex is being dragged).
        """
        cmds: list[SplitWireCommand] = []
        seen: set[tuple[str, tuple[float, float]]] = set()
        for pt in points:
            for wire_id, idx in wire_splits_at(self._schematic, pt):
                if wire_id == exclude_wire_id:
                    continue
                key = (wire_id, pt)
                if key in seen:
                    continue
                seen.add(key)
                cmds.append(SplitWireCommand(wire_id, idx, pt))
        return cmds

    def add_wire(self, points: list[tuple[float, float]]) -> Wire | None:
        """Add a wire via an undoable WireCommand. Needs ≥2 points.

        If the new wire's endpoints land in the *middle* of an existing wire's
        segment, that wire is split (a vertex inserted) so the connection is
        real topology and a junction dot appears. The split(s) and the new wire
        are committed as one undoable MacroCommand.
        """
        pts = simplify_points(list(points))
        if len(pts) < 2:
            return None
        wire = Wire(id=str(uuid.uuid4()), points=pts)

        # Mid-segment connections at the new wire's endpoints (T-connections).
        split_cmds = self._split_commands_for({pts[0], pts[-1]})

        if split_cmds:
            self._push(MacroCommand(split_cmds + [WireCommand(wire)], label="Wire"))
        else:
            self._push(WireCommand(wire))
        return wire

    def delete_selected(self) -> None:
        """Delete the current selection via DeleteCommand.

        Removes selected components (and any wires connected to their pins) and
        any directly-selected wires.
        """
        comp_ids = self.selected_component_ids()
        wire_ids = self.selected_wire_ids()
        if comp_ids or wire_ids:
            self._push(DeleteCommand(comp_ids, wire_ids))

    def rotate_component(self, component_id: str, new_rotation: int) -> None:
        """Set the rotation of a component via an undoable RotateCommand."""
        self._push(RotateCommand(component_id, new_rotation))

    def rotate_selected_cw(self) -> None:
        """Rotate selected components 90° CW, or rotate the placement ghost."""
        if self._mode == Mode.PLACE:
            self._place_rotation = (self._place_rotation + 90) % 360
            self._update_ghost_transform()
            return
        for cid in self.selected_component_ids():
            comp = next((c for c in self._schematic.components if c.id == cid), None)
            if comp is not None:
                self._push(RotateCommand(cid, (comp.rotation + 90) % 360))

    def mirror_component(self, component_id: str, new_mirror: bool) -> None:
        """Set the mirror state of a component via an undoable MirrorCommand."""
        self._push(MirrorCommand(component_id, new_mirror))

    def mirror_selected(self) -> None:
        """Toggle mirror on selected components, or mirror the placement ghost."""
        if self._mode == Mode.PLACE:
            self._place_mirror = not self._place_mirror
            self._update_ghost_transform()
            return
        for cid in self.selected_component_ids():
            comp = next((c for c in self._schematic.components if c.id == cid), None)
            if comp is not None:
                self._push(MirrorCommand(cid, not comp.mirror))

    def _update_ghost_transform(self) -> None:
        """Rebuild the ghost item to reflect the current _place_rotation/_place_mirror."""
        if self._ghost is None or self._place_kind is None:
            return
        pos = self._ghost.pos()  # preserve current screen position
        self._spawn_ghost(self._place_kind)
        self._ghost.setPos(pos)

    def nudge_selected(self, dx_gu: float, dy_gu: float) -> None:
        """Move selected components by a delta via MoveCommand (arrow keys)."""
        ids = self.selected_component_ids()
        if ids:
            self._push(MoveCommand(ids, (dx_gu, dy_gu)))

    def selected_component_ids(self) -> list[str]:
        out = []
        for item in self.selectedItems():
            if isinstance(item, ComponentItem):
                out.append(item.component.id)
        return out

    def selected_wire_ids(self) -> list[str]:
        out = []
        for item in self.selectedItems():
            if isinstance(item, WireItem):
                out.append(item.wire.id)
        return out

    # ------------------------------------------------------------------
    # Model ↔ item synchronisation
    # ------------------------------------------------------------------

    def _rebuild_items(self) -> None:
        """Reconcile the scene's graphics items with the current model.

        This is a **diff**, not a teardown: items whose model object still
        exists are kept and merely refreshed (position / rotation / wire
        geometry); items are created only for genuinely new ids and removed
        only for ids that vanished from the model.

        Why this matters: a full destroy-and-recreate run *inside* a mouse
        handler (e.g. after a drag pushes a MoveCommand) would delete the very
        item Qt is still finalizing its grab on, corrupting the scene's
        interaction state and leaving the next rebuilt item un-painted — the
        "component disappears on the second move" bug. Reusing live items keeps
        Qt's grab valid across the command.
        """
        model_comp_ids = {c.id for c in self._schematic.components}
        model_wire_ids = {w.id for w in self._schematic.wires}

        # --- remove items whose model object is gone ----------------------
        for cid in list(self._comp_items):
            if cid not in model_comp_ids:
                self.removeItem(self._comp_items.pop(cid))
        for wid in list(self._wire_items):
            if wid not in model_wire_ids:
                self.removeItem(self._wire_items.pop(wid))

        # --- add new / refresh existing component items -------------------
        for comp in self._schematic.components:
            item = self._comp_items.get(comp.id)
            if item is None:
                cls = ITEM_CLASSES.get(comp.kind, ComponentItem)
                item = cls(comp)
                self.addItem(item)
                self._comp_items[comp.id] = item
            else:
                # Refresh in place from the (possibly mutated) model object.
                item.component = comp
                item.setPos(self.gu_to_scene(*comp.position))
                t = QTransform()
                if comp.mirror:
                    t.scale(-1.0, 1.0)
                t.rotate(comp.rotation)
                item.setTransform(t)
                # Defensive: a reused item must always be left visible and
                # fully opaque — never leave a live component invisible.
                item.setVisible(True)
                item.setOpacity(1.0)
                item.update()

        # --- add new / refresh existing wire items ------------------------
        # A rebuild supersedes any in-flight drag ghost.
        self._previewed_wire_ids = set()
        pins = self._all_pin_positions()
        for wire in self._schematic.wires:
            item = self._wire_items.get(wire.id)
            if item is None:
                item = WireItem(wire)
                self.addItem(item)
                self._wire_items[wire.id] = item
            else:
                item.wire = wire
                item.clear_preview_points()
                item.prepareGeometryChange()
                item.update()
            # Mark which vertices are locked (endpoints sitting on a pin), so
            # the item only draws grab handles on draggable vertices.
            item.locked_indices = {
                i
                for i in range(len(wire.points))
                if not self.vertex_is_draggable(wire, i, pins)
            }

        # --- junction dots (3+ wires, or pin + 2 wires) -------------------
        wanted = junction_points(self._schematic)
        for coord in list(self._junction_items):
            if coord not in wanted:
                self.removeItem(self._junction_items.pop(coord))
        for coord in wanted:
            if coord not in self._junction_items:
                dot = JunctionItem()
                dot.setPos(self.gu_to_scene(*coord))
                self.addItem(dot)
                self._junction_items[coord] = dot

        # --- open-circle nodes (wire endpoints not on any component pin) ---
        wanted_oc = open_endpoints(self._schematic)
        for coord in list(self._open_circle_items):
            if coord not in wanted_oc:
                self.removeItem(self._open_circle_items.pop(coord))
        for coord in wanted_oc:
            if coord not in self._open_circle_items:
                oc = OpenCircleItem()
                oc.setPos(self.gu_to_scene(*coord))
                self.addItem(oc)
                self._open_circle_items[coord] = oc

        # Gate interactivity on the current mode (newly created items are
        # movable/selectable by default).
        self._apply_item_flags()

    def _apply_item_flags(self) -> None:
        """Enable component drag/selection only in SELECT mode.

        Components are draggable solely in SELECT mode. In PLACE / WIRE / PAN
        modes a press on a component must not grab-and-drag the graphics item:
        if it did, Qt would move the item out from under the model with no
        MoveCommand, leaving the item desynced until the next ``_rebuild_items``
        snaps it back (which reads on screen as the component "disappearing").
        Wire items follow the same rule for selection.
        """
        interactive = self._mode == Mode.SELECT
        for item in self._comp_items.values():
            item.setFlag(QGraphicsItem.ItemIsMovable, interactive)
            item.setFlag(QGraphicsItem.ItemIsSelectable, interactive)
            if not interactive:
                item.setSelected(False)
        for item in self._wire_items.values():
            item.setFlag(QGraphicsItem.ItemIsSelectable, interactive)
            if not interactive:
                item.setSelected(False)

    def _on_selection_changed(self) -> None:
        self.selection_changed_gu.emit(self.selected_component_ids())

    # ------------------------------------------------------------------
    # Placement ghost
    # ------------------------------------------------------------------

    def _spawn_ghost(self, kind: str) -> None:
        self._cancel_ghost()
        cls = ITEM_CLASSES.get(kind, ComponentItem)
        ghost_comp = Component(
            id="__ghost__", kind=kind, position=(0.0, 0.0),
            rotation=self._place_rotation, mirror=self._place_mirror, labels={}
        )
        ghost = cls(ghost_comp)
        ghost.set_ghost(True)
        ghost.setFlag(ghost.GraphicsItemFlag.ItemIsSelectable, False)
        ghost.setFlag(ghost.GraphicsItemFlag.ItemIsMovable, False)
        ghost.setZValue(1000)
        self.addItem(ghost)
        self._ghost = ghost

    def _move_ghost(self, gu: tuple[float, float]) -> None:
        if self._ghost is not None:
            self._ghost.setPos(self.gu_to_scene(*gu))

    def _cancel_ghost(self) -> None:
        if self._ghost is not None:
            self.removeItem(self._ghost)
            self._ghost = None

    def _cancel_placement(self) -> None:
        self._cancel_ghost()
        self._place_kind = None
        self._place_rotation = 0
        self._place_mirror = False

    # ------------------------------------------------------------------
    # Wire routing
    # ------------------------------------------------------------------

    def _nearest_pin_gu(self, gu: tuple[float, float]) -> tuple[float, float] | None:
        """Return the nearest pin within PIN_SNAP_GU of *gu*, else None."""
        gx, gy = gu
        best: tuple[float, float] | None = None
        best_d2 = PIN_SNAP_GU * PIN_SNAP_GU
        for comp in self._schematic.components:
            for px, py in _component_pin_positions(comp):
                d2 = (px - gx) ** 2 + (py - gy) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = (px, py)
        return best

    def wire_snap_target(
        self,
        gu: tuple[float, float],
        exclude_wire_id: str | None = None,
    ) -> tuple[tuple[float, float], bool]:
        """Resolve a wire endpoint for cursor position *gu* (already snapped).

        Snap priority: component pin → existing wire vertex → nearest point on
        an existing wire segment → bare 0.5 GU grid node. Snapping to a pin or
        to existing wire geometry forms a junction (and is treated as a
        "connectable" target). Returns ``(point, is_connectable)`` where the
        flag drives the preview marker (ring vs. plain dot) and termination.

        *exclude_wire_id* omits one wire from the wire-vertex / wire-segment
        snap — used while dragging a vertex so it does not snap to its own wire.
        """
        pin = self._nearest_pin_gu(gu)
        if pin is not None:
            return pin, True
        vtx = self._nearest_wire_vertex_gu(gu, exclude_wire_id)
        if vtx is not None:
            return vtx, True
        seg = self._nearest_wire_segment_point_gu(gu, exclude_wire_id)
        if seg is not None:
            return seg, True
        return gu, False

    def _nearest_wire_vertex_gu(
        self, gu: tuple[float, float], exclude_wire_id: str | None = None
    ) -> tuple[float, float] | None:
        """Nearest existing wire vertex within PIN_SNAP_GU of *gu*, else None."""
        gx, gy = gu
        best: tuple[float, float] | None = None
        best_d2 = PIN_SNAP_GU * PIN_SNAP_GU
        for wire in self._schematic.wires:
            if wire.id == exclude_wire_id:
                continue
            for px, py in wire.points:
                d2 = (px - gx) ** 2 + (py - gy) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = (px, py)
        return best

    def _nearest_wire_segment_point_gu(
        self, gu: tuple[float, float], exclude_wire_id: str | None = None
    ) -> tuple[float, float] | None:
        """Nearest point on an existing wire segment, snapped to 0.5 GU.

        Returns the snapped foot of the perpendicular from *gu* onto the closest
        Manhattan segment, but only if it is within PIN_SNAP_GU and lands on the
        segment interior. Lets a wire T into the middle of another wire.
        """
        gx, gy = gu
        best: tuple[float, float] | None = None
        best_d2 = PIN_SNAP_GU * PIN_SNAP_GU
        for wire in self._schematic.wires:
            if wire.id == exclude_wire_id:
                continue
            pts = wire.points
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                # Project onto the (axis-aligned) segment, then snap.
                if x0 == x1:                       # vertical segment
                    fx = x0
                    fy = min(max(gy, min(y0, y1)), max(y0, y1))
                elif y0 == y1:                     # horizontal segment
                    fy = y0
                    fx = min(max(gx, min(x0, x1)), max(x0, x1))
                else:                              # (shouldn't happen: Manhattan)
                    continue
                sx, sy = self.snap_gu(fx), self.snap_gu(fy)
                d2 = (sx - gx) ** 2 + (sy - gy) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = (sx, sy)
        return best

    def _wire_endpoint_positions(self) -> set[tuple[float, float]]:
        """All wire endpoint coordinates currently in the schematic."""
        out: set[tuple[float, float]] = set()
        for wire in self._schematic.wires:
            if wire.points:
                out.add(wire.points[0])
                out.add(wire.points[-1])
        return out

    def unconnected_pin_at(
        self, scene_pt: QPointF
    ) -> tuple[float, float] | None:
        """Return the pin under *scene_pt* only if a wire may be auto-started.

        Used to auto-start a wire when the user clicks a free pin in SELECT
        mode. Returns None (so the click falls through to normal selection /
        component drag) when:

        * the cursor is not tightly on a pin (within ``PIN_GRAB_GU``);
        * the nearest pin already has a wire endpoint on it.

        The tight grab radius (smaller than the body half-extent) is what keeps
        component dragging intact: a press near the *centre* of a component is
        not on a pin and falls through to selection/drag, while a press right at
        a free pin/lead end starts a wire.
        """
        gu = self.snap_point_gu(scene_pt)
        # Use the raw (unsnapped) distance to the pin so the grab is tight.
        rx, ry = self.scene_to_gu(scene_pt)
        pin = self._nearest_pin_gu(gu)
        if pin is None:
            return None
        if (pin[0] - rx) ** 2 + (pin[1] - ry) ** 2 > PIN_GRAB_GU * PIN_GRAB_GU:
            return None
        if pin in self._wire_endpoint_positions():
            return None
        return pin

    # -- wire vertex hit-testing -----------------------------------------

    def _all_pin_positions(self) -> set[tuple[float, float]]:
        pins: set[tuple[float, float]] = set()
        for comp in self._schematic.components:
            for p in _component_pin_positions(comp):
                pins.add(p)
        return pins

    def vertex_is_draggable(
        self, wire: Wire, index: int, pins: set[tuple[float, float]] | None = None
    ) -> bool:
        """A vertex is draggable unless it is an endpoint sitting on a pin.

        Endpoints that coincide with a component pin are owned by wire-following
        (they move with the component), so they are locked here. Intermediate
        vertices and free (non-pin) endpoints are draggable.
        """
        pts = wire.points
        if not (0 <= index < len(pts)):
            return False
        is_endpoint = index == 0 or index == len(pts) - 1
        if not is_endpoint:
            return True
        if pins is None:
            pins = self._all_pin_positions()
        return pts[index] not in pins

    def wire_vertex_at(
        self, scene_pt: QPointF
    ) -> tuple[str, int] | None:
        """Return the (wire_id, index) of a draggable vertex under *scene_pt*.

        Picks the nearest draggable vertex within VERTEX_HIT_GU; returns None if
        none qualifies. Endpoints on pins are skipped (not draggable).
        """
        gx, gy = self.scene_to_gu(scene_pt)
        pins = self._all_pin_positions()
        best: tuple[str, int] | None = None
        best_d2 = VERTEX_HIT_GU * VERTEX_HIT_GU
        for wire in self._schematic.wires:
            for i, (px, py) in enumerate(wire.points):
                d2 = (px - gx) ** 2 + (py - gy) ** 2
                if d2 <= best_d2 and self.vertex_is_draggable(wire, i, pins):
                    best_d2 = d2
                    best = (wire.id, i)
        return best

    def move_wire_vertex(
        self, wire_id: str, index: int, new_point: tuple[float, float]
    ) -> None:
        """Move a wire vertex via an undoable MoveWireVertexCommand.

        If the vertex is dropped in the *middle* of another wire's segment, that
        wire is split (a vertex inserted) so a junction is formed — bundled with
        the move as one undoable MacroCommand.
        """
        snapped = (self.snap_gu(new_point[0]), self.snap_gu(new_point[1]))
        # The dragged wire must not split itself.
        split_cmds = self._split_commands_for({snapped}, exclude_wire_id=wire_id)
        move_cmd = MoveWireVertexCommand(wire_id, index, snapped)
        if split_cmds:
            self._push(MacroCommand(split_cmds + [move_cmd], label="Move node"))
        else:
            self._push(move_cmd)

    def _route(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
        vfirst: bool | None = None,
    ) -> list[tuple[float, float]]:
        """Two-segment Manhattan route from a to b (spec §6.4).

        *vfirst* selects vertical-first routing; when None it falls back to the
        scene's ``_wire_vfirst`` flag. The mouse handlers pass the live Shift
        state so the preview and the committed wire agree.
        """
        if vfirst is None:
            vfirst = self._wire_vfirst
        ax, ay = a
        bx, by = b
        if ax == bx or ay == by:
            return [a, b]
        corner = (ax, by) if vfirst else (bx, ay)
        return [a, corner, b]

    # -- wire preview ghost ----------------------------------------------

    def _ensure_wire_preview(self) -> WirePreviewItem:
        if self._wire_preview is None:
            self._wire_preview = WirePreviewItem()
            self.addItem(self._wire_preview)
        return self._wire_preview

    def _refresh_wire_preview(
        self,
        cursor_gu: tuple[float, float] | None,
        cursor_is_pin: bool = False,
        vfirst: bool = False,
    ) -> None:
        """Update the in-progress wire ghost to follow *cursor_gu*."""
        if not self._wire_pts:
            self._cancel_wire_preview()
            return
        preview = self._ensure_wire_preview()
        if cursor_gu is None:
            preview.set_path(self._wire_pts, None)
            return
        # Pending leg from the last committed vertex to the cursor.
        legs = self._route(self._wire_pts[-1], cursor_gu, vfirst=vfirst)
        full = list(self._wire_pts) + legs[1:]
        # Show committed vertices as anchors; the cursor end carries the marker.
        preview.set_path(full[:-1], full[-1], cursor_is_pin)

    def _cancel_wire_preview(self) -> None:
        if self._wire_preview is not None:
            self.removeItem(self._wire_preview)
            self._wire_preview = None

    def _cancel_wire(self) -> None:
        self._wire_pts = []
        self._cancel_wire_preview()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        gu = self.snap_point_gu(event.scenePos())
        self.cursor_moved.emit(gu[0], gu[1])

        if self._mode == Mode.PLACE:
            self._move_ghost(gu)
            event.accept()
            return

        if self._mode == Mode.WIRE and self._wire_pts:
            target, is_pin = self.wire_snap_target(gu)
            vfirst = bool(event.modifiers() & Qt.ShiftModifier)
            self._refresh_wire_preview(target, is_pin, vfirst)
            event.accept()
            return

        if self._vertex_drag is not None:
            self._preview_vertex_drag(gu)
            event.accept()
            return

        # Let Qt move any dragged component items, then snap them to the grid
        # and ghost their connected wires.
        super().mouseMoveEvent(event)
        if self._mode == Mode.SELECT and self._drag_start:
            for cid in self._drag_start:
                item = self._comp_items.get(cid)
                if item is not None:
                    snapped = self.snap_point_gu(item.pos())
                    item.setPos(self.gu_to_scene(*snapped))
            self._preview_component_drag()

    def _preview_vertex_drag(self, gu: tuple[float, float]) -> None:
        """Live visual feedback while dragging a wire vertex (model untouched).

        Repaints the affected wire item with the dragged vertex moved to *gu*,
        inserting elbows on adjacent segments so the preview stays Manhattan —
        matching the behaviour of MoveWireVertexCommand. The model is only
        updated on release.
        """
        wire_id, idx, _orig = self._vertex_drag
        item = self._wire_items.get(wire_id)
        if item is None:
            return
        pts = list(item.wire.points)
        if not (0 <= idx < len(pts)):
            return
        pts[idx] = gu
        # Insert elbows on the two segments that touch the moved vertex so
        # the preview path stays Manhattan (mirrors MoveWireVertexCommand.do).
        def _elbow(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
            ax, ay = a
            bx, by = b
            if ax == bx or ay == by:
                return None
            elbow = (bx, ay)
            return None if (elbow == a or elbow == b) else elbow

        rebuilt: list[tuple[float, float]] = []
        for j, p in enumerate(pts):
            if j == 0:
                rebuilt.append(p)
                continue
            prev = pts[j - 1]
            if j == idx or j - 1 == idx:
                elbow = _elbow(prev, p)
                if elbow is not None:
                    rebuilt.append(elbow)
            rebuilt.append(p)
        simplified = simplify_points(rebuilt)
        item.set_preview_points(simplified)
        self._update_ocirc_preview({wire_id: simplified})

    def _preview_component_drag(self) -> None:
        """Ghost connected wires as the dragged components move (model untouched).

        Pins of the moving components are taken at their drag-start positions;
        the live delta comes from each item's current (snapped) position. Each
        connected wire is reshaped and simplified with the shared helper and
        pushed to its WireItem as a preview path. On release the real
        MoveCommand commits.
        """
        if not self._drag_start:
            return

        # Live delta per component, and the union of their start-pos pins.
        deltas: dict[str, tuple[float, float]] = {}
        start_pins: set[tuple[float, float]] = set()
        for cid, start in self._drag_start.items():
            item = self._comp_items.get(cid)
            if item is None:
                continue
            cur = self.scene_to_gu(item.pos())   # unsnapped, for smooth ghosting
            deltas[cid] = (cur[0] - start[0], cur[1] - start[1])
            # Pins at the start position (use a stand-in component at `start`).
            comp = next(
                (c for c in self._schematic.components if c.id == cid), None
            )
            if comp is not None:
                from dataclasses import replace

                at_start = replace(comp, position=start)
                for p in _component_pin_positions(at_start):
                    start_pins.add((round(p[0], 6), round(p[1], 6)))

        if not deltas:
            return
        # A single representative delta (all co-dragged items share it).
        dx, dy = next(iter(deltas.values()))

        previewed: set[str] = set()
        for wire in self._schematic.wires:
            pts = wire.points
            if len(pts) < 2:
                continue
            start_hit = (round(pts[0][0], 6), round(pts[0][1], 6)) in start_pins
            end_hit = (round(pts[-1][0], 6), round(pts[-1][1], 6)) in start_pins
            if not (start_hit or end_hit):
                continue
            new_pts = reshape_wire_points(
                pts, start_hit=start_hit, end_hit=end_hit, dx=dx, dy=dy,
                simplify=True,
            )
            item = self._wire_items.get(wire.id)
            if item is not None:
                item.set_preview_points(new_pts)
                previewed.add(wire.id)

        # Clear any wire that was previewed last frame but no longer is.
        for wid in self._previewed_wire_ids - previewed:
            it = self._wire_items.get(wid)
            if it is not None:
                it.clear_preview_points()
        self._previewed_wire_ids = previewed

        # Keep open-circle items in sync with the previewed wire endpoints.
        # Also pass the current (dragged) pin positions so endpoints that have
        # followed a dragged pin are not incorrectly shown as unconnected.
        preview_pts: dict[str, list[tuple[float, float]]] = {}
        for wire in self._schematic.wires:
            wi = self._wire_items.get(wire.id)
            if wi is not None and wi._preview_points is not None:
                preview_pts[wire.id] = wi._preview_points

        dragged_pins: set[tuple[float, float]] = set()
        for cid, start in self._drag_start.items():
            comp = next((c for c in self._schematic.components if c.id == cid), None)
            item = self._comp_items.get(cid)
            if comp is not None and item is not None:
                cur = self.scene_to_gu(item.pos())
                ddx = cur[0] - start[0]
                ddy = cur[1] - start[1]
                from dataclasses import replace as _replace
                at_cur = _replace(comp, position=(comp.position[0] + ddx, comp.position[1] + ddy))
                for p in _component_pin_positions(at_cur):
                    dragged_pins.add((round(p[0], 6), round(p[1], 6)))

        self._update_ocirc_preview(preview_pts, extra_pin_positions=dragged_pins)
        self._update_junction_preview(preview_pts, dragged_pins)

    def _update_ocirc_preview(
        self,
        preview_pts_by_wire: dict[str, list[tuple[float, float]]],
        extra_pin_positions: set[tuple[float, float]] | None = None,
    ) -> None:
        """Reposition open-circle items to match wire endpoint preview positions.

        *preview_pts_by_wire* maps wire_id → the preview point list for that
        wire (only wires whose endpoints are moving need to be included).

        *extra_pin_positions* is an optional set of additional pin coordinates
        to treat as connected — used during component drag to supply the current
        (dragged) pin positions, which differ from the model positions and would
        otherwise cause connected wire endpoints to falsely appear as open.
        """
        # Build the set of all pin positions, using dragged positions for
        # components currently being dragged (their model positions are stale).
        dragged_comp_ids = set(self._drag_start.keys())
        pin_positions: set[tuple[float, float]] = set()
        for comp in self._schematic.components:
            if comp.id in dragged_comp_ids:
                continue  # replaced by extra_pin_positions below
            for p in _component_pin_positions(comp):
                pin_positions.add((round(p[0], 6), round(p[1], 6)))
        if extra_pin_positions:
            pin_positions |= extra_pin_positions

        # Build a count of how many times each coordinate appears across all
        # wire point lists (using preview positions where available).  A count
        # > 1 means the point is shared with another wire — not an open endpoint.
        all_wire_points: dict[tuple[float, float], int] = {}
        for wire in self._schematic.wires:
            pts = preview_pts_by_wire.get(wire.id, wire.points)
            for pt in pts:
                pt_r = (round(pt[0], 6), round(pt[1], 6))
                all_wire_points[pt_r] = all_wire_points.get(pt_r, 0) + 1

        # Compute the desired ocirc positions from model wires, substituting
        # preview endpoints for any wire that is being previewed.
        desired: set[tuple[float, float]] = set()
        for wire in self._schematic.wires:
            pts = preview_pts_by_wire.get(wire.id, wire.points)
            if len(pts) < 2:
                continue
            for pt in (pts[0], pts[-1]):
                pt_r = (round(pt[0], 6), round(pt[1], 6))
                if pt_r in pin_positions:
                    continue
                if all_wire_points.get(pt_r, 0) > 1:
                    continue
                desired.add(pt_r)

        # Remove items no longer needed.
        for coord in list(self._open_circle_items):
            if coord not in desired:
                self.removeItem(self._open_circle_items.pop(coord))
        # Add or reposition items.
        for coord in desired:
            if coord not in self._open_circle_items:
                oc = OpenCircleItem()
                oc.setPos(self.gu_to_scene(*coord))
                self.addItem(oc)
                self._open_circle_items[coord] = oc

    def _update_junction_preview(
        self,
        preview_pts_by_wire: dict[str, list[tuple[float, float]]],
        extra_pin_positions: set[tuple[float, float]] | None = None,
    ) -> None:
        """Move junction dot items to match previewed wire positions during drag.

        Recomputes junction degree using preview wire points so dots follow
        the dragged topology rather than staying at the pre-drag model positions.
        """
        from app.schematic.model import junction_points as _junction_points

        # Build a temporary schematic-like view using preview points.
        # We recompute degree manually using the same logic as junction_points().
        degree: dict[tuple[float, float], int] = {}

        def add(pt: tuple[float, float], d: int) -> None:
            pt = (round(pt[0], 6), round(pt[1], 6))
            degree[pt] = degree.get(pt, 0) + d

        for wire in self._schematic.wires:
            pts = preview_pts_by_wire.get(wire.id, wire.points)
            own: dict[tuple[float, float], int] = {}
            n = len(pts)
            for i, pt in enumerate(pts):
                pt_r = (round(pt[0], 6), round(pt[1], 6))
                own[pt_r] = own.get(pt_r, 0) + (1 if (i == 0 or i == n - 1) else 2)
            for pt, d in own.items():
                add(pt, d)

        # Add pin positions for all components, but use dragged positions for
        # components currently being dragged (their model positions are stale).
        dragged_comp_ids = set(self._drag_start.keys())
        for comp in self._schematic.components:
            if comp.id in dragged_comp_ids:
                continue  # replaced by extra_pin_positions below
            for p in _component_pin_positions(comp):
                add(p, 1)
        for p in (extra_pin_positions or set()):
            add(p, 1)

        wanted = {pt for pt, d in degree.items() if d >= 3}

        # Remove dots no longer needed.
        for coord in list(self._junction_items):
            if coord not in wanted:
                self.removeItem(self._junction_items.pop(coord))
        # Add or reposition.
        for coord in wanted:
            if coord not in self._junction_items:
                dot = JunctionItem()
                dot.setPos(self.gu_to_scene(*coord))
                self.addItem(dot)
                self._junction_items[coord] = dot
            else:
                self._junction_items[coord].setPos(self.gu_to_scene(*coord))

    def _clear_component_drag_preview(self) -> None:
        for wid in self._previewed_wire_ids:
            item = self._wire_items.get(wid)
            if item is not None:
                item.clear_preview_points()
        self._previewed_wire_ids = set()

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._panning:
            super().mousePressEvent(event)
            return

        gu = self.snap_point_gu(event.scenePos())

        if self._mode == Mode.PLACE:
            if event.button() == Qt.RightButton:
                self.set_mode(Mode.SELECT)
                event.accept()
                return
            if event.button() == Qt.LeftButton and self._place_kind:
                self.place_component(
                    self._place_kind, gu,
                    rotation=self._place_rotation,
                    mirror=self._place_mirror,
                )
                # Stay in PLACE mode for rapid repeated placement (spec §6.2).
                event.accept()
                return

        if self._mode == Mode.WIRE:
            if event.button() == Qt.LeftButton:
                target, is_pin = self.wire_snap_target(gu)
                vfirst = bool(event.modifiers() & Qt.ShiftModifier)
                if not self._wire_pts:
                    # Begin the wire — anchor the first vertex (pin or node).
                    self._wire_pts = [target]
                    self._refresh_wire_preview(target, is_pin, vfirst)
                else:
                    # Append a Manhattan leg (a click on empty space drops an
                    # intermediate grid-node anchor; a pin click terminates).
                    route = self._route(self._wire_pts[-1], target, vfirst=vfirst)
                    self._wire_pts.extend(route[1:])
                    if is_pin and len(self._wire_pts) >= 2:
                        pts = self._wire_pts
                        self._wire_pts = []
                        self.add_wire(pts)
                        self._cancel_wire_preview()
                        # Terminating on a pin returns to SELECT mode.
                        self.set_mode(Mode.SELECT)
                    else:
                        self._refresh_wire_preview(target, is_pin, vfirst)
                event.accept()
                return

        # SELECT mode: a press on a draggable wire vertex starts a vertex drag
        # (takes priority over component drag / rubber-band select).
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            hit = self.wire_vertex_at(event.scenePos())
            if hit is not None:
                wire_id, idx = hit
                wire = next(
                    (w for w in self._schematic.wires if w.id == wire_id), None
                )
                if wire is not None:
                    self._vertex_drag = (wire_id, idx, wire.points[idx])
                    self.clearSelection()
                    event.accept()
                    return

        # SELECT mode: clicking an UNCONNECTED pin auto-enters WIRE mode and
        # begins a wire there. Connected pins fall through to normal selection.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            pin = self.unconnected_pin_at(event.scenePos())
            if pin is not None:
                self.clearSelection()
                self._mode = Mode.WIRE
                self._apply_item_flags()
                self.mode_changed.emit(Mode.WIRE)
                self._wire_pts = [pin]
                self._refresh_wire_preview(pin, True, False)
                event.accept()
                return

        # SELECT mode: record drag-start positions for a possible MoveCommand.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            super().mousePressEvent(event)
            self._drag_start = {
                item.component.id: item.component.position
                for item in self.selectedItems()
                if isinstance(item, ComponentItem)
            }
            return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        # Commit a wire-vertex drag if one is active.
        if self._vertex_drag is not None and event.button() == Qt.LeftButton:
            wire_id, idx, orig = self._vertex_drag
            self._vertex_drag = None
            gu = self.snap_point_gu(event.scenePos())
            # Snap the dropped vertex onto a pin / another wire's vertex or
            # segment (excluding the dragged wire itself), so it lands exactly
            # on the target and forms a junction.
            target, _ = self.wire_snap_target(gu, exclude_wire_id=wire_id)
            item = self._wire_items.get(wire_id)
            if item is not None:
                item.clear_preview_points()  # drop visual preview
            if target != orig:
                self.move_wire_vertex(wire_id, idx, target)
            event.accept()
            return

        pending = self._mode == Mode.SELECT and bool(self._drag_start)

        # Drop any wire drag-ghosts before committing; the MoveCommand below
        # rebuilds the wire items with their real (snapped) geometry.
        self._clear_component_drag_preview()

        # Let Qt finish its own mouse-grab / drag bookkeeping FIRST. Pushing a
        # command (which reconciles items) before this returns can run while Qt
        # still treats the drag as in-progress, corrupting interaction state.
        super().mouseReleaseEvent(event)

        if pending:
            # Read each item's final snapped position, then compute its own
            # delta from its recorded start. Items are grouped by identical
            # snapped delta so the resulting command is exact.
            per_delta: dict[tuple[float, float], list[str]] = {}
            for cid, start in self._drag_start.items():
                item = self._comp_items.get(cid)
                if item is None:
                    continue
                new_gu = self.snap_point_gu(item.pos())
                # Reset the item to its model position; the command moves it.
                item.setPos(self.gu_to_scene(*start))
                d = (new_gu[0] - start[0], new_gu[1] - start[1])
                if d != (0.0, 0.0):
                    per_delta.setdefault(d, []).append(cid)
            self._drag_start = {}

            move_cmds = [MoveCommand(ids, d) for d, ids in per_delta.items()]
            if len(move_cmds) == 1:
                self._push(move_cmds[0])
            elif move_cmds:
                self._push(MacroCommand(move_cmds, label="Move"))

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._mode == Mode.WIRE and self._wire_pts:
            gu = self.snap_point_gu(event.scenePos())
            target, is_pin = self.wire_snap_target(gu)
            vfirst = bool(event.modifiers() & Qt.ShiftModifier)
            if target != self._wire_pts[-1]:
                route = self._route(self._wire_pts[-1], target, vfirst=vfirst)
                self._wire_pts.extend(route[1:])
            pts = self._wire_pts
            self._wire_pts = []
            self.add_wire(pts)
            self._cancel_wire_preview()
            # Ending on a pin returns to SELECT; ending in empty space stays in
            # WIRE mode so the user can keep routing.
            if is_pin:
                self.set_mode(Mode.SELECT)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------
    # Escape handling (called by the view's keyPressEvent)
    # ------------------------------------------------------------------

    def cancel_current(self) -> None:
        """Escape: cancel placement/wire in progress and return to SELECT."""
        if self._mode == Mode.PLACE:
            self.set_mode(Mode.SELECT)
        elif self._mode == Mode.WIRE and self._wire_pts:
            self._cancel_wire()
        else:
            self.set_mode(Mode.SELECT)

    # ------------------------------------------------------------------
    # Grid background
    # ------------------------------------------------------------------

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        super().drawBackground(painter, rect)

        left = int(rect.left()) - (int(rect.left()) % int(GRID_PX))
        top = int(rect.top()) - (int(rect.top()) % int(GRID_PX))
        half = GRID_PX * SNAP_GU

        # Sub-grid (0.5 GU) — faint.
        sub_pen = QPen(_GRID_SUB)
        sub_pen.setWidth(0)
        painter.setPen(sub_pen)
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(x + half, rect.top()), QPointF(x + half, rect.bottom()))
            x += GRID_PX
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y + half), QPointF(rect.right(), y + half))
            y += GRID_PX

        # Integer grid — normal weight.
        main_pen = QPen(_GRID_NORMAL)
        main_pen.setWidth(0)
        painter.setPen(main_pen)
        x = left
        while x < rect.right():
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            x += GRID_PX
        y = top
        while y < rect.bottom():
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            y += GRID_PX


# ---------------------------------------------------------------------------
# Pin geometry (shared transform with commands/codegen)
# ---------------------------------------------------------------------------

def _component_pin_positions(comp: Component) -> list[tuple[float, float]]:
    """Absolute (mirror-then-rotate) pin coordinates of *comp*, in GU.

    Mirrors the transform used in :mod:`app.canvas.commands` so wire pin-snap
    and delete-connectivity agree on where a component's pins actually are.
    """
    defn = REGISTRY.get(comp.kind)
    if defn is None:
        return []
    ox, oy = comp.position
    out: list[tuple[float, float]] = []
    for pin in defn.pins:
        dx, dy = pin.offset
        if comp.mirror:
            dx = -dx
        r = comp.rotation % 360
        if r == 90:
            rx, ry = (-dy, dx)
        elif r == 180:
            rx, ry = (-dx, -dy)
        elif r == 270:
            rx, ry = (dy, -dx)
        else:
            rx, ry = (dx, dy)
        out.append((ox + rx, oy + ry))
    return out
