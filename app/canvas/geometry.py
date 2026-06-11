"""
Pure geometry helpers for the schematic canvas.

These functions carry **no scene/Qt state** — they operate purely on numbers,
coordinate tuples, and ``QPointF`` values, so they can be unit-tested in
isolation without a ``QGraphicsScene`` or a running ``QApplication``.

Two coordinate systems are in play (see :mod:`app.canvas.scene`):

* **Schematic coords** (GU): what the model stores. Snap granularity 0.25 GU.
* **Scene/pixel coords**: GU × ``GRID_PX``. All graphics items live here.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF

from app.canvas.style import GRID_PX
from app.schematic.model import GRID_GU

# ---------------------------------------------------------------------------
# Snap / proximity constants (spec §3.1, §6.4)
# ---------------------------------------------------------------------------

SNAP_GU: float = GRID_GU
"""Grid snap granularity — the minor grid (spec §3.1). Components, wire vertices,
and junctions all live on this 0.25 GU lattice. Aliases the Qt-free
``app.schematic.model.GRID_GU`` so the pitch has a single source of truth."""

NUDGE_GU: float = 0.25
"""Arrow-key nudge step (one minor-grid cell)."""

# Proximity radii are kept below half the grid spacing (0.125 GU) so a click is
# never ambiguous between two adjacent 0.25 GU nodes.
PIN_SNAP_GU: float = 0.125
"""Wire endpoints snap to a pin within this radius (spec §6.4)."""

VERTEX_HIT_GU: float = 0.15
"""A wire vertex is grabbable for dragging within this radius of the cursor."""

PIN_GRAB_GU: float = 0.15
"""Auto-start a wire only when the click is within this radius of a free pin."""


# ---------------------------------------------------------------------------
# Coordinate conversion (depend only on GRID_PX / SNAP_GU)
# ---------------------------------------------------------------------------

def snap_gu(value: float) -> float:
    """Round a GU value to the nearest grid node (SNAP_GU = 0.25)."""
    return round(value / SNAP_GU) * SNAP_GU


def scene_to_gu(pt: QPointF) -> tuple[float, float]:
    """Convert a scene/pixel point to (x, y) in GU."""
    return (pt.x() / GRID_PX, pt.y() / GRID_PX)


def gu_to_scene(x: float, y: float) -> QPointF:
    """Convert GU coordinates to a scene/pixel ``QPointF``."""
    return QPointF(x * GRID_PX, y * GRID_PX)


def snap_point_gu(pt: QPointF) -> tuple[float, float]:
    """Convert a scene point to GU and snap to the nearest 0.25 GU node."""
    x, y = scene_to_gu(pt)
    return (snap_gu(x), snap_gu(y))


# ---------------------------------------------------------------------------
# Span / terminal rotation mapping
# ---------------------------------------------------------------------------
#
# A two-terminal component stores its terminal offset as a *local* span; the
# terminal's world position is that span rotated clockwise (Qt's Y-down
# convention) and then horizontally mirrored (a global Flip-X applied outermost,
# matching the canvas QTransform). These two helpers are exact inverses of each
# other's rotation step and were previously copy-pasted three times inside the
# scene's endpoint-drag code.

def world_delta_to_local(dx_w: float, dy_w: float, rotation: int) -> tuple[float, float]:
    """Map a world-space delta back into a component's local span axes.

    The inverse rotation of :func:`local_span_to_world` (mirror not applied —
    the drag math handles mirror separately).
    """
    r = rotation % 360
    if r == 90:
        return (dy_w, -dx_w)
    if r == 180:
        return (-dx_w, -dy_w)
    if r == 270:
        return (-dy_w, dx_w)
    return (dx_w, dy_w)


def local_span_to_world(
    span: tuple[float, float], rotation: int, mirror: bool
) -> tuple[float, float]:
    """Map a component-local span to its world-space terminal offset.

    Applies a clockwise rotation (Y-down) first, then the horizontal mirror —
    matching the canvas ``QTransform`` (``scale(-1,1)`` is applied *outermost*,
    i.e. a global Flip-X of the already-rotated component) and
    ``component_pin_positions`` in the model. Mirroring *after* rotation keeps a
    bipole's terminals on their grid cells for every rotation; mirroring before
    would move the far terminal to the opposite side of the origin at 90°/270°.
    """
    sdx, sdy = span
    r = rotation % 360
    if r == 90:
        rx, ry = (-sdy, sdx)
    elif r == 180:
        rx, ry = (-sdx, -sdy)
    elif r == 270:
        rx, ry = (sdy, -sdx)
    else:
        rx, ry = (sdx, sdy)
    if mirror:
        rx = -rx
    return (rx, ry)


# ---------------------------------------------------------------------------
# Segment proximity
# ---------------------------------------------------------------------------

def dist2_to_segment(
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


def wire_proximity_key(
    gx: float, gy: float, points: list[tuple[float, float]]
) -> tuple[float, int] | None:
    """Sort key for how close (gx,gy) is to a polyline *points*, or None if empty.

    Key is ``(rounded_dist2, endpoint_rank)`` where endpoint_rank is 0 when the
    closest point is in a segment interior (cursor passes through) and 1 when it
    is only an endpoint touch. Smaller sorts as "more on the wire".

    A click that lands exactly on an intermediate vertex gets rank 0: the wire
    passes through that point (shared by two adjacent segments), so it is a full
    interior hit even though both adjacent segments individually report
    ``at_end=True``.
    """
    best: tuple[float, int] | None = None
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        d2, at_end = dist2_to_segment(gx, gy, x0, y0, x1, y1)
        key = (round(d2, 9), 1 if at_end else 0)
        if best is None or key < best:
            best = key
    # Promote rank to 0 if the best distance matches an intermediate vertex.
    # Each segment reports at_end=True for its shared endpoint, so without this
    # correction a click at an intermediate vertex is ranked 1 instead of 0,
    # losing unfairly to an adjacent wire stub.
    if best is not None and best[1] == 1:
        for vx, vy in points[1:-1]:
            if round((gx - vx) ** 2 + (gy - vy) ** 2, 9) == best[0]:
                best = (best[0], 0)
                break
    return best
