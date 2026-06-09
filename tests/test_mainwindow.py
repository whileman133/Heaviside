"""
MainWindow auto-export integration tests (spec §10.8 / §8.6).

Exercises the real ``MainWindow._auto_export`` against an isolated Preferences
store (a temp INI — never the user's settings) and an offscreen Qt platform.
The focus is the TeX-snippet branch, which must run **without** ``pdflatex``.

Run headless:  QT_QPA_PLATFORM=offscreen pytest
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _app = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - depends on host GL/EGL libs
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

import app.ui.mainwindow as mw  # noqa: E402
from app.ui.mainwindow import MainWindow  # noqa: E402
from app.ui.preferences import Preferences  # noqa: E402


@pytest.fixture(autouse=True)
def _no_dependency_modal(monkeypatch):
    """Stop MainWindow's startup dependency check from popping a *modal*
    QMessageBox when a tool (e.g. pdflatex) is absent — on a headless CI runner
    that dialog blocks forever and hangs the whole suite. Tests don't assert on
    it, so neutralise the check for every MainWindow built here.
    """
    monkeypatch.setattr(mw, "check_dependencies", lambda: [])


def _win(tmp_path):
    """A MainWindow whose Preferences are backed by an isolated temp INI."""
    win = MainWindow()
    win._prefs = Preferences(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    return win


def test_auto_export_tex_needs_no_latex(tmp_path, monkeypatch):
    """TeX auto-export writes the snippet without ever invoking the compiler."""
    win = _win(tmp_path)
    win._prefs.auto_export_tex = True

    # If the TeX branch ever reaches the image-compile path, fail loudly: the
    # whole point is that a .tex snippet needs no pdflatex.
    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("_compile_to_pdf must not be called for TeX-only export")

    monkeypatch.setattr(win, "_compile_to_pdf", _boom)

    out = tmp_path / "demo.hv"
    win._auto_export(out)

    tex = out.with_suffix(".tex")
    assert tex.exists()
    assert "Heaviside" in tex.read_text(encoding="utf-8")
    assert not out.with_suffix(".pdf").exists()


def test_auto_export_disabled_writes_nothing(tmp_path):
    """With every auto-export preference off, no sibling files are written."""
    win = _win(tmp_path)  # fresh store → all defaults off
    out = tmp_path / "demo.hv"
    win._auto_export(out)
    assert not any(out.with_suffix(s).exists() for s in (".tex", ".pdf", ".eps", ".svg"))


def test_document_panel_writes_styles_live():
    """The Document inspector tab writes the chosen styles onto the schematic live
    and signals the change (it replaced the modal Document Settings dialog)."""
    from app.canvas.scene import SchematicScene
    from app.ui.properties import DocumentPropertiesPanel

    scene = SchematicScene()
    panel = DocumentPropertiesPanel()
    panel.set_scene(scene)               # combos load from the (american) document
    assert panel._voltage.currentData() == "american"

    changed = []
    panel.document_changed.connect(lambda: changed.append(True))
    panel._voltage.setCurrentIndex(panel._voltage.findData("european"))

    assert scene.schematic.voltage_style == "european"
    assert scene.schematic.current_style == "american"
    assert changed == [True]

    # refresh() reloads the combos from the document (e.g. after Open) silently.
    scene.schematic.current_style = "european"
    panel.refresh()
    assert panel._current.currentData() == "european"
    assert changed == [True]   # refresh does not re-emit


def test_inspector_tabs_switch_with_selection(tmp_path):
    """The inspector surfaces the Document tab when nothing is selected and the
    Properties tab when something is (the Document tab replaced the old dialog)."""
    win = _win(tmp_path)
    win._preview_worker.request_compile = lambda *a, **k: None  # no real LaTeX
    try:
        labels = [win._inspector_tabs.tabText(i)
                  for i in range(win._inspector_tabs.count())]
        assert labels == ["Properties", "Document"]

        win._on_selection_changed([])                       # nothing selected
        assert win._inspector_tabs.currentWidget() is win._doc_props

        comp = win._scene.place_component("R", (2.0, 2.0))   # select something
        win._on_selection_changed([comp.id])
        assert win._inspector_tabs.currentWidget() is win._props

        win._on_selection_changed([])                        # deselect → Document
        assert win._inspector_tabs.currentWidget() is win._doc_props
    finally:
        win._modified = False   # avoid the unsaved-changes prompt on close
        win.close()


def test_document_tab_edit_relayouts_and_marks_modified(tmp_path):
    """Editing a style in the Document tab re-lays out annotations and refreshes
    via schematic_changed (the live replacement for Document Settings…)."""
    win = _win(tmp_path)
    win._preview_worker.request_compile = lambda *a, **k: None  # no real LaTeX
    try:
        fired = []
        win._scene.schematic_changed.connect(lambda: fired.append(True))
        win._doc_props._voltage.setCurrentIndex(
            win._doc_props._voltage.findData("european")
        )
        assert win._scene.schematic.voltage_style == "european"
        assert fired  # schematic_changed emitted → source/preview refresh + modified
    finally:
        win._modified = False   # avoid the unsaved-changes prompt on close
        win.close()


class _FakeClipboard:
    """Records what the handler sets, so tests never touch the real platform
    clipboard — which segfaults under the offscreen QPA plugin on CI."""

    def __init__(self):
        self.image = None
        self.mime = None

    def setImage(self, image):  # noqa: N802
        self.image = image

    def setMimeData(self, mime):  # noqa: N802
        self.mime = mime


def test_copy_png_puts_image_on_clipboard(tmp_path, monkeypatch):
    """Copy-as-PNG renders the compiled figure to a QImage and sets the clipboard
    image (compile/render and the clipboard itself stubbed — no LaTeX, no real
    clipboard)."""
    from PySide6.QtGui import QImage

    win = _win(tmp_path)
    monkeypatch.setattr(win, "_compile_to_pdf", lambda *a, **k: b"%PDF-1.4")
    img = QImage(8, 8, QImage.Format_ARGB32)
    img.fill(0xFF112233)
    monkeypatch.setattr(mw, "pdf_to_qimage", lambda *a, **k: img)
    fake = _FakeClipboard()
    monkeypatch.setattr(mw.QGuiApplication, "clipboard", staticmethod(lambda: fake))

    win._on_copy_png()
    assert fake.image is img and not fake.image.isNull()


def test_preview_panel_has_only_copy_png_button():
    """The preview header offers a single Copy PNG icon button (PDF/SVG clipboard
    copy was dropped — they paste as raster anyway; vector stays in Export)."""
    from PySide6.QtWidgets import QToolButton

    panel = mw._PreviewPanel()
    assert not hasattr(panel, "copy_pdf_requested")
    assert not hasattr(panel, "copy_svg_requested")
    got = []
    panel.copy_png_requested.connect(lambda: got.append("png"))
    buttons = panel.findChildren(QToolButton)
    assert len(buttons) == 1   # icon-only Copy PNG in the header
    buttons[0].click()
    assert got == ["png"]


def test_copy_png_uses_dpi_preference(tmp_path, monkeypatch):
    """Copy PNG renders at the configured PNG DPI preference (default 300)."""
    from PySide6.QtGui import QImage

    win = _win(tmp_path)
    win._prefs.png_dpi = 600
    monkeypatch.setattr(win, "_compile_to_pdf", lambda *a, **k: b"%PDF-1.4")
    seen = {}
    img = QImage(8, 8, QImage.Format_RGB32)

    def fake_qimage(pdf, dpi=150):
        seen["dpi"] = dpi
        return img

    monkeypatch.setattr(mw, "pdf_to_qimage", fake_qimage)
    fake = _FakeClipboard()
    monkeypatch.setattr(mw.QGuiApplication, "clipboard", staticmethod(lambda: fake))

    win._on_copy_png()
    assert seen["dpi"] == 600
    assert fake.image is img


def test_retypeset_labels_runs_on_populated_scene(tmp_path):
    """Scene-wide re-typeset (used when the math engine changes) is safe and
    covers labelled components without error."""
    from app.preview import mathrender

    win = _win(tmp_path)
    r = win._scene.place_component("R", (2.0, 0.0))
    win._scene.edit_component_options(r.id, "l=$R_1$, v=$V_s$")

    mathrender.set_force_ziamath(True)
    try:
        win._scene.retypeset_labels()  # must not raise
    finally:
        mathrender.set_force_ziamath(False)


def test_apply_theme_switches_both_palettes(tmp_path):
    """MainWindow._apply_theme swaps the canvas + chrome palettes together and
    re-applies the toolbar stylesheet, so a dark appearance themes everything."""
    from app.canvas import style
    from app.ui import theme

    win = _win(tmp_path)
    try:
        win._dark = True
        win._apply_theme()
        assert style.is_dark()
        assert theme.is_dark()
        assert theme._DARK["SURFACE"] in win._toolbar.styleSheet()

        win._dark = False
        win._apply_theme()
        assert not style.is_dark()
        assert not theme.is_dark()
        assert theme._LIGHT["SURFACE"] in win._toolbar.styleSheet()
    finally:
        # Never leak the dark palette into other tests.
        style.set_dark(False)
        theme.set_dark(False)


def test_toolbar_dark_toggle(tmp_path):
    """The toolbar light/dark toggle flips the theme and pins it: once toggled it
    stops following the OS appearance (a one-way opt-out within the session)."""
    from app.canvas import style
    from app.ui import theme

    win = _win(tmp_path)
    try:
        win._dark = False
        win._follow_system = True
        win._sync_dark_action()

        win._act_dark.setChecked(True)  # user flips it on
        assert win._dark is True
        assert style.is_dark() and theme.is_dark()
        assert win._follow_system is False  # no longer tracks the OS

        # An OS change is now ignored (manual choice wins).
        win._on_color_scheme_changed()
        assert win._dark is True

        # The palette search box and canvas scrollbars are themed explicitly (so
        # they follow the toolbar toggle even when the OS is in light mode).
        assert theme._DARK["SURFACE_ALT"] in win._palette._search.styleSheet()
        assert "QScrollBar" in win._view.styleSheet()

        win._act_dark.setChecked(False)  # flip back to light
        assert win._dark is False
        assert not style.is_dark() and not theme.is_dark()
        assert theme._LIGHT["SURFACE_ALT"] in win._palette._search.styleSheet()
    finally:
        style.set_dark(False)
        theme.set_dark(False)


def test_palette_keyboard_shortcuts(tmp_path, monkeypatch):
    """Letters select a category and digits place the Nth component; modifier
    chords are ignored so they don't shadow menu accelerators (§10.2)."""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    win = _win(tmp_path)
    started = []
    monkeypatch.setattr(win._scene, "start_placement", lambda k: started.append(k))

    def press(text, key, mods=Qt.NoModifier):
        return win._handle_palette_shortcut(QKeyEvent(QEvent.KeyPress, key, mods, text))

    assert press("c", Qt.Key_C)                      # → Capacitors
    assert win._palette._active_cat == "Capacitors"
    assert press("1", Qt.Key_1)                      # place first capacitor
    assert started == [win._palette._by_cat["Capacitors"][0]]
    # A Ctrl-chord (e.g. Ctrl+C) must NOT be hijacked as the category letter.
    assert not press("c", Qt.Key_C, Qt.ControlModifier)


def test_export_png_renders_at_dpi(tmp_path, monkeypatch):
    """Export PNG renders the figure at the PNG DPI preference and writes a file."""
    from PySide6.QtGui import QImage

    win = _win(tmp_path)
    win._prefs.png_dpi = 200
    out = tmp_path / "fig.png"
    monkeypatch.setattr(win, "_compile_to_pdf", lambda *a, **k: b"%PDF-1.4")
    seen = {}
    img = QImage(8, 8, QImage.Format_RGB32); img.fill(0xFFFFFFFF)

    def fake_qimage(pdf, dpi=150):
        seen["dpi"] = dpi
        return img

    monkeypatch.setattr(mw, "pdf_to_qimage", fake_qimage)
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(out), "")))
    win._on_export_png()
    assert seen["dpi"] == 200
    assert out.exists() and out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_auto_export_png_writes_file(tmp_path, monkeypatch):
    """With auto-export PNG on, saving writes a sibling <name>.png."""
    from PySide6.QtGui import QImage

    win = _win(tmp_path)
    win._prefs.auto_export_png = True
    img = QImage(8, 8, QImage.Format_RGB32); img.fill(0xFFFFFFFF)
    monkeypatch.setattr(win, "_compile_to_pdf", lambda *a, **k: b"%PDF-1.4")
    monkeypatch.setattr(mw, "pdf_to_qimage", lambda *a, **k: img)

    out = tmp_path / "demo.hv"
    win._auto_export(out)
    assert out.with_suffix(".png").exists()
