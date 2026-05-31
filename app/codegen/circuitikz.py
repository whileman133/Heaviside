"""
CircuiTikZ code generator.

generate(schematic) -> str

Pure function: no side effects, no global state. The same Schematic always
produces the same output. Raises ValueError if the schematic violates any
invariant.

Output format (spec §7.1):

    \\begin{circuitikz}
      \\draw
        % wires and components
      ;
    \\end{circuitikz}

Mapping rules (spec §7.2):
  - Two-terminal components  → (x0,y0) to[KIND, LABELS] (x1,y1)
  - Multi-terminal components → (x,y) node[KIND, rotate=ROT] (NODEID) {LABEL}
  - Wires                    → (x0,y0) -- (x1,y1) -- ...
"""

from __future__ import annotations

from app.components.registry import REGISTRY
from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    junction_points,
    open_endpoints,
    simplify_points,
)
from app.schematic.validate import validate

# ---------------------------------------------------------------------------
# Component classification
# ---------------------------------------------------------------------------

# Two-terminal components use to[] path syntax.
_TWO_TERMINAL_KINDS: frozenset[str] = frozenset({
    "R", "C", "L", "D",
    "V", "I", "vsource", "isource",
    "cV", "cI",
})

# Multi-terminal components use node[] syntax.
_MULTI_TERMINAL_KINDS: frozenset[str] = frozenset({
    "op amp",
    "nigfete",
    "nmos", "pmos",
    "nmos, bodydiode", "pmos, bodydiode",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(schematic: Schematic) -> str:
    """
    Return a CircuiTikZ environment string for *schematic*.

    Raises ValueError if the schematic violates any invariant.
    """
    errors = validate(schematic)
    if errors:
        raise ValueError(f"Invalid schematic: {errors[0]}")

    lines: list[str] = []
    lines.append(r"\begin{circuitikz}")
    lines.append(r"  \draw")

    draw_lines: list[str] = []

    for comp in schematic.components:
        draw_lines.extend(_component_lines(comp))

    for wire in schematic.wires:
        draw_lines.append(_wire_line(wire))

    if draw_lines:
        # All but the last get a trailing space (continuation); last gets none.
        for i, dl in enumerate(draw_lines):
            suffix = "" if i == len(draw_lines) - 1 else ""
            lines.append(f"    {dl}")

    lines.append(r"  ;")

    # Connection dots at junctions (3+ wires, or a pin + 2+ wires). These are
    # standalone \node[circ] statements, not part of the \draw path above.
    for x, y in sorted(junction_points(schematic)):
        lines.append(rf"  \node[circ] at ({_fmt(x)},{_fmt(y)}) {{}};")

    # Open-circle nodes at wire endpoints not connected to any component pin.
    for x, y in sorted(open_endpoints(schematic)):
        lines.append(rf"  \node[ocirc] at ({_fmt(x)},{_fmt(y)}) {{}};")

    lines.append(r"\end{circuitikz}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Component rendering
# ---------------------------------------------------------------------------

def _component_lines(comp: Component) -> list[str]:
    kind = comp.kind
    if kind in _TWO_TERMINAL_KINDS:
        return [_two_terminal_line(comp)]
    elif kind in _MULTI_TERMINAL_KINDS:
        return [_multi_terminal_line(comp)]
    else:
        # Unknown kind — validate() would have caught this, but be safe.
        raise ValueError(f"Unknown component kind '{kind}'")


def _two_terminal_line(comp: Component) -> str:
    """Render a two-terminal component as: (x0,y0) to[KIND, LABELS] (x1,y1)"""
    defn = REGISTRY[comp.kind]
    x0, y0 = comp.position

    # Rotate the default_span vector by comp.rotation (clockwise).
    dx, dy = _rotate(defn.default_span, comp.rotation)
    x1 = x0 + dx
    y1 = y0 + dy

    # Apply mirror: flip the span direction (horizontal mirror before rotation
    # means we negate dx of the unrotated span, but since we already rotated,
    # we negate x component of the result for horizontal mirror).
    # Per spec §4.2: mirror is horizontal mirror *before* rotation.
    # Re-compute with mirror applied before rotation.
    if comp.mirror:
        mdx, mdy = defn.default_span
        mdx = -mdx  # horizontal mirror negates x
        dx, dy = _rotate((mdx, mdy), comp.rotation)
        x1 = x0 + dx
        y1 = y0 + dy

    coord0 = f"({_fmt(x0)},{_fmt(y0)})"
    coord1 = f"({_fmt(x1)},{_fmt(y1)})"

    label_str = _label_args(comp)
    to_arg = comp.kind
    if label_str:
        to_arg = f"{comp.kind}, {label_str}"

    return f"{coord0} to[{to_arg}] {coord1}"


def _multi_terminal_line(comp: Component) -> str:
    """Render a multi-terminal component as: (x,y) node[KIND, rotate=ROT] (NODEID) {LABEL}"""
    x, y = comp.position
    coord = f"({_fmt(x)},{_fmt(y)})"

    node_id = f"node_{comp.id[:8]}"

    kind_arg = comp.kind
    if comp.rotation != 0:
        kind_arg = f"{comp.kind}, rotate={comp.rotation}"
    if comp.mirror:
        kind_arg = f"{kind_arg}, xscale=-1"

    label_text = comp.labels.get("l", "")

    return f"{coord} node[{kind_arg}] ({node_id}) {{{label_text}}}"


# ---------------------------------------------------------------------------
# Wire rendering
# ---------------------------------------------------------------------------

def _wire_line(wire: Wire) -> str:
    """Render a wire as: (x0,y0) -- (x1,y1) -- ...

    Redundant collinear / duplicate vertices are collapsed first so the emitted
    path uses the minimum number of nodes (e.g. a straight wire is two points,
    never three). This is defensive — the model is normalized too — so output
    stays minimal regardless of how the wire was built.
    """
    coords = [f"({_fmt(x)},{_fmt(y)})" for x, y in simplify_points(wire.points)]
    return " -- ".join(coords)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rotate(span: tuple[float, float], rotation: int) -> tuple[float, float]:
    """
    Apply a clockwise rotation of *rotation* degrees to vector *span*,
    in Qt's Y-down coordinate system.

    In Y-down space, CW rotation maps:
      0°:   (dx, dy)  →  ( dx,  dy)
      90°:  (dx, dy)  →  (-dy,  dx)   ← matches component_pin_positions
      180°: (dx, dy)  →  (-dx, -dy)
      270°: (dx, dy)  →  ( dy, -dx)
    """
    dx, dy = span
    if rotation == 0:
        return (dx, dy)
    elif rotation == 90:
        return (-dy, dx)
    elif rotation == 180:
        return (-dx, -dy)
    elif rotation == 270:
        return (dy, -dx)
    else:
        raise ValueError(f"Invalid rotation {rotation!r}")


def _label_args(comp: Component) -> str:
    """
    Build the label portion of a to[] argument from comp.labels.

    Only non-empty label values are included. Order follows the order keys
    appear in comp.labels (insertion order in Python 3.7+).
    """
    parts = [f"{slot}={value}" for slot, value in comp.labels.items() if value]
    return ", ".join(parts)


def _fmt(value: float) -> str:
    """
    Format a coordinate value per spec §7.3:
    - Integers output without decimal point (e.g. 2, not 2.0).
    - Half-integers output without trailing zero (e.g. 1.5, not 1.50).
    - Other values rounded to 2 decimal places.
    """
    # Check if it's an integer value.
    if value == int(value):
        return str(int(value))
    # Check if it's a half-integer (multiple of 0.5).
    doubled = value * 2
    if doubled == int(doubled):
        # Format with one decimal place, no trailing zero needed.
        return f"{value:.1f}"
    # General case: 2 decimal places.
    return f"{value:.2f}"
