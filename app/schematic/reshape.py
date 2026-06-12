"""
Pure wire-reshape computations shared by the undo commands and drag previews.

Every rule for how connected wires follow a moving/resizing component (and how
a dragged vertex reshapes its wire) lives **exactly once**, in this Qt-free
module:

* :func:`reshape_wire_points` — endpoint shift + auto-elbow (the primitive);
* :func:`move_vertex_points` / :func:`reshape_junction_wire` — vertex drags;
* :func:`compute_move_reshape` — the full component/wire-move rule set
  (sole-lead endpoint test, explicit junction-tap follow, re-stretch leads,
  contained-wire removal);
* :func:`compute_pin_drag_reshape` — wires following a single relocated pin
  (resizable two-terminal endpoint drag);
* :func:`compute_box_resize_reshape` — wires following a rect/circle box
  resize (anchored scale of the connection points).

The ``compute_*`` functions are **pure**: they take explicit inputs (wire
objects exposing ``id``/``points``, pin-key sets, a delta or span pair) and
return a :class:`WireReshapeResult` describing the new geometry plus the
structured side effects (wires to remove, re-stretch leads to create) without
mutating anything. The commands (:mod:`app.canvas.commands`) apply the result
to the model with their undo bookkeeping; the drag previews
(:mod:`app.canvas.drag`) render the *same* result as ghosts and apply nothing
— so the preview can never drift from the committed outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace as _dc_replace
from types import SimpleNamespace
from typing import Sequence

from app.schematic.model import (
    component_connection_points,
    point_key,
    route,
    simplify_points,
    snap_point,
    wire_contained_by_others,
)

__all__ = [
    "WireReshapeResult",
    "reshape_wire_points",
    "move_vertex_points",
    "reshape_junction_wire",
    "compute_move_reshape",
    "compute_pin_drag_reshape",
    "compute_box_resize_reshape",
]

Point = tuple[float, float]


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

def _seg_elbow(moved: Point, neighbour: Point) -> Point | None:
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
    points: list[Point],
    *,
    start_hit: bool,
    end_hit: bool,
    dx: float,
    dy: float,
    simplify: bool = True,
) -> list[Point]:
    """Return the points of a wire after a connected component moves by (dx,dy).

    *start_hit* / *end_hit* say whether the wire's first / last vertex is
    attached to a moving component's pin:

    * both ends attached → rigid translation of the whole polyline;
    * one end attached   → that endpoint shifts, with an auto-elbow inserted on
      the adjacent segment if it would otherwise go diagonal.

    When *simplify* is True the result is run through :func:`simplify_points` to
    drop redundant collinear vertices. This is the single shared implementation
    used by both the committed commands and the live drag previews.
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


def move_vertex_points(
    points: list[Point],
    idx: int,
    new_point: Point,
) -> list[Point]:
    """Move vertex *idx* to *new_point*, inserting horizontal-first elbows on any
    adjacent segment that turned diagonal; returns the simplified point list
    (may be < 2 points if the wire collapsed). Shared by
    :class:`~app.canvas.commands.MoveWireVertexCommand` and the vertex-drag
    preview, so the ghost always matches the committed result."""
    pts = list(points)
    if not (0 <= idx < len(pts)):
        return pts
    pts[idx] = new_point
    rebuilt: list[Point] = []
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
    points: list[Point],
    idx: int,
    new_point: Point,
) -> list[Point]:
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
        return move_vertex_points(pts, idx, new_point)

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


def _point_on_polyline(pt: Point, points: list[Point]) -> bool:
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


# ---------------------------------------------------------------------------
# Whole-reshape computations (pure; commands apply, previews render)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WireReshapeResult:
    """Outcome of a pure reshape computation. Nothing has been mutated; the
    caller applies it (a command) or renders it as ghosts (a drag preview).

    * ``new_points`` — wire id → reshaped polyline, for **every** wire the
      gesture touches (including wires the result then removes, whose entry
      holds their pre-removal geometry).
    * ``collapsed_ids`` — wires whose reshape collapsed them to fewer than two
      points; they must be removed from the schematic.
    * ``contained_ids`` — wires whose reshaped polyline lies entirely on top of
      other wires (redundant); they must be removed from the schematic.
    * ``lead_paths`` — fresh "re-stretch" leads to create (a pin dragged off a
      multi-wire junction stays connected by a new lead from the node to its
      new position). Paths only — the applying command assigns wire ids.
      Leads that would themselves be fully contained are already excluded.
    """

    new_points: dict[str, list[Point]] = field(default_factory=dict)
    collapsed_ids: tuple[str, ...] = ()
    contained_ids: tuple[str, ...] = ()
    lead_paths: tuple[tuple[Point, ...], ...] = ()

    @property
    def removed_ids(self) -> tuple[str, ...]:
        """All wire ids the gesture removes (collapsed first, then contained)."""
        return self.collapsed_ids + self.contained_ids


def compute_move_reshape(
    wires: Sequence,
    *,
    moving_pins: set[Point],
    delta: Point,
    explicit_wire_ids: "frozenset[str] | set[str]" = frozenset(),
    all_dragged: bool = False,
) -> WireReshapeResult:
    """The complete move rule set, computed without mutating anything.

    *wires* is the schematic's wire list (objects exposing ``id`` and
    ``points``); *moving_pins* is the :func:`point_key`'d set of the moving
    components' connection points **before** the move; *delta* the (dx, dy)
    translation; *explicit_wire_ids* the wires selected for rigid translation;
    *all_dragged* True when the whole circuit translates (select-all move).

    Rules (each exists only here — :class:`~app.canvas.commands.MoveCommand`
    applies the result, the component/wire drag previews render it):

    1. **Rigid translate** for explicit wires and for every wire when
       *all_dragged* — free endpoints (open-circle nodes) move with the circuit.
    2. **Sole-lead endpoint test** — a wire endpoint on a moving pin follows
       only when it is the *sole* wire endpoint at that coordinate; on a shared
       junction the net stays put and rule 4 re-stretches the connection.
    3. **Junction-tap follow** — any wire endpoint resting anywhere on an
       explicitly translated wire's original polyline follows by the delta, so
       taps stay connected.
    4. **Re-stretch leads** — a moving pin that sits on a multi-wire junction
       gets a fresh lead from the node to its new position.
    5. **Contained-wire removal** — a touched wire (or fresh lead) whose final
       polyline lies entirely on other wires is removed as redundant.
    """
    dx, dy = delta
    explicit = frozenset(explicit_wire_ids)
    if not moving_pins and not all_dragged and not explicit:
        return WireReshapeResult()

    # How many wire endpoints coincide at each coordinate. A moving pin that
    # sits on a *junction* (≥2 wire endpoints there) must NOT drag those wires:
    # doing so tears the net apart when the component is dragged off the node.
    # Only a pin that is the *sole* wire endpoint at its coordinate — a genuine
    # lead — follows; on a shared junction the pin stays connected by a fresh
    # re-stretch lead (rule 4).
    endpoint_count: dict[Point, int] = {}
    for w in wires:
        if len(w.points) < 2:
            continue
        for end in (point_key(w.points[0]), point_key(w.points[-1])):
            endpoint_count[end] = endpoint_count.get(end, 0) + 1

    # Pre-move geometry of explicitly-translated wires. Any OTHER wire joined at
    # one of their vertices/segments — a junction tap — follows at that shared
    # point when the explicit wire moves rigidly (spec §6.3 "keep connected").
    explicit_orig = [
        list(w.points) for w in wires
        if w.id in explicit and len(w.points) >= 2
    ]

    def _on_explicit(p: Point) -> bool:
        return any(_point_on_polyline(p, poly) for poly in explicit_orig)

    new_points: dict[str, list[Point]] = {}
    collapsed: list[str] = []
    for wire in wires:
        pts = wire.points
        if len(pts) < 2:
            continue
        if all_dragged or wire.id in explicit:
            start_hit = end_hit = True
        else:
            s_key, e_key = point_key(pts[0]), point_key(pts[-1])
            start_hit = (
                (s_key in moving_pins and endpoint_count.get(s_key, 0) == 1)
                or _on_explicit(pts[0])
            )
            end_hit = (
                (e_key in moving_pins and endpoint_count.get(e_key, 0) == 1)
                or _on_explicit(pts[-1])
            )
            if not start_hit and not end_hit:
                continue
        reshaped = reshape_wire_points(
            pts, start_hit=start_hit, end_hit=end_hit, dx=dx, dy=dy
        )
        new_points[wire.id] = reshaped
        if len(reshaped) < 2:
            collapsed.append(wire.id)       # collapsed to a point — remove

    # Re-stretch leads: keep a component connected when it is dragged off a
    # multi-wire junction. No leads when the whole circuit translates rigidly
    # (nothing is left behind to reconnect to) or for a zero-distance move.
    lead_paths: list[list[Point]] = []
    if not all_dragged and (dx != 0.0 or dy != 0.0):
        for p in sorted(moving_pins):
            if endpoint_count.get(p, 0) < 2:
                continue  # free pin or a single lead that already followed
            path = route(p, (p[0] + dx, p[1] + dy))
            if len(path) >= 2 and len(set(path)) >= 2:
                lead_paths.append(path)

    # Contained-wire removal: drop any *touched* wire (or fresh lead) whose
    # polyline now lies entirely on top of other wires (e.g. a lead dragged
    # collinearly onto the rail it connects to). Untouched redundant wires
    # elsewhere are left alone. Removals cascade in list order against the
    # not-yet-removed remainder, matching the committed application order.
    collapsed_set = set(collapsed)
    effective: list[tuple[object, list[Point]]] = []
    for wire in wires:
        if wire.id in collapsed_set or len(wire.points) < 2:
            continue
        effective.append((wire.id, new_points.get(wire.id, wire.points)))
    for i, path in enumerate(lead_paths):
        effective.append((("__lead__", i), path))
    candidates: set[object] = set(new_points) | {
        ("__lead__", i) for i in range(len(lead_paths))
    }
    removed: set[object] = set()
    contained: list[str] = []
    removed_leads: set[int] = set()
    for key, pts in effective:
        if key not in candidates or len(pts) < 2:
            continue
        others = [
            SimpleNamespace(points=op)
            for ok, op in effective
            if ok != key and ok not in removed
        ]
        if wire_contained_by_others(pts, others):
            removed.add(key)
            if isinstance(key, str):
                contained.append(key)
            else:
                removed_leads.add(key[1])

    return WireReshapeResult(
        new_points=new_points,
        collapsed_ids=tuple(collapsed),
        contained_ids=tuple(contained),
        lead_paths=tuple(
            tuple(path)
            for i, path in enumerate(lead_paths)
            if i not in removed_leads
        ),
    )


def compute_pin_drag_reshape(
    wires: Sequence,
    *,
    old_pin: Point,
    dx: float,
    dy: float,
) -> WireReshapeResult:
    """Wires following one relocated pin (a resizable two-terminal component's
    dragged terminal): every wire whose endpoint sits on *old_pin* has that
    endpoint shifted by (dx, dy) with an auto-elbow. Shared by
    :class:`~app.canvas.commands.ResizeCommand` and the endpoint-drag preview."""
    pin_key = point_key(old_pin)
    new_points: dict[str, list[Point]] = {}
    collapsed: list[str] = []
    for wire in wires:
        pts = wire.points
        if len(pts) < 2:
            continue
        start_hit = point_key(pts[0]) == pin_key
        end_hit = point_key(pts[-1]) == pin_key
        if not start_hit and not end_hit:
            continue
        reshaped = reshape_wire_points(
            pts, start_hit=start_hit, end_hit=end_hit, dx=dx, dy=dy
        )
        new_points[wire.id] = reshaped
        if len(reshaped) < 2:
            collapsed.append(wire.id)
    return WireReshapeResult(new_points=new_points, collapsed_ids=tuple(collapsed))


def compute_box_resize_reshape(
    component,
    *,
    old_span: Point,
    new_span: Point,
    wires: Sequence,
) -> WireReshapeResult:
    """Wires following a box annotation (rect/circle) resize.

    The resize is an anchored scale about the box's fixed corner
    (``component.position``): a connection point P maps to
    ``position + (P - position) * (new_span / old_span)``, snapped to the
    0.25 GU grid, so each connection point stays on its corresponding new
    edge. Points on the two edges through the anchored corner map to
    themselves; the opposite edges translate; mid-edge points scale
    proportionally. The connection-point set is the kind's own
    (:func:`component_connection_points`): a rect's full perimeter, a circle's
    four cardinal points. Shared by :class:`~app.canvas.commands.ResizeCommand`
    and the box-resize drag preview."""
    x0, y0 = component.position
    odx, ody = old_span
    ndx, ndy = new_span

    # Connection points under the OLD span (the live component may already
    # carry the new span, or a preview copy — use a replace()'d stand-in).
    old_perim = {
        point_key(p)
        for p in component_connection_points(
            _dc_replace(component, span_override=old_span)
        )
    }

    def _map(p: Point) -> Point:
        px, py = p
        fx = (px - x0) / odx if odx else 0.0
        fy = (py - y0) / ody if ody else 0.0
        return snap_point((x0 + fx * ndx, y0 + fy * ndy))

    new_points: dict[str, list[Point]] = {}
    collapsed: list[str] = []
    for wire in wires:
        pts = wire.points
        if len(pts) < 2:
            continue
        start_hit = point_key(pts[0]) in old_perim
        end_hit = point_key(pts[-1]) in old_perim
        start_tgt = _map(pts[0]) if start_hit else None
        end_tgt = _map(pts[-1]) if end_hit else None
        start_moves = (
            start_tgt is not None and point_key(start_tgt) != point_key(pts[0])
        )
        end_moves = (
            end_tgt is not None and point_key(end_tgt) != point_key(pts[-1])
        )
        if not start_moves and not end_moves:
            continue
        reshaped = list(pts)
        if start_moves:
            reshaped = reshape_wire_points(
                reshaped, start_hit=True, end_hit=False,
                dx=start_tgt[0] - reshaped[0][0],
                dy=start_tgt[1] - reshaped[0][1],
            )
        if end_moves:
            reshaped = reshape_wire_points(
                reshaped, start_hit=False, end_hit=True,
                dx=end_tgt[0] - reshaped[-1][0],
                dy=end_tgt[1] - reshaped[-1][1],
            )
        new_points[wire.id] = reshaped
        if len(reshaped) < 2:
            collapsed.append(wire.id)
    return WireReshapeResult(new_points=new_points, collapsed_ids=tuple(collapsed))
