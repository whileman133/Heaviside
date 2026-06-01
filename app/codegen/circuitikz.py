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
  - Multi-terminal components → (x,y) node[KIND, anchor=A] (NODEID) {LABEL}
  - Wires                    → (x0,y0) -- (x1,y1) -- ...

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Y-AXIS CONVENTION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

The canvas uses Qt's Y-down convention (Y increases downward). CircuiTikZ uses
the standard mathematical Y-up convention. The preview pipeline (build_tex in
app/preview/latex.py) negates all Y coordinates in the generated source before
passing to pdflatex, so the rendered output matches the canvas orientation.

Consequence for rotation: a 90° CW rotation in Qt Y-down space maps vector
(dx,dy) → (-dy, dx). This is the convention used by both component_pin_positions
in the model and _rotate() in this file. They must stay in sync.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MULTI-TERMINAL COMPONENT PLACEMENT — DESIGN NOTES
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CircuiTikZ multi-terminal nodes (op amp, nigfete) have internal pin geometry
that does not align with our 0.5-GU grid. The canvas uses SVG lead stubs
(from tools/export_circuitikz_svgs.sh) to extend the symbol to grid-aligned
endpoints. The codegen must bridge the same gap in the LaTeX output.

op amp
------
CircuiTikZ internal pin positions from node center (measured from compiled
output, converted to GU; 1 GU = 28.348 pt):

  anchor  CTikZ Y-up from center    Qt Y-down from center
  +       (-1.194, -0.492)          (-1.194, +0.492)
  -       (-1.194, +0.492)          (-1.194, -0.492)
  out     (+1.194,  0.0)            (+1.194,  0.0)

Registry pins (Qt Y-down, from node center = comp.position):
  +   (-1.5, +0.5)
  -   (-1.5, -0.5)
  out (+1.5,  0.0)

The registry uses ±1.5 GU (from the SVG export lead stubs) rather than the
CTikZ internal ±1.194 GU. The codegen bridges this by:
1. Placing the node by center (comp.position).
2. Drawing short lead wires from each named CTikZ anchor to the registry pin
   coordinate: (node_id.+) -- (pin_coord), etc.  (_MULTI_TERMINAL_LEADS)

This ensures wires drawn to registry pin coordinates connect exactly in the
rendered output, with the short stub absorbing the internal geometry gap.

nigfete
-------
CircuiTikZ internal pin positions from node center (GU):

  anchor  CTikZ Y-up from center    Qt Y-down from center
  gate    (-0.984, -0.270)          (-0.984, +0.270)
  drain   ( 0.0,   +0.773)         ( 0.0,   -0.773)
  source  ( 0.0,   -0.773)         ( 0.0,   +0.773)

Registry pins (Qt Y-down, from gate pin = comp.position):
  gate   (0.0,   0.0)
  drain  (1.0,  -1.0)
  source (1.0,  +0.5)

The registry pins were chosen to match the CTikZ anchor positions snapped to
the nearest 0.5 GU, after placement with anchor=gate. The lead stubs in the
SVG export (tools/export_circuitikz_svgs.sh → TRIPOLE_LEADS[nigfete]) draw
to these same coordinates so the canvas symbol matches.

Because the drain/source CTikZ anchors are not rectilinearly aligned with the
registry pin positions (the leads would be diagonal), no lead wires are drawn
for drain/source in the codegen. Instead, xscale=1.0167 is applied to the
node to horizontally stretch it so the drain/source x aligns with the grid:
  CTikZ drain/source x from gate: 0.984 GU
  After xscale=1.0167:            0.984 × 1.0167 = 1.0 GU ✓

The node is placed with anchor=gate at the gate pin coordinate.

Named anchor references
-----------------------
Wire endpoints and two-terminal component terminals that coincide with a
multi-terminal pin are rendered as named anchor references (e.g.
(node_abc.gate)) instead of bare coordinates. This produces cleaner, more
readable LaTeX and makes the connection explicit. The lookup is built in
generate() as pin_coord_to_ref and threaded through _wire_line and
_two_terminal_line.
"""

from __future__ import annotations

import math
import re

from app.components.model import (
    BipoleComponent,
    DiodeComponent,
    DrawingComponent,
    MosfetComponent,
    RectComponent,
    TextNodeComponent,
)
from app.components.registry import REGISTRY
from app.components.style import compose_style_options
from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    component_pin_positions,
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
    "R", "C", "L",
    "D", "zD", "sD", "tD", "zzD", "leD",
    "V", "I", "vsourcesin", "isourcesin",
    "cV", "cI",
    "open", "short",
})

# Multi-terminal components use node[] syntax.
_MULTI_TERMINAL_KINDS: frozenset[str] = frozenset({
    "npn", "pnp",
    "op amp",
    "nigfete", "nigfetd",
    "pigfete", "pigfetd",
})

# Single-terminal node components: emitted as \node[kind] at (x,y) {};
_NODE_KINDS: frozenset[str] = frozenset({
    "ground", "sground", "cground", "rground", "nground", "pground", "eground",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(schematic: Schematic, y_flip: bool = False) -> str:
    """
    Return a CircuiTikZ environment string for *schematic*.

    Parameters
    ----------
    y_flip:
        When True, all Y coordinates are negated at emission time so the
        output is in CircuiTikZ's Y-up convention (matching the rendered PDF
        orientation).  Use this when generating source for pdflatex.

        When False (default), coordinates are emitted in the canvas Y-down
        convention.  Use this for the source panel display and file export.

    Raises ValueError if the schematic violates any invariant.
    """
    errors = validate(schematic)
    if errors:
        raise ValueError(f"Invalid schematic: {errors[0]}")

    # When y_flip=True, negate Y at the point of emission so the output is in
    # CircuiTikZ's Y-up convention.  A simple wrapper handles this uniformly.
    def _y(y: float) -> float:
        return -y if y_flip else y

    def _rot(r: int) -> int:
        return -r if y_flip else r

    lines: list[str] = []
    lines.append(r"\begin{circuitikz}")

    # Background drawing annotations (z_order < 0) — emitted before \draw so
    # they appear behind circuit elements in the rendered PDF.
    # Sorted ascending: lower z_order is drawn first (further back).
    bg = sorted(
        (c for c in schematic.components if isinstance(c, DrawingComponent) and c.z_order < 0),
        key=lambda c: c.z_order,
    )
    for comp in bg:
        if isinstance(comp, TextNodeComponent):
            lines.append("  " + _text_node_line(comp, _y))
        elif isinstance(comp, RectComponent):
            lines.append("  " + _rect_line(comp, _y))
        elif isinstance(comp, BipoleComponent):
            lines.append("  " + _bipole_node_line(comp, _y))

    lines.append(r"  \draw")

    draw_lines: list[str] = []

    # Build coordinate → node-terminal reference for multi-terminal components.
    # e.g. (78.5, 79.5) → "(node_abc123.+)"
    # Used so wire endpoints referencing component pins use named anchors.
    # Keys use pre-flip coordinates (canvas space) for lookup; the flip is
    # applied when the reference is emitted, not when the key is stored.
    pin_coord_to_ref: dict[tuple[float, float], str] = {}
    # Single pass over components: build pin_coord_to_ref, all_pin_refs,
    # and emit draw lines — avoids iterating schematic.components three times.
    all_pin_refs: dict[tuple[float, float], list[str]] = {}
    for comp in schematic.components:
        defn = REGISTRY[comp.kind]
        node_id = f"node_{comp.id[:8]}"
        pin_positions = component_pin_positions(comp)
        anchor_map = _PIN_TO_CTIKZ_ANCHOR.get(comp.kind)

        # Populate pin_coord_to_ref and all_pin_refs in the same loop.
        for i, pin in enumerate(defn.pins):
            if i >= len(pin_positions):
                continue
            px, py = pin_positions[i]
            coord = (round(px, 6), round(py, 6))
            if anchor_map and pin.name in anchor_map:
                ctikz_anchor = anchor_map[pin.name]
                ref = f"({node_id}.{ctikz_anchor})"
                pin_coord_to_ref[coord] = ref
            else:
                ref = f"({_fmt(px)},{_fmt(_y(py))})"
            all_pin_refs.setdefault(coord, []).append(ref)

        draw_lines.extend(_component_lines(comp, pin_coord_to_ref, _y, _rot))

    for wire in schematic.wires:
        draw_lines.append(_wire_line(wire, pin_coord_to_ref, _y))

    wired_coords: set[tuple[float, float]] = set()
    for wire in schematic.wires:
        pts = simplify_points(wire.points)
        if pts:
            wired_coords.add((round(pts[0][0], 6), round(pts[0][1], 6)))
            wired_coords.add((round(pts[-1][0], 6), round(pts[-1][1], 6)))

    for coord, refs in all_pin_refs.items():
        if len(refs) >= 2 and coord not in wired_coords:
            named_refs = [r for r in refs if r.startswith("(node_")]
            if len(named_refs) >= 2:
                draw_lines.append(f"{named_refs[0]} -- {named_refs[1]}")

    if draw_lines:
        for dl in draw_lines:
            lines.append(f"    {dl}")

    lines.append(r"  ;")

    # Connection dots at junctions.
    for x, y in sorted(junction_points(schematic)):
        lines.append(rf"  \node[circ] at ({_fmt(x)},{_fmt(_y(y))}) {{}};")

    # Open-circle nodes at wire endpoints not connected to any component pin.
    for x, y in sorted(open_endpoints(schematic)):
        lines.append(rf"  \node[ocirc] at ({_fmt(x)},{_fmt(_y(y))}) {{}};")

    # Foreground drawing annotations (z_order >= 0) — emitted after the draw
    # block so they appear in front of circuit elements.
    # Sorted ascending: lower z_order is drawn first (further back).
    fg = sorted(
        (c for c in schematic.components if isinstance(c, DrawingComponent) and c.z_order >= 0),
        key=lambda c: c.z_order,
    )
    for comp in fg:
        if isinstance(comp, TextNodeComponent):
            lines.append("  " + _text_node_line(comp, _y))
        elif isinstance(comp, RectComponent):
            lines.append("  " + _rect_line(comp, _y))
        elif isinstance(comp, BipoleComponent):
            lines.append("  " + _bipole_node_line(comp, _y))

    lines.append(r"\end{circuitikz}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Component rendering
# ---------------------------------------------------------------------------

def _component_lines(
    comp: Component,
    pin_coord_to_ref: dict[tuple[float, float], str] | None = None,
    y_fn=lambda y: y,
    rot_fn=lambda r: r,
) -> list[str]:
    kind = comp.kind
    if kind in _TWO_TERMINAL_KINDS:
        return [_two_terminal_line(comp, pin_coord_to_ref, y_fn)]
    elif kind in _MULTI_TERMINAL_KINDS:
        return [_multi_terminal_line(comp, y_fn, rot_fn)]
    elif kind in _NODE_KINDS:
        return [_node_line(comp, y_fn)]
    elif isinstance(comp, DrawingComponent):
        # Emitted as standalone commands outside the \draw block; nothing here.
        return []
    else:
        raise ValueError(f"Unknown component kind '{kind}'")


def _two_terminal_line(
    comp: Component,
    pin_coord_to_ref: dict[tuple[float, float], str] | None = None,
    y_fn=lambda y: y,
) -> str:
    """Render a two-terminal component as: (x0,y0) to[KIND, LABELS] (x1,y1)

    If *pin_coord_to_ref* is provided, either endpoint that coincides with a
    multi-terminal component pin is rendered as a named anchor reference.
    *y_fn* is applied to all emitted Y coordinates (used for Y-flip).
    """
    defn = REGISTRY[comp.kind]
    x0, y0 = comp.position

    base_span = comp.span_override if comp.span_override is not None else defn.default_span
    dx, dy = _rotate(base_span, comp.rotation)
    x1 = x0 + dx
    y1 = y0 + dy

    if comp.mirror:
        mdx, mdy = base_span
        mdx = -mdx
        dx, dy = _rotate((mdx, mdy), comp.rotation)
        x1 = x0 + dx
        y1 = y0 + dy

    def _ref(x: float, y: float) -> str:
        if pin_coord_to_ref:
            key = (round(x, 6), round(y, 6))
            ref = pin_coord_to_ref.get(key)
            if ref:
                return ref
        return f"({_fmt(x)},{_fmt(y_fn(y))})"

    coord0 = _ref(x0, y0)
    coord1 = _ref(x1, y1)

    label_str = _label_args(comp)
    tikz_kind = defn.tikz_keyword + ("*" if isinstance(comp, DiodeComponent) and comp.filled else "")
    to_arg = tikz_kind
    if label_str:
        to_arg = f"{tikz_kind}, {label_str}"

    return f"{coord0} to[{to_arg}] {coord1}"


def _multi_terminal_line(
    comp: Component,
    y_fn=lambda y: y,
    rot_fn=lambda r: r,
) -> str:
    """Render a multi-terminal component.

    *y_fn* and *rot_fn* are applied to all emitted Y coordinates and rotation
    angles respectively (used for Y-flip in preview output).
    """
    defn = REGISTRY[comp.kind]
    node_id = f"node_{comp.id[:8]}"
    pin_positions = component_pin_positions(comp)

    kind_arg = comp.kind
    if isinstance(comp, MosfetComponent) and comp.body_diode:
        kind_arg = f"{kind_arg}, bodydiode"
    extra_opts = _MULTI_TERMINAL_EXTRA_OPTS.get(comp.kind, "")
    if extra_opts:
        kind_arg = f"{kind_arg}, {extra_opts}"
    rotation = rot_fn(comp.rotation)
    if rotation != 0:
        kind_arg = f"{kind_arg}, rotate={rotation}"
    if comp.mirror:
        if extra_opts and "xscale=" in extra_opts:
            kind_arg = re.sub(
                r"xscale=([\d.]+)",
                lambda m: f"xscale=-{m.group(1)}",
                kind_arg,
                count=1,
            )
        else:
            kind_arg = f"{kind_arg}, xscale=-1"

    # Append user options to the node[] argument.
    user_opts = _label_args(comp)
    if user_opts:
        kind_arg = f"{kind_arg}, {user_opts}"

    # Determine placement coordinate and anchor option.
    anchor_info = _MULTI_TERMINAL_ANCHOR_PIN.get(comp.kind)
    if anchor_info:
        ctikz_anchor_name, registry_pin_name = anchor_info
        pin_index = next(
            (i for i, p in enumerate(defn.pins) if p.name == registry_pin_name), None
        )
        if pin_index is not None and pin_index < len(pin_positions):
            px, py = pin_positions[pin_index]
            coord = f"({_fmt(px)},{_fmt(y_fn(py))})"
            kind_arg = f"{kind_arg}, anchor={ctikz_anchor_name}"
        else:
            x, y = comp.position
            coord = f"({_fmt(x)},{_fmt(y_fn(y))})"
    else:
        x, y = comp.position
        coord = f"({_fmt(x)},{_fmt(y_fn(y))})"

    node_line = f"{coord} node[{kind_arg}] ({node_id}) {{}}"

    # Append lead wires if defined for this kind.
    leads = _MULTI_TERMINAL_LEADS.get(comp.kind, [])
    if not leads:
        return node_line

    lines = [node_line]
    for ctikz_anchor, pin_name in leads:
        pin_index = next(
            (i for i, p in enumerate(defn.pins) if p.name == pin_name), None
        )
        if pin_index is not None and pin_index < len(pin_positions):
            px, py = pin_positions[pin_index]
            lines.append(
                f"({node_id}.{ctikz_anchor}) -- ({_fmt(px)},{_fmt(y_fn(py))})"
            )
    return "\n    ".join(lines)


# Map from component kind → list of (circuitikz_anchor_name, registry_pin_name)
# A short lead wire is drawn from each named CircuiTikZ anchor to the
# corresponding registry pin coordinate, bridging the gap between the node's
# internal geometry and the canvas grid.
_MULTI_TERMINAL_LEADS: dict[str, list[tuple[str, str]]] = {
    "op amp":  [("+", "+"), ("-", "-"), ("out", "out")],
    # BJTs: xscale/yscale corrections align C/E anchors exactly with the grid,
    # so no bridge lead wires are needed (same strategy as MOSFETs).
    "npn": [],
    "pnp": [],
    # MOSFETs: placed by anchor=gate (exact gate connection). Drain/source
    # CTikZ anchors are not rectilinearly aligned with the registry grid pins,
    # so no lead wires are drawn. xscale=1.0167 corrects the x offset instead.
    "nigfete": [],
    "nigfetd": [],
    "pigfete": [],
    "pigfetd": [],
}

# Components placed by a specific named anchor rather than by center.
# Maps kind → (ctikz_anchor_name, registry_pin_name).
# The node is placed so ctikz_anchor_name coincides with the registry pin coordinate.
_MULTI_TERMINAL_ANCHOR_PIN: dict[str, tuple[str, str]] = {
    "npn": ("B", "base"),
    "pnp": ("B", "base"),
    "nigfete": ("gate", "gate"),
    "nigfetd": ("gate", "gate"),
    "pigfete": ("gate", "gate"),
    "pigfetd": ("gate", "gate"),
}

# Extra node options injected into the node[] argument for specific kinds.
# Used to correct geometry mismatches between CTikZ internal coords and our grid.
# All IGFET variants: drain/source x is 0.9836 GU from gate; xscale=1.0167 stretches to 1.0 GU.
_MULTI_TERMINAL_EXTRA_OPTS: dict[str, str] = {
    # BJTs: actual CTikZ C/E offset from base = (0.847, ±0.777) GU (measured from
    # the bare-node SVG export).  xscale and yscale stretch to the snapped (1.0, ±1.0)
    # GU grid so C/E anchors land on-grid without needing bridge lead wires.
    #   xscale = 1.0 / 0.8471 = 1.181
    #   yscale = 1.0 / 0.7770 = 1.287
    "npn": "xscale=1.181, yscale=1.287",
    "pnp": "xscale=1.181, yscale=1.287",
    "nigfete": "xscale=1.0167",
    "nigfetd": "xscale=1.0167",
    "pigfete": "xscale=1.0167",
    "pigfetd": "xscale=1.0167",
}

# Maps registry pin name → CTikZ anchor name for each multi-terminal kind.
# Used to substitute wire endpoint coordinates with named node references.
_PIN_TO_CTIKZ_ANCHOR: dict[str, dict[str, str]] = {
    "op amp":  {"+": "+", "-": "-", "out": "out"},
    "npn": {"base": "B", "collector": "C", "emitter": "E"},
    "pnp": {"base": "B", "collector": "C", "emitter": "E"},
    "nigfete": {"gate": "gate", "drain": "drain", "source": "source"},
    "nigfetd": {"gate": "gate", "drain": "drain", "source": "source"},
    "pigfete": {"gate": "gate", "drain": "drain", "source": "source"},
    "pigfetd": {"gate": "gate", "drain": "drain", "source": "source"},
}


# ---------------------------------------------------------------------------
# Startup validation: every multi-terminal kind must have a _PIN_TO_CTIKZ_ANCHOR
# entry so named anchor references are emitted correctly. Missing entries cause
# silent fallback to bare coordinates, producing hard-to-diagnose misalignment.
def _validate_codegen_tables() -> None:
    """Check that every multi-terminal kind in the registry has a
    _PIN_TO_CTIKZ_ANCHOR entry.  Kinds in _MULTI_TERMINAL_KINDS that are not
    yet in the registry are skipped — they may be planned for future use.
    """
    for kind in _MULTI_TERMINAL_KINDS:
        if kind not in REGISTRY:
            continue  # not yet registered; skip
        if kind not in _PIN_TO_CTIKZ_ANCHOR:
            raise RuntimeError(
                f"Multi-terminal kind {kind!r} is in the registry but missing "
                f"from _PIN_TO_CTIKZ_ANCHOR in app/codegen/circuitikz.py. "
                f"Add an entry before using this kind."
            )

_validate_codegen_tables()


# ---------------------------------------------------------------------------
# Node rendering (single-terminal: ground, sground, etc.)
# ---------------------------------------------------------------------------

def _node_line(comp: Component, y_fn=lambda y: y) -> str:
    """Render a single-terminal node as: (x,y) node[kind, rotate=R] {}"""
    x, y = comp.position
    rotate_arg = ""
    if comp.rotation:
        rotate_arg = f", rotate={comp.rotation}"
    return f"({_fmt(x)},{_fmt(y_fn(y))}) node[{comp.kind}{rotate_arg}] {{}}"


# ---------------------------------------------------------------------------
# Wire rendering
# ---------------------------------------------------------------------------

def _wire_line(
    wire: Wire,
    pin_coord_to_ref: dict[tuple[float, float], str] | None = None,
    y_fn=lambda y: y,
) -> str:
    """Render a wire as: (x0,y0) -- (x1,y1) -- ...

    *y_fn* is applied to all emitted Y coordinates (used for Y-flip).
    Named anchor substitution applies to endpoints only.
    """
    pts = simplify_points(wire.points)
    refs: list[str] = []
    for i, (x, y) in enumerate(pts):
        if pin_coord_to_ref and (i == 0 or i == len(pts) - 1):
            key = (round(x, 6), round(y, 6))
            ref = pin_coord_to_ref.get(key)
            if ref:
                refs.append(ref)
                continue
        refs.append(f"({_fmt(x)},{_fmt(y_fn(y))})")
    return " -- ".join(refs)


# ---------------------------------------------------------------------------
# Drawing annotation rendering
# ---------------------------------------------------------------------------

_FONT_FAMILY_CMD: dict[str, str] = {
    "serif": r"\rmfamily",
    "sans":  r"\sffamily",
    "mono":  r"\ttfamily",
}


def _text_node_line(comp: "TextNodeComponent", y_fn=lambda y: y) -> str:
    r"""Render a text annotation as \node[...] at (x,y) {text};

    Font size (``font_size``), bold (``font_bold``), italic (``font_italic``),
    and family (``font_family``) are all encoded into the ``font=`` node option
    when any of them is set.
    """
    x, y = comp.position
    text = comp.options

    has_size   = comp.font_size != 12.0
    has_style  = comp.font_bold or comp.font_italic or bool(comp.font_family)

    if has_size or has_style:
        parts: list[str] = []
        if has_size:
            fs = comp.font_size
            leading = round(fs * 1.2, 2)
            leading_str = (
                str(int(leading)) if leading == int(leading)
                else f"{leading:.1f}" if (leading * 10 == int(leading * 10))
                else f"{leading:.2f}"
            )
            parts.append(rf"\fontsize{{{_fmt(fs)}}}{{{leading_str}}}\selectfont")
        if comp.font_bold:
            parts.append(r"\bfseries")
        if comp.font_italic:
            parts.append(r"\itshape")
        family_cmd = _FONT_FAMILY_CMD.get(comp.font_family, "")
        if family_cmd:
            parts.append(family_cmd)
        opts = r"[font=" + "".join(parts) + "]"
    else:
        opts = ""

    # Negate rotation: stored rotation follows CW-visually convention (matching
    # circuit components on canvas), but TikZ rotate= is CCW (standard math).
    tikz_rotation = (-comp.rotation) % 360
    rotate_opt = f", rotate={tikz_rotation}" if tikz_rotation else ""
    if rotate_opt:
        if opts:
            opts = opts[:-1] + rotate_opt + "]"
        else:
            opts = f"[rotate={tikz_rotation}]"

    return rf"\node{opts} at ({_fmt(x)},{_fmt(y_fn(y))}) {{{text}}};"


def _rect_line(comp: Component, y_fn=lambda y: y) -> str:
    r"""Render a rectangle annotation as \draw[style] (x1,y1) rectangle (x2,y2);

    The draw style (line style, line width, fill) comes from the
    StyledComponent fields.  The second corner is computed from
    ``comp.span_override`` (falling back to ``default_span`` from the registry).
    """
    defn = REGISTRY[comp.kind]
    x1, y1 = comp.position
    so = comp.span_override if comp.span_override is not None else defn.default_span
    dx, dy = so
    x2, y2 = x1 + dx, y1 + dy

    style = compose_style_options(
        fill_color=comp.fill_color,
        border_width=comp.border_width,
        line_style=comp.line_style,
    )
    style_arg = f"[{style}]" if style else ""

    return (
        rf"\draw{style_arg} ({_fmt(x1)},{_fmt(y_fn(y1))}) "
        rf"rectangle ({_fmt(x2)},{_fmt(y_fn(y2))});"
    )


_BIPOLE_HALF_H_GU = 0.25  # must match canvas/items.py _BIPOLE_HALF_H


def _bipole_node_line(comp: "BipoleComponent", y_fn=lambda y: y) -> str:
    r"""Render a bipole element as \node[draw, minimum width=W, minimum height=H] at (cx,cy) {label};

    The node dimensions are derived from ``span_override`` so the box exactly
    fills the space between the two pin coordinates — resizing the component on
    the canvas directly controls the box size in the output.

    Label text is taken from the ``t=`` slot in ``comp.options``; other slots
    are stored in options but are not rendered on the standalone node.
    """
    defn = REGISTRY[comp.kind]
    x0, y0 = comp.position
    so = comp.span_override if comp.span_override is not None else defn.default_span

    # Span length is invariant under rotation and mirror.
    span_len = math.hypot(so[0], so[1])

    # Compute actual terminal offset (mirror then rotate, same as _two_terminal_line).
    dx, dy = so
    if comp.mirror:
        dx = -dx
    rdx, rdy = _rotate((dx, dy), comp.rotation)
    x1, y1 = x0 + rdx, y0 + rdy

    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    height_gu = _BIPOLE_HALF_H_GU * 2  # 0.5 GU = 0.5 cm

    # TikZ rotation is CCW; canvas rotation is CW.
    tikz_rot = (-comp.rotation) % 360
    rotate_opt = f", rotate={tikz_rot}" if tikz_rot else ""

    m = re.search(r'\bt\s*=\s*([^,]+)', comp.options)
    label = m.group(1).strip() if m else ""

    fs = comp.font_size
    leading = round(fs * 1.2, 1)
    leading_str = str(int(leading)) if leading == int(leading) else f"{leading:.1f}"
    fs_str = str(int(fs)) if fs == int(fs) else f"{fs:.1f}"
    font_cmds = rf"\fontsize{{{fs_str}}}{{{leading_str}}}\selectfont"
    if comp.font_bold:
        font_cmds += r"\bfseries"
    if comp.font_italic:
        font_cmds += r"\itshape"
    if comp.font_family in _FONT_FAMILY_CMD:
        font_cmds += _FONT_FAMILY_CMD[comp.font_family]
    font_opt = rf", font={font_cmds}"

    style = compose_style_options(
        fill_color=comp.fill_color,
        border_width=comp.border_width,
        line_style=comp.line_style,
    )
    extra_opts = f", {style}" if style else ""

    return (
        rf"\node[draw, minimum width={_fmt(span_len)}cm, "
        rf"minimum height={_fmt(height_gu)}cm{rotate_opt}{font_opt}{extra_opts}] "
        rf"at ({_fmt(cx)},{_fmt(y_fn(cy))}) {{{label}}};"
    )


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
    """Return the raw options string from comp.options, stripped of whitespace."""
    return comp.options.strip()


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
