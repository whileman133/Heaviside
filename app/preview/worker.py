"""
Preview worker (spec §8.1).

PreviewWorker
-------------
A ``QThread``-backed ``QObject`` that compiles a CircuiTikZ source string to a
rendered ``QImage`` on a background thread so the main UI thread is never
blocked.

Typical usage::

    worker = PreviewWorker()
    worker.preview_ready.connect(self._on_preview_ready)
    worker.preview_error.connect(self._on_preview_error)
    worker.request_compile(circuitikz_source)

    # On application exit:
    worker.shutdown()

Debouncing
----------
``request_compile()`` is debounced: repeated calls within 1.5 seconds collapse
into a single compile run (spec §8.1).  A ``QTimer`` fires after the debounce
window and marshals the compile onto the worker thread via a queued signal.

Thread safety
-------------
``_source`` is written on the calling (main) thread and read on the worker
thread.  The ordering is safe: the write precedes the timer expiry, which in
turn precedes the queued signal dispatch to the worker thread.

EquationPreviewWorker
---------------------
A lightweight variant for per-slot equation previews in the Properties Panel
(spec §8.2) with a 500 ms debounce.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from app.preview.latex import CompileError, build_equation_tex, build_tex, compile_tex, pdf_to_qimage

# ---------------------------------------------------------------------------
# Schematic preview worker (spec §8.1)
# ---------------------------------------------------------------------------

_SCHEMATIC_DEBOUNCE_MS = 5000


class _SchematicCompileWorker(QObject):
    """Internal object that lives on the worker thread and does the actual work."""

    preview_ready = Signal(QImage)
    preview_error = Signal(str)
    compile_started = Signal()

    def __init__(self, dpi: int) -> None:
        super().__init__()
        self._dpi = dpi
        self.source: str = ""

    @Slot()
    def do_compile(self) -> None:
        """Called (via queued connection) on the worker thread."""
        self.compile_started.emit()
        try:
            tex = build_tex(self.source)
            pdf_bytes = compile_tex(tex)
            image = pdf_to_qimage(pdf_bytes, dpi=self._dpi)
            self.preview_ready.emit(image)
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

        self._thread = QThread()
        self._worker = _SchematicCompileWorker(dpi=dpi)
        self._worker.moveToThread(self._thread)

        # Forward signals from the internal worker to this object.
        self._worker.preview_ready.connect(self.preview_ready)
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

    def request_compile(self, circuitikz_source: str) -> None:
        """
        Schedule a compile with 1.5 s debounce.

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
        """Stop the background thread.  Call before the application exits."""
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


# ---------------------------------------------------------------------------
# Equation preview worker (spec §8.2)
# ---------------------------------------------------------------------------

_EQUATION_DEBOUNCE_MS = 500  # 500 ms


class _EquationCompileWorker(QObject):
    """Internal object that lives on the equation-preview worker thread."""

    preview_ready = Signal(QImage)
    preview_error = Signal()

    def __init__(self, dpi: int) -> None:
        super().__init__()
        self._dpi = dpi
        self.label: str = ""

    @Slot()
    def do_compile(self) -> None:
        if not self.label.strip():
            return
        try:
            tex = build_equation_tex(self.label)
            pdf_bytes = compile_tex(tex)
            image = pdf_to_qimage(pdf_bytes, dpi=self._dpi)
            self.preview_ready.emit(image)
        except CompileError:
            self.preview_error.emit()
        except Exception:
            import sys
            print(f"EquationPreviewWorker: unexpected error for label {self.label!r}",
                  file=sys.stderr)
            import traceback
            traceback.print_exc(file=sys.stderr)
            self.preview_error.emit()


class EquationPreviewWorker(QObject):
    """
    Compile a label string to a ``QImage`` on a background thread.

    Used by the Properties Panel for per-slot equation previews (spec §8.2).

    Signals
    -------
    preview_ready(QImage)
        Emitted on successful render.
    preview_error()
        Emitted when the LaTeX is invalid.  The UI should show a red border
        on the input field and no image.
    """

    preview_ready = Signal(QImage)
    preview_error = Signal()

    def __init__(self, parent: QObject | None = None, dpi: int = 120) -> None:
        super().__init__(parent)

        self._thread = QThread()
        self._worker = _EquationCompileWorker(dpi=dpi)
        self._worker.moveToThread(self._thread)

        self._worker.preview_ready.connect(self.preview_ready)
        self._worker.preview_error.connect(self.preview_error)

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_EQUATION_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._dispatch_compile)

        self._thread.start()

    def request_compile(self, label: str) -> None:
        """Schedule compilation of *label* with 500 ms debounce."""
        self._worker.label = label
        self._debounce_timer.start()

    def shutdown(self) -> None:
        """Stop the background thread."""
        self._debounce_timer.stop()
        self._thread.quit()
        self._thread.wait()

    def _dispatch_compile(self) -> None:
        from PySide6.QtCore import QMetaObject, Qt
        QMetaObject.invokeMethod(self._worker, "do_compile", Qt.ConnectionType.QueuedConnection)
