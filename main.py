"""
Heaviside entry point (spec §11).

Creates the QApplication, shows the MainWindow, and starts the Qt event loop.
A ``.hv`` path on the command line is opened on launch — this is how the Windows
file association (and a shell "open with") starts the app, passing the file as
``argv[1]``.
"""

from __future__ import annotations

import datetime
import sys
import tempfile
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QStandardPaths
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from app.resources import resource_path
from app.ui.mainwindow import MainWindow


def _crash_log_path() -> Path:
    """Where uncaught-exception tracebacks are appended (user app-data dir)."""
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    if not base:
        base = tempfile.gettempdir()
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path / "heaviside-errors.log"


def _handle_uncaught(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
    """Last-chance handler for uncaught exceptions (sys/threading excepthook).

    Logs the traceback to the app-data dir, echoes it to stderr, and — when a
    QApplication exists and we are on the GUI thread — tells the user where the
    log is. It deliberately does **not** exit: the user keeps the session (and
    can save their work). The handler itself must never raise, so every step is
    individually guarded.
    """
    if isinstance(exc_type, type) and issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    try:
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    except Exception:  # noqa: BLE001
        text = f"{exc_type}: {exc_value}\n"
    log_path: Path | None = None
    try:
        log_path = _crash_log_path()
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.datetime.now().isoformat()} ---\n{text}")
    except Exception:  # noqa: BLE001
        log_path = None
    try:
        sys.stderr.write(text)
    except Exception:  # noqa: BLE001
        pass
    try:
        # A message box may only be shown from the GUI thread, and only once a
        # QApplication exists (an early failure has nowhere to show it).
        if (QApplication.instance() is not None
                and threading.current_thread() is threading.main_thread()):
            from PySide6.QtWidgets import QMessageBox
            where = f"\n\nDetails were written to:\n{log_path}" if log_path else ""
            QMessageBox.critical(
                None,
                "Internal Error",
                "Heaviside hit an internal error. You can keep working — "
                "saving your schematic now is a good idea." + where,
            )
    except Exception:  # noqa: BLE001
        pass


def _install_excepthooks() -> None:
    """Install the crash guard for the main thread and worker threads."""
    sys.excepthook = _handle_uncaught

    def _thread_hook(args) -> None:  # noqa: ANN001
        _handle_uncaught(args.exc_type, args.exc_value, args.exc_traceback)

    threading.excepthook = _thread_hook


def _schematic_arg(argv: list[str]) -> str | None:
    """The first existing ``.hv`` path among *argv* (the args after the program
    name), or ``None``. Tolerant of extra flags so an OS-passed file path is
    found regardless of position."""
    for arg in argv[1:]:
        if arg.lower().endswith(".hv") and Path(arg).is_file():
            return arg
    return None


class _FileOpenFilter(QObject):
    """Route macOS Finder "open document" events to the window.

    On macOS the OS delivers a double-clicked / "open with" ``.hv`` (the document
    type declared in ``heaviside.spec``) as a :class:`QFileOpenEvent`, **not** as
    a command-line argument — so the Windows argv path does not cover it. This
    application-level filter forwards such events to :meth:`MainWindow.load_path`,
    guarding unsaved work first (a no-op at launch, where nothing is modified)."""

    def __init__(self, window: MainWindow) -> None:
        super().__init__()
        self._window = window

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.FileOpen:
            path = event.file()
            if path and path.lower().endswith(".hv") and self._window._confirm_discard():
                self._window.load_path(path)
            return True
        return super().eventFilter(obj, event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Heaviside")
    app.setOrganizationName("Heaviside")

    # Crash guard: installed after the app name is set so the log lands in the
    # right app-data folder. An uncaught exception logs + informs, never exits.
    _install_excepthooks()

    icon_path = resource_path("assets", "icon.png")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = MainWindow()
    window.show()

    # Open a file passed on the command line (Windows .hv association / "open
    # with"). A fresh launch has no unsaved work, so no discard prompt is needed.
    path = _schematic_arg(sys.argv)
    if path is not None:
        window.load_path(path)

    # macOS delivers Finder document opens as QFileOpenEvents (kept referenced so
    # the filter outlives this scope).
    file_open_filter = _FileOpenFilter(window)
    app.installEventFilter(file_open_filter)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
