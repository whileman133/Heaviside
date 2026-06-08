"""
Tests for the component library (spec: ``spec/component-editor.md``).

``REGISTRY`` and the ``circuitikz`` codegen tables are now built from
``components/definitions.json`` (via ``app/components/library.py``).  These tests
pin the expected values as golden constants — independent of the source — so a
drift in the data file is caught here, and verify the registry/codegen are wired
from the library.  End-to-end behaviour is covered by ``test_registry``,
``test_codegen``, and ``test_examples``.

Regenerate the file with ``python components/generate_components.py`` after changing a
component.
"""

from __future__ import annotations

from app.codegen import circuitikz as cg
from app.components import library
from app.components.model import Component, PinDef
from app.components.registry import REGISTRY

_LIBRARY_KINDS = set(REGISTRY) - library.NON_LIBRARY_KINDS


# ---------------------------------------------------------------------------
# The library covers the right kinds and the registry is wired from it
# ---------------------------------------------------------------------------

def test_library_covers_the_svg_symbol_kinds():
    assert set(library.load_library()) == _LIBRARY_KINDS


def test_registry_is_sourced_from_library():
    defs = library.library_component_defs()
    for kind in _LIBRARY_KINDS:
        assert REGISTRY[kind] == defs[kind]


def test_registry_has_all_kinds():
    assert set(REGISTRY) == _LIBRARY_KINDS | library.NON_LIBRARY_KINDS


# ---------------------------------------------------------------------------
# Golden ComponentDef values (independent of the data file)
# ---------------------------------------------------------------------------

def test_resistor_def():
    r = REGISTRY["R"]
    assert r.pins == [PinDef("in", (0.0, 0.0)), PinDef("out", (2.0, 0.0))]
    assert r.default_span == (2.0, 0.0)
    assert r.component_class is Component


def test_op_amp_def():
    a = REGISTRY["op amp"]
    assert a.pins == [
        PinDef("+", (-1.5, 0.5)), PinDef("-", (-1.5, -0.5)), PinDef("out", (1.5, 0.0)),
    ]
    assert a.bbox == (-1.5, -1.0, 1.5, 1.0)
    assert a.label_slots == ["l"]


def test_nigfete_def():
    m = REGISTRY["nigfete"]
    assert m.pins == [
        PinDef("gate", (0.0, 0.0)), PinDef("drain", (1.0, -1.0)), PinDef("source", (1.0, 0.5)),
    ]
    # Variants are generic per-instance state now; the kind is a plain Component.
    assert m.component_class is Component
    assert "body_diode" in {v["name"] for v in library.variant_specs("nigfete")}


def test_diode_declares_filled_variant():
    assert REGISTRY["D"].component_class is Component
    assert "filled" in {v["name"] for v in library.variant_specs("D")}


# ---------------------------------------------------------------------------
# Golden codegen-table values (now sourced from the library)
# ---------------------------------------------------------------------------

def test_two_terminal_kinds_include_bespoke():
    # Library two-terminal kinds plus the bespoke open/short annotations.
    assert {"R", "C", "L", "D", "V", "open", "short"} <= cg._TWO_TERMINAL_KINDS
    assert "op amp" not in cg._TWO_TERMINAL_KINDS


def test_european_and_cute_variants_present():
    """European resistor/inductor and cute inductor are registered path elements
    using CircuiTikZ's style-independent shape keywords, in the R/L categories."""
    expected = {
        "eR": ("Resistors", "european resistor"),
        "eL": ("Inductors", "european inductor"),
        "cuteL": ("Inductors", "cute inductor"),
    }
    for kind, (category, tikz) in expected.items():
        defn = REGISTRY[kind]
        assert defn.category == category
        assert defn.tikz_keyword == tikz
        assert [p.name for p in defn.pins] == ["in", "out"]
        # path emission (to[…]), so not classified multi-terminal/node
        assert kind in cg._TWO_TERMINAL_KINDS


def test_european_sources_and_variable_resistors_present():
    """European sources and variable-resistor/potentiometer are path elements in
    the Sources / Resistors categories, using `european …` shape keywords."""
    expected = {
        "eV": ("Sources", "european voltage source"),
        "eI": ("Sources", "european current source"),
        "ecV": ("Sources", "european controlled voltage source"),
        "ecI": ("Sources", "european controlled current source"),
        "evR": ("Resistors", "variable european resistor"),
        "epot": ("Resistors", "european potentiometer"),
        "ethermistor": ("Resistors", "european resistive sensor"),
        "pR": ("Resistors", "pR"),  # american potentiometer, pairs with epot
    }
    for kind, (category, tikz) in expected.items():
        defn = REGISTRY[kind]
        assert defn.category == category
        assert defn.tikz_keyword == tikz
        assert kind in cg._TWO_TERMINAL_KINDS  # two-terminal to[…] path elements


def test_european_logic_gates_present():
    """European (IEC) logic gates use the style-independent `european … port`
    keywords; the AND/OR family is parametric, NOT/buffer are fixed; all are
    multi-terminal nodes in the Logic category."""
    parametric = {"eand": "and", "eor": "or", "enand": "nand",
                  "enor": "nor", "exor": "xor", "exnor": "xnor"}
    fixed = {"enot": "not", "ebuffer": "buffer"}
    for kind, word in {**parametric, **fixed}.items():
        defn = REGISTRY[kind]
        assert defn.category == "Logic"
        assert defn.tikz_keyword == f"european {word} port"
        assert kind in cg._MULTI_TERMINAL_KINDS
    for kind in parametric:
        assert library.param_spec(kind) is not None      # variable input count
        assert library.param_spec(kind)["height_key"].startswith("tripoles/european")
    for kind in fixed:
        assert library.param_spec(kind) is None


def test_battery_cell_and_inst_amp_components():
    """Battery (multi-cell), the relabeled single Cell, and the instrumentation /
    transconductance amplifiers are registered with the right kind/category."""
    assert REGISTRY["battery1"].display_name == "Cell"          # single-cell, relabeled
    assert REGISTRY["battery"].display_name == "Battery"
    assert REGISTRY["battery"].category == "Sources"
    assert "battery" in cg._TWO_TERMINAL_KINDS                  # two-terminal to[…]
    for kind, tikz in (("instamp", "inst amp"), ("gmamp", "gm amp")):
        defn = REGISTRY[kind]
        assert defn.category == "Amplifiers"
        assert defn.tikz_keyword == tikz
        assert kind in cg._MULTI_TERMINAL_KINDS                 # node[…] with anchors
        assert [p.name for p in defn.pins] == ["+", "-", "out"]


def test_emission_is_path_or_node():
    # Emission collapses to two LaTeX-syntax groups: ``path`` (to[…]) and
    # ``node`` (node[…]).  Multi-terminal-ness is derived from the data, not a
    # third emission type.
    comps = library.load_library()
    assert {e["emission"] for e in comps.values()} == {"path", "node"}


def test_is_multi_terminal_entry_derived():
    comps = library.load_library()
    # A node element with anchored pins (op amp) is multi-terminal; a single-
    # point node element (ground) is not; a path element (R) never is.
    assert library.is_multi_terminal_entry(comps["op amp"]) is True
    assert library.is_multi_terminal_entry(comps["ground"]) is False
    assert library.is_multi_terminal_entry(comps["R"]) is False


def test_multi_terminal_kinds():
    # The curated multi-terminal kinds must stay classified as such.  This is a
    # subset check, not an exact inventory: importing more components (e.g. extra
    # transistor families) should not break it — only a regression in these will.
    assert {"op amp", "nigfete", "nigfetd", "pigfete", "pigfetd", "npn", "pnp"} <= (
        cg._MULTI_TERMINAL_KINDS
    )


def test_scale_corrections_golden():
    # BJT/MOSFET are scaled so their pins land on the grid (no diagonal stubs);
    # the op amp uses leads instead (it is absent here).  Checked per-kind so that
    # newly imported scaled components don't break the curated values.
    expected = {
        "npn": "xscale=1.1905, yscale=1.2987",
        "pnp": "xscale=1.1905, yscale=1.2987",
        "nigfete": "xscale=1.0204, yscale=0.962",
        "nigfetd": "xscale=1.0204, yscale=0.962",
        "pigfete": "xscale=1.0204, yscale=0.962",
        "pigfetd": "xscale=1.0204, yscale=0.962",
    }
    for kind, opts in expected.items():
        assert cg._MULTI_TERMINAL_EXTRA_OPTS[kind] == opts
    assert "op amp" not in cg._MULTI_TERMINAL_EXTRA_OPTS


def test_leads_golden():
    # op amp extends clean leads; BJT scales fully (no leads); MOSFET adds a
    # single small residual lead for the source's sub-grid y offset.
    assert cg._MULTI_TERMINAL_LEADS["op amp"] == [("+", "+"), ("-", "-"), ("out", "out")]
    assert cg._MULTI_TERMINAL_LEADS["npn"] == []
    assert cg._MULTI_TERMINAL_LEADS["nigfete"] == [("source", "source")]


def test_anchor_pin_golden():
    assert cg._MULTI_TERMINAL_ANCHOR_PIN["nigfete"] == ("gate", "gate")
    assert cg._MULTI_TERMINAL_ANCHOR_PIN["npn"] == ("B", "base")
    assert "op amp" not in cg._MULTI_TERMINAL_ANCHOR_PIN  # placed by centre


def test_pin_to_ctikz_golden():
    assert cg._PIN_TO_CTIKZ_ANCHOR["npn"] == {"base": "B", "collector": "C", "emitter": "E"}
    assert cg._PIN_TO_CTIKZ_ANCHOR["op amp"] == {"+": "+", "-": "-", "out": "out"}


def test_diode_kinds_golden():
    assert cg._DIODE_KINDS == frozenset({"D", "zD", "sD", "tD", "zzD", "leD"})


# ---------------------------------------------------------------------------
# Variants reflect filled / body_diode
# ---------------------------------------------------------------------------

def test_diodes_have_filled_variant():
    lib = library.load_library()
    for kind in ("D", "zD", "sD", "tD", "zzD", "leD"):
        assert "filled" in {v["name"] for v in lib[kind].get("variants", [])}


def test_mosfets_have_body_diode_variant():
    lib = library.load_library()
    for kind in ("nigfete", "nigfetd", "pigfete", "pigfetd"):
        assert "body_diode" in {v["name"] for v in lib[kind].get("variants", [])}


def test_parametric_accessors_for_logic_gate():
    """library resolves a parametric kind's value and pins from the instance."""
    from app.components import library
    from app.components.model import Component

    assert library.is_parametric("and") and not library.is_parametric("R")
    c = Component(id="x", kind="and", position=(0, 0), rotation=0, options="",
                  params={"inputs": 4})
    assert library.param_value(c) == 4
    pins = library.resolved_pins(c)
    assert [p.name for p in pins] == ["out", "in1", "in2", "in3", "in4"]
    # inputs symmetric about the output, on the 0.25 grid
    ys = [p.offset[1] for p in pins[1:]]
    assert ys == [-0.75, -0.25, 0.25, 0.75]
    # value clamps to the declared range
    over = Component(id="y", kind="and", position=(0, 0), rotation=0, options="",
                     params={"inputs": 99})
    assert library.param_value(over) == 16
