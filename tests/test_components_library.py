"""
Tests for the component library (spec: ``spec/component-editor.md``).

Proves the one flat data file ``components/components.json`` reconstructs today's
hand-maintained registry and codegen tables exactly — so it can replace the
scattered magic numbers without changing behaviour.

Regenerate the file with ``python tools/generate_components.py`` after changing a
component.
"""

from __future__ import annotations

from app.codegen import circuitikz as cg
from app.components import library
from app.components.registry import REGISTRY

# The 33 SVG-symbol kinds the library covers (everything but the bespoke ones).
_LIBRARY_KINDS = set(REGISTRY) - library.NON_LIBRARY_KINDS


def test_library_covers_the_svg_symbol_kinds():
    assert set(library.load_library()) == _LIBRARY_KINDS


def test_build_registry_equals_current_registry():
    assert library.build_registry() == REGISTRY


def test_build_registry_covers_all_kinds():
    assert set(library.build_registry()) == set(REGISTRY)


# --- the codegen tables reconstruct exactly (restricted to library kinds) ----

def test_codegen_emission_sets_match():
    t = library.build_codegen_tables()
    assert t["two_terminal_kinds"] == set(cg._TWO_TERMINAL_KINDS) - {"open", "short"}
    assert t["multi_terminal_kinds"] == set(cg._MULTI_TERMINAL_KINDS)
    assert t["node_kinds"] == set(cg._NODE_KINDS)


def test_codegen_anchor_pin_matches():
    assert library.build_codegen_tables()["anchor_pin"] == cg._MULTI_TERMINAL_ANCHOR_PIN


def test_codegen_pin_to_ctikz_matches():
    assert library.build_codegen_tables()["pin_to_ctikz"] == cg._PIN_TO_CTIKZ_ANCHOR


def test_codegen_extra_opts_matches():
    assert library.build_codegen_tables()["extra_opts"] == cg._MULTI_TERMINAL_EXTRA_OPTS


def test_codegen_leads_match():
    assert library.build_codegen_tables()["leads"] == cg._MULTI_TERMINAL_LEADS


# --- variants reflect filled / body_diode -----------------------------------

def test_diodes_have_filled_variant():
    lib = library.load_library()
    for kind in cg._DIODE_KINDS:
        variants = {v["name"] for v in lib[kind].get("variants", [])}
        assert "filled" in variants


def test_mosfets_have_body_diode_variant():
    lib = library.load_library()
    for kind in ("nigfete", "nigfetd", "pigfete", "pigfetd"):
        variants = {v["name"] for v in lib[kind].get("variants", [])}
        assert "body_diode" in variants
