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
