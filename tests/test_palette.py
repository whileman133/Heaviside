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
    # One card per *palette* category (Logic split into American/European, supply
    # rails/batteries split into Supplies — see _palette_category).
    cats = {_palette_category(k, d.category) for k, d in REGISTRY.items()}
    assert set(p._cards) == cats
    assert p._active_cat in cats  # a default active category is selected
    assert {"Logic (Am)", "Logic (Eu)", "Supplies"} <= cats


def test_american_components_sorted_before_european():
    """Within a category, american-style kinds come before european-style ones."""
    from app.ui.palette import _is_european_style

    p = _palette()
    for kinds in p._by_cat.values():
        styles = [_is_european_style(k) for k in kinds]
        # No european kind precedes an american one (False sorts before True).
        assert styles == sorted(styles)
    # Concretely: the american resistor leads, the european resistor trails.
    res = p._by_cat["Resistors"]
    assert res.index("R") < res.index("eR")


def test_supplies_split_out_of_sources():
    """Power rails and batteries live in their own Supplies category; the actual
    sources (incl. european ones) stay in Sources."""
    p = _palette()
    assert "Supplies" in p._cards
    assert "vcc" in p._by_cat["Supplies"] and "battery" in p._by_cat["Supplies"]
    assert "vcc" not in p._by_cat["Sources"]
    assert "V" in p._by_cat["Sources"] and "eV" in p._by_cat["Sources"]


def test_logic_split_by_style():
    """Logic is split into American and European palette categories."""
    p = _palette()
    assert "and" in p._by_cat["Logic (Am)"]
    assert "eand" in p._by_cat["Logic (Eu)"]
    assert "Logic" not in p._cards


def test_selecting_a_category_makes_it_active():
    p = _palette()
    target = "Sources" if "Sources" in p._cards else next(iter(p._cards))
    p._select_category(target)
    assert p._active_cat == target
    assert p._cards[target]._select  # card wired
    assert p._active._toggle.text() == target.upper()


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
    assert p._categories.isHidden() and p._active.isHidden()
    # european kinds (eR, eL, eand, …) match.
    assert "(" in p._results._toggle.text()  # "SEARCH RESULTS (N)"
    p._search.clear()
    assert p._results.isHidden()
    assert not p._categories.isHidden()


def test_clicking_a_tile_starts_placement(monkeypatch):
    p = _palette()
    started = []
    monkeypatch.setattr(p._scene, "start_placement", lambda k: started.append(k))
    p._place("C")
    assert started == ["C"]


def test_select_category_by_letter():
    """Each category's mnemonic letter activates it (keyboard shortcut path)."""
    from app.ui.palette import _CATEGORY_LETTERS

    p = _palette()
    assert p.select_category_by_letter("C")        # Capacitors
    assert p._active_cat == "Capacitors"
    assert p.select_category_by_letter("d")        # case-insensitive → Diodes
    assert p._active_cat == "Diodes"
    assert not p.select_category_by_letter("?")    # unknown letter → no-op
    assert p._active_cat == "Diodes"
    # The letter map is unique (no two categories share a key).
    assert len(set(_CATEGORY_LETTERS.values())) == len(_CATEGORY_LETTERS)


def test_place_active_index(monkeypatch):
    """Digit shortcuts place the Nth component of the active category."""
    p = _palette()
    started = []
    monkeypatch.setattr(p._scene, "start_placement", lambda k: started.append(k))
    p.select_category_by_letter("R")               # Resistors active
    kinds = p._by_cat["Resistors"]
    assert p.place_active_index(0)                  # press "1"
    assert started == [kinds[0]]
    assert p.place_active_index(2)                  # press "3"
    assert started == [kinds[0], kinds[2]]
    # Out-of-range index is ignored (no crash, returns False).
    assert not p.place_active_index(999)
    assert len(started) == 2


def test_category_icons_render_from_representative_kind():
    """Each category's card icon renders from a real component symbol (no empty
    pixmaps), so the icons match the components."""
    from app.ui.palette import _CATEGORY_REP, _category_pixmap
    from app.components.registry import REGISTRY

    for cat, kind in _CATEGORY_REP.items():
        assert kind in REGISTRY, f"{cat}: representative {kind!r} not in registry"
        pm = _category_pixmap(cat, 20)
        assert not pm.isNull(), f"{cat}: icon failed to render"


def test_search_box_does_not_grab_initial_focus():
    """The search box uses click-focus so it doesn't steal focus at startup and
    swallow the palette letter/number hotkeys (regression)."""
    from PySide6.QtCore import Qt

    p = _palette()
    assert p._search.focusPolicy() == Qt.ClickFocus


def test_grounds_uses_g_shortcut():
    """Grounds is reachable via 'G'; the two Logic groups use 'O' and 'E'."""
    p = _palette()
    assert p.select_category_by_letter("G")
    assert p._active_cat == "Grounds"
    assert p.select_category_by_letter("O")
    assert p._active_cat == "Logic (Am)"
    assert p.select_category_by_letter("E")
    assert p._active_cat == "Logic (Eu)"


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
