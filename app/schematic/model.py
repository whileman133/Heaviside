"""
Schematic data model — wires, schematic root, and geometry helpers.

The per-instance Component classes live in app.components.model alongside
ComponentDef; they are re-exported here for backwards-compatible imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.components.model import (  # re-export for backwards compat
    Component,
    DiodeComponent,
    DrawingComponent,
    RectComponent,
    TextNodeComponent,
)

__all__ = [
    "Component",
    "DiodeComponent",
    "DrawingComponent",
    "RectComponent",
    "TextNodeComponent",
    "Wire",
    "Schematic",
]


@dataclass
class Wire:
    """A Manhattan-routed polyline connecting two points on the canvas."""

    id: str
    """UUID. Must be unique within a Schematic."""

    points: list[tuple[float, float]]
    """Ordered list of vertices forming the path.

    Constraints (enforced by validate()):
    - All vertices lie on 0.5 GU boundaries.
    - Every consecutive pair of segments is strictly horizontal or vertical
      (Manhattan constraint — no diagonals).
    - At least two points (a single segment).
    """


@dataclass
class Schematic:
    """Root document object — the complete logical description of a circuit."""

    version: str
    """Spec version this schematic was created under, e.g. '0.1'."""

    name: str
    """User-visible schematic name."""

    components: list[Component] = field(default_factory=list)
    wires: list[Wire] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def simplify_points(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Reduce a polyline to the fewest vertices that draw the same path.

    Two reductions are applied:

    * **Duplicate removal** — consecutive identical points are merged.
    * **Collinear collapse** — for any three consecutive points that are
      collinear (all share the same x, or all share the same y), the middle
      point is dropped. This removes the redundant intermediate vertices that
      accumulate when a wire endpoint is dragged straight along an existing
      segment, so ``(0,0) -- (2,0) -- (5,0)`` collapses to ``(0,0) -- (5,0)``.

    The first and last points are always preserved. Diagonal corners (a genuine
    Manhattan elbow, where the three points are not collinear) are kept. A path
    of two or fewer points is returned with duplicates removed but otherwise
    untouched. Pure function: the input list is not modified.
    """
    if len(points) < 2:
        return list(points)

    # 1) drop consecutive duplicates
    dedup: list[tuple[float, float]] = [points[0]]
    for p in points[1:]:
        if p != dedup[-1]:
            dedup.append(p)

    if len(dedup) < 3:
        return dedup

    # 2) collapse collinear runs (axis-aligned middle points)
    out: list[tuple[float, float]] = [dedup[0]]
    for prev, cur, nxt in zip(dedup, dedup[1:], dedup[2:]):
        same_x = prev[0] == cur[0] == nxt[0]
        same_y = prev[1] == cur[1] == nxt[1]
        if same_x or same_y:
            # cur lies on the straight run prev→nxt; drop it.
            continue
        out.append(cur)
    out.append(dedup[-1])

    # 3) dedup again: collapsing a collinear middle point whose neighbours were
    # non-adjacent duplicates (e.g. A–B–A → A–A after B is dropped) can
    # introduce new consecutive duplicates that the first dedup pass missed.
    result: list[tuple[float, float]] = [out[0]]
    for p in out[1:]:
        if p != result[-1]:
            result.append(p)
    return result


def route(
    a: tuple[float, float],
    b: tuple[float, float],
    vfirst: bool | None = None,
) -> list[tuple[float, float]]:
    """Two-segment Manhattan path from *a* to *b* (spec §6.4 "routing primitive").

    Returns ``[a, b]`` when the points already share an x or y coordinate,
    otherwise ``[a, corner, b]`` with a single auto-corner.

    *vfirst* selects the corner orientation:

    * ``False`` → horizontal-first: corner at ``(b.x, a.y)``.
    * ``True``  → vertical-first:   corner at ``(a.x, b.y)``.
    * ``None``  → **dominant axis**: travel the longer leg first
      (horizontal-first when ``|dx| >= |dy|``, else vertical-first). This is the
      default used while drawing; there is no modifier key to flip it — the user
      steers the route by dropping intermediate vertices.

    This is the single routing primitive shared by the drawing preview, the
    drawing commit, vertex dragging, and component-follow elbows. Callers that
    need a specific orientation pass *vfirst* explicitly; nobody re-implements
    the corner math. The intermediate corner (0 or 1 points) is ``route(...)[1:-1]``.

    Pure function: inputs are not modified.
    """
    ax, ay = a
    bx, by = b
    if ax == bx or ay == by:
        return [a, b]
    if vfirst is None:
        vfirst = abs(by - ay) > abs(bx - ax)
    corner = (ax, by) if vfirst else (bx, ay)
    return [a, corner, b]


def component_pin_positions(component: "Component") -> list[tuple[float, float]]:
    """Absolute (mirror-then-rotate) pin coordinates of *component*, in GU.

    Pin offsets live in the registry relative to the component origin; this
    applies the same mirror-then-clockwise-rotate transform the canvas and code
    generator use, so connectivity tests operate in schematic coordinates.
    Returns an empty list for an unknown kind.
    """
    # Lazy import avoids a cycle during package construction.
    from app.components.registry import REGISTRY

    defn = REGISTRY.get(component.kind)
    if defn is None:
        return []

    ox, oy = component.position
    out: list[tuple[float, float]] = []
    for i, pin in enumerate(defn.pins):
        dx, dy = pin.offset
        # For resizable two-terminal components, the terminal pin (index 1)
        # uses span_override when set instead of the registry default offset.
        if (
            i == 1
            and defn.resizable
            and component.span_override is not None
        ):
            dx, dy = component.span_override
        if component.mirror:
            dx = -dx
        r = component.rotation % 360
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


def junction_points(schematic: "Schematic") -> set[tuple[float, float]]:
    """Coordinates that need a solid connection dot.

    Uses the *degree* of each coordinate — the number of wire segment-ends that
    meet there — which is the topologically correct test and handles T-splits:

    * Each wire contributes **1** to a coordinate's degree if the point is one
      of that wire's endpoints, or **2** if it's an interior vertex (a segment
      arrives and another departs — a pass-through or corner).
    * A **component pin** at the coordinate adds **1** (a terminal).

    A dot is drawn when the total degree is **3 or more**: a T-junction
    (interior vertex of one wire, degree 2, plus another wire's endpoint,
    degree 1 → 3), a 4-way cross (4), or a pin meeting a pass-through wire
    (1 + 2 → 3). A straight pass-through (2), a lone corner (2), a pin with a
    single wire end (1 + 1 → 2), and two wires merely meeting end-to-end (2)
    get no dot. (In this model coincident wires are electrically joined; there
    is no non-connecting "hop" crossing.)

    Pure function; returns a set of (x, y) tuples in grid units.
    """
    degree: dict[tuple[float, float], int] = {}

    def add(pt: tuple[float, float], d: int) -> None:
        degree[pt] = degree.get(pt, 0) + d

    for wire in schematic.wires:
        pts = wire.points
        if len(pts) < 2:
            continue
        # Count how many segment-ends of THIS wire touch each coordinate:
        # endpoints contribute 1, interior vertices contribute 2. Guard against
        # a wire that revisits a coordinate by summing its own incidences.
        own: dict[tuple[float, float], int] = {}
        n = len(pts)
        for i, pt in enumerate(pts):
            pt = tuple(pt)
            own[pt] = own.get(pt, 0) + (1 if (i == 0 or i == n - 1) else 2)
        for pt, d in own.items():
            add(pt, d)

    for comp in schematic.components:
        for p in component_pin_positions(comp):
            add(p, 1)

    return {pt for pt, d in degree.items() if d >= 3}


def open_endpoints(schematic: "Schematic") -> set[tuple[float, float]]:
    """Wire endpoints that do not connect to any component pin.

    An open endpoint is a wire's first or last point that lies at a position
    not occupied by any component pin. These are rendered as open circles
    (\\node[ocirc]) to indicate unconnected terminals.

    Interior wire vertices are never open endpoints — only the first and last
    point of each wire are candidates.

    Pure function; returns a set of (x, y) tuples in grid units.
    """
    # Collect all component pin positions.
    pin_positions: set[tuple[float, float]] = set()
    for comp in schematic.components:
        for p in component_pin_positions(comp):
            pin_positions.add((round(p[0], 6), round(p[1], 6)))

    # Collect all wire vertex positions (every point on every wire).
    # A wire endpoint that coincides with ANY vertex of ANY other wire is
    # connected — it should not be shown as an open endpoint.
    all_wire_points: dict[tuple[float, float], int] = {}
    for wire in schematic.wires:
        for pt in wire.points:
            pt_r = (round(pt[0], 6), round(pt[1], 6))
            all_wire_points[pt_r] = all_wire_points.get(pt_r, 0) + 1

    candidates: set[tuple[float, float]] = set()
    for wire in schematic.wires:
        pts = wire.points
        if len(pts) < 2:
            continue
        for pt in (pts[0], pts[-1]):
            pt_r = (round(pt[0], 6), round(pt[1], 6))
            if pt_r in pin_positions:
                continue
            # Connected to another wire if this point appears in more than one
            # wire's point list, or appears as an interior vertex of any wire.
            if all_wire_points.get(pt_r, 0) > 1:
                continue
            candidates.add(pt_r)

    return candidates


def unconnected_pins(schematic: "Schematic") -> set[tuple[float, float]]:
    """Component pin positions that nothing connects to.

    A pin is *unconnected* when no wire vertex (endpoint or interior) lies at
    its coordinate and no other component pin shares that exact coordinate.
    These can be marked with open circles (``\\node[ocirc]``) to flag dangling
    terminals — the counterpart of :func:`open_endpoints`, which flags dangling
    *wire* ends. The two sets are disjoint by construction: open endpoints are
    wire ends *not* at a pin, while these are pins with *no* wire.

    Pure function; returns a set of (x, y) tuples in grid units.
    """
    # Every wire vertex coordinate (endpoints and interior points alike).
    wire_points: set[tuple[float, float]] = set()
    for wire in schematic.wires:
        for pt in wire.points:
            wire_points.add((round(pt[0], 6), round(pt[1], 6)))

    # Count component pins per coordinate so two abutting pins are not flagged.
    pin_count: dict[tuple[float, float], int] = {}
    for comp in schematic.components:
        for p in component_pin_positions(comp):
            key = (round(p[0], 6), round(p[1], 6))
            pin_count[key] = pin_count.get(key, 0) + 1

    return {
        coord
        for coord, count in pin_count.items()
        if count == 1 and coord not in wire_points
    }


def _point_strictly_on_segment(
    pt: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    """True if *pt* lies on segment a–b but is not an endpoint.

    Segments are axis-aligned (Manhattan). Endpoints are excluded so a point
    that already coincides with a vertex is never treated as a split site.
    """
    if pt == a or pt == b:
        return False
    px, py = pt
    ax, ay = a
    bx, by = b
    if ax == bx:                                  # vertical
        return px == ax and min(ay, by) < py < max(ay, by)
    if ay == by:                                  # horizontal
        return py == ay and min(ax, bx) < px < max(ax, bx)
    return False                                  # diagonal — shouldn't occur


def wire_splits_at(
    schematic: "Schematic", point: tuple[float, float]
) -> list[tuple[str, int]]:
    """Find wires whose interior passes through *point* (a split site).

    Returns a list of ``(wire_id, insert_index)`` where *insert_index* is the
    position at which a vertex equal to *point* should be inserted into that
    wire's ``points`` list (i.e. just after the segment's start vertex). A wire
    that already has a vertex exactly at *point* is **not** returned — there is
    nothing to split there.
    """
    out: list[tuple[str, int]] = []
    for wire in schematic.wires:
        pts = wire.points
        if point in pts:
            continue
        for i in range(len(pts) - 1):
            if _point_strictly_on_segment(point, pts[i], pts[i + 1]):
                out.append((wire.id, i + 1))
                break       # at most one split per wire per point
    return out


def wire_corner_splits_at(
    schematic: "Schematic", point: tuple[float, float]
) -> list[tuple[str, int]]:
    """Find wires that have *point* as an intermediate (corner) vertex.

    Returns ``(wire_id, vertex_index)`` for each wire whose point list
    contains *point* at an interior position (not the first or last vertex).
    Used to split L-shaped wires at their elbow when a new wire connects to
    that corner.
    """
    out: list[tuple[str, int]] = []
    for wire in schematic.wires:
        pts = wire.points
        for i in range(1, len(pts) - 1):   # skip endpoints
            if pts[i] == point:
                out.append((wire.id, i))
                break   # at most one interior match per wire
    return out
