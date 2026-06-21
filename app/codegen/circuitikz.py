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
MULTI-TERMINAL COMPONENT PLACEMENT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CircuiTikZ multi-terminal nodes (op amp, MOSFETs, BJTs) have internal pin
anchors that do not land on the 0.25-GU grid.  Alignment is **scale-only** (§4):
every node is centre-placed and a per-axis ``xscale=…, yscale=…`` lands its
anchors on the grid (pins that can't reach it stay off-grid, reached by the
magnet).  Connecting wires reference each pin's named anchor ``(node.anchor)``.
There are no ``anchor=`` placements and no lead bridges.

These placement/alignment tables (``_MULTI_TERMINAL_KINDS``,
``_MULTI_TERMINAL_EXTRA_OPTS``, ``_PIN_TO_CTIKZ_ANCHOR``)
are **derived** from ``components/definitions.json`` via
``app.components.library.build_codegen_tables`` — they are not hand-maintained.
The canvas (``app/canvas/svgsym.py``) draws the same scaled symbol (baked into the
geometry by ``components/generate_components.py``), so the canvas and the LaTeX agree.
See ``spec/component-pipeline.md``.

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
from typing import NamedTuple

from app.components import library as _library
from app.components.model import (
    BipoleComponent,
    CircleComponent,
    DrawingComponent,
    RectComponent,
    TextNodeComponent,
)
from app.components.registry import REGISTRY
from app.components.style import (
    balance_braces,
    compose_style_options,
    protect_label_values,
    split_top_level,
)
from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    component_pin_positions,
    junction_points,
    open_endpoints,
    unconnected_pins,
    simplify_points,
    wire_crossings,
    wire_point_at_fraction,
)
from app.schematic.validate import validate

import copy as _copy


def _translate_to_origin(schematic: Schematic) -> Schematic:
    """Return a copy of *schematic* shifted so its drawn extent starts near (0,0).

    The canvas places schematics in the middle of a large scene, so stored
    coordinates are typically offset by tens of grid units from the origin. That
    is invisible in the rendered figure (CircuiTikZ output is cropped to its
    bounding box), but it makes the generated source needlessly hard to read and
    hand-edit. This translates every *absolute* coordinate — component
    ``position`` and wire ``points`` — by a whole-GU amount so the schematic's
    minimum corner sits at the origin, while leaving *relative* values
    (``span_override``, ``label_offset``, pin offsets) untouched.

    The shift is computed from component pin positions and wire vertices so a
    component whose body extends left/down of its origin is not pushed negative.
    It is an integer number of GU, so grid alignment is preserved exactly. An
    empty schematic (no coordinates) is returned unchanged.
    """
    xs: list[float] = []
    ys: list[float] = []
    for comp in schematic.components:
        for px, py in component_pin_positions(comp):
            xs.append(px)
            ys.append(py)
        # Components with no named pins (rect/circle/text) still occupy their
        # origin; include it so they anchor the bounding box too.
        xs.append(comp.position[0])
        ys.append(comp.position[1])
    for wire in schematic.wires:
        for px, py in wire.points:
            xs.append(px)
            ys.append(py)

    if not xs:
        return schematic

    # Shift by a whole number of GU so the result stays grid-aligned. floor()
    # guarantees the minimum corner lands at or just above 0 without introducing
    # a fractional offset.
    dx = math.floor(min(xs))
    dy = math.floor(min(ys))
    if dx == 0 and dy == 0:
        return schematic

    shifted = _copy.deepcopy(schematic)
    for comp in shifted.components:
        comp.position = (comp.position[0] - dx, comp.position[1] - dy)
    for wire in shifted.wires:
        wire.points = [(x - dx, y - dy) for (x, y) in wire.points]
    return shifted

# ---------------------------------------------------------------------------
# Component classification
#
# These tables are derived from the component data file (components/definitions.json
# via app/components/library.py), not hand-maintained.  The library carries every
# CircuiTikZ symbol's emission mode, pin→anchor mapping, and alignment (scale /
# lead stubs); the two-terminal annotations open/short are bespoke (no symbol in
# the file) so they are merged in here.  See spec/component-pipeline.md.
# ---------------------------------------------------------------------------

_CODEGEN_TABLES = _library.build_codegen_tables()

# Two-terminal components use to[] path syntax.  open/short are bespoke.
_TWO_TERMINAL_KINDS: frozenset[str] = frozenset(
    _CODEGEN_TABLES["two_terminal_kinds"] | {"open", "short"}
)

# Diode-family bipoles.  CircuiTikZ's default diode body is visually large
# relative to the other bipoles, so it is scaled down by DIODE_SYMBOL_SCALE via
# a picture-scoped ``\ctikzset{diodes/scale=…}``.  The canvas SVG assets are
# exported at the same scale (see components/generate_components.py) so the canvas
# and the rendered output stay in sync (§5.3 / §7.2).
_DIODE_KINDS: frozenset[str] = frozenset(_CODEGEN_TABLES["diode_kinds"])

#: Body-scale factor applied to every diode in both the output and the canvas.
#: Single-sourced in app/components/library.py (re-exported here under the
#: historical public name); the renderer (app/components/generate.py)
#: reads the same constant, so the two cannot drift.
DIODE_SYMBOL_SCALE: float = _library.DIODE_SYMBOL_SCALE

# Multi-terminal components use node[] syntax.
_MULTI_TERMINAL_KINDS: frozenset[str] = frozenset(_CODEGEN_TABLES["multi_terminal_kinds"])

# Single-terminal node components: emitted as \node[kind] at (x,y) {};
_NODE_KINDS: frozenset[str] = frozenset(_CODEGEN_TABLES["node_kinds"])


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(
    schematic: Schematic,
    y_flip: bool = False,
    mark_unconnected_pins: bool = False,
    mark_line_hops: bool = False,
) -> str:
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
    mark_unconnected_pins:
        When True, an open circle (``\\node[ocirc]``) is emitted at every
        component pin that nothing connects to (see ``unconnected_pins``).  This
        is a display preference; it defaults to False so output is unchanged
        unless explicitly requested.
    mark_line_hops:
        When True, a small semicircular bump is drawn on the higher-``z_order``
        wire wherever two wires cross without connecting (see ``wire_crossings``).
        A display preference; defaults to False so output is unchanged unless
        requested (the app passes the user's preference, which defaults on).

    Raises ValueError if the schematic violates any invariant.
    """
    errors = validate(schematic)
    if errors:
        raise ValueError(f"Invalid schematic: {errors[0]}")

    # Shift the schematic so its source coordinates start near the origin rather
    # than wherever it happened to sit on the canvas (the figure is unchanged —
    # CircuiTikZ crops to the bounding box — but the source is far more readable).
    schematic = _translate_to_origin(schematic)

    # When y_flip=True, negate Y at the point of emission so the output is in
    # CircuiTikZ's Y-up convention.  A simple wrapper handles this uniformly.
    def _y(y: float) -> float:
        return -y if y_flip else y

    def _rot(r: int) -> int:
        return -r if y_flip else r

    lines: list[str] = []
    lines.append(r"\begin{circuitikz}")

    # Shrink the (visually large) default diode body to better match the other
    # bipoles, scoped to this picture so it never leaks into the user's other
    # figures.  Emitted only when a diode is present; the canvas SVGs are
    # exported at the same scale so the two stay in sync (§5.3 / §7.2).
    if any(c.kind in _DIODE_KINDS for c in schematic.components):
        lines.append(rf"  \ctikzset{{diodes/scale={DIODE_SYMBOL_SCALE:g}}}")

    # Document voltage/current label conventions (§4 / §7.2). Applied **per
    # annotated component** as a local `voltage=european` / `current=european`
    # option (see _annotation_style_opts), NOT as a global `\ctikzset`: the global
    # form also restyles some component *symbols*, but Heaviside provides separate
    # american/european symbols as distinct components, so the convention must only
    # affect the v=/i= annotation arrows.
    voltage_european = getattr(schematic, "voltage_style", "american") == "european"
    current_european = getattr(schematic, "current_style", "american") == "european"

    # Line-hops (decoration where wires cross without connecting, §6.4).
    # PROTOTYPE (Option B): each crossing becomes a CircuiTikZ `jump crossing`
    # node placed at the crossing point, and **both** wires are broken to connect
    # to its anchors — the node draws the hop arc on the hopper and the gap on the
    # crossed wire. We build, per crossing: one node record, and a "break" on each
    # of the two wires recording which node + role (hopper/crossed) so the wire
    # emitter can route its arms to the right anchors.
    crossing_nodes: list[tuple[str, tuple[float, float], str]] = []
    breaks_by_wire: dict[str, list[_Break]] = {}
    for k, hop in enumerate(wire_crossings(schematic, default_on=mark_line_hops)):
        node_id = f"xing{k}"
        crossing_nodes.append((node_id, hop.point, hop.orientation))
        breaks_by_wire.setdefault(hop.wire_id, []).append(
            _Break(hop.point, node_id, "hopper", hop.orientation)
        )
        if hop.crossed_wire_id is not None:
            breaks_by_wire.setdefault(hop.crossed_wire_id, []).append(
                _Break(hop.point, node_id, "crossed", hop.orientation)
            )

    # Coordinate → node-terminal reference for multi-terminal component pins,
    # e.g. (78.5, 79.5) → "(node_abc123.+)", plus all pin refs per coordinate.
    # Built up front (before the z-layer blocks) so background/foreground wires
    # can use named anchors too. Keys use pre-flip (canvas) coordinates.
    pin_coord_to_ref: dict[tuple[float, float], str] = {}
    all_pin_refs: dict[tuple[float, float], list[str]] = {}
    for comp in schematic.components:
        # Only default-layer (z_order == 0) components define named anchors. A
        # re-layered component is emitted in its own \draw before/after the main
        # block, so a named reference to it from a wire in another layer could be a
        # forward reference (compile error). Wires connecting to a layered
        # component fall back to absolute pin coordinates instead, which connect
        # exactly (see the module docstring), so nothing is lost geometrically.
        if comp.z_order != 0:
            continue
        defn = REGISTRY[comp.kind]
        node_id = f"node_{comp.id[:8]}"
        pin_positions = component_pin_positions(comp)
        anchor_map = _PIN_TO_CTIKZ_ANCHOR.get(comp.kind)
        # A scaled logic gate's pins sit at the true scaled node anchor (no lead
        # stub), so a connecting wire's endpoint — snapped onto that pin by the
        # magnet — is exactly the node anchor and maps to `(node.anchor)` like any
        # other multi-terminal pin.  ``pin_positions`` already carries the scaled
        # (off-grid) coordinate, so the lookup key matches the wire endpoint.
        for i, pin in enumerate(defn.pins):
            if i >= len(pin_positions):
                continue
            px, py = pin_positions[i]
            coord = (round(px, 6), round(py, 6))
            if anchor_map and pin.name in anchor_map:
                ref = f"({node_id}.{anchor_map[pin.name]})"
                pin_coord_to_ref[coord] = ref
            else:
                ref = f"({_fmt(px)},{_fmt(_y(py))})"
            all_pin_refs.setdefault(coord, []).append(ref)

    def _wire_layer_line(wire: Wire, *, use_refs: bool) -> str:
        # Background-layer wires are emitted *before* the main \draw block where
        # multi-terminal component nodes (op amp, MOSFET, BJT) are defined, so a
        # named-anchor reference like (node_abc.gate) would point at a node that
        # does not exist yet → a LaTeX compile error. Such wires fall back to
        # absolute coordinates (use_refs=False); the registry pin coords already
        # connect exactly via the scaled anchor (see module docstring), so there
        # is no geometric loss. Foreground wires come after and keep named refs.
        refs = pin_coord_to_ref if use_refs else None
        return _wire_draw_statement(wire, breaks_by_wire.get(wire.id, []), refs, _y)

    def _emit_layer_component(comp: Component) -> list[str]:
        """Layer-block lines for one component: a drawing annotation via its
        standalone-command path, any other (circuit) kind via its own \\draw."""
        if isinstance(comp, DrawingComponent):
            return _drawing_component_lines(comp, _y)
        return _component_layer_lines(
            comp, _y, _rot,
            voltage_european=voltage_european, current_european=current_european,
        )

    # Crossing nodes (jump crossings) are emitted up front, before any \draw that
    # references their anchors (TikZ has no forward node references). A vertical
    # hopper needs the node rotated 90° so its arc lands on the vertical arm.
    for node_id, (cx, cy), orientation in crossing_nodes:
        opts = "jump crossing" + (", rotate=90" if orientation == "v" else "")
        lines.append(
            rf"  \node[{opts}] ({node_id}) at ({_fmt(cx)},{_fmt(_y(cy))}) {{}};"
        )

    # Background layer (z_order < 0): components (drawing annotations *and* plain
    # circuit kinds) and wires, emitted before \draw so they sit behind the main
    # circuit. Sorted ascending by (z_order, document order) so lower/earlier
    # items render furthest back.
    bg_items: list[tuple[int, int, str, object]] = []
    for i, comp in enumerate(schematic.components):
        if comp.z_order < 0:
            bg_items.append((comp.z_order, i, "c", comp))
    for i, wire in enumerate(schematic.wires):
        if wire.z_order < 0 and len(wire.points) >= 2:
            bg_items.append((wire.z_order, i, "w", wire))
    bg_items.sort(key=lambda t: (t[0], t[1]))
    for _z, _i, _kind, obj in bg_items:
        if _kind == "c":
            lines.extend(_emit_layer_component(obj))
        else:
            # Absolute coords: the multi-terminal nodes aren't defined yet.
            lines.append("  " + _wire_layer_line(obj, use_refs=False))

    # Nodes that need a local \ctikzset (a gate's body height, a cute/european
    # transformer's inductor shape) are emitted first, each in its own group so the
    # setting reverts afterward; their (global) node names then resolve for wires in
    # the main \draw. A re-layered (z_order != 0) node is emitted in its own layer
    # block instead, so skip it here.
    for comp in schematic.components:
        if comp.z_order == 0 and _node_group_ctikzset(comp):
            lines.extend(_node_group_lines(comp, _y, _rot))

    lines.append(r"  \draw")

    draw_lines: list[str] = []
    for comp in schematic.components:
        if comp.z_order != 0:
            continue  # emitted in a background/foreground layer block
        if _node_group_ctikzset(comp):
            continue  # already emitted in its own group above
        draw_lines.extend(_component_lines(
            comp, pin_coord_to_ref, _y, _rot,
            voltage_european=voltage_european, current_european=current_european,
        ))

    # Wires at the default layer (z_order == 0) share the main \draw path;
    # non-zero-z wires are emitted in the background/foreground layers instead.
    styled_wire_lines: list[str] = []
    for wire in schematic.wires:
        # Skip degenerate wires (fewer than two points): they have no segment to
        # draw and would emit a stray lone coordinate in the \draw path.
        if len(wire.points) < 2 or wire.z_order != 0:
            continue
        breaks = breaks_by_wire.get(wire.id, [])
        style = compose_style_options(
            line_style=wire.line_style, line_width=wire.line_width
        )
        arrow = _wire_arrow_spec(wire)
        # The arrow spec must lead the option list (``-{Latex}, dashed`` not the
        # reverse) so PGF parses it as the path's arrow specification.
        opts = ", ".join(p for p in (arrow, style) if p)
        if opts:
            # Wires with a style or an endpoint marker are emitted as their own
            # \draw[...] statement so the options apply only to that wire, not
            # the whole shared path.
            styled_wire_lines.append(
                rf"\draw[{opts}] {_wire_path(wire, breaks, pin_coord_to_ref, _y)};"
            )
        else:
            draw_lines.append(_wire_path(wire, breaks, pin_coord_to_ref, _y))

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

    # Styled wires: each its own \draw[...] statement after the shared path.
    for swl in styled_wire_lines:
        lines.append(f"  {swl}")

    # Connection dots at junctions.
    for x, y in sorted(junction_points(schematic)):
        lines.append(rf"  \node[circ] at ({_fmt(x)},{_fmt(_y(y))}) {{}};")

    # Open-circle nodes at wire endpoints not connected to any component pin.
    for x, y in sorted(open_endpoints(schematic)):
        lines.append(rf"  \node[ocirc] at ({_fmt(x)},{_fmt(_y(y))}) {{}};")

    # Optionally, open-circle nodes at component pins that nothing connects to.
    if mark_unconnected_pins:
        for x, y in sorted(unconnected_pins(schematic)):
            lines.append(rf"  \node[ocirc] at ({_fmt(x)},{_fmt(_y(y))}) {{}};")

    # Text/math labels at wire endpoints (e.g. an arrow terminating into text).
    for wire in schematic.wires:
        for node in _wire_label_nodes(wire, _y):
            lines.append(f"  {node}")

    # Foreground layer, emitted after the draw block so it sits in front of the
    # circuit. Drawing annotations at z_order >= 0 belong here (a drawing
    # annotation is never part of the main \draw, so its 0 baseline is "in front");
    # plain circuit components only when z_order > 0 (z == 0 stays in the main
    # draw). Wires at z_order > 0. Sorted ascending by (z_order, document order).
    fg_items: list[tuple[int, int, str, object]] = []
    for i, comp in enumerate(schematic.components):
        is_drawing = isinstance(comp, DrawingComponent)
        if (is_drawing and comp.z_order >= 0) or (not is_drawing and comp.z_order > 0):
            fg_items.append((comp.z_order, i, "c", comp))
    for i, wire in enumerate(schematic.wires):
        if wire.z_order > 0 and len(wire.points) >= 2:
            fg_items.append((wire.z_order, i, "w", wire))
    fg_items.sort(key=lambda t: (t[0], t[1]))
    for _z, _i, _kind, obj in fg_items:
        if _kind == "c":
            lines.extend(_emit_layer_component(obj))
        else:
            # Foreground wires come after the \draw block — named anchors resolve.
            lines.append("  " + _wire_layer_line(obj, use_refs=True))

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
    *,
    voltage_european: bool = False,
    current_european: bool = False,
) -> list[str]:
    kind = comp.kind
    style = _annotation_style_opts(comp, voltage_european, current_european)
    if kind in _TWO_TERMINAL_KINDS:
        return [_two_terminal_line(comp, pin_coord_to_ref, y_fn, style)]
    elif kind in _MULTI_TERMINAL_KINDS:
        return [_multi_terminal_line(comp, y_fn, rot_fn, style)]
    elif kind in _NODE_KINDS:
        return [_node_line(comp, y_fn, style)]
    elif isinstance(comp, DrawingComponent):
        # Emitted as standalone commands outside the \draw block; nothing here.
        return []
    else:
        raise ValueError(f"Unknown component kind '{kind}'")


def _component_layer_lines(
    comp: Component,
    y_fn=lambda y: y,
    rot_fn=lambda r: r,
    *,
    voltage_european: bool = False,
    current_european: bool = False,
) -> list[str]:
    """Standalone LaTeX statement(s) for one circuit component emitted **in its own
    z-layer** (``z_order != 0``), outside the shared ``\\draw`` block.

    A drawing annotation is handled by :func:`_drawing_component_lines`; this is
    its counterpart for plain circuit kinds. A node that needs a local
    ``\\ctikzset`` group (a gate's body height, a cute/european transformer) reuses
    :func:`_node_group_lines` (already a self-contained group); every other kind's
    path fragment is wrapped in its own ``\\draw …;``. Endpoints use **absolute
    coordinates** (no ``pin_coord_to_ref``): a layered component contributes no
    named anchor (see :func:`generate`), and absolute pin coords connect exactly.
    """
    if _node_group_ctikzset(comp):
        return _node_group_lines(comp, y_fn, rot_fn)
    frags = _component_lines(
        comp, None, y_fn, rot_fn,
        voltage_european=voltage_european, current_european=current_european,
    )
    return [rf"  \draw {frag};" for frag in frags]


def _two_terminal_line(
    comp: Component,
    pin_coord_to_ref: dict[tuple[float, float], str] | None = None,
    y_fn=lambda y: y,
    style: list[str] = (),
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
    # Mirror is the canvas global Flip-X applied *after* rotation (negate the
    # rotated world x), matching ``component_pin_positions``. Mirroring before
    # rotation would move the far terminal across the origin at 90°/270° and
    # detach it from connected wires.
    if comp.mirror:
        dx = -dx
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
    _suffix, _ = _library.variant_tikz(comp.kind, comp.variants)
    tikz_kind = defn.tikz_keyword + _suffix

    # The endpoints above already place the bipole on the canvas Flip-X axis with
    # the correct along-axis direction (mirror applied after rotation, §7 Mirror).
    # The CircuiTikZ ``mirror`` key supplies the remaining *perpendicular*
    # reflection, so off-axis features (an LED's emission arrows, a voltage
    # label's side) land where the canvas Flip-X puts them at every rotation.
    opts = [tikz_kind]
    lw = _line_width_opt(comp)
    if lw:
        opts.append(lw)
    opts.extend(style)   # local voltage=european / current=european (per annotation)
    if comp.mirror:
        opts.append("mirror")
    if label_str:
        opts.append(label_str)

    return f"{coord0} to[{', '.join(opts)}] {coord1}"


def _gate_height_setting(comp: Component) -> tuple[str, float] | None:
    """``(height_key, height)`` for a parametric gate that sizes its body, else
    ``None``.  Such a gate is emitted in its own ``{ \\ctikzset{…} \\draw …; }``
    group (so the setting reverts), before the main path so its node name resolves
    for wires."""
    spec = _library.param_spec(comp.kind)
    if spec and spec.get("height_key"):
        nd = _library.param_n_data(comp)
        if nd and "height" in nd:
            # Scale the body height by the per-instance Component.scale so a scaled
            # gate's input pitch (and thus its `.in k` anchors) shrink/grow with
            # it, landing exactly at base_offset*scale (see _multi_terminal_line).
            s = float(getattr(comp, "scale", 1.0))
            return spec["height_key"], nd["height"] * s
    return None


def _node_group_ctikzset(comp: Component) -> list[str]:
    """The ``\\ctikzset`` settings a node must be wrapped in its own group with (so
    they revert after) — a parametric gate's body height, or a kind's static
    ``ctikzset`` (a cute/european transformer's ``inductor=…`` shape). Empty when
    the node needs no group (emitted in the main path)."""
    height = _gate_height_setting(comp)
    if height is not None:
        return [f"{height[0]}={height[1]:g}"]   # full precision (grid alignment)
    return _library.node_ctikzset(comp.kind)


def _node_group_lines(comp: Component, y_fn=lambda y: y, rot_fn=lambda r: r) -> list[str]:
    """A node needing local ``\\ctikzset`` (gate height / transformer inductor
    style) wrapped in its own group so the setting reverts after."""
    node = _multi_terminal_line(comp, y_fn, rot_fn)
    sets = [rf"    \ctikzset{{{c}}}" for c in _node_group_ctikzset(comp)]
    return ["  {", *sets, rf"    \draw {node};", "  }"]


def _multi_terminal_line(
    comp: Component,
    y_fn=lambda y: y,
    rot_fn=lambda r: r,
    style: list[str] = (),
) -> str:
    """Render a multi-terminal component.

    *y_fn* and *rot_fn* are applied to all emitted Y coordinates and rotation
    angles respectively (used for Y-flip in preview output).
    """
    defn = REGISTRY[comp.kind]
    node_id = f"node_{comp.id[:8]}"
    pin_positions = component_pin_positions(comp)

    # The node uses the CircuiTikZ *keyword* (e.g. "and port"), which differs from
    # the registry *kind* ("and") for parametric components; the option/anchor
    # tables below are still keyed by kind.
    kind_arg = defn.tikz_keyword
    _, _variant_opts = _library.variant_tikz(comp.kind, comp.variants)
    for _opt in _variant_opts:
        kind_arg = f"{kind_arg}, {_opt}"
    # Parametric kinds (logic gates): append the param option (e.g. "number
    # inputs=4") and use that value's scale; fixed kinds use the static table.
    _param = _library.param_spec(comp.kind)
    if _param is not None:
        kind_arg = f"{kind_arg}, {_param['option'].format(n=_library.param_value(comp))}"
        _nd = _library.param_n_data(comp)
        extra_opts = _library._scale_opts(_nd["scale"]) if _nd else ""
        # A gate's body height (so inputs land on the grid without a node yscale
        # that would oval the bubble) is set in a local group around the node by
        # the caller — see _gate_height_setting / generate.
    elif _library.is_parametric(comp.kind):
        # Multi-parameter measured-pins kinds (mux/demux): the concrete shape
        # option for this value-combo (e.g. "muxdemux def={Lh=8, …, NB=2}") is
        # baked into the instance's n_data record at generation time, alongside
        # the baked grid-alignment scale.
        _nd = _library.param_n_data(comp)
        if _nd and _nd.get("option"):
            kind_arg = f"{kind_arg}, {_nd['option']}"
        extra_opts = _library._scale_opts(_nd["scale"]) if (_nd and _nd.get("scale")) else ""
    else:
        extra_opts = _MULTI_TERMINAL_EXTRA_OPTS.get(comp.kind, "")
    # Scalable kinds (logic gates + digital blocks) carry a per-instance size
    # multiplier (Component.scale, the inspector Size): fold it into the emitted
    # xscale/yscale on top of the symbol's baked alignment scale so the LaTeX body
    # matches the canvas.
    if _library.is_scalable(comp.kind):
        s_user = float(getattr(comp, "scale", 1.0))
        if abs(s_user - 1.0) > 1e-9:
            base = _library.block_scale(comp)   # baked [sx, sy] (gate or block)
            if defn.tikz_keyword.endswith(" port") and _param is not None:
                # A multi-input gate sizes its input pitch via the body *height*
                # (set in the surrounding \ctikzset group — scaled by s_user in
                # _gate_height_setting), NOT via yscale. So scale only x (width)
                # here; the height carries the uniform y-scale (xscale would
                # mis-size the pitch — CircuiTikZ derives it from height).
                extra_opts = _library._scale_opts([base[0] * s_user, base[1]])
            else:
                # Single-input gates and digital blocks scale uniformly.
                extra_opts = _library._scale_opts([base[0] * s_user, base[1] * s_user])
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
    # CircuiTikZ quadpole shapes (transformers) flip their internal coils when the
    # node is rotated by an odd 90° (crossing the terminal leads) or mirrored (the
    # coils face outward). Drawing the shape under the node transform (``transform
    # shape``) reorients it rigidly — matching the canvas — without that distortion,
    # and leaves the terminal anchors unchanged so connected wires still meet them.
    if defn.tikz_keyword.startswith("transformer") and (rotation != 0 or comp.mirror):
        kind_arg = f"{kind_arg}, transform shape"

    # Append user options to the node[] argument.  Logic-port shapes (keyword
    # "<gate> port") don't accept the bipole-style ``l=`` quick key, so a label
    # slot is converted to a CircuiTikZ ``label=above:{…}`` option — placed above
    # the body, matching where the canvas draws the gate's ``l`` slot (above the
    # lead axis; see ComponentItem._slot_direction).  Other slots pass through.
    if defn.tikz_keyword.endswith(" port"):
        user_opts = _gate_label_args(comp)
    else:
        user_opts = _label_args(comp)
    if user_opts:
        kind_arg = f"{kind_arg}, {user_opts}"
    lw = _line_width_opt(comp)
    if lw:
        kind_arg = f"{kind_arg}, {lw}"
    for s in style:   # local voltage=european / current=european (per annotation)
        kind_arg = f"{kind_arg}, {s}"

    # Every multi-terminal node is centre-placed (§4): the node sits at the
    # component position, and its pins connect via their named anchors
    # `(node.anchor)` (see pin_coord_to_ref in generate()) — no anchor= option,
    # no lead stubs.
    x, y = comp.position
    coord = f"({_fmt(x)},{_fmt(y_fn(y))})"
    node_line = f"{coord} node[{kind_arg}] ({node_id}) {{}}"

    lines = [node_line]
    # Transformer polarity dots: a filled circle (CircuiTikZ ``circ``) at each
    # checked inner-dot anchor (the dot variants, §5.4).
    for mark in _library.dot_marks(comp):
        lines.append(f"({node_id}.{mark['anchor']}) node[circ]{{}}")
    if len(lines) == 1:
        return node_line  # single-point node / no polarity dots
    return "\n    ".join(lines)


# Alignment tables, all derived from the component data file (see classification
# block above). Every multi-terminal node is centre-placed and aligned by scale
# alone (§4):
#   _MULTI_TERMINAL_EXTRA_OPTS: kind → "xscale=…, yscale=…" per-axis stretch that
#       lands the anchors on grid (e.g. BJT "xscale=0.8929, yscale=0.974").
#   _PIN_TO_CTIKZ_ANCHOR: kind → {registry_pin: ctikz_anchor} for every pin, so a
#       connecting wire references `(node.anchor)`.
_MULTI_TERMINAL_EXTRA_OPTS: dict[str, str] = _CODEGEN_TABLES["extra_opts"]
_PIN_TO_CTIKZ_ANCHOR: dict[str, dict[str, str]] = _CODEGEN_TABLES["pin_to_ctikz"]


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

_POWER_RAIL_KINDS: frozenset[str] = frozenset({"vcc", "vdd", "vee", "vss"})


def _node_line(comp: Component, y_fn=lambda y: y, style: list[str] = ()) -> str:
    """Render a single-terminal node as: (x,y) node[kind, options] {}

    For power rail kinds, an ``l=`` slot in comp.options is converted to a
    CircuiTikZ ``label=right:VALUE`` argument.  Using the east anchor places
    the label at the bar level (right of the symbol tip) which matches the
    conventional schematic style for power-rail voltage names.
    """
    x, y = comp.position
    args = comp.kind
    if comp.rotation:
        args += f", rotate={comp.rotation}"
    if comp.kind in _POWER_RAIL_KINDS:
        # Pull the l= slot value with the comma-aware splitter so a label
        # containing commas (e.g. inside $...$) is not truncated.
        for seg in split_top_level(comp.options):
            key, eq, val = seg.partition("=")
            if eq and key.strip() == "l" and val.strip():
                args += f", label=right:{{{balance_braces(val.strip())}}}"
                break
    for s in style:   # local voltage=european / current=european (per annotation)
        args += f", {s}"
    return f"({_fmt(x)},{_fmt(y_fn(y))}) node[{args}] {{}}"


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


class _Break(NamedTuple):
    """One place a wire is split to meet a CircuiTikZ ``jump crossing`` node.

    ``point`` is the crossing coordinate (GU, canvas convention); ``node_id`` the
    emitted node's name; ``role`` is ``"hopper"`` (the arm carrying the arc) or
    ``"crossed"`` (the gapped arm); ``orientation`` is the *hopper's* direction
    (``"h"``/``"v"``), which fixes the node rotation and hence the anchor scheme.
    """

    point: tuple[float, float]
    node_id: str
    role: str
    orientation: str


def _hop_on_segment(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> bool:
    """True if *p* lies strictly inside the axis-aligned segment a–b."""
    if a[1] == b[1]:                                   # horizontal
        return p[1] == a[1] and min(a[0], b[0]) < p[0] < max(a[0], b[0])
    if a[0] == b[0]:                                   # vertical
        return p[0] == a[0] and min(a[1], b[1]) < p[1] < max(a[1], b[1])
    return False


def _crossing_anchor(
    role: str, orientation: str,
    side: tuple[float, float], center: tuple[float, float],
) -> str:
    """The ``jump crossing`` anchor a wire arm on the *side* of *center* connects
    to. Both points are in **output** coordinates (post Y-flip). The mapping was
    validated against CircuiTikZ's shape: the horizontal arm carries the arc and
    the vertical arm the gap, with the node rotated 90° for a vertical hopper."""
    sx, sy = side
    cx, cy = center
    if role == "hopper":
        if orientation == "h":                         # arc on horizontal arm
            return ".west" if sx < cx else ".east"
        return ".west" if sy < cy else ".east"         # vertical hopper (rotate=90)
    # Crossed (gapped) arm — perpendicular to the hopper.
    if orientation == "h":                             # crossed is vertical
        return ".south" if sy < cy else ".north"
    return ".north" if sx < cx else ".south"           # crossed horizontal (rotate=90)


def _wire_line_with_crossings(
    wire: Wire,
    breaks: list[_Break],
    pin_coord_to_ref: dict[tuple[float, float], str] | None,
    y_fn=lambda y: y,
) -> str:
    r"""Like :func:`_wire_line`, but the path is broken at each crossing so its
    arms meet the ``jump crossing`` node's anchors (the node, emitted separately,
    draws the arc/gap). At a break the incoming sub-path ends at one anchor and a
    new sub-path resumes at the opposite one — e.g. ``-- (xing0.west) (xing0.east)
    --`` — leaving the crossing itself for the node to draw."""
    pts = simplify_points(wire.points)

    def ref_for(i: int, x: float, y: float) -> str:
        if pin_coord_to_ref and (i == 0 or i == len(pts) - 1):
            r = pin_coord_to_ref.get((round(x, 6), round(y, 6)))
            if r:
                return r
        return f"({_fmt(x)},{_fmt(y_fn(y))})"

    out = ref_for(0, pts[0][0], pts[0][1])
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        seg = [b for b in breaks if _hop_on_segment(b.point, (ax, ay), (bx, by))]
        if ay == by:                                   # horizontal segment
            seg.sort(key=lambda b: (b.point[0] - ax) * (1 if bx >= ax else -1))
        else:                                          # vertical segment
            seg.sort(key=lambda b: (b.point[1] - ay) * (1 if by >= ay else -1))
        stops = [(ax, ay)] + [b.point for b in seg] + [(bx, by)]
        for k, brk in enumerate(seg):
            cx, cy = brk.point
            center = (cx, y_fn(cy))
            prev, nxt = stops[k], stops[k + 2]
            a_in = _crossing_anchor(
                brk.role, brk.orientation, (prev[0], y_fn(prev[1])), center)
            a_out = _crossing_anchor(
                brk.role, brk.orientation, (nxt[0], y_fn(nxt[1])), center)
            # End the incoming sub-path at the node, resume on the far side (the
            # space before the second coordinate starts a fresh sub-path).
            out += f" -- ({brk.node_id}{a_in}) ({brk.node_id}{a_out})"
        out += " -- " + ref_for(i + 1, bx, by)
    return out


def _wire_path(
    wire: Wire,
    breaks: list[_Break],
    pin_coord_to_ref: dict[tuple[float, float], str] | None,
    y_fn=lambda y: y,
) -> str:
    """Wire path string, broken at jump-crossing nodes when *breaks* is non-empty."""
    if breaks:
        return _wire_line_with_crossings(wire, breaks, pin_coord_to_ref, y_fn)
    return _wire_line(wire, pin_coord_to_ref, y_fn)


def _wire_draw_statement(
    wire: Wire,
    breaks: list[_Break],
    pin_coord_to_ref: dict[tuple[float, float], str] | None,
    y_fn=lambda y: y,
) -> str:
    r"""A standalone ``\draw[...] <path>;`` for one wire (used for z-layered wires)."""
    style = compose_style_options(line_style=wire.line_style, line_width=wire.line_width)
    arrow = _wire_arrow_spec(wire)
    opts = ", ".join(p for p in (arrow, style) if p)
    path = _wire_path(wire, breaks, pin_coord_to_ref, y_fn)
    return rf"\draw[{opts}] {path};" if opts else rf"\draw {path};"


def _drawing_component_lines(comp: Component, y_fn) -> list[str]:
    """The indented LaTeX line(s) for one DrawingComponent (z-layer block)."""
    out: list[str] = []
    if isinstance(comp, TextNodeComponent):
        out.append("  " + _text_node_line(comp, y_fn))
    elif isinstance(comp, RectComponent):
        out.append("  " + _rect_line(comp, y_fn))
        tl = _centered_text_line(comp, y_fn)
        if tl:
            out.append("  " + tl)
    elif isinstance(comp, CircleComponent):
        out.append("  " + _circle_line(comp, y_fn))
        tl = _centered_text_line(comp, y_fn)
        if tl:
            out.append("  " + tl)
    elif isinstance(comp, BipoleComponent):
        out.append("  " + _bipole_node_line(comp, y_fn))
    return out


# Map a custom endpoint marker kind to its TikZ ``arrows.meta`` tip name. These
# require ``\usetikzlibrary{arrows.meta}`` (loaded by the standalone template and
# documented in the snippet preamble — see app/preview/latex.py). ``""`` (no
# marker) maps to an empty tip.
_MARKER_TIP: dict[str, str] = {
    "": "",
    "arrow": "Latex",
    "stealth": "Stealth",
    "open": "Latex[open]",
    "bar": "Bar",
}


def _wire_arrow_spec(wire: Wire) -> str:
    r"""Compose a TikZ arrow specification for a wire's endpoint markers.

    Returns an ``arrows.meta`` spec such as ``"-{Latex}"`` (end only),
    ``"{Latex}-"`` (start only), or ``"{Stealth}-{Latex}"`` (both, possibly
    different tips), or ``""`` when neither end has a marker. The start/end tips
    correspond to ``points[0]`` / ``points[-1]`` — the same order
    :func:`_wire_line` emits coordinates — and ``arrows.meta`` tips auto-orient
    to point outward from the path at each end.
    """
    start = _MARKER_TIP.get(wire.start_marker, "")
    end = _MARKER_TIP.get(wire.end_marker, "")
    if not start and not end:
        return ""
    start_tip = f"{{{start}}}" if start else ""
    end_tip = f"{{{end}}}" if end else ""
    return f"{start_tip}-{end_tip}"


#: Gap (GU) between a wire endpoint and its label node, so the label clears the
#: wire end / arrow tip. The node's own inner sep adds a little more.
_WIRE_LABEL_GAP: float = 0.1


def _first_distinct(pts: list[tuple[float, float]], from_end: bool) -> tuple[float, float]:
    """The first vertex distinct from the terminal one, scanning inward.

    Used to find the direction of a wire's terminal segment. *from_end* picks the
    last point (and scans backward); otherwise the first point (scanning forward).
    Falls back to the opposite terminal for a degenerate all-coincident list.
    """
    if from_end:
        anchor = pts[-1]
        for p in reversed(pts[:-1]):
            if p != anchor:
                return p
        return pts[0]
    anchor = pts[0]
    for p in pts[1:]:
        if p != anchor:
            return p
    return pts[-1]


def _wire_label_nodes(wire: Wire, y_fn=lambda y: y) -> list[str]:
    r"""Emit ``\node[anchor=…] at (x,y) {text};`` lines for a wire's end labels.

    The label sits just beyond its endpoint, on the far side from the wire, so
    an arrow marker reads as terminating into the text. The anchor is derived
    from the terminal segment's outward direction **in emitted space** (after
    *y_fn*), so it stays correct under the preview's Y-flip; a small outward gap
    clears the wire end / arrow tip.

    ``inner sep=0`` strips the node's default padding (~3.3 pt) so the visible
    gap is exactly ``_WIRE_LABEL_GAP`` (0.1 GU) — matching the canvas, whose
    label clearance (``_WIRE_LABEL_GAP_PX`` = 6 px = 0.1 GU) has no such padding.
    """
    pts = wire.points
    if len(pts) < 2:
        return []
    lines: list[str] = []
    ends = (
        (wire.start_label, pts[0], _first_distinct(pts, from_end=False),
         wire.start_label_placement),
        (wire.end_label, pts[-1], _first_distinct(pts, from_end=True),
         wire.end_label_placement),
    )
    for text, tip, neighbour, placement in ends:
        if not text:
            continue
        ex, ey = tip
        nx, ny = neighbour
        dx = ex - nx
        dy = y_fn(ey) - y_fn(ny)  # outward Y in emitted (post-flip) space
        if placement in ("above", "below"):
            # Tuck the label beside the wire at the endpoint, extending *inward*
            # (back along the terminal segment) so it never crosses the endpoint
            # into a connected rect/circle. box_x / box_y are the ±1 directions
            # the text box extends from the endpoint; the anchor is the opposite
            # corner so a one-gap offset clears the wire and the endpoint.
            if abs(dx) >= abs(dy):
                # Horizontal segment: along-x inward; side is up/down.
                box_x = -1.0 if dx >= 0 else 1.0
                box_y = 1.0 if placement == "above" else -1.0  # emitted +Y = up
            else:
                # Vertical segment: along-y inward; side is left/right
                # (above → left, below → right).
                box_x = -1.0 if placement == "above" else 1.0
                box_y = -1.0 if dy >= 0 else 1.0
            anchor = (
                ("south" if box_y > 0 else "north")
                + " "
                + ("west" if box_x > 0 else "east")
            )
            px = ex + box_x * _WIRE_LABEL_GAP
            py = y_fn(ey) + box_y * _WIRE_LABEL_GAP
        else:
            # Off the end: along the terminal segment's outward direction.
            if abs(dx) >= abs(dy):
                # Horizontal terminal segment: label left/right of the endpoint.
                if dx >= 0:
                    anchor, ox, oy = "west", _WIRE_LABEL_GAP, 0.0
                else:
                    anchor, ox, oy = "east", -_WIRE_LABEL_GAP, 0.0
            else:
                # Vertical terminal segment: label above/below (emitted +Y = up).
                if dy >= 0:
                    anchor, ox, oy = "south", 0.0, _WIRE_LABEL_GAP
                else:
                    anchor, ox, oy = "north", 0.0, -_WIRE_LABEL_GAP
            px = ex + ox
            py = y_fn(ey) + oy
        lines.append(
            rf"\node[anchor={anchor}, inner sep=0] at ({_fmt(px)},{_fmt(py)}) "
            rf"{{{balance_braces(text)}}};"
        )

    # Mid-wire label: centred *over* the wire with an opaque (white) backdrop so
    # the line does not run through the text. Placed at the fractional position.
    if wire.mid_label:
        mx, my = wire_point_at_fraction(pts, wire.mid_label_pos)
        lines.append(
            rf"\node[fill=white, inner sep=1pt] at "
            rf"({_fmt(mx)},{_fmt(y_fn(my))}) {{{balance_braces(wire.mid_label)}}};"
        )
    return lines


# ---------------------------------------------------------------------------
# Drawing annotation rendering
# ---------------------------------------------------------------------------

_FONT_FAMILY_CMD: dict[str, str] = {
    "serif": r"\rmfamily",
    "sans":  r"\sffamily",
    "mono":  r"\ttfamily",
}


def _font_opts_bracket(comp) -> str:  # noqa: ANN001
    r"""Return the font node option as ``[font=...]``, or ``""`` when all-default.

    Shared by :func:`_text_node_line` and :func:`_rect_text_line` (a FontedComponent
    is assumed; default size is 12 pt).
    """
    has_size  = comp.font_size != 12.0
    has_style = comp.font_bold or comp.font_italic or bool(comp.font_family)
    if not (has_size or has_style):
        return ""
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
    return r"[font=" + "".join(parts) + "]"


def _text_node_line(comp: "TextNodeComponent", y_fn=lambda y: y) -> str:
    r"""Render a text annotation as \node[...] at (x,y) {text};

    Font size (``font_size``), bold (``font_bold``), italic (``font_italic``),
    and family (``font_family``) are all encoded into the ``font=`` node option
    when any of them is set.
    """
    x, y = comp.position
    # Brace-balance: a stray unmatched ``}`` must not escape the node's {…}
    # argument and inject raw TeX into the document (see balance_braces).
    text = balance_braces(comp.options)

    opts = _font_opts_bracket(comp)

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
        line_width=comp.line_width,
        line_style=comp.line_style,
    )
    style_arg = f"[{style}]" if style else ""

    return (
        rf"\draw{style_arg} ({_fmt(x1)},{_fmt(y_fn(y1))}) "
        rf"rectangle ({_fmt(x2)},{_fmt(y_fn(y2))});"
    )


def _centered_text_line(comp: Component, y_fn=lambda y: y) -> str | None:
    r"""Render a box annotation's centred text as \node[font=...] at (cx,cy) {text};

    Shared by ``rect`` and ``circle``.  Returns ``None`` when the box has no text
    (``comp.options`` empty) so it emits only its outline (and a text-free rect
    stays byte-identical to the pre-text-feature output).  The node is centred on
    the bounding box (rects/circles are not rotated).
    """
    text = comp.options
    if not text:
        return None
    text = balance_braces(text)  # contain a stray ``}`` inside the {…} argument
    defn = REGISTRY[comp.kind]
    x1, y1 = comp.position
    so = comp.span_override if comp.span_override is not None else defn.default_span
    dx, dy = so
    cx, cy = x1 + dx / 2.0, y1 + dy / 2.0
    opts = _font_opts_bracket(comp)
    return rf"\node{opts} at ({_fmt(cx)},{_fmt(y_fn(cy))}) {{{text}}};"


def _circle_line(comp: Component, y_fn=lambda y: y) -> str:
    r"""Render a circle/ellipse annotation centred on its bounding box.

    Emits ``\draw[style] (cx,cy) circle (r);`` when the box is square, otherwise
    ``\draw[style] (cx,cy) ellipse (rx and ry);``.  Style (line style, width,
    fill) comes from the StyledComponent fields; the box is ``position`` →
    ``position + (span_override or default_span)``.
    """
    defn = REGISTRY[comp.kind]
    x0, y0 = comp.position
    so = comp.span_override if comp.span_override is not None else defn.default_span
    dx, dy = so
    cx, cy = x0 + dx / 2.0, y0 + dy / 2.0
    rx, ry = abs(dx) / 2.0, abs(dy) / 2.0

    style = compose_style_options(
        fill_color=comp.fill_color,
        line_width=comp.line_width,
        line_style=comp.line_style,
    )
    style_arg = f"[{style}]" if style else ""

    if rx == ry:
        shape = rf"circle ({_fmt(rx)})"
    else:
        shape = rf"ellipse ({_fmt(rx)} and {_fmt(ry)})"
    return rf"\draw{style_arg} ({_fmt(cx)},{_fmt(y_fn(cy))}) {shape};"


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
    label = balance_braces(m.group(1).strip()) if m else ""

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
        line_width=comp.line_width,
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
    """Return comp.options for the ``to[]`` / ``node[]`` argument.

    Label values containing a comma (e.g. ``v=$\\phi(0,0)$``) or an equals sign
    (e.g. ``l=$v=2$``) are brace-protected so TikZ's pgfkeys parser does not
    mis-split them into bogus keys (§7.3). The options are brace-balanced first so
    an unmatched ``}`` cannot escape the ``to[…]``/``node[…]`` bracket group
    (LaTeX-injection containment)."""
    return protect_label_values(balance_braces(comp.options))


def _line_width_opt(comp: Component) -> str:
    """``line width=<w>pt`` for a symbol component whose stroke differs from the
    CircuiTikZ default (0.4 pt); empty otherwise. Block components (rect/circle/
    bipole) emit the same unified ``line_width`` through ``compose_style_options``
    in their own draw/node options, so they are skipped here to avoid emitting it
    twice."""
    if isinstance(comp, DrawingComponent) or abs(comp.line_width - 0.4) < 1e-6:
        return ""
    return f"line width={comp.line_width:g}pt"


def _annotation_style_opts(comp: Component, voltage_european: bool,
                           current_european: bool) -> list[str]:
    """Local ``voltage=european`` / ``current=european`` options for a component
    whose options carry a ``v=`` / ``i=`` annotation, when the document uses the
    european style. Applied **per component** (not as a global ``\\ctikzset``) so it
    only restyles the annotation arrow, never the component symbol — Heaviside
    provides separate american/european *symbols* as distinct components."""
    if not (voltage_european or current_european):
        return []
    has_v = has_i = False
    for seg in split_top_level(comp.options):
        key = seg.split("=", 1)[0].strip()
        fam = key.rstrip("^_<>")[:1] if key else ""
        if fam == "v":
            has_v = True
        elif fam == "i":
            has_i = True
    opts: list[str] = []
    if has_v and voltage_european:
        opts.append("voltage=european")
    if has_i and current_european:
        opts.append("current=european")
    return opts


def _gate_label_args(comp: Component) -> str:
    r"""Like :func:`_label_args` but for logic-port shapes, which reject the
    bipole ``l=`` quick key.  Any ``l=`` slot is rewritten to ``label=above:{…}``
    (the option CircuiTikZ accepts on a node), placing the label above the gate
    body to match the canvas.  Remaining slots are passed through unchanged."""
    out: list[str] = []
    for seg in split_top_level(balance_braces(comp.options)):
        key, eq, val = seg.partition("=")
        if eq and key.strip() == "l" and val.strip():
            out.append(f"label=above:{{{val.strip()}}}")
        elif seg.strip():
            out.append(seg.strip())
    return protect_label_values(", ".join(out))


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
