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
* **Schematic coords** (GU): what the model stores. Snap granularity 0.25 GU.
* **Scene/pixel coords**: schematic coords × ``GRID_PX``. All items live here.

Helpers :meth:`scene_to_gu` / :meth:`gu_to_scene` convert between them, and
:meth:`snap_gu` rounds to the nearest 0.25 GU.
"""

from __future__ import annotations

import copy
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
    Command,
    DeleteCommand,
    EditCommand,
    GroupRotateCommand,
    MacroCommand,
    MergeWireCommand,
    MirrorCommand,
    SetBodyDiodeCommand,
    SetFilledCommand,
    SetFontSizeCommand,
    MoveCommand,
    MoveOptionsLabelCommand,
    MoveWireVertexCommand,
    PlaceCommand,
    RotateCommand,
    SplitWireCommand,
    UndoStack,
    WireCommand,
)
from app.canvas.items import (
    ComponentItem,
    JunctionItem,
    LabelTextItem,
    OpenCircleItem,
    WireItem,
    WirePreviewItem,
    _SlotLabel,
    _WireEndLabel,
    _WireMidLabel,
)
from app.canvas.geometry import (
    gu_to_scene as _gu_to_scene,
    scene_to_gu as _scene_to_gu,
    snap_gu as _snap_gu,
    snap_point_gu as _snap_point_gu,
)
from app.canvas.drag import DragPreviewController
from app.canvas.style import GRID_PX
from app.canvas.wiregeometry import WireGeometry
from app.components.registry import ITEM_CLASSES, REGISTRY
from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    WIRE_LINE_STYLE_CYCLE,
    WIRE_MARKER_CYCLE,
    component_pin_positions as _component_pin_positions,
    junction_points,
    open_endpoints,
    unconnected_pins,
    route,
    simplify_points,
    wire_corner_splits_at,
    wire_fraction_at_point,
    wire_splits_at,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Snap / proximity constants live in app.canvas.geometry; the wire-snap radii
# are used by WireGeometry.

_GRID_NORMAL = QColor("#FFD0D0D0")   # integer grid lines
_GRID_SUB = QColor("#22808080")      # 0.5 GU midline (reduced opacity)
_GRID_SUB_FINE = QColor("#11808080")  # 0.25/0.75 GU minor lines (faintest)

_LABEL_CLEARANCE = 6  # px gap used by auto-placement candidates (§8.3)


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

    component_double_clicked = Signal(str)
    """Emitted with the component id when a component is double-clicked (spec §6.5)."""

    def __init__(self, schematic: Schematic | None = None, parent=None):
        super().__init__(parent)
        self._schematic = schematic or Schematic(version="0.1", name="untitled")
        self._stack = UndoStack(self._schematic)

        # Stateless query helper for wire snapping / hit-testing. Reads the live
        # schematic through a getter so it stays valid across set_schematic().
        self._wire_geom = WireGeometry(lambda: self._schematic)

        # Owns drag state and live drag previews (component move, wire-vertex
        # drag, endpoint resize). The scene's mouse handlers drive it.
        self._drag = DragPreviewController(self)

        self._mode = Mode.SELECT
        self._panning = False
        # While dragging a wire's mid-label along its wire: (wire_id, press_pos).
        self._mid_label_drag: tuple[str, float] | None = None

        # kind -> item maps for sync
        self._comp_items: dict[str, ComponentItem] = {}
        self._wire_items: dict[str, WireItem] = {}
        # junction coordinate (gu) -> dot item
        self._junction_items: dict[tuple[float, float], JunctionItem] = {}
        # open-endpoint coordinate (gu) -> open-circle item
        self._open_circle_items: dict[tuple[float, float], OpenCircleItem] = {}
        # unconnected-pin coordinate (gu) -> open-circle item (display preference)
        self._pin_circle_items: dict[tuple[float, float], OpenCircleItem] = {}
        # Whether to draw open circles at unconnected component pins (§10.8).
        self._mark_unconnected_pins: bool = False

        # Placement state
        self._place_kind: str | None = None
        self._place_rotation: int = 0
        self._place_mirror: bool = False
        self._ghost: ComponentItem | None = None

        # Wire-routing state
        self._wire_pts: list[tuple[float, float]] = []
        self._wire_preview: WirePreviewItem | None = None

        # Copy/paste clipboard: deep copies of components and wires.
        self._clipboard_components: list[Component] = []
        self._clipboard_wires: list[Wire] = []

        # Use a plain (un-indexed) item list rather than the default BSP tree.
        # _rebuild_items removes coordinate-keyed junction/open-circle dots by
        # dropping their last Python reference, so PySide frees the C++ item the
        # instant removeItem() returns. The BSP index only *defers* removal, so a
        # subsequent paint would walk a dangling pointer and segfault (notably:
        # group-rotate a circuit with a junction dot, then delete — the rotate
        # churns the dot and the delete frees it with no paint cycle between to
        # flush the index). NoIndex updates the item list synchronously on
        # removeItem, eliminating the dangling pointer; linear hit-testing is a
        # non-issue for schematic-sized scenes that mutate this frequently.
        self.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)

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
            self._drag.any_active()            # component/vertex/endpoint drag
            or bool(self._wire_pts)            # wire being drawn (has anchored points)
        )

    # Read-only views of drag state, retained for the test suite.
    @property
    def _drag_start(self) -> dict[str, tuple[float, float]]:
        return self._drag.drag_start

    @property
    def _vertex_drag(self) -> tuple[str, int, tuple[float, float]] | None:
        return self._drag.vertex_drag

    @property
    def _previewed_wire_ids(self) -> set[str]:
        return self._drag.previewed_wire_ids

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

    # These delegate to the pure helpers in app.canvas.geometry; kept as methods
    # so existing callers (event handlers, tests) use the same names.
    @staticmethod
    def snap_gu(value: float) -> float:
        """Round a GU value to the nearest 0.25 GU."""
        return _snap_gu(value)

    @staticmethod
    def scene_to_gu(pt: QPointF) -> tuple[float, float]:
        return _scene_to_gu(pt)

    @staticmethod
    def gu_to_scene(x: float, y: float) -> QPointF:
        return _gu_to_scene(x, y)

    def snap_point_gu(self, pt: QPointF) -> tuple[float, float]:
        return _snap_point_gu(pt)

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

    def enter_pan_mode(self) -> None:
        self.set_mode(Mode.PAN)

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

    def _component_by_id(self, component_id: str) -> Component | None:
        """Return the live model Component with *component_id*, or None."""
        return next(
            (c for c in self._schematic.components if c.id == component_id), None
        )

    def place_component(
        self,
        kind: str,
        position: tuple[float, float],
        rotation: int = 0,
        mirror: bool = False,
        options: str = "",
    ) -> Component:
        """Place a component at *position* (GU) via an undoable PlaceCommand."""
        defn = REGISTRY[kind]
        cls = defn.component_class
        extra: dict = {}
        if kind == "rect":
            extra["z_order"] = -10
        comp = cls(
            id=str(uuid.uuid4()),
            kind=kind,
            position=(self.snap_gu(position[0]), self.snap_gu(position[1])),
            rotation=rotation,
            options=options,
            mirror=mirror,
            **extra,
        )
        place_cmd = PlaceCommand(comp)
        split_cmds = self._split_commands_for(
            {pos for pos in _component_pin_positions(comp)}
        )
        if split_cmds:
            self._push(MacroCommand([place_cmd] + split_cmds, label="Place"))
        else:
            self._push(place_cmd)
        return comp

    def _pin_splits_after_delta(
        self,
        comp_ids: list[str],
        delta: tuple[float, float],
    ) -> list[SplitWireCommand]:
        """SplitWireCommands for any pin that will land mid-segment after (dx,dy).

        Computes new pin positions = current + delta, then delegates to
        _split_commands_for so placement, move, nudge, and paste all get
        automatic wire splits when a pin arrives on a wire segment.
        """
        dx, dy = delta
        positions: set[tuple[float, float]] = set()
        comp_id_set = set(comp_ids)
        for comp in self._schematic.components:
            if comp.id not in comp_id_set:
                continue
            for px, py in _component_pin_positions(comp):
                positions.add((px + dx, py + dy))
        return self._split_commands_for(positions)

    def _split_commands_for(
        self,
        points: set[tuple[float, float]],
        exclude_wire_id: str | None = None,
    ) -> list[SplitWireCommand]:
        """Build SplitWireCommands for any *points* that land mid-segment or at a corner.

        For each point that lies strictly inside an existing wire's segment
        (per :func:`wire_splits_at`) or at an existing wire's intermediate
        vertex (per :func:`wire_corner_splits_at`), produce a split command so
        the connection becomes real topology and a junction dot appears.
        *exclude_wire_id* skips a wire that must not split itself.
        """
        cmds: list[SplitWireCommand] = []
        seen: set[tuple[str, tuple[float, float]]] = set()
        for pt in points:
            hits = wire_splits_at(self._schematic, pt) + wire_corner_splits_at(self._schematic, pt)
            for wire_id, idx in hits:
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
        any directly selected wires.  If the deletion dissolves a T-junction
        (leaving exactly two wire endpoints at a free point), the two remaining
        stubs are automatically merged into one wire as part of the same
        undoable action.
        """
        comp_ids = self.selected_component_ids()
        wire_ids = self.selected_wire_ids()
        if not comp_ids and not wire_ids:
            return
        merge_cmds = self._merge_commands_after_delete(comp_ids, wire_ids)
        delete_cmd = DeleteCommand(comp_ids, wire_ids)
        if merge_cmds:
            self._push(MacroCommand([delete_cmd] + merge_cmds, label="Delete"))
        else:
            self._push(delete_cmd)

    def copy_selection(self) -> None:
        """Copy selected components and wires to the internal clipboard."""
        comp_ids = set(self.selected_component_ids())
        wire_ids = set(self.selected_wire_ids())
        if not comp_ids and not wire_ids:
            return
        self._clipboard_components = [
            copy.deepcopy(c)
            for c in self._schematic.components
            if c.id in comp_ids
        ]
        self._clipboard_wires = [
            copy.deepcopy(w)
            for w in self._schematic.wires
            if w.id in wire_ids
        ]

    def paste(self) -> None:
        """Paste clipboard contents offset by 1 GU, with new UUIDs."""
        if not self._clipboard_components and not self._clipboard_wires:
            return
        _OFFSET = 1.0
        cmds: list[Command] = []
        new_comp_ids: list[str] = []
        new_wire_ids: list[str] = []
        new_comps: list = []
        for comp in self._clipboard_components:
            new_id = str(uuid.uuid4())
            new_comp = copy.deepcopy(comp)
            new_comp.id = new_id
            new_comp.position = (comp.position[0] + _OFFSET, comp.position[1] + _OFFSET)
            cmds.append(PlaceCommand(new_comp))
            new_comp_ids.append(new_id)
            new_comps.append(new_comp)
        for wire in self._clipboard_wires:
            new_wire = copy.deepcopy(wire)
            new_wire.id = str(uuid.uuid4())
            new_wire.points = [(x + _OFFSET, y + _OFFSET) for x, y in wire.points]
            cmds.append(WireCommand(new_wire))
            new_wire_ids.append(new_wire.id)
        # Split any existing wires whose segments the pasted pins land on.
        paste_pin_positions = {
            pos for nc in new_comps for pos in _component_pin_positions(nc)
        }
        cmds.extend(self._split_commands_for(paste_pin_positions))
        self._push(MacroCommand(cmds, label="Paste"))
        self.enter_select_mode()
        self.clearSelection()
        for cid in new_comp_ids:
            item = self._comp_items.get(cid)
            if item:
                item.setSelected(True)
        for wid in new_wire_ids:
            item = self._wire_items.get(wid)
            if item:
                item.setSelected(True)

    def _merge_commands_after_delete(
        self,
        comp_ids: list[str],
        wire_ids: list[str],
    ) -> list[MergeWireCommand]:
        """Return MergeWireCommands for endpoints that become degree-2 after deletion.

        Simulates removing the given components and wires (including pin-connected
        wires) to find shared endpoints that are no longer junctions.  A
        MergeWireCommand is emitted for each such point where exactly two wires
        remain, neither endpoint is a component pin, and the two wires are not
        themselves being deleted.
        """
        # Collect all pin positions for the components being deleted.
        deleted_pin_positions: set[tuple[float, float]] = set()
        comp_id_set = set(comp_ids)
        for comp in self._schematic.components:
            if comp.id in comp_id_set:
                for pos in _component_pin_positions(comp):
                    deleted_pin_positions.add(pos)

        # Wire IDs being removed: explicit selection + pin-connected.
        explicit = set(wire_ids)
        removed_ids: set[str] = set(explicit)
        for wire in self._schematic.wires:
            if any(wire.points[0] == p or wire.points[-1] == p for p in deleted_pin_positions):
                removed_ids.add(wire.id)

        # Collect all free endpoints of the wires being removed.
        candidate_points: set[tuple[float, float]] = set()
        for wire in self._schematic.wires:
            if wire.id in removed_ids:
                candidate_points.add(wire.points[0])
                candidate_points.add(wire.points[-1])

        # Pin positions of ALL surviving components.
        surviving_pins: set[tuple[float, float]] = set()
        for comp in self._schematic.components:
            if comp.id not in comp_id_set:
                for pos in _component_pin_positions(comp):
                    surviving_pins.add(pos)

        # For each candidate point, count surviving wire endpoints there.
        merge_cmds: list[MergeWireCommand] = []
        seen_points: set[tuple[float, float]] = set()
        for pt in candidate_points:
            if pt in seen_points:
                continue
            if pt in surviving_pins:
                continue
            neighbors = [
                w for w in self._schematic.wires
                if w.id not in removed_ids
                and (w.points[0] == pt or w.points[-1] == pt)
            ]
            if len(neighbors) == 2:
                seen_points.add(pt)
                merge_cmds.append(
                    MergeWireCommand(neighbors[0].id, neighbors[1].id, pt)
                )
        return merge_cmds

    def edit_component_options(self, component_id: str, new_options: str) -> None:
        """Replace the options string of a component via an undoable EditCommand.

        When options transition from empty to non-empty and no label position has
        been set yet, auto-placement runs first so the label avoids overlapping
        other component bounding boxes.
        """
        comp = self._component_by_id(component_id)
        if comp is None:
            return
        was_empty = not comp.options
        cmds = [EditCommand(component_id, new_options)]
        if was_empty and new_options and comp.label_offset is None:
            offset = self._auto_place_label(component_id, new_options)
            if offset is not None:
                cmds.append(MoveOptionsLabelCommand(component_id, offset))
        if len(cmds) == 1:
            self._push(cmds[0])
        else:
            self._push(MacroCommand(cmds, label="Edit"))

    def move_options_label(
        self, component_id: str, new_offset: tuple[float, float]
    ) -> None:
        """Persist a label drag via an undoable MoveOptionsLabelCommand."""
        self._push(MoveOptionsLabelCommand(component_id, new_offset))

    def _auto_place_label(
        self, component_id: str, options_text: str
    ) -> tuple[float, float] | None:
        """Find a label position near the component that avoids existing bboxes.

        Tries eight candidate offsets around the component (above, below, left,
        right, and the four diagonals), scores each by overlap area with other
        component bboxes in scene coordinates, and returns the component-local
        (dx, dy) for the best candidate.  Returns ``None`` if the default
        above-centre position is already clear (no change needed).
        """
        from app.canvas.items import LabelTextItem as _LabelTextItem  # local to avoid circular at module level

        comp = self._component_by_id(component_id)
        if comp is None:
            return None

        item = self._comp_items.get(component_id)
        if item is None:
            return None

        # Approximate label size from a temporary text measurement.
        # Use the options_item child which already has the right font.
        options_item = next(
            (ch for ch in item.childItems() if isinstance(ch, _LabelTextItem)), None
        )
        if options_item is None:
            return None
        options_item.setPlainText(options_text)
        lw = options_item.boundingRect().width()
        lh = options_item.boundingRect().height()

        # Component bbox in component-local pixel coords.
        defn = item._defn
        x0, y0, x1, y1 = defn.bbox
        bx0 = x0 * GRID_PX
        by0 = y0 * GRID_PX
        bx1 = x1 * GRID_PX
        by1 = y1 * GRID_PX
        cx = (bx0 + bx1) / 2
        bw = bx1 - bx0
        bh = by1 - by0

        gap = _LABEL_CLEARANCE

        # Eight candidate positions (dx, dy) in component-local px,
        # ordered by preference: above, right, below, left, then diagonals.
        candidates: list[tuple[float, float]] = [
            (cx - lw / 2, by0 - gap - lh),           # above-centre (default)
            (bx1 + gap, (by0 + by1) / 2 - lh / 2),  # right-middle
            (cx - lw / 2, by1 + gap),                 # below-centre
            (bx0 - gap - lw, (by0 + by1) / 2 - lh / 2),  # left-middle
            (bx1 + gap, by0 - gap - lh),              # top-right
            (bx1 + gap, by1 + gap),                   # bottom-right
            (bx0 - gap - lw, by0 - gap - lh),        # top-left
            (bx0 - gap - lw, by1 + gap),              # bottom-left
        ]

        # Build scene-space rects for every OTHER component's bbox.
        obstacle_rects: list[QRectF] = []
        for other_comp in self._schematic.components:
            if other_comp.id == component_id:
                continue
            other_item = self._comp_items.get(other_comp.id)
            if other_item is None:
                continue
            # Map other item's bbox to scene coords.
            obstacle_rects.append(
                other_item.mapToScene(other_item.boundingRect()).boundingRect()
            )

        # Map the candidate label rects from component-local to scene coords,
        # then score by total overlap area.
        def overlap_area(dx: float, dy: float) -> float:
            label_scene = item.mapToScene(
                QRectF(dx, dy, lw, lh)
            ).boundingRect()
            total = 0.0
            for obs in obstacle_rects:
                inter = label_scene.intersected(obs)
                if not inter.isEmpty():
                    total += inter.width() * inter.height()
            return total

        default_dx, default_dy = candidates[0]
        best_dx, best_dy = default_dx, default_dy
        best_score = overlap_area(default_dx, default_dy)

        if best_score == 0.0:
            # Default position is already clear — signal no override needed.
            return None

        for dx, dy in candidates[1:]:
            score = overlap_area(dx, dy)
            if score < best_score:
                best_score = score
                best_dx, best_dy = dx, dy
                if score == 0.0:
                    break

        return (best_dx, best_dy)

    def rotate_component(self, component_id: str, new_rotation: int) -> None:
        """Set the rotation of a component via an undoable RotateCommand."""
        self._push(RotateCommand(component_id, new_rotation))

    def rotate_selected_cw(self) -> None:
        """Rotate selected components and wires 90° CW around their group centroid."""
        if self._mode == Mode.PLACE:
            self._place_rotation = (self._place_rotation + 90) % 360
            self._update_ghost_transform()
            return
        comp_ids = self.selected_component_ids()
        wire_ids = self.selected_wire_ids()
        if not comp_ids and not wire_ids:
            return

        # Centroid = bounding-box centre of selected component positions,
        # or wire vertices if only wires are selected.
        # Snapped to 0.25 GU so rotated positions stay on the grid.
        def _snap(v: float) -> float:
            return round(v * 2) / 2

        if comp_ids:
            comp_id_set = set(comp_ids)
            xs = [c.position[0] for c in self._schematic.components if c.id in comp_id_set]
            ys = [c.position[1] for c in self._schematic.components if c.id in comp_id_set]
        else:
            wire_id_set = set(wire_ids)
            pts = [
                p
                for w in self._schematic.wires if w.id in wire_id_set
                for p in w.points
            ]
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]

        cx = _snap((min(xs) + max(xs)) / 2)
        cy = _snap((min(ys) + max(ys)) / 2)
        self._push(GroupRotateCommand(comp_ids, wire_ids, (cx, cy)))

    def mirror_component(self, component_id: str, new_mirror: bool) -> None:
        """Set the mirror state of a component via an undoable MirrorCommand."""
        self._push(MirrorCommand(component_id, new_mirror))

    def set_component_filled(self, component_id: str, new_filled: bool) -> None:
        """Set the filled state of a component via an undoable SetFilledCommand."""
        self._push(SetFilledCommand(component_id, new_filled))

    def set_component_body_diode(self, component_id: str, new_body_diode: bool) -> None:
        """Set the body_diode state of a MosfetComponent via an undoable command."""
        self._push(SetBodyDiodeCommand(component_id, new_body_diode))

    def set_fill_color(self, component_id: str, new_fill: str) -> None:
        """Set fill_color on a StyledComponent (bipole or rect) via an undoable command."""
        from app.components.model import StyledComponent
        from app.canvas.commands import SetFillColorCommand
        comp = self._component_by_id(component_id)
        if comp is None or not isinstance(comp, StyledComponent) or comp.fill_color == new_fill:
            return
        self._push(SetFillColorCommand(component_id, new_fill, comp.fill_color))

    def set_border_width(self, component_id: str, new_width: float) -> None:
        """Set border_width on a StyledComponent (bipole or rect) via an undoable command."""
        from app.components.model import StyledComponent
        from app.canvas.commands import SetBorderWidthCommand
        comp = self._component_by_id(component_id)
        if comp is None or not isinstance(comp, StyledComponent) or abs(comp.border_width - new_width) < 1e-6:
            return
        self._push(SetBorderWidthCommand(component_id, new_width, comp.border_width))

    def set_line_style(self, component_id: str, new_style: str) -> None:
        """Set line_style on a StyledComponent (bipole or rect) via an undoable command."""
        from app.components.model import StyledComponent
        from app.canvas.commands import SetLineStyleCommand
        comp = self._component_by_id(component_id)
        if comp is None or not isinstance(comp, StyledComponent) or comp.line_style == new_style:
            return
        self._push(SetLineStyleCommand(component_id, new_style, comp.line_style))

    def _wire_by_id(self, wire_id: str):  # noqa: ANN201
        return next((w for w in self._schematic.wires if w.id == wire_id), None)

    def set_wire_line_style(self, wire_id: str, new_style: str) -> None:
        """Set line_style on a wire via an undoable command (no-op if unchanged)."""
        from app.canvas.commands import SetWireLineStyleCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.line_style == new_style:
            return
        self._push(SetWireLineStyleCommand(wire_id, new_style, wire.line_style))

    def set_wire_line_width(self, wire_id: str, new_width: float) -> None:
        """Set line_width (pt) on a wire via an undoable command (no-op if unchanged)."""
        from app.canvas.commands import SetWireLineWidthCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or abs(wire.line_width - new_width) < 1e-6:
            return
        self._push(SetWireLineWidthCommand(wire_id, new_width, wire.line_width))

    def set_wire_no_junction_dots(self, wire_id: str, value: bool) -> None:
        """Toggle no_junction_dots on a wire via an undoable command (no-op if unchanged)."""
        from app.canvas.commands import SetWireNoJunctionDotsCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.no_junction_dots == value:
            return
        self._push(SetWireNoJunctionDotsCommand(wire_id, value, wire.no_junction_dots))

    def set_wire_no_termination_dots(self, wire_id: str, value: bool) -> None:
        """Toggle no_termination_dots on a wire via an undoable command (no-op if unchanged)."""
        from app.canvas.commands import SetWireNoTerminationDotsCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.no_termination_dots == value:
            return
        self._push(SetWireNoTerminationDotsCommand(wire_id, value, wire.no_termination_dots))

    def set_wire_start_marker(self, wire_id: str, marker: str) -> None:
        """Set the custom start-endpoint marker on a wire (no-op if unchanged)."""
        from app.canvas.commands import SetWireStartMarkerCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.start_marker == marker:
            return
        self._push(SetWireStartMarkerCommand(wire_id, marker, wire.start_marker))

    def set_wire_end_marker(self, wire_id: str, marker: str) -> None:
        """Set the custom end-endpoint marker on a wire (no-op if unchanged)."""
        from app.canvas.commands import SetWireEndMarkerCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.end_marker == marker:
            return
        self._push(SetWireEndMarkerCommand(wire_id, marker, wire.end_marker))

    def set_wire_start_label(self, wire_id: str, text: str) -> None:
        """Set the text/math label at a wire's first point (no-op if unchanged)."""
        from app.canvas.commands import SetWireStartLabelCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.start_label == text:
            return
        self._push(SetWireStartLabelCommand(wire_id, text, wire.start_label))

    def set_wire_end_label(self, wire_id: str, text: str) -> None:
        """Set the text/math label at a wire's last point (no-op if unchanged)."""
        from app.canvas.commands import SetWireEndLabelCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.end_label == text:
            return
        self._push(SetWireEndLabelCommand(wire_id, text, wire.end_label))

    def set_wire_mid_label(self, wire_id: str, text: str) -> None:
        """Set the over-the-wire mid label (no-op if unchanged)."""
        from app.canvas.commands import SetWireMidLabelCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.mid_label == text:
            return
        self._push(SetWireMidLabelCommand(wire_id, text, wire.mid_label))

    def set_wire_mid_label_pos(self, wire_id: str, pos: float) -> None:
        """Set the mid-label's fractional position along the wire (no-op if unchanged)."""
        from app.canvas.commands import SetWireMidLabelPosCommand
        wire = self._wire_by_id(wire_id)
        if wire is None:
            return
        pos = max(0.0, min(1.0, pos))
        if abs(wire.mid_label_pos - pos) < 1e-9:
            return
        self._push(SetWireMidLabelPosCommand(wire_id, pos, wire.mid_label_pos))

    # -- Tab-cycle of wire styling under the cursor (§6.4) -----------------

    def cycle_at(self, scene_pt: QPointF, backward: bool = False) -> bool:
        """Tab-cycle wire styling at *scene_pt*; return True if anything changed.

        A point on a free wire endpoint cycles that endpoint's marker
        (none → arrow → stealth → open → bar → none); a point on a wire body
        cycles the wire's line style (solid → dashed → dotted → dash-dot →
        solid). *backward* (Shift+Tab) steps the other way. Connected endpoints
        and interior vertices fall through to the wire-body case.
        """
        hit = self.wire_vertex_at(scene_pt)
        if hit is not None:
            wid, idx = hit
            wire = self._wire_by_id(wid)
            if wire is not None and idx in (0, len(wire.points) - 1):
                self._cycle_wire_marker(wid, "start" if idx == 0 else "end", backward)
                return True
        for it in self.items(scene_pt):
            if isinstance(it, WireItem):
                self._cycle_wire_line_style(it.wire.id, backward)
                return True
        return False

    @staticmethod
    def _cycle_value(cycle: tuple[str, ...], current: str, backward: bool) -> str:
        step = -1 if backward else 1
        idx = cycle.index(current) if current in cycle else 0
        return cycle[(idx + step) % len(cycle)]

    def _cycle_wire_marker(self, wire_id: str, end: str, backward: bool) -> None:
        wire = self._wire_by_id(wire_id)
        if wire is None:
            return
        if end == "start":
            self.set_wire_start_marker(
                wire_id, self._cycle_value(WIRE_MARKER_CYCLE, wire.start_marker, backward)
            )
        else:
            self.set_wire_end_marker(
                wire_id, self._cycle_value(WIRE_MARKER_CYCLE, wire.end_marker, backward)
            )

    def _cycle_wire_line_style(self, wire_id: str, backward: bool) -> None:
        wire = self._wire_by_id(wire_id)
        if wire is None:
            return
        self.set_wire_line_style(
            wire_id, self._cycle_value(WIRE_LINE_STYLE_CYCLE, wire.line_style, backward)
        )

    def set_component_z_order(self, component_id: str, new_z: int) -> None:
        """Set z_order on a drawing annotation via an undoable SetZOrderCommand."""
        from app.components.model import DrawingComponent
        from app.canvas.commands import SetZOrderCommand
        comp = self._component_by_id(component_id)
        if comp is None or not isinstance(comp, DrawingComponent) or comp.z_order == new_z:
            return
        self._push(SetZOrderCommand(component_id, new_z, comp.z_order))

    def set_font_size(self, component_id: str, new_size: float) -> None:
        """Set font_size on any FontedComponent via an undoable SetFontSizeCommand."""
        from app.components.model import FontedComponent
        comp = self._component_by_id(component_id)
        if comp is None or not isinstance(comp, FontedComponent):
            return
        if comp.font_size == new_size:
            return
        self._push(SetFontSizeCommand(component_id, new_size, comp.font_size))

    def set_font_style(
        self,
        component_id: str,
        bold: bool,
        italic: bool,
        family: str,
    ) -> None:
        """Set font style on any FontedComponent via an undoable SetTextStyleCommand."""
        from app.components.model import FontedComponent
        from app.canvas.commands import SetTextStyleCommand
        comp = self._component_by_id(component_id)
        if comp is None or not isinstance(comp, FontedComponent):
            return
        if (comp.font_bold, comp.font_italic, comp.font_family) == (bold, italic, family):
            return
        self._push(SetTextStyleCommand(
            component_id,
            bold, italic, family,
            comp.font_bold, comp.font_italic, comp.font_family,
        ))

    def mirror_selected(self) -> None:
        """Toggle mirror on selected components, or mirror the placement ghost."""
        if self._mode == Mode.PLACE:
            self._place_mirror = not self._place_mirror
            self._update_ghost_transform()
            return
        for cid in self.selected_component_ids():
            comp = self._component_by_id(cid)
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
        """Move selected components by a delta via MoveCommand (arrow keys).

        On the 0.25 GU grid a one-cell nudge in any direction keeps connected
        wires grid-valid (an auto-elbow lands on a 0.25 node), so no direction
        needs special handling (spec §3.1).
        """
        ids = self.selected_component_ids()
        if not ids:
            return
        move_cmd = MoveCommand(ids, (dx_gu, dy_gu))
        split_cmds = self._pin_splits_after_delta(ids, (dx_gu, dy_gu))
        if split_cmds:
            self._push(MacroCommand([move_cmd] + split_cmds, label="Move"))
        else:
            self._push(move_cmd)

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

    def _remove_item(self, item: QGraphicsItem | None) -> None:
        """Single chokepoint for taking a graphics item out of the scene.

        Every removal goes through here so the lifetime rule (spec §6.7) is
        enforced in one place: detach from the scene with ``removeItem`` before
        the caller drops its last reference. PySide frees the C++ object the
        moment that reference dies, and ``removeItem`` synchronously clears all
        of the scene's internal pointers to the item (selection, focus, mouse
        grabber, hover, and — under NoIndex — the item list), so the subsequent
        free can never dangle. Callers pass ``dict.pop(key)`` directly so the
        tracking entry and the scene item are dropped together.
        """
        if item is not None:
            self.removeItem(item)

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
                self._remove_item(self._comp_items.pop(cid))
        for wid in list(self._wire_items):
            if wid not in model_wire_ids:
                self._remove_item(self._wire_items.pop(wid))

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
                item.apply_transform(t)
                # Defensive: a reused item must always be left visible and
                # fully opaque — never leave a live component invisible.
                item.setVisible(True)
                item.setOpacity(1.0)
                item.update()

        # --- add new / refresh existing wire items ------------------------
        # A rebuild supersedes any in-flight drag ghost.
        self._drag.reset_preview_tracking()
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
                item.refresh_labels()
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
                self._remove_item(self._junction_items.pop(coord))
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
                self._remove_item(self._open_circle_items.pop(coord))
        for coord in wanted_oc:
            if coord not in self._open_circle_items:
                oc = OpenCircleItem()
                oc.setPos(self.gu_to_scene(*coord))
                self.addItem(oc)
                self._open_circle_items[coord] = oc

        # --- unconnected-pin circles (display preference, §10.8) -----------
        # Open circles at component pins nothing connects to.  Mirrors the
        # generator's mark_unconnected_pins option; same OpenCircleItem visual.
        wanted_pc = unconnected_pins(self._schematic) if self._mark_unconnected_pins else set()
        for coord in list(self._pin_circle_items):
            if coord not in wanted_pc:
                self._remove_item(self._pin_circle_items.pop(coord))
        for coord in wanted_pc:
            if coord not in self._pin_circle_items:
                pc = OpenCircleItem()
                pc.setPos(self.gu_to_scene(*coord))
                self.addItem(pc)
                self._pin_circle_items[coord] = pc

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
            item.set_label_interactive(interactive)
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
        ghost_comp = REGISTRY[kind].component_class(
            id="__ghost__", kind=kind, position=(0.0, 0.0),
            rotation=self._place_rotation, mirror=self._place_mirror, options=""
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
            self._remove_item(self._ghost)
            self._ghost = None

    def _cancel_placement(self) -> None:
        self._cancel_ghost()
        self._place_kind = None
        self._place_rotation = 0
        self._place_mirror = False

    # ------------------------------------------------------------------
    # Wire routing
    # ------------------------------------------------------------------

    # Wire snapping / hit-testing delegate to the stateless WireGeometry helper;
    # kept as scene methods so event handlers and tests use the same names.

    def _nearest_pin_gu(self, gu: tuple[float, float]) -> tuple[float, float] | None:
        return self._wire_geom.nearest_pin(gu)

    def _all_pin_positions(self) -> set[tuple[float, float]]:
        return self._wire_geom.all_pin_positions()

    def wire_snap_target(
        self,
        gu: tuple[float, float],
        exclude_wire_id: str | None = None,
    ) -> tuple[tuple[float, float], bool]:
        return self._wire_geom.wire_snap_target(gu, exclude_wire_id)

    def _wire_snap_point(self, scene_pt: QPointF) -> tuple[float, float] | None:
        return self._wire_geom.wire_snap_point(scene_pt)

    def unconnected_pin_at(self, scene_pt: QPointF) -> tuple[float, float] | None:
        return self._wire_geom.unconnected_pin_at(scene_pt)

    def set_mark_unconnected_pins(self, enabled: bool) -> None:
        """Toggle open-circle markers at unconnected component pins (§10.8).

        No-op if unchanged; otherwise rebuilds canvas items so the markers
        appear or disappear immediately.
        """
        enabled = bool(enabled)
        if enabled == self._mark_unconnected_pins:
            return
        self._mark_unconnected_pins = enabled
        self._rebuild_items()

    def vertex_is_draggable(
        self, wire: Wire, index: int, pins: set[tuple[float, float]] | None = None
    ) -> bool:
        return self._wire_geom.vertex_is_draggable(wire, index, pins)

    def wire_vertex_at(self, scene_pt: QPointF) -> tuple[str, int] | None:
        return self._wire_geom.wire_vertex_at(scene_pt)

    def _mid_label_wire_at(self, scene_pt: QPointF) -> str | None:
        """Wire id whose mid-label glyph is under *scene_pt*, else None."""
        for it in self.items(scene_pt):
            if isinstance(it, _WireMidLabel):
                parent = it.parentItem()
                if isinstance(parent, WireItem):
                    return parent.wire.id
        return None

    def _click_select_wire_id(self, scene_pt: QPointF, grabbed_id: str) -> str:
        return self._wire_geom.click_select_wire_id(scene_pt, grabbed_id)

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
    ) -> list[tuple[float, float]]:
        """Dominant-axis Manhattan route from a to b (spec §6.4).

        Delegates to the shared :func:`route` primitive with no orientation
        override, so the corner follows the longer leg (no modifier key flips
        it). The preview and the committed wire both call this, so they agree.
        """
        return route(a, b)

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
        legs = self._route(self._wire_pts[-1], cursor_gu)
        full = list(self._wire_pts) + legs[1:]
        # Show committed vertices as anchors; the cursor end carries the marker.
        preview.set_path(full[:-1], full[-1], cursor_is_pin)

    def _cancel_wire_preview(self) -> None:
        if self._wire_preview is not None:
            self._remove_item(self._wire_preview)
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
            target, is_connectable = self.wire_snap_target(gu)
            self._refresh_wire_preview(target, is_connectable)
            event.accept()
            return

        if self._mid_label_drag is not None:
            wid, _ = self._mid_label_drag
            wire = self._wire_by_id(wid)
            item = self._wire_items.get(wid)
            if wire is not None and item is not None:
                frac = wire_fraction_at_point(wire.points, self.scene_to_gu(event.scenePos()))
                item.preview_mid_label(frac)
            event.accept()
            return

        if self._drag.vertex_drag is not None:
            self._drag.preview_vertex_drag(gu)
            event.accept()
            return

        if self._drag.endpoint_drag is not None:
            self._drag.preview_endpoint_drag(gu)
            event.accept()
            return

        # Let Qt move any dragged component items, then snap them to the grid
        # and ghost their connected wires.
        super().mouseMoveEvent(event)
        if self._mode == Mode.SELECT and self._drag.drag_start:
            for cid in self._drag.drag_start:
                item = self._comp_items.get(cid)
                if item is not None:
                    snapped = self.snap_point_gu(item.pos())
                    item.setPos(self.gu_to_scene(*snapped))
            self._drag.preview_component_drag()


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
                target, is_connectable = self.wire_snap_target(gu)
                if not self._wire_pts:
                    # Begin the wire — anchor the first vertex (pin or node).
                    self._wire_pts = [target]
                    self._refresh_wire_preview(target, is_connectable)
                else:
                    # Commit the previewed L (its corner + the target). A click
                    # on empty space drops an intermediate vertex and keeps
                    # routing; a connectable target (pin / wire) finalizes.
                    legs = self._route(self._wire_pts[-1], target)
                    self._wire_pts.extend(legs[1:])
                    if is_connectable and len(self._wire_pts) >= 2:
                        pts = self._wire_pts
                        self._wire_pts = []
                        self.add_wire(pts)
                        self._cancel_wire_preview()
                        # Terminating on a pin or existing wire returns to SELECT.
                        self.set_mode(Mode.SELECT)
                    else:
                        self._refresh_wire_preview(target, is_connectable)
                event.accept()
                return

        # SELECT mode: a press on a resizable component's terminal handle starts
        # an endpoint drag (takes priority over wire-auto-enter and vertex drag).
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            comp_id = self._drag.endpoint_handle_at(event.scenePos())
            if comp_id is not None:
                comp = self._component_by_id(comp_id)
                if comp is not None:
                    old_span = comp.span_override if comp.span_override is not None else REGISTRY[comp.kind].default_span
                    self._drag.endpoint_drag = (comp_id, 1, old_span)
                    self._drag.endpoint_press_gu = self.snap_point_gu(event.scenePos())
                    # Select the item so resize handles become visible.
                    item = self._comp_items.get(comp_id)
                    if item is not None:
                        self.clearSelection()
                        item.setSelected(True)
                    event.accept()
                    return

        # SELECT mode: a press on a wire's mid-label starts dragging it along the
        # wire (takes priority over vertex drag / selection).
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            mid_wid = self._mid_label_wire_at(event.scenePos())
            if mid_wid is not None:
                wire = self._wire_by_id(mid_wid)
                if wire is not None:
                    self._mid_label_drag = (mid_wid, wire.mid_label_pos)
                    item = self._wire_items.get(mid_wid)
                    if item is not None:
                        self.clearSelection()
                        item.setSelected(True)
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
                    self._drag.vertex_drag = (wire_id, idx, wire.points[idx])
                    self._drag.vertex_press_gu = self.snap_point_gu(event.scenePos())
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
                self._refresh_wire_preview(pin, True)
                event.accept()
                return

        # SELECT mode: record drag-start positions for a possible MoveCommand.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            # Capture wire selection NOW — super() can deselect non-movable items
            # (WireItem has ItemIsMovable=False) when it sets up the component drag.
            self._drag.drag_wire_ids = {
                item.wire.id
                for item in self.selectedItems()
                if isinstance(item, WireItem)
            }
            super().mousePressEvent(event)
            self._drag.drag_start = {
                item.component.id: item.component.position
                for item in self.selectedItems()
                if isinstance(item, ComponentItem)
            }
            return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        # Commit an endpoint drag if one is active.
        if self._drag.endpoint_drag is not None and event.button() == Qt.LeftButton:
            comp_id, _handle_idx, old_span = self._drag.endpoint_drag
            press_gu = self._drag.endpoint_press_gu
            self._drag.endpoint_drag = None
            self._drag.endpoint_press_gu = None
            gu = self.snap_point_gu(event.scenePos())
            if gu != press_gu:
                self._drag.commit_endpoint_drag(comp_id, old_span, gu)
            else:
                # No movement: clear any wire preview points set during the drag.
                for wire_item in self._wire_items.values():
                    wire_item.clear_preview_points()
            # On plain click (no movement) the item is already selected from press.
            event.accept()
            return

        # Commit a mid-label drag if one is active.
        if self._mid_label_drag is not None and event.button() == Qt.LeftButton:
            wid, _press_pos = self._mid_label_drag
            self._mid_label_drag = None
            wire = self._wire_by_id(wid)
            item = self._wire_items.get(wid)
            if wire is not None and item is not None:
                frac = wire_fraction_at_point(wire.points, self.scene_to_gu(event.scenePos()))
                item.clear_mid_label_preview()
                self.set_wire_mid_label_pos(wid, frac)
            event.accept()
            return

        # Commit a wire-vertex drag if one is active.
        if self._drag.vertex_drag is not None and event.button() == Qt.LeftButton:
            wire_id, idx, _orig = self._drag.vertex_drag
            press_gu = self._drag.vertex_press_gu
            self._drag.vertex_drag = None
            self._drag.vertex_press_gu = None
            gu = self.snap_point_gu(event.scenePos())
            item = self._wire_items.get(wire_id)
            if item is not None:
                item.clear_preview_points()  # drop visual preview
            # Distinguish a click from a drag by whether the *cursor* moved to a
            # different grid node — NOT by comparing the snap target to the
            # vertex's old position. A vertex can be grabbed from up to
            # VERTEX_HIT_GU away, so a stationary click whose snapped cursor
            # differs from the vertex would otherwise be misread as a drag and
            # teleport the vertex onto the cursor (e.g. onto a pin, spuriously
            # inserting a junction dot).
            if gu != press_gu:
                # Real drag: move the vertex to the snapped target (a pin or
                # another wire's vertex/segment forms a junction).
                target, _ = self.wire_snap_target(gu, exclude_wire_id=wire_id)
                self.move_wire_vertex(wire_id, idx, target)
            else:
                # Plain click (no grid movement): select the wire the cursor is
                # actually on, not necessarily the wire whose vertex was grabbed.
                # When a stub's endpoint sits on another wire's through-segment,
                # the grabbed vertex belongs to the stub, but a click on the
                # other wire must select that other wire. Selecting by nearest
                # segment (preferring a pass-through over an endpoint-touch) also
                # keeps short wires selectable near their ends.
                sel_id = self._click_select_wire_id(event.scenePos(), wire_id)
                sel_item = self._wire_items.get(sel_id)
                if sel_item is not None:
                    sel_item.setSelected(True)
            event.accept()
            return

        pending = self._mode == Mode.SELECT and bool(self._drag.drag_start)

        # Drop any wire drag-ghosts before committing; the MoveCommand below
        # rebuilds the wire items with their real (snapped) geometry.
        self._drag.clear_component_drag_preview()

        # Let Qt finish its own mouse-grab / drag bookkeeping FIRST. Pushing a
        # command (which reconciles items) before this returns can run while Qt
        # still treats the drag as in-progress, corrupting interaction state.
        super().mouseReleaseEvent(event)

        if pending:
            self._drag.commit_component_drag()

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._mode == Mode.WIRE and self._wire_pts:
            gu = self.snap_point_gu(event.scenePos())
            target, is_connectable = self.wire_snap_target(gu)
            if target != self._wire_pts[-1]:
                legs = self._route(self._wire_pts[-1], target)
                self._wire_pts.extend(legs[1:])
            pts = self._wire_pts
            self._wire_pts = []
            self.add_wire(pts)
            self._cancel_wire_preview()
            # Ending on a pin or existing wire returns to SELECT; ending in empty
            # space leaves an open endpoint and stays in WIRE mode for more routing.
            if is_connectable:
                self.set_mode(Mode.SELECT)
            event.accept()
            return
        # In SELECT mode, a double-click on a wire's rendered label (endpoint or
        # mid) edits it in place (parallels double-clicking a component's
        # rendered label). These run before the wire-body check so the label
        # isn't shadowed by the "double-click wire → WIRE mode" gesture.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            for it in self.items(event.scenePos()):
                if isinstance(it, _WireMidLabel):
                    parent = it.parentItem()
                    if isinstance(parent, WireItem):
                        parent.begin_label_edit("mid")
                        event.accept()
                        return
                if isinstance(it, _WireEndLabel):
                    parent = it.parentItem()
                    if isinstance(parent, WireItem):
                        parent.begin_label_edit(it.end)
                        event.accept()
                        return

        # In SELECT mode, a double-click on a *free* wire endpoint opens that
        # endpoint's label editor — so a label can be started even when none is
        # set yet (there is no rendered label to click). wire_vertex_at only
        # returns draggable vertices, so connected (pin-locked) endpoints and the
        # segment body fall through to the WIRE-mode routing gestures below.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            hit = self.wire_vertex_at(event.scenePos())
            if hit is not None:
                wid, idx = hit
                wire = self._wire_by_id(wid)
                item = self._wire_items.get(wid)
                if wire is not None and item is not None and idx in (0, len(wire.points) - 1):
                    item.begin_label_edit("start" if idx == 0 else "end")
                    event.accept()
                    return

        # In SELECT mode, a double-click on a wire body enters WIRE mode.
        # This check runs before the component check so that wires near (or
        # overlapping with) a component bounding box are not shadowed by the
        # component's hit area.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            start = self._wire_snap_point(event.scenePos())
            if start is not None:
                self.clearSelection()
                self._mode = Mode.WIRE
                self._apply_item_flags()
                self.mode_changed.emit(Mode.WIRE)
                self._wire_pts = [start]
                self._refresh_wire_preview(start, start in self._all_pin_positions())
                event.accept()
                return

        # In SELECT mode, a double-click on a label activates in-place editing;
        # a double-click on the component body opens the Properties Panel.
        if self._mode == Mode.SELECT:
            items = self.items(event.scenePos())
            for it in items:
                if isinstance(it, LabelTextItem):
                    it.begin_edit()
                    event.accept()
                    return
                # A per-side slot label (display only) maps to its component's
                # in-place editor — double-clicking a rendered label edits it.
                if isinstance(it, _SlotLabel):
                    parent = it.parentItem()
                    if isinstance(parent, ComponentItem):
                        parent.begin_options_edit()
                        self.component_double_clicked.emit(parent.component.id)
                        event.accept()
                        return
                if isinstance(it, ComponentItem):
                    # Start in-place options editing and open the Properties Panel.
                    it.begin_options_edit()
                    self.component_double_clicked.emit(it.component.id)
                    event.accept()
                    return

        # In SELECT mode, a double-click on blank canvas enters WIRE mode from
        # the snapped grid point (no wire or component was hit above).
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            gu = self.snap_point_gu(event.scenePos())
            self.clearSelection()
            self._mode = Mode.WIRE
            self._apply_item_flags()
            self.mode_changed.emit(Mode.WIRE)
            self._wire_pts = [gu]
            self._refresh_wire_preview(gu, False)
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

        # Minor grid: lines at every 0.25 GU within each integer cell. The 0.5
        # midline is drawn a touch stronger than the 0.25/0.75 lines so the
        # unit cell stays readable on the denser lattice.
        for frac in (0.25, 0.5, 0.75):
            pen = QPen(_GRID_SUB if frac == 0.5 else _GRID_SUB_FINE)
            pen.setWidth(0)
            painter.setPen(pen)
            off = GRID_PX * frac
            x = left
            while x < rect.right():
                painter.drawLine(QPointF(x + off, rect.top()), QPointF(x + off, rect.bottom()))
                x += GRID_PX
            y = top
            while y < rect.bottom():
                painter.drawLine(QPointF(rect.left(), y + off), QPointF(rect.right(), y + off))
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


# _component_pin_positions is the canonical implementation in app.schematic.model.
# Imported at the top of this module as component_pin_positions.
