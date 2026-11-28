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
    ("capacitor", (False, True, True)),
    ("empty diode", (False, True, True)),
    ("npn", (True, True, False)),
    ("twoport", (True, True, True)),
    ("op amp", (True, True, True)),
    ("ALU", (True, True, True)),
    ("short", (False, False, False)),
])
def test_symbol_style_capabilities(kind, expected) -> None:
    assert style_capabilities(_comp(kind)) == expected


def test_symbol_style_gate() -> None:
    plain = _comp("resistor", line_style="dashed", line_color="red", fill_color="yellow")
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
    res = scene.place_component("resistor", (9.0, 3.0))
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


@pytest.mark.parametrize(("kind", "style", "variants"), [
    ("capacitor", {}, {}),
    ("empty TVS diode", {}, {}),
    ("european resistor", {}, {}),
    ("sparkgap", {}, {"sparkgap/circle": True}),
])
def test_native_fill_paints_canvas_and_exports(_app, kind, style, variants):
    from app.codegen.circuitikz import generate

    scene = SchematicScene()
    scene.schematic.symbol_style = style
    comp = scene.place_component(kind, (0.0, 0.0))
    for name, enabled in variants.items():
        scene.set_component_variant(comp.id, name, enabled)

    plain = _render(scene, comp.id)
    scene.set_fill_color(comp.id, "yellow")
    assert _yellow_pixels(plain) == 0
    assert _yellow_pixels(_render(scene, comp.id)) > 20
    source = generate(scene.schematic)
    assert "fill=yellow" in source


@pytest.mark.parametrize(("kind", "filled_keyword"), [
    ("european resistor", "european resistor"),
    ("variable european resistor", "variable european resistor"),
    ("european resistive sensor", "european resistive sensor"),
    ("european potentiometer", "european potentiometer"),
    ("european light dependent resistor", "european light dependent resistor"),
    ("phR", "phR"),
    ("capacitor", "capacitor"),
    ("curved capacitor", "curved capacitor"),
    ("ecapacitor", "ecapacitor"),
    ("variable capacitor", "variable capacitor"),
    ("capacitive sensor", "capacitive sensor"),
    ("feC", "feC"),
])
def test_fill_applies_from_appearance_in_default_document(_app, kind, filled_keyword):
    from app.codegen.circuitikz import generate
    from app.ui.properties import FillBorderSection, PropertiesPanel

    scene = SchematicScene()
    comp = scene.place_component(kind, (0.0, 0.0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(comp.id)
    section = next(s for s in panel._sections if isinstance(s, FillBorderSection))
    assert all(not section._fill_row.itemAt(i).widget().isHidden()
               for i in range(section._fill_row.count()))
    assert f"to[{kind}]" in generate(scene.schematic)
    section._line_color.set_value("red")
    section._fill.set_value("yellow")
    section._commit()

    assert f"to[{filled_keyword}, color=red, fill=yellow]" in generate(scene.schematic)
    assert _yellow_pixels(_render(scene, comp.id)) > 20
    scene.undo()
    assert f"to[{kind}]" in generate(scene.schematic)


@pytest.mark.parametrize("kind", ["R", "vR", "pR", "sR", "ldR"])
def test_american_resistors_ignore_existing_fill(_app, kind):
    from app.codegen.circuitikz import generate

    scene = SchematicScene()
    part = scene.place_component(kind, (0.0, 0.0))
    plain = _render(scene, part.id)
    scene.set_fill_color(part.id, "yellow")
    assert style_capabilities(part) == (False, True, False)
    assert _render(scene, part.id) == plain
    source = generate(scene.schematic)
    assert f"to[{kind}]" in source
    assert "fill=yellow" not in source


@pytest.mark.parametrize("kind", ["igct", "empty igct", "stroke igct", "full igct"])
def test_igct_line_color_and_fill_capabilities(_app, kind):
    from app.codegen.circuitikz import generate

    scene = SchematicScene()
    comp = scene.place_component(kind, (0, 0))
    scene.set_line_color(comp.id, "red")
    scene.set_fill_color(comp.id, "yellow")
    has_fill = kind != "full igct"
    assert style_capabilities(comp) == (False, True, has_fill)
    expected = f"to[{kind}, color=red" + (", fill=yellow]" if has_fill else "]")
    assert expected in generate(scene.schematic)


def test_invert_checkbox_reverses_diode_and_preserves_export_options(_app, tmp_path):
    from app.codegen.circuitikz import generate
    from app.schematic.io import load, save
    from app.schematic.model import component_pin_positions
    from app.ui.properties import PropertiesPanel, TransformSection

    scene = SchematicScene()
    diode = scene.place_component("empty diode", (0, 0))
    scene.edit_component_options(diode.id, r"l={$V$}, v=$4$, voltage=raised, i=$4$")
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(diode.id)
    rotation = next(s for s in panel._sections if isinstance(s, TransformSection))
    assert not rotation._invert_cb.isHidden()
    plain = _render(scene, diode.id)
    rotation._invert_cb.setChecked(True)
    part = scene.schematic.components[0]
    assert part.invert
    assert component_pin_positions(part) == [(0, 0), (2, 0)]
    assert _render(scene, diode.id) != plain
    source = generate(scene.schematic, y_flip=True)
    assert r"to[empty diode, invert, l={$V$}, v=$4$, voltage=raised, i=$4$]" in source

    path = tmp_path / "inverted.hv"
    save(scene.schematic, path)
    assert load(path).components[0].invert
    scene.undo()
    assert not scene.schematic.components[0].invert
    assert _render(scene, diode.id) == plain


def test_invert_moves_wiper_and_preserves_its_wire(_app):
    from app.codegen.circuitikz import generate
    from app.schematic.model import component_pin_positions

    scene = SchematicScene()
    part = scene.place_component("vR", (0, 0))
    old_pins = component_pin_positions(part)
    wire = scene.add_wire([(-1, 1), old_pins[2]])
    assert wire is not None
    scene.invert_component(part.id, True)
    new_pins = component_pin_positions(scene.schematic.components[0])
    assert new_pins[:2] == old_pins[:2]
    assert new_pins[2] == (2 - old_pins[2][0], old_pins[2][1])
    assert new_pins[2] in scene.schematic.wires[0].points
    source = generate(scene.schematic)
    assert "to[vR, invert, name=" in source
    assert f"(node_{part.id[:8]}.wiper)" in source
    scene.undo()
    assert old_pins[2] in scene.schematic.wires[0].points


@pytest.mark.parametrize("kind", ["dipchip", "qfpchip"])
def test_chip_numbers_stay_upright_by_default_and_can_rotate(_app, tmp_path, kind):
    from app.codegen.circuitikz import generate
    from app.schematic.io import load, save
    from app.ui.properties import PropertiesPanel, VariantSection

    scene = SchematicScene()
    comp = scene.place_component(kind, (0, 0))
    scene.rotate_component(comp.id, 90)
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(comp.id)
    variants = next(s for s in panel._sections if isinstance(s, VariantSection))
    toggle = variants._checks["straight_numbers"]
    assert toggle.isChecked()
    straight = _render(scene, comp.id)
    assert "straight numbers" in generate(scene.schematic)

    toggle.setChecked(False)
    assert "rotated numbers" in generate(scene.schematic)
    assert _render(scene, comp.id) != straight
    path = tmp_path / "chip.hv"
    save(scene.schematic, path)
    assert "rotated numbers" in generate(load(path))
    scene.undo()
    assert "straight numbers" in generate(scene.schematic)
    assert _render(scene, comp.id) == straight


def test_ieee_gate_input_rack_from_properties(_app, tmp_path):
    from PySide6.QtWidgets import QLabel
    from app.codegen.circuitikz import generate
    from app.schematic.model import route_pin_aware
    from app.components import library
    from app.schematic.io import load, save
    from app.schematic.model import component_pin_positions
    from app.ui.properties import ParamSection, PropertiesPanel

    scene = SchematicScene()
    gate = scene.place_component("ieeestd nor port", (0, 0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(gate.id)
    section = next(s for s in panel._sections if isinstance(s, ParamSection))
    assert [label.text() for label in section.findChildren(QLabel)] == [
        "Number inputs", "Inner inputs",
    ]
    section._spins["inputs"].setValue(32)
    panel.show_component(gate.id)
    section._spins["inner_inputs"].setValue(2)
    scene.rotate_component(gate.id, 90)
    scene.edit_component_node_text(gate.id, "IC1A")

    comp = scene.schematic.components[0]
    pins = dict(zip((p.name for p in library.resolved_pins(comp)),
                    component_pin_positions(comp)))
    assert "in 32" in pins
    assert pins["in 32"][0] == pytest.approx(-8.68)
    assert scene.add_wire(route_pin_aware((-10, -1), pins["in 32"]))
    source = generate(scene.schematic)
    assert "number inputs=32, inner inputs=2, rotate=90" in source
    assert rf"node_{gate.id[:8]}.in 32" in source
    assert f"node[anchor=center] at (node_{gate.id[:8]}.text) {{IC1A}}" in source
    assert len(library.param_n_data(comp)["input_rack"]) == 2
    assert scene._comp_items[gate.id].boundingRect().height() > 1000

    path = tmp_path / "rack.hv"
    save(scene.schematic, path)
    assert generate(load(path)) == source


@pytest.mark.parametrize(("kind", "choices", "toggles", "tokens"), [
    ("pmos", {
        "Source arrow": "Source arrow",
        "Circle": "Transistor circle and empty gate circle",
        "Gate options": "No gate",
    }, ("bodydiode", "bulk", "ferroel_gate"),
        ("arrowmos", "tr circle", "emptycircle", "nogate", "bodydiode", "bulk", "ferroel gate")),
    ("hemt", {
        "Source arrow": "−1",
        "Circle": "Transistor circle",
        "Gate options": "Split gate and no gate",
    }, ("bodydiode",),
        ("circuitikz/tripoles/hemt/source arrow=-1",
         "circuitikz/tripoles/hemt/split gate=true", "tr circle", "nogate", "bodydiode")),
    ("nigfete", {
        "Circle": "Transistor circle",
        "Gate options": "Double gate",
    }, ("bodydiode", "ferroel_gate", "solderdot"),
        ("tr circle", "doublegate", "bodydiode", "ferroel gate", "solderdot")),
])
def test_transistor_accessory_dropdowns_compose_and_round_trip(_app, tmp_path, kind,
                                                            choices, toggles, tokens):
    from PySide6.QtWidgets import QComboBox, QLabel
    from app.codegen.circuitikz import generate
    from app.components import library
    from app.schematic.io import load, save
    from app.ui.properties import PropertiesPanel, VariantSection

    scene = SchematicScene()
    component = scene.place_component(kind, (0, 0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(component.id)
    section = next(s for s in panel._sections if isinstance(s, VariantSection))
    dropdowns = {row.findChild(QLabel).text(): row.findChild(QComboBox)
                 for row in section._accessory_rows}
    assert set(dropdowns) == choices.keys()
    assert all(combo.currentText() == "Default" for combo in dropdowns.values())
    before = _render(scene, component.id)
    for label, choice in choices.items():
        dropdowns[label].setCurrentText(choice)
    for name in toggles:
        section._checks[name].setChecked(True)

    component = scene.schematic.components[0]
    assert set(library.variant_tikz(kind, component.variants)[1]) == set(tokens)
    assert library.accessory_record(component)
    assert _render(scene, component.id) != before
    if kind == "nigfete":
        assert "G2" in {pin.name for pin in library.resolved_pins(component)}
    path = tmp_path / "transistor.hv"
    save(scene.schematic, path)
    assert generate(load(path)) == generate(scene.schematic)

    combo = dropdowns["Gate options"]
    combo.setCurrentText("Default")
    assert not {"nogate", "doublegate", "circuitikz/tripoles/hemt/split gate=true"}.intersection(
        library.variant_tikz(kind, scene.schematic.components[0].variants)[1])
    scene.undo()
    assert set(library.variant_tikz(kind, scene.schematic.components[0].variants)[1]) == set(tokens)
