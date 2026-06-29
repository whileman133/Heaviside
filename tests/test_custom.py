"""
Custom components (spec §custom): capture, runtime registration, codegen, and
serialization.

Most tests use a hand-authored :class:`CustomComponentSpec` fixture so they run
without LaTeX (the registry / codegen / io / validate behaviour is independent of
how the spec was captured). The capture pipeline itself (``build_custom``, which
renders via LaTeX) is exercised in the few tests gated on ``latex``/``dvisvgm``.
"""

from __future__ import annotations

import shutil

import pytest

from app.components import custom, registry
from app.components.model import Component, CustomComponentSpec
from app.codegen.circuitikz import generate
from app.schematic.model import Schematic, Wire
from app.schematic.validate import validate

_HAS_TEX = shutil.which("latex") is not None and shutil.which("dvisvgm") is not None
_skip_tex = pytest.mark.skipif(not _HAS_TEX, reason="requires latex and dvisvgm")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _xfmr_spec(name: str = "custom:test xfmr",
               ctikzset: list[str] | None = None,
               extra_options: str = "core") -> CustomComponentSpec:
    """A LaTeX-free custom transformer spec: base ``transformer`` with two named
    terminals and a sub-node midtap anchor, plus a (trivial) geometry payload."""
    return CustomComponentSpec(
        name=name,
        display_name="Test Transformer",
        category=custom.CUSTOM_CATEGORY,
        base_kind="transformer",
        ctikzset=ctikzset if ctikzset is not None else ["transformers/coils/width=1.2"],
        extra_options=extra_options,
        pins=[
            {"name": "A1", "offset": [-1.05, -1.05], "anchor": "A1"},
            {"name": "B1", "offset": [1.05, -1.05], "anchor": "B1"},
            {"name": "L1.midtap", "offset": [-0.42, 0.0], "anchor": "-L1.midtap"},
        ],
        bbox=(-1.05, -1.05, 1.05, 1.05),
        default_span=(0.0, 0.0),
        geometry={"viewBox": "0 0 1 1", "width_pt": "1pt", "height_pt": "1pt",
                  "paths": [{"d": "M0 0L1 1", "stroke_width": 0.4,
                             "fill": "none", "stroke": "#000"}],
                  "glyphs": []},
        ctikz_version="1.6.7",
    )


def _r_spec(name: str = "custom:fat r") -> CustomComponentSpec:
    """A LaTeX-free custom path (resistor) spec with a scoped setting."""
    return CustomComponentSpec(
        name=name, display_name="Fat R", category=custom.CUSTOM_CATEGORY, base_kind="R",
        ctikzset=["resistors/width=0.5"], extra_options="",
        pins=[{"name": "in", "offset": [0.0, 0.0], "anchor": None},
              {"name": "out", "offset": [2.0, 0.0], "anchor": None}],
        bbox=(0.0, -0.25, 2.0, 0.25), default_span=(2.0, 0.0),
        geometry={"viewBox": "0 0 1 1", "paths": [], "glyphs": []},
        ctikz_version="1.6.7",
    )


@pytest.fixture(autouse=True)
def _clean_runtime():
    """Each test starts and ends with no runtime custom components."""
    registry.reset_runtime_components()
    yield
    registry.reset_runtime_components()


# ---------------------------------------------------------------------------
# spec → ComponentDef
# ---------------------------------------------------------------------------

def test_spec_to_component_def_inherits_base():
    spec = _xfmr_spec()
    defn = custom.spec_to_component_def(spec)
    assert defn.kind == spec.name
    assert defn.base_kind == "transformer"
    assert defn.tikz_keyword == "transformer"          # base's keyword
    assert defn.extra_options == "core"
    assert tuple(defn.ctikzset) == ("transformers/coils/width=1.2",)
    assert len(defn.pins) == 3
    assert defn.geometry is not None
    assert defn.resizable is False                      # Phase 1: fixed geometry


def test_is_custom_kind():
    assert custom.is_custom_kind("custom:foo")
    assert not custom.is_custom_kind("transformer")
    assert custom.make_kind("My  Iron Core").startswith("custom:")
    assert custom.make_kind("My  Iron Core") == "custom:my iron core"


# ---------------------------------------------------------------------------
# Runtime registry
# ---------------------------------------------------------------------------

def test_register_and_reset_round_trip():
    spec = _xfmr_spec()
    assert spec.name not in registry.REGISTRY
    registry.register_runtime_component(custom.spec_to_component_def(spec))
    assert spec.name in registry.REGISTRY
    assert spec.name in registry.runtime_component_kinds()
    registry.reset_runtime_components()
    assert spec.name not in registry.REGISTRY
    assert registry.runtime_component_kinds() == frozenset()


def test_sync_scrubs_previous_document():
    a = _xfmr_spec("custom:doc a")
    b = _r_spec("custom:doc b")
    registry.sync_runtime_components(
        Schematic(version="0.11", name="A", custom_components={a.name: a}))
    assert a.name in registry.REGISTRY and b.name not in registry.REGISTRY
    # Switching documents scrubs A's customs and registers B's.
    registry.sync_runtime_components(
        Schematic(version="0.11", name="B", custom_components={b.name: b}))
    assert b.name in registry.REGISTRY
    assert a.name not in registry.REGISTRY            # no cross-document leak


def test_sync_empty_document_clears_all():
    a = _xfmr_spec()
    registry.register_runtime_component(custom.spec_to_component_def(a))
    registry.sync_runtime_components(Schematic(version="0.11", name="empty"))
    assert registry.runtime_component_kinds() == frozenset()


def test_geometry_injected_into_canvas_store():
    pytest.importorskip("PySide6")
    from app.canvas import svgsym
    spec = _xfmr_spec()
    registry.register_runtime_component(custom.spec_to_component_def(spec))
    paths = svgsym.symbol_paths(spec.name)
    assert len(paths) == 1                              # the one path in the fixture
    registry.reset_runtime_components()
    assert svgsym.symbol_paths(spec.name) == ()        # cleared on reset


# ---------------------------------------------------------------------------
# Codegen — base delegation, scoped ctikzset, extra options, anchor refs
# ---------------------------------------------------------------------------

def test_codegen_node_scopes_ctikzset_and_appends_options():
    spec = _xfmr_spec()
    registry.register_runtime_component(custom.spec_to_component_def(spec))
    c = Component(id="aaaa1111", kind=spec.name, position=(2.0, 2.0),
                  rotation=0, options="")
    src = generate(Schematic(version="0.11", name="t", components=[c],
                             custom_components={spec.name: spec}), y_flip=True)
    assert r"\ctikzset{transformers/coils/width=1.2}" in src
    assert "node[transformer, core]" in src            # base keyword + extra options
    # The scoped settings are inside a group (so they revert).
    assert "{\n" in src and "}" in src


def test_codegen_node_emits_base_delegated_subnode_anchor():
    spec = _xfmr_spec(ctikzset=[])                      # no group -> emitted in main draw
    registry.register_runtime_component(custom.spec_to_component_def(spec))
    from app.codegen.circuitikz import component_pin_positions
    c = Component(id="cccc3333", kind=spec.name, position=(3.0, 3.0),
                  rotation=0, options="")
    defn = registry.REGISTRY[c.kind]
    pos = component_pin_positions(c)
    tap = next(i for i, p in enumerate(defn.pins) if p.name == "L1.midtap")
    w = Wire(id="wwww4444", points=[pos[tap], (pos[tap][0], pos[tap][1] - 1.0)])
    src = generate(Schematic(version="0.11", name="t", components=[c], wires=[w],
                             custom_components={spec.name: spec}), y_flip=True)
    assert "-L1.midtap)" in src                         # (node_id-L1.midtap) sub-node ref


def test_codegen_path_custom_scopes_ctikzset():
    spec = _r_spec()
    registry.register_runtime_component(custom.spec_to_component_def(spec))
    c = Component(id="bbbb2222", kind=spec.name, position=(0.0, 0.0),
                  rotation=0, options="l=$R_1$")
    src = generate(Schematic(version="0.11", name="t", components=[c],
                             custom_components={spec.name: spec}), y_flip=True)
    assert r"\ctikzset{resistors/width=0.5}" in src
    assert "to[R, l=$R_1$]" in src                      # base keyword + user label


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_accepts_custom_kind_declared_on_document():
    spec = _xfmr_spec()
    c = Component(id="aaaa1111", kind=spec.name, position=(0.0, 0.0),
                  rotation=0, options="")
    sch = Schematic(version="0.11", name="t", components=[c],
                    custom_components={spec.name: spec})
    assert validate(sch) == []                          # no "unknown kind" error


def test_validate_rejects_undeclared_custom_kind():
    c = Component(id="aaaa1111", kind="custom:nope", position=(0.0, 0.0),
                  rotation=0, options="")
    sch = Schematic(version="0.11", name="t", components=[c])
    assert any("unknown kind" in e for e in validate(sch))


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------

def test_io_round_trip(tmp_path):
    from app.schematic import io
    spec = _xfmr_spec()
    c = Component(id="aaaa1111", kind=spec.name, position=(2.0, 2.0),
                  rotation=90, options="")
    sch = Schematic(version="0.11", name="rt", components=[c],
                    custom_components={spec.name: spec})
    p = tmp_path / "t.hv"
    io.save(sch, p)
    loaded = io.load(p)
    assert loaded.version == "0.11"
    ls = loaded.custom_components[spec.name]
    assert ls.base_kind == "transformer"
    assert ls.ctikzset == spec.ctikzset
    assert ls.extra_options == "core"
    assert len(ls.pins) == len(spec.pins)
    assert ls.geometry == spec.geometry
    assert tuple(ls.bbox) == spec.bbox


def test_io_skips_malformed_custom_entry(tmp_path):
    import json
    from app.schematic import io
    spec = _xfmr_spec()
    sch = Schematic(version="0.11", name="rt",
                    custom_components={spec.name: spec})
    p = tmp_path / "t.hv"
    io.save(sch, p)
    raw = json.loads(p.read_text())
    raw["config"]["custom_components"]["bad"] = {"name": "bad"}  # no base_kind/geometry
    p.write_text(json.dumps(raw))
    loaded = io.load(p)
    assert "bad" not in loaded.custom_components
    assert spec.name in loaded.custom_components            # the good one survives


# ---------------------------------------------------------------------------
# Capture pipeline (requires LaTeX)
# ---------------------------------------------------------------------------

@_skip_tex
def test_build_custom_captures_geometry_and_anchors():
    spec = custom.build_custom(
        name="custom:iron xfmr", display_name="Iron Transformer", category="Custom",
        base_kind="transformer", ctikzset=["transformers/coils/width=1.2"],
        extra_options="core")
    assert spec.base_kind == "transformer"
    assert spec.geometry["paths"]                          # captured drawable geometry
    assert spec.ctikz_version                              # stamped version
    # Pins carry the base's anchor names; the midtap is a sub-node anchor.
    names = {p["name"] for p in spec.pins}
    assert {"A1", "B1"} <= names
    assert any(p.get("anchor", "").startswith("-") for p in spec.pins if p.get("anchor"))


@_skip_tex
def test_build_custom_anchor_matches_direct_measurement():
    from app.components import render
    ctikz = ["transformers/coils/width=1.2"]
    spec = custom.build_custom("custom:x", "X", "Custom", "transformer", ctikz, "core")
    direct = render.measure_anchors("transformer, core", ["A1"], ctikzset=ctikz)
    a1 = next(p for p in spec.pins if p["name"] == "A1")
    assert a1["offset"][0] == pytest.approx(direct["A1"][0], abs=1e-3)
    assert a1["offset"][1] == pytest.approx(direct["A1"][1], abs=1e-3)


def test_color_and_dash_helpers():
    """A default-ink stroke falls back to the theme colour; an explicit colour is
    honoured. Dash lengths are expressed as multiples of the pen width."""
    pytest.importorskip("PySide6")
    from app.canvas import svgsym

    assert svgsym.effective_color("#000", "#abcdef") == "#abcdef"
    assert svgsym.effective_color("black", "#abcdef") == "#abcdef"
    assert svgsym.effective_color("", "#abcdef") == "#abcdef"
    assert svgsym.effective_color("#f00", "#abcdef") == "#f00"     # explicit colour kept
    assert svgsym.dash_for_pen((), 2.0, 3.0) == []
    assert svgsym.dash_for_pen((6.0,), 2.0, 0.0) == []            # no pen, no dash
    assert svgsym.dash_for_pen((6.0, 3.0), 2.0, 4.0) == [3.0, 1.5]  # ×scale ÷pen_width


@_skip_tex
def test_build_custom_captures_color_and_dash():
    """A custom component with ``color=`` / ``dash=`` captures a non-black stroke and a
    dash array in its geometry (so the canvas can reproduce them)."""
    spec = custom.build_custom(
        "custom:cd", "CD", "Custom", "transformer core",
        ["transformer core/.cd, color=red, dash={{4pt}{2pt}}"], "")
    paths = spec.geometry["paths"]
    assert any(p.get("stroke", "#000").lower() not in ("#000", "") for p in paths), \
        "expected a non-black stroke captured"
    assert any(p.get("dash") for p in paths), "expected a dash array captured"


@_skip_tex
def test_build_custom_path_base():
    spec = custom.build_custom("custom:big r", "Big R", "Custom", "R", [], "")
    assert spec.base_kind == "R"
    assert spec.default_span == (2.0, 0.0)
    assert [p["name"] for p in spec.pins] == ["in", "out"]


# ---------------------------------------------------------------------------
# Creator dialog (requires Qt + LaTeX)
# ---------------------------------------------------------------------------

def test_dialog_base_picker_is_searchable():
    """The base picker is an editable, type-to-filter combo: a partial search yields
    no valid base, an exact item text resolves to its kind. (No LaTeX needed.)"""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.custom_component_dialog import CustomComponentDialog

    QApplication.instance() or QApplication([])
    dlg = CustomComponentDialog()
    assert dlg._base.isEditable()
    assert dlg._base.count() > 50          # the full base set is large (searchable)
    dlg._base.setEditText("transf")        # partial — not an exact item
    assert dlg._base_kind() == ""
    idx = dlg._base.findData("transformer")
    dlg._base.setCurrentIndex(idx)         # an exact item is selected
    assert dlg._base_kind() == "transformer"
    dlg.deleteLater()


def test_paste_carries_custom_components_across_documents():
    """Copying a placed custom component and pasting into another document brings its
    definition along (registered + added to the target), so the instance resolves."""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.canvas.scene import SchematicScene

    QApplication.instance() or QApplication([])
    spec = _xfmr_spec()
    c = Component(id="aaaa1111", kind=spec.name, position=(2.0, 2.0),
                 rotation=0, options="")
    doc_a = Schematic(version="0.11", name="A", components=[c],
                      custom_components={spec.name: spec})
    doc_b = Schematic(version="0.11", name="B")

    scene = SchematicScene()
    scene.set_schematic(doc_a)
    scene._comp_items[c.id].setSelected(True)
    scene.copy_selection()
    assert spec.name in scene._clipboard_custom_specs   # definition rides the clipboard

    scene.set_schematic(doc_b)                          # switch document (scrubs A's custom)
    assert spec.name not in doc_b.custom_components
    assert spec.name not in registry.REGISTRY
    scene.paste(at=(0.0, 0.0))
    assert spec.name in doc_b.custom_components          # imported into the target
    assert spec.name in registry.REGISTRY               # and registered at runtime
    assert any(comp.kind == spec.name for comp in doc_b.components)
    registry.reset_runtime_components()


def test_preview_anchor_hover_hit_test():
    """The preview reveals an anchor's name only when the cursor is near its dot."""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QPointF
    from PySide6.QtWidgets import QApplication
    from app.ui.custom_component_dialog import _ComponentPreview

    QApplication.instance() or QApplication([])
    pv = _ComponentPreview(dark=False)
    pv._anchor_pts = [(QPointF(50.0, 50.0), "A1"), (QPointF(120.0, 80.0), "B1")]
    assert pv._anchor_at(QPointF(52.0, 49.0)) == 0     # near the first dot
    assert pv._anchor_at(QPointF(119.0, 81.0)) == 1    # near the second
    assert pv._anchor_at(QPointF(300.0, 300.0)) == -1  # far from any
    pv.deleteLater()


def test_palette_always_has_custom_category():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.palette import CUSTOM_CATEGORY, ComponentPalette

    QApplication.instance() or QApplication([])
    p = ComponentPalette()
    assert CUSTOM_CATEGORY in p._by_cat          # shown even with no customs
    p.deleteLater()


def test_palette_lists_registered_custom_after_refresh():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.palette import CUSTOM_CATEGORY, ComponentPalette

    QApplication.instance() or QApplication([])
    spec = _xfmr_spec()
    registry.register_runtime_component(custom.spec_to_component_def(spec))
    p = ComponentPalette()
    p.refresh_registry()
    assert spec.name in p._by_cat[CUSTOM_CATEGORY]
    p.deleteLater()


@_skip_tex
def test_dialog_render_then_accept_builds_spec():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from app.ui.custom_component_dialog import CustomComponentDialog

    app = QApplication.instance() or QApplication([])
    dlg = CustomComponentDialog()
    dlg._name.setText("Iron Transformer")
    idx = dlg._base.findData("transformer")
    assert idx >= 0
    dlg._base.setCurrentIndex(idx)
    dlg._options.setText("core")
    dlg._ctikzset.setPlainText("transformers/coils/width=1.2")
    # OK is disabled until an explicit render succeeds (no capture lag on accept).
    from PySide6.QtWidgets import QDialogButtonBox
    assert not dlg._buttons.button(QDialogButtonBox.Ok).isEnabled()
    dlg._render()
    dlg._build_thread.wait(30000)      # background capture
    app.processEvents()                # deliver the done signal
    assert dlg._buttons.button(QDialogButtonBox.Ok).isEnabled()
    dlg._on_accept()
    spec = dlg.result_spec()
    assert spec is not None
    assert spec.name == "custom:iron transformer"
    assert spec.base_kind == "transformer"
    assert spec.extra_options == "core"
    dlg.deleteLater()


@_skip_tex
def test_dialog_edit_mode_keeps_kind():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    from app.ui.custom_component_dialog import CustomComponentDialog

    app = QApplication.instance() or QApplication([])
    original = custom.build_custom("custom:my xfmr", "My Xfmr", "Custom",
                                   "transformer", [], "")
    dlg = CustomComponentDialog(editing=original)
    assert dlg._name.text() == "My Xfmr"          # pre-filled
    dlg._options.setText("core")                  # change the customisation
    dlg._render()
    dlg._build_thread.wait(30000)
    app.processEvents()
    dlg._on_accept()
    spec = dlg.result_spec()
    assert spec is not None
    assert spec.name == "custom:my xfmr"          # kind preserved across edit
    assert spec.extra_options == "core"
    dlg.deleteLater()
