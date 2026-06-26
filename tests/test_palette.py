"""
Tests for the component palette (app/ui/palette, spec §10.2).

Offscreen Qt. Cover the redesigned panel: category cards, active-category items,
the in-use-in-document section, search, and that clicking a tile starts placement.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - host-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.canvas.scene import SchematicScene  # noqa: E402
from app.components.registry import REGISTRY  # noqa: E402
from app.ui.palette import ComponentPalette  # noqa: E402


def _palette():
    p = ComponentPalette()
    p.set_scene(SchematicScene())
    return p


def test_builds_with_a_card_per_category():
    from app.ui.palette import _palette_category

    p = _palette()
    # One card per *palette* category (raw Logic split into Gates American/European
    # plus a Logic-blocks group, supply rails/batteries split into Supplies — see
    # _palette_category).
    cats = {_palette_category(k, d.category) for k, d in REGISTRY.items()}
    assert set(p._cards) == cats
    assert p._active_cat in cats  # a default active category is selected
    assert {"Gates (Am)", "Gates (Eu)", "Logic", "Supplies"} <= cats


def test_categories_split_into_circuitikz_and_vanilla_sections():
    """The palette has two top-level category sections: CircuiTikZ (all the symbol
    categories) and Vanilla TikZ (our drawing primitives — Drawing). Every category
    lands in exactly one, with the drawing category in the vanilla section."""
    from app.ui.palette import _VANILLA_CATEGORIES

    p = _palette()
    assert "Drawing" in p._vanilla_cats
    assert "Drawing" not in p._circuitikz_cats
    # Partition: disjoint and together cover every category; no symbol category leaks
    # into the vanilla section.
    assert set(p._circuitikz_cats).isdisjoint(p._vanilla_cats)
    assert set(p._circuitikz_cats) | set(p._vanilla_cats) == set(p._ordered_cats)
    assert all(c in _VANILLA_CATEGORIES for c in p._vanilla_cats)
    # The default-open category is a CircuiTikZ one, not a drawing category.
    assert p._active_cat in p._circuitikz_cats
    # Both sections are shown (the manual/curated libraries both have drawing kinds).
    assert not p._circuitikz.isHidden() and not p._vanilla.isHidden()


def test_american_components_sorted_before_european():
    """For kinds *not* in the explicit display order, the palette groups
    american-style first then european within a category (an explicit order wins
    for listed kinds — see the Inductors group below)."""
    from app.ui.palette import _is_european_style
    from app.components.registry import display_rank

    p = _palette()
    for kinds in p._by_cat.values():
        unlisted = [k for k in kinds if display_rank(k) is None]
        styles = [_is_european_style(k) for k in unlisted]
        # No european kind precedes an american one (False sorts before True).
        assert styles == sorted(styles)
    # Concretely: the american resistor leads, the european resistor trails.
    res = p._by_cat["Resistors"]
    assert res.index("R") < res.index("eR")


def test_inductors_group_orders_inductors_transformers_choke():
    """The explicit display order wins in the Inductors group: the inductors, then
    the transformers, then the choke — even though the european inductor (`eL`)
    precedes the american transformers (which the heuristic alone would not allow)."""
    ind = _palette()._by_cat["Inductors"]
    assert ind.index("eL") < ind.index("transformer")          # inductors before transformers
    assert ind.index("european transformer core") < ind.index("choke")
    assert ind[-1] == "choke"


def test_supplies_split_out_of_sources():
    """Power rails and batteries live in their own Supplies category; the actual
    sources (incl. european ones) stay in Sources."""
    p = _palette()
    assert "Supplies" in p._cards
    assert "vcc" in p._by_cat["Supplies"] and "battery" in p._by_cat["Supplies"]
    assert "vcc" not in p._by_cat["Sources"]
    assert "V" in p._by_cat["Sources"] and "eV" in p._by_cat["Sources"]


def test_logic_split_into_gates_and_blocks():
    """The raw Logic registry category splits three ways: the boolean gates into
    style-grouped Gates (Am)/(Eu), and the blocks (flip-flops, mux/demux, ALU,
    adder) into their own Logic category."""
    p = _palette()
    assert "and" in p._by_cat["Gates (Am)"]
    assert "eand" in p._by_cat["Gates (Eu)"]
    assert "Logic (Am)" not in p._cards and "Logic (Eu)" not in p._cards
    # The blocks live in the new Logic category, not among the gates.
    logic = p._by_cat["Logic"]
    for block in ("flipflop D", "flipflop SR", "mux", "demux", "ALU", "adder"):
        assert block in logic
    assert "and" not in logic and "eand" not in logic
    # ...and no gate leaked into Logic / no block leaked into Gates.
    assert not any(b in p._by_cat["Gates (Am)"] for b in ("mux", "ALU", "flipflop D"))


def test_library_buildout_categories_present():
    """The library build-out added Tubes / Blocks / Transducers / Antennas
    categories (each with a representative icon kind), holding its new components."""
    from app.ui.palette import _CATEGORY_REP

    p = _palette()
    expected = {
        "Tubes": ("triode", "pentode"),
        "Blocks": ("amp", "lowpass"),
        "Transducers": ("loudspeaker", "mic"),
        "Antennas": ("antenna",),
    }
    for cat, members in expected.items():
        assert cat in p._cards, f"{cat} card missing"
        for kind in members:
            assert kind in p._by_cat[cat], f"{kind} not in {cat}"
        assert cat in _CATEGORY_REP
    # A handful of new kinds landed in the right existing categories.
    assert "varistor" in p._by_cat["Resistors"]
    assert "nigbt" in p._by_cat["Transistors"]
    assert "thyristor" in p._by_cat["Diodes"]
    assert "spst" in p._by_cat["Switches"]


def test_selecting_a_category_makes_it_active():
    from app.ui.palette import _category_doc

    p = _palette()
    target = "Sources" if "Sources" in p._cards else next(iter(p._cards))
    p._select_category(target)
    assert p._active_cat == target
    assert p._cards[target]._select  # card wired
    # The active header shows the manual's full section name (the card kept the
    # short tag); falls back to the short name where there is no manual section.
    doc = _category_doc(target)
    assert p._active.title() == (doc[0] if doc else target).upper()


def test_in_use_section_tracks_document():
    p = _palette()
    # The "in use" section now lives in a pinned bottom panel (independent
    # scroll); its visibility is driven through that panel.
    assert p._in_use_panel.isHidden()  # empty document → hidden
    p._scene.place_component("R", (2.0, 0.0))
    p._refresh_in_use()
    assert not p._in_use_panel.isHidden()  # now shows the placed kind


def test_search_switches_to_results():
    p = _palette()
    p._search.setText("euro")  # widget path → textChanged → _on_search
    assert not p._results.isHidden()
    assert p._circuitikz.isHidden() and p._vanilla.isHidden() and p._active.isHidden()
    # european kinds (eR, eL, eand, …) match.
    assert "(" in p._results.title()  # "SEARCH RESULTS (N)"
    p._search.clear()
    assert p._results.isHidden()
    assert not p._circuitikz.isHidden()


def test_clicking_a_tile_starts_placement(monkeypatch):
    p = _palette()
    started = []
    monkeypatch.setattr(p._scene, "start_placement", lambda k: started.append(k))
    p._place("C")
    assert started == ["C"]


def test_category_order_is_row_major_three_columns():
    """Categories are laid out three to a row, in the spec §5.4 order; the palette
    no longer assigns per-category keyboard shortcuts (no letter badges)."""
    import app.ui.palette as palette_mod

    assert not hasattr(palette_mod, "_CATEGORY_LETTERS")
    p = _palette()
    # Every palette category is present and the order reads row-by-row, with the
    # first row being Resistors | Inductors | Capacitors.
    assert p._ordered_cats[:3] == ["Resistors", "Inductors", "Capacitors"]
    assert set(p._ordered_cats) == set(p._cards)


def test_category_icons_render_from_representative_kind():
    """Each category's card icon renders from a real component symbol (no empty
    pixmaps), so the icons match the components."""
    from app.ui.palette import _CATEGORY_REP, _category_pixmap, _category_rep
    from app.components.registry import REGISTRY

    # The curated representative kinds are real and render.
    for cat, kind in _CATEGORY_REP.items():
        assert kind in REGISTRY, f"{cat}: representative {kind!r} not in registry"
        assert not _category_pixmap(kind, 20).isNull(), f"{cat}: icon failed to render"

    # Every actual palette category resolves to a renderable icon.
    p = _palette()
    for cat, kinds in p._by_cat.items():
        assert not _category_pixmap(_category_rep(cat, kinds), 20).isNull(), \
            f"{cat}: blank icon"


def test_unknown_category_icon_falls_back_to_first_member():
    """A category the curated map doesn't list (e.g. one the manual-generated library
    adds) takes its first member's symbol, so it never shows a blank card."""
    from app.ui.palette import _CATEGORY_REP, _category_rep

    assert "Brand New Category" not in _CATEGORY_REP
    assert _category_rep("Brand New Category", ["R", "C"]) == "R"
    assert _category_rep("Brand New Category", []) is None


def test_search_box_does_not_grab_initial_focus():
    """The search box uses click-focus so it doesn't steal focus at startup and
    swallow the palette letter/number hotkeys (regression)."""
    from PySide6.QtCore import Qt

    p = _palette()
    assert p._search.focusPolicy() == Qt.ClickFocus


def test_split_categories_are_selectable():
    """The split categories — Grounds, the two gate groups, and the Logic-blocks
    group — each render a card and become active when selected."""
    p = _palette()
    for cat in ("Grounds", "Gates (Am)", "Gates (Eu)", "Logic"):
        assert cat in p._cards
        p._select_category(cat)
        assert p._active_cat == cat


def test_wiring_quickbar_lists_terminals_category():
    """The canvas-side wiring bar is sourced by category (the Terminals connection
    markers), so it tracks the active library and is empty where there is none."""
    from app.ui.palette import WiringQuickBar, WIRING_CATEGORY

    bar = WiringQuickBar()
    expected = [k for k, d in REGISTRY.items() if d.category == WIRING_CATEGORY]
    assert bar._kinds == expected
    assert all(k in REGISTRY for k in bar._kinds)


def test_wiring_quickbar_place_starts_placement(monkeypatch):
    """The bar's placement hook starts placement of a kind, like a palette tile."""
    from app.ui.palette import WiringQuickBar

    bar = WiringQuickBar()
    scene = SchematicScene()
    bar.set_scene(scene)
    started: list[str] = []
    monkeypatch.setattr(scene, "start_placement", started.append)
    bar._place("R")
    assert started == ["R"]


def test_thumbnail_cache_keyed_by_symbol_style():
    """Thumbnails are cached per (kind, style value), so a styled kind re-renders when
    the document style changes while unstyled kinds share one entry."""
    import app.ui.palette as P

    P._thumb_cache.clear()
    P._thumb_style = {}
    # An unstyled kind's cache key carries no style value (shared across styles).
    assert P._thumb_style_value("text_node") == ""
    P._thumbnail("text_node")
    assert ("text_node", "") in P._thumb_cache


def test_active_category_shows_manual_doc_link():
    """The open (active) category's header carries a documentation-link button to
    the matching CircuiTikZ manual section; its tooltip is the full manual section
    name and it points at the manual URL with a fragment anchor (spec §10.2)."""
    from app.ui.palette import _MANUAL_BASE_URL

    p = _palette()
    p._select_category("Resistors")
    btn = p._active._doc_btn
    assert not btn.isHidden()                       # link shown for a manual category
    assert p._active._doc_url == f"{_MANUAL_BASE_URL}#sec:resistive-bipoles"
    assert "Resistive bipoles" in btn.toolTip()     # full manual name is the title
    # The header itself shows the full manual name (not the short "Resistors" card tag).
    assert p._active.title() == "RESISTIVE BIPOLES"


def test_active_category_doc_link_hidden_for_bespoke_categories():
    """Categories with no manual section (the bespoke Drawing/Annotations groups)
    hide the doc-link button rather than linking somewhere wrong."""
    p = _palette()
    for cat in ("Drawing", "Annotations"):
        if cat in p._cards:
            p._select_category(cat)
            assert p._active._doc_btn.isHidden()
            assert p._active._doc_url is None


def test_doc_link_opens_manual_url(monkeypatch):
    """Clicking the doc-link button opens the section URL in the browser."""
    import app.ui.palette as P

    opened: list[str] = []
    monkeypatch.setattr(P.QDesktopServices, "openUrl",
                        lambda url: opened.append(url.toString()))
    p = _palette()
    p._select_category("Transistors")
    p._active._doc_btn.click()
    assert opened == [f"{P._MANUAL_BASE_URL}#sec:transistors"]


def test_category_doc_titles_are_full_manual_names():
    """Every documented category resolves to (full manual title, manual URL); the
    title is the manual's own long section heading and the URL is a fragment of the
    single-page manual."""
    from app.ui.palette import _CATEGORY_DOC, _category_doc, _MANUAL_BASE_URL

    assert _category_doc("Brand New Category") is None
    for cat in _CATEGORY_DOC:
        title, url = _category_doc(cat)
        assert title and not title.isupper()        # the long heading, not the short tag
        assert url.startswith(f"{_MANUAL_BASE_URL}#sec:")


def test_category_doc_is_in_manual_section_order():
    """`_CATEGORY_DOC` is authored in the manual's own section order, and the
    manual-library palette orders its category cards by it (`list(_CATEGORY_DOC)`).
    Locks that order so a reordering of the doc map can't silently scramble the
    manual palette (which can't be exercised in the default-library test run)."""
    from app.ui.palette import _CATEGORY_DOC

    manual_order = [
        "Grounds", "Resistors", "Cap/Ind", "Diodes", "Sources", "Instruments",
        "Mechanical", "Misc", "Buses", "Crossings", "Arrows", "Terminals",
        "Connectors", "Blocks", "Transistors", "Tubes", "RF", "Electromech",
        "Transformers", "Amplifiers", "Switches", "Logic", "Flip-flops",
        "Mux/Demux", "Chips", "Displays",
    ]
    # The manual categories lead the map (curated-only aliases follow), in order.
    assert list(_CATEGORY_DOC)[:len(manual_order)] == manual_order


def test_category_names_follow_dark_theme():
    """Category card names are re-inked on a light/dark swap (their stylesheet pins
    the colour, so apply_theme must rebuild them with the new token; §10)."""
    from PySide6.QtWidgets import QLabel
    from app.ui import theme

    p = _palette()
    try:
        theme.set_dark(True)
        p.apply_theme()
        card = next(iter(p._cards.values()))
        labels = card.findChildren(QLabel)
        assert any(theme._DARK["TEXT"] in lbl.styleSheet() for lbl in labels)
    finally:
        theme.set_dark(False)
        p.apply_theme()
