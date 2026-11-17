"""Which components have a fillable body (``app/components/shapes.py``) and how that
reaches the canvas: the data is consistent with the registry, the gate is shared,
and a fill actually paints pixels only for a shaped symbol."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.canvas.scene import SchematicScene
from app.canvas.svgsym import body_fill_index
from app.components.registry import REGISTRY
from app.components.shapes import SHAPED_KINDS, is_shaped, symbol_style
from app.schematic.model import Component


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def _comp(kind: str, **style) -> Component:
    c = Component(id="x", kind=kind, position=(0.0, 0.0), rotation=0, options="")
    for k, v in style.items():
        setattr(c, k, v)
    return c


def test_shaped_kinds_are_real_registry_kinds() -> None:
    """Guards a stale table after the component library is regenerated."""
    assert SHAPED_KINDS <= set(REGISTRY)


def test_examples_from_the_spec() -> None:
    for kind in ("op amp", "empty diode", "ALU"):
        assert is_shaped(_comp(kind)), kind
    for kind in ("R", "capacitor", "ground", "npn"):   # no enclosed body
        assert not is_shaped(_comp(kind)), kind


def test_symbol_style_gate() -> None:
    plain = _comp("R", line_style="dashed", line_color="red", fill_color="yellow")
    assert symbol_style(plain) == ("dashed", "red", "")  # no body: style + colour, no fill
    amp = _comp("op amp", line_style="dashed", line_color="red", fill_color="yellow")
    assert symbol_style(amp) == ("dashed", "red", "yellow")
    assert symbol_style(_comp("op amp")) == ("", "", "")


def test_body_fill_index_finds_bodies_not_slivers() -> None:
    assert body_fill_index("op amp") is not None
    assert body_fill_index("empty diode") is not None
    for kind in ("resistor", "capacitor"):              # no body / plates only
        assert body_fill_index(kind) is None, kind


def _render(scene: SchematicScene, comp_id: str) -> QImage:
    item = scene._comp_items[comp_id]
    img = QImage(240, 200, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    scene.render(p, QRectF(img.rect()), item.sceneBoundingRect().adjusted(-12, -12, 12, 12))
    p.end()
    return img


def _yellow_pixels(img: QImage) -> int:
    n = 0
    for y in range(img.height()):
        for x in range(img.width()):
            c = img.pixelColor(x, y)
            if c.red() > 200 and c.green() > 200 and c.blue() < 120:
                n += 1
    return n


def test_fill_paints_a_shaped_symbol_but_is_ignored_on_a_plain_one(_app) -> None:
    scene = SchematicScene()
    amp = scene.place_component("op amp", (3.0, 3.0))
    res = scene.place_component("R", (9.0, 3.0))
    for comp in (amp, res):
        scene.set_fill_color(comp.id, "yellow")
    assert _yellow_pixels(_render(scene, amp.id)) > 200
    assert _yellow_pixels(_render(scene, res.id)) == 0
