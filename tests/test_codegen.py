"""
Phase 4 tests — CircuiTikZ code generation.

All tests are pure: no Qt, no filesystem, no LaTeX.
"""

from __future__ import annotations

import copy
import os
import uuid

import pytest

from app.codegen.circuitikz import generate, _fmt
from app.components.model import CircleComponent, Component, RectComponent, TextNodeComponent
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
# European / cute component shape keywords (§5)
# ---------------------------------------------------------------------------

def test_european_components_emit_shape_keywords() -> None:
    """European/cute components emit their style-independent shape keyword, so the
    shape is fixed regardless of the document's global resistor/inductor style."""
    assert "to[european resistor]" in generate(_schematic(_comp("eR")))
    assert "to[european inductor]" in generate(_schematic(_comp("eL")))
    assert "to[cute inductor]" in generate(_schematic(_comp("cuteL")))


def test_european_logic_gates_emit_keywords() -> None:
    """European logic gates emit `node[european … port]`; the parametric AND keeps
    its scoped european height setting."""
    assert "node[european not port" in generate(_schematic(_comp("enot")))
    src = generate(_schematic(_comp("eand")))
    assert "node[european and port" in src
    assert r"\ctikzset{tripoles/european and port/height" in src


# ---------------------------------------------------------------------------
# Document voltage/current label styles (§7.2)
# ---------------------------------------------------------------------------

def test_american_style_emits_no_ctikzset() -> None:
    """The default (american) styles add no voltage/current ctikzset line, so
    existing output is byte-for-byte unchanged."""
    src = generate(_schematic(_comp("R", options="v=$V$, i=$I$")))
    assert "voltage=" not in src
    assert "current=" not in src


def test_european_voltage_emits_scoped_ctikzset() -> None:
    s = _schematic(_comp("R"))
    s.voltage_style = "european"
    lines = [l.strip() for l in generate(s).splitlines() if "ctikzset" in l]
    assert r"\ctikzset{voltage=european}" in lines
    assert "current=european" not in "\n".join(lines)


def test_european_both_styles_combined() -> None:
    s = _schematic(_comp("R"))
    s.voltage_style = "european"
    s.current_style = "european"
    assert r"\ctikzset{voltage=european, current=european}" in generate(s)


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


def test_label_value_with_comma_is_brace_protected() -> None:
    """A label value containing a comma is wrapped in braces so pgfkeys does not
    mis-split the to[] option list into bogus keys (regression)."""
    comp = _comp("cV", options=r"v=$\phi(0,0^+)$")
    src = generate(_schematic(comp))
    assert r"to[cV, v={$\phi(0,0^+)$}]" in src


def test_comma_free_label_value_left_unwrapped() -> None:
    """Values without commas are emitted verbatim (no needless braces)."""
    comp = _comp("R", options="l=$R_1$")
    src = generate(_schematic(comp))
    assert "to[R, l=$R_1$]" in src


def test_protect_label_commas_unit() -> None:
    """protect_label_commas wraps only comma-bearing values, idempotently."""
    from app.components.style import protect_label_commas as p

    assert p(r"v=$\phi(0,0)$") == r"v={$\phi(0,0)$}"
    assert p("l=$R_1$") == "l=$R_1$"                      # no comma → untouched
    assert p(r"l=$R$, v=$\phi(0,1)$") == r"l=$R$, v={$\phi(0,1)$}"
    assert p(r"v={$\phi(0,0)$}") == r"v={$\phi(0,0)$}"    # already a brace group
    assert p("mirror, scale=2") == "mirror, scale=2"      # flags unaffected
    assert p("") == ""


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
    """Capacitor at (2,0) → output is normalised toward the origin: (0,0) to[C] (2,0)."""
    src = generate(_schematic(_comp("C", position=(2.0, 0.0))))
    assert "(0,0) to[C] (2,0)" in src


def test_output_normalized_toward_origin() -> None:
    """A schematic placed far from origin (as the canvas does) emits source whose
    bounding box starts at (0,0), not at the canvas position."""
    # A resistor + wire sitting around 75 GU, like a real canvas placement.
    r = _comp("R", position=(75.0, 78.0), options="l=$R_1$")
    w = Wire(id="w", points=[(77.0, 78.0), (80.0, 78.0)])
    src = generate(_schematic(r, wires=(w,)))
    assert "(0,0) to[R, l=$R_1$] (2,0)" in src
    assert "(2,0) -- (5,0)" in src
    # No coordinate in the output should be anywhere near the original ~75 offset.
    import re
    coords = re.findall(r"\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)", src)
    assert all(abs(float(x)) < 50 and abs(float(y)) < 50 for x, y in coords)


def test_normalization_preserves_relative_geometry() -> None:
    """Translating toward the origin shifts every coordinate by the same amount,
    so relative spacing between parts is unchanged."""
    def _two(p_a, p_b):
        return Schematic(
            version="0.1", name="t",
            components=[
                Component(id="a", kind="R", position=p_a, rotation=0, options="", mirror=False),
                Component(id="b", kind="R", position=p_b, rotation=0, options="", mirror=False),
            ],
        )

    near = generate(_two((0.0, 0.0), (5.0, 0.0)))
    far = generate(_two((40.0, 40.0), (45.0, 40.0)))
    # Same shape regardless of where on the canvas it was drawn.
    assert near == far


def test_normalization_keeps_grid_alignment() -> None:
    """The shift is a whole number of GU, so a half-grid coordinate stays on the
    quarter/half grid after normalisation (no fractional drift)."""
    # Resistor whose origin is on the 0.5 grid, far from origin.
    src = generate(_schematic(_comp("R", position=(75.5, 80.0))))
    assert "(0.5,0) to[R] (2.5,0)" in src


def test_already_at_origin_is_unchanged() -> None:
    """A schematic already at the origin is emitted without any shift."""
    src = generate(_schematic(_comp("R", position=(0.0, 0.0))))
    assert "(0,0) to[R] (2,0)" in src


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
    comp = Component(id=_uid(), kind="D", position=(0.0, 0.0), rotation=0, options="", variants={"filled": True})
    src = generate(_schematic(comp))
    assert "(0,0) to[D*] (2,0)" in src


def test_diode_emits_picture_scoped_scale() -> None:
    """A schematic containing a diode emits a picture-scoped `diodes/scale`
    inside the environment so the (large) default diode body is shrunk to match
    the canvas SVGs; the line sits right after `\\begin{circuitikz}`."""
    src = generate(_schematic(_comp("D")))
    assert r"\ctikzset{diodes/scale=0.8}" in src
    lines = [ln.strip() for ln in src.splitlines()]
    i = lines.index(r"\begin{circuitikz}")
    assert lines[i + 1] == r"\ctikzset{diodes/scale=0.8}"


def test_no_diode_scale_without_diodes() -> None:
    """Schematics with no diode-family component omit the diodes/scale line."""
    src = generate(_schematic(_comp("R"), _comp("C", position=(4.0, 0.0))))
    assert "diodes/scale" not in src


def test_zener_diode() -> None:
    """Zener diode → to[zD] in output."""
    src = generate(_schematic(_comp("zD")))
    assert "(0,0) to[zD] (2,0)" in src


def test_zener_diode_filled() -> None:
    """Filled Zener diode → to[zD*] in output."""
    comp = Component(id=_uid(), kind="zD", position=(0.0, 0.0), rotation=0, options="", variants={"filled": True})
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
    # Output is normalised toward the origin; the op-amp's pins extend left/below
    # its origin, so the node lands at (2,1).
    assert "(2,1)" in src


# ---------------------------------------------------------------------------
# test_nmos_node
# ---------------------------------------------------------------------------

def test_npn_node() -> None:
    """NPN BJT is scaled so its collector/emitter land on the grid (no stubs);
    placed anchor=B (spec/component-editor.md §4)."""
    comp = _comp("npn", position=(2.0, 3.0))
    src = generate(_schematic(comp))
    assert "node[npn, xscale=1.1905, yscale=1.2987, anchor=B]" in src


def test_pnp_node() -> None:
    """PNP BJT is scaled (anchor=B), same as NPN."""
    comp = _comp("pnp", position=(1.0, 1.0))
    src = generate(_schematic(comp))
    assert "node[pnp, xscale=1.1905, yscale=1.2987, anchor=B]" in src


def test_npn_no_bridge_leads() -> None:
    """NPN lands C/E on grid by scaling — no bridge lead wires are needed."""
    comp = _comp("npn", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "xscale=1.1905" in src
    assert ".C) -- " not in src
    assert ".E) -- " not in src


def test_gate_label_emitted_as_label_above() -> None:
    """Logic-port shapes reject the bipole ``l=`` quick key, so a gate's label
    slot is emitted as ``label=above:{…}`` (which CircuiTikZ accepts), not ``l=``.
    Above matches where the canvas draws the gate's ``l`` slot."""
    comp = _comp("nand", options=r"l=$U$")
    src = generate(_schematic(comp))
    assert "label=above:{$U$}" in src
    assert "l=$U$" not in src


def test_not_gate_label_emitted_as_label_above() -> None:
    """Non-parametric gates (not/buffer) take the same label path."""
    comp = _comp("not", options=r"l=$Y$")
    src = generate(_schematic(comp))
    assert "label=above:{$Y$}" in src
    assert "l=$Y$" not in src


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
    """nigfete is scaled (anchor=gate) so drain/source align with the grid; a
    short residual lead bridges the source's sub-grid y offset."""
    comp = _comp("nigfete", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[nigfete, xscale=1.0204, yscale=0.962, anchor=gate]" in src
    assert ".source) -- " in src  # small residual lead


def test_nmos_depletion_node() -> None:
    """nigfetd is scaled (anchor=gate), same as nigfete."""
    comp = _comp("nigfetd", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[nigfetd, xscale=1.0204, yscale=0.962, anchor=gate]" in src


def test_pmos_node() -> None:
    """pigfete is scaled (anchor=gate) — the y-mirror of nigfete."""
    comp = _comp("pigfete", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[pigfete, xscale=1.0204, yscale=0.962, anchor=gate]" in src


def test_pmos_depletion_node() -> None:
    """pigfetd is scaled (anchor=gate), same as pigfete."""
    comp = _comp("pigfetd", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[pigfetd, xscale=1.0204, yscale=0.962, anchor=gate]" in src


def test_nmos_bodydiode() -> None:
    """nigfete with body_diode=True emits the bodydiode option (with the scale)."""
    comp = Component(id=_uid(), kind="nigfete", position=(0.0, 0.0), rotation=0, options="", variants={"body_diode": True})
    src = generate(_schematic(comp))
    assert "node[nigfete, bodydiode, xscale=1.0204, yscale=0.962, anchor=gate]" in src


def test_nmos_no_bodydiode() -> None:
    """nigfete with body_diode off omits the bodydiode option."""
    comp = Component(id=_uid(), kind="nigfete", position=(0.0, 0.0), rotation=0, options="")
    src = generate(_schematic(comp))
    assert "bodydiode" not in src


def test_pmos_bodydiode() -> None:
    """pigfete with body_diode=True emits the bodydiode option."""
    comp = Component(id=_uid(), kind="pigfete", position=(0.0, 0.0), rotation=0, options="", variants={"body_diode": True})
    src = generate(_schematic(comp))
    assert "node[pigfete, bodydiode, xscale=1.0204, yscale=0.962, anchor=gate]" in src


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


def test_voltage_annotation_endpoint_emits_ocirc() -> None:
    """A wire ending on a voltage annotation (`open`) pin still gets ocirc.

    The annotation is an open circuit, so it does not connect the wire end.
    """
    va = _comp("open", position=(2.0, 0.0))   # pins (2,0),(4,0)
    s = _schematic(va, wires=[_wire([(0.0, 0.0), (2.0, 0.0)])])
    src = generate(s)
    assert r"\node[ocirc] at (2,0) {};" in src   # wire end on annotation → open
    assert r"\node[ocirc] at (0,0) {};" in src


def test_degenerate_wire_skipped_but_endpoint_open() -> None:
    """A single-point wire emits no draw line and does not suppress an ocirc."""
    s = _schematic(wires=[
        _wire([(0.0, 0.0), (4.0, 0.0)]),
        _wire([(4.0, 0.0)]),   # degenerate
    ])
    src = generate(s)
    assert "\n    (4,0)\n" not in src                  # no stray lone coordinate
    assert r"\node[ocirc] at (4,0) {};" in src         # endpoint still open
    assert r"\node[ocirc] at (0,0) {};" in src


def test_no_open_endpoints_no_ocirc() -> None:
    """A wire whose both ends land on pins emits no \\node[ocirc]."""
    r1 = _comp("R", position=(0.0, 0.0))
    r2 = _comp("R", position=(6.0, 0.0))
    s = _schematic(r1, r2, wires=[_wire([(2.0, 0.0), (6.0, 0.0)])])
    assert r"\node[ocirc]" not in generate(s)


def test_plain_wire_in_shared_draw() -> None:
    """A default-styled wire is emitted inside the shared \\draw path."""
    s = _schematic(wires=[_wire([(0.0, 0.0), (4.0, 0.0)])])
    src = generate(s)
    assert "    (0,0) -- (4,0)" in src
    assert r"\draw[" not in src  # no per-wire styled statement


def test_styled_wire_separate_draw() -> None:
    """A wire with a non-default style is emitted as its own \\draw[...] line."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             line_style="dashed", line_width=0.8)
    src = generate(_schematic(wires=[w]))
    assert r"\draw[dashed, line width=0.8pt] (0,0) -- (4,0);" in src


def test_styled_wire_line_width_only() -> None:
    """line_width alone (no dash) still triggers a styled statement."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], line_width=1.0)
    src = generate(_schematic(wires=[w]))
    assert r"\draw[line width=1pt] (0,0) -- (4,0);" in src


def test_wire_end_marker_emits_arrow() -> None:
    """A wire with end_marker='arrow' becomes a \\draw[-{Latex}] statement."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], end_marker="arrow")
    src = generate(_schematic(wires=[w]))
    assert r"\draw[-{Latex}] (0,0) -- (4,0);" in src


def test_wire_start_marker_emits_reverse_arrow() -> None:
    """start_marker='arrow' puts the tip on the first point: \\draw[{Latex}-]."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], start_marker="arrow")
    src = generate(_schematic(wires=[w]))
    assert r"\draw[{Latex}-] (0,0) -- (4,0);" in src


def test_wire_both_markers_emit_double_arrow() -> None:
    """Markers on both ends emit \\draw[{Latex}-{Latex}]."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             start_marker="arrow", end_marker="arrow")
    src = generate(_schematic(wires=[w]))
    assert r"\draw[{Latex}-{Latex}] (0,0) -- (4,0);" in src


def test_wire_marker_styles_map_to_arrows_meta_tips() -> None:
    """Each marker kind maps to its arrows.meta tip on the end endpoint."""
    cases = {
        "stealth": r"\draw[-{Stealth}] (0,0) -- (4,0);",
        "open":    r"\draw[-{Latex[open]}] (0,0) -- (4,0);",
        "bar":     r"\draw[-{Bar}] (0,0) -- (4,0);",
    }
    for kind, expected in cases.items():
        w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], end_marker=kind)
        assert expected in generate(_schematic(wires=[w]))


def test_wire_mixed_markers_emit_distinct_tips() -> None:
    """Different start/end kinds compose independently."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             start_marker="bar", end_marker="stealth")
    src = generate(_schematic(wires=[w]))
    assert r"\draw[{Bar}-{Stealth}] (0,0) -- (4,0);" in src


def test_wire_marker_combines_with_style() -> None:
    """Arrow spec leads, followed by the line style/width options."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             end_marker="arrow", line_style="dashed", line_width=0.8)
    src = generate(_schematic(wires=[w]))
    assert r"\draw[-{Latex}, dashed, line width=0.8pt] (0,0) -- (4,0);" in src


# ---------------------------------------------------------------------------
# Wire endpoint text/math labels
# ---------------------------------------------------------------------------

def test_wire_end_label_horizontal_anchor_west() -> None:
    """A label past a rightward end anchors west (text extends right of the tip)."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], end_label="$y(t)$")
    src = generate(_schematic(wires=[w]))
    assert r"\node[anchor=west, inner sep=0] at (4.10,0) {$y(t)$};" in src


def test_wire_start_label_horizontal_anchor_east() -> None:
    """A label past a leftward start anchors east (text extends left of the tip)."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], start_label="in")
    src = generate(_schematic(wires=[w]))
    assert r"\node[anchor=east, inner sep=0] at (-0.10,0) {in};" in src


def test_wire_label_vertical_anchor_under_yflip() -> None:
    """Vertical-wire labels anchor by emitted-space direction (Y-flip aware)."""
    # Canvas-down wire: start at top (0,0), end at bottom (0,3).
    w = Wire(id=_uid(), points=[(0.0, 0.0), (0.0, 3.0)],
             start_label="top", end_label="bottom")
    src = generate(_schematic(wires=[w]), y_flip=True)
    assert r"\node[anchor=south, inner sep=0] at (0,0.10) {top};" in src       # above the top end
    assert r"\node[anchor=north, inner sep=0] at (0,-3.10) {bottom};" in src   # below the bottom end


def test_wire_end_label_placement_above_horizontal() -> None:
    """On a horizontal wire, 'above' tucks the label above the wire, inward of the
    endpoint (right edge at the endpoint, not past it)."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             end_label="$y(t)$", end_label_placement="above")
    src = generate(_schematic(wires=[w]))
    # south east: box extends up-left from (endpoint - gap, +gap) → above, inward.
    assert r"\node[anchor=south east, inner sep=0] at (3.90,0.10) {$y(t)$};" in src


def test_wire_end_label_placement_below_horizontal() -> None:
    """On a horizontal wire, 'below' tucks the label below the wire, inward."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             end_label="$y(t)$", end_label_placement="below")
    src = generate(_schematic(wires=[w]))
    assert r"\node[anchor=north east, inner sep=0] at (3.90,-0.10) {$y(t)$};" in src


def test_wire_start_label_placement_tucks_inward_at_start() -> None:
    """A start label tucks inward from the start endpoint (extends right, not left)."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             start_label="in", start_label_placement="above")
    src = generate(_schematic(wires=[w]))
    # south west: box extends up-right from (start + gap, +gap).
    assert r"\node[anchor=south west, inner sep=0] at (0.10,0.10) {in};" in src


def test_wire_label_placement_vertical_left_and_right() -> None:
    """On a VERTICAL wire, 'above' = left of the wire, 'below' = right (not over it)."""
    left = Wire(id=_uid(), points=[(0.0, 0.0), (0.0, 4.0)],
                end_label="L", end_label_placement="above")
    right = Wire(id=_uid(), points=[(0.0, 0.0), (0.0, 4.0)],
                 end_label="R", end_label_placement="below")
    src_l = generate(_schematic(wires=[left]))
    src_r = generate(_schematic(wires=[right]))
    # Bottom endpoint (0,4); inward is up. Left → right edge left of wire (x<0).
    assert r"\node[anchor=north east, inner sep=0] at (-0.10,3.90) {L};" in src_l
    # Right → left edge right of wire (x>0).
    assert r"\node[anchor=north west, inner sep=0] at (0.10,3.90) {R};" in src_r


def test_wire_label_placement_above_under_yflip() -> None:
    """Under the preview Y-flip, 'above' still sits visually above a horizontal wire."""
    w = Wire(id=_uid(), points=[(0.0, 1.0), (4.0, 1.0)],
             end_label="$y$", end_label_placement="above")
    src = generate(_schematic(wires=[w]), y_flip=True)
    # Coordinates are normalized toward the origin: the wire spans y=1→ floor-shifted
    # so the y-flipped endpoint y becomes 0, and the upward gap puts the label at 0.10.
    assert r"\node[anchor=south east, inner sep=0] at (3.90,0.10) {$y$};" in src


def test_wire_label_empty_emits_no_node() -> None:
    """A wire with no labels emits no label node."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)])
    src = generate(_schematic(wires=[w]))
    assert "anchor=" not in src


def test_wire_label_degenerate_wire_skipped() -> None:
    """A degenerate (single-point) wire emits no label node."""
    w = Wire(id=_uid(), points=[(2.0, 2.0)], end_label="x")
    src = generate(_schematic(wires=[w]))
    assert "{x}" not in src


def test_wire_label_coexists_with_arrow_marker() -> None:
    """An arrow marker and an end label render together (arrow into text)."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             end_marker="arrow", end_label="$y(t)$")
    src = generate(_schematic(wires=[w]))
    assert r"\draw[-{Latex}] (0,0) -- (4,0);" in src
    assert r"\node[anchor=west, inner sep=0] at (4.10,0) {$y(t)$};" in src


def test_wire_mid_label_node_with_white_fill() -> None:
    """A mid-label emits a white-filled node at the fractional midpoint."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], mid_label="$V_{bus}$")
    src = generate(_schematic(wires=[w]))
    assert r"\node[fill=white, inner sep=1pt] at (2,0) {$V_{bus}$};" in src


def test_wire_mid_label_respects_position() -> None:
    """mid_label_pos places the node at the fractional arc-length point."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], mid_label="X", mid_label_pos=0.25)
    src = generate(_schematic(wires=[w]))
    assert r"\node[fill=white, inner sep=1pt] at (1,0) {X};" in src


def test_wire_mid_label_empty_emits_no_node() -> None:
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)])
    assert "fill=white" not in generate(_schematic(wires=[w]))


def test_wire_marker_suppresses_ocirc_at_that_end() -> None:
    """The marked end gets no \\node[ocirc]; the unmarked end still does."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], end_marker="arrow")
    src = generate(_schematic(wires=[w]))
    assert r"\node[ocirc] at (0,0) {};" in src        # unmarked end keeps terminal
    assert r"\node[ocirc] at (4,0) {};" not in src    # marked end suppressed


def test_no_junction_dots_wire_suppresses_circ() -> None:
    """A wire flagged no_junction_dots emits no \\node[circ] at its T-junction."""
    main = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)])
    branch = Wire(id=_uid(), points=[(2.0, 0.0), (2.0, 2.0)], no_junction_dots=True)
    src = generate(_schematic(wires=[main, branch]))
    assert r"\node[circ]" not in src
    # Without the flag the same topology DOES produce a junction dot.
    branch_on = Wire(id=_uid(), points=[(2.0, 0.0), (2.0, 2.0)])
    assert r"\node[circ] at (2,0) {};" in generate(_schematic(wires=[main, branch_on]))


def test_no_termination_dots_wire_suppresses_ocirc() -> None:
    """A wire flagged no_termination_dots emits no \\node[ocirc] at its ends."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)], no_termination_dots=True)
    assert r"\node[ocirc]" not in generate(_schematic(wires=[w]))
    # The same free wire without the flag DOES get open-circle terminals.
    plain = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)])
    assert r"\node[ocirc] at (0,0) {};" in generate(_schematic(wires=[plain]))


def test_mark_unconnected_pins_off_by_default() -> None:
    """A lone resistor emits no ocirc unless the option is set."""
    s = _schematic(_comp("R", position=(0.0, 0.0)))
    assert r"\node[ocirc]" not in generate(s)


def test_mark_unconnected_pins_marks_dangling_pins() -> None:
    """With the option on, both free pins of a lone resistor get an ocirc."""
    s = _schematic(_comp("R", position=(0.0, 0.0)))   # pins at (0,0) and (2,0)
    src = generate(s, mark_unconnected_pins=True)
    assert r"\node[ocirc] at (0,0) {};" in src
    assert r"\node[ocirc] at (2,0) {};" in src


def test_mark_unconnected_pins_skips_wired_pin() -> None:
    """A pin with a wire on it gets no ocirc even when the option is on."""
    r = _comp("R", position=(0.0, 0.0))   # pins at (0,0) and (2,0)
    s = _schematic(r, wires=[_wire([(2.0, 0.0), (5.0, 0.0)])])
    src = generate(s, mark_unconnected_pins=True)
    assert r"\node[ocirc] at (2,0) {};" not in src   # wired
    assert r"\node[ocirc] at (0,0) {};" in src       # dangling


def test_mark_unconnected_pins_respects_y_flip() -> None:
    """Marked pins honor the y_flip convention like every other coordinate."""
    s = _schematic(_comp("V", position=(0.0, 0.0)))   # vertical: pins (0,0),(0,2)
    src = generate(s, y_flip=True, mark_unconnected_pins=True)
    assert r"\node[ocirc] at (0,0) {};" in src
    assert r"\node[ocirc] at (0,-2) {};" in src


def test_mark_unconnected_pins_voltage_annotation_not_a_connection() -> None:
    """A voltage annotation (`open`) on a pin does not suppress its ocirc."""
    r = _comp("R", position=(0.0, 0.0))         # pins (0,0),(2,0)
    va = _comp("open", position=(2.0, 0.0))     # annotation pin coincides with (2,0)
    src = generate(_schematic(r, va), mark_unconnected_pins=True)
    assert r"\node[ocirc] at (2,0) {};" in src  # real pin still flagged
    assert r"\node[ocirc] at (0,0) {};" in src


# ---------------------------------------------------------------------------
# Drawing annotations
# ---------------------------------------------------------------------------

def test_text_node_basic() -> None:
    r"""text_node emits \node at (x,y) {text}; outside the \draw block.

    Coordinates are normalized toward the origin, so (2,3) emits at (0,0).
    """
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(2.0, 3.0),
        rotation=0, options="Hello", mirror=False,
    )
    src = generate(_schematic(comp))
    assert r"\node at (0,0) {Hello};" in src


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
    r"""text_node with y_flip=True negates the y coordinate.

    With normalization toward the origin, the lone node lands at (0,0)
    both before and after the y-flip.
    """
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(2.0, 3.0),
        rotation=0, options="Flip", mirror=False,
    )
    src = generate(_schematic(comp), y_flip=True)
    assert r"\node at (0,0) {Flip};" in src


def test_text_node_rotation() -> None:
    r"""text_node rotation=90 → rotate=270 (negated so CW-visual on canvas maps to CW in TikZ)."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(1.0, 2.0),
        rotation=90, options="Hello", mirror=False,
    )
    src = generate(_schematic(comp))
    # Coordinates are normalized toward the origin: (1,2) emits at (0,0).
    assert r"\node[rotate=270] at (0,0) {Hello};" in src


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
    r"""rect with no style → \draw (x1,y1) rectangle (x2,y2); (no brackets).

    Coordinates are normalized toward the origin: the corner at (-0.5,-0.5)
    is floor-shifted by (-1,-1), so the rect emits at (0.5,0.5)..(5.5,1.5).
    """
    comp = RectComponent(
        id=_uid(), kind="rect", position=(-0.5, -0.5),
        rotation=0, options="", mirror=False,
        span_override=(5.0, 1.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw (0.5,0.5) rectangle (5.5,1.5);" in src


def test_rect_dashed() -> None:
    r"""rect with line_style="dashed" → \draw[dashed] ... rectangle ...;"""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 2.0), line_style="dashed",
    )
    src = generate(_schematic(comp))
    assert r"\draw[dashed] (0,0) rectangle (2,2);" in src


def test_rect_uses_default_span_when_none() -> None:
    """rect with span_override=None falls back to the registry default_span (1,1)."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
    )
    src = generate(_schematic(comp))
    assert "rectangle (1,1)" in src


def test_rect_no_text_emits_only_rectangle() -> None:
    """A text-free rect emits only its \\draw rectangle line — no text \\node."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False, span_override=(2.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw (0,0) rectangle (2,2);" in src
    # No \node at all: a lone rect has no wires (so no circ/ocirc) and no text.
    assert r"\node" not in src


def test_rect_with_text_emits_centred_node() -> None:
    r"""A rect with text emits the rectangle plus a centred \node{text} at its centre."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="$H(s)$", mirror=False, span_override=(4.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw (0,0) rectangle (4,2);" in src
    # Centred at the rect centre (2,1); default font → no [font=...] bracket.
    assert r"\node at (2,1) {$H(s)$};" in src


def test_rect_text_font_options() -> None:
    r"""Bold + sans + size on a rect's text are encoded into the node's font= option."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="Block", mirror=False, span_override=(2.0, 2.0),
        font_size=10.0, font_bold=True, font_family="sans",
    )
    src = generate(_schematic(comp))
    assert r"\fontsize{10}" in src
    assert r"\bfseries" in src
    assert r"\sffamily" in src
    assert "at (1,1) {Block};" in src


def test_circle_square_emits_circle() -> None:
    r"""A square circle emits \draw[style] (cx,cy) circle (r); centred on the box."""
    comp = CircleComponent(
        id=_uid(), kind="circle", position=(0.0, 0.0),
        rotation=0, options="", mirror=False, span_override=(2.0, 2.0),
        fill_color="gray!15",
    )
    src = generate(_schematic(comp))
    assert r"\draw[fill=gray!15] (1,1) circle (1);" in src


def test_circle_nonsquare_emits_ellipse() -> None:
    r"""A non-square circle emits \draw (cx,cy) ellipse (rx and ry);.

    Coordinates are normalized toward the origin, so the centre emits at (2,1).
    """
    comp = CircleComponent(
        id=_uid(), kind="circle", position=(1.0, 1.0),
        rotation=0, options="", mirror=False, span_override=(4.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw (2,1) ellipse (2 and 1);" in src


def test_circle_no_text_emits_only_shape() -> None:
    """A text-free circle emits only its outline — no \\node."""
    comp = CircleComponent(
        id=_uid(), kind="circle", position=(0.0, 0.0),
        rotation=0, options="", mirror=False, span_override=(2.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw (1,1) circle (1);" in src
    assert r"\node" not in src


def test_circle_with_text_emits_centred_node() -> None:
    r"""A circle with text emits the shape plus a centred \node{text}."""
    comp = CircleComponent(
        id=_uid(), kind="circle", position=(0.0, 0.0),
        rotation=0, options="$\\Sigma$", mirror=False, span_override=(2.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"\draw (1,1) circle (1);" in src
    assert r"\node at (1,1) {$\Sigma$};" in src


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
    r"""rect with line_style + border_width → emitted in the \draw[...] arg."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 2.0), line_style="dashed", border_width=1.5,
    )
    src = generate(_schematic(comp))
    assert r"\draw[dashed, line width=1.5pt] (0,0) rectangle (2,2);" in src


def test_rect_with_fill() -> None:
    r"""rect with fill_color → emitted as \draw[fill=...] ... rectangle ...;"""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 2.0), fill_color="yellow!20",
    )
    src = generate(_schematic(comp))
    assert r"\draw[fill=yellow!20] (0,0) rectangle (2,2);" in src


def test_rect_z_order_background_before_draw_block() -> None:
    """A rect with z_order=-1 is emitted before the \\draw block."""
    r = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 2.0), z_order=-1, line_style="dashed",
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


# ---------------------------------------------------------------------------
# Bipole component tests
# ---------------------------------------------------------------------------

from app.components.model import BipoleComponent


def test_bipole_basic() -> None:
    r"""bipole emits \node[draw, minimum width=W, minimum height=0.5cm, font=\fontsize{7}{8.4}\selectfont] at (...) {};"""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(3.0, 0.0),
    )
    src = generate(_schematic(comp))
    assert "minimum width=3cm" in src
    assert "minimum height=0.5cm" in src
    assert r"\fontsize{7}{8.4}\selectfont" in src
    assert "at (1.5,0) {};" in src


def test_bipole_with_label() -> None:
    r"""bipole with t=Processor → \node[...] at (...) {Processor};"""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="t=Processor", mirror=False,
        span_override=(3.0, 0.0),
    )
    src = generate(_schematic(comp))
    assert "minimum width=3cm" in src
    assert "{Processor};" in src


def test_bipole_default_span() -> None:
    """bipole with span_override=None falls back to registry default_span (1,0)."""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(1.0, 2.0),
        rotation=0, options="", mirror=False,
    )
    src = generate(_schematic(comp))
    # default span=1: width=1cm. Coordinates are normalized toward the origin,
    # so the node centre emits at (0.5,0).
    assert "minimum width=1cm" in src
    assert "at (0.5,0)" in src


def test_bipole_resizable_span() -> None:
    """bipole with custom span_override uses the overridden width as minimum width."""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="t=ADC", mirror=False,
        span_override=(4.0, 0.0),
    )
    src = generate(_schematic(comp))
    assert "minimum width=4cm" in src
    assert "{ADC}" in src


def test_bipole_outside_draw_block() -> None:
    """bipole is emitted as a standalone \\node, not inside the \\draw block."""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="t=DSP", mirror=False,
        span_override=(2.0, 0.0),
    )
    src = generate(_schematic(comp))
    draw_block = src.split(r"\draw")[1].split(";")[0]
    assert r"\node" not in draw_block
    assert r"\node[draw" in src


def test_bipole_fill_color() -> None:
    """bipole with fill_color emits fill=... in the node options."""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="t=CPU", mirror=False,
        span_override=(2.0, 0.0),
        fill_color="yellow!20",
    )
    src = generate(_schematic(comp))
    assert "fill=yellow!20" in src


def test_bipole_border_width() -> None:
    """bipole with non-default border_width emits line width=... in the node options."""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 0.0),
        border_width=1.5,
    )
    src = generate(_schematic(comp))
    assert "line width=1.5pt" in src


def test_bipole_default_border_width_omitted() -> None:
    """bipole at default border_width (0.4pt) does not emit line width."""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 0.0),
    )
    src = generate(_schematic(comp))
    assert "line width" not in src


def test_bipole_line_style() -> None:
    """bipole with line_style emits the style token in the node options."""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="t=CPU", mirror=False,
        span_override=(2.0, 0.0), line_style="dashed",
    )
    src = generate(_schematic(comp))
    assert "dashed" in src


def test_rect_line_style_and_fill_combined() -> None:
    r"""rect with line_style + fill composes both into the \draw[...] arg."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 2.0), line_style="dotted", fill_color="cyan!15",
    )
    src = generate(_schematic(comp))
    assert r"\draw[dotted, fill=cyan!15] (0,0) rectangle (2,2);" in src


# ---------------------------------------------------------------------------
# build_snippet — includable .tex export (§8.5)
# ---------------------------------------------------------------------------

def test_build_snippet_wraps_environment() -> None:
    """build_snippet prepends a preamble comment and keeps the environment."""
    from app.preview.latex import build_snippet

    src = generate(_schematic(_comp("R")), y_flip=True)
    snippet = build_snippet(src)
    assert r"\begin{circuitikz}" in snippet
    assert r"\end{circuitikz}" in snippet
    # The original source is included verbatim.
    assert src in snippet


def test_build_snippet_lists_required_preamble() -> None:
    """The snippet documents the packages the host document must load."""
    from app.preview.latex import build_snippet

    snippet = build_snippet(generate(_schematic(_comp("R")), y_flip=True))
    assert r"\usepackage[american]{circuitikz}" in snippet
    assert r"\usetikzlibrary{arrows.meta}" in snippet  # for wire endpoint markers
    assert r"\input" in snippet


def test_build_tex_loads_arrows_meta() -> None:
    """The standalone template loads arrows.meta so wire markers compile."""
    from app.preview.latex import build_tex

    tex = build_tex(generate(_schematic(_comp("R")), y_flip=True))
    assert r"\usetikzlibrary{arrows.meta}" in tex


def test_build_snippet_has_no_document_wrapper() -> None:
    r"""A snippet is includable, not standalone: no \documentclass/\begin{document}."""
    from app.preview.latex import build_snippet

    snippet = build_snippet(generate(_schematic(_comp("R")), y_flip=True))
    assert r"\documentclass" not in snippet
    assert r"\begin{document}" not in snippet


# ---------------------------------------------------------------------------
# pdf_to_eps — EPS image export (§8.6)
# ---------------------------------------------------------------------------

def test_pdf_to_eps_missing_tool(monkeypatch) -> None:
    """pdf_to_eps raises CompileError when pdftocairo is absent."""
    from app.preview import latex, tools

    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    tools.set_tool_paths({})  # no explicit override -> falls through to (patched) PATH
    with pytest.raises(latex.CompileError, match="pdftocairo"):
        latex.pdf_to_eps(b"%PDF-1.4")


@pytest.mark.skipif(
    __import__("shutil").which("pdflatex") is None
    or __import__("shutil").which("pdftocairo") is None,
    reason="requires pdflatex and pdftocairo",
)
def test_pdf_to_eps_roundtrip() -> None:
    """A compiled schematic PDF converts to a valid EPS document."""
    from app.preview.latex import build_tex, compile_tex, pdf_to_eps

    src = generate(_schematic(_comp("R")), y_flip=True)
    pdf_bytes = compile_tex(build_tex(src))
    eps_bytes = pdf_to_eps(pdf_bytes)
    assert eps_bytes.startswith(b"%!PS-Adobe")
    assert b"EPSF" in eps_bytes[:64]
    assert b"%%BoundingBox" in eps_bytes


# ---------------------------------------------------------------------------
# pdf_to_svg — SVG image export (§8.6), shares Poppler with EPS (no new dep)
# ---------------------------------------------------------------------------

def test_pdf_to_svg_missing_tool(monkeypatch) -> None:
    """pdf_to_svg raises CompileError when pdftocairo is absent."""
    from app.preview import latex, tools

    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    tools.set_tool_paths({})
    with pytest.raises(latex.CompileError, match="pdftocairo"):
        latex.pdf_to_svg(b"%PDF-1.4")


@pytest.mark.skipif(
    __import__("shutil").which("pdflatex") is None
    or __import__("shutil").which("pdftocairo") is None,
    reason="requires pdflatex and pdftocairo",
)
def test_pdf_to_svg_roundtrip() -> None:
    """A compiled schematic PDF converts to a valid SVG document."""
    from app.preview.latex import build_tex, compile_tex, pdf_to_svg

    src = generate(_schematic(_comp("R")), y_flip=True)
    pdf_bytes = compile_tex(build_tex(src))
    svg_bytes = pdf_to_svg(pdf_bytes)
    assert b"<svg" in svg_bytes[:512]


# ---------------------------------------------------------------------------
# _ensure_tool_dirs_on_path — macOS GUI PATH augmentation (packaging)
# ---------------------------------------------------------------------------

def test_ensure_tool_dirs_adds_existing_dir(monkeypatch, tmp_path) -> None:
    """An existing tool dir missing from PATH is appended (macOS)."""
    from app.preview import tools

    monkeypatch.setattr(tools.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tools, "_MAC_TOOL_DIRS", (str(tmp_path),))
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    tools.ensure_tool_dirs_on_path()
    assert str(tmp_path) in os.environ["PATH"].split(os.pathsep)


def test_ensure_tool_dirs_idempotent(monkeypatch, tmp_path) -> None:
    """Calling twice does not duplicate the appended dir."""
    from app.preview import tools

    monkeypatch.setattr(tools.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tools, "_MAC_TOOL_DIRS", (str(tmp_path),))
    monkeypatch.setenv("PATH", "/usr/bin")
    tools.ensure_tool_dirs_on_path()
    tools.ensure_tool_dirs_on_path()
    assert os.environ["PATH"].split(os.pathsep).count(str(tmp_path)) == 1


def test_ensure_tool_dirs_skips_missing_dir(monkeypatch) -> None:
    """A non-existent tool dir is never added."""
    from app.preview import tools

    monkeypatch.setattr(tools.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(tools, "_MAC_TOOL_DIRS", ("/no/such/dir/xyz",))
    monkeypatch.setenv("PATH", "/usr/bin")
    tools.ensure_tool_dirs_on_path()
    assert "/no/such/dir/xyz" not in os.environ["PATH"].split(os.pathsep)


def test_ensure_tool_dirs_noop_off_darwin(monkeypatch, tmp_path) -> None:
    """Off macOS the function makes no change to PATH."""
    from app.preview import tools

    monkeypatch.setattr(tools.platform, "system", lambda: "Linux")
    monkeypatch.setattr(tools, "_MAC_TOOL_DIRS", (str(tmp_path),))
    monkeypatch.setenv("PATH", "/usr/bin")
    tools.ensure_tool_dirs_on_path()
    assert os.environ["PATH"] == "/usr/bin"


# ---------------------------------------------------------------------------
# Line-hops and wire z-layering
# ---------------------------------------------------------------------------

def _crossing() -> Schematic:
    """Horizontal wire (z=1, hops) crossing a vertical wire (z=0) at (2,1)."""
    h = Wire(id="h", points=[(0.0, 1.0), (4.0, 1.0)], z_order=1)
    v = Wire(id="v", points=[(2.0, 0.0), (2.0, 3.0)], z_order=0)
    return _schematic(wires=(h, v))


def test_line_hops_disabled_by_default() -> None:
    """generate() emits no bump unless mark_line_hops is requested."""
    src = generate(_crossing())
    assert "controls" not in src
    assert "(0,1) -- (4,1)" in src   # plain straight wire


def test_always_hop_mode_emits_even_when_disabled() -> None:
    """A wire with hop_mode='always' emits a bump even with mark_line_hops off."""
    h = Wire(id="h", points=[(0.0, 1.0), (4.0, 1.0)], hop_mode="always")
    v = Wire(id="v", points=[(2.0, 0.0), (2.0, 3.0)])
    src = generate(_schematic(wires=(h, v)))     # mark_line_hops defaults False
    assert "controls" in src


def test_line_hops_emits_bezier_bump() -> None:
    """With mark_line_hops, the hopping wire gets a cubic-Bezier bump at (2,1)."""
    src = generate(_crossing(), mark_line_hops=True)
    assert "controls" in src
    # The bump approaches (2-r,1) and resumes at (2+r,1) on the y=1 wire.
    assert "(1.92,1)" in src and "(2.08,1)" in src


def test_line_hop_bump_bulges_up_without_yflip() -> None:
    """A horizontal hopper bulges to smaller y (canvas up): control y = 1-(4/3)*0.08."""
    src = generate(_crossing(), mark_line_hops=True)
    # _fmt rounds to 2 dp: 0.8933… → 0.89.
    assert "controls (1.92,0.89) and (2.08,0.89)" in src


def test_line_hop_bump_flips_with_yflip() -> None:
    """Under y_flip the whole bump negates, so the control y becomes -0.89."""
    src = generate(_crossing(), mark_line_hops=True, y_flip=True)
    assert "controls (1.92,-0.89) and (2.08,-0.89)" in src


def test_zero_z_wire_keeps_shared_draw_path() -> None:
    """A default-layer (z=0) wire stays in the shared \\draw path (no churn)."""
    w = Wire(id="w", points=[(0.0, 0.0), (4.0, 0.0)])
    src = generate(_schematic(wires=(w,)))
    assert "(0,0) -- (4,0)" in src


def test_negative_z_wire_emitted_before_draw_block() -> None:
    """A z<0 wire is emitted in the background, before the \\draw block."""
    w = Wire(id="bg", points=[(0.0, 0.0), (4.0, 0.0)], z_order=-5)
    src = generate(_schematic(wires=(w,)))
    bg_idx = src.index(r"\draw (0,0) -- (4,0);")
    draw_idx = src.index("\\draw\n")
    assert bg_idx < draw_idx     # background wire precedes the main \draw


def test_positive_z_wire_emitted_after_draw_block() -> None:
    """A z>0 wire is emitted in the foreground, after the main \\draw path closes."""
    w = Wire(id="fg", points=[(0.0, 0.0), (4.0, 0.0)], z_order=5)
    src = generate(_schematic(wires=(w,)))
    fg_idx = src.index(r"\draw (0,0) -- (4,0);")
    semi_idx = src.index("\n  ;")        # end of the shared \draw path
    assert fg_idx > semi_idx


def test_background_wire_to_mosfet_uses_absolute_coords() -> None:
    """A background (z<0) wire on a MOSFET pin must use absolute coordinates, not
    a named node anchor — the node is defined later in the main \\draw, so a
    forward reference like (node_x.gate) would be a LaTeX compile error.

    Coordinates are normalized toward the origin: the schematic at (5,5)→(3,5)
    is floor-shifted, so the bg wire emits at the absolute (2,1)→(0,1).
    """
    fet = Component(
        id=_uid(), kind="nigfete", position=(5.0, 5.0),
        rotation=0, options="", mirror=False,
    )
    # gate pin is at the component position (offset 0,0); wire from it, sent back.
    w = Wire(id="g", points=[(5.0, 5.0), (3.0, 5.0)], z_order=-1)
    src = generate(_schematic(fet, wires=(w,)))
    bg = src.split("\n  \\draw\n", 1)[0]      # everything before the main \draw
    assert "node_" not in bg                  # no forward anchor reference
    assert "(2,1) -- (0,1)" in bg             # the bg wire, with absolute coords


def test_foreground_wire_to_mosfet_keeps_named_anchor() -> None:
    """A foreground (z>0) wire is emitted after the node, so it keeps the named
    anchor reference (which resolves fine)."""
    fet = Component(
        id=_uid(), kind="nigfete", position=(5.0, 5.0),
        rotation=0, options="", mirror=False,
    )
    w = Wire(id="g", points=[(5.0, 5.0), (3.0, 5.0)], z_order=1)
    src = generate(_schematic(fet, wires=(w,)))
    assert ".gate)" in src                    # references the MOSFET gate anchor


# ---------------------------------------------------------------------------
# Parametric logic gates (variable input count)
# ---------------------------------------------------------------------------

def test_logic_gate_emits_height_group_no_yscale():
    """A parametric gate is emitted in a local group that sets its body height
    (so inputs land on grid without a node yscale that would oval the bubble):
    { \\ctikzset{…/height=H}  \\draw … node[and port, number inputs=N, xscale=…]; }."""
    # Default value (2 inputs).
    src2 = generate(_schematic(_comp("and")))
    assert "node[and port, number inputs=2, xscale=0.974" in src2
    assert "anchor=out" in src2
    assert "yscale" not in src2                                    # height, not yscale
    assert r"\ctikzset{tripoles/american and port/height=0.7143}" in src2
    # the height is set in a group, before the node, and reverts:
    assert src2.index(r"\ctikzset{tripoles/american and port/height") < src2.index("node[and port")

    # Explicit 4 inputs: number inputs=4 and the 4-input height (full precision).
    c = Component(id=_uid(), kind="and", position=(0.0, 0.0), rotation=0,
                  options="", params={"inputs": 4})
    src4 = generate(_schematic(c))
    assert "number inputs=4" in src4 and "yscale" not in src4
    assert "tripoles/american and port/height=1.4286" in src4
