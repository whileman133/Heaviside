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
    p = _palette()
    # One category card per category present in the registry.
    cats = {defn.category for defn in REGISTRY.values()}
    assert set(p._cards) == cats
    assert p._active_cat in cats  # a default active category is selected


def test_selecting_a_category_makes_it_active():
    p = _palette()
    target = "Sources" if "Sources" in p._cards else next(iter(p._cards))
    p._select_category(target)
    assert p._active_cat == target
    assert p._cards[target]._select  # card wired
    assert p._active._toggle.text() == target.upper()


def test_in_use_section_tracks_document():
    p = _palette()
    assert p._in_use.isHidden()  # empty document → hidden
    p._scene.place_component("R", (2.0, 0.0))
    p._refresh_in_use()
    assert not p._in_use.isHidden()  # now shows the placed kind


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
