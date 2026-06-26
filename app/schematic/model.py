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
    DrawingComponent,
    RectComponent,
    TextNodeComponent,
)

__all__ = [
    "Component",
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

#: Half-width (in grid units) of a line-hop, where one wire hops over another
#: (see :func:`wire_crossings`). A hop is rendered as a CircuiTikZ ``jump
#: crossing`` node, whose ``.west/.east/.north/.south`` anchors sit this far from
#: the crossing point. The value is **measured** from CircuiTikZ's default
#: ``bipoles/crossing/size`` (0.2): the anchor offset is ``0.5·0.2·Rlen`` with the
#: bipole base length ``Rlen = 1.4`` GU, i.e. 0.14 GU. The canvas matches this so
#: the on-screen hop lines up with the exported one; codegen uses the default
#: size (no ``\ctikzset`` override) so the offset stays in sync.
HOP_HALF_GU: float = 0.14

#: Radius (GU) of the semicircular hump the canvas paints for a hop, matching the
#: CircuiTikZ ``jump crossing`` shape (its arc radius is 0.4·half-width). The
#: straight arms out to the anchors (±:data:`HOP_HALF_GU`) are the plain wire.
HOP_ARC_RADIUS_GU: float = 0.4 * HOP_HALF_GU

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
    #: The *other* wire at this crossing — the one hopped over (gets the gap when
    #: rendered as a CircuiTikZ ``jump crossing`` node). ``None`` only for hops
    #: constructed without it (older call sites/tests).
    crossed_wire_id: str | None = None


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

    # Document-level CircuiTikZ label conventions (§4 / §7). Each is "american"
    # or "european" and selects the voltage/current arrow style for the whole
    # figure (emitted as a picture-scoped \ctikzset; see codegen). Defaults match
    # CircuiTikZ's american default and keep pre-config files unchanged.
    voltage_style: str = "american"
    current_style: str = "american"

    # Document-level symbol style (§5.4), manual library. Maps a CircuiTikZ style
    # *family* ("resistors"/"inductors") to its value ("american"/"european"/"cute"),
    # switching every symbol of that family at once. Empty = all defaults (american),
    # so pre-0.7 files are unchanged. See app.components.library.STYLE_AXES.
    symbol_style: dict[str, str] = field(default_factory=dict)

    # Document-level LaTeX preamble settings (§7.2). ``siunitx`` adds the
    # package to CircuiTikZ's option list so unit macros (\qty, \unit) work in
    # labels (issue #29). It defaults **on**: siunitx is cheap to load and most
    # schematics use units at some point, so a new document supports \qty out of
    # the box. ``preamble`` is free-form LaTeX spliced verbatim into the document
    # preamble — the escape hatch for packages/macros/\ctikzset the UI does not
    # surface as a dedicated control; default empty. Both travel with the .hv file.
    siunitx: bool = True
    preamble: str = ""


#: Accepted values for the document voltage/current label styles.
LABEL_STYLES: tuple[str, ...] = ("american", "european")


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

#: Decimal places of the canonical coordinate key. The single constant from
#: which both :func:`point_key` (point identity) and :data:`KEY_EPS` (interval
#: coverage tolerance) derive, so the two conventions cannot diverge.
_KEY_DECIMALS: int = 6

#: Coordinate tolerance consistent with :func:`point_key`: two coordinates
#: whose keys agree differ by at most one unit in the last rounded decimal.
#: Used wherever a comparison needs an epsilon rather than exact key equality
#: (interval coverage in the contained-wire test).
KEY_EPS: float = 10.0 ** -_KEY_DECIMALS


def point_key(pt: tuple[float, float]) -> tuple[float, float]:
    """Canonical coordinate key (6-dp rounding) for coincidence comparisons.

    The **single** connectivity convention: every wire↔pin and wire↔wire
    coincidence test (junctions, open endpoints, wire-following on move/resize,
    splits, merges, collinearity/coverage in the contained-wire test, dict/set
    keys for canvas decorations) compares coordinates through this helper, so
    float noise from off-grid pins (scaled gates) can never silently detach a
    wire. Stored geometry stays **unrounded** — only comparisons go through
    the key.
    """
    return (round(pt[0], _KEY_DECIMALS), round(pt[1], _KEY_DECIMALS))


#: Grid snap granularity in GU — the minor grid (spec §3.1). The canvas-side
#: constant ``app.canvas.geometry.SNAP_GU`` aliases this value, so the grid
#: pitch has a single Qt-free source of truth.
GRID_GU: float = 0.25


def snap_coord(v: float) -> float:
    """Round a single GU coordinate to the nearest grid node (GRID_GU)."""
    return round(v / GRID_GU) * GRID_GU


def snap_point(pt: tuple[float, float]) -> tuple[float, float]:
    """Round a GU point to the nearest grid node on both axes."""
    return (snap_coord(pt[0]), snap_coord(pt[1]))


def coord_on_grid(v: float) -> bool:
    """True when a single coordinate lies on the 0.25-GU grid."""
    return abs(v * 4 - round(v * 4)) < 1e-6


def point_on_grid(pt: tuple[float, float]) -> bool:
    """True when both coordinates lie on the 0.25-GU grid."""
    return coord_on_grid(pt[0]) and coord_on_grid(pt[1])


#: Kinds drawn as resizable block-diagram boxes (anchored-corner resize,
#: perimeter/cardinal connection points, behind-circuit default z-order).
BOX_KINDS: frozenset[str] = frozenset({"rect", "circle"})


def is_box_kind(obj: "Component | str") -> bool:
    """True when *obj* (a Component or a kind string) is a box annotation
    (``rect``/``circle``) — the single predicate for box-kind dispatch."""
    kind = obj if isinstance(obj, str) else obj.kind
    return kind in BOX_KINDS


#: Categories whose components are single-point **connection markers** (junction
#: dots, the terminal poles). Their symbol coincides with their pin, so they: draw
#: no pin-dot marker; are selected/dragged on click rather than auto-starting a
#: wire; and **follow** a component they sit on when it moves (so a dot placed on a
#: transformer/op-amp anchor tracks the symbol). Single source for the predicate.
TERMINAL_MARKER_CATEGORIES: frozenset[str] = frozenset({"Terminals"})


def is_terminal_marker(obj: "Component | str") -> bool:
    """True when *obj* (a Component or kind string) is a single-point connection
    marker (a Terminals-category dot/pole)."""
    kind = obj if isinstance(obj, str) else obj.kind
    from app.components.registry import REGISTRY
    defn = REGISTRY.get(kind)
    return defn is not None and defn.category in TERMINAL_MARKER_CATEGORIES


def is_resizable_node(obj: "Component | str") -> bool:
    """True when *obj* (a Component or a kind string) is an **anisotropic** 2D
    drag-resizable node (§6.4): a scalable multi-terminal symbol that is *not* sized
    by a CircuiTikZ body-height key. Covers the manual-library logic gates, the
    digital blocks (flip-flops, ALU, adder) and the muxdemux. The curated gates use
    a height key and resize **uniformly** instead (via ``Component.scale``)."""
    kind = obj if isinstance(obj, str) else obj.kind
    from app.components import library
    return library.is_scalable(kind) and not library.gate_uses_height(kind)


#: Half-width of the magnetic snap zone for a continuous resize, expressed as a
#: *pin displacement* in GU: a resize factor snaps to a pin-grid-aligning value
#: only when the nearest pin it would realign sits within this distance of the
#: grid. Well below ``GRID_GU / 2`` so a genuine continuous band remains between
#: snaps (≈3 px on screen at the default zoom).
RESIZE_SNAP_GU: float = 0.05


def snap_resize_factor(
    f_raw: float,
    offsets: "list[float]",
    *,
    grid: float = GRID_GU,
    tol: float = RESIZE_SNAP_GU,
    minimum: float = GRID_GU,
) -> float:
    """Snap a continuous resize *factor* to a value that lands a pin on the grid.

    A resizable node sits on the grid and scales each pin offset ``o`` (GU, along
    one axis) to ``o * f``; that pin is grid-aligned when ``o * f`` is a multiple
    of *grid*. Given the raw factor *f_raw* from a drag and the unscaled pin
    *offsets* along that axis, this returns the nearest factor that grid-aligns a
    pin **iff** doing so moves that pin by less than *tol* (so resizing stays
    continuous everywhere except a gentle magnet around each aligning size); the
    raw factor (rounded) is returned otherwise. The strongest pull wins: among the
    pins within tolerance, the one realigned by the least displacement is chosen.
    The result is floored at *minimum* so the body can't collapse or invert."""
    best_f: float | None = None
    best_err = tol
    for o in offsets:
        a = abs(o)
        if a < 1e-9:
            continue
        n = round(o * f_raw / grid)
        f_c = n * grid / o
        if f_c < minimum - 1e-9:
            continue
        err = abs(f_c - f_raw) * a          # how far this pin moves to align (GU)
        if err < best_err:
            best_err, best_f = err, f_c
    if best_f is not None:
        return max(minimum, round(best_f, 6))
    return max(minimum, round(f_raw, 6))


def node_resize_factors(component: "Component") -> tuple[float, float] | None:
    """The per-instance ``(wf, hf)`` width/height scale factors for an anisotropic
    resizable node, or ``None`` when it is not such a node or is at its natural size.
    The factors live in ``span_override`` (the corner drag-resize); a legacy uniform
    ``Component.scale`` (from the old Size dropdown) is honoured as ``(s, s)`` so
    older documents still render scaled."""
    if not is_resizable_node(component):
        return None
    so = component.span_override
    if so is not None:
        return (so[0], so[1])
    s = float(getattr(component, "scale", 1.0))
    return (s, s) if abs(s - 1.0) > 1e-9 else None


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


def _interval_covered(
    lo: float, hi: float, intervals: list[tuple[float, float]], eps: float = KEY_EPS
) -> bool:
    """True if [lo, hi] is fully covered by the union of *intervals* (each a
    (start, end) pair on the same axis). A zero-length target is trivially
    covered. The tolerance defaults to :data:`KEY_EPS` — the same 6-dp
    convention as :func:`point_key` — so coverage and point identity agree."""
    if hi - lo <= eps:
        return True
    relevant = sorted(iv for iv in intervals if iv[1] > lo - eps and iv[0] < hi + eps)
    cur = lo
    for a, b in relevant:
        if a > cur + eps:
            return False  # gap before this interval
        cur = max(cur, b)
        if cur >= hi - eps:
            return True
    return cur >= hi - eps


def _segment_covered(
    seg: tuple[tuple[float, float], tuple[float, float]],
    other_segs: list[tuple[tuple[float, float], tuple[float, float]]],
) -> bool:
    """True if axis-aligned *seg* lies entirely on collinear *other_segs*.

    Collinearity is decided through :func:`point_key` (the 6-dp connectivity
    convention) rather than exact float equality, so float noise from off-grid
    pins (scaled gates) — e.g. ``0.30000000000000004`` vs ``0.3`` — cannot make
    a genuinely contained wire go undetected (or vice versa). Interval coverage
    uses the matching :data:`KEY_EPS` tolerance; on-grid geometry behaves
    exactly as before.
    """
    (x0, y0), (x1, y1) = point_key(seg[0]), point_key(seg[1])
    keyed = [(point_key(a), point_key(b)) for a, b in other_segs]
    if x0 == x1:        # vertical at x = x0
        lo, hi = sorted((y0, y1))
        ivals = [tuple(sorted((b, d)))
                 for (a, b), (c, d) in keyed if a == x0 and c == x0]
    elif y0 == y1:      # horizontal at y = y0
        lo, hi = sorted((x0, x1))
        ivals = [tuple(sorted((a, c)))
                 for (a, b), (c, d) in keyed if b == y0 and d == y0]
    else:               # diagonal (should not occur on a Manhattan wire)
        return False
    return _interval_covered(lo, hi, ivals)


def wire_contained_by_others(points: list[tuple[float, float]], others) -> bool:
    """True if a wire's polyline lies *entirely* on top of other wires' segments.

    Such a wire is redundant — it draws nothing not already drawn and forms no new
    connection — so it should be removed (a degenerate class alongside the
    single-point wire). *others* is an iterable of objects with a ``points`` list.
    Every segment of *points* must be collinear-covered by the union of the other
    wires' segments. A wire with fewer than two points is not handled here (that
    is the single-point degenerate case)."""
    if len(points) < 2:
        return False
    other_segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for o in others:
        op = list(o.points)
        if len(op) >= 2:
            other_segs.extend(zip(op, op[1:]))
    if not other_segs:
        return False
    return all(_segment_covered(seg, other_segs) for seg in zip(points, points[1:]))


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


# Backwards-compatible alias for the shared grid predicate (see coord_on_grid).
_on_quarter_grid = coord_on_grid


def route_pin_aware(
    a: tuple[float, float],
    b: tuple[float, float],
    vfirst: bool | None = None,
) -> list[tuple[float, float]]:
    """Manhattan route a→b that respects **off-grid component pins** (a scaled
    logic gate's terminal). The leg adjacent to an off-grid endpoint is oriented
    to keep that endpoint's off-grid coordinate, so the wire extends from the pin
    along its own lead line and only then elbows onto the grid (the corner
    inherits the pin's off-grid coordinate, which validation permits, §3.1).

    With **neither** endpoint off-grid, the corner follows *vfirst* (the caller's
    orientation preference — e.g. the cursor's heading while drawing, so the elbow
    traces the path the cursor took; ``None`` = dominant-axis default).

    When an off-grid endpoint is off-grid in **one axis only** (a scaled gate's
    terminal, sitting at the end of an axis-aligned lead), the lead direction wins
    over *vfirst*: the adjacent leg keeps the pin's off-grid coordinate so the wire
    extends along the lead before elbowing onto the grid. But when the endpoint is
    off-grid in **both** axes (e.g. the thyristor/triac gate, which has no single
    lead axis), *either* orientation produces a valid corner — the corner inherits
    one of the pin's own off-grid coordinates, which validation permits — so
    *vfirst* is honoured, making vertical routing work just like horizontal. Shared
    by the canvas drawing/vertex-drag router (`SchematicScene._route`) and
    component-follow re-routing (`SetComponentScaleCommand`). Pure function."""
    a_off = not (_on_quarter_grid(a[0]) and _on_quarter_grid(a[1]))
    b_off = not (_on_quarter_grid(b[0]) and _on_quarter_grid(b[1]))
    if not a_off and not b_off:
        return route(a, b, vfirst)
    if len(route(a, b)) != 3:
        return [a, b]
    # A single-axis off-grid endpoint forces the orientation that keeps its
    # off-grid coordinate in the adjacent leg (continue along the lead); a
    # both-axes off-grid endpoint imposes no constraint, so honour *vfirst*.
    forced: bool | None = None
    if a_off:
        ax_off, ay_off = not _on_quarter_grid(a[0]), not _on_quarter_grid(a[1])
        if not (ax_off and ay_off):
            forced = True if ax_off else False   # keep off-grid x → vfirst; else hfirst
    else:  # b_off
        bx_off, by_off = not _on_quarter_grid(b[0]), not _on_quarter_grid(b[1])
        if not (bx_off and by_off):
            forced = True if by_off else False   # keep off-grid y → vfirst; else hfirst
    return route(a, b, forced if forced is not None else vfirst)


def component_pin_positions(component: "Component") -> list[tuple[float, float]]:
    """Absolute (rotate-then-mirror) pin coordinates of *component*, in GU.

    Pin offsets live in the registry relative to the component origin; this
    applies the same clockwise-rotate-then-horizontal-mirror transform the canvas
    ``QTransform`` and code generator use, so connectivity tests operate in
    schematic coordinates. Mirroring *after* rotation (a global Flip-X of the
    rotated component) keeps a bipole's terminals on their grid cells at every
    rotation — mirroring before rotation would relocate the far terminal at
    90°/270°, detaching it from connected wires. Returns an empty list for an
    unknown kind.
    """
    # Lazy import avoids a cycle during package construction.
    from app.components.registry import REGISTRY
    from app.components import library

    defn = REGISTRY.get(component.kind)
    if defn is None:
        return []

    # Parametric kinds (logic gates) resolve their pins from the instance's
    # parameter value; fixed kinds use the static registry pins.
    pins = library.resolved_pins(component)
    # A scaled logic gate snaps each pin onto the 0.25-GU grid (the lead stubs
    # bridge the off-grid scaled body anchor to it); None for unscaled gates and
    # all other kinds, which use their base offsets unchanged.
    gate = library.gate_layout(component)
    # 2D-resizable nodes (e.g. muxdemux) scale every pin offset by the instance's
    # (wf, hf) factors before rotation/mirror, so connectivity, the magnet and the
    # codegen anchors all track the resized body (§6.4).
    nf = node_resize_factors(component)

    ox, oy = component.position
    out: list[tuple[float, float]] = []
    for i, pin in enumerate(pins):
        dx, dy = pin.offset
        # For resizable two-terminal components, the terminal pin (index 1)
        # uses span_override when set instead of the registry default offset.
        if (
            i == 1
            and defn.resizable
            and component.span_override is not None
        ):
            dx, dy = component.span_override
        elif gate is not None:
            dx, dy = gate[i]["pin_offset"]
        if nf is not None:
            dx, dy = dx * nf[0], dy * nf[1]
        r = component.rotation % 360
        if r == 90:
            rx, ry = (-dy, dx)
        elif r == 180:
            rx, ry = (-dx, -dy)
        elif r == 270:
            rx, ry = (dy, -dx)
        else:
            rx, ry = (dx, dy)
        if component.mirror:
            rx = -rx
        out.append((ox + rx, oy + ry))
    return out


# Spacing of connection points along a rectangle's edges, in GU — the minor
# grid (GRID_GU, the single Qt-free source of the grid pitch).
_RECT_EDGE_SPACING: float = GRID_GU


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


def junction_points(
    schematic: "Schematic",
    *,
    points_override: dict[str, list[tuple[float, float]]] | None = None,
    pin_positions: "list[tuple[float, float]] | None" = None,
) -> set[tuple[float, float]]:
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

    This is the **single** source of the junction-dot rule. The live canvas
    decorations call it directly; the drag preview calls it with overrides so
    it can never drift from the committed result (incl. the per-wire
    ``no_junction_dots`` opt-out):

    * *points_override* maps ``wire_id → preview points`` to substitute a
      wire's geometry mid-drag (only moving wires need an entry).
    * *pin_positions* supplies the component pin coordinates to count
      (with multiplicity) instead of deriving them from the components — used
      during a component drag, where the dragged pins are at live positions,
      not their model ones.

    Pure function; returns a set of (x, y) tuples in grid units (each rounded
    through :func:`point_key`).
    """
    override = points_override or {}
    degree: dict[tuple[float, float], int] = {}

    def add(pt: tuple[float, float], d: int) -> None:
        degree[pt] = degree.get(pt, 0) + d

    for wire in schematic.wires:
        # Annotation wires opt out of junction-dot placement entirely.
        if wire.no_junction_dots:
            continue
        pts = override.get(wire.id, wire.points)
        if len(pts) < 2:
            continue
        # Count how many segment-ends of THIS wire touch each coordinate:
        # endpoints contribute 1, interior vertices contribute 2. Guard against
        # a wire that revisits a coordinate by summing its own incidences.
        own: dict[tuple[float, float], int] = {}
        n = len(pts)
        for i, pt in enumerate(pts):
            pt = point_key(pt)
            own[pt] = own.get(pt, 0) + (1 if (i == 0 or i == n - 1) else 2)
        for pt, d in own.items():
            add(pt, d)

    if pin_positions is None:
        pin_positions = [
            p
            for comp in schematic.components
            for p in component_pin_positions(comp)
        ]
    for p in pin_positions:
        add(point_key(p), 1)

    return {pt for pt, d in degree.items() if d >= 3}


# Kinds whose pins do not form an electrical connection. The voltage
# annotation (``open``) renders as a CircuiTikZ ``to[open]`` — an open circuit
# that draws nothing but a voltage label — so anything that merely abuts a
# voltage annotation pin is still electrically open. Such pins neither connect a
# wire endpoint (``open_endpoints``) nor a component pin (``unconnected_pins``),
# and are never themselves flagged. (The current annotation ``short`` is a real
# closed wire and is intentionally NOT listed here.)
NON_CONNECTING_KINDS: frozenset[str] = frozenset({"open"})


def open_endpoints(
    schematic: "Schematic",
    *,
    points_override: dict[str, list[tuple[float, float]]] | None = None,
    pin_positions: set[tuple[float, float]] | None = None,
) -> set[tuple[float, float]]:
    """Wire endpoints that do not connect to any component pin.

    An open endpoint is a wire's first or last point that lies at a position
    not occupied by any connecting component pin. These are rendered as open
    circles (\\node[ocirc]) to indicate unconnected terminals.

    Interior wire vertices are never open endpoints — only the first and last
    point of each wire are candidates.  Pins of :data:`NON_CONNECTING_KINDS`
    (the ``open`` voltage annotation) do not count as a connection, so a wire
    end that only touches one stays open. A wire flagged ``no_termination_dots``
    contributes no open ends (but still counts for *other* wires' connection
    detection), and an end carrying a custom marker has its automatic terminal
    replaced by that marker.

    This is the **single** source of the open-terminal rule. The live canvas
    decorations call it directly; the drag preview calls it with overrides so it
    can never drift from the committed result:

    * *points_override* maps ``wire_id → preview points`` to substitute a wire's
      geometry mid-drag (only moving wires need an entry).
    * *pin_positions* supplies the connecting-pin coordinate set (already rounded)
      to use instead of deriving it from the components — used during a component
      drag, where the dragged pins are at live positions, not their model ones.

    Pure function; returns a set of (x, y) tuples in grid units.
    """
    override = points_override or {}

    def pts_of(wire: "Wire") -> list[tuple[float, float]]:
        return override.get(wire.id, wire.points)

    # Connecting component pin positions (voltage annotations excluded), unless
    # the caller supplied an explicit set (live drag positions).
    if pin_positions is None:
        pin_positions = set()
        for comp in schematic.components:
            if comp.kind in NON_CONNECTING_KINDS:
                continue
            for p in component_connection_points(comp):
                pin_positions.add(point_key(p))

    # Count every wire vertex coordinate. A wire endpoint that coincides with ANY
    # vertex of ANY other wire is connected, so it is not an open endpoint.
    # Degenerate wires (fewer than two points) connect nothing and are skipped.
    all_wire_points: dict[tuple[float, float], int] = {}
    for wire in schematic.wires:
        pts = pts_of(wire)
        if len(pts) < 2:
            continue
        for pt in pts:
            pt_r = point_key(pt)
            all_wire_points[pt_r] = all_wire_points.get(pt_r, 0) + 1

    candidates: set[tuple[float, float]] = set()
    for wire in schematic.wires:
        # Annotation wires opt out of terminal (ocirc) markers on their own ends.
        if wire.no_termination_dots:
            continue
        pts = pts_of(wire)
        if len(pts) < 2:
            continue
        # A custom endpoint marker (e.g. an arrow) replaces the automatic
        # open-circle terminal at that specific end, so skip it here.
        for pt, marker in ((pts[0], wire.start_marker), (pts[-1], wire.end_marker)):
            if marker:
                continue
            pt_r = point_key(pt)
            if pt_r in pin_positions:
                continue
            if all_wire_points.get(pt_r, 0) > 1:
                continue
            candidates.add(pt_r)

    return candidates


def unconnected_pins(
    schematic: "Schematic",
    *,
    points_override: dict[str, list[tuple[float, float]]] | None = None,
    pin_positions: "list[tuple[float, float]] | None" = None,
) -> set[tuple[float, float]]:
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

    Like :func:`open_endpoints` this is the single source of its rule; the drag
    preview substitutes live geometry via the override hooks:

    * *points_override* maps ``wire_id → preview points`` for wires mid-drag.
    * *pin_positions* supplies the connecting-pin coordinates (counted with
      multiplicity) to use instead of deriving them from the components — the
      caller is then responsible for excluding :data:`NON_CONNECTING_KINDS`.

    Pure function; returns a set of (x, y) tuples in grid units.
    """
    override = points_override or {}
    # Every wire vertex coordinate (endpoints and interior points alike).
    # Degenerate wires (fewer than two points) connect nothing and are skipped,
    # so a stray single-point wire on a pin does not mark it as connected.
    wire_points: set[tuple[float, float]] = set()
    for wire in schematic.wires:
        pts = override.get(wire.id, wire.points)
        if len(pts) < 2:
            continue
        for pt in pts:
            wire_points.add(point_key(pt))

    # Count component pins per coordinate so two abutting pins are not flagged.
    # Non-connecting kinds (voltage annotation) are skipped entirely.
    if pin_positions is None:
        pin_positions = [
            p
            for comp in schematic.components
            if comp.kind not in NON_CONNECTING_KINDS
            for p in component_pin_positions(comp)
        ]
    pin_count: dict[tuple[float, float], int] = {}
    for p in pin_positions:
        key = point_key(p)
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
    Coordinates are compared through :func:`point_key` (float-noise guard).
    """
    pt, a, b = point_key(pt), point_key(a), point_key(b)
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
    key = point_key(point)
    out: list[tuple[str, int]] = []
    for wire in schematic.wires:
        pts = wire.points
        if any(point_key(p) == key for p in pts):
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
    key = point_key(point)
    out: list[tuple[str, int]] = []
    for wire in schematic.wires:
        pts = wire.points
        for i in range(1, len(pts) - 1):   # skip endpoints
            if point_key(pts[i]) == key:
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
            pins.add(point_key(p))

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
        va = {point_key(p) for p in wa.points}
        segs_a = _axis_segments(wa)
        for bi in range(ai + 1, len(eligible)):
            idx_b, wb = eligible[bi]
            a_ok, b_ok = _can_hop(wa), _can_hop(wb)
            if not a_ok and not b_ok:                  # neither wire may hop here
                continue
            vb = {point_key(p) for p in wb.points}
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
                    if point_key(p) in va or point_key(p) in vb:
                        continue                       # a vertex here = a connection
                    pr = point_key(p)
                    if pr in junctions or pr in pins:  # defensive: real connection
                        continue
                    # Pick the hopper among the wires allowed to hop here.
                    if a_ok and (not b_ok or _rank(wa, idx_a) >= _rank(wb, idx_b)):
                        hop_id, orient, seg_index = wa.id, oa, ia
                        crossed_id = wb.id
                    else:
                        hop_id, orient, seg_index = wb.id, ob, ib
                        crossed_id = wa.id
                    key = (hop_id, pr)
                    if key in seen:
                        continue
                    seen.add(key)
                    hops.append(WireHop(p, hop_id, orient, seg_index, crossed_id))
    return hops
