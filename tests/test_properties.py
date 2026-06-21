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


def test_inspector_controls_have_no_ancestor_stylesheet(_app):
    """Regression: a stylesheet on the scroll area or its content forces every
    descendant form control into Qt's compact stylesheet rendering, so the
    text fields / spinboxes / combos render *squished* (non-native). The panel
    must theme the scrollbar on the scrollbar widget itself and get its
    transparency from autoFillBackground — leaving the controls' ancestors free
    of any stylesheet so the controls stay native."""
    from app.ui.properties import PropertiesPanel

    panel = PropertiesPanel()
    # No stylesheet on any ancestor of the form controls.
    assert panel._scroll.styleSheet() == ""
    assert panel._content.styleSheet() == ""
    assert panel._scroll.viewport().styleSheet() == ""
    # Transparency comes from the attribute, not a sheet.
    assert panel._scroll.viewport().autoFillBackground() is False
    assert panel._content.autoFillBackground() is False
    # The scrollbar is still themed — on the scrollbar widget directly.
    assert panel._scroll.verticalScrollBar().styleSheet() != ""


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
    assert sec._spins["inputs"].value() == 11

    # Selecting a non-parametric component removes the control entirely.
    sec._load(Component(id="r", kind="R", position=(0, 0), rotation=0, options=""))
    assert sec.findChildren(QLabel) == []


def test_param_section_two_spinboxes_for_mux(_app):
    """A multi-parameter kind (mux) shows one spinbox per parameter — Inputs and
    Selects — both wired to its own param name."""
    from app.ui.properties import ParamSection
    from app.canvas.scene import SchematicScene
    from app.components.model import Component

    sec = ParamSection()
    scene = SchematicScene()
    comp = Component(id="m", kind="mux", position=(0, 0), rotation=0, options="",
                     params={"inputs": 4, "selects": 2})
    sec.bind(comp, scene)
    assert set(sec._spins) == {"inputs", "selects"}
    assert sec._spins["inputs"].value() == 4
    assert sec._spins["selects"].value() == 2
    assert len(sec.findChildren(QSpinBox)) == 2


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


def test_multi_select_mixed_kinds_edits_shared_properties(_app):
    """A mixed-kind selection (resistor + capacitor) exposes the shared,
    kind-independent capability sections (stroke, rotation) and edits them across
    every selected component as one undo step. Kind-specific sections (the
    CircuiTikZ options string) are suppressed."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import (
        OptionsSection,
        StrokeWidthSection,
        TransformSection,
        PropertiesPanel,
    )

    scene = SchematicScene()
    a = scene.place_component("R", (2.0, 0.0))
    c = scene.place_component("C", (6.0, 0.0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_components([a.id, c.id])

    # Header reflects a shared-property multi-edit, not the old count-only fallback.
    assert "shared properties" in panel._header.text()

    stroke = next(s for s in panel._sections if isinstance(s, StrokeWidthSection))
    transform = next(s for s in panel._sections if isinstance(s, TransformSection))
    options = next(s for s in panel._sections if isinstance(s, OptionsSection))

    # A bound section carries the selection's ids; an unbound one is cleared.
    assert stroke._comp_ids == [a.id, c.id]
    assert transform._comp_ids == [a.id, c.id]
    # Kind-specific free-text section is suppressed for a mixed selection.
    assert options._comp_ids == []

    # Editing stroke width hits both kinds as a single undo step.
    stroke._width.setValue(1.2)
    stroke._commit()
    assert all(abs(comp.line_width - 1.2) < 1e-9 for comp in scene.schematic.components)
    scene.undo()
    assert all(abs(comp.line_width - 0.4) < 1e-9 for comp in scene.schematic.components)

    # Rotation likewise applies to both as one step.
    transform._on_rotate(90)
    assert all(comp.rotation == 90 for comp in scene.schematic.components)
    scene.undo()
    assert all(comp.rotation == 0 for comp in scene.schematic.components)


def test_multi_select_symbol_and_block_share_stroke_width(_app):
    """A symbol + a block (resistor + rectangle) now share the unified stroke
    width: selecting both exposes the Stroke section and a single edit sets the
    width of both as one undo step. Sections that apply to only one of them
    (Fill & line style → rect only; the options string → symbol only) stay
    hidden."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import (
        FillBorderSection,
        OptionsSection,
        StrokeWidthSection,
        PropertiesPanel,
    )

    scene = SchematicScene()
    r = scene.place_component("R", (2.0, 0.0))       # symbol
    box = scene.place_component("rect", (6.0, 0.0))  # block
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_components([r.id, box.id])

    assert "shared properties" in panel._header.text()

    stroke = next(s for s in panel._sections if isinstance(s, StrokeWidthSection))
    fill = next(s for s in panel._sections if isinstance(s, FillBorderSection))
    options = next(s for s in panel._sections if isinstance(s, OptionsSection))

    # Stroke (unified width) applies to both; fill (rect-only) and options
    # (symbol-only) apply to just one, so they're suppressed for the mixed set.
    assert stroke._comp_ids == [r.id, box.id]
    assert fill._comp_ids == []
    assert options._comp_ids == []

    stroke._width.setValue(1.6)
    stroke._commit()
    assert all(abs(c.line_width - 1.6) < 1e-9 for c in scene.schematic.components)
    scene.undo()  # one step reverts both
    assert all(abs(c.line_width - 0.4) < 1e-9 for c in scene.schematic.components)


def test_scale_section_applies_to_gates_only_and_is_undoable(_app):
    """The Size (scale) section shows for logic gates only; changing it sets the
    gate's scale across the selection as one undo step."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import ScaleSection, PropertiesPanel
    from app.components.model import Component

    sec = ScaleSection()
    gate = Component(id="g", kind="and", position=(0.0, 0.0), rotation=0, options="")
    res = Component(id="r", kind="R", position=(0.0, 0.0), rotation=0, options="")
    assert sec.applies_to(gate) is True
    assert sec.applies_to(res) is False

    scene = SchematicScene()
    a = scene.place_component("and", (10.0, 10.0))   # default scale 1.0
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(a.id)
    s = next(x for x in panel._sections if isinstance(x, ScaleSection))
    assert s._comp_ids == [a.id]
    s._combo.setCurrentIndex(s._values.index(0.5))   # pick 50 %
    assert abs(scene.schematic.components[0].scale - 0.5) < 1e-9
    scene.undo()
    assert abs(scene.schematic.components[0].scale - 1.0) < 1e-9


def test_multi_wire_select_edits_all_as_one_undo_step(_app):
    """Selecting several wires and editing a property in the inspector applies it to
    every selected wire as a single undo step."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import PropertiesPanel

    scene = SchematicScene()
    a = scene.add_wire([(0.0, 0.0), (3.0, 0.0)])
    b = scene.add_wire([(0.0, 2.0), (3.0, 2.0)])
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_wires([a.id, b.id])
    assert panel._header.text() == "2 wires selected"
    assert panel._wire_section._wire_ids == [a.id, b.id]

    # Edit line width via the section's debounced commit; applies to both wires.
    panel._wire_section._width.setValue(1.5)
    panel._wire_section._commit()
    assert all(abs(w.line_width - 1.5) < 1e-9 for w in scene.schematic.wires)
    scene.undo()  # one step reverts both
    assert all(abs(w.line_width - 0.4) < 1e-9 for w in scene.schematic.wires)

    # A discrete control (start marker) also applies to both as one step.
    panel._wire_section._start_marker.setCurrentText("Arrow")
    panel._wire_section._on_start_marker()
    assert all(w.start_marker == "arrow" for w in scene.schematic.wires)
    scene.undo()
    assert all(w.start_marker == "" for w in scene.schematic.wires)


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


# ---------------------------------------------------------------------------
# Pending-edit flush (debounced sections)
# ---------------------------------------------------------------------------

def test_unbind_flushes_pending_debounced_edit(_app):
    """Clearing the panel while an options edit is still inside the debounce
    window commits it first instead of dropping the keystrokes."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import OptionsSection, PropertiesPanel

    scene = SchematicScene()
    comp = scene.place_component("R", (2.0, 0.0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(comp.id)

    sec = next(s for s in panel._sections if isinstance(s, OptionsSection))
    sec._field.setText("l=$R_{42}$")     # starts the 300 ms debounce timer
    assert sec._timer.isActive()

    panel.clear()                        # unbind before the timer fired
    assert scene.schematic.components[0].options == "l=$R_{42}$"
    assert not sec._timer.isActive()


def test_node_text_section_applies_only_to_node_style(_app):
    """NodeTextSection shows for node-style kinds (npn, vcc) and hides for path-style
    (R) and drawing annotations (text_node)."""
    from app.ui.properties import NodeTextSection
    from app.components.model import Component, TextNodeComponent

    sec = NodeTextSection()
    npn = Component(id="q", kind="npn", position=(0, 0), rotation=0, options="")
    vcc = Component(id="v", kind="vcc", position=(0, 0), rotation=0, options="")
    res = Component(id="r", kind="R", position=(0, 0), rotation=0, options="")
    txt = TextNodeComponent(id="t", kind="text_node", position=(0, 0), rotation=0,
                            options="hi")
    assert sec.applies_to(npn) and sec.applies_to(vcc)
    assert not sec.applies_to(res)
    assert not sec.applies_to(txt)


def test_node_text_section_edits_node_text_undoably(_app):
    """Editing the Node text field commits node_text via an undoable command."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import NodeTextSection, PropertiesPanel

    scene = SchematicScene()
    comp = scene.place_component("npn", (2.0, 2.0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(comp.id)

    sec = next(s for s in panel._sections if isinstance(s, NodeTextSection))
    assert sec._comp_ids == [comp.id]            # bound for a node-style kind
    sec._field.setText("$Q_1$")
    panel.flush_pending_edits()
    assert scene._component_by_id(comp.id).node_text == "$Q_1$"

    scene.undo()
    assert scene._component_by_id(comp.id).node_text == ""


def test_options_section_relabels_for_node_style(_app):
    """For a node-style kind the options field is the node[…] bracket, so its title
    reads 'Node options'; a path-style kind keeps 'CircuiTikZ options'."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import OptionsSection
    from app.components.model import Component

    sec = OptionsSection()
    scene = SchematicScene()
    sec.bind(Component(id="q", kind="npn", position=(0, 0), rotation=0, options=""), scene)
    assert sec._title_label.text() == "Node options"
    sec.bind(Component(id="r", kind="R", position=(0, 0), rotation=0, options=""), scene)
    assert sec._title_label.text() == "CircuiTikZ options"


def test_panel_flush_pending_edits_commits_every_section(_app):
    """PropertiesPanel.flush_pending_edits commits a pending debounced edit
    immediately (what MainWindow calls before save/export)."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import OptionsSection, PropertiesPanel

    scene = SchematicScene()
    comp = scene.place_component("R", (2.0, 0.0))
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_component(comp.id)

    sec = next(s for s in panel._sections if isinstance(s, OptionsSection))
    sec._field.setText("l=$R_x$")
    assert sec._timer.isActive()
    panel.flush_pending_edits()
    assert scene.schematic.components[0].options == "l=$R_x$"
    # Section stays bound after a flush (unlike unbind).
    assert sec._comp_ids == [comp.id]


def test_wire_section_unbind_flushes_pending_edit(_app):
    """The wire inspector flushes its debounced line-width edit on unbind too."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import PropertiesPanel

    scene = SchematicScene()
    wire = scene.add_wire([(0.0, 0.0), (3.0, 0.0)])
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_wire(wire.id)
    panel._wire_section._width.setValue(2.5)     # debounced
    assert panel._wire_section._timer.isActive()
    panel.clear()
    assert abs(scene.schematic.wires[0].line_width - 2.5) < 1e-9


# ---------------------------------------------------------------------------
# Focus guards on programmatic reloads
# ---------------------------------------------------------------------------

def test_options_load_does_not_clobber_focused_field(_app):
    """A programmatic reload while the user is typing must not replace the
    field text (which would also jump the cursor to the end)."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import OptionsSection

    scene = SchematicScene()
    comp = scene.place_component("R", (2.0, 0.0))
    sec = OptionsSection()
    sec.bind(comp, scene)
    sec._field.hasFocus = lambda: True           # simulate active typing
    sec._field.setText("l=$R_{half-typed")
    sec._timer.stop()                            # isolate the load behaviour

    sec._load(comp)                              # concurrent re-bind
    assert sec._field.text() == "l=$R_{half-typed"

    sec._field.hasFocus = lambda: False          # focus left → load applies
    sec._load(comp)
    assert sec._field.text() == comp.options


def test_text_and_bipole_loads_guard_focus(_app):
    from app.canvas.scene import SchematicScene
    from app.ui.properties import BipoleLabelSection, TextContentSection

    scene = SchematicScene()
    text_comp = scene.place_component("text_node", (2.0, 0.0))
    sec = TextContentSection()
    sec.bind(text_comp, scene)
    sec._field.hasFocus = lambda: True
    sec._field.setText("typing…")
    sec._timer.stop()
    sec._load(text_comp)
    assert sec._field.text() == "typing…"

    bip = scene.place_component("bipole", (6.0, 0.0))
    bsec = BipoleLabelSection()
    bsec.bind(bip, scene)
    bsec._label_field.hasFocus = lambda: True
    bsec._label_field.setText("half")
    bsec._timer.stop()
    bsec._load(bip)
    assert bsec._label_field.text() == "half"


# ---------------------------------------------------------------------------
# Comma-safe bipole t= parsing (regression: t=$f(a,b)$)
# ---------------------------------------------------------------------------

def test_bipole_label_extract_and_replace_survive_math_commas(_app):
    from app.ui.properties import (
        _extract_bipole_label,
        _replace_bipole_label,
        _strip_bipole_label,
    )

    opts = "t=$f(a,b)$, l=$H(s)$"
    assert _extract_bipole_label(opts) == "$f(a,b)$"
    assert _strip_bipole_label(opts) == "l=$H(s)$"
    # Round-trip: replacing with the same label reproduces the string.
    assert _replace_bipole_label(opts, "$f(a,b)$") == "t=$f(a,b)$, l=$H(s)$"
    # Braced commas survive too.
    opts2 = "t={a, b}, color=red"
    assert _extract_bipole_label(opts2) == "{a, b}"
    assert _replace_bipole_label(opts2, "{a, b}") == "t={a, b}, color=red"
    # Removing the label keeps the rest.
    assert _replace_bipole_label(opts, "") == "l=$H(s)$"


def test_bipole_section_round_trips_comma_label(_app):
    """Loading then committing t=$f(a,b)$ through the section leaves it intact
    (the old [^,]+ regex mangled it)."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import BipoleLabelSection

    scene = SchematicScene()
    bip = scene.place_component("bipole", (2.0, 0.0))
    scene.edit_component_options(bip.id, "t=$f(a,b)$, l=$H(s)$")
    live = next(c for c in scene.schematic.components if c.id == bip.id)

    sec = BipoleLabelSection()
    sec.bind(live, scene)
    assert sec._label_field.text() == "$f(a,b)$"
    assert sec._opts_field.text() == "l=$H(s)$"
    sec._commit()                                # commit without changes
    comp = scene.schematic.components[0]
    assert comp.options == "t=$f(a,b)$, l=$H(s)$"


# ---------------------------------------------------------------------------
# Unrepresentable combo values are preserved
# ---------------------------------------------------------------------------

def test_fill_combo_preserves_hand_authored_value(_app):
    """A fill the preset list can't represent (fill=red!40) round-trips through
    an unrelated section commit instead of being overwritten."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import _FILL_OPTIONS, FillBorderSection

    scene = SchematicScene()
    comp = scene.place_component("rect", (2.0, 0.0))
    comp.fill_color = "red!40"                   # hand-authored in the .hv

    sec = FillBorderSection()
    sec.bind(comp, scene)
    assert sec._fill.currentText() == "red!40"   # shown as an extra item

    sec._commit()                                # unrelated commit
    assert scene.schematic.components[0].fill_color == "red!40"

    # Loading a representable value removes the extra item again.
    comp.fill_color = "white"
    sec._load(comp)
    assert sec._fill.currentText() == "White"
    assert sec._fill.count() == len(_FILL_OPTIONS)


def test_wire_line_style_combo_preserves_unknown_value(_app):
    from app.canvas.scene import SchematicScene
    from app.ui.properties import PropertiesPanel

    scene = SchematicScene()
    wire = scene.add_wire([(0.0, 0.0), (3.0, 0.0)])
    scene.schematic.wires[0].line_style = "loosely dashed"   # hand-authored

    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_wire(wire.id)
    sec = panel._wire_section
    assert sec._line_style.currentText() == "loosely dashed"
    sec._commit()
    assert scene.schematic.wires[0].line_style == "loosely dashed"


# ---------------------------------------------------------------------------
# Document properties through the undo stack
# ---------------------------------------------------------------------------

def test_document_panel_change_is_undoable(_app):
    """The Document tab pushes an undoable command; undo restores the styles and
    refresh() reloads the combos."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import DocumentPropertiesPanel

    scene = SchematicScene()
    panel = DocumentPropertiesPanel()
    panel.set_scene(scene)

    panel._voltage.setCurrentIndex(panel._voltage.findData("european"))
    assert scene.schematic.voltage_style == "european"
    assert scene.undo_stack.can_undo()

    scene.undo()
    assert scene.schematic.voltage_style == "american"
    panel.refresh()
    assert panel._voltage.currentData() == "american"

    scene.redo()
    assert scene.schematic.voltage_style == "european"


def test_document_panel_preamble_settings_undoable(_app):
    """The siunitx checkbox and custom-preamble editor push undoable commands;
    undo restores them and refresh() reloads the controls."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import DocumentPropertiesPanel

    scene = SchematicScene()
    panel = DocumentPropertiesPanel()
    panel.set_scene(scene)

    # siunitx defaults on, so toggle it off and confirm undo restores it.
    assert scene.schematic.siunitx is True
    panel._siunitx.setChecked(False)
    assert scene.schematic.siunitx is False
    assert scene.undo_stack.can_undo()
    scene.undo()
    assert scene.schematic.siunitx is True
    panel.refresh()
    assert panel._siunitx.isChecked() is True

    # The preamble editor commits on focus-out (committed signal); drive it
    # directly rather than simulating focus.
    panel._preamble.setPlainText(r"\usepackage{mathtools}")
    panel._preamble.committed.emit()
    assert scene.schematic.preamble == r"\usepackage{mathtools}"
    scene.undo()
    assert scene.schematic.preamble == ""
    panel.refresh()
    assert panel._preamble.toPlainText() == ""
