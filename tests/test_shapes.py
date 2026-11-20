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
from app.components.shapes import (COLORABLE_KINDS, DASHABLE_KINDS, FILLABLE_KINDS,
                                   style_capabilities, symbol_style)
from app.schematic.model import Component


@pytest.fixture(scope="module")
def _app():
    return QApplication.instance() or QApplication([])


def _comp(kind: str, **style) -> Component:
    c = Component(id="x", kind=kind, position=(0.0, 0.0), rotation=0, options="")
    for k, v in style.items():
        setattr(c, k, v)
    return c


def test_style_capability_tables_match_registry() -> None:
    assert COLORABLE_KINDS | DASHABLE_KINDS | FILLABLE_KINDS <= set(REGISTRY)


@pytest.mark.parametrize(("kind", "expected"), [
    ("R", (False, True, False)),
    ("capacitor", (False, True, False)),
    ("empty diode", (False, True, False)),
    ("npn", (True, True, False)),
    ("twoport", (True, True, True)),
    ("op amp", (True, True, True)),
    ("ALU", (True, True, True)),
    ("short", (False, False, False)),
])
def test_symbol_style_capabilities(kind, expected) -> None:
    assert style_capabilities(_comp(kind)) == expected


def test_symbol_style_gate() -> None:
    plain = _comp("R", line_style="dashed", line_color="red", fill_color="yellow")
    assert symbol_style(plain) == ("", "red", "")
    amp = _comp("op amp", line_style="dashed", line_color="red", fill_color="yellow")
    assert symbol_style(amp) == ("dashed", "red", "yellow")
    assert symbol_style(_comp("op amp")) == ("", "", "")


def test_body_fill_index_finds_bodies_not_slivers() -> None:
    assert all(body_fill_index(kind) is not None for kind in FILLABLE_KINDS)
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


def test_symbol_colour_leaves_canvas_leads_unchanged(_app) -> None:
    scene = SchematicScene()
    part = scene.place_component("generic", (0.0, 0.0))

    def render_body() -> QImage:
        image = QImage(160, 80, QImage.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        painter.translate(20.0, 40.0)
        scene._comp_items[part.id].paint(painter, None)
        painter.end()
        return image

    plain = render_body()
    scene.set_line_color(part.id, "red")
    styled = render_body()
    # The symbol's left and right leads end before the body at local x 26…94.
    for x, width in ((24, 15), (121, 15)):
        assert styled.copy(x, 37, width, 6) == plain.copy(x, 37, width, 6)
    assert any(
        styled.pixelColor(x, y).red() > 220 and styled.pixelColor(x, y).green() < 160
        for x in range(46, 114) for y in range(24, 56)
    )
