"""
MainWindow — the top-level application window (spec §10.1).

Layout::

    ┌─────────────────────────────────────────────────────────────┐
    │  Menu Bar: File | Edit | View | Help                        │
    ├─────────────────────────────────────────────────────────────┤
    │  Toolbar: New | Open | Save | | Undo | Redo | | Compile     │
    ├────┬─────────┬──────────────────────────────┬──────────────┤
    │Tool│ Palette │         Canvas               │  Properties  │
    │Rib-│         │    (QGraphicsView)           │  Panel       │
    │bon │         │                              │              │
    ├────┴─────────┴──────────────────┬───────────┴──────────────┤
    │  Source Panel (CircuiTikZ)      │  LaTeX Preview           │
    ├─────────────────────────────────┴──────────────────────────┤
    │  Status bar: cursor coords | zoom | compile status          │
    └─────────────────────────────────────────────────────────────┘

The preview occupies the lower-right of the bottom strip, beside the source.
"""

from __future__ import annotations

from pathlib import Path

import qtawesome as qta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QFont, QImage, QKeySequence,
    QPainter, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
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

from app.resources import resource_path
from app.canvas.scene import Mode, SchematicScene  # noqa: F401 (Mode used in type hints)
from app.canvas.view import SchematicView
from app.codegen.circuitikz import generate
from app.preview.latex import (
    CompileError,
    build_snippet,
    build_tex,
    check_dependencies,
    compile_tex,
    pdf_to_eps,
)
from app.preview.worker import PreviewWorker
from app.schematic.io import SchematicLoadError, load, save
from app.schematic.model import Schematic
from app.ui.palette import ComponentPalette
from app.ui.preferences import Preferences, PreferencesDialog
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
        self._prefs = Preferences()
        self._scene.set_mark_unconnected_pins(self._prefs.mark_unconnected_pins)

        # -- Build UI -------------------------------------------------------
        self._build_menu()
        self._build_toolbar()
        self._build_tool_ribbon()
        self._build_central()
        self._build_statusbar()

        # -- Window-level Escape: cancel placement/wire regardless of focus ----
        # The view's keyPressEvent also handles Escape when the view has focus,
        # but clicking a palette entry shifts focus to the palette widget.  A
        # window-level QShortcut fires regardless of which child widget is focused.
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WindowShortcut)
        esc.activated.connect(self._scene.cancel_current)

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

        self._act_export_tex = QAction("&Export to TeX…", self)
        self._act_export_tex.setShortcut(QKeySequence("Ctrl+E"))
        self._act_export_tex.triggered.connect(self._on_export_tex)
        file_menu.addAction(self._act_export_tex)

        self._act_export_pdf = QAction("Export to &PDF…", self)
        self._act_export_pdf.triggered.connect(self._on_export_pdf)
        file_menu.addAction(self._act_export_pdf)

        self._act_export_eps = QAction("Export to E&PS…", self)
        self._act_export_eps.triggered.connect(self._on_export_eps)
        file_menu.addAction(self._act_export_eps)

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

        self._act_copy = QAction("&Copy", self)
        self._act_copy.setShortcut(QKeySequence.Copy)
        self._act_copy.triggered.connect(self._scene.copy_selection)
        edit_menu.addAction(self._act_copy)

        self._act_paste = QAction("&Paste", self)
        self._act_paste.setShortcut(QKeySequence.Paste)
        self._act_paste.triggered.connect(self._scene.paste)
        edit_menu.addAction(self._act_paste)

        edit_menu.addSeparator()

        act_select_all = QAction("Select &All", self)
        act_select_all.setShortcut(QKeySequence.SelectAll)
        act_select_all.triggered.connect(self._select_all)
        edit_menu.addAction(act_select_all)

        act_delete = QAction("&Delete", self)
        act_delete.setShortcut(QKeySequence.Delete)
        act_delete.triggered.connect(self._scene.delete_selected)
        edit_menu.addAction(act_delete)

        edit_menu.addSeparator()

        self._act_preferences = QAction("&Preferences…", self)
        self._act_preferences.setShortcut(QKeySequence("Ctrl+,"))
        # On macOS this role relocates the item to the application menu.
        self._act_preferences.setMenuRole(QAction.PreferencesRole)
        self._act_preferences.triggered.connect(self._on_preferences)
        edit_menu.addAction(self._act_preferences)

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
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.setStyleSheet(
            "QToolBar { background: #ebebeb; border: none; spacing: 2px; }"
            "QToolButton { background: transparent; border: none; border-radius: 4px; padding: 3px; }"
            "QToolButton:hover { background: palette(midlight); }"
            "QToolButton:pressed { background: palette(mid); }"
        )
        self.addToolBar(tb)

        self._act_new.setIcon(qta.icon("fa5s.file"))
        self._act_open.setIcon(qta.icon("fa5s.folder-open"))
        self._act_save.setIcon(qta.icon("fa5s.save"))
        self._act_undo.setIcon(qta.icon("fa5s.undo"))
        self._act_redo.setIcon(qta.icon("fa5s.redo"))

        tb.addAction(self._act_new)
        tb.addAction(self._act_open)
        tb.addAction(self._act_save)
        tb.addSeparator()
        tb.addAction(self._act_undo)
        tb.addAction(self._act_redo)
        tb.addSeparator()

        compile_btn = QAction(qta.icon("fa5s.play"), "Compile", self)
        compile_btn.setShortcut(QKeySequence("Ctrl+Return"))
        compile_btn.triggered.connect(self._on_compile_now)
        tb.addAction(compile_btn)

        for action in (self._act_new, self._act_open, self._act_save,
                       self._act_undo, self._act_redo, compile_btn):
            btn = tb.widgetForAction(action)
            if btn:
                btn.setCursor(Qt.PointingHandCursor)

    # ------------------------------------------------------------------
    # Tool ribbon (left vertical strip: Select | Wire | Pan)
    # ------------------------------------------------------------------

    def _build_tool_ribbon(self) -> None:
        ribbon = QToolBar("Tools")
        ribbon.setMovable(False)
        ribbon.setToolButtonStyle(Qt.ToolButtonIconOnly)
        ribbon.setIconSize(QSize(22, 22))
        ribbon.setStyleSheet(
            "QToolBar { background: #ebebeb; border: none; spacing: 2px; padding: 4px 2px; }"
            "QToolButton { background: transparent; border: none; border-radius: 4px; padding: 3px;"
            "              min-width: 32px; min-height: 32px; }"
            "QToolButton:hover { background: palette(midlight); }"
            "QToolButton:pressed { background: palette(mid); }"
            "QToolButton:checked { background: palette(highlight); color: palette(highlighted-text); }"
            "QToolButton:checked:hover { background: palette(highlight); }"
        )
        self.addToolBar(Qt.LeftToolBarArea, ribbon)

        group = QActionGroup(self)
        group.setExclusive(True)

        self._tool_select = QAction(qta.icon("fa5s.mouse-pointer"), "Select", self)
        self._tool_select.setToolTip("Select  [S / Esc]")
        self._tool_select.setCheckable(True)
        self._tool_select.setChecked(True)
        self._tool_select.triggered.connect(self._scene.enter_select_mode)
        group.addAction(self._tool_select)
        ribbon.addAction(self._tool_select)

        self._tool_wire = QAction(qta.icon("fa5s.pen"), "Wire", self)
        self._tool_wire.setToolTip("Wire  [W]")
        self._tool_wire.setCheckable(True)
        self._tool_wire.triggered.connect(self._scene.enter_wire_mode)
        group.addAction(self._tool_wire)
        ribbon.addAction(self._tool_wire)

        self._tool_pan = QAction(qta.icon("fa5s.hand-paper"), "Pan", self)
        self._tool_pan.setToolTip("Pan  [P / Space+drag]")
        self._tool_pan.setCheckable(True)
        self._tool_pan.triggered.connect(self._scene.enter_pan_mode)
        group.addAction(self._tool_pan)
        ribbon.addAction(self._tool_pan)

        for action in (self._tool_select, self._tool_wire, self._tool_pan):
            btn = ribbon.widgetForAction(action)
            if btn:
                btn.setCursor(Qt.PointingHandCursor)

        self._scene.mode_changed.connect(self._on_mode_changed_ribbon)

    def _on_mode_changed_ribbon(self, mode: Mode) -> None:
        self._tool_select.setChecked(mode == Mode.SELECT)
        self._tool_wire.setChecked(mode == Mode.WIRE)
        self._tool_pan.setChecked(mode == Mode.PAN)

    # ------------------------------------------------------------------
    # Central widget (three-panel + source strip)
    # ------------------------------------------------------------------

    def _build_central(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Horizontal splitter: palette | canvas | properties.
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(4)

        # Left: palette.
        self._palette = ComponentPalette()
        self._palette.set_scene(self._scene)
        splitter.addWidget(self._palette)

        # Centre: stacked widget — welcome screen (page 0) / canvas (page 1).
        from PySide6.QtWidgets import QStackedWidget
        self._canvas_stack = QStackedWidget()
        self._canvas_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._canvas_stack.addWidget(_WelcomeScreen())   # index 0
        self._canvas_stack.addWidget(self._view)          # index 1
        self._canvas_stack.setCurrentIndex(0)
        splitter.addWidget(self._canvas_stack)

        # Right: properties.
        self._props = PropertiesPanel()
        self._props.set_scene(self._scene)
        splitter.addWidget(self._props)

        splitter.setStretchFactor(0, 0)   # palette: fixed
        splitter.setStretchFactor(1, 1)   # canvas: stretch
        splitter.setStretchFactor(2, 0)   # props: fixed

        outer.addWidget(splitter, 1)

        # Bottom strip: source panel (left) + preview panel (right), in a
        # draggable splitter. The CircuiTikZ source lines are short, so the
        # preview gets the larger initial share of the width; the user can drag
        # the handle to rebalance.
        bottom = QWidget()
        bottom.setFixedHeight(260)
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(0, 0, 0, 0)
        bottom_layout.setSpacing(0)

        bottom_split = QSplitter(Qt.Horizontal)
        bottom_split.setHandleWidth(4)
        bottom_split.setChildrenCollapsible(False)

        self._source_panel = SourcePanel(preferences=self._prefs)
        self._source_panel.set_scene(self._scene)
        bottom_split.addWidget(self._source_panel)

        self._preview_panel = _PreviewPanel()
        bottom_split.addWidget(self._preview_panel)

        # Source stays only as wide as it needs; preview takes the extra room.
        bottom_split.setStretchFactor(0, 0)
        bottom_split.setStretchFactor(1, 1)
        bottom_split.setSizes([440, 840])

        bottom_layout.addWidget(bottom_split)
        outer.addWidget(bottom)

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
        # Welcome screen → canvas transitions (one-way; never go back to welcome).
        # Triggered by: File → New, File → Open, or clicking a palette item.
        self._scene.mode_changed.connect(self._on_mode_changed)

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

    def _on_mode_changed(self, mode: Mode) -> None:
        if mode == Mode.PLACE:
            self._show_canvas()
            # Clicking a palette entry moves keyboard focus to the palette widget;
            # restore it to the view so R, X, Escape, etc. work immediately.
            self._view.setFocus()

    def _show_canvas(self) -> None:
        """Switch the centre pane from the welcome screen to the live canvas."""
        self._canvas_stack.setCurrentIndex(1)

    def _on_schematic_changed(self) -> None:
        self._modified = True
        self._update_title()
        # Keep the properties panel in sync when a single component is selected
        # (e.g. after an in-place options edit that doesn't change the selection).
        comp_ids = self._scene.selected_component_ids()
        if len(comp_ids) == 1:
            self._props.show_component(comp_ids[0])

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
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins)
        except Exception:
            return
        self._preview_worker.request_compile(source)

    def _on_compile_now(self) -> None:
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins)
        except Exception as exc:
            self._status_compile.setText(f"Error: {exc}")
            return
        self._preview_worker.compile_now(source)

    def _on_preview_ready(self, image: QImage) -> None:
        self._preview_panel.set_image(image)
        self._status_compile.setText("Preview ready")

    def _on_preview_error(self, error: str) -> None:
        self._preview_panel.set_error(error)
        first_line = error.split("\n")[0][:80]
        self._status_compile.setText(f"LaTeX error: {first_line}")

    # ------------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------------

    def _on_new(self) -> None:
        if not self._confirm_discard():
            return
        self._show_canvas()
        self._scene.set_schematic(Schematic(version="0.1", name="untitled"))
        self._current_path = None
        self._modified = False
        self._update_title()
        self._preview_panel.clear()
        self._status_compile.setText("Ready")

    def _on_open(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Schematic", "", "Heaviside Schematics (*.hv);;All Files (*)"
        )
        if not path:
            return
        try:
            schematic = load(path)
        except SchematicLoadError as exc:
            QMessageBox.critical(self, "Load Error", str(exc))
            return
        self._show_canvas()
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
            self, "Save Schematic", "", "Heaviside Schematics (*.hv);;All Files (*)"
        )
        if not path:
            return
        if not path.endswith(".hv"):
            path += ".hv"
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
        self._auto_export(path)

    def _auto_export(self, path: Path) -> None:
        """Write sibling PDF/EPS images next to *path* if enabled in Preferences.

        Runs after a successful save so an ``\\includegraphics`` of the sibling
        file stays in sync with the schematic (§10.8).  Compilation failures are
        reported in the status bar only — a modal dialog on every save would be
        intrusive — and never block the save itself.
        """
        want_pdf = self._prefs.auto_export_pdf
        want_eps = self._prefs.auto_export_eps
        if not (want_pdf or want_eps):
            return

        self._status_compile.setText("Auto-exporting…")
        pdf_bytes = self._compile_to_pdf(quiet=True)
        if pdf_bytes is None:
            self._status_compile.setText("Auto-export failed (see Compile)")
            return

        written: list[str] = []
        try:
            if want_pdf:
                pdf_path = path.with_suffix(".pdf")
                pdf_path.write_bytes(pdf_bytes)
                written.append(pdf_path.name)
            if want_eps:
                eps_path = path.with_suffix(".eps")
                eps_path.write_bytes(pdf_to_eps(pdf_bytes))
                written.append(eps_path.name)
        except (OSError, CompileError) as exc:
            self._status_compile.setText(f"Auto-export failed: {exc}")
            return

        self._status_compile.setText("Auto-exported " + ", ".join(written))

    def _on_preferences(self) -> None:
        """Open the modal Preferences dialog (§10.8).

        On accept, refresh the source panel and recompile the preview so a
        display change (e.g. marking unconnected pins) is reflected immediately.
        """
        if PreferencesDialog(self._prefs, self).exec() == QDialog.Accepted:
            self._scene.set_mark_unconnected_pins(self._prefs.mark_unconnected_pins)
            self._source_panel.refresh()
            self._on_auto_compile()

    def _on_export_tex(self) -> None:
        """Export the schematic as an includable CircuiTikZ ``.tex`` snippet.

        The snippet uses ``y_flip=True`` so the included figure renders in the
        same orientation as the canvas (see §8.5).
        """
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins)
        except Exception as exc:
            QMessageBox.critical(self, "Export Error", f"Cannot generate source:\n{exc}")
            return

        default_name = (self._current_path.stem if self._current_path else "untitled") + ".tex"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to TeX", default_name, "LaTeX Source (*.tex);;All Files (*)"
        )
        if not path:
            return
        if not path.endswith(".tex"):
            path += ".tex"
        try:
            Path(path).write_text(build_snippet(source), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            return
        self._status_compile.setText(f"Exported to {Path(path).name}")

    def _compile_to_pdf(self, *, quiet: bool = False) -> bytes | None:
        """Generate source and compile it to PDF bytes for image export.

        Returns the PDF bytes, or None on failure (invalid schematic or
        ``pdflatex`` error).  When *quiet* is False, failures raise a modal
        error dialog; when True (auto-export on save), they are silent so the
        caller can report via the status bar instead.
        """
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins)
        except Exception as exc:
            if not quiet:
                QMessageBox.critical(self, "Export Error", f"Cannot generate source:\n{exc}")
            return None
        try:
            return compile_tex(build_tex(source))
        except CompileError as exc:
            if not quiet:
                detail = f"{exc}\n\n{exc.log}".strip() if exc.log else str(exc)
                QMessageBox.critical(self, "Export Error", detail[:1000])
            return None

    def _on_export_pdf(self) -> None:
        """Export the schematic as a compiled PDF image (§8.6)."""
        self._status_compile.setText("Compiling…")
        pdf_bytes = self._compile_to_pdf()
        if pdf_bytes is None:
            self._status_compile.setText("Export failed")
            return
        default_name = (self._current_path.stem if self._current_path else "untitled") + ".pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to PDF", default_name, "PDF Document (*.pdf);;All Files (*)"
        )
        if not path:
            return
        if not path.endswith(".pdf"):
            path += ".pdf"
        try:
            Path(path).write_bytes(pdf_bytes)
        except OSError as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            return
        self._status_compile.setText(f"Exported to {Path(path).name}")

    def _on_export_eps(self) -> None:
        """Export the schematic as an EPS image (compile then convert, §8.6)."""
        self._status_compile.setText("Compiling…")
        pdf_bytes = self._compile_to_pdf()
        if pdf_bytes is None:
            self._status_compile.setText("Export failed")
            return
        try:
            eps_bytes = pdf_to_eps(pdf_bytes)
        except CompileError as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            self._status_compile.setText("Export failed")
            return
        default_name = (self._current_path.stem if self._current_path else "untitled") + ".eps"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to EPS", default_name, "EPS Image (*.eps);;All Files (*)"
        )
        if not path:
            return
        if not path.endswith(".eps"):
            path += ".eps"
        try:
            Path(path).write_bytes(eps_bytes)
        except OSError as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            return
        self._status_compile.setText(f"Exported to {Path(path).name}")

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
        _AboutDialog(self).exec()

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
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Welcome overlay
# ---------------------------------------------------------------------------

_ROW_H = 18.0   # height of one reference-table row

# Keyboard-shortcut reference: (keys, action).
# Left column: canvas tools and editing; right column: file / view commands.
_SHORTCUTS_LEFT = [
    ("S",       "Select tool"),
    ("W",       "Wire tool"),
    ("P",       "Pan / Space-drag"),
    ("R",       "Rotate 90° CW"),
    ("Del",     "Delete selection"),
    ("Arrows",  "Nudge 0.5 units"),
    ("Ctrl+A",  "Select all"),
    ("Esc",     "Cancel / deselect"),
]
_SHORTCUTS_RIGHT = [
    ("Ctrl+N",        "New schematic"),
    ("Ctrl+O",        "Open"),
    ("Ctrl+S",        "Save"),
    ("Ctrl+E",        "Export to TeX"),
    ("Ctrl+Z",        "Undo"),
    ("Ctrl+Shift+Z",  "Redo"),
    ("Ctrl+0",        "Fit to view"),
    ("Ctrl+Return",   "Compile preview"),
]

# Colours for the welcome screen
_C_BG    = QColor(245, 247, 250)        # solid background
_C_STEP  = QColor( 80, 120, 175, 200)   # step-function line
_C_AXIS  = QColor(160, 175, 190, 180)   # axis lines
_C_LABEL = QColor(100, 130, 170, 210)   # H(t) / 1 / t annotations
_C_TITLE = QColor( 60,  80, 110, 220)   # section headers
_C_HINT  = QColor(120, 140, 165, 200)   # hint lines


class _WelcomeScreen(QWidget):
    """
    Solid welcome screen shown in the canvas slot before any document is
    active.  Draws the Heaviside unit step function H(t) as a centred diagram,
    followed by a keyboard-shortcut list.

    Replaced by the live SchematicView (via QStackedWidget) as soon as the
    user creates/opens a document or begins component placement.
    """

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w, h = float(self.width()), float(self.height())
        painter.fillRect(self.rect(), _C_BG)

        # ---- layout constants ------------------------------------------
        step_w  = min(w * 0.38, 230.0)
        step_h  = min(h * 0.20, 110.0)
        n1 = max(len(_SHORTCUTS_LEFT), len(_SHORTCUTS_RIGHT))
        # A reference block is: header (16) + sep gap (10) + n rows.
        diagram_h    = step_h * 1.6
        section_gap  = 22.0
        block1_h     = 26.0 + n1 * _ROW_H
        block_h = diagram_h + section_gap + block1_h
        top     = max(12.0, (h - block_h) / 2.0)
        cx   = w / 2.0

        # ---- step function ---------------------------------------------
        step_cy  = top + step_h * 0.8
        zero_y   = step_cy + step_h / 2
        one_y    = step_cy - step_h / 2
        left_x   = cx - step_w / 2
        right_x  = cx + step_w / 2
        origin_x = cx

        ax_pen = QPen(_C_AXIS, 1.2, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(ax_pen)
        painter.drawLine(QPointF(left_x - 10, zero_y), QPointF(right_x + 20, zero_y))
        painter.drawLine(QPointF(origin_x, zero_y + 12), QPointF(origin_x, one_y - 20))
        _arrow_right(painter, _C_AXIS, QPointF(right_x + 20, zero_y), size=7)
        _arrow_up   (painter, _C_AXIS, QPointF(origin_x, one_y - 20), size=7)
        painter.drawLine(QPointF(origin_x - 5, one_y), QPointF(origin_x + 5, one_y))

        step_pen = QPen(_C_STEP, 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(step_pen)
        painter.drawLine(QPointF(left_x,   zero_y), QPointF(origin_x, zero_y))
        painter.drawLine(QPointF(origin_x, zero_y), QPointF(origin_x, one_y))
        painter.drawLine(QPointF(origin_x, one_y),  QPointF(right_x,  one_y))
        _open_dot  (painter, _C_STEP, QPointF(origin_x, zero_y), r=4.5)
        _filled_dot(painter, _C_STEP, QPointF(origin_x, one_y),  r=4.5)

        ann_font = QFont()
        ann_font.setPointSizeF(11.5)
        ann_font.setItalic(True)
        painter.setFont(ann_font)
        painter.setPen(QPen(_C_LABEL))
        painter.drawText(QPointF(right_x + 8, one_y + 5), "H(t)")
        ann_font.setItalic(False)
        ann_font.setPointSizeF(10.5)
        painter.setFont(ann_font)
        painter.drawText(QPointF(origin_x - 15, one_y + 5),  "1")
        painter.drawText(QPointF(right_x + 22,  zero_y + 5), "t")

        # ---- reference blocks ------------------------------------------
        y = top + diagram_h
        self._draw_ref_block(
            painter, "KEYBOARD SHORTCUTS",
            _SHORTCUTS_LEFT, _SHORTCUTS_RIGHT, y + section_gap, w, cx,
            code_frac=0.42,
        )

    def _draw_ref_block(
        self,
        painter: QPainter,
        title: str,
        left: list,
        right: list,
        y: float,
        w: float,
        cx: float,
        code_frac: float = 0.5,
    ) -> float:
        """Draw a titled two-column reference table starting at *y*.

        Returns the y coordinate just below the last row, so the caller can
        stack the next block beneath it.
        """
        # Section header (centred).
        hdr_font = QFont()
        hdr_font.setPointSizeF(8.5)
        hdr_font.setWeight(QFont.DemiBold)
        hdr_font.setLetterSpacing(QFont.AbsoluteSpacing, 1.0)
        painter.setFont(hdr_font)
        painter.setPen(QPen(_C_TITLE))
        painter.drawText(QRectF(0, y, w, 14), Qt.AlignHCenter | Qt.AlignTop, title)

        # Thin separator.
        sep_y = y + 16
        margin = max(20.0, (w - 520) / 2)
        painter.setPen(QPen(QColor(180, 195, 210, 140), 0.75))
        painter.drawLine(QPointF(margin, sep_y), QPointF(w - margin, sep_y))

        # Two-column rows: monospaced code on the left of each column, plain
        # description on the right.
        code_font = QFont("Menlo")
        code_font.setStyleHint(QFont.TypeWriter)
        code_font.setPointSizeF(8.5)
        desc_font = QFont()
        desc_font.setPointSizeF(8.5)

        col_w = min(250.0, (w - 2 * margin) / 2)
        lx = cx - col_w               # left column x start
        rx = cx + 4                   # right column x start
        desc_x = code_frac + 0.02     # description starts just after the code
        row_y = sep_y + 10
        n_rows = max(len(left), len(right))

        for i in range(n_rows):
            for col_data, x0 in ((left, lx), (right, rx)):
                if i >= len(col_data):
                    continue
                code, desc = col_data[i]
                painter.setFont(code_font)
                painter.setPen(QPen(_C_STEP))
                painter.drawText(QRectF(x0, row_y, col_w * code_frac, _ROW_H),
                                 Qt.AlignLeft | Qt.AlignVCenter, code)
                painter.setFont(desc_font)
                painter.setPen(QPen(_C_HINT))
                painter.drawText(QRectF(x0 + col_w * desc_x, row_y, col_w * (1 - desc_x), _ROW_H),
                                 Qt.AlignLeft | Qt.AlignVCenter, desc)
            row_y += _ROW_H
        return row_y


# ---------------------------------------------------------------------------
# Small painter helpers used by _WelcomeScreen
# ---------------------------------------------------------------------------

def _arrow_right(painter: QPainter, color: QColor, tip: QPointF, size: float) -> None:
    pen = QPen(color, 1.0)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawLine(tip, QPointF(tip.x() - size, tip.y() - size * 0.5))
    painter.drawLine(tip, QPointF(tip.x() - size, tip.y() + size * 0.5))


def _arrow_up(painter: QPainter, color: QColor, tip: QPointF, size: float) -> None:
    pen = QPen(color, 1.0)
    pen.setCapStyle(Qt.RoundCap)
    painter.setPen(pen)
    painter.drawLine(tip, QPointF(tip.x() - size * 0.5, tip.y() + size))
    painter.drawLine(tip, QPointF(tip.x() + size * 0.5, tip.y() + size))


def _open_dot(painter: QPainter, color: QColor, centre: QPointF, r: float) -> None:
    painter.setPen(QPen(color, 1.8))
    painter.setBrush(Qt.NoBrush)
    painter.drawEllipse(centre, r, r)


def _filled_dot(painter: QPainter, color: QColor, centre: QPointF, r: float) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawEllipse(centre, r, r)
    painter.setBrush(Qt.NoBrush)


# ---------------------------------------------------------------------------
# About dialog
# ---------------------------------------------------------------------------

_APP_VERSION = "0.1.0"
_ASSETS_DIR = resource_path("assets")

_HEAVISIDE_QUOTE = (
    "“The best result of mathematics is to be able to do without it.”"
    "\n— Oliver Heaviside"
)


class _AboutDialog(QDialog):
    """Custom About dialog with logo, version, authors, and quote."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About Heaviside")
        self.setFixedWidth(400)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(0)

        # Logo
        logo_path = _ASSETS_DIR / "icon.png"
        if logo_path.exists():
            pix = QPixmap(str(logo_path))
            dpr = self.devicePixelRatioF()
            size = int(96 * dpr)
            pix = pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
            logo = QLabel()
            logo.setPixmap(pix)
            logo.setAlignment(Qt.AlignCenter)
            layout.addWidget(logo)
            layout.addSpacing(12)

        # App name
        name_label = QLabel("Heaviside")
        name_label.setAlignment(Qt.AlignCenter)
        name_label.setStyleSheet("font-size: 22px; font-weight: bold;")
        layout.addWidget(name_label)

        # Subtitle + version
        sub_label = QLabel(
            f"CircuiTikZ Schematic Editor  ·  v{_APP_VERSION}"
        )
        sub_label.setAlignment(Qt.AlignCenter)
        sub_label.setStyleSheet("font-size: 12px; color: #666;")
        layout.addWidget(sub_label)

        layout.addSpacing(16)

        # Author + affiliation
        authors_label = QLabel("Wesley Hileman · University of Colorado Colorado Springs")
        authors_label.setWordWrap(True)
        authors_label.setAlignment(Qt.AlignCenter)
        authors_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(authors_label)

        layout.addSpacing(6)

        claude_label = QLabel("Built with the assistance of Claude")
        claude_label.setAlignment(Qt.AlignCenter)
        claude_label.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(claude_label)

        layout.addSpacing(20)

        # Divider
        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: #ddd;")
        layout.addWidget(divider)

        layout.addSpacing(16)

        # Quote
        quote_label = QLabel(_HEAVISIDE_QUOTE)
        quote_label.setWordWrap(True)
        quote_label.setAlignment(Qt.AlignCenter)
        quote_label.setStyleSheet("font-size: 11px; color: #555; font-style: italic;")
        layout.addWidget(quote_label)

        layout.addSpacing(20)

        # OK button
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _separator() -> QWidget:
    """A thin vertical separator for the status bar."""
    sep = QWidget()
    sep.setFixedWidth(1)
    sep.setStyleSheet("background: #ccc;")
    return sep


class _PreviewPanel(QWidget):
    """
    Preview image panel in the lower-right of the bottom strip.

    Renders the compiled PDF page at native sharpness (Retina-aware), scaled
    to fill the panel while preserving aspect ratio.
    """

    _MIN_W = 240

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Resizable: the panel lives in a splitter and re-renders to fit (see
        # resizeEvent). A minimum width keeps it from collapsing to nothing.
        self.setMinimumWidth(self._MIN_W)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet(
            "background: white; border-left: 1px solid #ddd;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(2)

        from PySide6.QtWidgets import QLabel as _QLabel
        title = _QLabel("LaTeX Preview")
        title.setStyleSheet("font-weight: bold; font-size: 11px; color: #555;")
        layout.addWidget(title)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._img_label, 1)

        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: red; font-size: 10px;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        self._raw_image: QImage | None = None

    def resizeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().resizeEvent(event)
        if self._raw_image is not None:
            self._render(self._raw_image)

    def set_image(self, image: QImage) -> None:
        self._raw_image = image
        self._error_label.hide()
        self._render(image)

    def _render(self, image: QImage) -> None:
        pix = QPixmap.fromImage(image)
        dpr = self.devicePixelRatioF()
        pix.setDevicePixelRatio(dpr)

        available_w = self._img_label.width()
        available_h = self._img_label.height()
        if available_w < 1 or available_h < 1:
            return

        logical_w = pix.width() / dpr
        logical_h = pix.height() / dpr
        scale = min(available_w / max(logical_w, 1), available_h / max(logical_h, 1), 1.0)
        display_w = int(logical_w * scale)
        display_h = int(logical_h * scale)

        physical_w = int(display_w * dpr)
        physical_h = int(display_h * dpr)
        scaled_pix = pix.scaled(
            physical_w, physical_h,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )
        scaled_pix.setDevicePixelRatio(dpr)
        self._img_label.setPixmap(scaled_pix)

    def set_error(self, error: str) -> None:
        self._raw_image = None
        self._img_label.clear()
        self._error_label.setText(error[:400])
        self._error_label.show()

    def clear(self) -> None:
        self._raw_image = None
        self._img_label.clear()
        self._error_label.hide()
