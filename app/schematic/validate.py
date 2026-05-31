"""
Schematic validation.

validate(schematic) checks all invariants defined in spec section 4.5 and
returns a list of human-readable error strings. An empty list means the
schematic is valid.

This module has no Qt dependency and no side effects.
"""

from __future__ import annotations

from app.components.registry import REGISTRY
from app.schematic.model import Schematic

_VALID_ROTATIONS = {0, 90, 180, 270}


def _is_on_half_grid(value: float) -> bool:
    """Return True if value is a multiple of 0.5."""
    return (value * 2) == int(value * 2)


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
    # Invariant 2: all Component.rotation values are in {0, 90, 180, 270}
    # ------------------------------------------------------------------
    for comp in schematic.components:
        if comp.rotation not in _VALID_ROTATIONS:
            errors.append(
                f"Component '{comp.id}': invalid rotation {comp.rotation!r} "
                f"(must be one of {sorted(_VALID_ROTATIONS)})"
            )

    # ------------------------------------------------------------------
    # Invariant 3: all Wire.points vertices lie on 0.5 GU boundaries
    # ------------------------------------------------------------------
    for wire in schematic.wires:
        for i, (x, y) in enumerate(wire.points):
            if not _is_on_half_grid(x) or not _is_on_half_grid(y):
                errors.append(
                    f"Wire '{wire.id}': point[{i}] ({x}, {y}) is not on a 0.5 GU boundary"
                )

    # ------------------------------------------------------------------
    # Invariant 4: all consecutive wire segment pairs are horizontal or vertical
    # ------------------------------------------------------------------
    for wire in schematic.wires:
        pts = wire.points
        for i in range(len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            if x0 != x1 and y0 != y1:
                errors.append(
                    f"Wire '{wire.id}': segment {i}→{i+1} "
                    f"({x0},{y0})→({x1},{y1}) is diagonal (violates Manhattan constraint)"
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
