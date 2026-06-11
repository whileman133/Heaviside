"""
Undo/redo command stack (spec §6.6).

This module implements the Command pattern over the :class:`Schematic` model.
It is intentionally **Qt-free**: every command mutates a plain ``Schematic``
dataclass and nothing else. This keeps the command layer fully unit-testable
in a headless environment (no display server, no PySide6 import required) and
lets the canvas scene wrap an :class:`UndoStack` without leaking Qt types into
the model.

Command set and inverses (spec §6.6):

    | Command          | Inverse                                          |
    | ---------------- | ------------------------------------------------ |
    | PlaceCommand     | Remove component                                 |
    | DeleteCommand    | Restore component(s) and connected wires         |
    | MoveCommand      | Move back to original position                   |
    | WireCommand      | Remove wire                                      |
    | SplitWireCommand | Restore original wire (remove two halves)        |
    | MergeWireCommand | Split merged wire back into two originals        |
    | EditCommand            | Restore previous options string                  |
    | MoveOptionsLabelCommand| Restore previous label_offset                    |
    | MacroCommand           | Composite of the above (undone in reverse order) |

Each command exposes ``do(schematic)`` and ``undo(schematic)``. A command must
be idempotent with respect to repeated do/undo cycles: ``do`` then ``undo``
must return the schematic to a state equal to the one before ``do`` ran, and a
subsequent ``do`` must reproduce the post-``do`` state exactly. Commands store
deep copies of any mutable state they capture so that later edits to the live
model cannot corrupt the undo history.
"""

from __future__ import annotations

import copy
import uuid
from abc import ABC, abstractmethod
from typing import TypeVar

from app.components.model import DrawingComponent, FontedComponent, TextNodeComponent
from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    _point_strictly_on_segment,
    component_connection_points as _component_connection_points,
    component_pin_positions as _component_pin_positions,
    coord_on_grid,
    is_box_kind,
    point_key,
    route,
    route_pin_aware,
    simplify_points,
    snap_point,
    wire_contained_by_others,
)

__all__ = [
    "Command",
    "PlaceCommand",
    "DeleteCommand",
    "MoveCommand",
    "ResizeCommand",
    "SetFontSizeCommand",
    "SetZOrderCommand",
    "SetTextStyleCommand",
    "SetVariantCommand",
    "SetFillColorCommand",
    "SetComponentLineWidthCommand",
    "SetComponentScaleCommand",
    "SetLineStyleCommand",
    "SetWireLineStyleCommand",
    "SetWireLineWidthCommand",
    "SetWireNoJunctionDotsCommand",
    "SetWireNoTerminationDotsCommand",
    "MoveWireVertexCommand",
    "SplitWireCommand",
    "MergeWireCommand",
    "WireCommand",
    "EditCommand",
    "MoveOptionsLabelCommand",
    "RotateCommand",
    "MirrorCommand",
    "GroupRotateCommand",
    "MacroCommand",
    "UndoStack",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_component(schematic: Schematic, comp_id: str) -> Component:
    """Return the live component with *comp_id* or raise KeyError."""
    for comp in schematic.components:
        if comp.id == comp_id:
            return comp
    raise KeyError(f"no component with id {comp_id!r} in schematic")


def _find_wire(schematic: Schematic, wire_id: str) -> Wire | None:
    """Return the live wire with *wire_id*, or None if it no longer exists.

    Every caller handles None explicitly (a missing wire makes the command a
    clean no-op rather than corrupting the undo machinery mid-macro).
    """
    for wire in schematic.wires:
        if wire.id == wire_id:
            return wire
    return None


def _set_wire_attr(schematic: Schematic, wire_id: str, attr: str, value) -> None:
    """Set *attr* on the wire with *wire_id*; a missing wire is a no-op.

    Shared body of the simple per-attribute wire commands (style, markers,
    labels, …) so each handles a vanished wire identically.
    """
    wire = _find_wire(schematic, wire_id)
    if wire is not None:
        setattr(wire, attr, value)


# Wire fields that describe the *whole* wire (not a specific endpoint). When a
# wire is split into halves or two wires are merged, the new wire(s) inherit
# these so style/line-hops/z-order/dot-suppression survive the operation.
_WIRE_BODY_FIELDS = (
    "line_style", "line_width", "z_order", "hop_mode",
    "no_junction_dots", "no_termination_dots",
)


def _carry_wire_body(dst: Wire, src: Wire) -> None:
    """Copy whole-wire style/behaviour fields from *src* onto *dst* (in place)."""
    for f in _WIRE_BODY_FIELDS:
        setattr(dst, f, getattr(src, f))


def _polyline_length(points: list[tuple[float, float]]) -> float:
    """Total Manhattan path length of *points* (0 for <2 points)."""
    total = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        total += abs(x1 - x0) + abs(y1 - y0)
    return total


def _point_on_polyline(pt: tuple[float, float], points: list[tuple[float, float]]) -> bool:
    """True if *pt* is a vertex of, or lies on any (axis-aligned) segment of, the
    polyline *points* (inclusive of vertices). Used to detect a wire endpoint that
    sits on another wire — a junction that must follow when that wire is moved.
    All coordinate comparisons go through :func:`point_key` (float-noise guard)."""
    px, py = point_key(pt)
    if any(point_key(p) == (px, py) for p in points):
        return True
    for a, b in zip(points, points[1:]):
        (ax, ay), (bx, by) = point_key(a), point_key(b)
        if ax == bx and px == ax and min(ay, by) <= py <= max(ay, by):
            return True
        if ay == by and py == ay and min(ax, bx) <= px <= max(ax, bx):
            return True
    return False


def _split_wire_into_halves(orig: Wire, half1_pts, half2_pts, id1, id2):
    """Build the two halves of a split *orig* wire, distributing its decorations.

    ``half1`` keeps the original first point (so it inherits the start marker/
    label/placement); ``half2`` keeps the original last point (end marker/label/
    placement). Both inherit the whole-wire body fields. A mid-label is assigned
    to whichever half contains its fractional arc-length position (rescaled to the
    half), defaulting to the longer half when it cannot be localised.
    """
    half1 = Wire(id=id1, points=list(half1_pts))
    half2 = Wire(id=id2, points=list(half2_pts))
    _carry_wire_body(half1, orig)
    _carry_wire_body(half2, orig)
    half1.start_marker = orig.start_marker
    half1.start_label = orig.start_label
    half1.start_label_placement = orig.start_label_placement
    half2.end_marker = orig.end_marker
    half2.end_label = orig.end_label
    half2.end_label_placement = orig.end_label_placement
    if orig.mid_label:
        total = _polyline_length(orig.points)
        frac = (_polyline_length(half1.points) / total) if total > 0 else 0.5
        if 0.0 < frac < 1.0 and orig.mid_label_pos <= frac:
            half1.mid_label = orig.mid_label
            half1.mid_label_pos = orig.mid_label_pos / frac
        elif 0.0 < frac < 1.0:
            half2.mid_label = orig.mid_label
            half2.mid_label_pos = (orig.mid_label_pos - frac) / (1.0 - frac)
        else:
            target = half1 if frac >= 0.5 else half2
            target.mid_label = orig.mid_label
            target.mid_label_pos = 0.5
    return half1, half2


def _merge_wire_decorations(merged: Wire, w1: Wire, rev1: bool, w2: Wire, rev2: bool) -> None:
    """Carry style + endpoint decorations onto a *merged* wire (in place).

    The merge orients w1 so the join point is its last vertex (``rev1`` true when
    w1 was reversed) and w2 so the join point is its first vertex. So the merged
    wire's start is w1's *far* end and its end is w2's *far* end; pull each end's
    marker/label/placement from the corresponding original endpoint. Body style is
    taken from w1. A surviving mid-label (preferring w1) is re-centred within its
    wire's arc-length share of the merged path.
    """
    _carry_wire_body(merged, w1)
    # Merged start = w1's far end: its original start if w1 wasn't reversed, else end.
    if rev1:
        merged.start_marker, merged.start_label, merged.start_label_placement = (
            w1.end_marker, w1.end_label, w1.end_label_placement)
    else:
        merged.start_marker, merged.start_label, merged.start_label_placement = (
            w1.start_marker, w1.start_label, w1.start_label_placement)
    # Merged end = w2's far end: its original end if w2 wasn't reversed, else start.
    if rev2:
        merged.end_marker, merged.end_label, merged.end_label_placement = (
            w2.start_marker, w2.start_label, w2.start_label_placement)
    else:
        merged.end_marker, merged.end_label, merged.end_label_placement = (
            w2.end_marker, w2.end_label, w2.end_label_placement)
    l1, l2 = _polyline_length(w1.points), _polyline_length(w2.points)
    total = l1 + l2
    share1 = (l1 / total) if total > 0 else 0.5
    if w1.mid_label:
        merged.mid_label = w1.mid_label
        merged.mid_label_pos = share1 * 0.5
    elif w2.mid_label:
        merged.mid_label = w2.mid_label
        merged.mid_label_pos = share1 + (1.0 - share1) * 0.5


_C = TypeVar("_C", bound=Component)


def _typed_component(schematic: Schematic, comp_id: str, cls: type[_C]) -> _C:
    """Find the component with *comp_id* and assert it is an instance of *cls*.

    Collapses the recurring find-and-type-check pair while preserving the
    narrowed type for static checkers. Raises ``TypeError`` (not an ``assert``,
    which vanishes under ``-O``) when the component is of the wrong class.
    """
    comp = _find_component(schematic, comp_id)
    if not isinstance(comp, cls):
        raise TypeError(
            f"component {comp_id!r} is {type(comp).__name__}, expected {cls.__name__}"
        )
    return comp


def _wire_touches_position(wire: Wire, pos: tuple[float, float]) -> bool:
    """Return True if either endpoint of *wire* lies exactly on *pos*.

    Connectivity in the v1 model is purely geometric: a wire is "connected" to
    a component if one of the wire's two endpoints coincides with a pin
    coordinate of that component (compared through :func:`point_key`). Deleting
    the component therefore also deletes any wire whose start or end touches
    one of its pins.
    """
    if not wire.points:
        return False
    key = point_key(pos)
    return point_key(wire.points[0]) == key or point_key(wire.points[-1]) == key



def _seg_elbow(
    moved: tuple[float, float], neighbour: tuple[float, float]
) -> tuple[float, float] | None:
    """Elbow vertex between *moved* and *neighbour*, or None if already
    axis-aligned. Keeps the path Manhattan after an endpoint shift.

    Thin wrapper over the shared :func:`route` primitive (spec §6.4): the elbow
    is the vertical-first corner ``(moved.x, neighbour.y)`` — i.e. vertical from
    the moved endpoint, then horizontal into the neighbour. ``route`` returns
    ``[moved, neighbour]`` (no corner) when the two are already axis-aligned, so
    slicing the middle yields ``None`` here.
    """
    mid = route(moved, neighbour, vfirst=True)[1:-1]
    return mid[0] if mid else None


def reshape_wire_points(
    points: list[tuple[float, float]],
    *,
    start_hit: bool,
    end_hit: bool,
    dx: float,
    dy: float,
    simplify: bool = True,
) -> list[tuple[float, float]]:
    """Return the points of a wire after a connected component moves by (dx,dy).

    *start_hit* / *end_hit* say whether the wire's first / last vertex is
    attached to a moving component's pin. Behaviour mirrors :class:`MoveCommand`
    exactly (it is the shared implementation):

    * both ends attached → rigid translation of the whole polyline;
    * one end attached   → that endpoint shifts, with an auto-elbow inserted on
      the adjacent segment if it would otherwise go diagonal.

    When *simplify* is True the result is run through :func:`simplify_points` to
    drop redundant collinear vertices. The live drag preview passes
    ``simplify=False`` for smoother intermediate frames; the committed command
    simplifies.
    """
    pts = list(points)
    if len(pts) < 2 or not (start_hit or end_hit):
        return pts

    if start_hit and end_hit:
        pts = [(x + dx, y + dy) for (x, y) in pts]
    else:
        if start_hit:
            p, nb = pts[0], pts[1]
            new_p = (p[0] + dx, p[1] + dy)
            elbow = _seg_elbow(new_p, nb)
            head = [new_p] + ([elbow] if elbow is not None else [])
            pts = head + pts[1:]
        else:  # end_hit
            p, nb = pts[-1], pts[-2]
            new_p = (p[0] + dx, p[1] + dy)
            elbow = _seg_elbow(new_p, nb)
            tail = ([elbow] if elbow is not None else []) + [new_p]
            pts = pts[:-1] + tail

    return simplify_points(pts) if simplify else pts


# ---------------------------------------------------------------------------
# Command base class
# ---------------------------------------------------------------------------

class Command(ABC):
    """Abstract base for all undoable operations.

    Subclasses implement :meth:`do` and :meth:`undo`, each taking the live
    :class:`Schematic` and mutating it in place. A command instance is bound to
    the specific objects it was constructed with; it is not reusable across
    different schematics.
    """

    #: Short human-readable label, e.g. for an Edit menu ("Undo Place").
    label: str = "Command"

    @abstractmethod
    def do(self, schematic: Schematic) -> None:
        """Apply this command's effect to *schematic*."""

    @abstractmethod
    def undo(self, schematic: Schematic) -> None:
        """Reverse this command's effect on *schematic*."""

    def redo(self, schematic: Schematic) -> None:
        """Re-apply this command. Defaults to :meth:`do`."""
        self.do(schematic)


# ---------------------------------------------------------------------------
# Concrete commands
# ---------------------------------------------------------------------------

class PlaceCommand(Command):
    """Place a single component onto the schematic.

    Inverse: remove the placed component. A deep copy of the component is
    stored so the inserted instance is independent of any caller-held
    reference.
    """

    label = "Place"

    def __init__(self, component: Component) -> None:
        self._component = copy.deepcopy(component)

    @property
    def component_id(self) -> str:
        return self._component.id

    def do(self, schematic: Schematic) -> None:
        # Insert a fresh copy so the live model and our stored template never
        # alias the same object.
        schematic.components.append(copy.deepcopy(self._component))

    def undo(self, schematic: Schematic) -> None:
        schematic.components[:] = [
            c for c in schematic.components if c.id != self._component.id
        ]


class DeleteCommand(Command):
    """Delete components and wires.

    Removes:
      * every component in *component_ids*,
      * every wire in *wire_ids* (directly selected wires), and
      * every wire connected to a deleted component's pins.

    Inverse: restore the deleted components and wires (spec §6.6, §6.3). The
    full removed set is captured at :meth:`do` time so that ``undo`` restores
    exactly what was removed, at its original index.
    """

    label = "Delete"

    def __init__(
        self,
        component_ids: list[str],
        wire_ids: list[str] | None = None,
    ) -> None:
        self._component_ids = list(component_ids)
        self._explicit_wire_ids = list(wire_ids or [])
        # Filled in on do(); deep copies of everything removed.
        self._removed_components: list[Component] = []
        self._removed_wires: list[Wire] = []
        # Remember original ordering positions so undo restores order.
        self._component_index: dict[str, int] = {}
        self._wire_index: dict[str, int] = {}

    def do(self, schematic: Schematic) -> None:
        target_ids = set(self._component_ids)
        explicit_wire_ids = set(self._explicit_wire_ids)

        # Collect the pin positions of every targeted component so we can find
        # the wires that touch them.
        connected_positions: set[tuple[float, float]] = set()
        for comp in schematic.components:
            if comp.id in target_ids:
                for pos in _component_connection_points(comp):
                    connected_positions.add(pos)

        # Snapshot removed components (preserving original index for undo).
        self._removed_components = []
        self._component_index = {}
        for idx, comp in enumerate(schematic.components):
            if comp.id in target_ids:
                self._component_index[comp.id] = idx
                self._removed_components.append(copy.deepcopy(comp))

        # Snapshot removed wires: those explicitly selected, plus any wire with
        # an endpoint on a connected pin.
        self._removed_wires = []
        self._wire_index = {}
        for idx, wire in enumerate(schematic.wires):
            connected = any(
                _wire_touches_position(wire, p) for p in connected_positions
            )
            if wire.id in explicit_wire_ids or connected:
                self._wire_index[wire.id] = idx
                self._removed_wires.append(copy.deepcopy(wire))

        # Apply removals.
        removed_wire_ids = {w.id for w in self._removed_wires}
        schematic.components[:] = [
            c for c in schematic.components if c.id not in target_ids
        ]
        schematic.wires[:] = [
            w for w in schematic.wires if w.id not in removed_wire_ids
        ]

    def undo(self, schematic: Schematic) -> None:
        # Restore components at their original indices (ascending order so that
        # earlier insertions don't shift later target indices).
        for comp in sorted(
            self._removed_components,
            key=lambda c: self._component_index[c.id],
        ):
            idx = self._component_index[comp.id]
            idx = min(idx, len(schematic.components))
            schematic.components.insert(idx, copy.deepcopy(comp))

        for wire in sorted(
            self._removed_wires,
            key=lambda w: self._wire_index[w.id],
        ):
            idx = self._wire_index[wire.id]
            idx = min(idx, len(schematic.wires))
            schematic.wires.insert(idx, copy.deepcopy(wire))


class MoveCommand(Command):
    """Move one or more components by a fixed delta in grid units.

    Connected wire endpoints follow the components they touch: any wire vertex
    that coincides with a moving component's pin (geometric connectivity, the
    same rule :class:`DeleteCommand` uses) is shifted by the same delta. If a
    shifted endpoint would make its adjacent segment diagonal, an auto-elbow
    vertex is inserted so the wire stays Manhattan-legal (spec invariant 4).

    Inverse: restore each affected wire's exact original point list and move the
    components back. Operates by component id so it stays valid even if the
    component list is rebuilt between do/undo cycles.
    """

    label = "Move"

    def __init__(
        self,
        component_ids: list[str],
        delta: tuple[float, float],
        wire_ids: list[str] | None = None,
    ) -> None:
        self._component_ids = list(component_ids)
        self._dx, self._dy = delta
        # Wires that are explicitly selected for rigid translation (in addition
        # to the wires that follow via pin connectivity).
        self._explicit_wire_ids: frozenset[str] = frozenset(wire_ids or [])
        # wire id -> original points, captured at first do() for exact undo.
        self._orig_wire_points: dict[str, list[tuple[float, float]]] = {}
        # wire id -> pristine original wire (deepcopy), so a wire removed by this
        # move is restored verbatim on undo (labels/markers/style intact), not as
        # a bare points-only wire.
        self._orig_wires: dict[str, Wire] = {}
        # wire id -> original position in schematic.wires, so a removed wire is
        # re-inserted where it was (z-order tie-breaks depend on list order).
        self._orig_wire_index: dict[str, int] = {}
        # wire ids that were removed because they collapsed; restored on undo.
        self._removed_wire_ids: set[str] = set()
        # Fresh "re-stretch" leads created when a pin moves off a multi-wire
        # junction (keeps the component connected — see _reshape_wires). Computed
        # once at the first do() (stable ids), re-added on redo, removed on undo.
        self._created_leads: list[Wire] | None = None

    # -- component motion -------------------------------------------------

    def _shift_components(self, schematic: Schematic, sign: float) -> None:
        ids = set(self._component_ids)
        for comp in schematic.components:
            if comp.id in ids:
                x, y = comp.position
                comp.position = (x + sign * self._dx, y + sign * self._dy)

    # -- connectivity -----------------------------------------------------

    def _connected_pin_set(self, schematic: Schematic) -> set[tuple[float, float]]:
        """Absolute pin coordinates (point_key'd) of the moving components,
        BEFORE the move."""
        ids = set(self._component_ids)
        pins: set[tuple[float, float]] = set()
        for comp in schematic.components:
            if comp.id in ids:
                for p in _component_connection_points(comp):
                    pins.add(point_key(p))
        return pins

    # -- wire reshaping ---------------------------------------------------

    def _reshape_wires(self, schematic: Schematic) -> None:
        """Drag connected endpoints by the delta, inserting elbows as needed.

        When every component in the schematic is being moved (select-all drag)
        every wire translates rigidly so free endpoints (open-circle nodes)
        move with the rest of the circuit.  Otherwise only endpoints that sit on
        a moving pin are shifted; free endpoints stay anchored.

        Wires that collapse to a single point (both endpoints moved to the same
        coordinate) are removed from the schematic. Their original points are
        still captured so undo can restore them.
        """
        # "Move the whole circuit" only when components are actually being moved
        # and they are *all* of them. Guard against the empty-set case: a wire-only
        # move (no component_ids) would otherwise satisfy ``set() >= set()`` and
        # rigidly translate every wire, dragging junction taps bodily instead of
        # letting them follow at the shared vertex.
        all_dragged = bool(self._component_ids) and (
            set(self._component_ids) >= {c.id for c in schematic.components}
        )
        pins = self._connected_pin_set(schematic)
        if not pins and not all_dragged and not self._explicit_wire_ids:
            return
        # How many wire endpoints coincide at each coordinate. A moving pin that
        # sits on a *junction* (≥2 wire endpoints there) must NOT drag those wires:
        # doing so tears the net apart when the component is dragged off the node
        # (e.g. moving a capacitor back down off the rail junction it had been
        # dragged onto would carry both rail stubs with it, leaving overlapping
        # segments and a phantom dot). Only a pin that is the *sole* wire endpoint
        # at its coordinate — a genuine lead — follows; on a shared junction the
        # pin stays connected by a fresh **re-stretch lead** from the node to its
        # new position (created below), leaving the existing net intact.
        endpoint_count: dict[tuple[float, float], int] = {}
        for w in schematic.wires:
            if len(w.points) < 2:
                continue
            for end in (point_key(w.points[0]), point_key(w.points[-1])):
                endpoint_count[end] = endpoint_count.get(end, 0) + 1
        # Snapshot the pre-move geometry of explicitly-translated wires (whole-wire
        # drag). Any OTHER wire joined at one of their vertices/segments — a
        # junction tap — must follow at that shared point when the explicit wire
        # moves rigidly, so the connection is preserved (spec §6.3 "keep connected").
        explicit_orig = [
            list(w.points) for w in schematic.wires
            if w.id in self._explicit_wire_ids and len(w.points) >= 2
        ]

        def _on_explicit(p: tuple[float, float]) -> bool:
            return any(_point_on_polyline(p, poly) for poly in explicit_orig)

        to_remove: list[str] = []
        for w_idx, wire in enumerate(schematic.wires):
            pts = wire.points
            if len(pts) < 2:
                continue

            if all_dragged or wire.id in self._explicit_wire_ids:
                start_hit = end_hit = True
            else:
                s_key, e_key = point_key(pts[0]), point_key(pts[-1])
                start_hit = (
                    (s_key in pins and endpoint_count.get(s_key, 0) == 1)
                    or _on_explicit(pts[0])
                )
                end_hit = (
                    (e_key in pins and endpoint_count.get(e_key, 0) == 1)
                    or _on_explicit(pts[-1])
                )
                if not start_hit and not end_hit:
                    continue

            # Capture the pristine path (and full wire + list position, for
            # verbatim restore of a wire this move removes) once, for undo.
            if wire.id not in self._orig_wire_points:
                self._orig_wire_points[wire.id] = list(wire.points)
                self._orig_wires[wire.id] = copy.deepcopy(wire)
                self._orig_wire_index[wire.id] = w_idx

            new_pts = reshape_wire_points(
                pts,
                start_hit=start_hit,
                end_hit=end_hit,
                dx=self._dx,
                dy=self._dy,
            )
            if len(new_pts) < 2:
                # Wire collapsed to a point — remove it.
                to_remove.append(wire.id)
            else:
                wire.points = new_pts

        if to_remove:
            self._removed_wire_ids.update(to_remove)
            schematic.wires[:] = [
                w for w in schematic.wires if w.id not in to_remove
            ]

        self._add_restretch_leads(schematic, pins, endpoint_count, all_dragged)
        self._remove_contained_wires(schematic)

    def _remove_contained_wires(self, schematic: Schematic) -> None:
        """Drop any wire *this move touched* whose polyline now lies entirely on
        top of other wires (a redundant, fully-contained wire — e.g. a lead
        dragged collinearly onto the rail it connects to). Only wires reshaped or
        created by this move are candidates, so an unrelated redundant wire
        elsewhere is left alone. Removals are captured for exact undo."""
        created_ids = {lead.id for lead in (self._created_leads or [])}
        # Candidates: wires this move reshaped or created.
        candidates = set(self._orig_wire_points) | created_ids
        removed: list[str] = []
        for wire in schematic.wires:
            if wire.id not in candidates or len(wire.points) < 2:
                continue  # untouched, or single-point (handled elsewhere)
            others = [w for w in schematic.wires
                      if w.id != wire.id and w.id not in removed]
            if wire_contained_by_others(wire.points, others):
                removed.append(wire.id)
        if not removed:
            return
        for wid in removed:
            if wid in created_ids:
                # A re-stretch lead that turned out redundant — just don't keep it.
                self._created_leads = [
                    lead for lead in self._created_leads if lead.id != wid
                ]
            else:
                if wid not in self._orig_wire_points:
                    idx, w = next(
                        (i, w) for i, w in enumerate(schematic.wires) if w.id == wid
                    )
                    self._orig_wire_points[wid] = list(w.points)
                    self._orig_wires[wid] = copy.deepcopy(w)
                    self._orig_wire_index[wid] = idx
                self._removed_wire_ids.add(wid)
        schematic.wires[:] = [w for w in schematic.wires if w.id not in removed]

    def _add_restretch_leads(
        self,
        schematic: Schematic,
        pins: set[tuple[float, float]],
        endpoint_count: dict[tuple[float, float], int],
        all_dragged: bool,
    ) -> None:
        """Keep a component connected when it is dragged off a multi-wire junction:
        add a fresh lead from each such pin's node (its pre-move coordinate) to its
        new position, so the net stays whole and the connection rubber-bands along
        instead of snapping. No leads when the whole circuit translates rigidly
        (everything moves together, nothing is left behind to reconnect to)."""
        if all_dragged or (self._dx == 0.0 and self._dy == 0.0):
            self._created_leads = self._created_leads or []
            return
        if self._created_leads is None:
            leads: list[Wire] = []
            for p in sorted(pins):
                if endpoint_count.get(p, 0) < 2:
                    continue  # free pin or a single lead that already followed
                new_p = (p[0] + self._dx, p[1] + self._dy)
                path = route(p, new_p)
                if len(path) >= 2 and len(set(path)) >= 2:
                    leads.append(Wire(id=str(uuid.uuid4()), points=path))
            self._created_leads = leads
        existing = {w.id for w in schematic.wires}
        for lead in self._created_leads:
            if lead.id not in existing:
                schematic.wires.append(Wire(id=lead.id, points=list(lead.points)))

    # -- Command API ------------------------------------------------------

    def do(self, schematic: Schematic) -> None:
        # Reshape wires using pin positions BEFORE moving the components.
        self._reshape_wires(schematic)
        self._shift_components(schematic, +1.0)

    def undo(self, schematic: Schematic) -> None:
        self._shift_components(schematic, -1.0)
        # Remove the fresh re-stretch leads this move created.
        if self._created_leads:
            lead_ids = {lead.id for lead in self._created_leads}
            schematic.wires[:] = [w for w in schematic.wires if w.id not in lead_ids]
        # Restore each affected wire's exact original geometry.
        for wire in schematic.wires:
            orig = self._orig_wire_points.get(wire.id)
            if orig is not None:
                wire.points = list(orig)
        # Re-add any wires that were removed because they collapsed — restore the
        # pristine wire verbatim (labels/markers/style intact), not points-only,
        # at its original list position (ascending order so earlier insertions
        # don't shift later target indices).
        existing_ids = {w.id for w in schematic.wires}
        for wid in sorted(
            self._removed_wire_ids,
            key=lambda wid: self._orig_wire_index.get(wid, 0),
        ):
            orig_wire = self._orig_wires.get(wid)
            if orig_wire is not None and wid not in existing_ids:
                idx = min(
                    self._orig_wire_index.get(wid, len(schematic.wires)),
                    len(schematic.wires),
                )
                schematic.wires.insert(idx, copy.deepcopy(orig_wire))

    def redo(self, schematic: Schematic) -> None:
        # On redo the original points are already captured; reapply directly so
        # we don't recapture an already-reshaped path.
        self._reshape_wires(schematic)
        self._shift_components(schematic, +1.0)


class ResizeCommand(Command):
    """Drag the terminal endpoint of a resizable two-terminal component.

    Sets ``Component.span_override`` and reshapes any wire whose endpoint
    coincides with the old terminal-pin position, identical to how
    :class:`MoveCommand` handles connected wires.

    Inverse: restore the previous ``span_override`` and wire geometry.
    """

    label = "Resize"

    def __init__(
        self,
        component_id: str,
        new_span: tuple[float, float],
        old_span: tuple[float, float],
    ) -> None:
        self._component_id = component_id
        self._new_span = new_span
        self._old_span = old_span
        # wire id -> original points list, captured on first do().
        self._orig_wire_points: dict[str, list[tuple[float, float]]] = {}
        # wire id -> pristine original (deepcopy) + list position, so a wire this
        # resize removes is restored verbatim at its original index on undo.
        self._orig_wires: dict[str, Wire] = {}
        self._orig_wire_index: dict[str, int] = {}
        self._removed_wire_ids: set[str] = set()

    def _terminal_pin_pos(
        self, schematic: Schematic, use_old: bool
    ) -> tuple[float, float]:
        """World-space position of the terminal (second) pin given the chosen span."""
        comp = _find_component(schematic, self._component_id)
        from app.components.registry import REGISTRY
        from app.schematic.model import component_pin_positions
        span = self._old_span if use_old else self._new_span
        # Temporarily override to compute the position.
        orig = comp.span_override
        comp.span_override = span
        pins = component_pin_positions(comp)
        comp.span_override = orig
        return pins[1] if len(pins) > 1 else comp.position

    def _reshape_wires(
        self,
        schematic: Schematic,
        old_pin: tuple[float, float],
        dx: float,
        dy: float,
    ) -> None:
        pin_key = point_key(old_pin)
        to_remove: list[str] = []
        for w_idx, wire in enumerate(schematic.wires):
            pts = wire.points
            if len(pts) < 2:
                continue
            start_hit = point_key(pts[0]) == pin_key
            end_hit = point_key(pts[-1]) == pin_key
            if not start_hit and not end_hit:
                continue
            if wire.id not in self._orig_wire_points:
                self._orig_wire_points[wire.id] = list(pts)
                self._orig_wires[wire.id] = copy.deepcopy(wire)
                self._orig_wire_index[wire.id] = w_idx
            new_pts = reshape_wire_points(
                pts, start_hit=start_hit, end_hit=end_hit, dx=dx, dy=dy
            )
            if len(new_pts) < 2:
                to_remove.append(wire.id)
            else:
                wire.points = new_pts
        if to_remove:
            self._removed_wire_ids.update(to_remove)
            schematic.wires[:] = [w for w in schematic.wires if w.id not in to_remove]

    def _reshape_wires_scaled(
        self,
        schematic: Schematic,
        old_span: tuple[float, float],
        new_span: tuple[float, float],
    ) -> None:
        """Reshape edge-connected wires as a box annotation (rect/circle) resizes.

        The resize is an anchored scale about the box's fixed corner
        (``position``): a connection point P maps to
        ``position + (P - position) * (new_span / old_span)``, snapped to the
        0.25 GU grid, so each connection point stays on its corresponding new
        edge.  Points on the two edges through the anchored corner map to
        themselves; the opposite edges translate; mid-edge points scale
        proportionally.  The connection-point set is the kind's own
        (`component_connection_points`): a rect's full perimeter, a circle's
        four cardinal points.
        """
        comp = _find_component(schematic, self._component_id)
        x0, y0 = comp.position
        odx, ody = old_span
        ndx, ndy = new_span

        # Connection points under the OLD span (comp.span_override is already new).
        orig_so = comp.span_override
        comp.span_override = old_span
        old_perim = {point_key(p) for p in _component_connection_points(comp)}
        comp.span_override = orig_so

        def _map(p: tuple[float, float]) -> tuple[float, float]:
            px, py = p
            fx = (px - x0) / odx if odx else 0.0
            fy = (py - y0) / ody if ody else 0.0
            return snap_point((x0 + fx * ndx, y0 + fy * ndy))

        to_remove: list[str] = []
        for w_idx, wire in enumerate(schematic.wires):
            pts = wire.points
            if len(pts) < 2:
                continue
            start_hit = point_key(pts[0]) in old_perim
            end_hit = point_key(pts[-1]) in old_perim
            start_tgt = _map(pts[0]) if start_hit else None
            end_tgt = _map(pts[-1]) if end_hit else None
            start_moves = start_tgt is not None and point_key(start_tgt) != point_key(pts[0])
            end_moves = end_tgt is not None and point_key(end_tgt) != point_key(pts[-1])
            if not start_moves and not end_moves:
                continue
            if wire.id not in self._orig_wire_points:
                self._orig_wire_points[wire.id] = list(pts)
                self._orig_wires[wire.id] = copy.deepcopy(wire)
                self._orig_wire_index[wire.id] = w_idx
            new_pts = list(pts)
            if start_moves:
                new_pts = reshape_wire_points(
                    new_pts, start_hit=True, end_hit=False,
                    dx=start_tgt[0] - new_pts[0][0],
                    dy=start_tgt[1] - new_pts[0][1],
                )
            if end_moves:
                new_pts = reshape_wire_points(
                    new_pts, start_hit=False, end_hit=True,
                    dx=end_tgt[0] - new_pts[-1][0],
                    dy=end_tgt[1] - new_pts[-1][1],
                )
            if len(new_pts) < 2:
                to_remove.append(wire.id)
            else:
                wire.points = new_pts
        if to_remove:
            self._removed_wire_ids.update(to_remove)
            schematic.wires[:] = [w for w in schematic.wires if w.id not in to_remove]

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if is_box_kind(comp):
            comp.span_override = self._new_span
            self._reshape_wires_scaled(schematic, self._old_span, self._new_span)
            return
        old_pin = self._terminal_pin_pos(schematic, use_old=True)
        comp.span_override = self._new_span
        new_pin = self._terminal_pin_pos(schematic, use_old=False)
        dx = new_pin[0] - old_pin[0]
        dy = new_pin[1] - old_pin[1]
        self._reshape_wires(schematic, old_pin, dx, dy)

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.span_override = self._old_span
        # Restore wire geometry exactly.
        for wire in schematic.wires:
            orig = self._orig_wire_points.get(wire.id)
            if orig is not None:
                wire.points = list(orig)
        # Re-add removed wires verbatim (labels/markers/style intact) at their
        # original list positions (ascending so indices stay valid).
        existing_ids = {w.id for w in schematic.wires}
        for wid in sorted(
            self._removed_wire_ids,
            key=lambda wid: self._orig_wire_index.get(wid, 0),
        ):
            orig_wire = self._orig_wires.get(wid)
            if orig_wire is not None and wid not in existing_ids:
                idx = min(
                    self._orig_wire_index.get(wid, len(schematic.wires)),
                    len(schematic.wires),
                )
                schematic.wires.insert(idx, copy.deepcopy(orig_wire))

    def redo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if is_box_kind(comp):
            comp.span_override = self._new_span
            self._reshape_wires_scaled(schematic, self._old_span, self._new_span)
            return
        old_pin = self._terminal_pin_pos(schematic, use_old=True)
        comp.span_override = self._new_span
        new_pin = self._terminal_pin_pos(schematic, use_old=False)
        dx = new_pin[0] - old_pin[0]
        dy = new_pin[1] - old_pin[1]
        self._reshape_wires(schematic, old_pin, dx, dy)


class SetFontSizeCommand(Command):
    """Set font_size on any FontedComponent (text_node, bipole)."""

    label = "Set Font Size"

    def __init__(
        self,
        component_id: str,
        new_size: float,
        old_size: float,
    ) -> None:
        self._component_id = component_id
        self._new_size = new_size
        self._old_size = old_size

    def do(self, schematic: Schematic) -> None:
        comp = _typed_component(schematic, self._component_id, FontedComponent)
        comp.font_size = self._new_size

    def undo(self, schematic: Schematic) -> None:
        comp = _typed_component(schematic, self._component_id, FontedComponent)
        comp.font_size = self._old_size


class SetZOrderCommand(Command):
    """Set z_order on a drawing annotation component (text_node, rect)."""

    label = "Set Z-Order"

    def __init__(self, component_id: str, new_z: int, old_z: int) -> None:
        self._component_id = component_id
        self._new_z = new_z
        self._old_z = old_z

    def do(self, schematic: Schematic) -> None:
        comp = _typed_component(schematic, self._component_id, DrawingComponent)
        comp.z_order = self._new_z

    def undo(self, schematic: Schematic) -> None:
        comp = _typed_component(schematic, self._component_id, DrawingComponent)
        comp.z_order = self._old_z


class SetTextStyleCommand(Command):
    """Set font_bold, font_italic, and font_family on any FontedComponent (text_node, bipole)."""

    label = "Set Text Style"

    def __init__(
        self,
        component_id: str,
        new_bold: bool, new_italic: bool, new_family: str,
        old_bold: bool, old_italic: bool, old_family: str,
    ) -> None:
        self._component_id = component_id
        self._new = (new_bold, new_italic, new_family)
        self._old = (old_bold, old_italic, old_family)

    def _apply(self, schematic: Schematic, vals: tuple) -> None:
        comp = _typed_component(schematic, self._component_id, FontedComponent)
        comp.font_bold, comp.font_italic, comp.font_family = vals

    def do(self, schematic: Schematic) -> None:
        self._apply(schematic, self._new)

    def undo(self, schematic: Schematic) -> None:
        self._apply(schematic, self._old)


class WireCommand(Command):
    """Add a single wire to the schematic.

    Inverse: remove the wire by id.
    """

    label = "Wire"

    def __init__(self, wire: Wire) -> None:
        self._wire = copy.deepcopy(wire)

    @property
    def wire_id(self) -> str:
        return self._wire.id

    def do(self, schematic: Schematic) -> None:
        schematic.wires.append(copy.deepcopy(self._wire))

    def undo(self, schematic: Schematic) -> None:
        schematic.wires[:] = [
            w for w in schematic.wires if w.id != self._wire.id
        ]


class SplitWireCommand(Command):
    """Split an existing wire into two at a point on the wire.

    Used when a new wire connects to the middle of an existing wire's segment:
    the existing wire is replaced by two new wires that meet at the connection
    point, so each half is independently selectable and deletable.  Pairs with
    a :class:`WireCommand` inside a :class:`MacroCommand` so the split + add is
    one undoable action.

    The split site is resolved from the stored *point* against the wire's
    **current** geometry at :meth:`do` time. The constructor's *index* is only a
    hint and never trusted: inside a macro (move + split, nudge + split) the
    wire may have been reshaped/simplified by an earlier command, so an index
    computed against the pre-move geometry would corrupt the polyline. When the
    point is no longer on the wire, or sits at one of its endpoints, the
    command is a clean no-op (and so is its undo).

    Inverse: remove the two halves and restore the original wire.
    """

    label = "Split wire"

    def __init__(
        self,
        wire_id: str,
        index: int,
        point: tuple[float, float],
        new_id1: str | None = None,
        new_id2: str | None = None,
    ) -> None:
        self._wire_id = wire_id
        self._index = index            # hint only; do() re-resolves (see class doc)
        self._point = point
        self._new_id1 = new_id1 or str(uuid.uuid4())
        self._new_id2 = new_id2 or str(uuid.uuid4())
        self._orig_wire: Wire | None = None    # pristine original, for verbatim undo
        self._orig_index: int | None = None   # position in schematic.wires
        self._applied = False          # whether the last do() actually split

    def _find(self, schematic: Schematic, wire_id: str) -> tuple[int, Wire] | None:
        for i, w in enumerate(schematic.wires):
            if w.id == wire_id:
                return i, w
        return None

    def do(self, schematic: Schematic) -> None:
        self._applied = False
        result = self._find(schematic, self._wire_id)
        if result is None:
            return
        pos, wire = result
        pts = list(wire.points)
        if len(pts) < 2:
            return
        # Resolve the insertion index from the point against CURRENT geometry.
        key = point_key(self._point)
        keys = [point_key(p) for p in pts]
        if key in keys:
            idx = keys.index(key)
            if not (0 < idx < len(pts) - 1):
                # The point sits at an endpoint (e.g. a move reshaped an endpoint
                # onto the split site) — nothing to split.
                return
            # Genuine *intermediate* vertex (corner split): split without
            # inserting a duplicate.
            split_pts = pts
        else:
            seg = next(
                (
                    i
                    for i in range(len(pts) - 1)
                    if _point_strictly_on_segment(self._point, pts[i], pts[i + 1])
                ),
                None,
            )
            if seg is None:
                # The point is no longer on this wire (it was reshaped away) —
                # a clean no-op.
                return
            idx = seg + 1
            split_pts = pts[:idx] + [self._point] + pts[idx:]
        half1_pts = split_pts[:idx + 1]
        half2_pts = split_pts[idx:]
        # Belt-and-suspenders: never carve off a degenerate (<2 point) half.
        if len(half1_pts) < 2 or len(half2_pts) < 2:
            return
        # Capture the pristine wire for undo only now that a real split happens
        # (first time only — redo re-splits the identical restored wire).
        if self._orig_wire is None:
            self._orig_wire = copy.deepcopy(wire)
            self._orig_index = pos
        self._applied = True
        # Carry the original's labels/markers/style onto the two halves so a split
        # (e.g. a connection landing mid-segment of a labelled bus wire) preserves
        # them instead of resetting to defaults.
        half1, half2 = _split_wire_into_halves(
            wire, half1_pts, half2_pts, self._new_id1, self._new_id2
        )
        schematic.wires[pos:pos + 1] = [half1, half2]

    def undo(self, schematic: Schematic) -> None:
        if not self._applied or self._orig_wire is None:
            return
        self._applied = False
        # Remove both halves (they may be anywhere in the list now).
        new_ids = {self._new_id1, self._new_id2}
        pos = next(
            (i for i, w in enumerate(schematic.wires) if w.id in new_ids),
            None,
        )
        schematic.wires[:] = [w for w in schematic.wires if w.id not in new_ids]
        orig = copy.deepcopy(self._orig_wire)
        insert_at = pos if pos is not None else self._orig_index or 0
        insert_at = min(insert_at, len(schematic.wires))
        schematic.wires.insert(insert_at, orig)


class MergeWireCommand(Command):
    """Merge two wires that share a free endpoint into one wire.

    Used when deleting a wire dissolves a T-junction, leaving two wire stubs
    whose shared endpoint has degree 2 (no component pin, no third wire).
    Bundled after a :class:`DeleteCommand` inside a :class:`MacroCommand` so
    the delete + merge is one undoable action.

    The two wires are re-resolved at :meth:`do` time: when a referenced wire id
    no longer exists (an **earlier merge in the same macro** consumed it — two
    dissolved junctions can share a wire), the merge falls back to whichever
    wire currently ends at the merge point, so sequential merges compose
    instead of silently no-opping and leaving an unmerged junction.

    Inverse: split the merged wire back into the two originals (the wires
    actually consumed, captured at first do()).
    """

    label = "Merge wires"

    def __init__(
        self,
        wire_id1: str,
        wire_id2: str,
        merge_point: tuple[float, float],
        new_id: str | None = None,
    ) -> None:
        self._wire_id1 = wire_id1
        self._wire_id2 = wire_id2
        self._merge_point = merge_point
        self._new_id = new_id or str(uuid.uuid4())
        self._orig_wire1: Wire | None = None   # pristine originals, for verbatim undo
        self._orig_wire2: Wire | None = None
        self._orig_index: int | None = None

    def _find(self, schematic: Schematic, wire_id: str) -> tuple[int, Wire] | None:
        for i, w in enumerate(schematic.wires):
            if w.id == wire_id:
                return i, w
        return None

    def _resolve(
        self, schematic: Schematic, wire_id: str, exclude: set[str]
    ) -> tuple[int, Wire] | None:
        """Find *wire_id*, or — if it was consumed by an earlier merge — the
        wire that now ends at the merge point (excluding *exclude*)."""
        r = self._find(schematic, wire_id)
        if r is not None:
            return r
        key = point_key(self._merge_point)
        for i, w in enumerate(schematic.wires):
            if w.id in exclude or len(w.points) < 2:
                continue
            if point_key(w.points[0]) == key or point_key(w.points[-1]) == key:
                return i, w
        return None

    def do(self, schematic: Schematic) -> None:
        r1 = self._resolve(schematic, self._wire_id1, {self._wire_id2})
        if r1 is None:
            return
        pos1, w1 = r1
        r2 = self._resolve(schematic, self._wire_id2, {w1.id})
        if r2 is None or r2[1].id == w1.id:
            return
        w2 = r2[1]
        if self._orig_wire1 is None:
            self._orig_wire1 = copy.deepcopy(w1)
            self._orig_wire2 = copy.deepcopy(w2)
            self._orig_index = pos1
        p = self._merge_point
        # Orient w1 so that p is its last point, w2 so that p is its first point.
        rev1 = point_key(w1.points[-1]) != point_key(p)
        rev2 = point_key(w2.points[0]) != point_key(p)
        pts1 = list(reversed(w1.points)) if rev1 else list(w1.points)
        pts2 = list(reversed(w2.points)) if rev2 else list(w2.points)
        merged_pts = simplify_points(pts1 + pts2[1:])
        merged = Wire(id=self._new_id, points=merged_pts)
        # Carry the originals' style and endpoint decorations onto the merged wire
        # so a delete-dissolved T-junction doesn't strip labels/markers/line style.
        _merge_wire_decorations(merged, w1, rev1, w2, rev2)
        # Remove both originals and insert the merged wire where w1 was.
        old_ids = {w1.id, w2.id}
        schematic.wires[:] = [w for w in schematic.wires if w.id not in old_ids]
        insert_at = min(pos1, len(schematic.wires))
        schematic.wires.insert(insert_at, merged)

    def undo(self, schematic: Schematic) -> None:
        if self._orig_wire1 is None:
            return
        result = self._find(schematic, self._new_id)
        pos = result[0] if result is not None else (self._orig_index or 0)
        schematic.wires[:] = [w for w in schematic.wires if w.id != self._new_id]
        w1 = copy.deepcopy(self._orig_wire1)
        w2 = copy.deepcopy(self._orig_wire2)
        insert_at = min(pos, len(schematic.wires))
        schematic.wires.insert(insert_at, w1)
        schematic.wires.insert(insert_at + 1, w2)

    def redo(self, schematic: Schematic) -> None:
        self.do(schematic)


def _move_vertex_points(
    points: list[tuple[float, float]],
    idx: int,
    new_point: tuple[float, float],
) -> list[tuple[float, float]]:
    """Move vertex *idx* to *new_point*, inserting horizontal-first elbows on any
    adjacent segment that turned diagonal; returns the simplified point list
    (may be < 2 points if the wire collapsed)."""
    pts = list(points)
    if not (0 <= idx < len(pts)):
        return pts
    pts[idx] = new_point
    rebuilt: list[tuple[float, float]] = []
    for j, p in enumerate(pts):
        if j == 0:
            rebuilt.append(p)
            continue
        prev = pts[j - 1]
        if j == idx or j - 1 == idx:
            mid = route(prev, p, vfirst=False)[1:-1]
            if mid:
                rebuilt.append(mid[0])
        rebuilt.append(p)
    return simplify_points(rebuilt)


def reshape_junction_wire(
    points: list[tuple[float, float]],
    idx: int,
    new_point: tuple[float, float],
) -> list[tuple[float, float]]:
    """Move a junction vertex (*idx*) to *new_point*, **preserving the orientation
    of the segment that enters the junction** so a wire arriving vertically keeps
    arriving vertically (and horizontally likewise).

    Only endpoint junctions (``idx`` is 0 or the last index) are orientation-
    preserved; an interior junction vertex falls back to a plain vertex move. The
    terminal segment runs from the junction vertex to its neighbour:

    * neighbour is an **interior corner** → relocate it along the terminal axis
      (move its parallel coordinate to the new junction position), which keeps
      both the terminal segment and the corner's other (perpendicular) segment
      valid without inserting anything;
    * neighbour is the **far endpoint** (2-point wire, can't be moved) → insert
      an elbow whose first leg out of the junction keeps the original orientation.
    """
    pts = list(points)
    n = len(pts)
    if n < 2 or not (0 <= idx < n):
        return pts
    if idx not in (0, n - 1):
        return _move_vertex_points(pts, idx, new_point)

    nb = 1 if idx == 0 else n - 2
    old = pts[idx]
    nbp = pts[nb]
    vertical = abs(nbp[0] - old[0]) < 1e-9    # terminal segment is vertical
    pts[idx] = new_point

    if 1 <= nb <= n - 2:
        # Interior corner: slide it along the terminal axis to follow the move.
        if vertical:
            pts[nb] = (new_point[0], nbp[1])
        else:
            pts[nb] = (nbp[0], new_point[1])
    else:
        # Far endpoint: insert an orientation-preserving elbow.
        if vertical:
            corner = (new_point[0], nbp[1])
        else:
            corner = (nbp[0], new_point[1])
        if corner != new_point and corner != nbp:
            pts.insert(1 if idx == 0 else n - 1, corner)
    return simplify_points(pts)


class MoveWireVertexCommand(Command):
    """Move a single vertex of a wire to a new position.

    The dragged vertex goes exactly where it is dropped; each adjacent segment
    that would become diagonal gets an auto-elbow inserted so the path stays
    Manhattan (spec invariant 4). The result is then run through
    :func:`simplify_points` to drop any vertices that became redundant.

    Endpoints that sit on a component pin are *not* moved by this command — the
    scene refuses to start a vertex drag there (those endpoints are owned by
    wire-following). This command assumes the target vertex is draggable.

    Inverse: restore the wire's exact original point list.
    """

    label = "Move node"

    def __init__(
        self,
        wire_id: str,
        index: int,
        new_point: tuple[float, float],
    ) -> None:
        self._wire_id = wire_id
        self._index = index
        self._new_point = new_point
        self._orig_points: list[tuple[float, float]] | None = None
        # Pristine original (deepcopy) + list position, so a collapsed-and-removed
        # wire is restored verbatim (labels/markers/style intact) at its index.
        self._orig_wire: Wire | None = None
        self._orig_index: int | None = None
        self._removed = False   # set when the drag collapses the wire to a point

    def do(self, schematic: Schematic) -> None:
        wire = _find_wire(schematic, self._wire_id)
        if wire is None:
            return
        if self._orig_points is None:
            self._orig_points = list(wire.points)
            self._orig_wire = copy.deepcopy(wire)
            self._orig_index = next(
                i for i, w in enumerate(schematic.wires) if w.id == self._wire_id
            )

        if not (0 <= self._index < len(self._orig_points)):
            return
        new_pts = _move_vertex_points(self._orig_points, self._index, self._new_point)
        if len(new_pts) < 2:
            # The drag collapsed the wire to a single point — it has no segment
            # and would be a stray degenerate wire. Remove it (restored on undo),
            # mirroring MoveCommand's handling of collapsed wire-following.
            self._removed = True
            schematic.wires[:] = [w for w in schematic.wires if w.id != self._wire_id]
        else:
            self._removed = False
            wire.points = new_pts

    def undo(self, schematic: Schematic) -> None:
        if self._orig_points is None:
            return
        if self._removed:
            # Re-add the collapsed wire verbatim at its original position.
            if not any(w.id == self._wire_id for w in schematic.wires):
                idx = min(
                    self._orig_index if self._orig_index is not None
                    else len(schematic.wires),
                    len(schematic.wires),
                )
                schematic.wires.insert(idx, copy.deepcopy(self._orig_wire))
            self._removed = False
            return
        wire = _find_wire(schematic, self._wire_id)
        if wire is not None:
            wire.points = list(self._orig_points)

    def redo(self, schematic: Schematic) -> None:
        self.do(schematic)


class MoveJunctionCommand(Command):
    """Move a junction — every wire vertex in *targets* — to one point, keeping
    each wire's orientation into the junction (`reshape_junction_wire`).

    *targets* is ``[(wire_id, index), ...]`` (the coincident vertices). A wire
    that collapses to a single point is removed and restored on undo, mirroring
    :class:`MoveWireVertexCommand`.

    Inverse: restore every affected wire's exact original point list.
    """

    label = "Move junction"

    def __init__(
        self, targets: list[tuple[str, int]], new_point: tuple[float, float]
    ) -> None:
        self._targets = list(targets)
        self._new_point = new_point
        self._orig: dict[str, list[tuple[float, float]]] = {}
        # Pristine originals (deepcopy) + list positions for verbatim restore of
        # wires this move removes (labels/markers/style and z-order intact).
        self._orig_wire: dict[str, Wire] = {}
        self._orig_index: dict[str, int] = {}
        self._removed: set[str] = set()

    def do(self, schematic: Schematic) -> None:
        # Group targets by wire and apply every index move to ONE evolving copy
        # of the pristine points: a wire with BOTH endpoints at the junction
        # would otherwise have its second reshape computed from the pristine
        # list, overwriting the first.
        by_wire: dict[str, list[int]] = {}
        for wire_id, idx in self._targets:
            by_wire.setdefault(wire_id, []).append(idx)
        for wire_id, idxs in by_wire.items():
            wire = _find_wire(schematic, wire_id)
            if wire is None:
                continue
            if wire_id not in self._orig:
                self._orig[wire_id] = list(wire.points)
                self._orig_wire[wire_id] = copy.deepcopy(wire)
                self._orig_index[wire_id] = next(
                    i for i, w in enumerate(schematic.wires) if w.id == wire_id
                )
            orig = self._orig[wire_id]
            if not (0 <= idxs[0] < len(orig)):
                continue
            # All targets share the junction coordinate; reshaping may renumber
            # vertices, so locate each remaining coincident vertex by coordinate
            # in the evolving copy rather than by its pristine index.
            old_key = point_key(orig[idxs[0]])
            pts = list(orig)
            for _ in idxs:
                j = next(
                    (i for i, p in enumerate(pts) if point_key(p) == old_key),
                    None,
                )
                if j is None:
                    break
                pts = reshape_junction_wire(pts, j, self._new_point)
                if point_key(self._new_point) == old_key:
                    break       # zero-distance move; avoid re-finding forever
            if len(pts) < 2:
                self._removed.add(wire_id)
                schematic.wires[:] = [w for w in schematic.wires if w.id != wire_id]
            else:
                wire.points = pts

    def undo(self, schematic: Schematic) -> None:
        existing = {w.id for w in schematic.wires}
        for wire_id in sorted(
            self._orig, key=lambda wid: self._orig_index.get(wid, 0)
        ):
            if wire_id in self._removed:
                if wire_id not in existing:
                    idx = min(
                        self._orig_index.get(wire_id, len(schematic.wires)),
                        len(schematic.wires),
                    )
                    schematic.wires.insert(
                        idx, copy.deepcopy(self._orig_wire[wire_id])
                    )
            else:
                w = _find_wire(schematic, wire_id)
                if w is not None:
                    w.points = list(self._orig[wire_id])
        self._removed.clear()

    def redo(self, schematic: Schematic) -> None:
        self.do(schematic)


class EditCommand(Command):
    """Replace the options string of a single component.

    Inverse: restore the previous options string.
    """

    label = "Edit"

    def __init__(
        self,
        component_id: str,
        new_options: str,
        old_options: str | None = None,
    ) -> None:
        self._component_id = component_id
        self._new_options = new_options
        # If old_options is not supplied, it is captured on first do().
        self._old_options: str | None = old_options

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_options is None:
            self._old_options = comp.options
        comp.options = self._new_options

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.options = self._old_options or ""


class MoveOptionsLabelCommand(Command):
    """Set (or clear) the label_offset of a single component's options label.

    Inverse: restore the previous label_offset value.
    """

    label = "Move Label"

    # Sentinel distinguishing "old value not yet captured" from explicit None.
    _UNSET: tuple[()] = ()

    def __init__(
        self,
        component_id: str,
        new_offset: tuple[float, float] | None,
    ) -> None:
        self._component_id = component_id
        self._new_offset = new_offset
        self._old_offset: object = self._UNSET

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_offset is self._UNSET:
            self._old_offset = comp.label_offset
        comp.label_offset = self._new_offset

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.label_offset = self._old_offset  # type: ignore[assignment]


class RotateCommand(Command):
    """Change the rotation of a single component by a multiple of 90°.

    Inverse: restore the previous rotation value.
    """

    label = "Rotate"

    def __init__(self, component_id: str, new_rotation: int, old_rotation: int | None = None) -> None:
        if new_rotation not in (0, 90, 180, 270):
            raise ValueError(f"Invalid rotation {new_rotation!r}")
        self._component_id = component_id
        self._new_rotation = new_rotation
        self._old_rotation: int | None = old_rotation

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_rotation is None:
            self._old_rotation = comp.rotation
        comp.rotation = self._new_rotation

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.rotation = self._old_rotation if self._old_rotation is not None else 0


class MirrorCommand(Command):
    """Toggle (or explicitly set) the horizontal mirror state of a component.

    Inverse: restore the previous mirror value.
    """

    label = "Mirror"

    def __init__(self, component_id: str, new_mirror: bool, old_mirror: bool | None = None) -> None:
        self._component_id = component_id
        self._new_mirror = new_mirror
        self._old_mirror: bool | None = old_mirror

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_mirror is None:
            self._old_mirror = comp.mirror
        comp.mirror = self._new_mirror

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.mirror = self._old_mirror if self._old_mirror is not None else False


class SetVariantCommand(Command):
    """Toggle a named boolean variant on a component (e.g. ``filled``,
    ``body_diode``).  Generic over any variant the component's kind declares."""

    label = "Set Variant"

    def __init__(self, component_id: str, name: str, new_value: bool,
                 old_value: bool | None = None) -> None:
        self._component_id = component_id
        self._name = name
        self._new_value = new_value
        self._old_value: bool | None = old_value

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_value is None:
            self._old_value = comp.variants.get(self._name, False)
        comp.variants[self._name] = self._new_value

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.variants[self._name] = bool(self._old_value)


class SetParamCommand(Command):
    """Set an integer parameter on a parametric component (e.g. a logic gate's
    input count).  Generic over any parameter the component's kind declares.

    Changing the value relocates the pins (a gate's inputs redistribute about the
    output) and may add or remove some, so connected wires **follow** their pins
    (`_follow_pins`), keyed by pin name: a pin that still exists moves to its new
    position; a pin that is removed (fewer inputs) leaves its wire snapped to the
    grid (a valid, disconnected end the user can rewire). Undo restores wiring.
    """

    label = "Set Parameter"

    def __init__(self, component_id: str, name: str, new_value: int,
                 old_value: int | None = None, had_value: bool | None = None) -> None:
        self._component_id = component_id
        self._name = name
        self._new_value = int(new_value)
        self._old_value = old_value
        self._had_value = had_value
        self._orig_wires: dict[str, Wire] = {}

    def _named_pins(self, schematic: Schematic) -> dict[str, tuple[float, float]]:
        from app.components import library
        comp = _find_component(schematic, self._component_id)
        names = [p.name for p in library.resolved_pins(comp)]
        return dict(zip(names, _component_pin_positions(comp)))

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._had_value is None:
            self._had_value = self._name in comp.params
            self._old_value = comp.params.get(self._name)
        old_named = self._named_pins(schematic)
        comp.params[self._name] = self._new_value
        new_named = self._named_pins(schematic)
        # Map by pin NAME: a surviving pin moves to its new position; a removed pin
        # sends its wire to the nearest grid node (valid, disconnected).
        old_to_new: dict[tuple[float, float], tuple[float, float]] = {}
        for name, opos in old_named.items():
            tgt = new_named.get(name, snap_point(opos))
            old_to_new[point_key(opos)] = tgt
        _follow_pins(schematic, old_to_new, self._orig_wires)

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._had_value:
            comp.params[self._name] = int(self._old_value)
        else:
            comp.params.pop(self._name, None)
        _restore_followed_wires(schematic, self._orig_wires)


class SetFillColorCommand(Command):
    """Set fill_color on a StyledComponent (bipole or rect)."""

    label = "Set Fill"

    def __init__(self, component_id: str, new_fill: str, old_fill: str) -> None:
        self._component_id = component_id
        self._new_fill = new_fill
        self._old_fill = old_fill

    def do(self, schematic: Schematic) -> None:
        from app.components.model import StyledComponent
        comp = _typed_component(schematic, self._component_id, StyledComponent)
        comp.fill_color = self._new_fill

    def undo(self, schematic: Schematic) -> None:
        from app.components.model import StyledComponent
        comp = _typed_component(schematic, self._component_id, StyledComponent)
        comp.fill_color = self._old_fill


class SetComponentLineWidthCommand(Command):
    """Set the unified stroke/outline width (``line_width``) on any component.

    One command for both circuit symbols and block outlines (rect/circle/bipole)
    — there is no separate border-width command."""

    label = "Set Stroke Width"

    def __init__(self, component_id: str, new_width: float, old_width: float) -> None:
        self._component_id = component_id
        self._new_width = new_width
        self._old_width = old_width

    def do(self, schematic: Schematic) -> None:
        from app.components.model import Component
        _typed_component(schematic, self._component_id, Component).line_width = self._new_width

    def undo(self, schematic: Schematic) -> None:
        from app.components.model import Component
        _typed_component(schematic, self._component_id, Component).line_width = self._old_width


def _refollow_wire_end(
    pts: list[tuple[float, float]], at_start: bool, new_pin: tuple[float, float]
) -> list[tuple[float, float]]:
    """Re-route the pin end of *pts* to *new_pin*.

    Drops the old pin vertex (and any trailing off-grid approach vertices that
    belonged to it), then routes from the remaining on-grid anchor to the new pin
    with :func:`route_pin_aware` (extend along the pin's lead line, elbow onto the
    grid). Keeps every interior vertex valid for the *new* geometry.
    """
    work = list(reversed(pts)) if at_start else list(pts)
    work = work[:-1]                                      # drop the old pin vertex
    while len(work) >= 2 and not (
        coord_on_grid(work[-1][0]) and coord_on_grid(work[-1][1])
    ):
        work = work[:-1]                                  # drop the old approach
    anchor = work[-1]
    work = work[:-1] + route_pin_aware(anchor, new_pin)
    return list(reversed(work)) if at_start else work


def _follow_pins(
    schematic: Schematic,
    old_to_new: dict[tuple[float, float], tuple[float, float]],
    orig_wires: dict[str, Wire],
) -> None:
    """Re-route every wire whose endpoint sits on a relocated pin.

    *old_to_new* maps a rounded old pin coordinate to its new position. Each
    affected wire is re-routed (:func:`_refollow_wire_end`) and its pristine
    original is captured into *orig_wires* (for exact undo). A wire that collapses
    to a single point is removed (its original is still in *orig_wires*)."""
    to_remove: list[str] = []
    for wire in schematic.wires:
        pts = wire.points
        if len(pts) < 2:
            continue
        start_new = old_to_new.get(point_key(pts[0]))
        end_new = old_to_new.get(point_key(pts[-1]))
        if start_new is None and end_new is None:
            continue
        if wire.id not in orig_wires:
            orig_wires[wire.id] = copy.deepcopy(wire)
        new_pts = list(pts)
        if end_new is not None and end_new != pts[-1]:
            new_pts = _refollow_wire_end(new_pts, at_start=False, new_pin=end_new)
        if start_new is not None and start_new != pts[0]:
            new_pts = _refollow_wire_end(new_pts, at_start=True, new_pin=start_new)
        new_pts = simplify_points(new_pts)
        if len(new_pts) < 2:
            to_remove.append(wire.id)
        else:
            wire.points = new_pts
    if to_remove:
        rm = set(to_remove)
        schematic.wires[:] = [w for w in schematic.wires if w.id not in rm]


def _restore_followed_wires(schematic: Schematic, orig_wires: dict[str, Wire]) -> None:
    """Undo a :func:`_follow_pins`: restore each touched wire from its pristine
    original (geometry and style/labels), re-adding any that were removed."""
    present = {w.id for w in schematic.wires}
    for wid, orig in orig_wires.items():
        if wid in present:
            next(x for x in schematic.wires if x.id == wid).points = list(orig.points)
        else:
            schematic.wires.append(copy.deepcopy(orig))
    orig_wires.clear()


class SetComponentScaleCommand(Command):
    """Set a logic gate's size multiplier (``scale``) on a component.

    Scaling a gate relocates its pins (each by a different, non-uniform amount —
    a scaled gate's pins sit at ``base_offset × scale``), so any wire connected to
    a pin **follows** it (`_follow_pins`), just as it follows a moved/resized
    component, keeping the schematic valid. Undo restores the original wiring.
    """

    label = "Set Scale"

    def __init__(self, component_id: str, new_scale: float, old_scale: float) -> None:
        self._component_id = component_id
        self._new_scale = new_scale
        self._old_scale = old_scale
        self._orig_wires: dict[str, Wire] = {}

    def _pin_positions(self, schematic: Schematic, scale: float) -> list[tuple[float, float]]:
        comp = _typed_component(schematic, self._component_id, Component)
        orig = comp.scale
        comp.scale = scale
        pins = _component_pin_positions(comp)
        comp.scale = orig
        return pins

    def do(self, schematic: Schematic) -> None:
        old_pins = self._pin_positions(schematic, self._old_scale)
        comp = _typed_component(schematic, self._component_id, Component)
        comp.scale = self._new_scale
        new_pins = self._pin_positions(schematic, self._new_scale)
        old_to_new = {point_key(o): n for o, n in zip(old_pins, new_pins)}
        _follow_pins(schematic, old_to_new, self._orig_wires)

    def undo(self, schematic: Schematic) -> None:
        _typed_component(schematic, self._component_id, Component).scale = self._old_scale
        _restore_followed_wires(schematic, self._orig_wires)


class SetLineStyleCommand(Command):
    """Set line_style on a StyledComponent (bipole or rect)."""

    label = "Set Line Style"

    def __init__(self, component_id: str, new_style: str, old_style: str) -> None:
        self._component_id = component_id
        self._new_style = new_style
        self._old_style = old_style

    def do(self, schematic: Schematic) -> None:
        from app.components.model import StyledComponent
        comp = _typed_component(schematic, self._component_id, StyledComponent)
        comp.line_style = self._new_style

    def undo(self, schematic: Schematic) -> None:
        from app.components.model import StyledComponent
        comp = _typed_component(schematic, self._component_id, StyledComponent)
        comp.line_style = self._old_style


class SetWireLineStyleCommand(Command):
    """Set line_style on a Wire."""

    label = "Set Wire Line Style"

    def __init__(self, wire_id: str, new_style: str, old_style: str) -> None:
        self._wire_id = wire_id
        self._new_style = new_style
        self._old_style = old_style

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "line_style", self._new_style)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "line_style", self._old_style)


class SetWireLineWidthCommand(Command):
    """Set line_width (pt) on a Wire."""

    label = "Set Wire Line Width"

    def __init__(self, wire_id: str, new_width: float, old_width: float) -> None:
        self._wire_id = wire_id
        self._new_width = new_width
        self._old_width = old_width

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "line_width", self._new_width)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "line_width", self._old_width)


class SetWireNoJunctionDotsCommand(Command):
    """Toggle no_junction_dots on a Wire."""

    label = "Set Wire Junction Dots"

    def __init__(self, wire_id: str, new_value: bool, old_value: bool) -> None:
        self._wire_id = wire_id
        self._new_value = new_value
        self._old_value = old_value

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "no_junction_dots", self._new_value)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "no_junction_dots", self._old_value)


class SetWireNoTerminationDotsCommand(Command):
    """Toggle no_termination_dots on a Wire."""

    label = "Set Wire Termination Dots"

    def __init__(self, wire_id: str, new_value: bool, old_value: bool) -> None:
        self._wire_id = wire_id
        self._new_value = new_value
        self._old_value = old_value

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "no_termination_dots", self._new_value)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "no_termination_dots", self._old_value)


class SetWireHopModeCommand(Command):
    """Set hop_mode on a Wire (per-wire line-hop override: ''/never/always)."""

    label = "Set Wire Line Hops"

    def __init__(self, wire_id: str, new_value: str, old_value: str) -> None:
        self._wire_id = wire_id
        self._new_value = new_value
        self._old_value = old_value

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "hop_mode", self._new_value)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "hop_mode", self._old_value)


class SetWireZOrderCommand(Command):
    """Set z_order (layer + hop priority) on a Wire."""

    label = "Set Wire Z-Order"

    def __init__(self, wire_id: str, new_value: int, old_value: int) -> None:
        self._wire_id = wire_id
        self._new_value = new_value
        self._old_value = old_value

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "z_order", self._new_value)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "z_order", self._old_value)


class SetWireStartMarkerCommand(Command):
    """Set the custom endpoint marker on a Wire's first point."""

    label = "Set Wire Start Marker"

    def __init__(self, wire_id: str, new_marker: str, old_marker: str) -> None:
        self._wire_id = wire_id
        self._new_marker = new_marker
        self._old_marker = old_marker

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "start_marker", self._new_marker)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "start_marker", self._old_marker)


class SetWireEndMarkerCommand(Command):
    """Set the custom endpoint marker on a Wire's last point."""

    label = "Set Wire End Marker"

    def __init__(self, wire_id: str, new_marker: str, old_marker: str) -> None:
        self._wire_id = wire_id
        self._new_marker = new_marker
        self._old_marker = old_marker

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "end_marker", self._new_marker)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "end_marker", self._old_marker)


class SetWireStartLabelCommand(Command):
    """Set the text/math label at a Wire's first point."""

    label = "Set Wire Start Label"

    def __init__(self, wire_id: str, new_label: str, old_label: str) -> None:
        self._wire_id = wire_id
        self._new_label = new_label
        self._old_label = old_label

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "start_label", self._new_label)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "start_label", self._old_label)


class SetWireEndLabelCommand(Command):
    """Set the text/math label at a Wire's last point."""

    label = "Set Wire End Label"

    def __init__(self, wire_id: str, new_label: str, old_label: str) -> None:
        self._wire_id = wire_id
        self._new_label = new_label
        self._old_label = old_label

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "end_label", self._new_label)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "end_label", self._old_label)


class SetWireMidLabelCommand(Command):
    """Set the text/math label drawn over the middle of a Wire."""

    label = "Set Wire Mid Label"

    def __init__(self, wire_id: str, new_label: str, old_label: str) -> None:
        self._wire_id = wire_id
        self._new_label = new_label
        self._old_label = old_label

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "mid_label", self._new_label)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "mid_label", self._old_label)


class SetWireMidLabelPosCommand(Command):
    """Set the fractional position of a Wire's mid-label."""

    label = "Move Wire Mid Label"

    def __init__(self, wire_id: str, new_pos: float, old_pos: float) -> None:
        self._wire_id = wire_id
        self._new_pos = new_pos
        self._old_pos = old_pos

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "mid_label_pos", self._new_pos)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "mid_label_pos", self._old_pos)


class SetWireStartLabelPlacementCommand(Command):
    """Set the placement of a Wire's start label ("" / "above" / "below")."""

    label = "Set Wire Start Label Placement"

    def __init__(self, wire_id: str, new_placement: str, old_placement: str) -> None:
        self._wire_id = wire_id
        self._new_placement = new_placement
        self._old_placement = old_placement

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "start_label_placement", self._new_placement)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "start_label_placement", self._old_placement)


class SetWireEndLabelPlacementCommand(Command):
    """Set the placement of a Wire's end label ("" / "above" / "below")."""

    label = "Set Wire End Label Placement"

    def __init__(self, wire_id: str, new_placement: str, old_placement: str) -> None:
        self._wire_id = wire_id
        self._new_placement = new_placement
        self._old_placement = old_placement

    def do(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "end_label_placement", self._new_placement)

    def undo(self, schematic: Schematic) -> None:
        _set_wire_attr(schematic, self._wire_id, "end_label_placement", self._old_placement)


class GroupRotateCommand(Command):
    """Rotate a group of components and wires 90° CW around a shared centroid.

    Three categories of non-selected wires are handled automatically:

    * **Internal wires** — both endpoints land on selected-component pins.
      All vertices are rotated with the group.
    * **Boundary wires** — exactly one endpoint lands on a selected pin.
      That endpoint follows its new pin position; the wire is reshaped with
      the same elbow logic used by ``MoveCommand``.
    * **Unconnected wires** — not touched.

    ``Component.label_offset`` is cleared (reset to auto) for each rotated
    component because the parent-local coordinate system changes with the
    rotation.
    """

    label = "Rotate"

    def __init__(
        self,
        component_ids: list[str],
        wire_ids: list[str],
        centroid: tuple[float, float],
    ) -> None:
        self._component_ids = list(component_ids)
        self._wire_ids = list(wire_ids)
        self._cx, self._cy = centroid
        # Captured at first do() — never overwritten on redo.
        self._orig_comp: dict[str, tuple] = {}
        self._orig_wire: dict[str, list] = {}
        # Pristine originals (deepcopy) + list positions, so a wire removed by
        # this rotation is restored verbatim at its original index on undo.
        self._orig_wire_obj: dict[str, Wire] = {}
        self._orig_wire_index: dict[str, int] = {}
        # Boundary wires that collapsed to a point under the rotation and were
        # removed (recomputed each do(); restored on undo).
        self._removed_wire_ids: set[str] = set()

    @staticmethod
    def _rot90cw(
        x: float, y: float, cx: float, cy: float
    ) -> tuple[float, float]:
        """Rotate (x, y) 90° CW on screen (Qt Y-down) around (cx, cy).

        component_pin_positions uses rotation=90 → (dx,dy) to (-dy, dx),
        which is CW on a Y-down canvas.  Geometric position rotation must
        use the same convention so rotated positions land exactly on the
        new pin locations.
        """
        dx, dy = x - cx, y - cy
        return (cx - dy, cy + dx)

    def _build_pin_motion(
        self,
        schematic: Schematic,
        comp_id_set: set[str],
    ) -> dict[tuple[float, float], tuple[float, float]]:
        """Map old_pin_pos → new_pin_pos for every pin of every selected
        component (keys are :func:`point_key`-rounded for noise-safe lookups)."""
        mapping: dict[tuple[float, float], tuple[float, float]] = {}
        for comp in schematic.components:
            if comp.id not in comp_id_set:
                continue
            for pin_pos in _component_pin_positions(comp):
                mapping[point_key(pin_pos)] = self._rot90cw(
                    pin_pos[0], pin_pos[1], self._cx, self._cy
                )
        return mapping

    def do(self, schematic: Schematic) -> None:
        comp_id_set = set(self._component_ids)
        wire_id_set = set(self._wire_ids)

        # Build pin-motion map BEFORE moving anything.
        pin_motion = self._build_pin_motion(schematic, comp_id_set)

        # Classify non-selected wires.
        fully_rotate_extra: set[str] = set()
        boundary: dict[str, tuple[bool, bool]] = {}  # id → (start_hit, end_hit)
        for wire in schematic.wires:
            if wire.id in wire_id_set:
                continue
            s, e = point_key(wire.points[0]), point_key(wire.points[-1])
            sh, eh = s in pin_motion, e in pin_motion
            if sh and eh:
                fully_rotate_extra.add(wire.id)
            elif sh or eh:
                boundary[wire.id] = (sh, eh)

        # Capture original state (idempotent — only on first do()).
        for comp in schematic.components:
            if comp.id in comp_id_set and comp.id not in self._orig_comp:
                self._orig_comp[comp.id] = (
                    comp.position, comp.rotation, comp.mirror, comp.label_offset
                )
        for w_idx, wire in enumerate(schematic.wires):
            wid = wire.id
            if wid not in self._orig_wire and (
                wid in wire_id_set
                or wid in fully_rotate_extra
                or wid in boundary
            ):
                self._orig_wire[wid] = list(wire.points)
                self._orig_wire_obj[wid] = copy.deepcopy(wire)
                self._orig_wire_index[wid] = w_idx

        # Rotate components.
        for comp in schematic.components:
            if comp.id not in comp_id_set:
                continue
            nx, ny = self._rot90cw(
                comp.position[0], comp.position[1], self._cx, self._cy
            )
            comp.position = (nx, ny)
            # Mirror is a global Flip-X applied OUTERMOST (after the stored
            # rotation), so a visual 90° CW turn corresponds to rotation−90 for
            # a mirrored component (R90·M·R(r) = M·R(r−90)). Using +90 would
            # send the pins to the mirror-image of where _rot90cw moved the
            # component and its wires, detaching every connection.
            step = -90 if comp.mirror else 90
            comp.rotation = (comp.rotation + step) % 360
            comp.label_offset = None

        # Rotate selected + internal wire vertices.
        for wire in schematic.wires:
            if wire.id not in wire_id_set and wire.id not in fully_rotate_extra:
                continue
            orig = self._orig_wire.get(wire.id, wire.points)
            wire.points = [
                self._rot90cw(x, y, self._cx, self._cy) for x, y in orig
            ]

        # Reshape boundary wires.
        collapsed: list[str] = []
        for wire in schematic.wires:
            if wire.id not in boundary:
                continue
            sh, eh = boundary[wire.id]
            orig = self._orig_wire[wire.id]
            moving_pt = orig[0] if sh else orig[-1]
            new_pt = pin_motion.get(point_key(moving_pt))
            if new_pt is None:
                continue
            dx = new_pt[0] - moving_pt[0]
            dy = new_pt[1] - moving_pt[1]
            new_pts = reshape_wire_points(
                orig, start_hit=sh, end_hit=eh, dx=dx, dy=dy
            )
            if len(new_pts) < 2:
                # The rotation folded the wire's moving end onto its fixed end —
                # it collapsed to a point. Remove it rather than leave a stray
                # degenerate wire (mirrors MoveCommand). Restored on undo.
                collapsed.append(wire.id)
            else:
                wire.points = new_pts

        self._removed_wire_ids = set(collapsed)
        if collapsed:
            schematic.wires[:] = [
                w for w in schematic.wires if w.id not in self._removed_wire_ids
            ]

    def undo(self, schematic: Schematic) -> None:
        for comp in schematic.components:
            if comp.id in self._orig_comp:
                pos, rot, mir, loff = self._orig_comp[comp.id]
                comp.position = pos
                comp.rotation = rot
                comp.mirror = mir
                comp.label_offset = loff
        for wire in schematic.wires:
            if wire.id in self._orig_wire:
                wire.points = list(self._orig_wire[wire.id])
        # Re-add any boundary wires that collapsed (and were removed) under
        # do() — verbatim (labels/markers/style intact) at their original list
        # positions (ascending so earlier insertions don't shift later ones).
        existing = {w.id for w in schematic.wires}
        for wid in sorted(
            self._removed_wire_ids,
            key=lambda wid: self._orig_wire_index.get(wid, 0),
        ):
            orig_wire = self._orig_wire_obj.get(wid)
            if orig_wire is not None and wid not in existing:
                idx = min(
                    self._orig_wire_index.get(wid, len(schematic.wires)),
                    len(schematic.wires),
                )
                schematic.wires.insert(idx, copy.deepcopy(orig_wire))
        self._removed_wire_ids = set()


class SetDocumentPropertiesCommand(Command):
    """Set the per-document CircuiTikZ label-style conventions (spec §7.2).

    Edits the :class:`Schematic`'s ``voltage_style`` / ``current_style`` fields
    (the Document inspector tab) as one undoable step. Only the fields whose
    new value differs from the captured old value are written, so undo restores
    exactly what changed.
    """

    label = "Document Properties"

    def __init__(
        self,
        *,
        new_voltage: str,
        new_current: str,
        old_voltage: str,
        old_current: str,
    ) -> None:
        self._new_voltage = new_voltage
        self._new_current = new_current
        self._old_voltage = old_voltage
        self._old_current = old_current

    def do(self, schematic: Schematic) -> None:
        if self._new_voltage != self._old_voltage:
            schematic.voltage_style = self._new_voltage
        if self._new_current != self._old_current:
            schematic.current_style = self._new_current

    def undo(self, schematic: Schematic) -> None:
        if self._new_voltage != self._old_voltage:
            schematic.voltage_style = self._old_voltage
        if self._new_current != self._old_current:
            schematic.current_style = self._old_current


class MacroCommand(Command):
    """A composite command applied as a single undoable unit.

    ``do`` runs children in order; ``undo`` runs them in reverse order so that
    inverse effects unwind correctly (spec §6.6).
    """

    label = "Macro"

    def __init__(self, commands: list[Command], label: str | None = None) -> None:
        self._commands = list(commands)
        if label is not None:
            self.label = label

    @property
    def commands(self) -> list[Command]:
        return list(self._commands)

    def do(self, schematic: Schematic) -> None:
        """Run the children in order. If a child raises, the already-executed
        children are unwound (undone in reverse order) before the exception
        propagates, so a failed macro never leaves a half-applied document."""
        done: list[Command] = []
        try:
            for cmd in self._commands:
                cmd.do(schematic)
                done.append(cmd)
        except BaseException:
            for cmd in reversed(done):
                cmd.undo(schematic)
            raise

    def undo(self, schematic: Schematic) -> None:
        for cmd in reversed(self._commands):
            cmd.undo(schematic)


# ---------------------------------------------------------------------------
# Undo stack
# ---------------------------------------------------------------------------

class UndoStack:
    """A bounded-history undo/redo stack bound to one :class:`Schematic`.

    The stack owns the document it mutates. :meth:`push` applies a command and
    records it; :meth:`undo` / :meth:`redo` walk the history. Pushing a new
    command after one or more undos discards the redo tail, matching standard
    editor semantics. The stack is per-session and is never serialized (spec
    §6.6).
    """

    def __init__(self, schematic: Schematic) -> None:
        self._schematic = schematic
        self._undo: list[Command] = []
        self._redo: list[Command] = []
        # Undo-list length at the last save (mark_save_point); None when the
        # saved state has become unreachable (divergent edits truncated the
        # redo tail that contained it). A fresh stack is at its save point.
        self._save_point: int | None = 0

    # -- properties --------------------------------------------------------

    @property
    def schematic(self) -> Schematic:
        return self._schematic

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_count(self) -> int:
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        return len(self._redo)

    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    # -- save-point tracking (consumed by the UI's modified-state logic) ----

    def mark_save_point(self) -> None:
        """Record the current history position as the on-disk (saved) state."""
        self._save_point = len(self._undo)

    def is_modified(self) -> bool:
        """True iff the document differs from the last marked save point.

        After divergent edits (undo past the save point, then a new push) the
        saved state is unreachable, so the document stays modified until the
        next :meth:`mark_save_point`.
        """
        return self._save_point != len(self._undo)

    # -- operations --------------------------------------------------------

    def push(self, command: Command) -> None:
        """Apply *command* to the document and record it for undo.

        Clears the redo history (a new action invalidates redone-away future).
        If ``command.do()`` raises, nothing is recorded — the document was
        either untouched or (for a :class:`MacroCommand`) already unwound — and
        the exception propagates.
        """
        command.do(self._schematic)
        self._record(command)

    def record(self, command: Command) -> None:
        """Record an **already-applied** command for undo without executing it.

        Used by the scene's ``batch()`` flush, which applies each command
        immediately at push time (so later commands in the batch capture fresh
        old values) and records the wrapping macro once at the end. Undo/redo
        of a recorded command behave exactly as if it had been pushed.
        """
        self._record(command)

    def _record(self, command: Command) -> None:
        # A push below the save point truncates the redo tail that contained
        # the saved state — it becomes unreachable.
        if self._save_point is not None and self._save_point > len(self._undo):
            self._save_point = None
        self._undo.append(command)
        self._redo.clear()

    def undo(self) -> Command | None:
        """Undo the most recent command. Returns it, or None if empty."""
        if not self._undo:
            return None
        command = self._undo.pop()
        command.undo(self._schematic)
        self._redo.append(command)
        return command

    def redo(self) -> Command | None:
        """Redo the most recently undone command. Returns it, or None."""
        if not self._redo:
            return None
        command = self._redo.pop()
        command.redo(self._schematic)
        self._undo.append(command)
        return command

    def clear(self) -> None:
        """Drop all undo and redo history (e.g. after New / Open).

        The just-loaded document is the new baseline, so the (empty) history
        position becomes the save point: ``is_modified()`` is False.
        """
        self._undo.clear()
        self._redo.clear()
        self._save_point = 0
