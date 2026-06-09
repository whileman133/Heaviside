"""Inspector section tests (offscreen)."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtWidgets import QApplication, QLabel, QSpinBox  # noqa: E402


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def test_param_section_does_not_leak_labels_on_rebind(_app):
    """Regression: the parametric spinbox section rebinds on every spinner step;
    it must refresh in place, not stack a new "inputs" label each time."""
    from app.ui.properties import ParamSection
    from app.canvas.scene import SchematicScene
    from app.components.model import Component

    sec = ParamSection()
    scene = SchematicScene()
    comp = Component(id="g", kind="and", position=(0, 0), rotation=0, options="",
                     params={"inputs": 2})

    for n in range(2, 12):                      # simulate ten spinner steps
        comp.params["inputs"] = n
        sec.bind(comp, scene)                   # what the inspector does each change

    assert len(sec.findChildren(QLabel)) == 1   # exactly one "Inputs" label
    assert len(sec.findChildren(QSpinBox)) == 1
    assert sec._spin.value() == 11

    # Selecting a non-parametric component removes the control entirely.
    sec._load(Component(id="r", kind="R", position=(0, 0), rotation=0, options=""))
    assert sec.findChildren(QLabel) == []


def test_param_section_applies_only_to_parametric_kinds(_app):
    from app.ui.properties import ParamSection
    from app.components.model import Component

    sec = ParamSection()
    assert sec.applies_to(Component(id="a", kind="and", position=(0, 0), rotation=0, options=""))
    assert not sec.applies_to(Component(id="b", kind="R", position=(0, 0), rotation=0, options=""))


def test_multi_select_edits_all_same_kind_components(_app):
    """Binding the panel to several same-kind components edits all of them, as a
    single undo step; a mixed-kind selection falls back to a count."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import OptionsSection, PropertiesPanel

    scene = SchematicScene()
    a = scene.place_component("R", (2.0, 0.0))
    b = scene.place_component("R", (6.0, 0.0))

    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_components([a.id, b.id])
    assert panel._header.text().startswith("2 × ")

    opt = next(s for s in panel._sections if isinstance(s, OptionsSection))
    assert opt._comp_ids == [a.id, b.id]
    opt._field.setText("l=$R_x$")
    opt._commit()
    assert all(c.options == "l=$R_x$" for c in scene.schematic.components)

    scene.undo()  # one step reverts both
    assert all(c.options == "" for c in scene.schematic.components)


def test_multi_select_mixed_kinds_shows_count(_app):
    from app.canvas.scene import SchematicScene
    from app.ui.properties import PropertiesPanel

    scene = SchematicScene()
    a = scene.place_component("R", (2.0, 0.0))
    c = scene.place_component("C", (6.0, 0.0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_components([a.id, c.id])
    assert panel._header.text() == "2 items selected"


def test_apply_theme_reinks_labels(_app):
    """PropertiesPanel.apply_theme re-inks the header / section / hint labels for a
    light↔dark swap (their stylesheets pin the colour, so they would otherwise stay
    light when the toolbar toggle forces dark; §10)."""
    from app.ui.properties import PropertiesPanel
    from app.canvas.scene import SchematicScene
    from app.ui import theme

    try:
        scene = SchematicScene()
        comp = scene.place_component("R", (2.0, 2.0))
        panel = PropertiesPanel()
        panel.set_scene(scene)
        panel.show_component(comp.id)  # populates header + section/hint labels

        theme.set_dark(True)
        panel.apply_theme()
        assert theme._DARK["TEXT"] in panel._header.styleSheet()
        secs = [l for l in panel.findChildren(QLabel) if l.objectName() == "sectionLabel"]
        hints = [l for l in panel.findChildren(QLabel) if l.objectName() == "hintLabel"]
        assert secs and all(theme._DARK["ICON"] in l.styleSheet() for l in secs)
        assert all(theme._DARK["ICON_MUTED"] in l.styleSheet() for l in hints)

        theme.set_dark(False)
        panel.apply_theme()
        assert theme._LIGHT["TEXT"] in panel._header.styleSheet()
    finally:
        theme.set_dark(False)
