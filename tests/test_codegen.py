"""
Phase 4 tests — CircuiTikZ code generation.

All tests are pure: no Qt, no filesystem, no LaTeX.
"""

from __future__ import annotations

import copy
import uuid

import pytest

from app.codegen.circuitikz import generate, _fmt
from app.components.model import DiodeComponent, RectComponent, TextNodeComponent
from app.schematic.model import Component, Schematic, Wire


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _schematic(*components, wires=()) -> Schematic:
    return Schematic(
        version="0.1",
        name="test",
        components=list(components),
        wires=list(wires),
    )


def _comp(kind: str, position=(0.0, 0.0), rotation=0, options="", mirror=False) -> Component:
    return Component(
        id=_uid(),
        kind=kind,
        position=position,
        rotation=rotation,
        options=options,
        mirror=mirror,
    )


def _wire(points) -> Wire:
    return Wire(id=_uid(), points=points)


# ---------------------------------------------------------------------------
# test_resistor_horizontal
# ---------------------------------------------------------------------------

def test_resistor_horizontal() -> None:
    """Single resistor at (0,0), rotation 0, no labels → (0,0) to[R] (2,0)."""
    src = generate(_schematic(_comp("R")))
    assert "(0,0) to[R] (2,0)" in src


# ---------------------------------------------------------------------------
# test_resistor_with_labels
# ---------------------------------------------------------------------------

def test_resistor_with_options() -> None:
    """Resistor with options string → to[R, l=$R_1$, v=$V$]."""
    comp = _comp("R", options="l=$R_1$, v=$V$")
    src = generate(_schematic(comp))
    assert "to[R, l=$R_1$, v=$V$]" in src


# ---------------------------------------------------------------------------
# test_resistor_rotated_90
# ---------------------------------------------------------------------------

def test_resistor_rotated_90() -> None:
    """Resistor at (0,0) rotation 90 → terminal pin rotated correctly.

    Span (2,0) rotated 90° CW in Qt Y-down space → (0,2).
    Origin (0,0), terminal (0,2).
    """
    comp = _comp("R", rotation=90)
    src = generate(_schematic(comp))
    assert "(0,0) to[R] (0,2)" in src


# ---------------------------------------------------------------------------
# test_capacitor_horizontal
# ---------------------------------------------------------------------------

def test_capacitor_horizontal() -> None:
    """Capacitor at (2,0), rotation 0 → (2,0) to[C] (4,0)."""
    src = generate(_schematic(_comp("C", position=(2.0, 0.0))))
    assert "(2,0) to[C] (4,0)" in src


# ---------------------------------------------------------------------------
# test_inductor_horizontal
# ---------------------------------------------------------------------------

def test_inductor_horizontal() -> None:
    """Inductor at (0,0), rotation 0 → (0,0) to[L] (2,0)."""
    src = generate(_schematic(_comp("L")))
    assert "(0,0) to[L] (2,0)" in src


# ---------------------------------------------------------------------------
# test_diode_horizontal
# ---------------------------------------------------------------------------

def test_diode_horizontal() -> None:
    """Diode at (0,0), rotation 0 → (0,0) to[D] (2,0)."""
    src = generate(_schematic(_comp("D")))
    assert "(0,0) to[D] (2,0)" in src


def test_diode_filled() -> None:
    """Filled diode → to[D*] in output."""
    comp = DiodeComponent(id=_uid(), kind="D", position=(0.0, 0.0), rotation=0, options="", filled=True)
    src = generate(_schematic(comp))
    assert "(0,0) to[D*] (2,0)" in src


def test_zener_diode() -> None:
    """Zener diode → to[zD] in output."""
    src = generate(_schematic(_comp("zD")))
    assert "(0,0) to[zD] (2,0)" in src


def test_zener_diode_filled() -> None:
    """Filled Zener diode → to[zD*] in output."""
    comp = DiodeComponent(id=_uid(), kind="zD", position=(0.0, 0.0), rotation=0, options="", filled=True)
    src = generate(_schematic(comp))
    assert "(0,0) to[zD*] (2,0)" in src


def test_led() -> None:
    """LED → to[leD] in output."""
    src = generate(_schematic(_comp("leD")))
    assert "(0,0) to[leD] (2,0)" in src


# ---------------------------------------------------------------------------
# test_voltage_source
# ---------------------------------------------------------------------------

def test_voltage_source() -> None:
    """Voltage source at (0,0), rotation 0 → (0,0) to[V] (0,2)."""
    src = generate(_schematic(_comp("V")))
    assert "(0,0) to[V] (0,2)" in src


# ---------------------------------------------------------------------------
# test_opamp_node
# ---------------------------------------------------------------------------

def test_opamp_node() -> None:
    """Op-amp produces node[op amp] syntax."""
    comp = _comp("op amp", position=(1.0, 2.0))
    src = generate(_schematic(comp))
    assert "node[op amp]" in src
    assert "(1,2)" in src


# ---------------------------------------------------------------------------
# test_nmos_node
# ---------------------------------------------------------------------------

def test_npn_node() -> None:
    """NPN BJT is placed with xscale/yscale correction and anchor=B (spec §7.2)."""
    comp = _comp("npn", position=(2.0, 3.0))
    src = generate(_schematic(comp))
    assert "node[npn, xscale=1.181, yscale=1.287, anchor=B]" in src
    assert "(2,3)" in src   # base placed at component position


def test_pnp_node() -> None:
    """PNP BJT is placed with xscale/yscale correction and anchor=B."""
    comp = _comp("pnp", position=(1.0, 1.0))
    src = generate(_schematic(comp))
    assert "node[pnp, xscale=1.181, yscale=1.287, anchor=B]" in src


def test_npn_no_bridge_leads() -> None:
    """NPN uses scale correction — no bridge lead wires should appear."""
    comp = _comp("npn", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    # The only reference to .C/.E should be in named anchor wire refs, not
    # standalone bridge lines (which would look like "(node_X.C) --").
    # With scale correction the C/E anchors land on-grid; no bridge needed.
    assert "xscale=1.181" in src
    assert "yscale=1.287" in src


def test_npn_pin_offsets() -> None:
    """NPN: base (0,0), collector (1,-1) [top], emitter (1,1) [bottom]."""
    from app.components.registry import REGISTRY
    defn = REGISTRY["npn"]
    pin_map = {p.name: p.offset for p in defn.pins}
    assert pin_map["base"]      == (0.0,  0.0)
    assert pin_map["collector"] == (1.0, -1.0)
    assert pin_map["emitter"]   == (1.0,  1.0)


def test_pnp_pin_offsets() -> None:
    """PNP: base (0,0), emitter (1,-1) [top], collector (1,1) [bottom]."""
    from app.components.registry import REGISTRY
    defn = REGISTRY["pnp"]
    pin_map = {p.name: p.offset for p in defn.pins}
    assert pin_map["base"]      == (0.0,  0.0)
    assert pin_map["emitter"]   == (1.0, -1.0)
    assert pin_map["collector"] == (1.0,  1.0)


def test_nmos_node() -> None:
    """nigfete is placed as node[nigfete] with its geometry correction (spec §7.2).

    The symbol is anchored at the gate pin and stretched horizontally by
    xscale=1.0167 to align the drain/source pins to the 0.5-GU grid.
    """
    comp = _comp("nigfete", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[nigfete, xscale=1.0167, anchor=gate]" in src


def test_nmos_depletion_node() -> None:
    """nigfetd uses the same xscale geometry correction as nigfete (spec §5.5)."""
    comp = _comp("nigfetd", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[nigfetd, xscale=1.0167, anchor=gate]" in src


def test_pmos_node() -> None:
    """pigfete is placed with xscale=1.0167 and anchor=gate (spec §7.2).

    PMOS has source at top (Qt offset (1.0,-0.5)) and drain at bottom
    (Qt offset (1.0,+1.0)) — the y-mirror of nigfete.
    """
    comp = _comp("pigfete", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[pigfete, xscale=1.0167, anchor=gate]" in src


def test_pmos_depletion_node() -> None:
    """pigfetd uses the same geometry correction as pigfete (spec §5.5)."""
    comp = _comp("pigfetd", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[pigfetd, xscale=1.0167, anchor=gate]" in src


def test_pmos_pin_offsets() -> None:
    """PMOS source is above gate (Qt y=-0.5) and drain is below (Qt y=+1.0)."""
    from app.schematic.model import component_pin_positions
    comp = _comp("pigfete", position=(2.0, 3.0))
    pins = {p.name: off for p, off in
            zip(__import__("app.components.registry", fromlist=["REGISTRY"]).REGISTRY["pigfete"].pins,
                component_pin_positions(comp))}
    assert pins["gate"]   == (2.0, 3.0)
    assert pins["source"] == (3.0, 2.5)   # 1 GU right, 0.5 GU above
    assert pins["drain"]  == (3.0, 4.0)   # 1 GU right, 1 GU below


# ---------------------------------------------------------------------------
# test_wire_straight
# ---------------------------------------------------------------------------

def test_wire_straight() -> None:
    """Two-point wire [(0,0),(2,0)] → (0,0) -- (2,0)."""
    src = generate(_schematic(wires=[_wire([(0.0, 0.0), (2.0, 0.0)])]))
    assert "(0,0) -- (2,0)" in src


# ---------------------------------------------------------------------------
# test_wire_manhattan
# ---------------------------------------------------------------------------

def test_wire_manhattan() -> None:
    """Three-point wire [(0,0),(2,0),(2,2)] → (0,0) -- (2,0) -- (2,2)."""
    src = generate(_schematic(wires=[_wire([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])]))
    assert "(0,0) -- (2,0) -- (2,2)" in src


# ---------------------------------------------------------------------------
# test_coordinate_formatting_integer
# ---------------------------------------------------------------------------

def test_coordinate_formatting_integer() -> None:
    """Coordinate 2.0 is formatted as '2', not '2.0' or '2.00'."""
    assert _fmt(2.0) == "2"
    assert _fmt(0.0) == "0"
    assert _fmt(-3.0) == "-3"


# ---------------------------------------------------------------------------
# test_coordinate_formatting_half
# ---------------------------------------------------------------------------

def test_coordinate_formatting_half() -> None:
    """Coordinate 1.5 is formatted as '1.5', not '1.50'."""
    assert _fmt(1.5) == "1.5"
    assert _fmt(0.5) == "0.5"
    assert _fmt(-2.5) == "-2.5"


# ---------------------------------------------------------------------------
# test_empty_schematic
# ---------------------------------------------------------------------------

def test_empty_schematic() -> None:
    """Empty schematic produces a valid (empty) circuitikz environment."""
    src = generate(_schematic())
    assert r"\begin{circuitikz}" in src
    assert r"\end{circuitikz}" in src
    assert r"\draw" in src


# ---------------------------------------------------------------------------
# test_generate_is_pure
# ---------------------------------------------------------------------------

def test_generate_is_pure() -> None:
    """Calling generate() twice on the same Schematic produces identical output."""
    s = _schematic(
        _comp("R", position=(0.0, 0.0), options="l=$R_1$"),
        _comp("C", position=(2.0, 0.0)),
        wires=[_wire([(0.0, 0.0), (2.0, 0.0)])],
    )
    assert generate(s) == generate(s)


# ---------------------------------------------------------------------------
# Junction dots — \node[circ]
# ---------------------------------------------------------------------------

def test_junction_emits_circ_node() -> None:
    s = _schematic(
        wires=[
            Wire(id="a", points=[(0.0, 2.0), (2.0, 2.0)]),
            Wire(id="b", points=[(2.0, 0.0), (2.0, 2.0)]),
            Wire(id="c", points=[(2.0, 2.0), (4.0, 2.0)]),
        ]
    )
    src = generate(s)
    assert r"\node[circ] at (2,2) {};" in src
    # The circ node sits after the \draw path's terminating ';'.
    assert src.index(";") < src.index(r"\node[circ]")


def test_no_junction_no_circ_node() -> None:
    s = _schematic(wires=[_wire([(0.0, 0.0), (2.0, 0.0)])])
    assert r"\node[circ]" not in generate(s)


def test_junction_node_count_matches() -> None:
    """Two separate 3-way junctions → two circ nodes."""
    s = _schematic(
        wires=[
            Wire(id="a1", points=[(0.0, 0.0), (2.0, 0.0)]),
            Wire(id="a2", points=[(2.0, 0.0), (2.0, 2.0)]),
            Wire(id="a3", points=[(2.0, 0.0), (4.0, 0.0)]),
            Wire(id="b1", points=[(0.0, 6.0), (2.0, 6.0)]),
            Wire(id="b2", points=[(2.0, 6.0), (2.0, 8.0)]),
            Wire(id="b3", points=[(2.0, 6.0), (4.0, 6.0)]),
        ]
    )
    assert generate(s).count(r"\node[circ]") == 2


def test_open_endpoint_emits_ocirc_node() -> None:
    """A free wire endpoint (not on any pin) emits \\node[ocirc]."""
    s = _schematic(wires=[_wire([(0.0, 0.0), (4.0, 0.0)])])
    src = generate(s)
    assert src.count(r"\node[ocirc]") == 2
    assert r"\node[ocirc] at (0,0) {};" in src
    assert r"\node[ocirc] at (4,0) {};" in src


def test_pin_connected_endpoint_no_ocirc() -> None:
    """A wire endpoint that sits on a component pin does not get \\node[ocirc]."""
    # Resistor at (0,0) → pins at (0,0) and (2,0); wire from (2,0) to (5,0).
    r = _comp("R", position=(0.0, 0.0))
    s = _schematic(r, wires=[_wire([(2.0, 0.0), (5.0, 0.0)])])
    src = generate(s)
    assert r"\node[ocirc] at (2,0) {};" not in src
    assert r"\node[ocirc] at (5,0) {};" in src


def test_no_open_endpoints_no_ocirc() -> None:
    """A wire whose both ends land on pins emits no \\node[ocirc]."""
    r1 = _comp("R", position=(0.0, 0.0))
    r2 = _comp("R", position=(6.0, 0.0))
    s = _schematic(r1, r2, wires=[_wire([(2.0, 0.0), (6.0, 0.0)])])
    assert r"\node[ocirc]" not in generate(s)


# ---------------------------------------------------------------------------
# Drawing annotations
# ---------------------------------------------------------------------------

def test_text_node_basic() -> None:
    r"""text_node emits \node at (x,y) {text}; outside the \draw block."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(2.0, 3.0),
        rotation=0, options="Hello", mirror=False,
    )
    src = generate(_schematic(comp))
    assert r"\node at (2,3) {Hello};" in src


def test_text_node_with_font_size() -> None:
    r"""text_node with font_size → \node[font=\fontsize{...}...] ..."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(1.0, 1.0),
        rotation=0, options="A", mirror=False,
        font_size=14.0,
    )
    src = generate(_schematic(comp))
    assert r"\node[font=\fontsize{14}" in src
    assert "{A};" in src


def test_text_node_bold_italic() -> None:
    r"""text_node with bold+italic → \bfseries\itshape in font= option."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(1.0, 1.0),
        rotation=0, options="Hi", mirror=False,
        font_bold=True, font_italic=True,
    )
    src = generate(_schematic(comp))
    assert r"\bfseries" in src
    assert r"\itshape" in src
    assert "{Hi};" in src


def test_text_node_font_family_sans() -> None:
    r"""text_node with font_family='sans' → \sffamily in font= option."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(1.0, 1.0),
        rotation=0, options="T", mirror=False,
        font_family="sans",
    )
    src = generate(_schematic(comp))
    assert r"\sffamily" in src


def test_text_node_font_family_mono() -> None:
    r"""text_node with font_family='mono' → \ttfamily in font= option."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(1.0, 1.0),
        rotation=0, options="T", mirror=False,
        font_family="mono",
    )
    src = generate(_schematic(comp))
    assert r"\ttfamily" in src


def test_text_node_font_all_options() -> None:
    r"""text_node with size+bold+italic+family → all parts present in font=."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(0.0, 0.0),
        rotation=0, options="X", mirror=False,
        font_size=10.0,
        font_bold=True, font_italic=True, font_family="serif",
    )
    src = generate(_schematic(comp))
    assert r"\fontsize{10}" in src
    assert r"\bfseries" in src
    assert r"\itshape" in src
    assert r"\rmfamily" in src


def test_text_node_y_flip() -> None:
    r"""text_node with y_flip=True negates the y coordinate."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(2.0, 3.0),
        rotation=0, options="Flip", mirror=False,
    )
    src = generate(_schematic(comp), y_flip=True)
    assert r"\node at (2,-3) {Flip};" in src


def test_text_node_rotation() -> None:
    r"""text_node rotation=90 → rotate=270 (negated so CW-visual on canvas maps to CW in TikZ)."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(1.0, 2.0),
        rotation=90, options="Hello", mirror=False,
    )
    src = generate(_schematic(comp))
    assert r"\node[rotate=270] at (1,2) {Hello};" in src


def test_text_node_rotation_with_font() -> None:
    r"""text_node rotation=270 + bold → rotate=90 and \bfseries both present."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(0.0, 0.0),
        rotation=270, options="Hi", mirror=False,
        font_bold=True,
    )
    src = generate(_schematic(comp))
    assert "rotate=90" in src
    assert r"\bfseries" in src


def test_rect_solid() -> None:
    r"""rect with no style → \draw (x1,y1) rectangle (x2,y2); (no brackets)."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(-0.5, -0.5),
        rotation=0, options="", mirror=False,
        span_override=(5.0, 1.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw (-0.5,-0.5) rectangle (4.5,0.5);" in src


def test_rect_dashed() -> None:
    r"""rect with options="dashed" → \draw[dashed] ... rectangle ...;"""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="dashed", mirror=False,
        span_override=(2.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw[dashed] (0,0) rectangle (2,2);" in src


def test_rect_uses_default_span_when_none() -> None:
    """rect with span_override=None falls back to the registry default_span (2,2)."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
    )
    src = generate(_schematic(comp))
    assert "rectangle (2,2)" in src


def test_drawing_kinds_not_in_draw_block() -> None:
    """text_node and rect produce nothing inside the main \\draw block."""
    t = TextNodeComponent(
        id=_uid(), kind="text_node", position=(0.0, 0.0),
        rotation=0, options="Hi", mirror=False,
    )
    r = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(1.0, 1.0),
    )
    src = generate(_schematic(t, r))
    # The \draw block should be empty (only the standalone lines follow the ;)
    draw_block = src.split(r"\draw")[1].split(";")[0]
    assert "text_node" not in draw_block
    assert "rectangle" not in draw_block


def test_rect_with_line_width() -> None:
    r"""rect with line width in options → emitted in the \draw[...] arg."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="dashed, line width=1.5pt", mirror=False,
        span_override=(2.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw[dashed, line width=1.5pt] (0,0) rectangle (2,2);" in src


def test_rect_with_fill() -> None:
    r"""rect with fill → emitted as \draw[fill=...] ... rectangle ...;"""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="fill=yellow!20", mirror=False,
        span_override=(2.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw[fill=yellow!20] (0,0) rectangle (2,2);" in src


def test_rect_z_order_background_before_draw_block() -> None:
    """A rect with z_order=-1 is emitted before the \\draw block."""
    r = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="dashed", mirror=False,
        span_override=(2.0, 2.0), z_order=-1,
    )
    resistor = Component(
        id=_uid(), kind="R", position=(3.0, 0.0),
        rotation=0, options="", mirror=False,
    )
    src = generate(_schematic(r, resistor))
    rect_pos = src.index(r"\draw[dashed]")
    draw_block_pos = src.index(r"\draw" + "\n")
    assert rect_pos < draw_block_pos, "Background rect must appear before \\draw block"


def test_rect_z_order_foreground_after_draw_block() -> None:
    """A rect with z_order=0 (default) is emitted after the \\draw block."""
    r = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 2.0), z_order=0,
    )
    src = generate(_schematic(r))
    draw_semi = src.index("  ;")
    rect_pos = src.index(r"\draw (0,0) rectangle (2,2);")
    assert rect_pos > draw_semi, "Foreground rect must appear after \\draw block"


def test_z_order_default_is_zero() -> None:
    """RectComponent.z_order defaults to 0."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
    )
    assert comp.z_order == 0


def test_z_order_sorts_within_background_group() -> None:
    """Two background rects are emitted in z_order ascending order (lower first = further back)."""
    large = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(4.0, 4.0), z_order=-2,
    )
    small = RectComponent(
        id=_uid(), kind="rect", position=(1.0, 1.0),
        rotation=0, options="dashed", mirror=False,
        span_override=(2.0, 2.0), z_order=-1,
    )
    # Pass in reverse insertion order to prove sorting overrides insertion order.
    src = generate(_schematic(small, large))
    large_pos = src.index("rectangle (4,4)")
    small_pos = src.index("rectangle (3,3)")
    assert large_pos < small_pos, "Lower z_order rect must be emitted first (further back)"


def test_z_order_sorts_within_foreground_group() -> None:
    """Two foreground rects are emitted in z_order ascending order."""
    bottom = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(4.0, 4.0), z_order=1,
    )
    top = RectComponent(
        id=_uid(), kind="rect", position=(1.0, 1.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 2.0), z_order=2,
    )
    src = generate(_schematic(top, bottom))
    bottom_pos = src.index("rectangle (4,4)")
    top_pos = src.index("rectangle (3,3)")
    assert bottom_pos < top_pos, "Lower z_order rect must be emitted first (further back)"
