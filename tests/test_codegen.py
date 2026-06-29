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
          node_text="", node_side="") -> Component:
    return Component(
        id=_uid(),
        kind=kind,
        position=position,
        rotation=rotation,
        options=options,
        mirror=mirror,
        node_text=node_text,
        node_side=node_side,
    )


def _wire(points) -> Wire:
    return Wire(id=_uid(), points=points)


# ---------------------------------------------------------------------------
# European / cute component shape keywords (§5)
# ---------------------------------------------------------------------------

# NOTE: test_european_components_emit_shape_keywords and
# test_european_logic_gates_emit_keywords DELETED — they asserted curated
# european/cute components as *separate kinds* (eR, eL, eV, eI, evR, epot,
# ethermistor, enot, eand). In the manual library, european/cute is a per-document
# STYLE AXIS, not distinct kinds, so those kinds no longer exist. The american
# potentiometer (pR) survives and is exercised by the potentiometer wiper test.


def test_scalable_without_height_scales_both_axes() -> None:
    """A scalable kind with no body-height mechanism (digital blocks, and the
    manual-library gates) folds the user scale into BOTH xscale and yscale so it
    grows uniformly, matching the canvas. A gate that *does* size via its height key
    scales only x (the height carries y — see the european-gate test above)."""
    import dataclasses
    from app.codegen.circuitikz import _gate_height_setting

    ff = dataclasses.replace(_comp("flipflop D"), scale=2.0)
    assert _gate_height_setting(ff) is None        # no height mechanism
    src = generate(_schematic(ff))
    assert "xscale=" in src and "yscale=" in src   # both axes scaled


def test_battery_and_inst_amp_emit_keywords() -> None:
    # Manual kinds carry CircuiTikZ-native names (the amps are "inst amp"/"gm amp"
    # with a space, not the curated instamp/gmamp).
    assert "to[battery]" in generate(_schematic(_comp("battery")))
    assert "node[inst amp" in generate(_schematic(_comp("inst amp")))
    assert "node[gm amp" in generate(_schematic(_comp("gm amp")))


def test_mirror_emits_mirror_option_for_two_terminal() -> None:
    """A mirrored two-terminal bipole adds CircuiTikZ's `mirror` key so off-axis
    features (e.g. an LED's emission arrows) land on the same side as the canvas
    Flip-X; unmirrored output is unchanged (regression for the canvas/LaTeX
    mirror mismatch)."""
    assert "to[full led, mirror]" in generate(_schematic(_comp("full led", mirror=True)))
    assert "mirror" not in generate(_schematic(_comp("full led", mirror=False)))
    # With a label the key precedes the label.
    assert "to[full diode, mirror, l=$D$]" in generate(
        _schematic(_comp("full diode", mirror=True, options="l=$D$"))
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
    # Manual kinds carry CircuiTikZ-native names (no curated short aliases).
    assert "to[normal open switch]" in generate(_schematic(_comp("normal open switch")))
    assert "to[normal closed switch]" in generate(_schematic(_comp("normal closed switch")))
    assert "to[push button]" in generate(_schematic(_comp("push button")))
    assert "to[cute choke]" in generate(_schematic(_comp("cute choke")))
    assert "to[opening switch]" in generate(_schematic(_comp("opening switch")))
    assert "to[closing switch]" in generate(_schematic(_comp("closing switch")))
    spdt_src = generate(_schematic(_comp("spdt")))
    # The SPDT is a centre-placed node, no anchor= placement, no bridge leads.
    # Manual symbols bake NO grid-alignment scale, so there is no xscale/yscale.
    assert "node[spdt" in spdt_src
    assert "anchor=" not in spdt_src
    assert "xscale=" not in spdt_src and "yscale=" not in spdt_src


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


@pytest.mark.parametrize("kind", ["capacitor", "L", "full diode", "full Zener diode", "full led"])
def test_two_terminal_keyword_emission(kind: str) -> None:
    """Every horizontal two-terminal kind emits `(0,0) to[<keyword>] (2,0)` — one
    shared codegen path, so the resistor (geometry) + these (keyword mapping) +
    the vertical voltage source below cover it; the kind→keyword map itself is
    guarded by the registry/library tests. The manual keyword IS the kind name."""
    src = generate(_schematic(_comp(kind)))
    assert f"(0,0) to[{kind}] (2,0)" in src


# NOTE: test_filled_variant_emission DELETED — the `filled` variant appended a
# curated suffix-mode `*` token (D -> D*). The manual library declares NO
# suffix-mode variants at all (variant_specs are option-mode only, e.g. the
# transistor `bodydiode` exercised by test_mosfet_bodydiode_emission below), so
# the keyword-suffix path has no manual kind to drive it.


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
    comp = _comp("american controlled voltage source", options=r"v=$\phi(0,0^+)$")
    src = generate(_schematic(comp))
    assert r"to[american controlled voltage source, v={$\phi(0,0^+)$}]" in src


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
    src = generate(_schematic(_comp("full diode")))
    assert r"\ctikzset{diodes/scale=0.6}" in src          # new-document default (§5)
    lines = [ln.strip() for ln in src.splitlines()]
    i = lines.index(r"\begin{circuitikz}")
    assert lines[i + 1] == r"\ctikzset{diodes/scale=0.6}"


def test_no_diode_scale_without_diodes() -> None:
    """Schematics with no diode-family component omit the diodes/scale line."""
    src = generate(_schematic(_comp("R"), _comp("capacitor", position=(4.0, 0.0))))
    assert "diodes/scale" not in src


def test_diode_scale_uses_document_value() -> None:
    """The emitted `diodes/scale` is the document's diode_scale (the inspector control,
    §5), not a hard-coded constant."""
    s = _schematic(_comp("full diode"))
    s.diode_scale = 0.5                                   # a non-default value
    assert r"\ctikzset{diodes/scale=0.5}" in generate(s)


def test_voltage_source() -> None:
    """Voltage source at (0,0), rotation 0 → (0,0) to[<kw>] (2,0). The manual
    voltage source has a horizontal default span (2,0), like every other bipole."""
    src = generate(_schematic(_comp("american voltage source")))
    assert "(0,0) to[american voltage source] (2,0)" in src


# ---------------------------------------------------------------------------
# test_opamp_node
# ---------------------------------------------------------------------------

def test_opamp_node() -> None:
    """Op-amp produces node[op amp] syntax."""
    comp = _comp("op amp", position=(1.0, 2.0))
    src = generate(_schematic(comp))
    # Centre-placed. Manual symbols bake NO grid-alignment scale, so the node
    # carries neither xscale/yscale nor an anchor= placement.
    assert "node[op amp]" in src
    assert "anchor=" not in src
    assert "xscale=" not in src and "yscale=" not in src


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


def test_gate_size_setting_emits_height_and_width(monkeypatch) -> None:
    r"""A manual gate with CircuiTikZ size keys emits its body height/width as
    ``\ctikzset{tripoles/<kw>/height=…, …/width=…}`` (default × resize factor), instead
    of node xscale/yscale. None at natural size, and None for a kind without size keys."""
    import app.codegen.circuitikz as cg
    import app.schematic.model as model
    keys = {"path": "tripoles/american and port", "height": 0.8, "width": 1.1}
    monkeypatch.setattr(cg._library, "gate_size_keys",
                        lambda k: keys if k == "and" else None)

    monkeypatch.setattr(model, "node_resize_factors", lambda c: (1.5, 2.0))
    assert cg._gate_size_setting(_comp("and")) == [
        "tripoles/american and port/height=1.6",     # 0.8 * 2.0
        "tripoles/american and port/width=1.65",     # 1.1 * 1.5
    ]
    assert cg._node_group_ctikzset(_comp("and")) == [
        "tripoles/american and port/height=1.6", "tripoles/american and port/width=1.65"]

    monkeypatch.setattr(model, "node_resize_factors", lambda c: None)
    assert cg._gate_size_setting(_comp("and")) is None        # natural size → no override
    monkeypatch.setattr(model, "node_resize_factors", lambda c: (1.5, 2.0))
    assert cg._gate_size_setting(_comp("R")) is None          # no size keys for this kind


def test_single_terminal_node_uses_standalone_node_at_syntax() -> None:
    """A single-terminal node (ground/supply/terminal dot) is emitted as a standalone
    ``\\node[kind] at (x,y) {};`` command, never as an inline ``(x,y) node[kind]{}``
    path operation inside the shared ``\\draw``. The command sits after the path's
    terminating ';' (like the junction/open-circle dots)."""
    src = generate(_schematic(_comp("ground")))
    assert r"\node[ground] at (0,0) {};" in src
    assert ") node[ground]" not in src                    # not the inline path op
    assert src.index("\n  ;") < src.index(r"\node[ground]")


def test_node_text_on_single_terminal_node() -> None:
    """A single-terminal node (power rail) is emitted as a standalone ``\\node at``
    command with node_text in {…} and its options in the node[…] bracket (no l=→label
    hack)."""
    src = generate(_schematic(_comp("vcc", node_text="$V_{cc}$", options="color=blue")))
    assert r"\node[vcc, color=blue] at (0,0) {$V_{cc}$};" in src
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


# NOTE: curated "vdd" dropped from the list — the manual library has no vdd kind
# (vcc/vee/ground remain and cover single-terminal supply nodes).
@pytest.mark.parametrize("kind", ["npn", "pnp", "op amp", "nigfete", "vcc", "vee", "ground"])
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

def test_flipflop_d_emits_centre_node_no_leads() -> None:
    """A D flip-flop emits a centre-placed ``node[flipflop D]`` and **no** lead
    bridges — its pins sit at the node anchors. Manual symbols bake NO
    grid-alignment scale, so there is no xscale/yscale on the node."""
    src = generate(_schematic(_comp("flipflop D", position=(4.0, 4.0))))
    assert "node[flipflop D]" in src
    assert "xscale=" not in src and "yscale=" not in src
    assert ") -- " not in src     # no bridge leads; pins are at the node anchors


# NOTE: test_flipflop_pins_are_grid_aligned DELETED — it asserted the curated
# "node scale correction" landed all four flip-flop pins on the 0.25-GU grid.
# Manual symbols are rendered at true CircuiTikZ size with NO grid-alignment
# scale, so their pins sit at the native (off-grid) anchors by design.


def test_mux_emits_muxdemux_def() -> None:
    """A multiplexer emits the configurable ``muxdemux`` shape with the concrete
    ``muxdemux def`` baked for its current (inputs, selects) combo."""
    src = generate(_schematic(_comp("muxdemux", position=(4.0, 4.0))))
    assert "node[muxdemux" in src and "muxdemux def=" in src
    assert "NL=2" in src and "NB=1" in src  # default combo: 2 inputs, 1 select


def test_mux_inputs_param_changes_shape() -> None:
    """Bumping the input count re-emits a wider ``muxdemux def`` (NL) and more
    data pins; the select count drives NB independently."""
    from app.schematic.model import Component as SComponent
    comp = SComponent(id=_uid(), kind="muxdemux", position=(4.0, 4.0),
                      rotation=0, options="", params={"inputs": 8, "selects": 3})
    src = generate(_schematic(comp))
    assert "NL=8" in src and "NB=3" in src


def test_alu_and_adder_emit_named_styles() -> None:
    """The ALU and adder use CircuiTikZ's predefined ``ALU`` / ``adder`` styles,
    placed as centre nodes. Manual symbols bake NO grid-alignment scale, so the
    nodes carry no xscale."""
    alu = generate(_schematic(_comp("ALU", position=(5.0, 5.0))))
    assert "node[ALU]" in alu and "xscale=" not in alu
    add = generate(_schematic(_comp("adder", position=(5.0, 5.0))))
    assert "node[adder]" in add and "xscale=" not in add


def test_digital_block_scale_multiplies_node() -> None:
    """A digital block's inspector Size (Component.scale) is emitted as the node
    xscale/yscale. Manual blocks bake no alignment scale, so at the default scale
    the node carries none; the user scale folds straight into both axes."""
    from app.schematic.model import Component as SComponent
    base = generate(_schematic(_comp("flipflop D", position=(4.0, 4.0))))
    comp = SComponent(id=_uid(), kind="flipflop D", position=(4.0, 4.0),
                      rotation=0, options="")
    comp.scale = 2.0
    scaled = generate(_schematic(comp))
    assert "xscale=" not in base                 # default scale → no override
    assert "xscale=2, yscale=2" in scaled        # user scale folds into both axes


def test_transformer_emits_centre_quadpole_node() -> None:
    """A transformer is a centre-placed quadpole node. Manual symbols bake NO
    grid-alignment scale, so the node carries no xscale/yscale; its four winding
    terminals are the native CircuiTikZ anchors A1/A2 (primary) and B1/B2
    (secondary)."""
    from app.components.library import resolved_pins
    for kind in ("transformer", "transformer core"):
        src = generate(_schematic(_comp(kind, position=(4.0, 4.0))))
        assert f"node[{kind}]" in src
        assert "xscale=" not in src and "yscale=" not in src
        names = {p.name for p in resolved_pins(_comp(kind))}
        assert {"A1", "A2", "B1", "B2"} <= names      # the four winding terminals


# NOTE: test_cute_european_transformers_wrap_inductor_ctikzset DELETED — it
# relied on the curated "cute transformer core"/"european transformer" kinds and
# the scoped inductor=cute/european ctikzset they emitted. cute/european is now a
# per-document STYLE AXIS, not separate transformer kinds, so those kinds (and the
# coil-shape ctikzset) no longer exist.
#
# NOTE: test_transformer_polarity_dots_emit_circ_nodes DELETED — it drove the
# curated transformer dot_p1/dot_s2 variants (variant_specs is empty for the
# manual transformer) and the curated p-/s-style "inner dot" anchor names. The
# manual transformer declares no dot variants.


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
    """A transformer is two coupled inductors; each coil inherits the inductor
    ``midtap`` centre tap, exposed as the ``L1.midtap`` (primary) / ``L2.midtap``
    (secondary) pins. They are reached via CircuiTikZ **sub-node** anchors, so a
    wire to one emits ``(node…-L1.midtap)`` — the sub-node form (no ``.`` before the
    coil name), not the node-relative ``(node….anchor)``."""
    import re
    import uuid
    from app.components.registry import REGISTRY
    from app.schematic.model import Component, Wire, component_pin_positions

    for kind, coil in (("transformer", "L1"), ("transformer core", "L2")):
        names = [p.name for p in REGISTRY[kind].pins]
        assert f"{coil}.midtap" in names
        c = Component(id=str(uuid.uuid4()), kind=kind, position=(6.0, 6.0),
                      rotation=0, options="", mirror=False)
        tap = component_pin_positions(c)[names.index(f"{coil}.midtap")]
        # the tap is off-grid; route the wire out along whichever axis keeps the
        # connecting leg on-grid (here straight down from the tap point).
        w = Wire(id=str(uuid.uuid4()), points=[tap, (tap[0], tap[1] - 1.0)])
        src = generate(_schematic(c, wires=[w]))
        assert re.search(rf"\(node_[0-9a-f]+-{coil}\.midtap\)", src), (kind, coil)


def test_transformer_centre_tap_pin_coincides_with_circuitikz_anchor() -> None:
    """The transformer centre-tap pins' baked positions equal where CircuiTikZ draws
    the ``L1.midtap`` / ``L2.midtap`` sub-node anchors, so the canvas dot lands on
    the real anchor the named-anchor export references."""
    import re
    from app.components import render
    from app.components.registry import REGISTRY
    from app.schematic.model import Component, component_pin_positions

    for kind in ("transformer", "transformer core"):
        names = [p.name for p in REGISTRY[kind].pins]
        c = Component(id="t", kind=kind, position=(0.0, 0.0), rotation=0, options="")
        pins = component_pin_positions(c)
        for coil in ("L1", "L2"):
            px, py = pins[names.index(f"{coil}.midtap")]
            body = rf"\node[{kind}] (X) at (0,0) {{}};"
            _svg, log = render.render_svg(body, border_pt=12,
                                          node_id=f"X-{coil}", anchors=["midtap"])
            m = re.search(r"HVANCHOR midtap = (-?[\d.]+)pt\s*,\s*(-?[\d.]+)pt", log)
            cx = float(m.group(1)) / render.TEXPT_PER_GU
            cy = -float(m.group(2)) / render.TEXPT_PER_GU
            assert abs(cx - px) < 0.02 and abs(cy - py) < 0.02, (kind, coil, (px, py), (cx, cy))


def test_inductor_midtap_pin_coincides_with_circuitikz_anchor() -> None:
    """The inductor's third (``midtap``) pin — an on-axis centre tap connected by
    *coordinate* (the inductor is an anonymous ``to[…]``) — coincides with the
    CircuiTikZ ``midtap`` anchor at all 8 rotation×mirror cases (the on-axis analogue
    of the off-axis wiper/gate invariant)."""
    import re
    from app.components import render
    from app.components.registry import REGISTRY
    from app.codegen.circuitikz import _rotate
    from app.schematic.model import Component, component_pin_positions

    names = [p.name for p in REGISTRY["L"].pins]
    assert "midtap" in names
    tikz = REGISTRY["L"].tikz_keyword
    for rot in (0, 90, 180, 270):
        for mir in (False, True):
            c = Component(id="l", kind="L", position=(0.0, 0.0),
                          rotation=rot, options="", mirror=mir)
            mx, my = component_pin_positions(c)[names.index("midtap")]
            dx, dy = _rotate((2.0, 0.0), rot)
            if mir:
                dx = -dx
            mk = ", mirror" if mir else ""
            body = rf"\draw (0,0) to[{tikz}, name=X{mk}] ({dx:g},{-dy:g});"
            _svg, log = render.render_svg(body, border_pt=12, node_id="X",
                                          anchors=["midtap"])
            m = re.search(r"HVANCHOR midtap = (-?[\d.]+)pt\s*,\s*(-?[\d.]+)pt", log)
            cx = float(m.group(1)) / render.TEXPT_PER_GU
            cy = -float(m.group(2)) / render.TEXPT_PER_GU
            assert abs(cx - mx) < 0.02 and abs(cy - my) < 0.02, (rot, mir, (mx, my), (cx, cy))


def test_path_bipole_extra_terminal_emits_named_anchor_ref() -> None:
    """A wire to a path bipole's *extra* terminal (the inductor centre tap, a
    thyristor gate, …) references the terminal's CircuiTikZ anchor **by name** —
    ``(node_….midtap)`` — instead of an opaque coordinate, and the device gains the
    matching ``name=…`` in its ``to[…]``. (The two axial terminals stay literal
    ``to[…]`` coordinates — they have no clean named anchor.)"""
    import uuid
    from app.components.registry import REGISTRY
    from app.schematic.model import Component, Wire, component_pin_positions

    c = Component(id=str(uuid.uuid4()), kind="L", position=(4.0, 4.0),
                  rotation=0, options="", mirror=False)
    names = [p.name for p in REGISTRY["L"].pins]
    mt = component_pin_positions(c)[names.index("midtap")]
    w = Wire(id=str(uuid.uuid4()), points=[mt, (mt[0], mt[1] + 1.0)])
    src = generate(_schematic(c, wires=[w]))
    nid = f"node_{c.id[:8]}"
    assert f"name={nid}" in src              # the device is named
    assert f"({nid}.midtap)" in src          # the wire references the anchor by name
    assert "to[L" in src                     # still a path device (in/out are coords)


def test_path_bipole_unconnected_extra_terminal_leaves_device_unnamed() -> None:
    """A path bipole whose extra terminal has no wire on it stays ``name=``-free —
    the name is added only when a wire actually references the terminal, so the
    common case (an inductor with no centre-tap wire) emits no noise."""
    from app.schematic.model import Component, Wire

    # Two components so generate() doesn't normalise a lone component to the origin;
    # wire only to the resistor, never to the inductor's midtap.
    r = Component(id="r0", kind="R", position=(0.0, 0.0), rotation=0, options="", mirror=False)
    ind = Component(id="l0", kind="L", position=(4.0, 0.0), rotation=0, options="", mirror=False)
    w = Wire(id="w0", points=[(2.0, 0.0), (4.0, 0.0)])
    src = generate(_schematic(r, ind, wires=[w]))
    assert "name=" not in src


def test_terminal_marker_on_component_anchor_uses_named_anchor_ref() -> None:
    """A terminal marker (circ/ocirc/…) dropped on a component's named anchor is
    emitted as ``\\node[circ] at (node_….A1) {}`` — the anchor reference, not a raw
    coordinate — for both a multi-terminal node (a transformer winding) and a path
    device's extra terminal (an inductor centre tap, which is named on demand)."""
    import re
    import uuid
    from app.components.registry import REGISTRY
    from app.schematic.model import Component, component_pin_positions

    def _c(kind, pos):
        return Component(id=str(uuid.uuid4()), kind=kind, position=pos,
                         rotation=0, options="", mirror=False)

    # (a) marker on a multi-terminal node anchor (transformer A1)
    tr = _c("transformer", (4.0, 4.0))
    names = [p.name for p in REGISTRY["transformer"].pins]
    a1 = component_pin_positions(tr)[names.index("A1")]
    src = generate(_schematic(tr, _c("circ", a1)))
    nid = f"node_{tr.id[:8]}"
    assert re.search(rf"\\node\[circ\] at \({re.escape(nid)}\.A1\)", src)

    # (b) marker on a path device's extra terminal (inductor midtap): the device is
    # named on demand and the marker references the anchor. (Second component keeps
    # generate() from normalising the lone inductor to the origin.)
    ind = _c("L", (4.0, 8.0))
    other = _c("R", (8.0, 8.0))
    mn = [p.name for p in REGISTRY["L"].pins]
    mt = component_pin_positions(ind)[mn.index("midtap")]
    src2 = generate(_schematic(ind, other, _c("circ", mt)))
    iid = f"node_{ind.id[:8]}"
    assert f"name={iid}" in src2
    assert re.search(rf"\\node\[circ\] at \({re.escape(iid)}\.midtap\)", src2)


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
    # The "-" pin sits off-grid in x (native anchor), so the connecting wire leaves
    # along the on-grid y axis (carrying the off-grid x out one Manhattan leg).
    w = Wire(id=str(uuid.uuid4()), points=[minus, (minus[0], minus[1] - 1.0)])
    src = generate(_schematic(c, wires=[w]))
    import re
    assert re.search(r"\(node_[0-9a-f]+\.-\)", src)        # node-relative (node….-)
    assert not re.search(r"\(node_[0-9a-f]+-\)", src)      # never the sub-node form
    # NOTE: removed an orphaned dead-code block here that asserted the curated
    # transformer `dot_p1` variant returned empty suffix/opts — the manual
    # transformer declares no dot variants (see the deleted polarity-dot test).


def test_digital_blocks_take_no_bipole_label() -> None:
    """The raw pgf shapes are self-labelled (D/Q/CLK glyphs), so the registry
    offers no ``l`` label slot — emitting ``l=`` would be a LaTeX error."""
    from app.components.registry import REGISTRY
    for kind in ("flipflop D", "muxdemux", "ALU", "adder"):
        assert REGISTRY[kind].label_slots == []


# ---------------------------------------------------------------------------
# test_nmos_node
# ---------------------------------------------------------------------------

# NOTE: test_transistor_node_scale (all params) RETARGETED below as
# test_transistor_centre_node. The curated "node scale correction" (baked
# per-kind xscale/yscale) is GONE — manual symbols render at true CircuiTikZ size
# (cg._MULTI_TERMINAL_EXTRA_OPTS == {}). So the surviving invariant is: every
# BJT/IGFET is the same centre-placed node, emitted as node[<kind>] with NO
# scale and NO anchor= option.
_TRANSISTOR_KINDS = ["npn", "pnp", "nigfete", "nigfetd", "pigfete", "pigfetd"]


@pytest.mark.parametrize("kind", _TRANSISTOR_KINDS)
def test_transistor_centre_node(kind: str) -> None:
    """Centre-placed node `node[<kind>]` with no `anchor=` and no baked
    grid-alignment scale (manual symbols render at true size)."""
    src = generate(_schematic(_comp(kind, position=(2.0, 3.0))))
    assert f"node[{kind}]" in src
    assert "anchor=" not in src
    assert "xscale=" not in src and "yscale=" not in src


def test_npn_no_bridge_leads() -> None:
    """NPN is a centre-placed node — its pins are the native anchors and no bridge
    lead wires are emitted."""
    comp = _comp("npn", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[npn]" in src
    assert ".C) -- " not in src
    assert ".E) -- " not in src


@pytest.mark.parametrize("kind, value",
                         [("american nand port", "$U$"), ("american not port", "$Y$")])
def test_gate_label_emitted_as_label_above(kind: str, value: str) -> None:
    """Logic-port shapes reject the bipole ``l=`` quick key, so a gate's label slot
    is emitted as ``label=above:{…}`` (parametric and non-parametric gates alike) —
    above matches where the canvas draws the gate's ``l`` slot."""
    src = generate(_schematic(_comp(kind, options=f"l={value}")))
    assert f"label=above:{{{value}}}" in src
    assert f"l={value}" not in src


def test_npn_pin_offsets() -> None:
    """NPN (centre-placed): the pins carry the CircuiTikZ-native anchor names
    B/C/E at the native (off-grid) offsets — base left of centre, collector top,
    emitter bottom at the centre column. A wire to a named pin connects at the
    matching node anchor (test_npn_base_wire_uses_node_anchor below)."""
    from app.components.registry import REGISTRY
    defn = REGISTRY["npn"]
    pin_map = {p.name: p.offset for p in defn.pins}
    assert pin_map["B"] == (-0.84, 0.0)
    assert pin_map["C"] == (0.0, -0.77)
    assert pin_map["E"] == (0.0,  0.77)


def test_pnp_pin_offsets() -> None:
    """PNP (centre-placed): base left, collector top, emitter bottom — the
    emitter/collector y-swap of NPN, with CircuiTikZ-native names B/C/E."""
    from app.components.registry import REGISTRY
    defn = REGISTRY["pnp"]
    pin_map = {p.name: p.offset for p in defn.pins}
    assert pin_map["B"] == (-0.84, 0.0)
    assert pin_map["C"] == (0.0,  0.77)
    assert pin_map["E"] == (0.0, -0.77)


def test_npn_base_wire_uses_node_anchor() -> None:
    """A wire ending on the NPN base pin references the node's ``B`` anchor — the
    intent of the old pin-offset tests (the named pin → named anchor connection)."""
    from app.schematic.model import component_pin_positions
    c = Component(id="aaaaaaaa-base", kind="npn", position=(2.0, 2.0),
                  rotation=0, options="", mirror=False)
    base = component_pin_positions(c)[0]               # B pin, off-grid in x
    # Leave along the on-grid y axis (the base x is off-grid, carried out one leg).
    w = _wire([base, (base[0], base[1] - 1.0)])
    src = generate(_schematic(c, wires=[w]))
    assert ".B) -- " in src


# The manual library's option-mode variant is named ``bodydiode`` (the curated
# ``body_diode`` no longer exists), and is declared on nfet/pfet (not the IGFETs,
# which carry ``doublegate``). The option is emitted in the node bracket; manual
# symbols bake no scale, so the node is simply ``node[<kind>, bodydiode]``.
@pytest.mark.parametrize("kind", ["nfet", "pfet"])
def test_mosfet_bodydiode_emission(kind: str) -> None:
    """The bodydiode variant inserts the `bodydiode` option into the node bracket."""
    comp = Component(id=_uid(), kind=kind, position=(0.0, 0.0), rotation=0,
                     options="", variants={"bodydiode": True})
    src = generate(_schematic(comp))
    assert f"node[{kind}, bodydiode]" in src
    assert "xscale=" not in src and "yscale=" not in src


def test_nmos_no_bodydiode() -> None:
    """nfet with bodydiode off omits the bodydiode option."""
    comp = Component(id=_uid(), kind="nfet", position=(0.0, 0.0), rotation=0, options="")
    src = generate(_schematic(comp))
    assert "bodydiode" not in src


def test_pmos_pin_offsets() -> None:
    """PMOS-type (pigfete, centre-placed): the pins carry the CircuiTikZ-native
    anchor names G/D/S at the native offsets — gate left of centre and a touch
    down, drain above the centre column, source below it — positioned relative to
    the component origin."""
    from app.schematic.model import component_pin_positions
    comp = _comp("pigfete", position=(2.0, 3.0))
    pins = {p.name: off for p, off in
            zip(__import__("app.components.registry", fromlist=["REGISTRY"]).REGISTRY["pigfete"].pins,
                component_pin_positions(comp))}
    assert pins["G"] == (1.02, 2.75)   # offset (-0.98, -0.25)
    assert pins["S"] == (2.0, 2.23)    # offset (0.0, -0.77)
    assert pins["D"] == (2.0, 3.77)    # offset (0.0, 0.77)


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


def test_wire_laplata_diagonal() -> None:
    """A La Plata wire's 45° leg emits as a plain diagonal `--` segment (TikZ draws
    diagonals natively): [(0,0),(1,1),(4,1)] → (0,0) -- (1,1) -- (4,1)."""
    src = generate(_schematic(wires=[_wire([(0.0, 0.0), (1.0, 1.0), (4.0, 1.0)])]))
    assert "(0,0) -- (1,1) -- (4,1)" in src


def test_bipole_at_45_emits_diagonal_endpoints() -> None:
    """A two-terminal symbol rotated 45° draws between diagonal endpoints — the span
    is rotated 45° (so its second coordinate is on the 45° line), matching the canvas
    pins; CircuiTikZ orients the `to[…]` symbol along that diagonal automatically."""
    import math
    src = generate(_schematic(_comp("R", position=(2.0, 2.0), rotation=45)))
    d = 2.0 * math.cos(math.radians(45))
    # The bipole runs from (origin) to (origin + (d, d)) after the translate-to-origin
    # shift, so the two endpoints share the same +d offset on both axes (a 45° line).
    import re
    m = re.search(r"\(([-\d.]+),([-\d.]+)\) to\[R\] \(([-\d.]+),([-\d.]+)\)", src)
    assert m, src
    x0, y0, x1, y1 = (float(g) for g in m.groups())
    # Equal x/y deltas → a 45° line; magnitude ≈ d (coords are emitted at 2 dp).
    assert (x1 - x0) == pytest.approx(y1 - y0)
    assert (x1 - x0) == pytest.approx(d, abs=0.01) and (x1 - x0) > 0


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
        _comp("capacitor", position=(2.0, 0.0)),
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


def test_mark_junctions_off_suppresses_circ_nodes() -> None:
    """With the document's mark_junctions off, a junction emits no \\node[circ]."""
    s = _schematic(
        wires=[
            Wire(id="a", points=[(0.0, 2.0), (2.0, 2.0)]),
            Wire(id="b", points=[(2.0, 0.0), (2.0, 2.0)]),
            Wire(id="c", points=[(2.0, 2.0), (4.0, 2.0)]),
        ]
    )
    assert r"\node[circ]" in generate(s)            # default on
    s.mark_junctions = False
    assert r"\node[circ]" not in generate(s)        # suppressed document-wide


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


def test_mark_open_ends_off_suppresses_ocirc_nodes() -> None:
    """With the document's mark_open_ends off, free wire ends emit no \\node[ocirc]."""
    s = _schematic(wires=[_wire([(0.0, 0.0), (4.0, 0.0)])])
    assert r"\node[ocirc]" in generate(s)           # default on
    s.mark_open_ends = False
    assert r"\node[ocirc]" not in generate(s)       # suppressed document-wide


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
    # The manual voltage source is horizontal by default; rotate it 90° to get a
    # vertical bipole whose pins span the y axis (0,0)→(0,2) so the y_flip is visible.
    s = _schematic(_comp("american voltage source", position=(0.0, 0.0), rotation=90))
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
    # The op amp out pin sits off-grid in x (native anchor), so the connecting wire
    # leaves along the on-grid y axis (carrying the off-grid x out one leg).
    wire = Wire(id=_uid(), points=[out_pin, (out_pin[0], out_pin[1] + 2.0)])
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
    # The gate pin (CircuiTikZ-native ``G`` anchor) is at offset (-0.98, 0.25) from
    # the centre-placed node, i.e. (4.02, 5.25); its x is off-grid so the wire
    # leaves along the on-grid y axis. A foreground wire keeps the named anchor.
    from app.schematic.model import component_pin_positions
    gate = component_pin_positions(fet)[0]
    w = Wire(id="g", points=[gate, (gate[0], gate[1] - 1.0)], z_order=1)
    src = generate(_schematic(fet, wires=(w,)))
    assert ".G)" in src                       # references the MOSFET gate anchor


# ---------------------------------------------------------------------------
# Parametric logic gates (variable input count)
# ---------------------------------------------------------------------------

def test_logic_gate_emits_input_count_no_scale_at_natural_size():
    """A parametric manual gate is centre-placed (no anchor=) and carries its
    ``number inputs=N``. Manual symbols bake NO grid-alignment scale, so at the
    natural size the node carries no xscale/yscale and no body-size ctikzset group
    (the body keys are emitted only when the user resizes — see the scaled test)."""
    # Default value (2 inputs).
    src2 = generate(_schematic(_comp("american and port")))
    assert "node[american and port, number inputs=2]" in src2
    assert "anchor=" not in src2
    assert "xscale=" not in src2 and "yscale=" not in src2
    assert "height" not in src2 and "width" not in src2           # natural size

    # Explicit 4 inputs: number inputs=4, still no scale.
    c = Component(id=_uid(), kind="american and port", position=(0.0, 0.0), rotation=0,
                  options="", params={"inputs": 4})
    src4 = generate(_schematic(c))
    assert "number inputs=4" in src4
    assert "xscale=" not in src4 and "yscale=" not in src4


def test_scaled_gate_scales_via_body_size_keys_no_leads():
    """A resized multi-input manual gate scales its body via the CircuiTikZ
    height/width size keys (default × scale), set in a local group, with NO node
    xscale/yscale — and no lead stubs (its pins sit at the native body anchors)."""
    c = Component(id=_uid(), kind="american and port", position=(0.0, 0.0), rotation=0,
                  options="", params={"inputs": 3}, scale=0.5)
    src = generate(_schematic(c), y_flip=True)
    assert "node[american and port, number inputs=3]" in src
    assert "xscale=" not in src and "yscale=" not in src   # size via body keys, not node scale
    assert r"\ctikzset{tripoles/american and port/height=0.4}" in src   # 0.8 × 0.5
    assert r"\ctikzset{tripoles/american and port/width=0.55}" in src   # 1.1 × 0.5
    assert ".in 1) --" not in src                          # no lead stubs


def test_scaled_even_input_gate_emits_no_lead_stubs():
    """A 4-input gate at 0.5 sits at the native body anchors (no snapping), so NO
    lead stubs are emitted — a wire attaches directly at `(node.in k)`."""
    c = Component(id=_uid(), kind="american or port", position=(0.0, 0.0), rotation=0,
                  options="", params={"inputs": 4}, scale=0.5)
    src = generate(_schematic(c), y_flip=True)
    assert "number inputs=4" in src
    assert "xscale=" not in src and "yscale=" not in src
    assert r"\ctikzset{tripoles/american or port/height=0.4}" in src    # 0.8 × 0.5
    for i in (1, 2, 3, 4):
        assert f".in {i}) --" not in src                   # no lead stub for any input


# NOTE: test_wire_to_scaled_gate_pin_attaches_at_node_anchor DELETED — it relied
# on the curated grid-aligned scaled gate anchors. A manual gate is rendered at
# true CircuiTikZ size, so its input pins sit off-grid in BOTH axes (e.g. the
# 2-input AND inputs at (8.614, 9.72)). A wire can never validly connect to such a
# pin (no Manhattan leg lands on the 0.25 grid), so the premise — a wire attaching
# at (node.in k) — is impossible. The no-lead-stub invariant for gates is still
# covered by test_scaled_gate_scales_via_body_size_keys_no_leads above.


def test_unscaled_gate_emits_no_leads_and_no_yscale():
    """A gate at the default scale 1.0 is unchanged: no node yscale (manual gates
    bake no scale), no body-size group, and no lead stubs."""
    c = Component(id=_uid(), kind="american and port", position=(0.0, 0.0), rotation=0,
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
        assert gate.name == "G"                       # CircuiTikZ-native gate anchor name
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

    # Manual library: european potentiometer is a style axis, not a separate kind
    # (no curated "epot"). Cover the american pot (pR) and the variable resistor
    # (vR), both of which carry a native ``wiper`` anchor at pins[2].
    for kind in ("pR", "vR"):
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


def test_node_side_emits_placement_keyword():
    r"""A single-terminal node's user-set ``node_side`` is emitted as a TikZ placement
    key right after the kind, so the symbol sits on that side — e.g. an inversion bubble
    \node[ocirc, left] at (x,y){} is tangent. The side is the user's explicit choice
    (node_side), not inferred from gate context."""
    src = generate(_schematic(_comp("ground", node_side="left")))
    assert r"\node[ground, left] at (0,0) {};" in src


def test_node_side_default_emits_no_keyword():
    """With no ``node_side`` set, the node is centred — no placement keyword appears."""
    src = generate(_schematic(_comp("ground")))
    assert r"\node[ground] at (0,0) {};" in src
    for side in ("left", "right", "above", "below"):
        assert f"ground, {side}" not in src
