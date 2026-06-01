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
    SetFilledCommand,
    SetFontSizeCommand,
    MoveCommand,
    MoveOptionsLabelCommand,
    MoveWireVertexCommand,
    PlaceCommand,
    ResizeCommand,
    RotateCommand,
    SplitWireCommand,
    UndoStack,
    WireCommand,
    reshape_wire_points,
)
from app.canvas.items import (
    ComponentItem,
    JunctionItem,
    LabelTextItem,
    OpenCircleItem,
    OpenItem,
    _ResizableTwoTerminalItem,
    WireItem,
    WirePreviewItem,
)
from app.canvas.style import GRID_PX
from app.components.registry import ITEM_CLASSES, REGISTRY
from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    component_pin_positions as _component_pin_positions,
    junction_points,
    open_endpoints,
    route,
    simplify_points,
    wire_corner_splits_at,
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
        self._wire_preview: WirePreviewItem | None = None

        # Drag-move bookkeeping: id -> position at drag start (GU)
        self._drag_start: dict[str, tuple[float, float]] = {}
        # Wire IDs selected at drag-start, captured before super() may deselect them.
        self._drag_wire_ids: set[str] = set()

        # Wire-vertex drag: (wire_id, index, original_point_gu) or None.
        self._vertex_drag: tuple[str, int, tuple[float, float]] | None = None
        # Snapped cursor position where the vertex grab began, to tell a click
        # (no grid movement → select) from a real drag (→ move the vertex).
        self._vertex_press_gu: tuple[float, float] | None = None

        # Endpoint drag for resizable components: (comp_id, handle_index, old_span) or None.
        # handle_index: 0 = origin handle (moves component), 1 = terminal handle.
        self._endpoint_drag: tuple[str, int, tuple[float, float]] | None = None
        self._endpoint_press_gu: tuple[float, float] | None = None

        # Wire ids currently showing a drag-preview (during a component drag),
        # so they can be cleared precisely on release.
        self._previewed_wire_ids: set[str] = set()

        # Copy/paste clipboard: deep copies of components and wires.
        self._clipboard_components: list[Component] = []
        self._clipboard_wires: list[Wire] = []

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
            bool(self._drag_start)             # component drag in progress
            or self._vertex_drag is not None   # wire vertex drag in progress
            or self._endpoint_drag is not None # endpoint resize in progress
            or bool(self._wire_pts)            # wire being drawn (has anchored points)
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
        any directly-selected wires.  If the deletion dissolves a T-junction
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
        comp = next((c for c in self._schematic.components if c.id == component_id), None)
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

        comp = next((c for c in self._schematic.components if c.id == component_id), None)
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
        # Snapped to 0.5 GU so rotated positions stay on the grid.
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

    def set_component_span(
        self,
        component_id: str,
        new_span: tuple[float, float] | None,
    ) -> None:
        """Set span_override without wire reshaping (for drawing annotations)."""
        comp = next(
            (c for c in self._schematic.components if c.id == component_id), None
        )
        if comp is None:
            return
        if comp.span_override == new_span:
            return
        from app.canvas.commands import SetSpanCommand
        self._push(SetSpanCommand(component_id, new_span, comp.span_override))

    def set_component_z_order(self, component_id: str, new_z: int) -> None:
        """Set z_order on a drawing annotation via an undoable SetZOrderCommand."""
        from app.components.model import DrawingComponent
        from app.canvas.commands import SetZOrderCommand
        comp = next(
            (c for c in self._schematic.components if c.id == component_id), None
        )
        if comp is None or not isinstance(comp, DrawingComponent) or comp.z_order == new_z:
            return
        self._push(SetZOrderCommand(component_id, new_z, comp.z_order))

    def set_text_node_font_size(self, component_id: str, new_size: float) -> None:
        """Set font_size on a TextNodeComponent via an undoable SetFontSizeCommand."""
        from app.components.model import TextNodeComponent
        comp = next(
            (c for c in self._schematic.components if c.id == component_id), None
        )
        if comp is None or not isinstance(comp, TextNodeComponent):
            return
        if comp.font_size == new_size:
            return
        self._push(SetFontSizeCommand(component_id, new_size, comp.font_size))

    def set_text_node_style(
        self,
        component_id: str,
        bold: bool,
        italic: bool,
        family: str,
    ) -> None:
        """Set font style on a TextNodeComponent via an undoable SetTextStyleCommand."""
        from app.components.model import TextNodeComponent
        from app.canvas.commands import SetTextStyleCommand
        comp = next(
            (c for c in self._schematic.components if c.id == component_id), None
        )
        if comp is None or not isinstance(comp, TextNodeComponent):
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
                item.apply_transform(t)
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

    def _wire_snap_point(
        self, scene_pt: QPointF
    ) -> tuple[float, float] | None:
        """Return the nearest wire vertex or segment point if within snap range.

        Like :meth:`wire_snap_target` but ignores component pins and the bare
        grid fallback — returns non-None only when the cursor is genuinely on
        or very close to an existing wire.  Used by the double-click-on-wire
        gesture to locate the start point for a new wire.
        """
        gu = self.snap_point_gu(scene_pt)
        vtx = self._nearest_wire_vertex_gu(gu)
        if vtx is not None:
            return vtx
        return self._nearest_wire_segment_point_gu(gu)

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

    @staticmethod
    def _dist2_to_segment(
        px: float, py: float,
        x0: float, y0: float, x1: float, y1: float,
    ) -> tuple[float, bool]:
        """Squared distance from (px,py) to segment (x0,y0)-(x1,y1).

        Returns ``(dist2, at_endpoint)`` where *at_endpoint* is True when the
        closest point is one of the segment's ends (the cursor only touches the
        tip) rather than its interior (the cursor passes through it).
        """
        dx, dy = x1 - x0, y1 - y0
        seg2 = dx * dx + dy * dy
        if seg2 == 0.0:
            return ((px - x0) ** 2 + (py - y0) ** 2, True)
        t = ((px - x0) * dx + (py - y0) * dy) / seg2
        at_end = t <= 0.0 or t >= 1.0
        t = max(0.0, min(1.0, t))
        cx, cy = x0 + t * dx, y0 + t * dy
        return ((px - cx) ** 2 + (py - cy) ** 2, at_end)

    def _wire_proximity_key(
        self, gx: float, gy: float, wire: Wire
    ) -> tuple[float, int] | None:
        """Sort key for how close (gx,gy) is to *wire*, or None if empty.

        Key is ``(rounded_dist2, endpoint_rank)`` where endpoint_rank is 0 when
        the closest point is in a segment interior (cursor passes through) and 1
        when it is only an endpoint touch. Smaller sorts as "more on the wire".

        A click that lands exactly on an intermediate vertex gets rank 0: the
        wire passes through that point (the vertex is shared by two adjacent
        segments), so it is a full interior hit even though both adjacent
        segments individually report ``at_end=True``.
        """
        best: tuple[float, int] | None = None
        pts = wire.points
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            d2, at_end = self._dist2_to_segment(gx, gy, x0, y0, x1, y1)
            key = (round(d2, 9), 1 if at_end else 0)
            if best is None or key < best:
                best = key
        # Promote rank to 0 if the best distance matches an intermediate vertex.
        # Each segment reports at_end=True for its shared endpoint, so without
        # this correction a click at an intermediate vertex is ranked 1 instead
        # of 0, losing unfairly to an adjacent wire stub.
        if best is not None and best[1] == 1:
            for vx, vy in pts[1:-1]:
                if round((gx - vx) ** 2 + (gy - vy) ** 2, 9) == best[0]:
                    best = (best[0], 0)
                    break
        return best

    def _click_select_wire_id(
        self, scene_pt: QPointF, grabbed_id: str
    ) -> str:
        """Wire to select for a click that grabbed a vertex of *grabbed_id*.

        Returns the wire the cursor is actually on — the closest segment within
        VERTEX_HIT_GU, preferring a pass-through over an endpoint-touch. On a
        true tie the grabbed wire wins, so a click where two wires overlap stays
        on the grabbed one. Falls back to *grabbed_id* if nothing is in range.
        """
        gx, gy = self.scene_to_gu(scene_pt)
        bound2 = VERTEX_HIT_GU * VERTEX_HIT_GU
        best_id: str | None = None
        best_key: tuple[float, int] | None = None
        for wire in self._schematic.wires:
            key = self._wire_proximity_key(gx, gy, wire)
            if key is None or key[0] > bound2:
                continue
            if best_key is None or key < best_key:
                best_key, best_id = key, wire.id
        if best_id is None:
            return grabbed_id
        grabbed = next(
            (w for w in self._schematic.wires if w.id == grabbed_id), None
        )
        if grabbed is not None:
            gk = self._wire_proximity_key(gx, gy, grabbed)
            if gk is not None and gk == best_key:
                return grabbed_id
        return best_id

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
            target, is_connectable = self.wire_snap_target(gu)
            self._refresh_wire_preview(target, is_connectable)
            event.accept()
            return

        if self._vertex_drag is not None:
            self._preview_vertex_drag(gu)
            event.accept()
            return

        if self._endpoint_drag is not None:
            self._preview_endpoint_drag(gu)
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

    # ------------------------------------------------------------------
    # Endpoint drag helpers (resizable components)
    # ------------------------------------------------------------------

    def _endpoint_handle_at(
        self, scene_pos: "QPointF"
    ) -> str | None:
        """Return comp_id if *scene_pos* is over the terminal resize handle of any
        OpenItem, regardless of selection state.  Checks selected items first."""
        candidates = list(self.selectedItems()) + [
            item for item in self._comp_items.values()
            if isinstance(item, _ResizableTwoTerminalItem) and item not in self.selectedItems()
        ]
        for item in candidates:
            if not isinstance(item, _ResizableTwoTerminalItem):
                continue
            local = item.mapFromScene(scene_pos)
            if item.terminal_handle_hit(local):
                return item.component.id
        return None

    def _preview_endpoint_drag(self, gu: tuple[float, float]) -> None:
        """Live visual update while dragging the terminal endpoint (model untouched)."""
        if self._endpoint_drag is None:
            return
        comp_id, _handle_idx, _old_span = self._endpoint_drag
        item = self._comp_items.get(comp_id)
        if not isinstance(item, _ResizableTwoTerminalItem):
            return
        comp = item.component
        ox, oy = comp.position
        dx_w = gu[0] - ox
        dy_w = gu[1] - oy
        r = comp.rotation % 360
        if r == 90:
            dx, dy = dy_w, -dx_w
        elif r == 180:
            dx, dy = -dx_w, -dy_w
        elif r == 270:
            dx, dy = -dy_w, dx_w
        else:
            dx, dy = dx_w, dy_w
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return
        item.set_preview_span((dx, dy))

    def _commit_endpoint_drag(
        self,
        comp_id: str,
        old_span: tuple[float, float],
        gu: tuple[float, float],
    ) -> None:
        """Commit a ResizeCommand for the dragged terminal endpoint."""
        item = self._comp_items.get(comp_id)
        if not isinstance(item, _ResizableTwoTerminalItem):
            return
        comp = item.component
        ox, oy = comp.position
        dx_w = gu[0] - ox
        dy_w = gu[1] - oy
        r = comp.rotation % 360
        if r == 90:
            dx, dy = dy_w, -dx_w
        elif r == 180:
            dx, dy = -dx_w, -dy_w
        elif r == 270:
            dx, dy = -dy_w, dx_w
        else:
            dx, dy = dx_w, dy_w
        dx = round(dx * 2) / 2
        dy = round(dy * 2) / 2
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            item.set_preview_span(old_span)
            return
        new_span = (dx, dy)
        if new_span == old_span:
            return
        cmd = ResizeCommand(comp_id, new_span, old_span)
        self._stack.push(cmd)
        self._rebuild_items()
        self.schematic_changed.emit()

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
        # Re-route the two segments that touch the moved vertex through the
        # shared horizontal-first primitive so the preview stays Manhattan
        # (mirrors MoveWireVertexCommand.do — identical corner convention).
        rebuilt: list[tuple[float, float]] = []
        for j, p in enumerate(pts):
            if j == 0:
                rebuilt.append(p)
                continue
            prev = pts[j - 1]
            if j == idx or j - 1 == idx:
                rebuilt.extend(route(prev, p, vfirst=False)[1:-1])
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

        # When every component is being dragged the whole circuit translates
        # rigidly, so free wire endpoints (open-circle nodes) move too.
        all_dragged = (
            set(self._drag_start.keys()) >= {c.id for c in self._schematic.components}
        )

        previewed: set[str] = set()
        for wire in self._schematic.wires:
            pts = wire.points
            if len(pts) < 2:
                continue
            # Mirror MoveCommand._reshape_wires exactly: selected wires and
            # all_dragged both force a rigid translate so free endpoints follow.
            if all_dragged or wire.id in self._drag_wire_ids:
                start_hit = end_hit = True
            else:
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
            if wi is not None and wi.preview_points is not None:
                preview_pts[wire.id] = wi.preview_points

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
        _junction_points = junction_points

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
            comp_id = self._endpoint_handle_at(event.scenePos())
            if comp_id is not None:
                comp = next((c for c in self._schematic.components if c.id == comp_id), None)
                if comp is not None:
                    from app.components.registry import REGISTRY as _REG
                    old_span = comp.span_override if comp.span_override is not None else _REG[comp.kind].default_span
                    self._endpoint_drag = (comp_id, 1, old_span)
                    self._endpoint_press_gu = self.snap_point_gu(event.scenePos())
                    # Select the item so resize handles become visible.
                    item = self._comp_items.get(comp_id)
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
                    self._vertex_drag = (wire_id, idx, wire.points[idx])
                    self._vertex_press_gu = self.snap_point_gu(event.scenePos())
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
            self._drag_wire_ids = {
                item.wire.id
                for item in self.selectedItems()
                if isinstance(item, WireItem)
            }
            super().mousePressEvent(event)
            self._drag_start = {
                item.component.id: item.component.position
                for item in self.selectedItems()
                if isinstance(item, ComponentItem)
            }
            return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        # Commit an endpoint drag if one is active.
        if self._endpoint_drag is not None and event.button() == Qt.LeftButton:
            comp_id, _handle_idx, old_span = self._endpoint_drag
            press_gu = self._endpoint_press_gu
            self._endpoint_drag = None
            self._endpoint_press_gu = None
            gu = self.snap_point_gu(event.scenePos())
            if gu != press_gu:
                self._commit_endpoint_drag(comp_id, old_span, gu)
            # On plain click (no movement) the item is already selected from press.
            event.accept()
            return

        # Commit a wire-vertex drag if one is active.
        if self._vertex_drag is not None and event.button() == Qt.LeftButton:
            wire_id, idx, _orig = self._vertex_drag
            press_gu = self._vertex_press_gu
            self._vertex_drag = None
            self._vertex_press_gu = None
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
            drag_wire_ids = list(self._drag_wire_ids)
            self._drag_start = {}
            self._drag_wire_ids = set()

            all_cmds: list = []
            for d, ids in per_delta.items():
                move_cmd = MoveCommand(ids, d, wire_ids=drag_wire_ids)
                split_cmds = self._pin_splits_after_delta(ids, d)
                all_cmds.append(move_cmd)
                all_cmds.extend(split_cmds)
            if len(all_cmds) == 1:
                self._push(all_cmds[0])
            elif all_cmds:
                self._push(MacroCommand(all_cmds, label="Move"))

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


# _component_pin_positions is the canonical implementation in app.schematic.model.
# Imported at the top of this module as component_pin_positions.
