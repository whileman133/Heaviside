"""
Schematic data model — wires, schematic root, and geometry helpers.

The per-instance Component classes live in app.components.model alongside
ComponentDef; they are re-exported here for backwards-compatible imports.
"""

from __future__ import annotations

import math
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
    - All vertices lie on 0.25 GU boundaries.
    - Every consecutive pair of segments is strictly horizontal or vertical
      (Manhattan constraint — no diagonals).
    - At least two points (a single segment).
    """

    line_style: str = ""
    """Raw TikZ line-style tokens, e.g. ``"dashed"``; ``""`` = solid."""

    line_width: float = 0.4
    """Line width in pt (TikZ default 0.4). Drawn proportionally on the canvas."""

    no_junction_dots: bool = False
    """When True, this wire does not contribute to junction-dot placement.

    Useful when a wire is an annotation (e.g. a lead into a voltage annotation)
    rather than a real electrical connection: it suppresses the solid ``circ``
    dots that would otherwise appear where it meets other wires/pins. Other
    wires/pins at the same coordinate still count, so a dot they independently
    justify is unaffected (see :func:`junction_points`)."""

    no_termination_dots: bool = False
    """When True, this wire's dangling ends do not get open-circle terminals.

    Suppresses the ``ocirc`` markers (see :func:`open_endpoints`) at the wire's
    own unconnected endpoints — useful for annotation leads that should simply
    stop without a visible terminal. Does not affect other wires' endpoints."""

    hop_mode: str = ""
    """Per-wire line-hop override (see :func:`wire_crossings`). One of:

    * ``""`` (**default**) — follow the global line-hops preference and the
      ``z_order`` priority: this wire hops over a crossing wire only when hops
      are globally enabled and it outranks the other wire.
    * ``"never"`` — this wire **never** draws a hop bump, but a crossing wire may
      still hop *over* it (it stays a valid thing to be hopped over).
    * ``"always"`` — this wire **always** hops at its crossings, regardless of the
      global preference and of ``z_order`` (it outranks default-mode wires).

    Independent of the dot-suppression flags. See :data:`WIRE_HOP_MODES`."""

    start_marker: str = ""
    """Custom endpoint decoration drawn at the wire's first point (``points[0]``).

    ``""`` = no custom marker (default). Non-empty kinds (see
    :data:`WIRE_MARKER_KINDS`) draw a tip pointing outward along the first
    segment — ``"arrow"``/``"stealth"``/``"open"`` arrowheads or a ``"bar"``
    terminal — used mainly to draw block diagrams.

    A custom marker is independent of the automatic ``circ``/``ocirc`` dots: it
    is the user's explicit choice, not topology-derived. When a marker is set at
    an end, that end does not also receive an automatic open-circle terminal
    (see :func:`open_endpoints`) — the marker replaces it."""

    end_marker: str = ""
    """Custom endpoint decoration drawn at the wire's last point (``points[-1]``).

    Same semantics as :attr:`start_marker`, applied to the wire's final point
    and oriented along the last segment."""

    start_label: str = ""
    """Text/math label placed just beyond the wire's first point (``points[0]``).

    ``""`` = no label. The string is a raw LaTeX fragment (same convention as a
    text annotation's content), so ``"$y(t)$"`` typesets as math and plain text
    renders verbatim. The label sits on the far side of the endpoint along the
    first segment, so an arrow marker reads as terminating *into* the text. Used
    mainly to caption block-diagram signal lines."""

    end_label: str = ""
    """Text/math label placed just beyond the wire's last point (``points[-1]``).

    Same semantics as :attr:`start_label`, applied to the wire's final point and
    oriented along the last segment."""

    mid_label: str = ""
    """Text/math label drawn *over* the wire (with a solid background).

    ``""`` = no label. A raw LaTeX fragment (``$…$`` typesets as math, plain text
    renders verbatim), centred on the wire at the fractional arc-length position
    :attr:`mid_label_pos`. Drawn with an opaque backdrop so the line does not run
    through the text. Used to caption a signal/bus mid-run; draggable along the
    wire on the canvas."""

    mid_label_pos: float = 0.5
    """Fractional arc-length position of :attr:`mid_label` along the wire.

    ``0.0`` = the first point, ``1.0`` = the last point, ``0.5`` (default) = the
    midpoint by path length. Clamped to ``[0, 1]``."""

    start_label_placement: str = ""
    """Where :attr:`start_label` sits relative to the wire's first point:

    ``""`` (default) = *off the end*, beyond the point along the terminal segment
    (so an arrow reads as terminating into the text). ``"above"`` / ``"below"``
    instead tuck the label **beside the wire at the endpoint**, extending *inward*
    (back along the terminal segment) so it never crosses the endpoint into a
    connected rect/circle, offset to one side so it never overlaps the wire. The
    side depends on the terminal segment's orientation: for a **horizontal**
    segment ``"above"``/``"below"`` are literal (above/below the wire); for a
    **vertical** segment they read as **left**/**right** of the wire
    (``"above"`` → left, ``"below"`` → right). Useful for labelling wires that
    meet a block-diagram shape, where the off-end direction points into the
    shape."""

    end_label_placement: str = ""
    """Where :attr:`end_label` sits relative to the wire's last point; same values
    and semantics as :attr:`start_label_placement`."""

    z_order: int = 0
    """Canvas/code-generation layer, and hop priority at a crossing.

    Same layering semantics as :attr:`DrawingComponent.z_order`: positive draws
    in front, negative behind, 0 the default. On the canvas it maps to the wire
    item's ``setZValue``; in the LaTeX output a wire with ``z_order < 0`` is
    emitted before the main ``\\draw`` block and ``z_order > 0`` after it.

    It also decides **which wire hops** where two wires cross without connecting
    (see :func:`wire_crossings`): the wire with the higher ``z_order`` arcs over
    the other (ties broken by position in the schematic's wire list)."""


#: Endpoint marker kinds in cycle order (``""`` = none first). Each non-empty
#: kind maps to a TikZ ``arrows.meta`` tip in code generation and to a canvas
#: glyph: ``"arrow"`` = filled ``Latex`` tip, ``"stealth"`` = sharp ``Stealth``
#: tip, ``"open"`` = outlined ``Latex[open]`` tip, ``"bar"`` = ``Bar`` terminal.
#: The order is also the Tab-cycle order on the canvas (§6.4).
WIRE_MARKER_CYCLE: tuple[str, ...] = ("", "arrow", "stealth", "open", "bar")

#: Valid values for :attr:`Wire.start_marker` / :attr:`Wire.end_marker` — the
#: cycle as an unordered set for membership checks.
WIRE_MARKER_KINDS: frozenset[str] = frozenset(WIRE_MARKER_CYCLE)

#: Wire ``line_style`` tokens in cycle order (``""`` = solid first), as raw TikZ
#: tokens. The order is the Tab-cycle order over a wire body (§6.4).
WIRE_LINE_STYLE_CYCLE: tuple[str, ...] = ("", "dashed", "dotted", "dash dot")

#: Valid values for the endpoint-label placement fields
#: (:attr:`Wire.start_label_placement` / :attr:`Wire.end_label_placement`):
#: ``""`` = off the end (along the terminal segment), ``"above"``, ``"below"``.
WIRE_LABEL_PLACEMENTS: tuple[str, ...] = ("", "above", "below")

#: Radius (in grid units) of the semicircular bump drawn where one wire hops
#: over another (see :func:`wire_crossings`). Single source of truth shared by
#: the canvas (``HOP_RADIUS_GU * GRID_PX`` pixels) and the LaTeX generator (GU
#: directly), so the rendered arc matches the exported one exactly.
HOP_RADIUS_GU: float = 0.08

#: Valid values for :attr:`Wire.hop_mode` — the per-wire line-hop override, also
#: the tri-state cycle order shown in the inspector (default → never → always).
WIRE_HOP_MODES: tuple[str, ...] = ("", "never", "always")


@dataclass(frozen=True)
class WireHop:
    """One line-hop: where the *hopping* wire arcs over a crossing wire.

    A pure-geometry, derived decoration (never stored) produced by
    :func:`wire_crossings`. ``point`` is the crossing coordinate (GU);
    ``wire_id`` is the wire that hops (arcs over); ``orientation`` is that
    wire's crossed-segment direction (``"h"`` or ``"v"``) and ``seg_index`` the
    index of that segment's start vertex in the wire's ``points`` list. The bump
    bulges perpendicular to ``orientation``.
    """

    point: tuple[float, float]
    wire_id: str
    orientation: str
    seg_index: int


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

def _segment_lengths(points: list[tuple[float, float]]) -> list[float]:
    return [
        math.hypot(points[i + 1][0] - points[i][0], points[i + 1][1] - points[i][1])
        for i in range(len(points) - 1)
    ]


def wire_point_at_fraction(
    points: list[tuple[float, float]], frac: float
) -> tuple[float, float]:
    """Point at fractional arc-length *frac* (0..1) along the polyline *points*.

    ``0`` → first point, ``1`` → last point, ``0.5`` → the midpoint by path
    length. *frac* is clamped to ``[0, 1]``. A degenerate or zero-length wire
    returns its first point. Pure function (grid units in, grid units out).
    """
    if not points:
        return (0.0, 0.0)
    if len(points) < 2:
        return points[0]
    frac = max(0.0, min(1.0, frac))
    segs = _segment_lengths(points)
    total = sum(segs)
    if total == 0.0:
        return points[0]
    target = frac * total
    acc = 0.0
    for i, length in enumerate(segs):
        if length > 0.0 and acc + length >= target:
            t = (target - acc) / length
            return (
                points[i][0] + t * (points[i + 1][0] - points[i][0]),
                points[i][1] + t * (points[i + 1][1] - points[i][1]),
            )
        acc += length
    return points[-1]


def wire_fraction_at_point(
    points: list[tuple[float, float]], pt: tuple[float, float]
) -> float:
    """Fractional arc-length (0..1) of the point on *points* nearest *pt*.

    Projects *pt* onto each segment, picks the closest projection, and returns
    its cumulative arc-length as a fraction of the total. Inverse of
    :func:`wire_point_at_fraction` for points that lie on the polyline. Pure
    function; returns ``0.0`` for a degenerate or zero-length wire.
    """
    if len(points) < 2:
        return 0.0
    segs = _segment_lengths(points)
    total = sum(segs)
    if total == 0.0:
        return 0.0
    px, py = pt
    best_d2 = math.inf
    best_frac = 0.0
    acc = 0.0
    for i, length in enumerate(segs):
        ax, ay = points[i]
        bx, by = points[i + 1]
        if length == 0.0:
            qx, qy, along = ax, ay, 0.0
        else:
            t = ((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / (length * length)
            t = max(0.0, min(1.0, t))
            qx, qy, along = ax + t * (bx - ax), ay + t * (by - ay), t * length
        d2 = (px - qx) ** 2 + (py - qy) ** 2
        if d2 < best_d2:
            best_d2 = d2
            best_frac = (acc + along) / total
        acc += length
    return best_frac


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


# Spacing of connection points along a rectangle's edges, in GU.  Matches the
# minor grid (SNAP_GU = 0.25 in app/canvas/geometry.py); duplicated here to keep
# the schematic layer free of a dependency on the canvas layer.
_RECT_EDGE_SPACING: float = 0.25


def rect_perimeter_points(component: "Component") -> set[tuple[float, float]]:
    """All 0.25-GU grid points along the edges of a ``rect`` component, in GU.

    A block-diagram rectangle accepts a wire connection at *any* grid point on
    its perimeter; this enumerates those points (corners included) so the
    coincident-coordinate connectivity machinery can treat them as connection
    targets.  Returns an empty set for non-rect kinds.  The box is taken to be
    axis-aligned (rects are not rotatable), spanning ``position`` to
    ``position + (span_override or default_span)``.
    """
    if component.kind != "rect":
        return set()
    from app.components.registry import REGISTRY

    defn = REGISTRY.get(component.kind)
    if defn is None:
        return set()

    x0, y0 = component.position
    dx, dy = component.span_override or defn.default_span
    xmin, xmax = (x0, x0 + dx) if dx >= 0 else (x0 + dx, x0)
    ymin, ymax = (y0, y0 + dy) if dy >= 0 else (y0 + dy, y0)

    step = _RECT_EDGE_SPACING
    nx = max(1, round((xmax - xmin) / step))
    ny = max(1, round((ymax - ymin) / step))
    xs = [xmin + i * step for i in range(nx + 1)]
    ys = [ymin + j * step for j in range(ny + 1)]

    pts: set[tuple[float, float]] = set()
    for x in xs:
        pts.add((x, ymin))
        pts.add((x, ymax))
    for y in ys:
        pts.add((xmin, y))
        pts.add((xmax, y))
    return pts


def circle_connection_points(component: "Component") -> set[tuple[float, float]]:
    """The four cardinal connection points (N/S/E/W) of a ``circle`` component.

    These are the midpoints of the bounding-box edges — the axis endpoints of the
    circle/ellipse inscribed in the box defined by ``position`` and
    ``span_override or default_span``.  Returns an empty set for other kinds.
    Each point lies on the 0.25 GU grid (span commits on the 0.5 grid, so half-
    spans are 0.25-aligned).
    """
    if component.kind != "circle":
        return set()
    from app.components.registry import REGISTRY

    defn = REGISTRY.get(component.kind)
    if defn is None:
        return set()

    x0, y0 = component.position
    dx, dy = component.span_override or defn.default_span
    xmin, xmax = (x0, x0 + dx) if dx >= 0 else (x0 + dx, x0)
    ymin, ymax = (y0, y0 + dy) if dy >= 0 else (y0 + dy, y0)
    cx, cy = (xmin + xmax) / 2.0, (ymin + ymax) / 2.0
    return {
        (cx, ymin),  # N
        (cx, ymax),  # S
        (xmax, cy),  # E
        (xmin, cy),  # W
    }


def component_connection_points(component: "Component") -> set[tuple[float, float]]:
    """Coordinates where a wire endpoint connects to *component* (and follows it).

    For a ``rect`` this is every grid point on its perimeter
    (:func:`rect_perimeter_points`); for a ``circle`` it is the four cardinal
    points (:func:`circle_connection_points`); for every other kind it is the set
    of named pin coordinates (:func:`component_pin_positions`).  This is the
    single source of truth for connectivity and component-follow behaviour —
    distinct from :func:`component_pin_positions`, which stays named-pins-only for
    the resize terminal pin, junction detection, unconnected-pin detection, and
    pin dots.
    """
    if component.kind == "rect":
        return rect_perimeter_points(component)
    if component.kind == "circle":
        return circle_connection_points(component)
    return set(component_pin_positions(component))


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
        # Annotation wires opt out of junction-dot placement entirely.
        if wire.no_junction_dots:
            continue
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


# Kinds whose pins do not form an electrical connection. The voltage
# annotation (``open``) renders as a CircuiTikZ ``to[open]`` — an open circuit
# that draws nothing but a voltage label — so anything that merely abuts a
# voltage annotation pin is still electrically open. Such pins neither connect a
# wire endpoint (``open_endpoints``) nor a component pin (``unconnected_pins``),
# and are never themselves flagged. (The current annotation ``short`` is a real
# closed wire and is intentionally NOT listed here.)
NON_CONNECTING_KINDS: frozenset[str] = frozenset({"open"})


def open_endpoints(schematic: "Schematic") -> set[tuple[float, float]]:
    """Wire endpoints that do not connect to any component pin.

    An open endpoint is a wire's first or last point that lies at a position
    not occupied by any connecting component pin. These are rendered as open
    circles (\\node[ocirc]) to indicate unconnected terminals.

    Interior wire vertices are never open endpoints — only the first and last
    point of each wire are candidates.  Pins of :data:`NON_CONNECTING_KINDS`
    (the ``open`` voltage annotation) do not count as a connection, so a wire
    end that only touches one stays open.

    Pure function; returns a set of (x, y) tuples in grid units.
    """
    # Collect connecting component pin positions (voltage annotations excluded).
    pin_positions: set[tuple[float, float]] = set()
    for comp in schematic.components:
        if comp.kind in NON_CONNECTING_KINDS:
            continue
        for p in component_connection_points(comp):
            pin_positions.add((round(p[0], 6), round(p[1], 6)))

    # Collect all wire vertex positions (every point on every wire).
    # A wire endpoint that coincides with ANY vertex of ANY other wire is
    # connected — it should not be shown as an open endpoint.  Degenerate wires
    # (fewer than two points) have no segments and connect nothing, so they are
    # skipped — otherwise a stray single-point wire would make a real endpoint
    # at the same coordinate look connected.
    all_wire_points: dict[tuple[float, float], int] = {}
    for wire in schematic.wires:
        if len(wire.points) < 2:
            continue
        for pt in wire.points:
            pt_r = (round(pt[0], 6), round(pt[1], 6))
            all_wire_points[pt_r] = all_wire_points.get(pt_r, 0) + 1

    candidates: set[tuple[float, float]] = set()
    for wire in schematic.wires:
        # Annotation wires opt out of terminal (ocirc) markers on their own ends.
        # They still count toward all_wire_points above, so other wires ending
        # on this wire remain connected.
        if wire.no_termination_dots:
            continue
        pts = wire.points
        if len(pts) < 2:
            continue
        # A custom endpoint marker (e.g. an arrow) replaces the automatic
        # open-circle terminal at that specific end, so skip it here.
        for pt, marker in ((pts[0], wire.start_marker), (pts[-1], wire.end_marker)):
            if marker:
                continue
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
    its coordinate and no other connecting component pin shares that exact
    coordinate. These can be marked with open circles (``\\node[ocirc]``) to
    flag dangling terminals — the counterpart of :func:`open_endpoints`, which
    flags dangling *wire* ends. The two sets are disjoint by construction: open
    endpoints are wire ends *not* at a pin, while these are pins with *no* wire.

    Pins of :data:`NON_CONNECTING_KINDS` (e.g. the ``open`` voltage annotation)
    are ignored entirely: they form no connection, so they neither count as a
    connection for a real pin nor are flagged themselves.

    Pure function; returns a set of (x, y) tuples in grid units.
    """
    # Every wire vertex coordinate (endpoints and interior points alike).
    # Degenerate wires (fewer than two points) connect nothing and are skipped,
    # so a stray single-point wire on a pin does not mark it as connected.
    wire_points: set[tuple[float, float]] = set()
    for wire in schematic.wires:
        if len(wire.points) < 2:
            continue
        for pt in wire.points:
            wire_points.add((round(pt[0], 6), round(pt[1], 6)))

    # Count component pins per coordinate so two abutting pins are not flagged.
    # Non-connecting kinds (voltage annotation) are skipped entirely.
    pin_count: dict[tuple[float, float], int] = {}
    for comp in schematic.components:
        if comp.kind in NON_CONNECTING_KINDS:
            continue
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


def _axis_segments(
    wire: "Wire",
) -> list[tuple[int, str, tuple[float, float], tuple[float, float]]]:
    """Return ``(seg_index, orientation, start, end)`` for each axis-aligned
    segment of *wire*. ``orientation`` is ``"h"`` or ``"v"``; zero-length or
    (illegal) diagonal segments are skipped."""
    out: list[tuple[int, str, tuple[float, float], tuple[float, float]]] = []
    pts = wire.points
    for i in range(len(pts) - 1):
        a, b = tuple(pts[i]), tuple(pts[i + 1])
        if a[0] == b[0] and a[1] != b[1]:
            out.append((i, "v", a, b))
        elif a[1] == b[1] and a[0] != b[0]:
            out.append((i, "h", a, b))
    return out


def wire_crossings(
    schematic: "Schematic", default_on: bool = True
) -> list["WireHop"]:
    """Line-hops for wires that cross without connecting (spec §6.4).

    A hop belongs at a point where a horizontal segment of one wire and a
    vertical segment of another cross **strictly interior to both** segments,
    with **no vertex** of either wire at the point — which in this model means
    the wires are genuinely not connected there (connections always share a
    vertex, because the editor splits a wire when another's endpoint lands
    mid-segment). The point is additionally required not to be a junction or a
    component connection point, as a defensive guard.

    *default_on* is the global line-hops preference (§10.8). It governs only
    **default-mode** wires (``hop_mode == ""``); per-wire overrides win over it.

    Which wire hops (arcs over the other) at a crossing is decided in order:

    1. A wire whose ``hop_mode == "never"`` cannot be the hopper (but may still
       be hopped *over*); ``"always"`` can always hop; ``""`` (default) can hop
       only when *default_on*.
    2. Among the wires that *can* hop there, the one with the higher **tier**
       wins (``"always"`` outranks default), then higher ``z_order``, then later
       position in ``schematic.wires``. If neither can hop, no bump is drawn.

    ``no_junction_dots`` wires (annotation leads) are excluded from hops entirely
    (in either role), paralleling their exclusion from junction dots.

    Pure function; returns a list of :class:`WireHop` (decoration, never stored).
    """
    eligible = [
        (i, w)
        for i, w in enumerate(schematic.wires)
        # No-junction-dot wires (annotation leads) are excluded from hops.
        if not w.no_junction_dots and len(w.points) >= 2
    ]

    junctions = junction_points(schematic)
    pins: set[tuple[float, float]] = set()
    for comp in schematic.components:
        for p in component_connection_points(comp):
            pins.add((round(p[0], 6), round(p[1], 6)))

    def _can_hop(w: "Wire") -> bool:
        if w.hop_mode == "never":
            return False
        if w.hop_mode == "always":
            return True
        return default_on

    def _rank(w: "Wire", idx: int) -> tuple[int, int, int]:
        # Higher is preferred: "always" tier beats default, then z_order, then
        # later position in the wire list.
        return (1 if w.hop_mode == "always" else 0, w.z_order, idx)

    hops: list[WireHop] = []
    seen: set[tuple[str, tuple[float, float]]] = set()

    for ai in range(len(eligible)):
        idx_a, wa = eligible[ai]
        va = {tuple(p) for p in wa.points}
        segs_a = _axis_segments(wa)
        for bi in range(ai + 1, len(eligible)):
            idx_b, wb = eligible[bi]
            a_ok, b_ok = _can_hop(wa), _can_hop(wb)
            if not a_ok and not b_ok:                  # neither wire may hop here
                continue
            vb = {tuple(p) for p in wb.points}
            segs_b = _axis_segments(wb)
            for ia, oa, a0, a1 in segs_a:
                for ib, ob, b0, b1 in segs_b:
                    if oa == ob:                       # need one H and one V
                        continue
                    hseg, vseg = ((a0, a1), (b0, b1)) if oa == "h" else ((b0, b1), (a0, a1))
                    p = (vseg[0][0], hseg[0][1])       # (vertical.x, horizontal.y)
                    if not _point_strictly_on_segment(p, a0, a1):
                        continue
                    if not _point_strictly_on_segment(p, b0, b1):
                        continue
                    if p in va or p in vb:             # a vertex here = a connection
                        continue
                    pr = (round(p[0], 6), round(p[1], 6))
                    if pr in junctions or pr in pins:  # defensive: real connection
                        continue
                    # Pick the hopper among the wires allowed to hop here.
                    if a_ok and (not b_ok or _rank(wa, idx_a) >= _rank(wb, idx_b)):
                        hop_id, orient, seg_index = wa.id, oa, ia
                    else:
                        hop_id, orient, seg_index = wb.id, ob, ib
                    key = (hop_id, pr)
                    if key in seen:
                        continue
                    seen.add(key)
                    hops.append(WireHop(p, hop_id, orient, seg_index))
    return hops
