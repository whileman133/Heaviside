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


def _comp(kind: str, position=(0.0, 0.0), rotation=0, options="", mirror=False,
          node_text="") -> Component:
    return Component(
        id=_uid(),
        kind=kind,
        position=position,
        rotation=rotation,
        options=options,
        mirror=mirror,
        node_text=node_text,
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
    assert "to[european voltage source]" in generate(_schematic(_comp("eV")))
    assert "to[european current source]" in generate(_schematic(_comp("eI")))
    assert "to[variable european resistor]" in generate(_schematic(_comp("evR")))
    assert "to[european potentiometer]" in generate(_schematic(_comp("epot")))
    assert "to[european resistive sensor]" in generate(_schematic(_comp("ethermistor")))
    assert "to[pR]" in generate(_schematic(_comp("pR")))  # american potentiometer


def test_european_logic_gates_emit_keywords() -> None:
    """European logic gates emit `node[european … port]`; the parametric AND keeps
    its scoped european height setting."""
    assert "node[european not port" in generate(_schematic(_comp("enot")))
    src = generate(_schematic(_comp("eand")))
    assert "node[european and port" in src
    assert r"\ctikzset{tripoles/european and port/height" in src


def test_battery_and_inst_amp_emit_keywords() -> None:
    assert "to[battery]" in generate(_schematic(_comp("battery")))
    assert "node[inst amp" in generate(_schematic(_comp("instamp")))
    assert "node[gm amp" in generate(_schematic(_comp("gmamp")))


def test_mirror_emits_mirror_option_for_two_terminal() -> None:
    """A mirrored two-terminal bipole adds CircuiTikZ's `mirror` key so off-axis
    features (e.g. an LED's emission arrows) land on the same side as the canvas
    Flip-X; unmirrored output is unchanged (regression for the canvas/LaTeX
    mirror mismatch)."""
    assert "to[leD, mirror]" in generate(_schematic(_comp("leD", mirror=True)))
    assert "mirror" not in generate(_schematic(_comp("leD", mirror=False)))
    # With a label the key precedes the label.
    assert "to[D, mirror, l=$D$]" in generate(
        _schematic(_comp("D", mirror=True, options="l=$D$"))
    )


def test_mirror_terminal_placement_matches_canvas_flip_x() -> None:
    """Mirror is the canvas global Flip-X applied *after* rotation, so the far
    terminal is the rotated span with its world x negated — never the
    mirror-before-rotation result, which lands on the opposite side at 90°/270°.

    The emitted endpoints (after ``generate`` shifts the figure to the origin)
    place the bipole exactly where the canvas draws it, so connected wires stay
    attached. (No Y-flip here — pure model coordinates.)
    """
    def line(rot: int) -> str:
        src = generate(_schematic(_comp("R", rotation=rot, mirror=True, options="l=$R$")))
        return next(ln.strip() for ln in src.splitlines() if "to[" in ln)

    # rot 0/180: horizontal bipole — Flip-X reverses it along its own axis.
    assert line(0) == "(2,0) to[R, mirror, l=$R$] (0,0)"
    assert line(180) == "(0,0) to[R, mirror, l=$R$] (2,0)"
    # rot 90/270: vertical bipole — Flip-X leaves the on-axis terminals in place
    # (the span is unchanged from the unmirrored component), only the symbol's
    # perpendicular features flip via the ``mirror`` key.
    assert line(90) == "(0,0) to[R, mirror, l=$R$] (0,2)"
    assert line(270) == "(0,2) to[R, mirror, l=$R$] (0,0)"


def test_mirror_at_90_keeps_two_terminal_wire_connected() -> None:
    """Mirroring a vertical (90°) two-terminal component must keep its ``to[...]``
    endpoints on the same grid cells as the unmirrored one, so a wire joined to
    the far terminal stays attached. Regression: the boost converter's vertical
    load resistor detached from the ground rail when mirrored because codegen
    mirrored *before* rotating, flipping the far terminal across the origin."""
    def endpoints(mirror: bool) -> str:
        r = _comp("R", position=(4.0, 4.0), rotation=90, mirror=mirror)
        # A wire from the far terminal (4,6) down to ground.
        w = _wire([(4.0, 6.0), (4.0, 8.0)])
        src = generate(_schematic(r, wires=[w]))
        return next(ln.strip() for ln in src.splitlines() if "to[" in ln)

    # The resistor spans the same two cells whether or not it is mirrored; the
    # mirror only adds the ``mirror`` key.
    assert endpoints(False) == "(0,0) to[R] (0,2)"
    assert endpoints(True) == "(0,0) to[R, mirror] (0,2)"


def test_switches_and_choke_emit_keywords() -> None:
    assert "to[nos]" in generate(_schematic(_comp("nos")))
    assert "to[ncs]" in generate(_schematic(_comp("ncs")))
    assert "to[push button]" in generate(_schematic(_comp("pushbutton")))
    assert "to[cute choke]" in generate(_schematic(_comp("choke")))
    assert "to[opening switch]" in generate(_schematic(_comp("opening")))
    assert "to[closing switch]" in generate(_schematic(_comp("closing")))
    spdt_src = generate(_schematic(_comp("spdt")))
    # The SPDT is a centre-placed scaled node (§4): its per-axis xscale/yscale
    # land the terminals on the grid; no anchor= placement, no bridge leads.
    assert "node[spdt" in spdt_src
    assert "anchor=" not in spdt_src
    assert "xscale=" in spdt_src and "yscale=" in spdt_src


# ---------------------------------------------------------------------------
# Document voltage/current label styles (§7.2)
# ---------------------------------------------------------------------------

def test_american_style_emits_no_ctikzset() -> None:
    """The default (american) styles add no voltage/current ctikzset line, so
    existing output is byte-for-byte unchanged."""
    src = generate(_schematic(_comp("R", options="v=$V$, i=$I$")))
    assert "voltage=" not in src
    assert "current=" not in src


def test_european_voltage_emitted_locally_per_annotation() -> None:
    """The european convention is applied as a **local** `voltage=european` option
    on each annotated component, NOT a global `\\ctikzset` (which would also
    restyle component symbols). A component with no v= carries no such option."""
    s = _schematic(_comp("R", options="v=$V$"), _comp("R", position=(3.0, 0.0)))
    s.voltage_style = "european"
    src = generate(s)
    assert "ctikzset{voltage=european" not in src          # not global
    assert "voltage=european" in src                       # but present locally
    r_lines = [l.strip() for l in src.splitlines() if "to[R" in l]
    assert any("voltage=european" in l and "v=$V$" in l for l in r_lines)
    assert any("voltage=european" not in l for l in r_lines)  # the plain R is clean
    assert "current=european" not in src


def test_european_both_styles_combined_locally() -> None:
    """A component with both v= and i= gets both local options; no global ctikzset."""
    s = _schematic(_comp("R", options="v=$V$, i=$I$"))
    s.voltage_style = "european"
    s.current_style = "european"
    src = generate(s)
    assert "ctikzset{voltage" not in src and "ctikzset{current" not in src
    assert "voltage=european" in src and "current=european" in src


# ---------------------------------------------------------------------------
# test_resistor_horizontal
# ---------------------------------------------------------------------------

def test_resistor_horizontal() -> None:
    """Single resistor at (0,0), rotation 0, no labels → (0,0) to[R] (2,0)."""
    src = generate(_schematic(_comp("R")))
    assert "(0,0) to[R] (2,0)" in src


@pytest.mark.parametrize("kind", ["C", "L", "D", "zD", "leD"])
def test_two_terminal_keyword_emission(kind: str) -> None:
    """Every horizontal two-terminal kind emits `(0,0) to[<keyword>] (2,0)` — one
    shared codegen path, so the resistor (geometry) + these (keyword mapping) +
    the vertical voltage source below cover it; the kind→keyword map itself is
    guarded by the registry/library tests."""
    src = generate(_schematic(_comp(kind)))
    assert f"(0,0) to[{kind}] (2,0)" in src


@pytest.mark.parametrize("kind", ["D", "zD"])
def test_filled_variant_emission(kind: str) -> None:
    """The `filled` variant appends the `*` suffix (shared variant_tikz path)."""
    comp = Component(id=_uid(), kind=kind, position=(0.0, 0.0), rotation=0,
                     options="", variants={"filled": True})
    assert f"(0,0) to[{kind}*] (2,0)" in generate(_schematic(comp))


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


def test_label_value_with_equals_is_brace_protected() -> None:
    """A label value containing an equals sign (e.g. `l=$v=2$`) is brace-protected
    so pgfkeys does not split on the inner `=` and emit broken LaTeX — a value
    with no comma/`=` (e.g. v=$V_s$) stays unbraced (regression)."""
    comp = _comp("R", options=r"l=$v=2$, v=$V_s$")
    src = generate(_schematic(comp))
    assert r"to[R, l={$v=2$}, v=$V_s$]" in src


def test_comma_free_label_value_left_unwrapped() -> None:
    """Values without commas are emitted verbatim (no needless braces)."""
    comp = _comp("R", options="l=$R_1$")
    src = generate(_schematic(comp))
    assert "to[R, l=$R_1$]" in src


def test_protect_label_values_unit() -> None:
    """protect_label_values wraps values bearing a comma or an inner `=`,
    idempotently, leaving plain values and flags alone."""
    from app.components.style import protect_label_values as p

    assert p(r"v=$\phi(0,0)$") == r"v={$\phi(0,0)$}"      # comma → wrapped
    assert p(r"l=$v=2$") == r"l={$v=2$}"                  # inner = → wrapped
    assert p("l=$R_1$") == "l=$R_1$"                      # neither → untouched
    assert p(r"l=$v=2$, v=$V_s$") == r"l={$v=2$}, v=$V_s$"
    assert p(r"v={$\phi(0,0)$}") == r"v={$\phi(0,0)$}"    # already a brace group
    assert p("mirror, scale=2") == "mirror, scale=2"      # plain value/flags unaffected
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
    # Centre-placed and per-axis scaled (§4) — xscale only (yscale==1 omitted).
    assert "node[op amp, xscale=1.0504]" in src
    assert "anchor=" not in src


# ---------------------------------------------------------------------------
# node_text — the node-style {…} slot (§5, #32)
# ---------------------------------------------------------------------------

def test_node_text_multi_terminal_inline_in_node_braces() -> None:
    """A multi-terminal node's text goes inline in the node's own {…} — the
    CircuiTikZ-idiomatic ``node[npn] (id) {$Q_1$}`` form."""
    src = generate(_schematic(_comp("npn", position=(2.0, 2.0), node_text="$Q_1$")))
    assert "node[npn" in src and ") {$Q_1$}" in src
    assert "inner sep=0" not in src              # no separate/chained label node
    # No node_text → empty braces.
    bare = generate(_schematic(_comp("npn", position=(2.0, 2.0))))
    assert "(node_" in bare and ") {}" in bare


def test_node_text_on_single_terminal_node() -> None:
    """A single-terminal node (power rail) renders node_text in {…} and its options
    in the node[…] bracket (no l=→label hack)."""
    src = generate(_schematic(_comp("vcc", node_text="$V_{cc}$", options="color=blue")))
    assert "node[vcc, color=blue] {$V_{cc}$}" in src
    assert "label=right" not in src                     # legacy hack is gone


def test_node_text_ignored_for_path_style() -> None:
    """A path-style to[…] component has no {…} slot, so node_text never appears."""
    src = generate(_schematic(_comp("R", options="l=$R_1$", node_text="ignored")))
    assert "to[" in src and "ignored" not in src


def test_node_text_braces_are_balanced() -> None:
    """An unbalanced brace in node_text is escaped so it can't escape the {…} group."""
    src = generate(_schematic(_comp("npn", position=(2.0, 2.0), node_text="a}b")))
    # The stray } is escaped (balance_braces), keeping the {…} group well-formed.
    assert r"{a\}b}" in src


@pytest.mark.parametrize("kind", ["npn", "pnp", "op amp", "nigfete", "vcc", "vdd", "vee", "ground"])
def test_node_text_always_present_in_source(kind: str) -> None:
    """Invariant: node text the user set MUST appear in the generated CircuiTikZ
    source (which is what the GUI displays and what is compiled), for every
    node-style kind — single- and multi-terminal alike. The source the user sees
    must match what is actually rendered."""
    src = generate(_schematic(_comp(kind, position=(2.0, 2.0), node_text="$X_7$")))
    assert "$X_7$" in src, f"node text missing from source for {kind!r}"


# ---------------------------------------------------------------------------
# Digital blocks — native flipflop / muxdemux shapes (centre-placed + leads)
# ---------------------------------------------------------------------------

def test_flipflop_d_emits_scaled_node_no_leads() -> None:
    """A D flip-flop emits ``node[flipflop D, xscale=…, yscale=…]`` with the baked
    grid-alignment scale and **no** lead bridges — its pins sit at the scaled
    (grid-aligned) anchors."""
    src = generate(_schematic(_comp("flipflop D", position=(4.0, 4.0))))
    assert "node[flipflop D, xscale=" in src and "yscale=" in src
    assert ") -- " not in src     # no bridge leads; pins are at the scaled anchors


def test_flipflop_pins_are_grid_aligned() -> None:
    """The alignment scale lands all four flip-flop pins on the 0.25-GU grid."""
    from app.components.library import resolved_pins
    for p in resolved_pins(_comp("flipflop D")):
        assert (p.offset[0] * 4) == int(p.offset[0] * 4)
        assert (p.offset[1] * 4) == int(p.offset[1] * 4)


def test_mux_emits_muxdemux_def() -> None:
    """A multiplexer emits the configurable ``muxdemux`` shape with the concrete
    ``muxdemux def`` baked for its current (inputs, selects) combo."""
    src = generate(_schematic(_comp("mux", position=(4.0, 4.0))))
    assert "node[muxdemux" in src and "muxdemux def=" in src
    assert "NL=2" in src and "NB=1" in src  # default combo: 2 inputs, 1 select


def test_mux_inputs_param_changes_shape() -> None:
    """Bumping the input count re-emits a wider ``muxdemux def`` (NL) and more
    data pins; the select count drives NB independently."""
    from app.schematic.model import Component as SComponent
    comp = SComponent(id=_uid(), kind="mux", position=(4.0, 4.0),
                      rotation=0, options="", params={"inputs": 8, "selects": 3})
    src = generate(_schematic(comp))
    assert "NL=8" in src and "NB=3" in src


def test_alu_and_adder_emit_named_styles() -> None:
    """The ALU and adder use CircuiTikZ's predefined ``ALU`` / ``one bit adder``
    styles, placed as centre nodes with their baked grid-alignment scale."""
    alu = generate(_schematic(_comp("ALU", position=(5.0, 5.0))))
    assert "node[ALU, xscale=" in alu
    add = generate(_schematic(_comp("adder", position=(5.0, 5.0))))
    assert "node[one bit adder, xscale=" in add


def test_digital_block_scale_multiplies_alignment() -> None:
    """A digital block's inspector Size (Component.scale) multiplies its baked
    alignment scale in the emitted xscale/yscale (like a logic gate)."""
    from app.schematic.model import Component as SComponent
    base = generate(_schematic(_comp("flipflop D", position=(4.0, 4.0))))
    comp = SComponent(id=_uid(), kind="flipflop D", position=(4.0, 4.0),
                      rotation=0, options="")
    comp.scale = 2.0
    scaled = generate(_schematic(comp))
    # 2× the ~0.893 baked scale ≈ 1.786
    assert "xscale=0.8929" in base
    assert "xscale=1.7858" in scaled


def test_transformer_emits_scaled_quadpole_node() -> None:
    """A transformer is a centre-placed quadpole node with its baked grid-alignment
    scale, four grid-aligned winding terminals (p1/p2 primary, s1/s2 secondary), and
    the two off-grid winding centre taps (tap_p/tap_s)."""
    from app.components.library import resolved_pins
    for kind in ("transformer", "transformer core"):
        src = generate(_schematic(_comp(kind, position=(4.0, 4.0))))
        assert f"node[{kind}, xscale=" in src and "yscale=" in src
        pins = {p.name: p.offset for p in resolved_pins(_comp(kind))}
        assert set(pins) == {"p1", "p2", "s1", "s2", "tap_p", "tap_s"}
        for name in ("p1", "p2", "s1", "s2"):           # the four terminals on grid
            off = pins[name]
            assert (off[0] * 4) == int(off[0] * 4)
            assert (off[1] * 4) == int(off[1] * 4)


def test_cute_european_transformers_wrap_inductor_ctikzset() -> None:
    """A cute/european transformer sets its coil shape with a scoped
    ``\\ctikzset{inductor=…}`` group (a node option doesn't reach the european
    rectangle); the plain transformer emits no such group."""
    cute = generate(_schematic(_comp("cute transformer core", position=(4.0, 4.0))))
    assert r"\ctikzset{inductor=cute}" in cute and "node[transformer core" in cute
    eu = generate(_schematic(_comp("european transformer", position=(4.0, 4.0))))
    assert r"\ctikzset{inductor=european}" in eu
    plain = generate(_schematic(_comp("transformer core", position=(4.0, 4.0))))
    assert "inductor=" not in plain


def test_transformer_polarity_dots_emit_circ_nodes() -> None:
    """Checked transformer ``dot`` variants emit a ``node[circ]`` at each chosen
    inner-dot anchor; an unchecked transformer emits none."""
    from app.schematic.model import Component as SComponent
    plain = generate(_schematic(_comp("transformer", position=(4.0, 4.0))))
    assert "node[circ]" not in plain
    dotted = generate(_schematic(SComponent(
        id=_uid(), kind="transformer", position=(4.0, 4.0), rotation=0, options="",
        variants={"dot_p1": True, "dot_s2": True})))
    assert "(node_" in dotted and ".inner dot A1) node[circ]{}" in dotted
    assert ".inner dot B2) node[circ]{}" in dotted
    assert ".inner dot A2)" not in dotted and ".inner dot B1)" not in dotted


def test_rotated_or_mirrored_transformer_uses_transform_shape() -> None:
    """A rotated **or** mirrored transformer emits ``transform shape`` (CircuiTikZ
    quadpoles otherwise flip their coils — crossed leads on an odd-90° rotation,
    outward-facing coils when mirrored). A plain transformer and a rotated
    *non*-quadpole node (op amp) do not."""
    rot = generate(_schematic(_comp("transformer core", position=(4.0, 4.0), rotation=90)))
    assert "rotate=" in rot and "transform shape" in rot
    mir = generate(_schematic(_comp("transformer core", position=(4.0, 4.0), mirror=True)))
    assert "xscale=-" in mir and "transform shape" in mir
    flat = generate(_schematic(_comp("transformer core", position=(4.0, 4.0))))
    assert "transform shape" not in flat
    opamp = generate(_schematic(_comp("op amp", position=(4.0, 4.0), rotation=90)))
    assert "rotate=" in opamp and "transform shape" not in opamp


def test_transformer_centre_taps_emit_subnode_anchor_refs() -> None:
    """A wire to a transformer's primary/secondary centre tap connects via the
    internal coil **sub-node** anchor — ``(node-L1.midtap)`` / ``(node-L2.midtap)``
    — not the usual ``(node.anchor)``. Covers every coil style and confirms the
    emitted source compiles under rotation/mirror (the named anchor is reoriented by
    ``transform shape``, like the four winding terminals)."""
    import uuid
    from app.components import render
    from app.schematic.model import Component, Wire, component_pin_positions

    def _stub(pt):                       # extend along whichever axis is on-grid
        x, y = pt
        return (x, y - 1.0) if (x * 4) != int(x * 4) else (x - 1.0, y)

    kinds = ("transformer", "transformer core", "cute transformer",
             "european transformer core")
    for kind in kinds:
        for rot, mir in ((0, False), (90, False), (270, False), (0, True)):
            c = Component(id=str(uuid.uuid4()), kind=kind, position=(6.0, 6.0),
                          rotation=rot, options="", mirror=mir)
            tap_p, tap_s = component_pin_positions(c)[4], component_pin_positions(c)[5]
            wires = [Wire(id=str(uuid.uuid4()), points=[tap_p, _stub(tap_p)]),
                     Wire(id=str(uuid.uuid4()), points=[tap_s, _stub(tap_s)])]
            src = generate(_schematic(c, wires=wires))
            assert "-L1.midtap)" in src and "-L2.midtap)" in src, (kind, rot, mir)
            # the emitted picture body must compile (the sub-node anchors resolve).
            body = "\n".join(l for l in src.splitlines()
                             if not l.startswith(r"\begin") and not l.startswith(r"\end"))
            render.render_svg(body, border_pt=6)   # raises RenderError on failure


def test_opamp_inverting_input_anchor_is_node_relative_not_subnode() -> None:
    """The op-amp's inverting input anchor is literally named ``-``. A wire to it
    must emit the node-relative ``(node.-)`` — *not* the sub-node form ``(node-)``
    that the transformer centre taps use. Guards the `-`+`.` sub-node discriminator
    (`pin_coord_to_ref`), so a bare ``-`` anchor is never mistaken for a sub-node."""
    import uuid
    from app.schematic.model import Component, Wire, component_pin_positions

    c = Component(id=str(uuid.uuid4()), kind="op amp", position=(6.0, 6.0),
                  rotation=0, options="")
    minus = component_pin_positions(c)[1]              # the "-" input pin
    w = Wire(id=str(uuid.uuid4()), points=[minus, (minus[0] - 1.0, minus[1])])
    src = generate(_schematic(c, wires=[w]))
    import re
    assert re.search(r"\(node_[0-9a-f]+\.-\)", src)        # node-relative (node….-)
    assert not re.search(r"\(node_[0-9a-f]+-\)", src)      # never the sub-node form
    """A ``dot`` variant draws a separate mark — it must not alter the node keyword
    or the geometry key (the base symbol is rendered, dots overlaid)."""
    from app.components import library
    suffix, opts = library.variant_tikz("transformer", {"dot_p1": True})
    assert suffix == "" and opts == []
    assert library.variant_geometry_suffix("transformer", {"dot_p1": True}) == ""


def test_digital_blocks_take_no_bipole_label() -> None:
    """The raw pgf shapes are self-labelled (D/Q/CLK glyphs), so the registry
    offers no ``l`` label slot — emitting ``l=`` would be a LaTeX error."""
    from app.components.registry import REGISTRY
    for kind in ("flipflop D", "mux", "demux", "ALU", "adder"):
        assert REGISTRY[kind].label_slots == []


# ---------------------------------------------------------------------------
# test_nmos_node
# ---------------------------------------------------------------------------

# Every BJT/IGFET is the same centre-placed-and-per-axis-scaled node path; one
# parametrized table proves the kind→scale emission (the scale *values* are
# independently verified in test_generate.py::test_best_alignment_*).
_TRANSISTOR_SCALES = [
    ("npn", "xscale=0.8929, yscale=0.974"),
    ("pnp", "xscale=0.8929, yscale=0.974"),
    ("nigfete", "xscale=1.0204, yscale=0.974"),
    ("nigfetd", "xscale=1.0204, yscale=0.974"),
    ("pigfete", "xscale=1.0204, yscale=0.974"),
    ("pigfetd", "xscale=1.0204, yscale=0.974"),
]


@pytest.mark.parametrize("kind, scale", _TRANSISTOR_SCALES)
def test_transistor_node_scale(kind: str, scale: str) -> None:
    """Centre-placed and per-axis scaled so the anchors land on the grid:
    `node[<kind>, <scale>]`, no `anchor=` option (spec/component-pipeline.md §4)."""
    src = generate(_schematic(_comp(kind, position=(2.0, 3.0))))
    assert f"node[{kind}, {scale}]" in src
    assert "anchor=" not in src


def test_npn_no_bridge_leads() -> None:
    """NPN lands its pins by scaling — no bridge lead wires are emitted."""
    comp = _comp("npn", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "xscale=0.8929" in src
    assert ".C) -- " not in src
    assert ".E) -- " not in src


@pytest.mark.parametrize("kind, value", [("nand", "$U$"), ("not", "$Y$")])
def test_gate_label_emitted_as_label_above(kind: str, value: str) -> None:
    """Logic-port shapes reject the bipole ``l=`` quick key, so a gate's label slot
    is emitted as ``label=above:{…}`` (parametric and non-parametric gates alike) —
    above matches where the canvas draws the gate's ``l`` slot."""
    src = generate(_schematic(_comp(kind, options=f"l={value}")))
    assert f"label=above:{{{value}}}" in src
    assert f"l={value}" not in src


def test_npn_pin_offsets() -> None:
    """NPN (centre-placed, §4): base half a GU left of centre, collector top and
    emitter bottom at the centre column, all on the 0.25 grid."""
    from app.components.registry import REGISTRY
    defn = REGISTRY["npn"]
    pin_map = {p.name: p.offset for p in defn.pins}
    assert pin_map["base"]      == (-0.75, 0.0)
    assert pin_map["collector"] == (0.0, -0.75)
    assert pin_map["emitter"]   == (0.0,  0.75)


def test_pnp_pin_offsets() -> None:
    """PNP (centre-placed): base left, emitter top, collector bottom — the
    emitter/collector swap of NPN."""
    from app.components.registry import REGISTRY
    defn = REGISTRY["pnp"]
    pin_map = {p.name: p.offset for p in defn.pins}
    assert pin_map["base"]      == (-0.75, 0.0)
    assert pin_map["emitter"]   == (0.0, -0.75)
    assert pin_map["collector"] == (0.0,  0.75)


@pytest.mark.parametrize("kind", ["nigfete", "pigfete"])
def test_mosfet_bodydiode_emission(kind: str) -> None:
    """The body_diode variant inserts the `bodydiode` option ahead of the scale."""
    comp = Component(id=_uid(), kind=kind, position=(0.0, 0.0), rotation=0,
                     options="", variants={"body_diode": True})
    src = generate(_schematic(comp))
    assert f"node[{kind}, bodydiode, xscale=1.0204, yscale=0.974]" in src


def test_nmos_no_bodydiode() -> None:
    """nigfete with body_diode off omits the bodydiode option."""
    comp = Component(id=_uid(), kind="nigfete", position=(0.0, 0.0), rotation=0, options="")
    src = generate(_schematic(comp))
    assert "bodydiode" not in src


def test_pmos_pin_offsets() -> None:
    """PMOS (centre-placed): gate one GU left and a touch up, source above the
    centre column, drain below it — positioned relative to the component origin."""
    from app.schematic.model import component_pin_positions
    comp = _comp("pigfete", position=(2.0, 3.0))
    pins = {p.name: off for p, off in
            zip(__import__("app.components.registry", fromlist=["REGISTRY"]).REGISTRY["pigfete"].pins,
                component_pin_positions(comp))}
    assert pins["gate"]   == (1.0, 2.75)   # offset (-1.0, -0.25)
    assert pins["source"] == (2.0, 2.25)   # offset (0.0, -0.75)
    assert pins["drain"]  == (2.0, 3.75)   # offset (0.0, 0.75)


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


def test_degenerate_wire_rejected_by_validation() -> None:
    """A single-point wire violates the wire-shape invariant: generate refuses
    the schematic with a clear error instead of silently skipping the wire."""
    s = _schematic(wires=[
        _wire([(0.0, 0.0), (4.0, 0.0)]),
        _wire([(4.0, 0.0)]),   # degenerate
    ])
    with pytest.raises(ValueError, match="at least two points"):
        generate(s)


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


def test_wire_label_degenerate_wire_rejected() -> None:
    """A degenerate (single-point) wire is rejected by validation, label or not."""
    w = Wire(id=_uid(), points=[(2.0, 2.0)], end_label="x")
    with pytest.raises(ValueError, match="at least two points"):
        generate(_schematic(wires=[w]))


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


@pytest.mark.parametrize("family, macro", [("sans", r"\sffamily"), ("mono", r"\ttfamily")])
def test_text_node_font_family(family: str, macro: str) -> None:
    """text_node font_family maps to the matching LaTeX family macro in font=."""
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(1.0, 1.0),
        rotation=0, options="T", mirror=False, font_family=family,
    )
    assert macro in generate(_schematic(comp))


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


def test_text_node_stray_close_brace_stays_contained() -> None:
    r"""A stray ``}`` in a text node cannot escape the node's {…} argument.

    LaTeX-injection regression: ``}\write18{evil}`` used to emit
    ``{}\write18{evil}};`` — the payload landed *outside* the brace group as raw
    TeX. The unmatched brace must be neutralised so the whole text stays inside.
    """
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(0.0, 0.0),
        rotation=0, options=r"}\write18{evil}", mirror=False,
    )
    src = generate(_schematic(comp))
    assert r"{\}\write18{evil}};" in src     # payload contained in the group
    assert r"{}\write18{evil}};" not in src  # the old escaping emission


def test_text_node_balanced_braces_untouched() -> None:
    r"""Legitimate balanced-brace LaTeX passes through byte-for-byte."""
    text = r"$\frac{a}{b}$ and $\theta_{s,0}$"
    comp = TextNodeComponent(
        id=_uid(), kind="text_node", position=(0.0, 0.0),
        rotation=0, options=text, mirror=False,
    )
    src = generate(_schematic(comp))
    assert rf"\node at (0,0) {{{text}}};" in src


def test_wire_label_stray_brace_contained() -> None:
    r"""Wire end and mid labels brace-balance their user text too."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             start_label=r"}\evil", mid_label=r"{open")
    src = generate(_schematic(wires=[w]))
    assert r"{\}\evil}" in src
    assert r"{\{open}" in src


def test_bipole_label_stray_brace_contained() -> None:
    r"""A stray ``}`` in a bipole option value cannot escape the to[…] group."""
    src = generate(_schematic(_comp("R", options=r"l=$R_1$}")))
    assert r"l=$R_1$\}" in src


def test_centered_box_text_stray_brace_contained() -> None:
    r"""A rect's centred text is brace-balanced inside its node argument."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options=r"}bad", mirror=False,
        span_override=(2.0, 2.0),
    )
    src = generate(_schematic(comp))
    assert r"{\}bad};" in src


def test_dark_template_colors_match_canvas_palette() -> None:
    r"""The dark preview's hvbg/hvfg are derived from the canvas dark palette
    (app/canvas/style._DARK), so the two can never drift apart."""
    from app.canvas import style as canvas_style
    from app.preview.latex import build_tex

    dark = build_tex(generate(_schematic(_comp("R")), y_flip=True), dark=True)
    bg = canvas_style._DARK["COLOR_BACKGROUND"].lstrip("#")[-6:].upper()
    fg = canvas_style._DARK["COLOR_NORMAL"].lstrip("#")[-6:].upper()
    assert rf"\definecolor{{hvbg}}{{HTML}}{{{bg}}}" in dark
    assert rf"\definecolor{{hvfg}}{{HTML}}{{{fg}}}" in dark


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
    r"""rect with line_style + line_width → emitted in the \draw[...] arg."""
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 2.0), line_style="dashed", line_width=1.5,
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


@pytest.mark.parametrize("z_lo, z_hi", [(-2, -1), (1, 2)])
def test_z_order_sorts_within_group(z_lo: int, z_hi: int) -> None:
    """Within a layer block (background z<0 or foreground z>0) items emit in
    ascending z_order — lower first (further back) — regardless of insertion
    order. One shared `sorted(key=z_order)`, so the sign of z doesn't matter."""
    big = RectComponent(id=_uid(), kind="rect", position=(0.0, 0.0), rotation=0,
                        options="", mirror=False, span_override=(4.0, 4.0), z_order=z_lo)
    small = RectComponent(id=_uid(), kind="rect", position=(1.0, 1.0), rotation=0,
                          options="", mirror=False, span_override=(2.0, 2.0), z_order=z_hi)
    # Pass in reverse insertion order to prove sorting overrides insertion order.
    src = generate(_schematic(small, big))
    assert src.index("rectangle (4,4)") < src.index("rectangle (3,3)")


# ---------------------------------------------------------------------------
# z_order on plain circuit components (extended from drawing annotations)
# ---------------------------------------------------------------------------

def test_plain_component_z_order_background_before_draw_block() -> None:
    """A plain circuit component with z_order < 0 is emitted in its own \\draw
    before the main draw block."""
    back = Component(
        id=_uid(), kind="R", position=(0.0, 0.0), rotation=0,
        options="l=$R_b$", mirror=False, z_order=-1,
    )
    front = Component(
        id=_uid(), kind="R", position=(0.0, 2.0), rotation=0,
        options="l=$R_f$", mirror=False,
    )
    src = generate(_schematic(back, front))
    back_pos = src.index("R_b")
    draw_block_pos = src.index(r"\draw" + "\n")
    assert back_pos < draw_block_pos, "z<0 component must precede the main \\draw block"


def test_plain_component_z_order_foreground_after_draw_block() -> None:
    """A plain circuit component with z_order > 0 is emitted after the main draw."""
    comp = Component(
        id=_uid(), kind="R", position=(0.0, 0.0), rotation=0,
        options="l=$R_f$", mirror=False, z_order=1,
    )
    src = generate(_schematic(comp))
    draw_semi = src.index("  ;")
    comp_pos = src.index("R_f")
    assert comp_pos > draw_semi, "z>0 component must follow the main \\draw block"


def test_plain_component_default_layer_unchanged() -> None:
    """A z_order==0 circuit component still emits inside the shared \\draw block."""
    comp = Component(
        id=_uid(), kind="R", position=(0.0, 0.0), rotation=0,
        options="l=$R_0$", mirror=False,
    )
    src = generate(_schematic(comp))
    # The component line is indented inside the \draw path (four-space indent),
    # not a standalone "\draw (...)" statement.
    assert "    (0,0) to[R" in src
    assert r"\draw (0,0) to[R" not in src


def test_layered_multiterminal_keeps_output_compilable() -> None:
    """A multi-terminal component (named node) sent to back is emitted before the
    main draw, and a connected default-layer wire falls back to absolute
    coordinates instead of a forward node-anchor reference."""
    op = Component(
        id=_uid(), kind="op amp", position=(4.0, 0.0), rotation=0,
        options="", mirror=False, z_order=-1,
    )
    from app.schematic.model import component_pin_positions
    out_pin = component_pin_positions(op)[2]
    wire = Wire(id=_uid(), points=[out_pin, (out_pin[0] + 2.0, out_pin[1])])
    src = generate(_schematic(op, wires=[wire]))
    # The op amp's node is defined before the main draw block...
    node_pos = src.index("node[op amp")
    draw_block_pos = src.index(r"\draw" + "\n")
    assert node_pos < draw_block_pos
    # ...and no wire references the node by name (would be a forward reference).
    assert ".out)" not in src and "node_" not in src.split(r"\draw" + "\n", 1)[1]


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
    """bipole with non-default line_width emits line width=... in the node options."""
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, options="", mirror=False,
        span_override=(2.0, 0.0),
        line_width=1.5,
    )
    src = generate(_schematic(comp))
    assert "line width=1.5pt" in src


def test_bipole_default_border_width_omitted() -> None:
    """bipole at default line_width (0.4pt) does not emit line width."""
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


def test_build_tex_dark_only_affects_preview() -> None:
    """The dark template (preview only) sets a dark page + light ink; the default
    (export) template stays light and byte-for-byte unchanged. Guards against dark
    mode leaking into the distributed figure."""
    from app.preview.latex import build_tex

    src = generate(_schematic(_comp("R")), y_flip=True)
    light = build_tex(src)
    dark = build_tex(src, dark=True)

    assert "pagecolor" not in light          # export path never darkens
    assert build_tex(src, dark=False) == light
    assert r"\pagecolor{hvbg}" in dark and r"\color{hvfg}" in dark
    assert src in dark                        # the figure source is still embedded


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
    """pdf_to_eps raises CompileError when BOTH converters are absent, and the
    message names both install options."""
    from app.preview import latex, tools

    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    monkeypatch.setattr(tools, "_EXTRA_TOOL_CANDIDATES", {})  # no local Inkscape
    tools.set_tool_paths({})  # no explicit override -> falls through to (patched) PATH
    with pytest.raises(latex.CompileError, match="pdftocairo") as exc:
        latex.pdf_to_eps(b"%PDF-1.4")
    assert "Inkscape" in str(exc.value)


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
    """pdf_to_svg raises CompileError when BOTH converters are absent."""
    from app.preview import latex, tools

    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    monkeypatch.setattr(tools, "_EXTRA_TOOL_CANDIDATES", {})  # no local Inkscape
    tools.set_tool_paths({})
    with pytest.raises(latex.CompileError, match="pdftocairo"):
        latex.pdf_to_svg(b"%PDF-1.4")


# ---------------------------------------------------------------------------
# Inkscape fallback for EPS/SVG export (§8.6) — used when Poppler is absent
# ---------------------------------------------------------------------------

def _runnable_stub(tmp_path, name: str) -> str:
    exe = tmp_path / name
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return str(exe)


def test_vector_converter_prefers_pdftocairo(monkeypatch, tmp_path) -> None:
    """With both converters available, Poppler wins (lighter, instant start)."""
    from app.preview import latex, tools

    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    tools.set_tool_paths({
        "pdftocairo": _runnable_stub(tmp_path, "pdftocairo"),
        "inkscape": _runnable_stub(tmp_path, "inkscape"),
    })
    try:
        name, exe = latex.vector_converter()
        assert name == "pdftocairo" and exe.endswith("pdftocairo")
    finally:
        tools.set_tool_paths({})


def test_pdf_to_svg_falls_back_to_inkscape(monkeypatch, tmp_path) -> None:
    """Without Poppler, the conversion runs Inkscape with the 1.x CLI args."""
    import subprocess
    from pathlib import Path

    from app.preview import latex, tools

    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    monkeypatch.setattr(tools, "_EXTRA_TOOL_CANDIDATES", {})
    inkscape = _runnable_stub(tmp_path, "inkscape")
    tools.set_tool_paths({"inkscape": inkscape})

    seen: dict = {}

    def fake_run(argv, *, cwd, capture_output, timeout):  # noqa: ANN001
        seen["argv"] = argv
        out = next(a for a in argv if a.startswith("--export-filename="))
        Path(out.split("=", 1)[1]).write_bytes(b"<svg>fake</svg>")
        return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(latex.subprocess, "run", fake_run)
    try:
        out = latex.pdf_to_svg(b"%PDF-1.4")
    finally:
        tools.set_tool_paths({})

    assert out == b"<svg>fake</svg>"
    assert seen["argv"][0] == inkscape
    assert seen["argv"][1].endswith("schematic.pdf")
    assert "--export-type=svg" in seen["argv"]


@pytest.mark.skipif(
    __import__("shutil").which("pdflatex") is None
    or __import__("app.preview.tools", fromlist=["resolve"]).resolve("inkscape") is None,
    reason="requires pdflatex and Inkscape",
)
def test_pdf_to_svg_roundtrip_inkscape(monkeypatch) -> None:
    """End-to-end: with Poppler masked out, a real Inkscape converts a compiled
    schematic PDF to a valid SVG (exercises the actual 1.x CLI arguments)."""
    from app.preview import latex, tools
    from app.preview.latex import build_tex, compile_tex, pdf_to_svg

    real_resolve = tools.resolve
    monkeypatch.setattr(
        latex._tools, "resolve",
        lambda name: None if name == "pdftocairo" else real_resolve(name),
    )
    src = generate(_schematic(_comp("R")), y_flip=True)
    pdf_bytes = compile_tex(build_tex(src))
    svg_bytes = pdf_to_svg(pdf_bytes, timeout=120)
    assert b"<svg" in svg_bytes[:1024]


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
    """generate() emits no crossing unless mark_line_hops is requested."""
    src = generate(_crossing())
    assert "jump crossing" not in src
    assert "(0,1) -- (4,1)" in src   # plain straight wire, uninterrupted


def test_always_hop_mode_emits_even_when_disabled() -> None:
    """A wire with hop_mode='always' emits a crossing even with mark_line_hops off."""
    h = Wire(id="h", points=[(0.0, 1.0), (4.0, 1.0)], hop_mode="always")
    v = Wire(id="v", points=[(2.0, 0.0), (2.0, 3.0)])
    src = generate(_schematic(wires=(h, v)))     # mark_line_hops defaults False
    assert "jump crossing" in src


def test_line_hops_emit_jump_crossing_node() -> None:
    """With mark_line_hops, a `jump crossing` node is placed at the crossing and
    both wires break to its anchors (the horizontal hopper to .west/.east, the
    vertical crossed wire to .north/.south)."""
    src = generate(_crossing(), mark_line_hops=True)
    assert r"\node[jump crossing] (xing0) at (2,1) {};" in src
    assert "(xing0.west)" in src and "(xing0.east) -- (4,1)" in src   # hopper arms
    assert "(xing0.north)" in src and "(xing0.south)" in src          # crossed arms


def test_vertical_hopper_rotates_node() -> None:
    """A vertical hopper rotates the node 90° so its arc lands on the vertical arm."""
    h = Wire(id="h", points=[(0.0, 1.0), (4.0, 1.0)], z_order=0)
    v = Wire(id="v", points=[(2.0, 0.0), (2.0, 3.0)], z_order=1)   # higher z → hops
    src = generate(_schematic(wires=(h, v)), mark_line_hops=True)
    assert r"\node[jump crossing, rotate=90] (xing0) at (2,1) {};" in src
    # Vertical hopper connects to .west/.east; horizontal crossed to .north/.south.
    assert "(xing0.west)" in src and "(xing0.east)" in src


def test_line_hop_node_position_flips_with_yflip() -> None:
    """Under y_flip the node position negates like every other coordinate."""
    src = generate(_crossing(), mark_line_hops=True, y_flip=True)
    assert r"(xing0) at (2,-1) {};" in src


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
    # The gate pin is now at the scaled anchor offset (-1.0, 0.25) from the
    # centre-placed node, i.e. (4.0, 5.25); a wire ending there keeps the anchor.
    w = Wire(id="g", points=[(4.0, 5.25), (3.0, 5.25)], z_order=1)
    src = generate(_schematic(fet, wires=(w,)))
    assert ".gate)" in src                    # references the MOSFET gate anchor


# ---------------------------------------------------------------------------
# Parametric logic gates (variable input count)
# ---------------------------------------------------------------------------

def test_logic_gate_emits_height_group_no_yscale():
    """A parametric gate is emitted in a local group that sets its body height
    (so inputs land on grid without a node yscale that would oval the bubble):
    { \\ctikzset{…/height=H}  \\draw … node[and port, number inputs=N, xscale=…]; }."""
    # Default value (2 inputs). Centre-placed (no anchor=), height-sized, xscale only.
    src2 = generate(_schematic(_comp("and")))
    assert "node[and port, number inputs=2, xscale=1.0823" in src2
    assert "anchor=" not in src2
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


def test_scaled_gate_scales_via_height_and_xscale_no_leads():
    """A scaled multi-input gate scales its body via the **height** group (× scale)
    and **xscale** (× scale), with NO yscale — so its `.in k` anchors land exactly
    at ``base_offset × scale`` and a connecting wire attaches there directly, with
    no lead stub."""
    c = Component(id=_uid(), kind="and", position=(0.0, 0.0), rotation=0,
                  options="", params={"inputs": 3}, scale=0.5)
    src = generate(_schematic(c), y_flip=True)
    assert "number inputs=3, xscale=0.54115" in src        # 1.0823 × 0.5
    assert "yscale" not in src                              # pitch comes from height
    assert "tripoles/american and port/height=0.5357" in src   # 1.0714 × 0.5
    assert ".in 1) --" not in src                          # no lead stubs


def test_scaled_even_input_gate_emits_no_lead_stubs():
    """A 4-input gate at 0.5 has off-grid scaled input anchors, but the pins now
    sit at that true anchor (no snapping), so NO lead stubs are emitted — a wire
    attaches directly at `(node.in k)` via the pin magnet."""
    c = Component(id=_uid(), kind="or", position=(0.0, 0.0), rotation=0,
                  options="", params={"inputs": 4}, scale=0.5)
    src = generate(_schematic(c), y_flip=True)
    assert "number inputs=4, xscale=0.54115" in src and "yscale" not in src
    assert "tripoles/american or port/height=0.7143" in src   # 1.4286 × 0.5
    for i in (1, 2, 3, 4):
        assert f".in {i}) --" not in src                   # no lead stub for any input


def test_wire_to_scaled_gate_pin_attaches_at_node_anchor():
    """A wire connecting to a scaled gate's input attaches at the gate's true
    `(node.in k)` anchor — where the pin now sits (the scaled body anchor, no lead
    stub). So there is exactly one `.in 1)` reference, the wire's, and it is a
    `-- (node.in 1)` connection."""
    from app.schematic.model import component_pin_positions
    g = Component(id=_uid(), kind="and", position=(10.0, 10.0), rotation=0,
                  options="", params={"inputs": 4}, scale=0.5)
    in1 = component_pin_positions(g)[1]                      # true scaled anchor (off-grid)
    w = Wire(id=_uid(), points=[(in1[0] - 2.0, in1[1]), (in1[0], in1[1])])
    src = generate(_schematic(g, wires=[w]), y_flip=True)
    node = f"node_{g.id[:8]}"
    assert f"-- ({node}.in 1)" in src                        # wire ends at the anchor
    assert src.count(f"{node}.in 1)") == 1                   # no separate lead stub


def test_unscaled_gate_emits_no_leads_and_no_yscale():
    """A gate at the default scale 1.0 is unchanged: authored xscale, the height
    group (no yscale), and no lead stubs."""
    c = Component(id=_uid(), kind="and", position=(0.0, 0.0), rotation=0,
                  options="", params={"inputs": 4}, scale=1.0)
    src = generate(_schematic(c), y_flip=True)
    assert "yscale" not in src and ".in 1) --" not in src


def test_line_width_emitted_for_symbol_component() -> None:
    """A non-default symbol stroke width is emitted as `line width=<w>pt`; the
    default (0.4 pt) emits nothing."""
    r = _comp("R")
    r.line_width = 0.8
    src = generate(_schematic(r), y_flip=True)
    assert "line width=0.8pt" in src
    assert "line width" not in generate(_schematic(_comp("R")), y_flip=True)


def test_line_width_emitted_once_for_block_components() -> None:
    """Rect/circle/bipole emit the unified `line_width` through their own
    draw/node options (via `compose_style_options`) — exactly once, not also via
    the symbol `_line_width_opt` path."""
    rect = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0), rotation=0, options="",
        line_width=0.9,
    )
    src = generate(_schematic(rect), y_flip=True)
    assert src.count("line width=0.9pt") == 1


# ---------------------------------------------------------------------------
# Thyristor / triac gate pin (off-axis pin on a two-terminal path device, §5.4)
# ---------------------------------------------------------------------------

def _grid(v: float) -> bool:
    return abs(v * 4 - round(v * 4)) < 1e-9


def test_thyristor_triac_carry_an_offgrid_gate_pin() -> None:
    """Thyristor and triac stay two-terminal path devices (axial terminal at
    pins[1]) but carry a third, off-axis ``gate`` pin at the native CircuiTikZ
    gate anchor (off the 0.25 grid). A wire leaving the gate validates at every
    orientation — the off-grid coordinate is carried one Manhattan leg out."""
    import re
    from app.codegen.circuitikz import _TWO_TERMINAL_KINDS
    from app.schematic.model import component_pin_positions
    from app.schematic.validate import validate
    from app.components.registry import REGISTRY

    for kind in ("thyristor", "triac"):
        defn = REGISTRY[kind]
        assert kind in _TWO_TERMINAL_KINDS            # still emitted via to[…]
        assert defn.pins[1].name == "out"            # axial terminal unmoved
        gate = defn.pins[2]
        assert gate.name == "gate"
        assert not (_grid(gate.offset[0]) and _grid(gate.offset[1]))  # off-grid
        # The third (gate) pin must not collapse the axial span: the device is
        # drawn between two *distinct* endpoints, not a degenerate point.
        assert defn.default_span == (2.0, 0.0)
        src = generate(_schematic(_comp(kind)), y_flip=True)
        body = [ln for ln in src.splitlines() if f"to[{kind}" in ln][0]
        coords = re.findall(r"\(([-\d.]+),([-\d.]+)\)", body)
        assert coords[0] != coords[1], f"{kind}: degenerate span {body!r}"

        snap = lambda v: round(v * 4) / 4
        for rot in (0, 90, 180, 270):
            for mir in (False, True):
                c = _comp(kind, rotation=rot, mirror=mir)
                gx, gy = component_pin_positions(c)[2]
                # one Manhattan leg out of the gate, landing on the grid in the
                # axis that is off-grid at this orientation (the other axis keeps
                # the gate's off-grid coordinate, which Invariant 3 permits).
                far = (gx, snap(gy) - 1.0) if not _grid(gy) else (snap(gx) - 1.0, gy)
                sch = _schematic(c, wires=[_wire([(gx, gy), far])])
                assert validate(sch) == [], (kind, rot, mir)


@pytest.mark.skipif(
    __import__("shutil").which("latex") is None
    or __import__("shutil").which("dvisvgm") is None,
    reason="requires latex and dvisvgm",
)
def test_thyristor_gate_pin_coincides_with_circuitikz_anchor() -> None:
    """The model's gate-pin world position equals where CircuiTikZ actually draws
    the gate stub, at all 8 rotation×mirror cases — so a wire to the gate (which
    connects by *coordinate*, the device being an anonymous ``to[…]``) lands on it
    in the rendered output, not just on the canvas. This is the invariant that
    makes the off-axis third pin safe."""
    import re
    from app.components import render
    from app.codegen.circuitikz import _rotate
    from app.schematic.model import Component, component_pin_positions

    for kind in ("thyristor", "triac"):
        for rot in (0, 90, 180, 270):
            for mir in (False, True):
                c = Component(id="t", kind=kind, position=(0.0, 0.0),
                              rotation=rot, options="", mirror=mir)
                gx, gy = component_pin_positions(c)[2]
                # Replicate the codegen emission (endpoints + mirror key, y-flip).
                dx, dy = _rotate((2.0, 0.0), rot)
                if mir:
                    dx = -dx
                mk = ", mirror" if mir else ""
                body = rf"\draw (0,0) to[{kind}, name=X{mk}] ({dx:g},{-dy:g});"
                _svg, log = render.render_svg(body, border_pt=12, node_id="X",
                                              anchors=["gate"])
                m = re.search(r"HVANCHOR gate = (-?[\d.]+)pt\s*,\s*(-?[\d.]+)pt", log)
                cgx = float(m.group(1)) / render.TEXPT_PER_GU
                cgy = -float(m.group(2)) / render.TEXPT_PER_GU   # tex y-up -> model
                assert abs(cgx - gx) < 0.02 and abs(cgy - gy) < 0.02, (
                    kind, rot, mir, (gx, gy), (cgx, cgy)
                )


def test_potentiometer_wiper_pin_coincides_with_circuitikz_anchor() -> None:
    """The potentiometer's third (wiper) pin world position equals where CircuiTikZ
    draws the ``wiper`` anchor, at all 8 rotation×mirror cases — so a wire to the
    wiper (which connects by *coordinate*, the device being an anonymous ``to[…]``)
    lands on it in the rendered output. The exact analogue of the thyristor-gate
    invariant for the off-axis third terminal."""
    import re
    from app.components import render
    from app.components.registry import REGISTRY
    from app.codegen.circuitikz import _rotate
    from app.schematic.model import Component, component_pin_positions

    for kind in ("pR", "epot"):
        for rot in (0, 90, 180, 270):
            for mir in (False, True):
                c = Component(id="p", kind=kind, position=(0.0, 0.0),
                              rotation=rot, options="", mirror=mir)
                wx, wy = component_pin_positions(c)[2]          # wiper is pins[2]
                dx, dy = _rotate((2.0, 0.0), rot)
                if mir:
                    dx = -dx
                mk = ", mirror" if mir else ""
                tikz = REGISTRY[kind].tikz_keyword
                body = rf"\draw (0,0) to[{tikz}, name=X{mk}] ({dx:g},{-dy:g});"
                _svg, log = render.render_svg(body, border_pt=12, node_id="X",
                                              anchors=["wiper"])
                m = re.search(r"HVANCHOR wiper = (-?[\d.]+)pt\s*,\s*(-?[\d.]+)pt", log)
                cwx = float(m.group(1)) / render.TEXPT_PER_GU
                cwy = -float(m.group(2)) / render.TEXPT_PER_GU   # tex y-up -> model
                assert abs(cwx - wx) < 0.02 and abs(cwy - wy) < 0.02, (
                    kind, rot, mir, (wx, wy), (cwx, cwy)
                )
