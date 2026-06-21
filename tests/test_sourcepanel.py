"""Source panel tests (offscreen).

The CircuiTikZ source shown in the bottom panel must reflect what is actually
compiled and exported — in particular it must honour the line-hops display
preference (which defaults on), not silently drop hop bumps the .tex output
contains. See app/ui/sourcepanel.py and spec §10.4.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def _crossing_scene():
    """A scene with a horizontal wire (z=1, hops) crossing a vertical one (z=0)."""
    from app.canvas.scene import SchematicScene
    from app.schematic.model import Schematic, Wire

    sch = Schematic(version="0.5", name="x", wires=[
        Wire(id="h", points=[(0.0, 1.0), (4.0, 1.0)], z_order=1),
        Wire(id="v", points=[(2.0, 0.0), (2.0, 3.0)], z_order=0),
    ])
    scene = SchematicScene()
    scene.set_schematic(sch)
    return scene


def _panel(line_hops: bool):
    from app.ui.sourcepanel import SourcePanel
    return SourcePanel(preferences=SimpleNamespace(
        mark_unconnected_pins=False, line_hops=line_hops))


def test_source_panel_shows_line_hops_when_pref_on(_app):
    """With line-hops on (the default), the displayed source contains the
    `jump crossing` node — matching the compiled .tex."""
    panel = _panel(line_hops=True)
    panel.set_scene(_crossing_scene())
    assert "jump crossing" in panel._text.toPlainText()


def test_source_panel_omits_line_hops_when_pref_off(_app):
    """With the preference off, the crossing is a plain straight wire — the
    panel still mirrors the (now hop-free) compiled output."""
    panel = _panel(line_hops=False)
    panel.set_scene(_crossing_scene())
    text = panel._text.toPlainText()
    assert "jump crossing" not in text
    assert "(0,1) -- (4,1)" in text


def test_source_panel_shows_node_text(_app):
    """Invariant: node text the user adds appears in the displayed source (so the
    GUI source always matches what is rendered/compiled), and refreshes when it
    changes."""
    from app.canvas.scene import SchematicScene

    panel = _panel(line_hops=True)
    scene = SchematicScene()
    panel.set_scene(scene)
    comp = scene.place_component("npn", (5.0, 5.0))
    scene.edit_component_node_text(comp.id, "$Q_1$")
    panel.refresh()
    assert "$Q_1$" in panel._text.toPlainText()


def test_source_panel_soft_wraps(_app):
    """Long lines (e.g. a node line with chained node text) soft-wrap to the panel
    width so nothing the source contains scrolls off the right edge unseen."""
    from PySide6.QtWidgets import QPlainTextEdit

    panel = _panel(line_hops=True)
    assert panel._text.lineWrapMode() == QPlainTextEdit.WidgetWidth
