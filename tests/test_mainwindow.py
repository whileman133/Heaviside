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

from app.ui.mainwindow import MainWindow  # noqa: E402
from app.ui.preferences import Preferences  # noqa: E402


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
