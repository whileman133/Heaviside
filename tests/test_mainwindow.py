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


def test_document_settings_dialog_writes_styles():
    """Accepting Document Settings writes the chosen styles onto the schematic."""
    from app.schematic.model import Schematic
    from app.ui.documentsettings import DocumentSettingsDialog

    s = Schematic(version="0.2", name="t")
    dlg = DocumentSettingsDialog(s)
    dlg._voltage.setCurrentIndex(dlg._voltage.findData("european"))
    dlg._on_accept()
    assert s.voltage_style == "european"
    assert s.current_style == "american"
    assert dlg.changed() is True

    # Accepting with no change reports changed() == False (caller skips recompile).
    dlg2 = DocumentSettingsDialog(s)
    dlg2._on_accept()
    assert dlg2.changed() is False


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


def test_copy_pdf_puts_pdf_mime_on_clipboard(tmp_path, monkeypatch):
    """Copy-as-PDF exposes the figure under both the macOS PDF pasteboard UTI
    (com.adobe.pdf — so Office/iWork paste it) and the application/pdf MIME type,
    plus a raster fallback for apps without vector paste."""
    from PySide6.QtGui import QImage

    win = _win(tmp_path)
    monkeypatch.setattr(win, "_compile_to_pdf", lambda *a, **k: b"%PDF-1.4 body")
    monkeypatch.setattr(mw, "pdf_to_qimage", lambda *a, **k: QImage(8, 8, QImage.Format_RGB32))
    fake = _FakeClipboard()
    monkeypatch.setattr(mw.QGuiApplication, "clipboard", staticmethod(lambda: fake))

    win._on_copy_pdf()
    assert fake.mime is not None
    assert fake.mime.hasFormat("com.adobe.pdf")       # macOS UTI (the fix)
    assert fake.mime.hasFormat("application/pdf")
    assert bytes(fake.mime.data("application/pdf")).startswith(b"%PDF")
    assert fake.mime.hasImage()                       # raster fallback


def test_preview_panel_buttons_emit_copy_signals():
    """The preview panel's Copy PNG/PDF/SVG buttons emit the copy-request signals."""
    from PySide6.QtWidgets import QPushButton

    panel = mw._PreviewPanel()
    got = []
    panel.copy_png_requested.connect(lambda: got.append("png"))
    panel.copy_pdf_requested.connect(lambda: got.append("pdf"))
    panel.copy_svg_requested.connect(lambda: got.append("svg"))
    for btn in panel.findChildren(QPushButton):
        btn.click()
    assert set(got) == {"png", "pdf", "svg"}


def test_copy_svg_puts_svg_mime_on_clipboard(tmp_path, monkeypatch):
    """Copy-as-SVG exposes the SVG under the macOS UTI (public.svg-image) and the
    image/svg+xml MIME type, plus a raster fallback — and crucially NOT as
    text/plain, so Word/PowerPoint paste an image instead of the raw XML markup
    (regression)."""
    from PySide6.QtGui import QImage

    win = _win(tmp_path)
    monkeypatch.setattr(win, "_compile_to_pdf", lambda *a, **k: b"%PDF-1.4")
    monkeypatch.setattr(mw, "pdf_to_svg", lambda *a, **k: b"<svg xmlns='...'/>")
    monkeypatch.setattr(mw, "pdf_to_qimage", lambda *a, **k: QImage(8, 8, QImage.Format_RGB32))
    fake = _FakeClipboard()
    monkeypatch.setattr(mw.QGuiApplication, "clipboard", staticmethod(lambda: fake))

    win._on_copy_svg()
    assert fake.mime is not None
    assert fake.mime.hasFormat("image/svg+xml")
    assert fake.mime.hasFormat("public.svg-image")    # macOS UTI (the fix)
    assert bytes(fake.mime.data("image/svg+xml")).startswith(b"<svg")
    assert fake.mime.hasImage()                       # raster fallback for Office
    assert not fake.mime.hasText()                    # the XML-paste bug is gone


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
