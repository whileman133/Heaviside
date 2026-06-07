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
