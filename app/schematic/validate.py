"""
Schematic validation.

validate(schematic) checks all invariants defined in spec section 4.5 and
returns a list of human-readable error strings. An empty list means the
schematic is valid.

This module has no Qt dependency and no side effects.
"""

from __future__ import annotations

import math

from app.components.registry import REGISTRY
from app.schematic.model import Schematic, component_connection_points

# Components may be oriented in 45° increments (§6.x): the four right angles plus
# the four diagonals. A 45° orientation puts the pins off the 0.25 grid, which the
# wire magnet / pin-axis alignment already handles (§3.1).
_VALID_ROTATIONS = {0, 45, 90, 135, 180, 225, 270, 315}


def _is_on_grid(value: float) -> bool:
    """Return True if value is a multiple of the 0.25 GU grid (spec §3.1).

    Non-finite values (NaN/±Infinity) are never on the grid; they must report
    an error rather than crash the int() conversion.
    """
    if not math.isfinite(value):
        return False
    return (value * 4) == int(value * 4)


def validate(schematic: Schematic) -> list[str]:
    """
    Validate all invariants for *schematic*.

    Returns a list of error message strings. Empty list → valid.
    """
    errors: list[str] = []

    # ------------------------------------------------------------------
    # Invariant 1: all Component.kind values exist in REGISTRY
    # ------------------------------------------------------------------
    for comp in schematic.components:
        if comp.kind not in REGISTRY:
            errors.append(
                f"Component '{comp.id}': unknown kind '{comp.kind}' (not in REGISTRY)"
            )

    # ------------------------------------------------------------------
    # Invariant 2: all Component.rotation values are a 45° multiple (0..315)
    # ------------------------------------------------------------------
    for comp in schematic.components:
        if comp.rotation not in _VALID_ROTATIONS:
            errors.append(
                f"Component '{comp.id}': invalid rotation {comp.rotation!r} "
                f"(must be one of {sorted(_VALID_ROTATIONS)})"
            )

    # ------------------------------------------------------------------
    # Invariant 2b: every wire has at least two points (a single segment).
    # A degenerate wire (0 or 1 points) draws nothing and index-errors the
    # canvas/codegen paths that assume points[0]/points[-1] exist.
    # ------------------------------------------------------------------
    for wire in schematic.wires:
        if len(wire.points) < 2:
            errors.append(
                f"Wire '{wire.id}': has {len(wire.points)} point(s) "
                f"(a wire must have at least two points)"
            )

    # ------------------------------------------------------------------
    # Invariant 3: all Wire.points vertices lie on 0.25 GU boundaries, with one
    # principled exception for off-grid component pins (spec §3.1). A scaled logic
    # gate's — or a 45°-rotated component's — pins sit off the grid; a wire endpoint
    # snaps onto such a pin (the magnet), and the leg approaching it carries the pin's
    # off-grid geometry into the adjacent corner. An off-grid wire vertex is therefore
    # allowed when it lies on one of the off-grid pin's **lines**:
    #   * its axis line — the same off-grid x, or the same off-grid y (Manhattan lead);
    #   * its **45° diagonal** — the same ``x − y`` or ``x + y`` (La Plata lead off a
    #     45°-rotated / off-grid pin: the diagonal comes straight off the pin, §6.4).
    # Any other off-grid value — a stray vertex unrelated to a pin — is still an error.
    # ------------------------------------------------------------------
    offgrid_pin_xs: set[float] = set()
    offgrid_pin_ys: set[float] = set()
    offgrid_pin_diag1: set[float] = set()   # x − y, for the +45° pin lines
    offgrid_pin_diag2: set[float] = set()   # x + y, for the −45° pin lines
    for comp in schematic.components:
        for px, py in component_connection_points(comp):
            off = not (_is_on_grid(px) and _is_on_grid(py))
            if not _is_on_grid(px):
                offgrid_pin_xs.add(round(px, 6))
            if not _is_on_grid(py):
                offgrid_pin_ys.add(round(py, 6))
            if off:                          # the pin's two 45° lead lines
                offgrid_pin_diag1.add(round(px - py, 6))
                offgrid_pin_diag2.add(round(px + py, 6))
    for wire in schematic.wires:
        for i, (x, y) in enumerate(wire.points):
            bad_x = not _is_on_grid(x) and round(x, 6) not in offgrid_pin_xs
            bad_y = not _is_on_grid(y) and round(y, 6) not in offgrid_pin_ys
            on_pin_diag = (round(x - y, 6) in offgrid_pin_diag1
                           or round(x + y, 6) in offgrid_pin_diag2)
            if (bad_x or bad_y) and not on_pin_diag:
                errors.append(
                    f"Wire '{wire.id}': point[{i}] ({x}, {y}) is not on a 0.25 GU boundary"
                )

    # ------------------------------------------------------------------
    # Invariant 4: every wire segment is axis-aligned (Manhattan) **or** at exactly
    # 45° (La Plata routing, spec §6.4). A 45° segment has |dx| == |dy|; any other
    # slant is still an error. (A La Plata wire's 45° corner between on-grid points
    # is itself on-grid, since a slope-±1 line threads grid nodes every 0.25 GU.)
    # ------------------------------------------------------------------
    for wire in schematic.wires:
        pts = wire.points
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            axis_aligned = x0 == x1 or y0 == y1
            diagonal_45 = abs(abs(x1 - x0) - abs(y1 - y0)) < 1e-6
            if not axis_aligned and not diagonal_45:
                errors.append(
                    f"Wire '{wire.id}': segment {i}→{i+1} "
                    f"({x0},{y0})→({x1},{y1}) is not horizontal, vertical, or 45°"
                )

    # ------------------------------------------------------------------
    # Invariant 5: no duplicate component ids; no duplicate wire ids
    # ------------------------------------------------------------------
    seen_comp_ids: set[str] = set()
    for comp in schematic.components:
        if comp.id in seen_comp_ids:
            errors.append(f"Duplicate component id '{comp.id}'")
        seen_comp_ids.add(comp.id)

    seen_wire_ids: set[str] = set()
    for wire in schematic.wires:
        if wire.id in seen_wire_ids:
            errors.append(f"Duplicate wire id '{wire.id}'")
        seen_wire_ids.add(wire.id)

    return errors
