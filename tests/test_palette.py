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
    # One card per registry category; the manual library's categories are used
    # verbatim (no Logic/Sources splitting).
    p = _palette()
    cats = {d.category for d in REGISTRY.values()}
    assert set(p._cards) == cats
    assert p._active_cat in cats  # a default active category is selected
    # The manual categories are present.
    assert {"Resistors", "Diodes", "Sources", "Logic"} <= cats


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


def test_within_category_kinds_follow_manual_order():
    """Within a category the palette lists kinds in the **manual's order** (the
    REGISTRY scrape sequence), not alphabetical or american/european-grouped — so the
    palette mirrors how the CircuiTikZ manual presents each section."""
    from app.components.registry import REGISTRY

    p = _palette()
    for cat, kinds in p._by_cat.items():
        manual = [k for k, d in REGISTRY.items() if d.category == cat]
        assert list(kinds) == manual, cat
    # Concretely: in Logic the manual leads with the american gates, the european
    # family trailing (so the american AND still precedes the european one).
    logic = p._by_cat["Logic"]
    assert logic.index("american and port") < logic.index("european and port")
    # And the order is genuinely the manual's, not alphabetical: "american nand port"
    # follows "american or port" (manual sequence) though "nand" < "or" alphabetically.
    assert logic.index("american or port") < logic.index("american nand port")


def test_bespoke_annotations_sort_to_manual_position():
    """The bespoke ``short``/``open`` annotations sit where the manual lists them — at
    the **front** of Resistors ("Resistive bipoles") — not appended after the library
    kinds. ``short`` overrides its library entry in place; ``open`` is anchored right
    after it."""
    p = _palette()
    res = p._by_cat["Resistors"]
    assert res[:2] == ["short", "open"], res
    assert res.index("short") < res.index("R")


# NOTE: deleted test_inductors_group_orders_inductors_transformers_choke,
# test_supplies_split_out_of_sources, and test_logic_split_into_gates_and_blocks —
# the curated explicit display order, the Supplies split out of Sources, and the
# Logic→Gates(Am)/(Eu)+blocks split are all gone with the curated library. The
# manual library uses its own categories (Sources, Logic) verbatim and orders
# within a category by manual (REGISTRY scrape) order (covered above). The separate
# eR/eL/cuteL/eand kinds do not exist (american/european is a per-document style
# axis now).


def test_library_buildout_categories_present():
    """The manual library populates Tubes / Blocks categories (each with a
    renderable representative icon) and lands new kinds in the right categories."""
    from app.ui.palette import _category_rep, _category_pixmap

    p = _palette()
    expected = {
        "Tubes": ("triode", "pentode"),
        "Blocks": ("amp", "adder"),
    }
    for cat, members in expected.items():
        assert cat in p._cards, f"{cat} card missing"
        for kind in members:
            assert kind in p._by_cat[cat], f"{kind} not in {cat}"
        rep = _category_rep(cat, p._by_cat[cat])
        assert not _category_pixmap(rep, 20).isNull(), f"{cat}: blank icon"
    # A handful of kinds land in the right existing categories.
    assert "varistor" in p._by_cat["Resistors"]
    assert "nigbt" in p._by_cat["Transistors"]
    assert "thyristor" in p._by_cat["Diodes"]


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
    # Every palette category is present and the order follows the manual section
    # order (_CATEGORY_DOC), with the first row being Grounds | Resistors | Cap/Ind.
    assert p._ordered_cats[:3] == ["Grounds", "Resistors", "Cap/Ind"]
    assert set(p._ordered_cats) == set(p._cards)


def test_category_icons_render_from_representative_kind():
    """Each category's card icon renders from a real component symbol (no empty
    pixmaps), so the icons match the components. The representative is the first
    kind in the category (`_category_rep`)."""
    from app.ui.palette import _category_pixmap, _category_rep
    from app.components.registry import REGISTRY

    # Every actual palette category resolves to a real, renderable icon.
    p = _palette()
    for cat, kinds in p._by_cat.items():
        rep = _category_rep(cat, kinds)
        assert rep in REGISTRY, f"{cat}: representative {rep!r} not in registry"
        assert not _category_pixmap(rep, 20).isNull(), f"{cat}: blank icon"


def test_unknown_category_icon_falls_back_to_first_member():
    """`_category_rep` takes the category's first member as its representative
    symbol, so a card never shows a blank icon (and an empty category yields None)."""
    from app.ui.palette import _category_rep

    assert _category_rep("Brand New Category", ["R", "C"]) == "R"
    assert _category_rep("Brand New Category", []) is None


def test_search_box_does_not_grab_initial_focus():
    """The search box uses click-focus so it doesn't steal focus at startup and
    swallow the palette letter/number hotkeys (regression)."""
    from PySide6.QtCore import Qt

    p = _palette()
    assert p._search.focusPolicy() == Qt.ClickFocus


def test_categories_are_selectable():
    """Each manual category renders a card and becomes active when selected."""
    p = _palette()
    for cat in ("Grounds", "Diodes", "Sources", "Logic"):
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


def test_wiring_quickbar_offers_wire_and_annotation_tools():
    """The quick bar leads with the two routing-mode tools (Manhattan, then La Plata),
    then the current/voltage annotations (``short`` → *i*, ``open`` → *v*), above the
    Terminals markers."""
    from PySide6.QtWidgets import QToolButton

    from app.ui.palette import WiringQuickBar, _ANNOTATION_QUICK

    assert _ANNOTATION_QUICK == (("short", "i"), ("open", "v"))
    bar = WiringQuickBar()
    assert bar._annotations == [("short", "i"), ("open", "v")]
    tips = [b.toolTip() for b in bar.findChildren(QToolButton)]
    assert tips[0].startswith("Manhattan wire")          # routing modes lead
    assert tips[1].startswith("La Plata wire")
    assert "(short)" in tips[2] and "(open)" in tips[3]  # annotations next
    # the Terminals markers (if any) follow the 2 mode + 2 annotation tiles
    assert len(tips) == 4 + len(bar._kinds)


def test_wiring_quickbar_routing_modes_are_exclusive_and_set_scene():
    """The two routing tiles are a mutually-exclusive pair: selecting La Plata checks
    it (and unchecks Manhattan), sets the scene's routing style, and enters Wire mode;
    selecting Manhattan switches back."""
    from app.ui.palette import WiringQuickBar
    from app.canvas.scene import Mode

    bar = WiringQuickBar()
    scene = SchematicScene()
    bar.set_scene(scene)
    assert scene.wire_routing == "manhattan"             # default synced to the scene
    assert bar._mode_btns["manhattan"].isChecked()

    bar._select_routing("laplata")
    assert scene.wire_routing == "laplata" and scene.mode == Mode.WIRE
    assert bar._mode_btns["laplata"].isChecked()
    assert not bar._mode_btns["manhattan"].isChecked()

    bar._select_routing("manhattan")
    assert scene.wire_routing == "manhattan"
    assert bar._mode_btns["manhattan"].isChecked()
    assert not bar._mode_btns["laplata"].isChecked()


def test_wiring_quickbar_wire_tool_enters_wire_mode(monkeypatch):
    """Selecting a routing mode puts the scene in WIRE mode; the annotation tiles
    start placement of their kind (short/open)."""
    from app.ui.palette import WiringQuickBar
    from app.canvas.scene import Mode

    bar = WiringQuickBar()
    scene = SchematicScene()
    bar.set_scene(scene)
    bar._select_routing("manhattan")
    assert scene.mode == Mode.WIRE
    started: list[str] = []
    monkeypatch.setattr(scene, "start_placement", started.append)
    bar._place("short")
    bar._place("open")
    assert started == ["short", "open"]


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
    """A category with no manual section (the bespoke Drawing group) hides the
    doc-link button rather than linking somewhere wrong."""
    p = _palette()
    assert "Annotations" not in p._cards   # the invented category is gone (#manual)
    for cat in ("Drawing",):
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
