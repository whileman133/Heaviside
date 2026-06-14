"""
Preview worker (spec §8.1).

PreviewWorker
-------------
A ``QThread``-backed ``QObject`` that compiles a CircuiTikZ source string to PDF
on a background thread (so the heavy pdflatex run never blocks the UI), then
renders that PDF to a ``QImage`` **on the UI thread**. The QImage — the only Qt
object in the path — is deliberately built on the UI thread, never on the worker:
constructing Qt objects off the UI thread races the UI garbage collector and
corrupts the heap (PROJECT_SPEC §8.1).

Typical usage::

    worker = PreviewWorker()
    worker.preview_ready.connect(self._on_preview_ready)
    worker.preview_error.connect(self._on_preview_error)
    worker.request_compile(circuitikz_source)

    # On application exit:
    worker.shutdown()

Debouncing
----------
``request_compile()`` is debounced: repeated calls within the debounce window
collapse into a single compile run (spec §8.1).  A ``QTimer`` fires after the
debounce window and marshals the compile onto the worker thread via a queued
signal.

Thread safety
-------------
``_source`` is written on the calling (main) thread and read on the worker
thread.  The ordering is safe: the write precedes the timer expiry, which in
turn precedes the queued signal dispatch to the worker thread.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from app.preview.latex import CompileError, build_tex, compile_tex, pdf_to_qimage

# ---------------------------------------------------------------------------
# Schematic preview worker (spec §8.1)
# ---------------------------------------------------------------------------

# Auto-compile debounce. Kept short because the render step is now Qt-native
# (QtPdf, no external rasterizer), so a compile turns around quickly; the delay
# only needs to coalesce a burst of rapid edits, not hide a slow pipeline.
_SCHEMATIC_DEBOUNCE_MS = 500


class _SchematicCompileWorker(QObject):
    """Internal object that lives on the worker thread and does the actual work.

    It produces only the compiled **PDF bytes** (Qt-free); the ``QImage`` is
    rendered on the UI thread by :class:`PreviewWorker`. No Qt object is ever
    constructed on this worker thread — see the threading invariant in
    PROJECT_SPEC §8.1 (constructing Qt objects off the UI thread races the UI
    garbage collector and corrupts the heap)."""

    pdf_ready = Signal(bytes)        # compiled PDF; rendered to a QImage on the UI thread
    preview_error = Signal(str)
    compile_started = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.source: str = ""
        # Written on the main thread before a dispatch, read here on the worker
        # thread (same ordering guarantee as ``source``). Dark renders the
        # preview with a dark page; exports never go through this path.
        self.dark: bool = False

    @Slot()
    def do_compile(self) -> None:
        """Called (via queued connection) on the worker thread. Qt-free: compiles
        to PDF bytes; the QImage render happens on the UI thread."""
        self.compile_started.emit()
        try:
            tex = build_tex(self.source, dark=self.dark)
            self.pdf_ready.emit(compile_tex(tex))
        except CompileError as exc:
            self.preview_error.emit(exc.log or str(exc))
        except Exception as exc:  # noqa: BLE001
            self.preview_error.emit(str(exc))


class PreviewWorker(QObject):
    """
    Compile CircuiTikZ source to a ``QImage`` on a background thread.

    Signals (re-exported from the internal worker for convenience)
    -------
    preview_ready(QImage)
        The compiled schematic as a raster image.
    preview_error(str)
        Human-readable error string (may include pdflatex log output).
    compile_started()
        Fired just before compilation begins; use to show a spinner.
    """

    preview_ready = Signal(QImage)
    preview_error = Signal(str)
    compile_started = Signal()

    def __init__(self, parent: QObject | None = None, dpi: int = 150) -> None:
        super().__init__(parent)

        self._stopped = False
        self._dpi = dpi
        self._thread = QThread()
        self._worker = _SchematicCompileWorker()
        self._worker.moveToThread(self._thread)

        # Always stop the worker thread before the application exits, even if the
        # window's closeEvent never fires (e.g. app.quit(), or a teardown path
        # that bypasses the main window). Otherwise Qt warns/aborts with
        # "QThread: Destroyed while thread is still running".
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

        # Forward signals from the internal worker. The compiled PDF is rendered
        # to a QImage in _on_pdf_ready, which runs on THIS object's (UI) thread —
        # the pdf_ready connection crosses threads, so it is delivered queued. Qt
        # objects (the QImage) are therefore built on the UI thread, never on the
        # worker (PROJECT_SPEC §8.1).
        self._worker.pdf_ready.connect(self._on_pdf_ready)
        self._worker.preview_error.connect(self.preview_error)
        self._worker.compile_started.connect(self.compile_started)

        # Debounce timer lives on the main thread (the thread that creates
        # PreviewWorker).
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_SCHEMATIC_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._dispatch_compile)

        self._thread.start()

    # ------------------------------------------------------------------
    # Public API (main thread)
    # ------------------------------------------------------------------

    def set_dark(self, dark: bool) -> None:
        """Render subsequent previews with a dark page (preview only). Set on the
        main thread; takes effect on the next compile."""
        self._worker.dark = dark

    def request_compile(self, circuitikz_source: str) -> None:
        """
        Schedule a compile, debounced by ``_SCHEMATIC_DEBOUNCE_MS``.

        Repeated calls within the debounce window discard earlier sources and
        only compile the most recent one.
        """
        self._worker.source = circuitikz_source
        self._debounce_timer.start()

    def compile_now(self, circuitikz_source: str) -> None:
        """
        Compile immediately, bypassing the debounce timer.

        Used for explicit Compile button / Ctrl+Return triggers (spec §8.1).
        """
        self._debounce_timer.stop()
        self._worker.source = circuitikz_source
        self._dispatch_compile()

    def shutdown(self) -> None:
        """Stop the background thread.  Idempotent; runs on app quit and on
        the main window's closeEvent, whichever happens first."""
        if self._stopped:
            return
        self._stopped = True
        self._debounce_timer.stop()
        self._thread.quit()
        self._thread.wait()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _dispatch_compile(self) -> None:
        """
        Invoke ``_worker.do_compile()`` on the worker thread via a queued
        meta-call.  ``QMetaObject.invokeMethod`` with ``Qt.QueuedConnection``
        is the canonical way to call a slot on a different thread's object.
        """
        from PySide6.QtCore import QMetaObject, Qt  # local import avoids top-level Qt dep
        QMetaObject.invokeMethod(self._worker, "do_compile", Qt.ConnectionType.QueuedConnection)

    @Slot(bytes)
    def _on_pdf_ready(self, pdf_bytes: bytes) -> None:
        """Render the compiled PDF to a QImage and emit ``preview_ready``.

        Runs on the UI thread (this object's thread; the ``pdf_ready`` signal
        crosses from the worker thread queued), so the QImage — the only Qt object
        in the compile path — is never constructed on the worker (PROJECT_SPEC §8.1).
        """
        try:
            self.preview_ready.emit(pdf_to_qimage(pdf_bytes, dpi=self._dpi))
        except Exception as exc:  # noqa: BLE001
            self.preview_error.emit(str(exc))
