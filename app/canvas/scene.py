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

import contextlib
import copy
import dataclasses
import logging
import uuid
from enum import Enum, auto

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF, QTransform
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSceneMouseEvent,
)

from app.canvas.commands import (
    Command,
    DeleteCommand,
    EditCommand,
    EditNodeSideCommand,
    EditNodeTextCommand,
    GroupRotateCommand,
    MacroCommand,
    MergeWireCommand,
    MirrorCommand,
    SetFontSizeCommand,
    SetVariantCommand,
    SetParamCommand,
    MoveCommand,
    MoveJunctionCommand,
    MoveOptionsLabelCommand,
    MoveWireVertexCommand,
    PlaceCommand,
    RotateCommand,
    SplitWireCommand,
    UndoStack,
    WireCommand,
)
from app.canvas.items import (
    AlignmentGuideItem,
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
from app.canvas import style
from app.canvas.style import GRID_PX
from app.canvas.wiregeometry import WireGeometry
from app.codegen.circuitikz import is_node_style
from app.components.registry import ITEM_CLASSES, REGISTRY
from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    WIRE_LABEL_PLACEMENTS,
    WIRE_LINE_STYLE_CYCLE,
    WIRE_MARKER_CYCLE,
    component_pin_positions as _component_pin_positions,
    coord_on_grid as _model_coord_on_grid,
    INVERSION_BUBBLE_KINDS,
    gate_body_anchor_side,
    is_box_kind,
    is_terminal_marker,
    junction_points,
    open_endpoints,
    point_key,
    unconnected_pins,
    route,
    route_pin_aware,
    simplify_points,
    wire_corner_splits_at,
    wire_crossings,
    wire_fraction_at_point,
    wire_splits_at,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Snap / proximity constants live in app.canvas.geometry; the wire-snap radii
# are used by WireGeometry.

# Grid line colours live in the switchable palette (app/canvas/style.py) and are
# read at paint time in drawBackground(), so the grid follows a light/dark swap.

_LABEL_CLEARANCE = 6  # px gap used by auto-placement candidates (§8.3)

_log = logging.getLogger(__name__)


class Mode(Enum):
    """Canvas interaction mode (spec §6.1)."""

    SELECT = auto()
    PLACE = auto()
    WIRE = auto()
    PAN = auto()


#: Annotation kinds placed by a two-click span gesture (click start, click end)
#: rather than the single-click ghost-follow placement (spec §6.2). The first
#: click anchors the origin; the ghost then spans origin→cursor; the second click
#: commits the span. The voltage (``open``) and current (``short``) annotations.
_SPAN_PLACE_KINDS: frozenset[str] = frozenset({"open", "short"})


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
        # Keep the canvas label renderer in step with this document's preamble
        # settings from the outset (covers the startup document, which is never
        # routed through set_schematic). See sync_label_preamble.
        self.sync_label_preamble()
        # When non-None, _push() accumulates commands here for one MacroCommand
        # (see batch()); used for multi-component inspector edits.
        self._batch: list | None = None

        # Re-entrancy guard for _rebuild_items: True while a reconcile pass is
        # running; a re-entrant request (e.g. a selectionChanged handler pushing
        # a command mid-rebuild) sets _rebuild_pending instead of recursing, and
        # the outer pass loops until clean (see _rebuild_items).
        self._rebuilding = False
        self._rebuild_pending = False

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
        # The "mark unconnected pins" (§10.8) and "line-hops" (§6.4) display options
        # now live on the **document** (Schematic.mark_unconnected_pins / line_hops),
        # read straight from self._schematic at render time so they travel with the
        # file and undo/redo of a document-property change just works.

        # Placement state
        self._place_kind: str | None = None
        self._place_rotation: int = 0
        self._place_mirror: bool = False
        self._ghost: ComponentItem | None = None
        # Two-click span placement (open/short annotations, §6.2): the clicked
        # origin once the first click lands, else None (awaiting the first click).
        self._place_start_gu: tuple[float, float] | None = None

        # Paste placement (§6.7): when the clipboard is pasted via the keyboard /
        # Edit menu, the group follows the cursor as ghosts (a sub-state of PLACE
        # mode) until a click commits it — so the user positions the pins instead
        # of pasting blind. ``_paste_anchor_gu`` is the clipboard group's min
        # corner (None when not pasting); ``_paste_ghosts`` pairs each ghost item
        # with its model-space (GU) base position so the group translates rigidly.
        self._paste_anchor_gu: tuple[float, float] | None = None
        self._paste_ghosts: list[tuple[QGraphicsItem, tuple[float, float]]] = []
        # Last cursor position (snapped GU), tracked on every move so a paste can
        # spawn its ghosts under the cursor immediately rather than at the origin.
        self._last_cursor_gu: tuple[float, float] | None = None

        # Wire-routing state
        self._wire_pts: list[tuple[float, float]] = []
        # Routing style for newly drawn wires: "manhattan" (axis-only) or "laplata"
        # (45° legs, §6.4). Editor state, not persisted (it shapes the router, not
        # stored geometry). Selected from the wiring quick-bar.
        self._wire_routing: str = "manhattan"
        self._wire_preview: WirePreviewItem | None = None
        # Faint guide line(s) shown when a wire end snaps onto a pin's off-grid
        # x/y axis line (lazily created; see _show_guides / _clear_guides).
        self._guide_item: AlignmentGuideItem | None = None
        # Cursor-heading memory for the in-progress wire (so the elbow follows the
        # path the cursor took; see _wire_vfirst / _update_wire_heading).
        self._wire_heading: str | None = None

        # Sticky style for newly drawn wires (§6.4). Captured from the most
        # recently selected single wire, applied to every new wire so the user
        # can pick a template wire (e.g. one with an arrow endpoint) and keep
        # drawing wires in that style.
        self._new_wire_style: dict = {
            "line_style": "", "line_width": 0.4, "start_marker": "", "end_marker": "",
        }

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
        # Together with _remove_item (which releases the mouse grab before any
        # removal) and the _rebuild_items re-entrancy guard, this makes item
        # teardown structurally safe rather than ordering-dependent.
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

    def retypeset_labels(self) -> None:
        """Re-render every on-canvas math label with the current math engine.

        Used after the label-rendering engine changes (e.g. toggling the ziamath
        debug preference, §10.8) so existing labels refresh without reopening the
        document.  Walks all items and re-issues the render for any that expose a
        ``retypeset`` (component slot/inline labels and wire labels)."""
        for it in self.items():
            fn = getattr(it, "retypeset", None)
            if callable(fn):
                fn()

    def relayout_annotations(self) -> None:
        """Re-lay out every component's per-side slot labels and voltage/current
        decorations. Used after the document voltage/current style changes (§7.2)
        so the ± signs / arrows on existing components update without reopening."""
        for it in self._comp_items.values():
            it._sync_options_item()

    def sync_label_preamble(self) -> None:
        """Mirror the document's LaTeX preamble settings (§7.2) into the canvas
        label renderer, so unit macros (``\\qty``, ``\\unit``) render on canvas
        when siunitx is enabled — the on-canvas typesetter is isolated from the
        pdflatex preview and otherwise wouldn't know the macro. Only siunitx is
        forwarded (the free-form custom preamble may contain circuitikz-only
        commands that the bare label document can't compile)."""
        from app.preview import mathrender
        mathrender.set_label_preamble(
            r"\usepackage{siunitx}" if self._schematic.siunitx else ""
        )

    def set_schematic(self, schematic: Schematic) -> None:
        """Replace the document (e.g. after File ▸ Open). Clears undo history."""
        self._schematic = schematic
        self._stack = UndoStack(schematic)
        self.sync_label_preamble()   # before _rebuild_items typesets the labels
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
        self._place_start_gu = None
        self._mode = Mode.PLACE
        self._apply_item_flags()
        # Span-placed annotations (open/short) show no ghost until the first click
        # anchors the origin; every other kind follows the cursor immediately.
        if kind not in _SPAN_PLACE_KINDS:
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
        # While batching (see batch()), apply each command IMMEDIATELY and
        # collect it: a later command in the batch then captures fresh old
        # values and runs its no-op guards against live state (deferring do()
        # to the flush would make both read stale, pre-batch state). The flush
        # records the collected commands as one MacroCommand without
        # re-executing them.
        if self._batch is not None:
            command.do(self._schematic)
            self._batch.append(command)
            return
        self._stack.push(command)
        self._rebuild_items()
        self.schematic_changed.emit()

    @contextlib.contextmanager
    def batch(self, label: str = "Edit"):
        """Group all mutations made inside the block into a single undoable step.

        Every scene mutation routes through :meth:`_push`; inside this context
        each command is **applied immediately** (so subsequent commands in the
        batch see its effect) and collected; on exit the group is recorded on
        the undo stack as one ``MacroCommand`` — without re-executing — via
        ``UndoStack.record`` (a single undo entry, one ``_rebuild_items`` + one
        ``schematic_changed``). Used for multi-component inspector edits.
        Re-entrant: a nested ``batch`` joins the outer one and flushes only at
        the outermost level.
        """
        if self._batch is not None:
            yield  # already batching → join the existing group
            return
        self._batch = []
        try:
            yield
        finally:
            cmds = self._batch
            self._batch = None
            if cmds:
                # Commands already ran at push time; record without re-applying.
                self._stack.record(
                    cmds[0] if len(cmds) == 1 else MacroCommand(cmds, label)
                )
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
        span_override: tuple[float, float] | None = None,
    ) -> Component:
        """Place a component at *position* (GU) via an undoable PlaceCommand.

        *span_override* sets a resizable two-terminal annotation's terminal offset
        (used by two-click span placement, §6.2); ``None`` keeps the registry
        ``default_span``."""
        defn = REGISTRY[kind]
        cls = defn.component_class
        extra: dict = {}
        if is_box_kind(kind):
            extra["z_order"] = -10
        if span_override is not None:
            extra["span_override"] = span_override
        # Logic gates are placed compact (a 0.25-GU input pitch); other kinds
        # keep the default scale of 1.0.
        from app.components import library
        default_scale = library.default_scale(kind)
        if abs(default_scale - 1.0) > 1e-9:
            extra["scale"] = default_scale
        # A terminal marker (junction dot) keeps the exact *position* it was placed
        # at — snapped by ``_place_target`` to the union of the grid and the connection
        # points, which may be off the grid (a manual-library / scaled-gate terminal).
        # Every other kind is grid-snapped defensively so a caller can't drop it off-grid.
        if is_terminal_marker(kind):
            pos = (position[0], position[1])
        else:
            pos = (self.snap_gu(position[0]), self.snap_gu(position[1]))
        # Smart default: an inversion bubble dropped on a logic-gate body anchor gets a
        # default placement side pointing away from the body (so it lands tangent). It's
        # only a default — the user can change it in the inspector. (ocirc/notcirc have a
        # centred pin, so the pin position is the placement position.)
        if kind in INVERSION_BUBBLE_KINDS:
            side = gate_body_anchor_side(self._schematic, pos)
            if side:
                extra["node_side"] = side
        comp = cls(
            id=str(uuid.uuid4()),
            kind=kind,
            position=pos,
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
        exclude_wire_ids: frozenset[str] = frozenset(),
    ) -> list[SplitWireCommand]:
        """Build SplitWireCommands for any *points* that land mid-segment or at a corner.

        For each point that lies strictly inside an existing wire's segment
        (per :func:`wire_splits_at`) or at an existing wire's intermediate
        vertex (per :func:`wire_corner_splits_at`), produce a split command so
        the connection becomes real topology and a junction dot appears.
        *exclude_wire_id* / *exclude_wire_ids* skip wires that must not split
        themselves (e.g. the dragged wire, or every wire in a junction group).
        """
        excludes = set(exclude_wire_ids)
        if exclude_wire_id is not None:
            excludes.add(exclude_wire_id)
        cmds: list[SplitWireCommand] = []
        seen: set[tuple[str, tuple[float, float]]] = set()
        for pt in points:
            hits = wire_splits_at(self._schematic, pt) + wire_corner_splits_at(self._schematic, pt)
            for wire_id, idx in hits:
                if wire_id in excludes:
                    continue
                key = (wire_id, point_key(pt))
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
        wire = Wire(
            id=str(uuid.uuid4()),
            points=pts,
            line_style=self._new_wire_style["line_style"],
            line_width=self._new_wire_style["line_width"],
            start_marker=self._new_wire_style["start_marker"],
            end_marker=self._new_wire_style["end_marker"],
        )

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

    def cut_selection(self) -> None:
        """Copy the selection to the clipboard, then delete it (one undoable
        delete). A no-op when nothing is selected."""
        if not self.selected_component_ids() and not self.selected_wire_ids():
            return
        self.copy_selection()
        self.delete_selected()

    def _clipboard_min_corner(self) -> tuple[float, float]:
        """Top-left (min x, min y) of the clipboard group in GU — the anchor used
        to position a cursor-targeted paste. Caller guarantees a non-empty
        clipboard."""
        xs: list[float] = [c.position[0] for c in self._clipboard_components]
        ys: list[float] = [c.position[1] for c in self._clipboard_components]
        for w in self._clipboard_wires:
            xs.extend(x for x, _ in w.points)
            ys.extend(y for _, y in w.points)
        return (min(xs), min(ys))

    def begin_paste(self) -> None:
        """Start an interactive paste: the clipboard group follows the cursor as
        ghosts (a sub-state of PLACE mode) until a left-click commits it at the
        cursor, or Escape / right-click cancels (§6.7).

        This is the keyboard / Edit-menu entry point. Unlike the blind fixed-offset
        paste, it lets the user position the group before it lands, so pasted pins
        do not silently split wires or connect to whatever sat under the default
        offset. No-op when the clipboard is empty (nothing to place)."""
        if not self._clipboard_components and not self._clipboard_wires:
            return
        self._cancel_wire()
        self._cancel_placement()          # drop any in-progress single-kind ghost
        self._paste_anchor_gu = self._clipboard_min_corner()
        self._mode = Mode.PLACE
        self._apply_item_flags()
        self._spawn_paste_ghosts()
        # Show the group under the cursor right away (else at its original anchor,
        # until the first move) so it reads as "attached to the pointer".
        self._move_paste_ghosts(self._last_cursor_gu or self._paste_anchor_gu)
        self.mode_changed.emit(Mode.PLACE)

    def _spawn_paste_ghosts(self) -> None:
        """Create ghost items for every clipboard component and wire, recording
        each one's model-space base position so the group can translate rigidly."""
        self._cancel_paste_ghosts()
        for comp in self._clipboard_components:
            cls = ITEM_CLASSES.get(comp.kind, ComponentItem)
            ghost = cls(copy.deepcopy(comp))   # carries the copy's rotation/mirror/scale
            ghost.set_ghost(True)
            ghost.setFlag(ghost.GraphicsItemFlag.ItemIsSelectable, False)
            ghost.setFlag(ghost.GraphicsItemFlag.ItemIsMovable, False)
            ghost.setZValue(1000)
            self.addItem(ghost)
            self._paste_ghosts.append((ghost, comp.position))
        for wire in self._clipboard_wires:
            # WirePreviewItem with no cursor draws a static polyline (ghost dash +
            # vertex dots) through the wire's points; base (0,0) so setPos shifts it.
            gw = WirePreviewItem()
            gw.set_path(wire.points, None)
            self.addItem(gw)
            self._paste_ghosts.append((gw, (0.0, 0.0)))

    def _move_paste_ghosts(self, cursor_gu: tuple[float, float]) -> None:
        """Translate every paste ghost so the group's anchor sits at *cursor_gu*."""
        if self._paste_anchor_gu is None:
            return
        dx = cursor_gu[0] - self._paste_anchor_gu[0]
        dy = cursor_gu[1] - self._paste_anchor_gu[1]
        for item, (bx, by) in self._paste_ghosts:
            item.setPos(self.gu_to_scene(bx + dx, by + dy))

    def _cancel_paste_ghosts(self) -> None:
        for item, _ in self._paste_ghosts:
            self._remove_item(item)
        self._paste_ghosts = []

    def paste(self, at: tuple[float, float] | None = None) -> None:
        """Paste clipboard contents with new UUIDs.

        With *at* (snapped GU coordinates, e.g. the right-click point or the
        commit point of an interactive :meth:`begin_paste`), the group's top-left
        corner is anchored there — a "paste here". Without it the group is offset
        by a fixed 1 GU so a repeat paste does not land exactly on the original.
        Either offset is a multiple of the grid, so pasted items stay grid-valid.
        """
        if not self._clipboard_components and not self._clipboard_wires:
            return
        if at is None:
            off = (1.0, 1.0)
        else:
            mx, my = self._clipboard_min_corner()
            off = (at[0] - mx, at[1] - my)
        ox, oy = off
        cmds: list[Command] = []
        new_comp_ids: list[str] = []
        new_wire_ids: list[str] = []
        new_comps: list = []
        for comp in self._clipboard_components:
            new_id = str(uuid.uuid4())
            new_comp = copy.deepcopy(comp)
            new_comp.id = new_id
            new_comp.position = (comp.position[0] + ox, comp.position[1] + oy)
            cmds.append(PlaceCommand(new_comp))
            new_comp_ids.append(new_id)
            new_comps.append(new_comp)
        for wire in self._clipboard_wires:
            new_wire = copy.deepcopy(wire)
            new_wire.id = str(uuid.uuid4())
            new_wire.points = [(x + ox, y + oy) for x, y in wire.points]
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
        # Collect all pin positions for the components being deleted
        # (point_key'd — the single connectivity convention).
        deleted_pin_positions: set[tuple[float, float]] = set()
        comp_id_set = set(comp_ids)
        for comp in self._schematic.components:
            if comp.id in comp_id_set:
                for pos in _component_pin_positions(comp):
                    deleted_pin_positions.add(point_key(pos))

        # Wire IDs being removed: explicit selection + pin-connected.
        explicit = set(wire_ids)
        removed_ids: set[str] = set(explicit)
        for wire in self._schematic.wires:
            if len(wire.points) < 2:
                continue
            ends = (point_key(wire.points[0]), point_key(wire.points[-1]))
            if any(p in deleted_pin_positions for p in ends):
                removed_ids.add(wire.id)

        # Collect all free endpoints of the wires being removed.
        candidate_points: set[tuple[float, float]] = set()
        for wire in self._schematic.wires:
            if wire.id in removed_ids and len(wire.points) >= 2:
                candidate_points.add(point_key(wire.points[0]))
                candidate_points.add(point_key(wire.points[-1]))

        # Pin positions of ALL surviving components.
        surviving_pins: set[tuple[float, float]] = set()
        for comp in self._schematic.components:
            if comp.id not in comp_id_set:
                for pos in _component_pin_positions(comp):
                    surviving_pins.add(point_key(pos))

        # For each candidate point, count surviving wire endpoints there. Two
        # merges may reference the SAME surviving wire (one wire bridging two
        # dissolved junctions); MergeWireCommand re-resolves consumed wire ids
        # at do() time so the sequential merges compose into one wire.
        merge_cmds: list[MergeWireCommand] = []
        for pt in sorted(candidate_points):
            if pt in surviving_pins:
                continue
            neighbors = [
                w for w in self._schematic.wires
                if w.id not in removed_ids and len(w.points) >= 2
                and (point_key(w.points[0]) == pt or point_key(w.points[-1]) == pt)
            ]
            if len(neighbors) == 2:
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

    def edit_component_node_text(self, component_id: str, new_text: str) -> None:
        """Replace the ``node_text`` (the ``{…}`` slot) of a node-style component
        via an undoable EditNodeTextCommand. No-op when unchanged or unknown."""
        comp = self._component_by_id(component_id)
        if comp is None or comp.node_text == new_text:
            return
        self._push(EditNodeTextCommand(component_id, new_text))

    def edit_component_node_side(self, component_id: str, new_side: str) -> None:
        """Set the ``node_side`` (placement keyword) of a single-terminal node via an
        undoable EditNodeSideCommand. No-op when unchanged or unknown."""
        comp = self._component_by_id(component_id)
        if comp is None or getattr(comp, "node_side", "") == new_side:
            return
        self._push(EditNodeSideCommand(component_id, new_side))

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
        """Rotate the selection CW around its group centroid in **45° steps** (§6.x).

        Components rotate by 45° (their pins land off-grid, reached by the wire
        magnet / pin-axis alignment, §3.1); connected wires follow. **When wires are
        explicitly selected the step falls back to 90°**, since rotating free wire
        vertices by 45° would leave them off-grid on no pin axis (invalid)."""
        if self._mode == Mode.PLACE:
            # Span-placed annotations take their orientation from the two clicks,
            # so rotation does not apply during their placement.
            if self._place_kind not in _SPAN_PLACE_KINDS:
                self._place_rotation = (self._place_rotation + 45) % 360
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

        if len(comp_ids) == 1 and not wire_ids:
            # A lone component spins in place at any angle: use its **exact** position
            # as the pivot (no snap), so a 45° turn doesn't drift it off its spot.
            only = next(c for c in self._schematic.components if c.id == comp_ids[0])
            cx, cy = only.position
        else:
            cx = _snap((min(xs) + max(xs)) / 2)
            cy = _snap((min(ys) + max(ys)) / 2)
        # 45° for a components-only rotation; 90° when wires are selected (free wire
        # vertices can't rotate validly off the right angles).
        step = 90 if wire_ids else 45
        self._push(GroupRotateCommand(comp_ids, wire_ids, (cx, cy), step=step))

    def mirror_component(self, component_id: str, new_mirror: bool) -> None:
        """Set the mirror state of a component via an undoable MirrorCommand."""
        self._push(MirrorCommand(component_id, new_mirror))

    def set_component_variant(self, component_id: str, name: str, value: bool) -> None:
        """Toggle a named boolean variant on a component (undoable, generic)."""
        self._push(SetVariantCommand(component_id, name, value))

    def set_component_param(self, component_id: str, name: str, value: int) -> None:
        """Set a named integer parameter on a component (undoable, generic)."""
        self._push(SetParamCommand(component_id, name, value))

    def set_fill_color(self, component_id: str, new_fill: str) -> None:
        """Set fill_color on a StyledComponent (bipole or rect) via an undoable command."""
        from app.components.model import StyledComponent
        from app.canvas.commands import SetFillColorCommand
        comp = self._component_by_id(component_id)
        if comp is None or not isinstance(comp, StyledComponent) or comp.fill_color == new_fill:
            return
        self._push(SetFillColorCommand(component_id, new_fill, comp.fill_color))

    def set_component_line_width(self, component_id: str, new_width: float) -> None:
        """Set the unified stroke/outline width (``line_width``) on a component via
        an undoable command — works for both circuit symbols and block kinds
        (rect/circle/bipole), which no longer carry a separate border width."""
        from app.canvas.commands import SetComponentLineWidthCommand
        comp = self._component_by_id(component_id)
        if comp is None or abs(comp.line_width - new_width) < 1e-6:
            return
        self._push(SetComponentLineWidthCommand(component_id, new_width, comp.line_width))

    def set_component_scale(self, component_id: str, new_scale: float) -> None:
        """Set a logic gate's size multiplier (``scale``) via an undoable command."""
        from app.canvas.commands import SetComponentScaleCommand
        comp = self._component_by_id(component_id)
        if comp is None or abs(comp.scale - new_scale) < 1e-6:
            return
        self._push(SetComponentScaleCommand(component_id, new_scale, comp.scale))

    def set_node_resize_factors(
        self, component_id: str, wf: float, hf: float
    ) -> None:
        """Set an anisotropic node's ``(wf, hf)`` resize factors (``span_override``) via
        an undoable ResizeNodeCommand — the inspector Size fields' write path, the same
        command the corner-drag uses. Resizes about the origin (no position shift);
        no-op when unchanged or not an anisotropic resizable node."""
        from app.canvas.commands import ResizeNodeCommand
        from app.schematic.model import is_resizable_node, node_resize_factors
        comp = self._component_by_id(component_id)
        if comp is None or not is_resizable_node(comp):
            return
        old = node_resize_factors(comp)            # (wf, hf) or None at natural size
        new = (round(wf, 6), round(hf, 6))
        if old is not None and abs(old[0] - new[0]) < 1e-9 and abs(old[1] - new[1]) < 1e-9:
            return
        self._push(ResizeNodeCommand(component_id, new, old))

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
        # Editing a wire's style (inspector or Tab-cycle) makes it sticky for
        # new wires.
        self._new_wire_style["line_style"] = new_style
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.line_style == new_style:
            return
        self._push(SetWireLineStyleCommand(wire_id, new_style, wire.line_style))

    def set_wire_line_width(self, wire_id: str, new_width: float) -> None:
        """Set line_width (pt) on a wire via an undoable command (no-op if unchanged)."""
        from app.canvas.commands import SetWireLineWidthCommand
        self._new_wire_style["line_width"] = new_width
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

    def set_wire_hop_mode(self, wire_id: str, mode: str) -> None:
        """Set a wire's hop_mode (``""``/``"never"``/``"always"``) undoably (§6.4).

        ``"never"`` = this wire never hops (but may be hopped over); ``"always"``
        = it always hops at crossings (overriding the global preference and
        z-order); ``""`` = follow the global preference and z-order."""
        from app.canvas.commands import SetWireHopModeCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.hop_mode == mode:
            return
        self._push(SetWireHopModeCommand(wire_id, mode, wire.hop_mode))

    def set_wire_z_order(self, wire_id: str, value: int) -> None:
        """Set a wire's z_order via an undoable command (no-op if unchanged).

        z_order layers the wire (front/back) and decides which wire hops at a
        crossing — the higher z_order arcs over the other (see wire_crossings)."""
        from app.canvas.commands import SetWireZOrderCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.z_order == value:
            return
        self._push(SetWireZOrderCommand(wire_id, value, wire.z_order))

    def set_wire_start_marker(self, wire_id: str, marker: str) -> None:
        """Set the custom start-endpoint marker on a wire (no-op if unchanged)."""
        from app.canvas.commands import SetWireStartMarkerCommand
        self._new_wire_style["start_marker"] = marker
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.start_marker == marker:
            return
        self._push(SetWireStartMarkerCommand(wire_id, marker, wire.start_marker))

    def set_wire_end_marker(self, wire_id: str, marker: str) -> None:
        """Set the custom end-endpoint marker on a wire (no-op if unchanged)."""
        from app.canvas.commands import SetWireEndMarkerCommand
        self._new_wire_style["end_marker"] = marker
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

    def set_wire_start_label_placement(self, wire_id: str, placement: str) -> None:
        """Set the start label's placement ("" / "above" / "below"; no-op if unchanged)."""
        from app.canvas.commands import SetWireStartLabelPlacementCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.start_label_placement == placement:
            return
        self._push(
            SetWireStartLabelPlacementCommand(wire_id, placement, wire.start_label_placement)
        )

    def set_wire_end_label_placement(self, wire_id: str, placement: str) -> None:
        """Set the end label's placement ("" / "above" / "below"; no-op if unchanged)."""
        from app.canvas.commands import SetWireEndLabelPlacementCommand
        wire = self._wire_by_id(wire_id)
        if wire is None or wire.end_label_placement == placement:
            return
        self._push(
            SetWireEndLabelPlacementCommand(wire_id, placement, wire.end_label_placement)
        )

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

        Priority by what the cursor is over:

        1. a rendered **endpoint label** (`_WireEndLabel`) → cycle that label's
           *placement* (off-end → above/left → below/right → off-end);
        2. a wire **endpoint** (free or connected — connected endpoints are
           draggable, so :meth:`wire_vertex_at` returns them) → cycle that end's
           *marker* (none → arrow → stealth → open → bar → none) — so an
           arrowhead into a block-diagram shape is cyclable with Tab;
        3. a wire **body** (or interior vertex) → cycle the *line style*
           (solid → dashed → dotted → dash-dot → solid).

        *backward* (Shift+Tab) steps the other way.
        """
        # 1. Over a rendered endpoint label → cycle its placement.
        for it in self.items(scene_pt):
            if isinstance(it, _WireEndLabel):
                parent = it.parentItem()
                if isinstance(parent, WireItem):
                    self._cycle_wire_label_placement(parent.wire.id, it.end, backward)
                    return True
        # 2. Over a wire endpoint → cycle that endpoint's marker.
        hit = self.wire_vertex_at(scene_pt)
        if hit is not None:
            wid, idx = hit
            wire = self._wire_by_id(wid)
            if wire is not None and idx in (0, len(wire.points) - 1):
                self._cycle_wire_marker(wid, "start" if idx == 0 else "end", backward)
                return True
        # 3. Over a wire body → cycle the line style.
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

    def _cycle_wire_label_placement(self, wire_id: str, end: str, backward: bool) -> None:
        wire = self._wire_by_id(wire_id)
        if wire is None:
            return
        if end == "start":
            self.set_wire_start_label_placement(
                wire_id,
                self._cycle_value(WIRE_LABEL_PLACEMENTS, wire.start_label_placement, backward),
            )
        else:
            self.set_wire_end_label_placement(
                wire_id,
                self._cycle_value(WIRE_LABEL_PLACEMENTS, wire.end_label_placement, backward),
            )

    def set_component_z_order(self, component_id: str, new_z: int) -> None:
        """Set z_order on any component via an undoable SetZOrderCommand."""
        from app.canvas.commands import SetZOrderCommand
        comp = self._component_by_id(component_id)
        if comp is None or comp.z_order == new_z:
            return
        self._push(SetZOrderCommand(component_id, new_z, comp.z_order))

    def _z_ordered_objects(self) -> list[tuple[str, int]]:
        """All objects carrying a z_order — every component and wire — as
        ``(id, z_order)`` pairs. Components and wires share one stack (the canvas
        ``setZValue`` and the codegen background/foreground blocks), so front/back
        ordering spans both."""
        out: list[tuple[str, int]] = [
            (c.id, c.z_order) for c in self._schematic.components
        ]
        out += [(w.id, w.z_order) for w in self._schematic.wires]
        return out

    def _set_z_order(self, obj_id: str, new_z: int) -> None:
        """Dispatch to the wire or drawing-component z-order setter by id."""
        if self._wire_by_id(obj_id) is not None:
            self.set_wire_z_order(obj_id, new_z)
        else:
            self.set_component_z_order(obj_id, new_z)

    def bring_to_front(self, obj_id: str) -> int:
        """Raise a wire or drawing component above all other z-ordered objects.

        Includes ``0`` as a baseline so "front" is always ``>= 1`` — in front of
        the plain circuit elements (which sit at z 0). Returns the new z_order."""
        others = [z for oid, z in self._z_ordered_objects() if oid != obj_id]
        new_z = max(others + [0]) + 1
        self._set_z_order(obj_id, new_z)
        return new_z

    def send_to_back(self, obj_id: str) -> int:
        """Lower a wire or drawing component below all other z-ordered objects.

        Includes ``0`` as a baseline so "back" is always ``<= -1`` — behind the
        plain circuit elements. Returns the new z_order."""
        others = [z for oid, z in self._z_ordered_objects() if oid != obj_id]
        new_z = min(others + [0]) - 1
        self._set_z_order(obj_id, new_z)
        return new_z

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
            # Span-placed annotations take their orientation from the two clicks.
            if self._place_kind not in _SPAN_PLACE_KINDS:
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

        **Grab safety (structural):** if the item being removed — or any of its
        child items — currently holds the scene's mouse grab (a command was
        pushed mid-gesture, e.g. from inside a mouse handler), the grab is
        explicitly released *before* removal. Qt therefore never finalises a
        drag against a freed item, regardless of where in an event handler the
        removal happens; callers no longer need to sequence their pushes after
        ``super().mouseReleaseEvent()`` for memory safety.
        """
        if item is None:
            return
        grabber = self.mouseGrabberItem()
        if grabber is not None:
            # Walk up from the grabber: removing an ancestor destroys the
            # grabbing child with it, so the grab must be released first.
            node = grabber
            while node is not None:
                if node is item:
                    grabber.ungrabMouse()
                    break
                node = node.parentItem()
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
        Qt's grab valid across the command. (Items that genuinely must go are
        removed through ``_remove_item``, which releases the mouse grab first,
        so even a removal mid-gesture cannot dangle.)

        **Re-entrancy (structural):** the reconcile pass can re-enter itself —
        ``removeItem``/``setSelected`` fire ``selectionChanged``, and a handler
        on it (or on a signal it cascades to) may push a command, which calls
        back into ``_rebuild_items`` synchronously. Recursing would reconcile
        against a half-updated item map. Instead, a re-entrant call only flags
        ``_rebuild_pending`` and returns; the outermost call loops until no new
        request arrived, so the final pass always reflects the latest model
        state and the synchronous model→items contract is preserved.
        """
        if self._rebuilding:
            # Re-entered from inside a reconcile pass (signal handler pushed a
            # command). Defer: the outer pass reruns until clean.
            _log.warning(
                "re-entrant _rebuild_items call deferred (triggered from "
                "within a rebuild — likely a selectionChanged/schematic_changed "
                "handler mutating the model)"
            )
            self._rebuild_pending = True
            return
        self._rebuilding = True
        try:
            while True:
                self._rebuild_pending = False
                self._rebuild_items_now()
                if not self._rebuild_pending:
                    break
        finally:
            self._rebuilding = False

    def _rebuild_items_now(self) -> None:
        """One reconcile pass (the body of :meth:`_rebuild_items`); never call
        directly — the wrapper provides the re-entrancy guard."""
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
        # Line-hop bumps (decoration where wires cross without connecting, §6.4),
        # grouped per hopping wire so each item can paint its own arcs. Computed
        # once here (mirrors junction_points) and fed to items like locked_indices.
        # Default-mode wires hop only when the global preference is on; per-wire
        # hop_mode overrides ("always"/"never") apply either way, so always run.
        hops_by_wire: dict[str, list] = {}
        for hop in wire_crossings(self._schematic, default_on=self._schematic.line_hops):
            hops_by_wire.setdefault(hop.wire_id, []).append(hop)
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
            item.setZValue(wire.z_order)         # keep layering in sync with model
            item.hops = hops_by_wire.get(wire.id, [])
            # Mark which vertices are locked (endpoints sitting on a pin), so
            # the item only draws grab handles on draggable vertices.
            item.locked_indices = {
                i
                for i in range(len(wire.points))
                if not self.vertex_is_draggable(wire, i, pins)
            }

        # --- junction dots (3+ wires, or pin + 2 wires) -------------------
        # Suppressed document-wide when mark_junctions is off (§10.3).
        wanted = (junction_points(self._schematic)
                  if self._schematic.mark_junctions else set())
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
        # Suppressed document-wide when mark_open_ends is off (§10.3).
        wanted_oc = (open_endpoints(self._schematic)
                     if self._schematic.mark_open_ends else set())
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
        wanted_pc = unconnected_pins(self._schematic) if self._schematic.mark_unconnected_pins else set()
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
        # Capture a single selected wire's style as the template for new wires.
        wids = self.selected_wire_ids()
        if len(wids) == 1 and not self.selected_component_ids():
            w = self._wire_by_id(wids[0])
            if w is not None:
                self._new_wire_style = {
                    "line_style": w.line_style,
                    "line_width": w.line_width,
                    "start_marker": w.start_marker,
                    "end_marker": w.end_marker,
                }
        self.selection_changed_gu.emit(self.selected_component_ids())

    # ------------------------------------------------------------------
    # Placement ghost
    # ------------------------------------------------------------------

    def _spawn_ghost(self, kind: str) -> None:
        self._cancel_ghost()
        from app.components import library
        cls = ITEM_CLASSES.get(kind, ComponentItem)
        # The ghost previews the component at the scale it will be placed at, so a
        # logic gate shows compact (its default scale) before the click.
        ghost_comp = REGISTRY[kind].component_class(
            id="__ghost__", kind=kind, position=(0.0, 0.0),
            rotation=self._place_rotation, mirror=self._place_mirror, options="",
            scale=library.default_scale(kind),
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

    def _marker_pin_offset(self, kind: str, rotation: int,
                           mirror: bool) -> tuple[float, float]:
        """A single-point marker's pin offset in **world** frame (rotation+mirror
        applied), backed out when magneting so the marker's *pin* (not its centre)
        lands on the target. Zero for a dot whose pin is at its centre — the junction
        dot and the inversion dot (``ocirc``/``notcirc``), which therefore sit centred
        on the anchor on the canvas (the export still draws the bubble tangent via the
        ``[ocirc, left]`` idiom — the canvas is a preview, not the exact rendering)."""
        defn = REGISTRY.get(kind)
        if defn is None or not defn.pins:
            return (0.0, 0.0)
        from app.canvas.geometry import local_span_to_world
        return local_span_to_world(tuple(defn.pins[0].offset), rotation, mirror)

    def _marker_drag_snap(self, comp: Component,
                          item_scene_pos: QPointF) -> tuple[float, float]:
        """New ``position`` for a dragged single-point marker whose item sits at
        *item_scene_pos*: snap its pin to the nearest point in the union of the grid and
        the connection points (off-grid pins OK). Used by both the live drag move and
        the commit, so the marker snaps onto off-grid pins *during* the drag (like
        placement), not just on release.

        The marker's **own** pin is excluded from the magnet: the schematic still holds
        it at its pre-drag position throughout the gesture, so without this it would
        magnet back onto where it started (pinning small moves and making it jump
        between its origin and an adjacent pin)."""
        off = self._marker_pin_offset(comp.kind, comp.rotation, comp.mirror)
        cx, cy = self.scene_to_gu(item_scene_pos)
        pin_raw = (cx + off[0], cy + off[1])
        pin_gu = (self.snap_gu(pin_raw[0]), self.snap_gu(pin_raw[1]))
        return self._marker_drop_position(comp.kind, comp.rotation, comp.mirror,
                                          pin_gu, pin_raw,
                                          exclude_component_id=comp.id)

    def _marker_drop_position(self, kind: str, rotation: int, mirror: bool,
                              pin_gu: tuple[float, float],
                              pin_raw: tuple[float, float],
                              exclude_component_id: str | None = None,
                              ) -> tuple[float, float]:
        """Component ``position`` (node centre) that lands a single-point marker's
        **pin** on the nearest point in the **union of the 0.25 GU grid and the
        connection points** (component pins + wire vertices/segments, off-grid OK).
        *pin_gu* is the grid-snapped intended pin location, *pin_raw* the unsnapped one
        (so the magnet measures from where the cursor actually is). Backs the centre out
        by the marker's pin offset.

        The marker still snaps — both to grid nodes and to pins — but to whichever is
        **nearer** the cursor, so it can land exactly on an **off-grid** pin (a scaled
        gate / manual-library terminal) without the dense grid pull making that
        impossible, while still snapping to the grid everywhere else. A pure grid snap
        (off-grid pins unreachable) and a pure free-float (no grid help) were both
        wrong; the union is the behaviour we want."""
        exclude = (exclude_component_id,) if exclude_component_id else ()
        target, connectable = self.wire_snap_target(
            pin_gu, raw_gu=pin_raw, exclude_component_ids=exclude)
        if connectable:
            # Pin/wire candidate found (within the magnet radius). Snap to it unless the
            # nearest grid node is strictly closer to the cursor — union, nearest wins
            # (ties go to the pin, so an inversion bubble lands cleanly on its anchor).
            d_pin = (target[0] - pin_raw[0]) ** 2 + (target[1] - pin_raw[1]) ** 2
            d_grid = (pin_gu[0] - pin_raw[0]) ** 2 + (pin_gu[1] - pin_raw[1]) ** 2
            if d_grid < d_pin:
                target = pin_gu
        else:
            target = pin_gu          # no connection point in range → nearest grid node
        off = self._marker_pin_offset(kind, rotation, mirror)
        return (target[0] - off[0], target[1] - off[1])

    def _place_target(self, scene_pos) -> tuple[float, float]:  # noqa: ANN001
        """Where the single-kind ghost would drop. A **terminal marker** (a junction
        dot / inversion dot — a single-point node meant to sit *on* a connection point)
        snaps its **pin** to the nearest point in the union of the 0.25 GU grid and the
        connection points (component pins + wires, off-grid OK), so it lands on a nearby
        pin when the cursor is closer to one and on the grid otherwise; every other kind
        snaps to the grid only (its origin is not a connection point, so a pin-magnet
        would be wrong)."""
        gu = self.snap_point_gu(scene_pos)
        if self._place_kind and is_terminal_marker(self._place_kind):
            return self._marker_drop_position(
                self._place_kind, self._place_rotation, self._place_mirror,
                gu, self.scene_to_gu(scene_pos))
        return gu

    def _span_snap_target(self, scene_pos) -> tuple[float, float]:  # noqa: ANN001
        """Magnet-snap a span-placement endpoint onto a nearby component pin or
        wire (the same magnet wire drawing uses), else the 0.25 GU grid node — so
        a voltage/current annotation can be drawn exactly across a component's
        pins."""
        gu = self.snap_point_gu(scene_pos)
        target, _connectable = self.wire_snap_target(
            gu, raw_gu=self.scene_to_gu(scene_pos)
        )
        return target

    def _place_span_click(self, gu: tuple[float, float]) -> None:
        """Handle a left-click while placing a span annotation (open/short).

        The first click anchors the origin and spawns the ghost; the second click
        commits the annotation with span = end − start and re-arms for the next
        one (PLACE mode stays active). A zero-length second click is ignored so a
        double-click never drops a degenerate annotation."""
        if self._place_start_gu is None:
            self._place_start_gu = gu
            self._begin_span_ghost(gu)
            return
        start = self._place_start_gu
        span = (gu[0] - start[0], gu[1] - start[1])
        if span == (0.0, 0.0):
            return                          # need a real second point
        self.place_component(self._place_kind, start, span_override=span)
        # Re-arm for another span placement (rapid repeated placement, §6.2).
        self._place_start_gu = None
        self._cancel_ghost()

    def _begin_span_ghost(self, start: tuple[float, float]) -> None:
        """Spawn the span-placement ghost anchored at *start* with a zero span
        (it grows to follow the cursor on the next move)."""
        self._spawn_ghost(self._place_kind)
        self._update_span_ghost(start, start)

    def _update_span_ghost(
        self, start: tuple[float, float], end: tuple[float, float]
    ) -> None:
        """Position the span ghost at *start* and stretch it to *end*."""
        if self._ghost is None:
            return
        self._ghost.setPos(self.gu_to_scene(*start))
        if hasattr(self._ghost, "set_preview_span"):
            self._ghost.set_preview_span((end[0] - start[0], end[1] - start[1]))

    def _cancel_ghost(self) -> None:
        if self._ghost is not None:
            self._remove_item(self._ghost)
            self._ghost = None

    def _cancel_placement(self) -> None:
        self._cancel_ghost()
        self._cancel_paste_ghosts()
        self._paste_anchor_gu = None
        self._place_kind = None
        self._place_rotation = 0
        self._place_mirror = False
        self._place_start_gu = None

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
        raw_gu: tuple[float, float] | None = None,
        exclude_component_ids: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> tuple[tuple[float, float], bool]:
        return self._wire_geom.wire_snap_target(
            gu, exclude_wire_id, raw_gu, exclude_component_ids)

    def _wire_snap_point(self, scene_pt: QPointF) -> tuple[float, float] | None:
        return self._wire_geom.wire_snap_point(scene_pt)

    def unconnected_pin_at(self, scene_pt: QPointF) -> tuple[float, float] | None:
        return self._wire_geom.unconnected_pin_at(scene_pt)

    def set_mark_unconnected_pins(self, enabled: bool) -> None:
        """Toggle the document's open-circle markers at unconnected component pins
        (§10.8) and rebuild so they appear/disappear immediately. No-op if unchanged.
        Mutates the document directly (non-undoable); the inspector edits the same field
        undoably via SetDocumentPropertiesCommand and rebuilds through the command path.
        """
        enabled = bool(enabled)
        if enabled == self._schematic.mark_unconnected_pins:
            return
        self._schematic.mark_unconnected_pins = enabled
        self._rebuild_items()

    def set_line_hops(self, enabled: bool) -> None:
        """Toggle the document's line-hop bumps where wires cross without connecting
        (§6.4) and rebuild. No-op if unchanged. Mutates the document directly; the
        inspector edits the same field undoably (see set_mark_unconnected_pins)."""
        enabled = bool(enabled)
        if enabled == self._schematic.line_hops:
            return
        self._schematic.line_hops = enabled
        self._rebuild_items()

    def set_mark_open_ends(self, enabled: bool) -> None:
        """Toggle the document's open-circle (``ocirc``) markers at dangling wire ends
        (§6.4) and rebuild. No-op if unchanged. Mutates the document directly; the
        inspector edits the same field undoably (see set_mark_unconnected_pins)."""
        enabled = bool(enabled)
        if enabled == self._schematic.mark_open_ends:
            return
        self._schematic.mark_open_ends = enabled
        self._rebuild_items()

    def set_mark_junctions(self, enabled: bool) -> None:
        """Toggle the document's solid junction dots (``circ``) where wires/pins are
        tied (§6.4) and rebuild. No-op if unchanged. Mutates the document directly; the
        inspector edits the same field undoably (see set_mark_unconnected_pins)."""
        enabled = bool(enabled)
        if enabled == self._schematic.mark_junctions:
            return
        self._schematic.mark_junctions = enabled
        self._rebuild_items()

    def _refresh_preview_hops(self, extra_wires: tuple = ()) -> dict:
        """Recompute line-hops against the *live* (in-gesture) geometry (§6.4).

        Used during a wire drag or while drawing a new wire so the bumps track
        the cursor instead of waiting for the commit/rebuild. Each wire's
        effective points are its live drag-preview points where present (read off
        the wire item), else its committed points; *extra_wires* adds transient
        wires that have no model entry yet (the in-progress draw). Every wire
        item's ``.hops`` is reassigned (and repainted if changed); the full
        ``{wire_id: [WireHop]}`` grouping is returned so a caller can route a
        synthetic wire's hops to the draw-preview item.

        Default-mode wires hop only when the global preference is on; per-wire
        ``hop_mode`` overrides apply regardless, so this always recomputes.
        """
        eff: list[Wire] = []
        for w in self._schematic.wires:
            item = self._wire_items.get(w.id)
            pts = item.preview_points if item is not None else None
            eff.append(dataclasses.replace(w, points=list(pts)) if pts else w)
        eff.extend(extra_wires)
        snapshot = Schematic(
            version=self._schematic.version,
            name=self._schematic.name,
            components=self._schematic.components,
            wires=eff,
        )
        grouped: dict[str, list] = {}
        for hop in wire_crossings(snapshot, default_on=self._schematic.line_hops):
            grouped.setdefault(hop.wire_id, []).append(hop)
        for w in self._schematic.wires:
            item = self._wire_items.get(w.id)
            if item is None:
                continue
            new = grouped.get(w.id, [])
            if item.hops != new:
                item.hops = new
                item.update()
        return grouped

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

    def _all_offgrid_pin_axes(self) -> tuple[set[float], set[float]]:
        """Every off-grid pin coordinate as an *axis snap line* (an "artificial grid
        line" extending from a pin's x and y).

        Scans **all** component connection points and returns ``(xs, ys)`` — the set
        of off-grid x-values and off-grid y-values. A wire end may snap onto any of
        these so it can sit off the 0.25-GU grid while staying *collinear with a
        pin*; validation permits exactly such a coordinate (it must line up with some
        pin's off-grid x or y — see :func:`app.schematic.validate.validate`). On-grid
        pins add nothing (the grid already covers them), so only off-grid pins
        contribute lines."""
        xs: set[float] = set()
        ys: set[float] = set()
        for px, py in self._all_pin_positions():
            if not self._coord_on_grid(px):
                xs.add(round(px, 6))
            if not self._coord_on_grid(py):
                ys.add(round(py, 6))
        return xs, ys

    def _snap_coord(self, v: float, offgrid: set[float]) -> float:
        """Snap *v* to the nearest of the 0.25-GU grid or an *offgrid* snap line
        (an off-grid pin coordinate); ties prefer the grid."""
        best, best_d = self.snap_gu(v), abs(v - self.snap_gu(v))
        for c in offgrid:
            d = abs(v - c)
            if d < best_d - 1e-9:
                best, best_d = c, d
        return best

    def _axis_snap(
        self, raw_gu: tuple[float, float], axes: tuple[set[float], set[float]]
    ) -> tuple[tuple[float, float], tuple[float | None, float | None]]:
        """Snap each axis of *raw_gu* to the nearer of the grid or a pin axis line.

        Returns ``(point, guides)`` where *guides* is ``(gx, gy)`` — the pin axis
        line each coordinate actually landed on (off-grid), or ``None`` where it
        snapped to a plain grid node. The guides drive the on-screen alignment
        guide lines (:meth:`_show_guides`)."""
        xs, ys = axes
        sx = self._snap_coord(raw_gu[0], xs)
        sy = self._snap_coord(raw_gu[1], ys)
        gx = sx if round(sx, 6) in xs else None
        gy = sy if round(sy, 6) in ys else None
        return (sx, sy), (gx, gy)

    def _resolve_wire_end(
        self,
        raw_gu: tuple[float, float],
        exclude_wire_id: str | None = None,
        exclude_component_ids: "frozenset[str] | set[str] | tuple[str, ...]" = (),
    ) -> tuple[tuple[float, float], bool, tuple[float | None, float | None]]:
        """Resolve where a wire end should land, from the **raw** (unsnapped) cursor.

        A nearby pin / wire vertex / wire segment may win (the magnet, so the end
        connects); otherwise each axis snaps to the nearer of the 0.25-GU grid or a
        pin axis line (:meth:`_all_offgrid_pin_axes`), so a wire end can sit off-grid
        while staying *collinear with a pin*. Returns ``(point, is_connectable,
        guides)``; *guides* is non-empty only when an off-grid pin line was used (the
        magnet path reports no guides — it landed on a real connection point).

        When the magnet *and* the per-axis snap both have a candidate, the one the
        raw cursor is actually **closer** to wins (ties → the magnet). This keeps a
        pin grabbable without letting it 'capture' a position the cursor is nearer to
        — notably the on-grid line *between* two off-grid pins, which sits one
        ``PIN_SNAP_GU`` from each pin and would otherwise always snap to a pin. Used
        by both the live preview and the commit, so they agree."""
        axis_pt, guides = self._axis_snap(raw_gu, self._all_offgrid_pin_axes())
        gu = (self.snap_gu(raw_gu[0]), self.snap_gu(raw_gu[1]))
        target, connectable = self.wire_snap_target(
            gu, exclude_wire_id=exclude_wire_id, raw_gu=raw_gu,
            exclude_component_ids=exclude_component_ids)
        if connectable:
            d_target = (raw_gu[0] - target[0]) ** 2 + (raw_gu[1] - target[1]) ** 2
            d_axis = (raw_gu[0] - axis_pt[0]) ** 2 + (raw_gu[1] - axis_pt[1]) ** 2
            if d_target <= d_axis:
                return target, True, (None, None)
        return axis_pt, False, guides

    def _snap_vertex_target(
        self,
        pt: tuple[float, float],
        wire_ids: "set[str] | frozenset[str] | None" = None,
    ) -> tuple[float, float]:
        """Snap a dragged-vertex target to the 0.25-GU grid, with two exceptions
        for off-grid component pins (scaled gate / manual-library terminals):

        * a target resting **exactly on a pin** is kept verbatim (connecting onto
          it — the magnet resolved it there);
        * otherwise each axis may snap to any **off-grid pin axis line**
          (:meth:`_all_offgrid_pin_axes`) as well as to the grid, so a vertex
          collinear with a pin slides along that pin's axis and the segment into it
          stays straight, instead of jogging onto the grid.

        *wire_ids* is accepted for backward compatibility but no longer narrows the
        axis set — alignment is global (every pin's axis line is a snap target)."""
        key = point_key(pt)
        for pin in self._all_pin_positions():
            if point_key(pin) == key:
                return pt
        xs, ys = self._all_offgrid_pin_axes()
        return (self._snap_coord(pt[0], xs), self._snap_coord(pt[1], ys))

    def _vertex_drag_target(
        self,
        wire_ids: "set[str] | frozenset[str]",
        raw_gu: tuple[float, float],
        exclude_wire_id: str | None = None,
    ) -> tuple[float, float]:
        """Where a dragged wire vertex should land (point only).

        Thin wrapper over :meth:`_resolve_wire_end` (which carries the shared
        magnet-vs-axis logic and the guide info); *wire_ids* is accepted for
        backward compatibility but no longer narrows the axis set."""
        pt, _connectable, _guides = self._resolve_wire_end(
            raw_gu, exclude_wire_id=exclude_wire_id)
        return pt

    # -- pin-alignment guide overlay -------------------------------------

    def _show_guides(self, gx: float | None, gy: float | None) -> None:
        """Show (or hide) the faint pin-alignment guide line(s). Both ``None``
        removes the overlay; otherwise a vertical line at *gx* and/or a horizontal
        line at *gy* marks the pin axis a wire end has snapped onto."""
        if gx is None and gy is None:
            self._clear_guides()
            return
        if self._guide_item is None:
            self._guide_item = AlignmentGuideItem()
            self.addItem(self._guide_item)
        self._guide_item.set_guides(gx, gy)

    def _clear_guides(self) -> None:
        if self._guide_item is not None:
            self._remove_item(self._guide_item)
            self._guide_item = None

    def move_wire_vertex(
        self, wire_id: str, index: int, new_point: tuple[float, float]
    ) -> None:
        """Move a wire vertex via an undoable MoveWireVertexCommand.

        If the vertex is dropped in the *middle* of another wire's segment, that
        wire is split (a vertex inserted) so a junction is formed — bundled with
        the move as one undoable MacroCommand.
        """
        snapped = self._snap_vertex_target(new_point, {wire_id})
        # The dragged wire must not split itself.
        split_cmds = self._split_commands_for({snapped}, exclude_wire_id=wire_id)
        move_cmd = MoveWireVertexCommand(wire_id, index, snapped)
        if split_cmds:
            self._push(MacroCommand(split_cmds + [move_cmd], label="Move node"))
        else:
            self._push(move_cmd)

    def _coincident_vertices(
        self, coord: tuple[float, float]
    ) -> list[tuple[str, int]]:
        """All ``(wire_id, index)`` whose vertex equals *coord* — i.e. the wires
        meeting at that junction. A lone point returns a single-element list."""
        key = point_key(coord)
        out: list[tuple[str, int]] = []
        for w in self._schematic.wires:
            for i, p in enumerate(w.points):
                if point_key(p) == key:
                    out.append((w.id, i))
        return out

    def move_junction(
        self, targets: list[tuple[str, int]], new_point: tuple[float, float]
    ) -> None:
        """Move a junction — every wire vertex in *targets* — to *new_point* as
        one undoable action, so all connected wires drag together. Any non-group
        wire whose segment the junction lands on is split to keep the connection."""
        group_ids = frozenset(wid for wid, _ in targets)
        snapped = self._snap_vertex_target(new_point, group_ids)
        split_cmds = self._split_commands_for({snapped}, exclude_wire_ids=group_ids)
        move_cmd = MoveJunctionCommand(targets, snapped)
        if split_cmds:
            self._push(MacroCommand(split_cmds + [move_cmd], label="Move junction"))
        else:
            self._push(move_cmd)

    def set_wire_routing(self, style: str) -> None:
        """Select the wire routing style for newly drawn wires (spec §6.4):
        ``"manhattan"`` (axis-only elbow) or ``"laplata"`` (a 45° leg). Editor
        state only — it shapes the router, not stored geometry, so it is not
        persisted. Unknown values are ignored."""
        from app.schematic.model import ROUTING_STYLES
        if style in ROUTING_STYLES:
            self._wire_routing = style

    @property
    def wire_routing(self) -> str:
        return self._wire_routing

    def _route(
        self,
        a: tuple[float, float],
        b: tuple[float, float],
        vfirst: bool | None = None,
    ) -> list[tuple[float, float]]:
        """Route from a to b in the active wire style (spec §6.4).

        In **Manhattan** style the elbow is axis-only; in **La Plata** style it adds
        a 45° leg (:func:`route_diagonal`). *vfirst* is the Manhattan corner-orientation
        preference (``None`` = dominant-axis default); while drawing, the scene passes
        the cursor's heading so the elbow traces the path the cursor took
        (:meth:`_wire_vfirst`). The preview and the committed wire both call this, so
        they agree.

        When an endpoint is an **off-grid component pin** (a scaled logic gate's
        terminal), the leg adjacent to it keeps the pin's off-grid coordinate — so the
        wire extends from the pin along its own lead line and only *then* elbows onto
        the grid (the corner inherits the pin's off-grid coordinate, which validation
        permits, §3.1); a La Plata wire falls back to this Manhattan lead routing into
        an off-grid pin. Delegates to the shared :func:`route_pin_aware` so the canvas
        router and component-follow re-routing agree.
        """
        return route_pin_aware(a, b, vfirst, style=self._wire_routing)

    #: How far (GU) the cursor must travel from the leg's start before its
    #: out-direction is locked, so a tiny initial jitter doesn't pick the axis.
    _WIRE_HEADING_LOCK_GU = 0.5

    def _wire_vfirst(self) -> bool | None:
        """The corner orientation for the in-progress wire's locked heading: the
        **first leg** follows the axis the cursor first went out along — horizontal
        (``vfirst=False``) for an ``"h"`` heading, vertical (``vfirst=True``) for
        ``"v"`` — so the elbow traces the path the cursor took instead of flipping
        to the dominant axis when the perpendicular leg grows longer (§6.4).
        ``None`` (no lock yet) falls back to the dominant-axis default."""
        if self._wire_heading == "h":
            return False
        if self._wire_heading == "v":
            return True
        return None

    def _update_wire_heading(self, gu: tuple[float, float]) -> None:
        """Lock the leg's out-direction once the cursor has clearly departed the
        last committed vertex along one axis. Once locked it stays for the rest of
        the leg (reset at each committed vertex), giving the router its memory."""
        if self._wire_heading is not None or not self._wire_pts:
            return                                        # already locked this leg
        ax, ay = self._wire_pts[-1]
        dx, dy = abs(gu[0] - ax), abs(gu[1] - ay)
        if max(dx, dy) < self._WIRE_HEADING_LOCK_GU:
            return                                        # not far enough yet
        if dx > dy + 1e-9:
            self._wire_heading = "h"
        elif dy > dx + 1e-9:
            self._wire_heading = "v"
        # exact diagonal departure: leave unlocked (dominant-axis default)

    def _reset_wire_heading(self, anchor: tuple[float, float] | None = None) -> None:
        """Clear the heading lock at the start of a wire or a new leg."""
        self._wire_heading = None

    @staticmethod
    def _coord_on_grid(v: float) -> bool:
        """True when a single coordinate lies on the 0.25-GU grid (delegates to
        the shared Qt-free predicate in app.schematic.model)."""
        return _model_coord_on_grid(v)

    @classmethod
    def _on_grid(cls, pt: tuple[float, float]) -> bool:
        """True when both coordinates lie on the 0.25-GU grid."""
        return cls._coord_on_grid(pt[0]) and cls._coord_on_grid(pt[1])

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
            preview.hops = self._draw_preview_hops(list(self._wire_pts))
            preview.set_path(self._wire_pts, None)
            return
        # Pending leg from the last committed vertex to the cursor, oriented by
        # the cursor's heading so the elbow follows the path it traced.
        legs = self._route(self._wire_pts[-1], cursor_gu, self._wire_vfirst())
        full = list(self._wire_pts) + legs[1:]
        # Line-hops where the in-progress wire crosses existing wires (set before
        # set_path so the geometry change covers the bumps).
        preview.hops = self._draw_preview_hops(full)
        # Show committed vertices as anchors; the cursor end carries the marker.
        preview.set_path(full[:-1], full[-1], cursor_is_pin)

    def _draw_preview_hops(self, poly: list[tuple[float, float]]) -> list:
        """Line-hops for the in-progress wire ``poly`` crossing existing wires.

        Recomputes every wire item's hops against this transient wire (so an
        existing wire the new line crosses also shows its bump where it should),
        and returns the hops that belong to the in-progress wire itself for the
        draw-preview item to paint.
        """
        if len(poly) < 2:
            self._refresh_preview_hops()      # reset committed hops, no synthetic wire
            return []
        synth = Wire(id="__wire_preview__", points=list(poly))
        grouped = self._refresh_preview_hops(extra_wires=(synth,))
        return grouped.get("__wire_preview__", [])

    def _cancel_wire_preview(self) -> None:
        if self._wire_preview is not None:
            self._remove_item(self._wire_preview)
            self._wire_preview = None

    def _cancel_wire(self) -> None:
        self._wire_pts = []
        self._reset_wire_heading()
        self._cancel_wire_preview()
        self._clear_guides()
        # Clear any bumps other wires were showing for the (now-gone) preview wire.
        self._refresh_preview_hops()

    # ------------------------------------------------------------------
    # Mouse events
    # ------------------------------------------------------------------

    def mouseMoveEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        gu = self.snap_point_gu(event.scenePos())
        self._last_cursor_gu = gu
        self.cursor_moved.emit(gu[0], gu[1])

        if self._mode == Mode.PLACE:
            if self._paste_anchor_gu is not None:
                # Interactive paste: the whole clipboard group tracks the cursor.
                self._move_paste_ghosts(gu)
            elif self._place_kind in _SPAN_PLACE_KINDS:
                # The ghost only exists (and stretches) once the origin is set.
                if self._place_start_gu is not None:
                    self._update_span_ghost(
                        self._place_start_gu, self._span_snap_target(event.scenePos())
                    )
            else:
                self._move_ghost(self._place_target(event.scenePos()))
            event.accept()
            return

        if self._mode == Mode.WIRE:
            target, is_connectable, guides = self._resolve_wire_end(
                self.scene_to_gu(event.scenePos()))
            self._show_guides(*guides)
            if self._wire_pts:
                self._update_wire_heading(gu)
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
            # Resolve the same target the commit will use, so the ghost matches:
            # the magnet snaps onto a pin (incl. off-grid), else the vertex slides
            # along the grid or any pin's off-grid axis line (with a guide line).
            wire_id = self._drag.vertex_drag[0]
            target, _connectable, guides = self._resolve_wire_end(
                self.scene_to_gu(event.scenePos()), exclude_wire_id=wire_id)
            self._show_guides(*guides)
            self._drag.preview_vertex_drag(target)
            self._refresh_preview_hops()      # bumps track the reshaped wire(s)
            event.accept()
            return

        if self._drag.endpoint_drag is not None:
            self._drag.preview_endpoint_drag(gu)
            self._refresh_preview_hops()
            event.accept()
            return

        if self._drag.resize_drag is not None:
            self._drag.preview_resize(gu)
            self._refresh_preview_hops()
            event.accept()
            return

        if self._mode == Mode.SELECT and self._drag.wire_drag_ids:
            start = self._drag.wire_drag_start_gu
            if start is not None:
                self._drag.preview_wire_drag(gu[0] - start[0], gu[1] - start[1])
                self._refresh_preview_hops()
            event.accept()
            return

        # Let Qt move any dragged component items, then snap them and ghost their
        # connected wires. A lone terminal marker magnet-snaps onto a nearby pin
        # (off-grid OK, like placement); everything else snaps to the 0.25 GU grid.
        super().mouseMoveEvent(event)
        if self._mode == Mode.SELECT and self._drag.drag_start:
            solo = len(self._drag.drag_start) == 1
            for cid in self._drag.drag_start:
                item = self._comp_items.get(cid)
                if item is None:
                    continue
                comp = self._component_by_id(cid)
                if solo and comp is not None and is_terminal_marker(comp):
                    new = self._marker_drag_snap(comp, item.pos())
                else:
                    new = self.snap_point_gu(item.pos())
                item.setPos(self.gu_to_scene(*new))
            self._drag.preview_component_drag()
            self._refresh_preview_hops()


    def _selectable_item_at(self, scene_pos: QPointF):
        """The top-most **directly-selectable** component/wire whose own shape is
        under *scene_pos*, or ``None`` (used for modifier-click multi-selection).

        This mirrors Qt's plain-click selection: it returns the item the user
        would get from an unmodified click. Crucially it does **not** climb from a
        non-selectable child (a slot label / annotation decoration) to its parent
        component — those children float over *other* components (e.g. an `open`
        voltage annotation's arrow and label bow across the elements it measures),
        so climbing would select the annotation when the click is really on the
        element beneath it. Skipping the non-selectable children and taking the
        first selectable item whose own shape is hit matches the unmodified
        click."""
        for it in self.items(scene_pos):
            if (isinstance(it, (ComponentItem, WireItem))
                    and it.flags() & QGraphicsItem.ItemIsSelectable):
                return it
        return None

    def _pin_belongs_to_terminal_marker(self, pin: tuple[float, float]) -> bool:
        """True when *pin* is a pin of a single-point **terminal marker** (a Terminals
        dot/pole or a marker kind like the inversion dot — ``is_terminal_marker``,
        whose symbol coincides with its pin).
        Such a marker would otherwise be un-grabbable: a press on it always lands on
        its pin, so the wire-auto-start would fire and it could never be selected,
        moved or deleted. Checked by coordinate (not exact shape hit) so the small
        dot is reliably grabbable within the pin snap radius."""
        from app.schematic.model import component_pin_positions, point_key

        key = point_key(pin)
        for comp in self._schematic.components:
            if not is_terminal_marker(comp):
                continue
            if any(point_key(p) == key for p in component_pin_positions(comp)):
                return True
        return False

    # ------------------------------------------------------------------
    # Right-click context menu (components & wires)
    # ------------------------------------------------------------------

    def contextMenuEvent(self, event) -> None:  # noqa: N802, ANN001
        """Show a per-item right-click menu (front/back layering) in SELECT mode.

        Right-clicking an item that is not already part of the selection makes it
        the sole selection first (standard desktop behaviour); right-clicking
        inside an existing multi-selection keeps it, so the action applies to the
        whole group. Empty space (or any non-SELECT mode) falls through.
        """
        if self._mode != Mode.SELECT:
            super().contextMenuEvent(event)
            return
        item = self._selectable_item_at(event.scenePos())
        if item is not None:
            clicked_id = (
                item.component.id if isinstance(item, ComponentItem) else item.wire.id
            )
            selected = (
                set(self.selected_component_ids()) | set(self.selected_wire_ids())
            )
            if clicked_id not in selected:
                self.clearSelection()
                item.setSelected(True)
        # Empty space keeps the current selection (so Copy/Cut still target it) and
        # still offers Paste — so the menu always shows in SELECT mode.
        self._show_item_context_menu(event)

    def _show_item_context_menu(self, event) -> None:  # noqa: ANN001
        """Build and exec the component/wire context menu at the cursor.

        Edit actions operate on the current selection; Paste drops the clipboard
        at the right-click point. Actions are enabled per state (a selection for
        cut/copy/delete/layer, a non-empty clipboard for paste)."""
        from PySide6.QtWidgets import QMenu
        target_ids = self.selected_component_ids() + self.selected_wire_ids()
        has_selection = bool(target_ids)
        has_clipboard = bool(self._clipboard_components or self._clipboard_wires)
        paste_at = self.snap_point_gu(event.scenePos())

        menu = QMenu(event.widget())
        act_cut = menu.addAction("Cut")
        act_copy = menu.addAction("Copy")
        act_paste = menu.addAction("Paste")
        menu.addSeparator()
        act_delete = menu.addAction("Delete")
        menu.addSeparator()
        act_front = menu.addAction("Bring to Front")
        act_back = menu.addAction("Send to Back")

        for act in (act_cut, act_copy, act_delete, act_front, act_back):
            act.setEnabled(has_selection)
        act_paste.setEnabled(has_clipboard)

        act_cut.triggered.connect(self.cut_selection)
        act_copy.triggered.connect(self.copy_selection)
        act_paste.triggered.connect(
            lambda _checked: self.paste(at=paste_at)
        )
        act_delete.triggered.connect(self.delete_selected)
        act_front.triggered.connect(
            lambda: self._layer_selection(target_ids, to_front=True)
        )
        act_back.triggered.connect(
            lambda: self._layer_selection(target_ids, to_front=False)
        )
        menu.exec(event.screenPos())
        event.accept()

    def _layer_selection(self, ids: list[str], *, to_front: bool) -> None:
        """Bring every id (components and/or wires) to the front, or send to back,
        as one undoable step. Items are processed in current-z order so their
        relative stacking is preserved within the moved group."""
        if not ids:
            return
        zmap = dict(self._z_ordered_objects())
        order = sorted(ids, key=lambda i: zmap.get(i, 0))
        if not to_front:
            order = list(reversed(order))   # highest first → stays on top of the back group
        with self.batch("Bring to Front" if to_front else "Send to Back"):
            for obj_id in order:
                if to_front:
                    self.bring_to_front(obj_id)
                else:
                    self.send_to_back(obj_id)

    def mousePressEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._panning:
            super().mousePressEvent(event)
            return

        gu = self.snap_point_gu(event.scenePos())

        # SELECT mode: the right button only raises the context menu (delivered
        # separately as contextMenuEvent). Swallow the press so the default
        # handler can't alter the selection out from under the menu.
        if self._mode == Mode.SELECT and event.button() == Qt.RightButton:
            event.accept()
            return

        if self._mode == Mode.PLACE:
            # Interactive paste (begin_paste): a left-click drops the group at the
            # cursor; a right-click cancels. paste() re-enters SELECT and selects
            # the pasted items, so the ghosts are torn down via _cancel_placement.
            if self._paste_anchor_gu is not None:
                if event.button() == Qt.LeftButton:
                    self.paste(at=gu)
                else:
                    self.set_mode(Mode.SELECT)
                event.accept()
                return
            if event.button() == Qt.RightButton:
                # During a span placement, the first right-click abandons the
                # in-progress span (back to awaiting the first click); otherwise
                # it leaves PLACE mode.
                if self._place_start_gu is not None:
                    self._place_start_gu = None
                    self._cancel_ghost()
                else:
                    self.set_mode(Mode.SELECT)
                event.accept()
                return
            if event.button() == Qt.LeftButton and self._place_kind:
                if self._place_kind in _SPAN_PLACE_KINDS:
                    self._place_span_click(self._span_snap_target(event.scenePos()))
                else:
                    self.place_component(
                        self._place_kind, self._place_target(event.scenePos()),
                        rotation=self._place_rotation,
                        mirror=self._place_mirror,
                    )
                # Stay in PLACE mode for rapid repeated placement (spec §6.2).
                event.accept()
                return

        if self._mode == Mode.WIRE:
            if event.button() == Qt.LeftButton:
                target, is_connectable, guides = self._resolve_wire_end(
                    self.scene_to_gu(event.scenePos()))
                self._show_guides(*guides)
                if not self._wire_pts:
                    # Begin the wire — anchor the first vertex (pin or node).
                    self._wire_pts = [target]
                    self._reset_wire_heading(target)
                    self._refresh_wire_preview(target, is_connectable)
                else:
                    # Commit the previewed L (its corner + the target). A click
                    # on empty space drops an intermediate vertex and keeps
                    # routing; a connectable target (pin / wire) finalizes.
                    legs = self._route(self._wire_pts[-1], target, self._wire_vfirst())
                    self._wire_pts.extend(legs[1:])
                    if is_connectable and len(self._wire_pts) >= 2:
                        pts = self._wire_pts
                        self._wire_pts = []
                        self.add_wire(pts)
                        self._cancel_wire_preview()
                        # Terminating on a pin or existing wire returns to SELECT.
                        self.set_mode(Mode.SELECT)
                    else:
                        # New leg from the just-dropped vertex re-derives heading.
                        self._reset_wire_heading(self._wire_pts[-1])
                        self._refresh_wire_preview(target, is_connectable)
                event.accept()
                return

        # SELECT mode: Shift / Ctrl / Cmd-click toggles an item in the selection
        # instead of replacing it, so the user can build a multi-selection. Checked
        # before the resize/drag handlers so a modified click always means "select"
        # (Qt's own additive modifier is Ctrl-only, so Shift would otherwise just
        # move the selection to the clicked item).
        if (self._mode == Mode.SELECT and event.button() == Qt.LeftButton
                and event.modifiers()
                & (Qt.ShiftModifier | Qt.ControlModifier | Qt.MetaModifier)):
            hit = self._selectable_item_at(event.scenePos())
            if hit is not None:
                hit.setSelected(not hit.isSelected())
            event.accept()
            return

        # SELECT mode: a press on a resizable component's endpoint handle starts
        # an endpoint drag — either the terminal (handle 1) or, for line
        # annotations, the origin (handle 0). Takes priority over wire-auto-enter
        # and vertex drag, so clicking-and-holding an endpoint drags it instead of
        # starting a wire from a coincident pin.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            hit = self._drag.endpoint_handle_at(event.scenePos())
            if hit is not None:
                comp_id, handle_idx = hit
                comp = self._component_by_id(comp_id)
                if comp is not None:
                    old_span = comp.span_override if comp.span_override is not None else REGISTRY[comp.kind].default_span
                    self._drag.endpoint_drag = (comp_id, handle_idx, old_span)
                    self._drag.endpoint_press_gu = self.snap_point_gu(event.scenePos())
                    # Select the item so resize handles become visible.
                    item = self._comp_items.get(comp_id)
                    if item is not None:
                        self.clearSelection()
                        item.setSelected(True)
                    event.accept()
                    return

        # SELECT mode: a press on a resizable item's corner handle starts a
        # drag-resize (muxdemux anisotropic; scalable gates/blocks uniform).
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            nid = self._drag.resize_handle_at(event.scenePos())
            if nid is not None:
                item = self._comp_items.get(nid)
                if item is not None:
                    self._drag.resize_drag = (nid, item.resize_value())
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
                    # A junction: all wire vertices coincident with the grabbed
                    # one drag together (a lone vertex is a group of one).
                    self._drag.vertex_drag_group = self._coincident_vertices(
                        wire.points[idx]
                    )
                    self._drag.vertex_press_gu = self.snap_point_gu(event.scenePos())
                    self._drag.vertex_press_raw = self.scene_to_gu(event.scenePos())
                    self.clearSelection()
                    event.accept()
                    return

        # SELECT mode: clicking an UNCONNECTED pin auto-enters WIRE mode and
        # begins a wire there. Connected pins fall through to normal selection.
        # Exception: a single-point **terminal marker** (a Terminals-category
        # connection dot — circ/ocirc/…) *is* its own pin, so a press would always
        # auto-start a wire and the dot could never be selected, moved or deleted.
        # Such a press falls through to normal selection/drag; wire from it in WIRE
        # mode instead.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            pin = self.unconnected_pin_at(event.scenePos())
            if pin is not None and not self._pin_belongs_to_terminal_marker(pin):
                self.clearSelection()
                self._mode = Mode.WIRE
                self._apply_item_flags()
                self.mode_changed.emit(Mode.WIRE)
                self._wire_pts = [pin]
                self._reset_wire_heading(pin)
                self._refresh_wire_preview(pin, True)
                event.accept()
                return

        # SELECT mode: a press on a wire's *body* (vertex / mid-label / endpoint
        # handles are handled above and take priority) starts a whole-wire drag.
        # The pressed wire is selected if it wasn't (so a press-then-drag moves it,
        # like a component); every selected wire translates together and any
        # junction tap follows (handled by MoveCommand). A modifier press is left to
        # the additive-selection handler at the top of this method.
        if (self._mode == Mode.SELECT and event.button() == Qt.LeftButton
                and not (event.modifiers()
                         & (Qt.ShiftModifier | Qt.ControlModifier | Qt.MetaModifier))):
            hit = self._selectable_item_at(event.scenePos())
            if isinstance(hit, WireItem):
                if hit.wire.id not in set(self.selected_wire_ids()):
                    self.clearSelection()
                    hit.setSelected(True)
                self._drag.wire_drag_ids = set(self.selected_wire_ids())
                self._drag.wire_drag_start_gu = self.snap_point_gu(event.scenePos())
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
        # Commit a whole-wire drag if one is active.
        if self._drag.wire_drag_ids and event.button() == Qt.LeftButton:
            start = self._drag.wire_drag_start_gu
            gu = self.snap_point_gu(event.scenePos())
            self._drag.clear_component_drag_preview()   # drop wire ghosts
            delta = (gu[0] - start[0], gu[1] - start[1]) if start is not None else (0.0, 0.0)
            self._drag.commit_wire_drag(delta)
            self._refresh_preview_hops()
            event.accept()
            return

        # Commit an endpoint drag if one is active.
        if self._drag.endpoint_drag is not None and event.button() == Qt.LeftButton:
            comp_id, handle_idx, old_span = self._drag.endpoint_drag
            press_gu = self._drag.endpoint_press_gu
            self._drag.endpoint_drag = None
            self._drag.endpoint_press_gu = None
            gu = self.snap_point_gu(event.scenePos())
            if gu != press_gu:
                self._drag.commit_endpoint_drag(comp_id, old_span, gu, handle_idx)
            else:
                # No movement: clear any wire preview points set during the drag
                # and restore the item's component — the preview swapped it for a
                # dataclasses.replace copy, which would otherwise stick (a
                # drag-and-return would leave the item desynced from the model).
                for wire_item in self._wire_items.values():
                    wire_item.clear_preview_points()
                self._drag.restore_endpoint_preview(comp_id)
            # On plain click (no movement) the item is already selected from press.
            event.accept()
            return

        # Commit a drag-resize if one is active.
        if self._drag.resize_drag is not None and event.button() == Qt.LeftButton:
            gu = self.snap_point_gu(event.scenePos())
            for wire_item in self._wire_items.values():
                wire_item.clear_preview_points()
            self._drag.commit_resize(gu)
            self._refresh_preview_hops()
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
            group = self._drag.vertex_drag_group or [(wire_id, idx)]
            press_gu = self._drag.vertex_press_gu
            press_raw = self._drag.vertex_press_raw
            self._drag.vertex_drag = None
            self._drag.vertex_drag_group = []
            self._drag.vertex_press_gu = None
            self._drag.vertex_press_raw = None
            gu = self.snap_point_gu(event.scenePos())
            raw = self.scene_to_gu(event.scenePos())
            for wid, _i in group:               # drop visual previews
                it = self._wire_items.get(wid)
                if it is not None:
                    it.clear_preview_points()
            self._drag.clear_junction_preview()  # remove the highlighted dot
            self._clear_guides()                 # drop any pin-alignment guides
            # Distinguish a click from a drag by whether the *cursor* moved — NOT
            # by comparing the snap target to the vertex's old position. A vertex
            # can be grabbed from up to VERTEX_HIT_GU away, so a stationary click
            # whose snapped cursor differs from the vertex would otherwise be
            # misread as a drag and teleport the vertex onto the cursor (e.g. onto
            # a pin, spuriously inserting a junction dot). The grid-node test misses
            # a real drag onto an *off-grid* pin (its nearest grid node can equal
            # the press node), so also treat genuine raw-cursor movement onto a
            # different magnet target as a drag.
            wire_ids = {wid for wid, _ in group}
            target = self._vertex_drag_target(wire_ids, raw, exclude_wire_id=wire_id)
            raw_moved = (
                press_raw is not None
                and (raw[0] - press_raw[0]) ** 2 + (raw[1] - press_raw[1]) ** 2 > 0.05 ** 2
            )
            if gu != press_gu or (raw_moved and target != _orig):
                # Real drag: move the vertex (or whole junction) to the snapped
                # target (a pin or another wire's vertex/segment forms a junction).
                if len(group) > 1:
                    self.move_junction(group, target)
                else:
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

        # Let Qt finish its own mouse-grab / drag bookkeeping before the commit
        # below. This ordering is the natural flow but is NO LONGER load-bearing
        # for memory safety: pushing a command mid-grab is structurally safe
        # because (a) _remove_item releases the mouse grab before removing the
        # grabbing item (or any ancestor of it), so Qt never finalises a drag
        # against a freed item, and (b) _rebuild_items coalesces re-entrant
        # reconcile requests instead of recursing. The commit itself reads the
        # items' final snapped positions, which the release delivery does not
        # change, so it does not depend on super() having run first.
        super().mouseReleaseEvent(event)

        if pending:
            self._drag.commit_component_drag()

    def mouseDoubleClickEvent(self, event: QGraphicsSceneMouseEvent) -> None:  # noqa: N802
        if self._mode == Mode.WIRE and self._wire_pts:
            target, _conn, _guides = self._resolve_wire_end(
                self.scene_to_gu(event.scenePos()))
            if target != self._wire_pts[-1]:
                legs = self._route(self._wire_pts[-1], target, self._wire_vfirst())
                self._wire_pts.extend(legs[1:])
            pts = self._wire_pts
            self._wire_pts = []
            self.add_wire(pts)
            self._cancel_wire_preview()
            # A double-click ends the wire and exits WIRE mode — whether it lands
            # on a pin/wire or in empty space (a free open endpoint) (§6.4).
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

        # In SELECT mode, a double-click on a wire endpoint opens that endpoint's
        # label editor — so a label can be started even when none is set yet
        # (there is no rendered label to click). Endpoints are draggable
        # (including connected ones), so wire_vertex_at returns them; an endpoint
        # connected to a component pin or a drawing element is a label target
        # too. Interior vertices and the segment body fall through to the
        # mid-label gesture below.
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

        # In SELECT mode, a double-click on a wire body:
        #   • plain  → split the wire at the click and start a new wire there
        #              (enter WIRE mode from the snapped point on the wire; the
        #              target wire splits when the new wire commits);
        #   • Alt    → edit the wire's mid-label ("Middle" caption) in place.
        # Runs before the component check so wires near (or overlapping with) a
        # component bounding box are not shadowed by the component's hit area.
        if self._mode == Mode.SELECT and event.button() == Qt.LeftButton:
            alt = bool(event.modifiers() & Qt.AltModifier)
            if alt:
                for it in self.items(event.scenePos()):
                    if isinstance(it, WireItem):
                        it.begin_label_edit("mid")
                        event.accept()
                        return
            else:
                start = self._wire_snap_point(event.scenePos())
                if start is not None:
                    self.clearSelection()
                    self._mode = Mode.WIRE
                    self._apply_item_flags()
                    self.mode_changed.emit(Mode.WIRE)
                    self._wire_pts = [start]
                    self._reset_wire_heading(start)
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
                # in-place editor — double-clicking a rendered label edits it. The
                # node-text label opens the *node-text* editor; every other slot
                # label opens the options editor.
                if isinstance(it, _SlotLabel):
                    parent = it.parentItem()
                    if isinstance(parent, ComponentItem):
                        if it is getattr(parent, "_node_text_item", None):
                            parent.begin_node_text_edit()
                        else:
                            parent.begin_options_edit()
                        self.component_double_clicked.emit(parent.component.id)
                        event.accept()
                        return
                if isinstance(it, ComponentItem):
                    # Start in-place editing and open the Properties Panel. A
                    # node-style component edits its **node text** on the canvas
                    # (its node[…] options are inspector-only); every other kind
                    # edits its options.
                    comp = it.component
                    if is_node_style(comp.kind):
                        it.begin_node_text_edit()
                    else:
                        it.begin_options_edit()
                    self.component_double_clicked.emit(comp.id)
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
            self._reset_wire_heading(gu)
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
        # Paint the canvas paper explicitly (theme-aware) rather than relying on
        # the view's palette, so a light/dark swap repaints reliably.
        painter.fillRect(rect, QColor(style.COLOR_BACKGROUND))
        super().drawBackground(painter, rect)

        # A dotted grid (a dot at each lattice intersection) instead of full
        # ruled lines, so the grid orients the eye without competing with the
        # schematic ink. Dots are stroked at a constant *device* size (a
        # round-cap pen of fixed width), so they stay crisp and small at every
        # zoom level rather than ballooning with the view transform.
        scale = painter.worldTransform().m11() or 1.0  # device px per scene px
        # On-screen spacing of the integer lattice; used to decide whether the
        # 0.25 GU minor dots are dense enough to drop (keeps zoomed-out views
        # readable and cheap — no point drawing a smear of overlapping dots).
        cell_px = GRID_PX * abs(scale)

        left = int(rect.left()) - (int(rect.left()) % int(GRID_PX))
        top = int(rect.top()) - (int(rect.top()) % int(GRID_PX))

        # Draw in device coordinates so the dots are a fixed pixel size.
        painter.save()
        painter.setWorldMatrixEnabled(False)
        t = painter.worldTransform()

        def _dots(step: float, color: str, dot_px: float) -> None:
            pts = []
            y = top
            while y < rect.bottom():
                x = left
                while x < rect.right():
                    pts.append(t.map(QPointF(x, y)))
                    x += step
                y += step
            pen = QPen(QColor(color))
            pen.setWidthF(dot_px)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            painter.drawPoints(QPolygonF(pts))

        # Minor dots at every 0.25 GU — small and a touch fainter than the
        # integer dots, drawn whenever the minor cells are large enough on screen
        # to read as distinct dots (skip only when very zoomed out, to avoid a
        # dense smear). The integer dots are then drawn over them, larger.
        if cell_px * 0.25 >= 5.0:
            _dots(GRID_PX * 0.25, style.COLOR_GRID_SUB, 1.6)
        # Integer-lattice dots — a touch larger/stronger so the unit cell reads.
        _dots(GRID_PX, style.COLOR_GRID, 2.4)

        painter.restore()


# _component_pin_positions is the canonical implementation in app.schematic.model.
# Imported at the top of this module as component_pin_positions.
