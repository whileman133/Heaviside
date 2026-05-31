"""
Schematic data model — per-instance state.

These dataclasses represent the live document: placed components, wires,
and top-level metadata. They are mutable (unlike the frozen ComponentDef /
PinDef in app/components/model.py).

The UI layer holds no schematic state independently; all display is derived
from these objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Component:
    """One placed instance of a component type on the canvas."""

    id: str
    """UUID assigned at placement. Must be unique within a Schematic."""

    kind: str
    """CircuiTikZ keyword; must exist as a key in REGISTRY."""

    position: tuple[float, float]
    """(x, y) of the origin pin in schematic grid coordinates."""

    rotation: int
    """Clockwise rotation in degrees. Must be one of {0, 90, 180, 270}."""

    labels: dict[str, str]
    """label slot name → LaTeX string, e.g. {"l": "$R_1$"}."""

    mirror: bool = False
    """Horizontal mirror applied before rotation."""


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
    for pin in defn.pins:
        dx, dy = pin.offset
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

    candidates: set[tuple[float, float]] = set()
    for wire in schematic.wires:
        pts = wire.points
        if len(pts) < 2:
            continue
        for pt in (pts[0], pts[-1]):
            pt_r = (round(pt[0], 6), round(pt[1], 6))
            if pt_r not in pin_positions:
                candidates.add(pt_r)

    return candidates


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
