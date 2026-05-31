"""
MainWindow — the top-level application window (spec §10.1).

Layout::

    ┌─────────────────────────────────────────────────────────────┐
    │  Menu Bar: File | Edit | View | Help                        │
    ├─────────────────────────────────────────────────────────────┤
    │  Toolbar: New | Open | Save | | Undo | Redo | | Compile     │
    ├──────────┬──────────────────────────────┬───────────────────┤
    │ Palette  │         Canvas               │  Properties       │
    │          │    (QGraphicsView)           │  Panel            │
    │          │                              │                   │
    ├──────────┴──────────────────────────────┴───────────────────┤
    │  Source Panel (read-only CircuiTikZ)  [Copy]                │
    ├─────────────────────────────────────────────────────────────┤
    │  Status bar: cursor coords | zoom | compile status          │
    └─────────────────────────────────────────────────────────────┘

The preview is a floating overlay anchored to the bottom-right of the canvas.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QImage, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.canvas.scene import Mode, SchematicScene
from app.canvas.view import SchematicView
from app.codegen.circuitikz import generate
from app.preview.latex import check_dependencies
from app.preview.worker import PreviewWorker
from app.schematic.io import SchematicLoadError, load, save
from app.schematic.model import Schematic
from app.ui.palette import ComponentPalette
from app.ui.properties import PropertiesPanel
from app.ui.sourcepanel import SourcePanel

_WINDOW_TITLE = "Heaviside — CircuiTikZ Editor"


class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_WINDOW_TITLE)
        self.resize(1280, 800)

        # -- Core objects --------------------------------------------------
        self._scene = SchematicScene()
        self._view = SchematicView(self._scene)
        self._preview_worker = PreviewWorker(self, dpi=300)
        self._current_path: Path | None = None
        self._modified = False

        # -- Build UI -------------------------------------------------------
        self._build_menu()
        self._build_toolbar()
        self._build_central()
        self._build_statusbar()

        # -- Wire signals ---------------------------------------------------
        self._connect_signals()

        # -- Dependency warnings (non-blocking) ----------------------------
        self._check_and_warn_dependencies()

    # ------------------------------------------------------------------
    # Menu bar
    # ------------------------------------------------------------------

    def _build_menu(self) -> None:
        mb = self.menuBar()

        # File menu.
        file_menu = mb.addMenu("&File")

        self._act_new = QAction("&New", self)
        self._act_new.setShortcut(QKeySequence.New)
        self._act_new.triggered.connect(self._on_new)
        file_menu.addAction(self._act_new)

        self._act_open = QAction("&Open…", self)
        self._act_open.setShortcut(QKeySequence.Open)
        self._act_open.triggered.connect(self._on_open)
        file_menu.addAction(self._act_open)

        file_menu.addSeparator()

        self._act_save = QAction("&Save", self)
        self._act_save.setShortcut(QKeySequence.Save)
        self._act_save.triggered.connect(self._on_save)
        file_menu.addAction(self._act_save)

        self._act_save_as = QAction("Save &As…", self)
        self._act_save_as.setShortcut(QKeySequence("Ctrl+Shift+S"))
        self._act_save_as.triggered.connect(self._on_save_as)
        file_menu.addAction(self._act_save_as)

        file_menu.addSeparator()

        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        # Edit menu.
        edit_menu = mb.addMenu("&Edit")

        self._act_undo = QAction("&Undo", self)
        self._act_undo.setShortcut(QKeySequence.Undo)
        self._act_undo.triggered.connect(self._scene.undo)
        edit_menu.addAction(self._act_undo)

        self._act_redo = QAction("&Redo", self)
        self._act_redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        self._act_redo.triggered.connect(self._scene.redo)
        edit_menu.addAction(self._act_redo)

        edit_menu.addSeparator()

        act_select_all = QAction("Select &All", self)
        act_select_all.setShortcut(QKeySequence.SelectAll)
        act_select_all.triggered.connect(self._select_all)
        edit_menu.addAction(act_select_all)

        act_delete = QAction("&Delete", self)
        act_delete.setShortcut(QKeySequence.Delete)
        act_delete.triggered.connect(self._scene.delete_selected)
        edit_menu.addAction(act_delete)

        # View menu.
        view_menu = mb.addMenu("&View")

        act_fit = QAction("&Fit to Schematic", self)
        act_fit.setShortcut(QKeySequence("Ctrl+0"))
        act_fit.triggered.connect(self._view.fit_to_schematic)
        view_menu.addAction(act_fit)

        act_zoom_in = QAction("Zoom &In", self)
        act_zoom_in.setShortcut(QKeySequence("Ctrl++"))
        act_zoom_in.triggered.connect(self._view.zoom_in)
        view_menu.addAction(act_zoom_in)

        act_zoom_out = QAction("Zoom &Out", self)
        act_zoom_out.setShortcut(QKeySequence("Ctrl+-"))
        act_zoom_out.triggered.connect(self._view.zoom_out)
        view_menu.addAction(act_zoom_out)

        view_menu.addSeparator()

        act_compile = QAction("&Compile Preview", self)
        act_compile.setShortcut(QKeySequence("Ctrl+Return"))
        act_compile.triggered.connect(self._on_compile_now)
        view_menu.addAction(act_compile)

        # Help menu (placeholder).
        help_menu = mb.addMenu("&Help")
        act_about = QAction("&About Heaviside", self)
        act_about.triggered.connect(self._on_about)
        help_menu.addAction(act_about)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addAction(self._act_new)
        tb.addAction(self._act_open)
        tb.addAction(self._act_save)
        tb.addSeparator()
        tb.addAction(self._act_undo)
        tb.addAction(self._act_redo)
        tb.addSeparator()

        compile_btn = QAction("Compile", self)
        compile_btn.setShortcut(QKeySequence("Ctrl+Return"))
        compile_btn.triggered.connect(self._on_compile_now)
        tb.addAction(compile_btn)

    # ------------------------------------------------------------------
    # Central widget (three-panel + source strip)
    # ------------------------------------------------------------------

    def _build_central(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Horizontal splitter: palette | canvas+preview | properties.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)

        # Left: palette.
        self._palette = ComponentPalette()
        self._palette.set_scene(self._scene)
        splitter.addWidget(self._palette)

        # Centre: canvas + preview overlay.
        canvas_container = QWidget()
        canvas_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        canvas_layout = QVBoxLayout(canvas_container)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.addWidget(self._view)
        splitter.addWidget(canvas_container)

        # Right: properties.
        self._props = PropertiesPanel()
        self._props.set_scene(self._scene)
        splitter.addWidget(self._props)

        splitter.setStretchFactor(0, 0)   # palette: fixed
        splitter.setStretchFactor(1, 1)   # canvas: stretch
        splitter.setStretchFactor(2, 0)   # props: fixed

        outer.addWidget(splitter, 1)

        # Bottom: source panel.
        self._source_panel = SourcePanel()
        self._source_panel.set_scene(self._scene)
        outer.addWidget(self._source_panel)

        # Preview overlay (bottom-right of canvas container, floating).
        self._preview_overlay = _PreviewOverlay(canvas_container)
        self._preview_overlay.show()

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _build_statusbar(self) -> None:
        sb = QStatusBar()
        self.setStatusBar(sb)

        self._status_cursor = QLabel("(0.00, 0.00)")
        self._status_cursor.setMinimumWidth(120)
        sb.addWidget(self._status_cursor)

        sb.addWidget(_separator())

        self._status_zoom = QLabel("Zoom: 100%")
        self._status_zoom.setMinimumWidth(90)
        sb.addWidget(self._status_zoom)

        sb.addWidget(_separator())

        self._status_compile = QLabel("Ready")
        sb.addWidget(self._status_compile)

    # ------------------------------------------------------------------
    # Signal connections
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        # Scene → UI.
        self._scene.cursor_moved.connect(self._on_cursor_moved)
        self._scene.schematic_changed.connect(self._on_schematic_changed)
        self._scene.selection_changed_gu.connect(self._on_selection_changed)
        self._scene.component_double_clicked.connect(self._on_component_double_clicked)

        # View → status bar zoom.
        self._view.zoom_changed.connect(self._on_zoom_changed)

        # Preview worker → overlay.
        self._preview_worker.compile_started.connect(
            lambda: self._status_compile.setText("Compiling…")
        )
        self._preview_worker.preview_ready.connect(self._on_preview_ready)
        self._preview_worker.preview_error.connect(self._on_preview_error)

        # Auto-compile on schematic change (debounced inside worker).
        self._scene.schematic_changed.connect(self._on_auto_compile)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _select_all(self) -> None:
        """Select all components and wires on the canvas."""
        self._scene.enter_select_mode()
        for item in self._scene.items():
            item.setSelected(True)

    def _on_cursor_moved(self, x: float, y: float) -> None:
        self._status_cursor.setText(f"({x:.2f}, {y:.2f})")

    def _on_zoom_changed(self, zoom: float) -> None:
        self._status_zoom.setText(f"Zoom: {zoom * 100:.0f}%")

    def _on_schematic_changed(self) -> None:
        self._modified = True
        self._update_title()

    def _on_selection_changed(self, comp_ids: list[str]) -> None:
        if len(comp_ids) == 0:
            self._props.clear()
        elif len(comp_ids) == 1:
            self._props.show_component(comp_ids[0])
        else:
            self._props.show_multi_select(len(comp_ids))

    def _on_component_double_clicked(self, comp_id: str) -> None:
        self._props.show_component(comp_id)

    def _on_auto_compile(self) -> None:
        if self._scene.is_gesture_in_progress:
            return
        try:
            source = generate(self._scene.schematic, y_flip=True)
        except Exception:
            return
        self._preview_worker.request_compile(source)

    def _on_compile_now(self) -> None:
        try:
            source = generate(self._scene.schematic, y_flip=True)
        except Exception as exc:
            self._status_compile.setText(f"Error: {exc}")
            return
        self._preview_worker.compile_now(source)

    def _on_preview_ready(self, image: QImage) -> None:
        self._preview_overlay.set_image(image)
        self._status_compile.setText("Preview ready")

    def _on_preview_error(self, error: str) -> None:
        self._preview_overlay.set_error(error)
        first_line = error.split("\n")[0][:80]
        self._status_compile.setText(f"LaTeX error: {first_line}")

    # ------------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------------

    def _on_new(self) -> None:
        if not self._confirm_discard():
            return
        self._scene.set_schematic(Schematic(version="0.1", name="untitled"))
        self._current_path = None
        self._modified = False
        self._update_title()
        self._preview_overlay.clear()
        self._status_compile.setText("Ready")

    def _on_open(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Schematic", "", "Heaviside Schematics (*.ctikz);;All Files (*)"
        )
        if not path:
            return
        try:
            schematic = load(path)
        except SchematicLoadError as exc:
            QMessageBox.critical(self, "Load Error", str(exc))
            return
        self._scene.set_schematic(schematic)
        self._current_path = Path(path)
        self._modified = False
        self._update_title()
        self._props.clear()
        self._status_compile.setText("Ready")

    def _on_save(self) -> None:
        if self._current_path is None:
            self._on_save_as()
        else:
            self._do_save(self._current_path)

    def _on_save_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Schematic", "", "Heaviside Schematics (*.ctikz);;All Files (*)"
        )
        if not path:
            return
        if not path.endswith(".ctikz"):
            path += ".ctikz"
        self._do_save(Path(path))

    def _do_save(self, path: Path) -> None:
        try:
            save(self._scene.schematic, path)
        except OSError as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return
        self._current_path = path
        self._modified = False
        self._update_title()

    def _confirm_discard(self) -> bool:
        """Return True if it is safe to discard the current document."""
        if not self._modified:
            return True
        result = QMessageBox.question(
            self,
            "Unsaved changes",
            "The current schematic has unsaved changes. Discard them?",
            QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return result == QMessageBox.Discard

    def _update_title(self) -> None:
        name = self._current_path.stem if self._current_path else "untitled"
        mod = " •" if self._modified else ""
        self.setWindowTitle(f"{_WINDOW_TITLE} — {name}{mod}")

    # ------------------------------------------------------------------
    # About
    # ------------------------------------------------------------------

    def _on_about(self) -> None:
        QMessageBox.about(
            self,
            "About Heaviside",
            "<b>Heaviside</b><br>"
            "A graphical editor for CircuiTikZ circuit diagrams.<br><br>"
            "Exports valid CircuiTikZ LaTeX source for use in research papers.",
        )

    # ------------------------------------------------------------------
    # Dependency check
    # ------------------------------------------------------------------

    def _check_and_warn_dependencies(self) -> None:
        warnings = check_dependencies()
        if warnings:
            msg = "\n".join(f"• {w}" for w in warnings)
            QMessageBox.warning(
                self,
                "Missing Dependencies",
                f"Some preview features may be unavailable:\n\n{msg}",
            )

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        if not self._confirm_discard():
            event.ignore()
            return
        self._preview_worker.shutdown()
        self._props.shutdown()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _separator() -> QWidget:
    """A thin vertical separator for the status bar."""
    sep = QWidget()
    sep.setFixedWidth(1)
    sep.setStyleSheet("background: #ccc;")
    return sep


class _PreviewOverlay(QWidget):
    """
    Floating preview image in the bottom-right corner of the canvas.

    Shows the compiled PDF page at native sharpness (Retina-aware), capped
    so it never exceeds half the canvas width/height.
    """

    # Maximum logical-pixel dimensions for the overlay.
    _MAX_LOGICAL_W = 480
    _MAX_LOGICAL_H = 360

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setStyleSheet(
            "background: white; border: 1px solid #bbb; border-radius: 4px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._img_label)

        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: red; font-size: 10px;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        self._reposition()

    def resizeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().resizeEvent(event)
        self._reposition()

    def _reposition(self) -> None:
        if self.parent():
            p = self.parent()
            pw, ph = p.width(), p.height()
            self.move(pw - self.width() - 8, ph - self.height() - 8)

    def set_image(self, image: QImage) -> None:
        self._error_label.hide()
        pix = QPixmap.fromImage(image)

        # The QImage was rendered at `dpi` dots per inch.  On a Retina (HiDPI)
        # display the device pixel ratio is 2, so we must tell Qt that each
        # physical pixel in this pixmap corresponds to 0.5 logical pixels —
        # otherwise Qt will stretch the image to twice its intended size.
        dpr = self.devicePixelRatioF()
        pix.setDevicePixelRatio(dpr)

        # Logical size of the image (what the user sees, independent of DPI).
        logical_w = pix.width() / dpr
        logical_h = pix.height() / dpr

        # Cap to _MAX_LOGICAL_W × _MAX_LOGICAL_H, keeping aspect ratio.
        scale = min(
            self._MAX_LOGICAL_W / max(logical_w, 1),
            self._MAX_LOGICAL_H / max(logical_h, 1),
            1.0,  # never upscale
        )
        display_w = int(logical_w * scale)
        display_h = int(logical_h * scale)

        # Scale the physical pixmap so it looks right at the capped logical size.
        physical_w = int(display_w * dpr)
        physical_h = int(display_h * dpr)
        scaled_pix = pix.scaled(
            physical_w, physical_h,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        scaled_pix.setDevicePixelRatio(dpr)

        self._img_label.setPixmap(scaled_pix)
        self._img_label.setFixedSize(display_w, display_h)
        self.adjustSize()
        self._reposition()
        self.show()

    def set_error(self, error: str) -> None:
        self._img_label.clear()
        self._error_label.setText(error[:400])
        self._error_label.show()
        self.adjustSize()
        self._reposition()
        self.show()

    def clear(self) -> None:
        self._img_label.clear()
        self._error_label.hide()
