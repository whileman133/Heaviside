"""
Tests for app/preview/worker.py — PreviewWorker thread lifecycle.

Focus on safe teardown: the background QThread must always be stopped before the
application exits, on either the window-close path or the app-quit path, and
shutdown() must be idempotent (both paths may run).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.preview.worker import PreviewWorker  # noqa: E402


def test_shutdown_stops_thread():
    w = PreviewWorker()
    assert w._thread.isRunning()
    w.shutdown()
    assert not w._thread.isRunning()


def test_shutdown_is_idempotent():
    """Both closeEvent and aboutToQuit may call shutdown(); the second is a no-op."""
    w = PreviewWorker()
    w.shutdown()
    w.shutdown()  # must not raise or re-stop an already-stopped thread
    assert w._stopped
    assert not w._thread.isRunning()


def test_about_to_quit_stops_thread():
    """Emitting aboutToQuit (the app-quit path, no window close) stops the thread."""
    w = PreviewWorker()
    assert w._thread.isRunning()
    _APP.aboutToQuit.emit()
    assert not w._thread.isRunning()
