"""
Tests for the component library (spec: ``spec/component-pipeline.md``).

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

# The manual library also contains scraped kinds (e.g. "short") that collide with
# the bespoke annotations; the registry uses the bespoke version, so those keys are
# in NON_LIBRARY_KINDS yet still present in the raw library. Account for the overlap.
_BESPOKE_IN_LIBRARY = set(library.load_library()) & library.NON_LIBRARY_KINDS


# ---------------------------------------------------------------------------
# The library covers the right kinds and the registry is wired from it
# ---------------------------------------------------------------------------

def test_library_covers_the_svg_symbol_kinds():
    # registry library-kinds == library keys, minus the bespoke-overridden collisions.
    assert set(library.load_library()) - _BESPOKE_IN_LIBRARY == _LIBRARY_KINDS


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


# NOTE: deleted the curated golden ComponentDef/codegen tests that pinned the
# curated library's contents, all gone with the curated library:
#   - test_op_amp_def, test_nigfete_def: curated baked pin offsets/scale (manual
#     op amp pins are at ±1.19 with empty label_slots; nfet variant is "bodydiode").
#   - test_diode_declares_filled_variant, test_diodes_have_filled_variant,
#     test_mosfets_have_body_diode_variant: curated "filled"/"body_diode" variant
#     names; the manual library models fills as distinct *kinds* (full/empty/stroke
#     diode) and uses a "bodydiode" variant name.
#   - test_two_terminal_kinds_include_bespoke: used curated C/D/V kinds.
#   - test_european_and_cute_variants_present, test_european_sources_and_variable_
#     resistors_present, test_european_logic_gates_present: the separate
#     eR/eL/cuteL/eV/eand/enot kinds do not exist — american/european/cute is a
#     per-document style axis in the manual library (library.STYLE_AXES), not kinds.
#   - test_switches_choke_and_merged_categories, test_battery_cell_and_inst_amp_
#     components: pinned curated kind names/categories/labels (nos/ncs/choke/
#     instamp/Supplies-merge) that don't match the manual scrape.
#   - test_scale_corrections_golden, test_pin_to_ctikz npn half (below): manual
#     bakes NO per-kind scale (_MULTI_TERMINAL_EXTRA_OPTS is empty).
#   - test_parametric_accessors_for_logic_gate: used the curated "and" pin layout.
# The manual library's contents are contract-tested by tests/test_generated_library.py.


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


# NOTE: deleted test_scale_corrections_golden — the manual library renders symbols
# at true size and bakes NO per-kind scale corrections (_MULTI_TERMINAL_EXTRA_OPTS
# is empty), so the curated golden xscale/yscale values no longer exist.


def test_no_leads_or_anchor_pin_tables():
    """The lead-bridge and anchor-pin codegen tables are gone (§4): alignment is
    scale-only and every node is centre-placed."""
    assert not hasattr(cg, "_MULTI_TERMINAL_LEADS")
    assert not hasattr(cg, "_MULTI_TERMINAL_ANCHOR_PIN")
    assert "leads" not in cg._CODEGEN_TABLES
    assert "anchor_pin" not in cg._CODEGEN_TABLES


def test_pin_to_ctikz_golden():
    # Pin → CircuiTikZ anchor map: the manual library names pins by their CircuiTikZ
    # anchor verbatim, so the map is identity. Each kind also carries the manual's other
    # documented anchors (geometric, body-diode, circle), so assert the primary
    # terminals as a subset rather than an exact map.
    assert {"B": "B", "C": "C", "E": "E"}.items() <= cg._PIN_TO_CTIKZ_ANCHOR["npn"].items()
    assert {"+": "+", "-": "-", "out": "out"}.items() <= cg._PIN_TO_CTIKZ_ANCHOR["op amp"].items()


def test_diode_kinds_golden():
    # The two-terminal members of the manual Diodes category (the set CircuiTikZ's
    # ``diodes/scale`` resizes); detection is category-based. The manual library
    # models fills as distinct *kinds* (full/empty/stroke), so assert a
    # representative handful is present and the set is non-empty.
    assert cg._DIODE_KINDS
    assert {"full diode", "empty diode", "stroke diode",
            "full Schottky diode", "full Zener diode"} <= cg._DIODE_KINDS


def test_diode_kinds_exclude_tripoles():
    # Thyristor/triac are in the Diodes category but are tripoles — their gate anchor
    # moves with diodes/scale, which would desync the canvas pin from the export, so
    # they must NOT be treated as scalable diodes.
    assert "thyristor" not in cg._DIODE_KINDS
    assert "triac" not in cg._DIODE_KINDS


def test_diode_scale_single_sourced():
    """The diode body scale is defined once (library) and shared by the codegen
    and the pipeline renderer (generate.py) — the canvas SVG assets and the emitted
    ``\\ctikzset{diodes/scale=…}`` can never drift apart."""
    from app.components import generate as renderer

    assert library.DIODE_SYMBOL_SCALE == 0.8           # golden value
    assert cg.DIODE_SYMBOL_SCALE == library.DIODE_SYMBOL_SCALE
    assert renderer.DIODE_SCALE == library.DIODE_SYMBOL_SCALE


def test_geometry_key_single_sourced():
    """``geometry_key`` is canonical in the library; svgsym and the renderer
    re-export the same function (byte-identical duplicates are gone)."""
    from app.components import generate as renderer

    assert renderer.geometry_key is library.geometry_key
    assert library.geometry_key("op amp") == "op_amp"
    assert library.geometry_key("flipflop D") == "flipflop_D"
    assert library.geometry_key("R") == "R"


# NOTE: deleted test_diodes_have_filled_variant, test_mosfets_have_body_diode_variant,
# and test_parametric_accessors_for_logic_gate — these pinned curated variant names
# ("filled"/"body_diode") and the curated "and" gate's pin layout. The manual library
# models diode fills as distinct kinds, names the MOSFET variant "bodydiode", and the
# parametric gate kind is "american and port" (not "and"). Variant/parametric
# behaviour is contract-tested by tests/test_generated_library.py.


def test_node_text_anchor_measured_per_kind():
    """The measured node-text anchor (components/add_text_anchors.py) loads into
    ComponentDef.text_anchor: a transistor's just east of centre, a power rail's
    north of it, an op-amp's at the centre. (Where the inline node text is
    west-anchored, so the canvas matches the compiled figure — §5.4.)"""
    from app.components.registry import REGISTRY

    npn = REGISTRY["npn"].text_anchor
    assert npn[0] > 0.0 and abs(npn[1]) < 1e-6        # a hair east, on the axis
    vcc = REGISTRY["vcc"].text_anchor
    assert abs(vcc[0]) < 1e-6 and vcc[1] < 0.0        # north (above the bar, y-down)
    assert REGISTRY["op amp"].text_anchor == (0.0, 0.0)
    # path-style kinds carry no node-text anchor.
    assert REGISTRY["R"].text_anchor == (0.0, 0.0)
