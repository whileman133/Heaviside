"""Arc placement, appearance, persistence, and TikZ export."""

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

from app.canvas.scene import SchematicScene
from app.codegen.circuitikz import generate
from app.components.model import ARC_ARROW_STYLES, ArcComponent
from app.schematic.io import load, save
from app.schematic.model import component_pin_positions
from app.ui.properties import ArcSection, FillBorderSection, PropertiesPanel


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def test_arc_inspector_canvas_and_round_trip(app, tmp_path):
    scene = SchematicScene()
    arc = scene.place_component("arc", (0, 0))
    assert isinstance(arc, ArcComponent)
    assert not component_pin_positions(arc)
    assert "arc[start angle=0, end angle=300, radius=0.5]" in generate(scene.schematic, y_flip=True)

    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(arc.id)
    geometry = next(section for section in panel._sections if isinstance(section, ArcSection))
    appearance = next(section for section in panel._sections if isinstance(section, FillBorderSection))
    assert [geometry._arrow.itemText(i) for i in range(geometry._arrow.count())] == list(ARC_ARROW_STYLES)
    geometry._numbers["radius"].setValue(0.625)
    geometry._numbers["radius"].editingFinished.emit()
    geometry._arrow.setCurrentText("Diamond")
    appearance._line_color.set_value("red")
    appearance._commit()
    scene.undo()
    assert scene.schematic.components[0].line_color == ""
    scene.redo()

    item = scene._comp_items[arc.id]
    image = QImage(160, 160, QImage.Format_ARGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    scene.render(painter, QRectF(image.rect()), item.sceneBoundingRect())
    painter.end()
    assert any(
        (color := image.pixelColor(x, y)).red() > 200 and color.green() < 100
        for x in range(image.width()) for y in range(image.height())
    )

    path = tmp_path / "arc.hv"
    save(scene.schematic, path)
    reopened = load(path)
    assert reopened.components[0] == scene.schematic.components[0]
    assert (
        r"\draw[-{Diamond}, draw=red] (0,0) arc[start angle=0, end angle=300, radius=0.625];"
        in generate(reopened, y_flip=True)
    )


def test_reflected_arc_preserves_sweep_and_export_precision(app):
    scene = SchematicScene()
    scene.place_component("arc", (0, 0))
    arc = scene.schematic.components[0]
    arc.start_angle, arc.end_angle = 30, -90
    arc.radius, arc.rotation, arc.mirror = 0.001, 90, True
    assert "arc[start angle=240, end angle=360, radius=0.001]" in generate(
        scene.schematic, y_flip=True
    )


def test_stroke_tvs_diode_accepts_independent_line_and_fill_colors(app):
    scene = SchematicScene()
    diode = scene.place_component("stroke TVS diode", (0, 0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(diode.id)
    appearance = next(section for section in panel._sections if isinstance(section, FillBorderSection))
    for row in (appearance._lc_row, appearance._fill_row):
        assert all(not row.itemAt(i).widget().isHidden() for i in range(row.count()))

    appearance._line_color.set_value("red")
    appearance._fill.set_value("yellow")
    appearance._commit()
    assert "to[stroke TVS diode, color=red, fill=yellow]" in generate(scene.schematic)
