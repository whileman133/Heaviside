"""
Tests for app/ui/preferences.py — the Preferences wrapper and dialog.

Uses an isolated ``QSettings`` backed by a temp INI file so the test never
touches (or depends on) the real user settings store.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.ui.preferences import Preferences, PreferencesDialog, _to_bool  # noqa: E402


@pytest.fixture
def prefs(tmp_path) -> Preferences:
    ini = tmp_path / "settings.ini"
    return Preferences(QSettings(str(ini), QSettings.IniFormat))


def test_export_defaults(prefs: Preferences) -> None:
    """A fresh store reports PDF/PNG auto-export on — the two formats needing
    only pdflatex — and TeX/EPS/SVG off (opt-in)."""
    assert prefs.auto_export_pdf is True
    assert prefs.auto_export_png is True
    assert prefs.auto_export_tex is False
    assert prefs.auto_export_eps is False
    assert prefs.auto_export_svg is False
    assert prefs.force_ziamath is False  # auto engine selection by default


def test_roundtrip_tex(prefs: Preferences) -> None:
    assert prefs.auto_export_tex is False  # defaults off (PDF/PNG are the defaults)
    prefs.auto_export_tex = True
    assert prefs.auto_export_tex is True
    prefs.auto_export_tex = False
    assert prefs.auto_export_tex is False


def test_tool_paths_default_empty(prefs: Preferences) -> None:
    """No tool paths configured by default; the dict covers every tool."""
    from app.preview import tools

    assert set(prefs.tool_paths) == set(tools.TOOLS)
    assert all(v == "" for v in prefs.tool_paths.values())


def test_tool_path_roundtrip(prefs: Preferences) -> None:
    prefs.set_tool_path("pdflatex", "/opt/tex/pdflatex")
    assert prefs.tool_path("pdflatex") == "/opt/tex/pdflatex"
    assert prefs.tool_paths["pdflatex"] == "/opt/tex/pdflatex"
    prefs.set_tool_path("pdflatex", "  /opt/tex/bin/pdflatex  ")  # trimmed
    assert prefs.tool_path("pdflatex") == "/opt/tex/bin/pdflatex"
    prefs.set_tool_path("pdflatex", "")
    assert prefs.tool_path("pdflatex") == ""


def test_roundtrip_force_ziamath(prefs: Preferences) -> None:
    assert prefs.force_ziamath is False
    prefs.force_ziamath = True
    assert prefs.force_ziamath is True
    prefs.force_ziamath = False
    assert prefs.force_ziamath is False


def test_roundtrip_pdf(prefs: Preferences) -> None:
    prefs.auto_export_pdf = True
    assert prefs.auto_export_pdf is True
    prefs.auto_export_pdf = False
    assert prefs.auto_export_pdf is False


def test_roundtrip_eps(prefs: Preferences) -> None:
    prefs.auto_export_eps = True
    assert prefs.auto_export_eps is True


def test_roundtrip_svg(prefs: Preferences) -> None:
    assert prefs.auto_export_svg is False  # defaults off (converter-dependent)
    prefs.auto_export_svg = True
    assert prefs.auto_export_svg is True
    prefs.auto_export_svg = False
    assert prefs.auto_export_svg is False


def test_persists_across_instances(tmp_path) -> None:
    """Values survive a new Preferences/QSettings over the same backing file."""
    ini = str(tmp_path / "settings.ini")
    Preferences(QSettings(ini, QSettings.IniFormat)).auto_export_eps = True
    reloaded = Preferences(QSettings(ini, QSettings.IniFormat))
    assert reloaded.auto_export_eps is True


def test_mark_unconnected_pins_default_and_roundtrip(prefs: Preferences) -> None:
    """The display preference defaults off and round-trips."""
    assert prefs.mark_unconnected_pins is False
    prefs.mark_unconnected_pins = True
    assert prefs.mark_unconnected_pins is True


def test_line_hops_default_on_and_roundtrip(prefs: Preferences) -> None:
    """Line-hops default ON (drawing convention) and round-trip."""
    assert prefs.line_hops is True
    prefs.line_hops = False
    assert prefs.line_hops is False
    prefs.line_hops = True
    assert prefs.line_hops is True


def test_check_updates_default_on_and_roundtrip(prefs: Preferences) -> None:
    """The update check defaults ON and round-trips."""
    assert prefs.check_updates_on_startup is True
    prefs.check_updates_on_startup = False
    assert prefs.check_updates_on_startup is False


def test_skipped_update_version_roundtrip(prefs: Preferences) -> None:
    assert prefs.skipped_update_version == ""
    prefs.skipped_update_version = "0.9.0"
    assert prefs.skipped_update_version == "0.9.0"


def test_to_bool_coerces_strings() -> None:
    """QSettings may return booleans as strings; _to_bool normalizes them."""
    assert _to_bool("true") is True
    assert _to_bool("false") is False
    assert _to_bool("1") is True
    assert _to_bool(None, default=True) is True
    assert _to_bool(True) is True


def test_dialog_accept_writes_values(prefs: Preferences) -> None:
    """Accepting the dialog persists the checkbox state to Preferences."""
    dlg = PreferencesDialog(prefs)
    dlg._chk_tex.setChecked(True)
    dlg._chk_pdf.setChecked(True)
    dlg._chk_eps.setChecked(True)
    dlg._chk_svg.setChecked(True)
    dlg._chk_open_pins.setChecked(True)
    dlg._chk_line_hops.setChecked(False)
    dlg._chk_force_ziamath.setChecked(True)
    dlg._tool_edits["pdflatex"].setText("/opt/tex/pdflatex")
    dlg._on_accept()
    assert prefs.auto_export_tex is True
    assert prefs.auto_export_pdf is True
    assert prefs.auto_export_eps is True
    assert prefs.auto_export_svg is True
    assert prefs.force_ziamath is True
    assert prefs.tool_path("pdflatex") == "/opt/tex/pdflatex"
    assert prefs.mark_unconnected_pins is True
    assert prefs.line_hops is False


def test_dialog_cancel_discards(prefs: Preferences) -> None:
    """Closing the dialog without accepting leaves Preferences untouched."""
    prefs.auto_export_pdf = False
    dlg = PreferencesDialog(prefs)
    dlg._chk_pdf.setChecked(True)
    dlg.reject()  # cancel — no write
    assert prefs.auto_export_pdf is False


def test_roundtrip_png(prefs: Preferences) -> None:
    assert prefs.auto_export_png is True  # defaults on
    prefs.auto_export_png = False
    assert prefs.auto_export_png is False
    prefs.auto_export_png = True
    assert prefs.auto_export_png is True


def test_png_dpi_default_and_roundtrip(prefs: Preferences) -> None:
    assert prefs.png_dpi == 300            # publication default
    prefs.png_dpi = 600
    assert prefs.png_dpi == 600


def test_dark_override_roundtrip(prefs: Preferences) -> None:
    """Unset → follow the OS (None); a pinned dark/light choice round-trips and is
    cleared back to None. This is what persists the dark-mode toggle (the value is
    re-read at the next launch)."""
    assert prefs.dark_override is None            # default: follow the OS
    prefs.set_dark_override(True)
    assert prefs.dark_override is True            # pinned dark
    prefs.set_dark_override(False)
    assert prefs.dark_override is False           # pinned light
    prefs.set_dark_override(None)
    assert prefs.dark_override is None            # cleared → follow the OS again


def test_dark_override_persists_across_instances(tmp_path) -> None:
    """A pinned choice survives a new Preferences over the same store (a relaunch)."""
    ini = tmp_path / "settings.ini"
    Preferences(QSettings(str(ini), QSettings.IniFormat)).set_dark_override(True)
    assert Preferences(QSettings(str(ini), QSettings.IniFormat)).dark_override is True


def test_png_dpi_clamped_to_dialog_range(prefs: Preferences) -> None:
    """A corrupt/hand-edited stored DPI is clamped to 72–1200 so a save can't
    trigger an absurd render; garbage falls back to the default."""
    prefs._settings.setValue("export/png_dpi", 999_999)
    assert prefs.png_dpi == 1200
    prefs._settings.setValue("export/png_dpi", 5)
    assert prefs.png_dpi == 72
    prefs._settings.setValue("export/png_dpi", "garbage")
    assert prefs.png_dpi == 300


def test_dialog_hint_labels_use_theme_token(prefs: Preferences) -> None:
    """The dialog's hint labels take their ink from the theme palette (readable
    in dark mode), not a hardcoded light-mode grey."""
    from app.ui import theme

    try:
        theme.set_dark(True)
        dlg = PreferencesDialog(prefs)
        from PySide6.QtWidgets import QLabel
        hints = [l for l in dlg.findChildren(QLabel)
                 if "font-size: 11px" in l.styleSheet()]
        assert hints
        assert all(theme._DARK["ICON_MUTED"] in l.styleSheet() for l in hints)
        assert all("#666" not in l.styleSheet() for l in hints)
    finally:
        theme.set_dark(False)
