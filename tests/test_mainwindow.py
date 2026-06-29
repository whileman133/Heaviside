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
    """Pretend the LaTeX toolchain is present so MainWindow's startup compile and
    the no-LaTeX preview notice don't kick in, and stub the startup update probe
    so it never hits the network. Tests that care about either drive them
    explicitly; the rest get a clean, side-effect-free MainWindow.
    """
    monkeypatch.setattr(mw, "check_dependencies", lambda: [])
    monkeypatch.setattr(
        mw.MainWindow, "_maybe_check_for_updates_on_startup", lambda self: None
    )


@pytest.fixture(autouse=True)
def _restore_light_theme():
    """Building a MainWindow sets the global light/dark theme tokens (from the OS
    or a persisted override). Restore light after each test so the dark state does
    not leak into other modules' palette-default assertions."""
    yield
    from app.canvas import style as _style
    from app.ui import theme as _theme
    _style.set_dark(False)
    _theme.set_dark(False)


@pytest.fixture(autouse=True)
def _reset_custom_runtime():
    """Scrub any runtime-registered custom components so they don't leak into the
    module-level REGISTRY seen by other tests."""
    yield
    from app.components.registry import reset_runtime_components
    reset_runtime_components()


def _custom_spec(name: str = "custom:t"):
    from app.components.model import CustomComponentSpec
    return CustomComponentSpec(
        name=name, display_name="T", category="Custom", base_kind="transformer",
        ctikzset=[], extra_options="",
        pins=[{"name": "A1", "offset": [-1.0, -1.0], "anchor": "A1"}],
        bbox=(-1.0, -1.0, 1.0, 1.0), default_span=(0.0, 0.0),
        geometry={"viewBox": "0 0 1 1", "paths": [], "glyphs": []}, ctikz_version=None)


def test_install_and_delete_custom_component(tmp_path, monkeypatch):
    from app.components.registry import REGISTRY
    from PySide6.QtWidgets import QMessageBox

    win = _win(tmp_path)
    spec = _custom_spec()
    win._install_custom_component(spec, rebuild=False)
    assert spec.name in win._scene.schematic.custom_components
    assert spec.name in REGISTRY                    # registered at runtime

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win._on_delete_custom_component(spec.name)
    assert spec.name not in win._scene.schematic.custom_components
    assert spec.name not in REGISTRY                # scrubbed


def test_delete_custom_component_blocked_when_in_use(tmp_path, monkeypatch):
    from app.components.model import Component
    from PySide6.QtWidgets import QMessageBox

    win = _win(tmp_path)
    spec = _custom_spec()
    win._install_custom_component(spec, rebuild=False)
    win._scene.schematic.components.append(
        Component(id="x1", kind=spec.name, position=(0.0, 0.0), rotation=0, options=""))

    shown = {"info": False}
    monkeypatch.setattr(QMessageBox, "information",
                        lambda *a, **k: shown.__setitem__("info", True))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win._on_delete_custom_component(spec.name)
    assert shown["info"]                            # the "in use" notice was shown
    assert spec.name in win._scene.schematic.custom_components   # not deleted


def _win(tmp_path):
    """A MainWindow whose Preferences are backed by an isolated temp INI."""
    win = MainWindow()
    win._prefs = Preferences(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    return win


def _wait_auto_export(win, timeout=10.0):
    """Pump the event loop until the background auto-export pipeline drains."""
    import time

    deadline = time.time() + timeout
    while ((win._auto_export_busy or win._auto_export_pending is not None)
           and time.time() < deadline):
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()
    assert not win._auto_export_busy, "auto-export did not finish in time"


def test_auto_export_tex_needs_no_latex(tmp_path, monkeypatch):
    """TeX auto-export writes the snippet without ever invoking the compiler."""
    win = _win(tmp_path)
    win._prefs.auto_export_tex = True
    # Isolate the TeX branch: SVG/PNG default on and would invoke the compiler.
    win._prefs.auto_export_pdf = False
    win._prefs.auto_export_eps = False
    win._prefs.auto_export_svg = False
    win._prefs.auto_export_png = False

    # If the TeX branch ever reaches the image-compile path, fail loudly: the
    # whole point is that a .tex snippet needs no pdflatex.
    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("compile_tex must not be called for TeX-only export")

    monkeypatch.setattr(mw, "compile_tex", _boom)

    out = tmp_path / "demo.hv"
    win._auto_export(out)
    _wait_auto_export(win)

    tex = out.with_suffix(".tex")
    assert tex.exists()
    assert "Heaviside" in tex.read_text(encoding="utf-8")
    assert not out.with_suffix(".pdf").exists()


def test_auto_export_disabled_writes_nothing(tmp_path):
    """With every auto-export preference off, no sibling files are written."""
    win = _win(tmp_path)
    for attr in ("auto_export_tex", "auto_export_pdf", "auto_export_eps",
                 "auto_export_svg", "auto_export_png"):
        setattr(win._prefs, attr, False)  # TeX/SVG/PNG now default on; force all off
    out = tmp_path / "demo.hv"
    win._auto_export(out)
    _wait_auto_export(win)
    assert not any(
        out.with_suffix(s).exists() for s in (".tex", ".pdf", ".eps", ".svg", ".png")
    )


def test_auto_export_runs_off_the_ui_thread(tmp_path, monkeypatch):
    """The compile/convert work runs on a worker thread (the UI thread only
    snapshots the source), so Ctrl+S never blocks on pdflatex."""
    import threading

    from PySide6.QtGui import QImage

    win = _win(tmp_path)
    for attr in ("auto_export_tex", "auto_export_pdf", "auto_export_eps",
                 "auto_export_svg"):
        setattr(win._prefs, attr, False)
    win._prefs.auto_export_png = True

    seen = {}

    def fake_compile(tex):
        seen["thread"] = threading.current_thread()
        return b"%PDF-1.4"

    img = QImage(8, 8, QImage.Format_RGB32); img.fill(0xFFFFFFFF)
    monkeypatch.setattr(mw, "compile_tex", fake_compile)
    monkeypatch.setattr(mw, "pdf_to_qimage", lambda *a, **k: img)

    win._auto_export(tmp_path / "demo.hv")
    _wait_auto_export(win)
    assert (tmp_path / "demo.png").exists()
    assert seen["thread"] is not threading.main_thread()


def test_auto_export_is_single_flight(tmp_path, monkeypatch):
    """A save while an export runs queues (and replaces) the pending job rather
    than overlapping; the newest queued job runs when the current one ends."""
    import threading

    from PySide6.QtGui import QImage

    win = _win(tmp_path)
    for attr in ("auto_export_tex", "auto_export_pdf", "auto_export_eps",
                 "auto_export_svg"):
        setattr(win._prefs, attr, False)
    win._prefs.auto_export_png = True

    gate = threading.Event()
    compiles = []

    def slow_compile(tex):
        compiles.append(threading.current_thread())
        gate.wait(5)
        return b"%PDF-1.4"

    img = QImage(8, 8, QImage.Format_RGB32); img.fill(0xFFFFFFFF)
    monkeypatch.setattr(mw, "compile_tex", slow_compile)
    monkeypatch.setattr(mw, "pdf_to_qimage", lambda *a, **k: img)

    win._auto_export(tmp_path / "a.hv")          # dispatched, blocks on the gate
    assert win._auto_export_busy
    win._auto_export(tmp_path / "b.hv")          # queued
    win._auto_export(tmp_path / "c.hv")          # replaces the queued job
    assert win._auto_export_pending is not None
    assert win._auto_export_pending.path == tmp_path / "c.hv"

    gate.set()
    _wait_auto_export(win)
    assert (tmp_path / "a.png").exists()
    assert (tmp_path / "c.png").exists()
    assert not (tmp_path / "b.png").exists()     # superseded job never ran
    assert len(compiles) == 2                    # one per dispatched job


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


def test_toolbar_theme_button_pins_dark(tmp_path):
    """Setting the theme to Dark flips the palettes and pins it: an OS change is
    then ignored (the manual choice wins)."""
    from app.canvas import style
    from app.ui import theme

    win = _win(tmp_path)
    try:
        win._set_theme_mode("light")
        assert win._dark is False and win._follow_system is False

        win._set_theme_mode("dark")
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

        win._set_theme_mode("light")  # back to light
        assert win._dark is False
        assert not style.is_dark() and not theme.is_dark()
        assert theme._LIGHT["SURFACE_ALT"] in win._palette._search.styleSheet()
    finally:
        style.set_dark(False)
        theme.set_dark(False)


def test_theme_radio_group_selects_mode(tmp_path):
    """The toolbar exposes System / Light / Dark as an exclusive radio group:
    triggering one selects and persists that mode and leaves the others unchecked
    (System clears the override; Light/Dark pin it)."""
    win = _win(tmp_path)
    acts = win._theme_actions
    assert set(acts) == {"system", "light", "dark"}
    # Exactly one button is active at a time (exclusive group).
    assert win._theme_group.isExclusive()

    acts["light"].trigger()
    assert win._theme_mode == "light" and win._prefs.dark_override is False
    assert acts["light"].isChecked()
    assert not acts["system"].isChecked() and not acts["dark"].isChecked()

    acts["dark"].trigger()
    assert win._theme_mode == "dark" and win._prefs.dark_override is True
    assert acts["dark"].isChecked()
    assert not acts["system"].isChecked() and not acts["light"].isChecked()

    acts["system"].trigger()
    assert win._theme_mode == "system" and win._prefs.dark_override is None
    assert win._follow_system is True
    assert acts["system"].isChecked()
    assert not acts["light"].isChecked() and not acts["dark"].isChecked()


def test_theme_radio_group_is_flat_icon_trio(tmp_path):
    """The three theme buttons render as plain flat toolbar buttons (no pill, no
    caption), grouped only by tight spacing inside their container, with a dotted
    divider separating them from the help/bug buttons."""
    from PySide6.QtWidgets import QToolButton

    win = _win(tmp_path)
    grp = win._theme_group_box
    btns = grp.findChildren(QToolButton)
    assert len(btns) == 3
    # Each button drives one theme action.
    assert {b.defaultAction() for b in btns} == set(win._theme_actions.values())
    # Flat, like the other toolbar buttons (auto-raised, no bordered-pill object name).
    assert all(b.autoRaise() for b in btns)
    assert grp.objectName() == ""
    # Tighter than the toolbar's own 3px spacing → reads as a group.
    assert grp.layout().spacing() == 0
    # A dotted divider separates the theme group from the help/bug buttons.
    div = win._theme_divider
    assert div.objectName() == "toolbarDottedDivider"
    assert "dotted" in div.styleSheet()


def test_theme_radio_group_reflects_persisted_mode(tmp_path):
    """The active radio button is checked on launch to match the persisted mode."""
    win = _win(tmp_path)
    win._set_theme_mode("dark")
    assert win._theme_actions["dark"].isChecked()
    win._set_theme_mode("system")
    assert win._theme_actions["system"].isChecked()
    assert not win._theme_actions["dark"].isChecked()


def test_placement_shortcuts_are_window_wide(tmp_path, monkeypatch):
    """A placement key delivered to the window (canvas not focused) still places —
    MainWindow delegates to the view's handler. Modifier chords are ignored (§10.2)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    win = _win(tmp_path)
    started: list[str] = []
    monkeypatch.setattr(win._scene, "start_placement", lambda k: started.append(k))

    win.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_C, Qt.NoModifier, "c"))
    assert started == ["capacitor"]
    # A Ctrl-chord (e.g. Ctrl+C copy) is never hijacked as a placement key.
    win.keyPressEvent(
        QKeyEvent(QKeyEvent.KeyPress, Qt.Key_C, Qt.ControlModifier, "c")
    )
    assert started == ["capacitor"]


def test_placement_shortcuts_skip_text_inputs(tmp_path, monkeypatch):
    """A placement key is ignored while a text field is focused, so typing into the
    palette search (or the read-only source panel) is never hijacked (§10.2)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QLineEdit

    win = _win(tmp_path)
    started: list[str] = []
    monkeypatch.setattr(win._scene, "start_placement", lambda k: started.append(k))
    # Pretend a text field holds focus (offscreen focus is unreliable to set).
    monkeypatch.setattr(
        "app.canvas.view.QApplication.focusWidget", staticmethod(lambda: QLineEdit())
    )
    win.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_C, Qt.NoModifier, "c"))
    assert started == []


def test_rotate_shortcut_is_ctrl_r(tmp_path):
    """Rotate is bound to Ctrl+R (⌘R on macOS), not a plain letter, so the letters
    stay free for placement (§10.2)."""
    from PySide6.QtGui import QKeySequence

    win = _win(tmp_path)
    assert win._rotate_shortcut.key() == QKeySequence("Ctrl+R")


def test_ctrl_r_rotates_selection(tmp_path):
    """Activating the rotate shortcut turns the selected component 45° CW (§6.x —
    components orient in 45° increments)."""
    win = _win(tmp_path)
    comp = win._scene.place_component("R", (5.0, 5.0))
    win._scene._comp_items[comp.id].setSelected(True)
    before = win._scene._component_by_id(comp.id).rotation
    win._rotate_shortcut.activated.emit()  # simulate the Ctrl+R press
    assert win._scene._component_by_id(comp.id).rotation == (before + 45) % 360


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
    win._prefs.auto_export_svg = False     # would need pdftocairo
    img = QImage(8, 8, QImage.Format_RGB32); img.fill(0xFFFFFFFF)
    monkeypatch.setattr(mw, "compile_tex", lambda *a, **k: b"%PDF-1.4")
    monkeypatch.setattr(mw, "pdf_to_qimage", lambda *a, **k: img)

    out = tmp_path / "demo.hv"
    win._auto_export(out)
    _wait_auto_export(win)
    assert out.with_suffix(".png").exists()


def test_manual_update_up_to_date_shows_info(tmp_path, monkeypatch):
    """Help ▸ Check for Updates with no newer release shows an info box and
    re-enables the menu action."""
    win = _win(tmp_path)
    seen = {}
    monkeypatch.setattr(mw.QMessageBox, "information",
                        lambda *a, **k: seen.setdefault("info", True))
    win._act_check_updates.setEnabled(False)
    win._on_manual_update_result(None)
    assert seen.get("info") is True
    assert win._act_check_updates.isEnabled()


def test_startup_update_respects_skipped_version(tmp_path, monkeypatch):
    """The startup result prompts only for a version the user hasn't skipped."""
    from app.update import UpdateInfo

    win = _win(tmp_path)
    prompted = []
    monkeypatch.setattr(win, "_show_update_available",
                        lambda info, **k: prompted.append(info.version))
    win._prefs.skipped_update_version = "0.3.0"

    win._on_startup_update_result(UpdateInfo("0.3.0", "v0.3.0", "u", "n", False))
    assert prompted == []  # skipped → no prompt

    win._on_startup_update_result(UpdateInfo("0.4.0", "v0.4.0", "u", "n", False))
    assert prompted == ["0.4.0"]  # not skipped → prompt


def test_auto_export_failure_in_one_format_does_not_block_others(tmp_path, monkeypatch):
    """A failing image export (e.g. SVG without Poppler) must not skip PNG.

    Regression for the shared-try block that bailed on the first failure once
    TeX/SVG/PNG became on-by-default.
    """
    from PySide6.QtGui import QImage
    from app.preview.latex import CompileError

    win = _win(tmp_path)
    for attr in ("auto_export_tex", "auto_export_pdf", "auto_export_eps"):
        setattr(win._prefs, attr, False)
    win._prefs.auto_export_svg = True
    win._prefs.auto_export_png = True

    monkeypatch.setattr(mw, "compile_tex", lambda *a, **k: b"%PDF-1.4")
    def _svg_boom(*a, **k):
        raise CompileError("pdftocairo missing")
    monkeypatch.setattr(mw, "pdf_to_svg", _svg_boom)
    img = QImage(8, 8, QImage.Format_RGB32); img.fill(0xFFFFFFFF)
    monkeypatch.setattr(mw, "pdf_to_qimage", lambda *a, **k: img)

    win._auto_export(tmp_path / "demo.hv")
    _wait_auto_export(win)
    assert (tmp_path / "demo.png").exists()        # PNG written despite SVG failure
    assert not (tmp_path / "demo.svg").exists()    # SVG failed
    assert "failed" in win._status_compile.text()  # reported via the status bar


def test_examples_menu_groups_by_category(tmp_path):
    """Open Example shows category submenus mirroring the examples/ sub-folders,
    and every bundled .hv appears exactly once."""
    from app.resources import resource_path

    win = _win(tmp_path)
    # Traverse inline in one pass — holding a QMenu wrapper across loop scopes
    # trips a PySide lifetime quirk, so process each submenu where it is live.
    leaves: list[str] = []
    n_categories = 0
    found = False
    for top in win.menuBar().actions():
        top_menu = top.menu()
        if top_menu is None:
            continue
        for act in top_menu.actions():
            ex_menu = act.menu()
            if ex_menu is None or ex_menu.title().replace("&", "") != "Open Example":
                continue
            found = True
            for ex_act in ex_menu.actions():
                cat_menu = ex_act.menu()
                if cat_menu is not None:                 # a category submenu
                    n_categories += 1
                    leaves += [it.text() for it in cat_menu.actions()]
                elif not ex_act.isSeparator() and ex_act.isEnabled():
                    leaves.append(ex_act.text())         # a loose example

    assert found, "Open Example submenu not found"
    assert n_categories >= 1, "expected at least one category submenu"
    expected = sorted(p.stem for p in resource_path("examples").rglob("*.hv"))
    assert sorted(leaves) == expected


def _menus_containing(win, action) -> set[str]:
    """The set of top-level menu names (mnemonics stripped) that hold *action*.

    Done in a single inline pass: holding a QMenu wrapper across a helper boundary
    trips a PySide object-lifetime quirk, so each menu is inspected while live.
    """
    names: set[str] = set()
    for top in win.menuBar().actions():
        menu = top.menu()
        if menu is None:
            continue
        if action in menu.actions():
            names.add(menu.title().replace("&", ""))
    return names


def test_preferences_reachable_in_menus_off_macos(tmp_path, monkeypatch):
    """On Linux/Windows the Preferences action must carry NoRole (so a global menu
    bar can't relocate it out of view) and appear in BOTH the Edit and File menus.

    Regression: with PreferencesRole on a Linux desktop that honours menu roles,
    Preferences was pulled into a non-existent application menu and unreachable.
    """
    from PySide6.QtGui import QAction

    monkeypatch.setattr(mw.sys, "platform", "linux")
    win = _win(tmp_path)

    act = win._act_preferences
    assert act.menuRole() == QAction.NoRole
    assert {"Edit", "File"} <= _menus_containing(win, act), "Preferences must be in Edit and File"


def test_preferences_uses_app_menu_role_on_macos(tmp_path, monkeypatch):
    """On macOS the action keeps PreferencesRole (Qt relocates it to the
    application menu) and is NOT duplicated into the File menu."""
    from PySide6.QtGui import QAction

    monkeypatch.setattr(mw.sys, "platform", "darwin")
    win = _win(tmp_path)

    act = win._act_preferences
    assert act.menuRole() == QAction.PreferencesRole
    assert "File" not in _menus_containing(win, act), "should not duplicate in File on macOS"


def test_preview_panel_no_latex_notice_is_exclusive_state(tmp_path):
    """`show_no_latex()` hands the whole preview area to a centred notice (image
    and error hidden) and names the missing tool; a rendered image takes it back.
    The three states (image / notice / error) are mutually exclusive."""
    from PySide6.QtGui import QImage

    panel = mw._PreviewPanel()

    panel.show_no_latex()
    assert panel._notice.isHidden() is False
    assert panel._img_label.isHidden() is True
    assert panel._error_label.isHidden() is True
    assert "pdflatex" in panel._notice_text.text()

    img = QImage(4, 4, QImage.Format_ARGB32)
    img.fill(0)
    panel.set_image(img)
    assert panel._img_label.isHidden() is False
    assert panel._notice.isHidden() is True

    panel.set_error("boom")
    assert panel._error_label.isHidden() is False
    assert panel._notice.isHidden() is True


def test_auto_compile_shows_no_latex_notice_and_skips_worker(tmp_path, monkeypatch):
    """When pdflatex is missing, an auto-compile shows the no-LaTeX notice and
    does NOT spawn the compile worker (which would only fail), replacing the old
    modal dependency warning."""
    monkeypatch.setattr(mw, "check_dependencies", lambda: ["pdflatex not found."])
    win = _win(tmp_path)

    compiled: list[str] = []
    monkeypatch.setattr(win._preview_worker, "request_compile", compiled.append)
    win._on_auto_compile()

    assert compiled == [], "compile worker must not run when pdflatex is missing"
    assert win._preview_panel._notice.isHidden() is False


def test_disabled_toolbar_icon_uses_muted_ink_not_palette():
    """A disabled toolbar icon must render in the theme's muted ink, independent of
    the application palette. Regression: qtawesome defaults `color_disabled` to
    `palette(Disabled, Text)` resolved at icon-creation time; toolbar icons are
    built before the dark palette is applied at a dark-persisted launch, so the
    disabled undo/redo captured the light palette's dark ink and rendered
    near-black on the dark toolbar."""
    from PySide6.QtGui import QIcon, QPalette, QColor
    from PySide6.QtCore import QSize
    from app.ui import theme

    qapp = QApplication.instance()
    saved = qapp.palette()
    try:
        # The adverse condition: dark theme tokens, but an app palette whose
        # Disabled/Text is near-black (as it is before _apply_color_scheme runs).
        theme.set_dark(True)
        pal = QPalette(saved)
        pal.setColor(QPalette.Disabled, QPalette.Text, QColor("#101010"))
        qapp.setPalette(pal)

        icon = mw.MainWindow._themed_qicon("fa5s.undo")
        img = icon.pixmap(QSize(24, 24), QIcon.Mode.Disabled).toImage()
        reds = [
            img.pixelColor(x, y).red()
            for y in range(0, 24, 2)
            for x in range(0, 24, 2)
            if img.pixelColor(x, y).alpha() > 100
        ]
        # Muted ink (ICON_MUTED ≈ #9aa0a8, red≈154), not the palette's near-black.
        assert reds, "icon rendered no opaque ink"
        assert max(reds) > 120, f"disabled ink too dark ({max(reds)}); palette leaked in"
    finally:
        qapp.setPalette(saved)
        theme.set_dark(False)


def test_load_path_opens_a_schematic(tmp_path):
    """load_path() loads a saved .hv into the editor (the shared loader behind
    File ▸ Open and the command-line / file-association launch)."""
    from app.schematic import io
    from app.schematic.model import Schematic

    win = _win(tmp_path)
    src = tmp_path / "demo.hv"
    io.save(Schematic(version="0.2", name="demo"), src)
    assert win.load_path(src) is True
    assert win._current_path == src
    assert win._modified is False


def test_load_path_reports_a_bad_file(tmp_path, monkeypatch):
    """A corrupt/unreadable file surfaces an error and load_path returns False
    (the dialog is stubbed so the headless run doesn't block)."""
    win = _win(tmp_path)
    monkeypatch.setattr(mw.QMessageBox, "critical", lambda *a, **k: None)
    bad = tmp_path / "broken.hv"
    bad.write_text("{ not valid json", encoding="utf-8")
    assert win.load_path(bad) is False


def test_schematic_arg_picks_the_hv_file(tmp_path):
    """main._schematic_arg finds an existing .hv among the CLI args (what the
    Windows file association passes), ignoring flags and missing paths."""
    import main as entry

    f = tmp_path / "x.hv"
    f.write_text("{}", encoding="utf-8")
    assert entry._schematic_arg(["heaviside.exe", str(f)]) == str(f)
    assert entry._schematic_arg(["heaviside.exe", "--flag", str(f)]) == str(f)
    assert entry._schematic_arg(["heaviside.exe"]) is None
    assert entry._schematic_arg(["heaviside.exe", str(tmp_path / "missing.hv")]) is None
    assert entry._schematic_arg(["heaviside.exe", str(tmp_path)]) is None  # dir, not file


def test_file_open_event_loads_schematic(tmp_path):
    """The macOS QFileOpenEvent filter routes a .hv open to load_path (the Finder
    'open with' path, the counterpart of the Windows argv association)."""
    from PySide6.QtGui import QFileOpenEvent
    from app.schematic import io
    from app.schematic.model import Schematic
    import main as entry

    win = _win(tmp_path)
    src = tmp_path / "doc.hv"
    io.save(Schematic(version="0.2", name="doc"), src)
    filt = entry._FileOpenFilter(win)
    handled = filt.eventFilter(win, QFileOpenEvent(str(src)))
    assert handled is True
    assert win._current_path == src


def test_theme_mode_persists_choice(tmp_path):
    """Setting the theme to dark/light pins the choice into Preferences (so it is
    restored next launch); System clears it (follow the OS again)."""
    win = _win(tmp_path)
    win._set_theme_mode("dark")
    assert win._dark is True and win._follow_system is False
    assert win._prefs.dark_override is True
    win._set_theme_mode("light")
    assert win._prefs.dark_override is False
    win._set_theme_mode("system")
    assert win._prefs.dark_override is None and win._follow_system is True


def test_launch_honors_persisted_dark(tmp_path, monkeypatch):
    """A MainWindow built with a stored dark override comes up dark (not following
    the OS) — the relaunch behaviour the persisted theme choice gives."""
    from PySide6.QtCore import QSettings

    seeded = Preferences(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    seeded.set_dark_override(True)
    # MainWindow.__init__ resolves the theme from Preferences() before building UI.
    monkeypatch.setattr(mw, "Preferences", lambda *a, **k: seeded)
    win = MainWindow()
    assert win._dark is True
    assert win._theme_mode == "dark"
    assert win._follow_system is False


# ---------------------------------------------------------------------------
# Unsaved-changes dialog (Save / Don't Save / Cancel) — data-loss fix
# ---------------------------------------------------------------------------

def _all_auto_export_off(win) -> None:
    for attr in ("auto_export_tex", "auto_export_pdf", "auto_export_eps",
                 "auto_export_svg", "auto_export_png"):
        setattr(win._prefs, attr, False)


def test_confirm_discard_offers_save_and_saves(tmp_path, monkeypatch):
    """The unsaved-changes dialog offers Save (default) / Discard / Cancel;
    choosing Save writes the file and proceeds."""
    win = _win(tmp_path)
    _all_auto_export_off(win)
    win._preview_worker.request_compile = lambda *a, **k: None
    win._scene.place_component("R", (2.0, 2.0))
    assert win._modified
    target = tmp_path / "kept.hv"
    win._current_path = target

    seen = {}

    def fake_question(parent, title, text, buttons, default):
        seen["buttons"] = buttons
        seen["default"] = default
        return mw.QMessageBox.Save

    monkeypatch.setattr(mw.QMessageBox, "question", staticmethod(fake_question))
    assert win._confirm_discard() is True
    assert target.exists()                          # Save actually saved
    assert win._modified is False
    assert seen["default"] == mw.QMessageBox.Save   # Save is the default button
    for btn in (mw.QMessageBox.Save, mw.QMessageBox.Discard, mw.QMessageBox.Cancel):
        assert seen["buttons"] & btn


def test_confirm_discard_save_cancelled_treated_as_cancel(tmp_path, monkeypatch):
    """Untitled document + Save → the Save As dialog; cancelling it must abort
    the whole operation (treated as Cancel — nothing is lost)."""
    win = _win(tmp_path)
    _all_auto_export_off(win)
    win._preview_worker.request_compile = lambda *a, **k: None
    win._scene.place_component("R", (2.0, 2.0))
    assert win._current_path is None and win._modified

    monkeypatch.setattr(mw.QMessageBox, "question",
                        staticmethod(lambda *a, **k: mw.QMessageBox.Save))
    monkeypatch.setattr(mw.QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))   # user cancels
    assert win._confirm_discard() is False
    assert win._modified  # still dirty — nothing was discarded


def test_confirm_discard_discard_and_cancel(tmp_path, monkeypatch):
    """Discard proceeds without saving; Cancel aborts."""
    win = _win(tmp_path)
    _all_auto_export_off(win)
    win._preview_worker.request_compile = lambda *a, **k: None
    win._scene.place_component("R", (2.0, 2.0))

    monkeypatch.setattr(mw.QMessageBox, "question",
                        staticmethod(lambda *a, **k: mw.QMessageBox.Discard))
    assert win._confirm_discard() is True

    monkeypatch.setattr(mw.QMessageBox, "question",
                        staticmethod(lambda *a, **k: mw.QMessageBox.Cancel))
    assert win._confirm_discard() is False


# ---------------------------------------------------------------------------
# Save-error handling
# ---------------------------------------------------------------------------

def test_do_save_reports_schematic_save_error(tmp_path, monkeypatch):
    """io.save raising SchematicSaveError shows the error dialog (noting the
    on-disk file is untouched) and _do_save returns False."""
    from app.schematic.io import SchematicSaveError

    win = _win(tmp_path)
    _all_auto_export_off(win)

    def bad_save(schematic, path):
        raise SchematicSaveError("schematic invariant violated: boom")

    seen = {}
    monkeypatch.setattr(mw, "save", bad_save)
    monkeypatch.setattr(
        mw.QMessageBox, "critical",
        staticmethod(lambda parent, title, text: seen.setdefault("text", text)),
    )
    assert win._do_save(tmp_path / "x.hv") is False
    assert "not modified" in seen["text"]
    assert not (tmp_path / "x.hv").exists()


# ---------------------------------------------------------------------------
# Action enabled-state + modified-state tracking
# ---------------------------------------------------------------------------

def test_undo_redo_actions_track_stack(tmp_path):
    """Undo/Redo enable/disable follows the stack instead of being always on."""
    win = _win(tmp_path)
    win._preview_worker.request_compile = lambda *a, **k: None
    assert not win._act_undo.isEnabled()
    assert not win._act_redo.isEnabled()

    win._scene.place_component("R", (2.0, 2.0))
    assert win._act_undo.isEnabled()
    assert not win._act_redo.isEnabled()

    win._scene.undo()
    assert not win._act_undo.isEnabled()
    assert win._act_redo.isEnabled()

    win._scene.redo()
    assert win._act_undo.isEnabled()
    assert not win._act_redo.isEnabled()


def test_modified_state_follows_undo_stack_save_point(tmp_path):
    """Undoing back to the saved state clears the dirty marker (and the title
    dot), instead of staying modified forever."""
    win = _win(tmp_path)
    win._preview_worker.request_compile = lambda *a, **k: None
    assert win._modified is False

    win._scene.place_component("R", (2.0, 2.0))
    assert win._modified is True

    win._scene.undo()                  # back at the (initial) save point
    assert win._modified is False
    assert "•" not in win.windowTitle()

    win._scene.redo()
    assert win._modified is True

    win._modified = False              # simulates a successful save
    assert win._modified is False
    win._scene.undo()                  # diverges from the new save point
    assert win._modified is True


def test_manual_update_check_reenables_after_timeout(tmp_path, monkeypatch):
    """If the async update probe never reports back, the fallback timer
    re-enables Help ▸ Check for Updates so it can't stay dead all session."""
    win = _win(tmp_path)
    monkeypatch.setattr(mw, "_UPDATE_CHECK_REENABLE_MS", 0)
    monkeypatch.setattr(mw._update, "check_async", lambda *a, **k: None)  # lost
    win._on_check_updates_manual()
    assert not win._act_check_updates.isEnabled()
    for _ in range(20):
        QApplication.processEvents()
    assert win._act_check_updates.isEnabled()


# ---------------------------------------------------------------------------
# New documents / load behaviour
# ---------------------------------------------------------------------------

def test_new_document_declares_current_format_version(tmp_path, monkeypatch):
    """File ▸ New creates a document at the current .hv format version."""
    from app.schematic import io

    win = _win(tmp_path)
    win._preview_worker.request_compile = lambda *a, **k: None
    monkeypatch.setattr(win, "_confirm_discard", lambda: True)
    win._on_new()
    assert win._scene.schematic.version == io._FORMAT_VERSION


def test_load_path_warns_about_dangerous_latex(tmp_path):
    """Loading a file whose labels contain risky LaTeX primitives shows a
    non-blocking warning (status bar + message box), once per load."""
    from app.schematic import io

    author = _win(tmp_path)
    author._preview_worker.request_compile = lambda *a, **k: None
    r = author._scene.place_component("R", (2.0, 2.0))
    author._scene.edit_component_options(r.id, "l=\\write18{evil}")
    evil = tmp_path / "evil.hv"
    io.save(author._scene.schematic, evil)

    win = _win(tmp_path)
    win._preview_worker.request_compile = lambda *a, **k: None
    assert win.load_path(evil) is True
    assert win._latex_warning_box is not None
    assert "dangerous" in win._status_compile.text().lower()
    win._latex_warning_box.close()

    # A clean file shows no warning.
    clean = tmp_path / "clean.hv"
    author._scene.edit_component_options(r.id, "l=$R_1$")
    io.save(author._scene.schematic, clean)
    win2 = _win(tmp_path)
    win2._preview_worker.request_compile = lambda *a, **k: None
    win2._latex_warning_box = None
    assert win2.load_path(clean) is True
    assert win2._latex_warning_box is None


def test_load_path_survives_unexpected_exception(tmp_path, monkeypatch):
    """An unexpected (non-SchematicLoadError) failure in load shows the error
    dialog instead of crashing (belt & suspenders over io's own conversion)."""
    win = _win(tmp_path)
    seen = {}
    monkeypatch.setattr(mw, "load", lambda p: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(
        mw.QMessageBox, "critical",
        staticmethod(lambda parent, title, text: seen.setdefault("text", text)),
    )
    assert win.load_path(tmp_path / "whatever.hv") is False
    assert "boom" in seen["text"]


# ---------------------------------------------------------------------------
# Inspector flush-on-save + auto-compile failure visibility
# ---------------------------------------------------------------------------

def test_save_flushes_pending_inspector_edit(tmp_path):
    """An options edit still inside the 300 ms debounce window is committed by
    the save (not lost / serialised stale)."""
    import json

    win = _win(tmp_path)
    _all_auto_export_off(win)
    win._preview_worker.request_compile = lambda *a, **k: None
    r = win._scene.place_component("R", (2.0, 2.0))
    win._props.show_component(r.id)

    from app.ui.properties import OptionsSection
    sec = next(s for s in win._props._sections if isinstance(s, OptionsSection))
    sec._field.setText("l=$R_{99}$")          # starts the debounce timer
    assert sec._timer.isActive()

    target = tmp_path / "flush.hv"
    win._current_path = target
    assert win._on_save() is True
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["components"][0]["options"] == "l=$R_{99}$"


def test_auto_compile_failure_is_visible_in_status_bar(tmp_path, monkeypatch):
    """A generate() crash during auto-compile is no longer swallowed silently."""
    win = _win(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("codegen bug")

    monkeypatch.setattr(mw, "generate", boom)
    win._on_auto_compile()
    assert "Preview update failed" in win._status_compile.text()


# ---------------------------------------------------------------------------
# Document properties through the undo stack
# ---------------------------------------------------------------------------

def test_document_properties_are_undoable(tmp_path):
    """A Document-tab edit is one undoable step; undo reverts the model and the
    panel's combos reload to match."""
    win = _win(tmp_path)
    win._preview_worker.request_compile = lambda *a, **k: None
    win._doc_props._voltage.setCurrentIndex(
        win._doc_props._voltage.findData("european")
    )
    assert win._scene.schematic.voltage_style == "european"
    assert win._modified is True

    win._scene.undo()
    assert win._scene.schematic.voltage_style == "american"
    assert win._doc_props._voltage.currentData() == "american"  # panel reloaded
    assert win._modified is False

    win._scene.redo()
    assert win._scene.schematic.voltage_style == "european"
    assert win._doc_props._voltage.currentData() == "european"


# ---------------------------------------------------------------------------
# Crash guard (main.py excepthook)
# ---------------------------------------------------------------------------

def test_excepthook_logs_and_informs_without_exiting(tmp_path, monkeypatch):
    """The uncaught-exception handler writes the traceback to the app-data log,
    shows a message box (QApplication exists), and never raises or exits."""
    import sys

    import main as entry

    log = tmp_path / "errors.log"
    monkeypatch.setattr(entry, "_crash_log_path", lambda: log)
    seen = {}
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(
        QMessageBox, "critical",
        staticmethod(lambda *a, **k: seen.setdefault("box", a[2] if len(a) > 2 else "")),
    )
    try:
        raise ValueError("kaboom-for-test")
    except ValueError:
        entry._handle_uncaught(*sys.exc_info())

    assert "kaboom-for-test" in log.read_text(encoding="utf-8")
    assert "box" in seen
    assert str(log) in seen["box"]


def test_install_excepthooks_sets_both_hooks(monkeypatch):
    import sys
    import threading

    import main as entry

    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    monkeypatch.setattr(threading, "excepthook", threading.excepthook)
    entry._install_excepthooks()
    assert sys.excepthook is entry._handle_uncaught
    assert threading.excepthook is not None


def test_excepthook_handler_never_raises(tmp_path, monkeypatch):
    """Even when logging and the message box both fail, the handler swallows it."""
    import sys

    import main as entry

    def bad_path():
        raise OSError("disk gone")

    monkeypatch.setattr(entry, "_crash_log_path", bad_path)
    from PySide6.QtWidgets import QMessageBox

    def bad_box(*a, **k):
        raise RuntimeError("no display")

    monkeypatch.setattr(QMessageBox, "critical", staticmethod(bad_box))
    try:
        raise ValueError("quiet")
    except ValueError:
        entry._handle_uncaught(*sys.exc_info())   # must not raise


# ---------------------------------------------------------------------------
# Theme: explicit-palette fallback when the platform ignores scheme forcing
# ---------------------------------------------------------------------------

def test_dark_mode_palettes_native_widgets_when_scheme_unsupported(tmp_path):
    """Forcing Dark must restyle native widgets even on platforms whose theme
    ignores QStyleHints.setColorScheme (the offscreen platform used here, and
    bare Linux sessions). Regression: the inspector sidebar stayed light in
    dark mode because only Window/WindowText were overridden while Base/Button
    kept the platform's light defaults."""
    from PySide6.QtGui import QPalette

    win = _win(tmp_path)
    try:
        win._set_theme_mode("dark")
        pal = QApplication.palette()
        assert win._palette_fallback_active
        assert pal.color(QPalette.Base).lightness() < 100, "field bg must go dark"
        assert pal.color(QPalette.Button).lightness() < 100
        assert pal.color(QPalette.Text).lightness() > 150

        win._set_theme_mode("light")
        pal = QApplication.palette()
        assert pal.color(QPalette.Base).lightness() > 150, "back to light fields"

        # System mode releases the pin and restores the platform palette.
        win._set_theme_mode("system")
        assert not win._palette_fallback_active
    finally:
        win._set_theme_mode("system")
        win.close()
        win.deleteLater()
        QApplication.processEvents()


def test_startup_with_pinned_dark_applies_native_palette(tmp_path):
    """A saved Dark override must pin native widgets at construction, not only
    after a later toggle."""
    from PySide6.QtGui import QPalette

    prefs = Preferences(QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat))
    prefs.set_dark_override(True)
    prefs._settings.sync()

    # MainWindow reads the real default QSettings; patch the saved override in
    # by constructing, then re-running the startup pinning path with the
    # isolated prefs state it would have read.
    win = MainWindow()
    try:
        win._prefs = prefs
        win._theme_mode = "dark"
        win._follow_system = False
        win._dark = True
        from app.canvas import style as _style2
        from app.ui import theme as _theme2
        _theme2.set_dark(True)
        _style2.set_dark(True)
        win._apply_color_scheme()   # the call __init__ now makes for overrides
        pal = QApplication.palette()
        assert win._palette_fallback_active
        assert pal.color(QPalette.Base).lightness() < 100
    finally:
        win._set_theme_mode("system")
        win.close()
        win.deleteLater()
        QApplication.processEvents()


def test_source_panel_uses_platform_fixed_font(tmp_path):
    """The source pane asks for the platform's real fixed-width font instead of
    the generic "Monospace" family (whose absence makes Qt scan every installed
    font: 'Populating font family aliases took NNN ms')."""
    from PySide6.QtGui import QFontDatabase

    win = _win(tmp_path)
    try:
        expected = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).family()
        assert win._source_panel._text.font().family() == expected
    finally:
        win.close()
        win.deleteLater()
        QApplication.processEvents()


# Captured at import time, before the autouse _no_dependency_modal fixture
# replaces the class attribute with a no-op for each test.
_REAL_STARTUP_UPDATE_CHECK = mw.MainWindow._maybe_check_for_updates_on_startup


def test_startup_update_check_skipped_when_version_unresolved(tmp_path, monkeypatch):
    """A 0.0.0 runtime version (resolution failure in a mis-packaged bundle)
    must not trigger the startup update prompt — every release looks "newer"
    than 0.0.0, which nagged users of the broken 0.3.0 Windows build on every
    launch. The manual Help menu check stays available."""
    calls = []
    monkeypatch.setattr(mw._update, "check_async", lambda *a, **k: calls.append(a))

    win = _win(tmp_path)
    try:
        win._prefs.check_updates_on_startup = True

        monkeypatch.setattr(mw, "__version__", "0.0.0")
        _REAL_STARTUP_UPDATE_CHECK(win)
        assert calls == [], "0.0.0 must suppress the automatic probe"

        monkeypatch.setattr(mw, "__version__", "1.0.0")
        _REAL_STARTUP_UPDATE_CHECK(win)
        assert len(calls) == 1, "a real version still probes"
    finally:
        win.close()
        win.deleteLater()
        QApplication.processEvents()


def test_paste_action_starts_cursor_follow_placement(tmp_path):
    """The Edit-menu / Ctrl+V Paste action starts an interactive cursor-follow
    paste without crashing.

    Regression for #33: the action was wired straight to scene.paste, so Qt's
    QAction.triggered `checked` bool bound to paste()'s `at` parameter, took the
    "paste here" branch, and subscripted a bool (TypeError). It now calls
    begin_paste(), which enters PLACE mode with one ghost per clipboard component
    and commits nothing until the user clicks."""
    from app.canvas.scene import Mode

    win = _win(tmp_path)
    scene = win._scene
    comp = scene.place_component("R", (5.0, 5.0))
    scene._comp_items[comp.id].setSelected(True)
    scene.copy_selection()

    before = len(scene._schematic.components)
    win._act_paste.trigger()  # must not raise (regression: bool is not subscriptable)

    assert scene._mode == Mode.PLACE
    assert len(scene._paste_ghosts) == 1               # ghost for the copied component
    assert len(scene._schematic.components) == before  # nothing committed until a click
