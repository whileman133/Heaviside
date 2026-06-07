"""
Tests for the component library (spec: ``spec/component-editor.md``).

``REGISTRY`` and the ``circuitikz`` codegen tables are now built from
``components/components.json`` (via ``app/components/library.py``).  These tests
pin the expected values as golden constants — independent of the source — so a
drift in the data file is caught here, and verify the registry/codegen are wired
from the library.  End-to-end behaviour is covered by ``test_registry``,
``test_codegen``, and ``test_examples``.

Regenerate the file with ``python tools/generate_components.py`` after changing a
component.
"""

from __future__ import annotations

from app.codegen import circuitikz as cg
from app.components import library
from app.components.model import Component, DiodeComponent, MosfetComponent, PinDef
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


def test_nigfete_def_is_mosfet():
    m = REGISTRY["nigfete"]
    assert m.pins == [
        PinDef("gate", (0.0, 0.0)), PinDef("drain", (1.0, -1.0)), PinDef("source", (1.0, 0.5)),
    ]
    assert m.component_class is MosfetComponent


def test_diode_def_is_diode():
    assert REGISTRY["D"].component_class is DiodeComponent


# ---------------------------------------------------------------------------
# Golden codegen-table values (now sourced from the library)
# ---------------------------------------------------------------------------

def test_two_terminal_kinds_include_bespoke():
    # Library two-terminal kinds plus the bespoke open/short annotations.
    assert {"R", "C", "L", "D", "V", "open", "short"} <= cg._TWO_TERMINAL_KINDS
    assert "op amp" not in cg._TWO_TERMINAL_KINDS


def test_multi_terminal_kinds():
    assert cg._MULTI_TERMINAL_KINDS == frozenset(
        {"op amp", "nigfete", "nigfetd", "pigfete", "pigfetd", "npn", "pnp"}
    )


def test_no_scale_corrections():
    # Lead-only alignment — every pin is bridged to the grid with a lead, so there
    # are no per-component xscale/yscale corrections.
    assert cg._MULTI_TERMINAL_EXTRA_OPTS == {}


def test_leads_golden():
    # Every non-origin pin gets a bridge lead to its grid offset.
    assert cg._MULTI_TERMINAL_LEADS["op amp"] == [("+", "+"), ("-", "-"), ("out", "out")]
    assert cg._MULTI_TERMINAL_LEADS["nigfete"] == [("drain", "drain"), ("source", "source")]
    assert cg._MULTI_TERMINAL_LEADS["npn"] == [("C", "collector"), ("E", "emitter")]


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
