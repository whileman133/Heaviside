"""
Welcome screen + Help dialog (mainwindow).

The welcome screen now shows only the H(t) step diagram; the full keyboard-
shortcut and gesture reference lives in the scrollable `_HelpDialog` (Help ▸
Keyboard Shortcuts & Gestures, F1), built from the `_HELP_SHORTCUT_GROUPS` /
`_HELP_GESTURE_GROUPS` tables. These tests guard the table shape, a few anchor
entries, and that both widgets render without error.

Run headless:  QT_QPA_PLATFORM=offscreen pytest
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication, QScrollArea  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.ui.mainwindow import (  # noqa: E402
    _HELP_GESTURE_GROUPS,
    _HELP_SHORTCUT_GROUPS,
    _HelpDialog,
    _WelcomeScreen,
)

_ALL_GROUPS = _HELP_SHORTCUT_GROUPS + _HELP_GESTURE_GROUPS


@pytest.fixture(autouse=True)
def _no_startup_modals(monkeypatch):
    """Neutralise MainWindow's startup dependency warning and the first-run
    update-check disclosure so a headless run never blocks on a modal, and the
    live update probe never hits the network."""
    import app.ui.mainwindow as mw
    monkeypatch.setattr(mw, "check_dependencies", lambda: [])
    monkeypatch.setattr(
        mw.MainWindow, "_maybe_check_for_updates_on_startup", lambda self: None
    )


@pytest.mark.parametrize("group", _ALL_GROUPS)
def test_help_groups_are_well_formed(group):
    """Each group is (title, [(keys/gesture, detailed description), ...]) of
    non-empty strings, and every description is a real sentence (ends with '.')."""
    title, rows = group
    assert isinstance(title, str) and title.strip()
    assert len(rows) > 0
    for row in rows:
        assert isinstance(row, tuple) and len(row) == 2
        keys, desc = row
        assert isinstance(keys, str) and keys.strip()
        assert isinstance(desc, str) and desc.strip()
        assert desc.endswith(".")        # detailed, sentence-style descriptions


def test_shortcuts_cover_core_actions():
    keys = {k for _, rows in _HELP_SHORTCUT_GROUPS for k, _ in rows}
    for want in ("S", "W", "P", "R", "Ctrl+N", "Ctrl+S", "Ctrl+Z", "Ctrl+Shift+Z"):
        assert want in keys
    # The Tab-cycle group documents each implemented cycling target + reverse.
    assert sum(k.startswith("Tab (over") for k in keys) >= 3
    assert any("Shift+Tab" in k for k in keys)


def test_gestures_cover_core_actions():
    keys = {g for _, rows in _HELP_GESTURE_GROUPS for g, _ in rows}
    assert any("endpoint" in g for g in keys)      # drag endpoint to disconnect
    assert any(g.startswith("Double-click") for g in keys)
    assert any("Scroll" in g for g in keys)        # zoom


def test_help_dialog_renders_and_is_scrollable():
    dlg = _HelpDialog()
    dlg.resize(600, 620)
    assert dlg.findChild(QScrollArea) is not None   # content is scrollable
    pm = dlg.grab()                                  # triggers layout/paint
    assert not pm.isNull()


def test_welcome_screen_renders_without_error():
    """The (now diagram-only) welcome screen paints at typical and small sizes."""
    for w, h in ((900, 700), (640, 420)):
        screen = _WelcomeScreen()
        screen.resize(w, h)
        assert not screen.grab().isNull()


def test_help_action_wired_to_toolbar_and_menu(monkeypatch):
    """The Help action opens the dialog from both the menu and a right-aligned
    toolbar button (the same shared `_act_help`)."""
    import app.ui.mainwindow as mw
    from PySide6.QtWidgets import QToolBar

    # Avoid the modal "missing dependencies" warning during construction.
    monkeypatch.setattr(mw, "check_dependencies", lambda: [])
    win = mw.MainWindow()
    try:
        assert hasattr(win, "_act_help")
        toolbar = win.findChild(QToolBar)
        assert win._act_help in toolbar.actions()
        # Help and the bug-report button are right-aligned (pushed by an
        # expanding spacer); the bug button sits just after Help, so it is last.
        assert toolbar.actions()[-2] is win._act_help
        assert toolbar.actions()[-1] is win._act_report_bug
    finally:
        win._preview_worker.shutdown()
        win.close()


def test_report_bug_opens_github_issues(monkeypatch):
    """Report-a-bug (Help menu + toolbar, the same shared action) opens the
    project's GitHub issues page in the browser."""
    import app.ui.mainwindow as mw
    from PySide6.QtWidgets import QMenu, QToolBar

    assert mw._ISSUES_URL == "https://github.com/whileman133/Heaviside/issues"

    captured = {}

    class _FakeDesktop:
        @staticmethod
        def openUrl(url):
            captured["url"] = url.toString()
            return True

    monkeypatch.setattr(mw, "QDesktopServices", _FakeDesktop)
    monkeypatch.setattr(mw, "check_dependencies", lambda: [])
    win = mw.MainWindow()
    try:
        # The same shared QAction is added to both the toolbar and a menu.
        toolbar = win.findChild(QToolBar)
        assert win._act_report_bug in toolbar.actions()
        assert any(
            isinstance(w, QMenu) for w in win._act_report_bug.associatedObjects()
        )
        # Triggering it opens the issues URL.
        win._act_report_bug.trigger()
        assert captured["url"] == mw._ISSUES_URL
    finally:
        win._preview_worker.shutdown()
        win.close()


def test_welcome_screen_renders_dark_with_theme_tokens():
    """The welcome diagram reads its inks from the theme palette at paint time,
    so it renders (readably) over the dark canvas background too."""
    from app.canvas import style
    from app.ui import theme

    try:
        theme.set_dark(True)
        style.set_dark(True)
        screen = _WelcomeScreen()
        screen.resize(640, 420)
        assert not screen.grab().isNull()
    finally:
        style.set_dark(False)
        theme.set_dark(False)


def test_help_table_inks_follow_theme():
    """The Help reference table takes its description/header colours from the
    theme tokens (hardcoded light inks were unreadable in dark mode)."""
    import app.ui.mainwindow as mw
    from PySide6.QtGui import QColor
    from app.ui import theme

    try:
        theme.set_dark(True)
        table = mw._RefTable(_HELP_SHORTCUT_GROUPS[:1], mono=True)
        # Row 0 is the group header; row 1 is the first key/description row.
        desc_item = table.item(1, 1)
        assert desc_item.foreground().color() == QColor(theme._DARK["TEXT"])
        head_item = table.item(0, 0)
        assert head_item.background().color() == QColor(theme._DARK["TABLE_HEADER_BG"])
    finally:
        theme.set_dark(False)


def test_about_dialog_inks_follow_theme():
    """The About dialog's secondary text uses theme tokens, not hardcoded greys."""
    import app.ui.mainwindow as mw
    from PySide6.QtWidgets import QLabel
    from app.ui import theme

    try:
        theme.set_dark(True)
        dlg = mw._AboutDialog()
        sheets = [l.styleSheet() for l in dlg.findChildren(QLabel)]
        assert any(theme._DARK["TEXT_MUTED"] in s for s in sheets)
        assert not any("#666" in s or "#888" in s or "#999" in s for s in sheets)
    finally:
        theme.set_dark(False)
