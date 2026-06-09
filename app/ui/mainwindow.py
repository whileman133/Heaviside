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

import shutil
from pathlib import Path

import qtawesome as qta

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont, QGuiApplication,
    QImage, QKeySequence, QPainter, QPalette, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractSpinBox,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.resources import resource_path
from app.version import __version__
from app.canvas.scene import Mode, SchematicScene  # noqa: F401 (Mode used in type hints)
from app.canvas.view import SchematicView
from app.canvas import style
from app.codegen.circuitikz import generate
from app.preview.latex import (
    CompileError,
    build_snippet,
    build_tex,
    check_dependencies,
    compile_tex,
    pdf_to_eps,
    pdf_to_qimage,
    pdf_to_svg,
)
from app.preview import mathrender, tools
from app.preview.worker import PreviewWorker
from app.schematic.io import SchematicLoadError, load, save
from app.schematic.model import Schematic
from app.ui.palette import ComponentPalette
from app.ui import theme
from app.ui.preferences import Preferences, PreferencesDialog
from app.ui.properties import DocumentPropertiesPanel, PropertiesPanel
from app.ui.sourcepanel import SourcePanel

_WINDOW_TITLE = "Heaviside — CircuiTikZ Editor"
#: GitHub issues page — opened by Help ▸ Report a Bug and the toolbar bug button.
_ISSUES_URL = "https://github.com/whileman133/Heaviside/issues"


def _system_is_dark() -> bool:
    """True if the OS appearance is dark.

    Uses Qt's cross-platform colour-scheme hint (Qt 6.5+); falls back to a
    window-vs-text luminance check on the older/unknown case.
    """
    hints = QGuiApplication.styleHints()
    scheme = hints.colorScheme()
    if scheme == Qt.ColorScheme.Dark:
        return True
    if scheme == Qt.ColorScheme.Light:
        return False
    # Unknown: compare the default window/text lightness.
    pal = QGuiApplication.palette()
    return pal.color(QPalette.Window).lightness() < pal.color(QPalette.WindowText).lightness()


def _component_editor_available() -> bool:
    """The Component Editor is a developer tool: it renders/measures CircuiTikZ
    symbols via ``latex`` + ``dvisvgm``, which a packaged end-user build does not
    ship.  Surface it only when that toolchain is on PATH."""
    return bool(shutil.which("latex") and shutil.which("dvisvgm"))


class MainWindow(QMainWindow):
    """Top-level application window."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_WINDOW_TITLE)
        self.resize(1280, 800)

        # Theme follows the OS appearance (light/dark). Resolve it *before*
        # building the UI so toolbars, palette tiles, and panels construct with
        # the right tokens, then keep it in sync via styleHints().colorSchemeChanged
        # (§10). Form controls (dialogs, message boxes, spin/combo boxes, line
        # edits) keep their **native** look — on macOS those already follow the OS
        # appearance — so only our themed chrome and the canvas are switched here.
        self._dark = _system_is_dark()
        # Until the user flips the toolbar toggle, the theme tracks the OS
        # appearance live; a manual toggle pins it and stops following the OS.
        self._follow_system = True
        theme.set_dark(self._dark)
        style.set_dark(self._dark)
        # Records (QAction, qtawesome-name) so toolbar/ribbon icons can be
        # re-tinted when the appearance changes.
        self._themed_icons: list[tuple[QAction, str]] = []
        self._toolbar: QToolBar | None = None
        self._ribbon: QToolBar | None = None
        self._apply_window_palette()

        # -- Core objects --------------------------------------------------
        self._scene = SchematicScene()
        self._view = SchematicView(self._scene)
        self._preview_worker = PreviewWorker(self, dpi=300)
        self._preview_worker.set_dark(self._dark)  # render the preview dark to match
        self._current_path: Path | None = None
        self._modified = False
        self._prefs = Preferences()
        # Apply configured external-tool paths before any discovery (dependency
        # check, preview, math-label engine) so they honour the user's settings.
        tools.set_tool_paths(self._prefs.tool_paths)
        self._scene.set_mark_unconnected_pins(self._prefs.mark_unconnected_pins)
        self._scene.set_line_hops(self._prefs.line_hops)
        mathrender.set_force_ziamath(self._prefs.force_ziamath)

        # -- Build UI -------------------------------------------------------
        self._build_menu()
        self._build_toolbar()
        self._build_tool_ribbon()
        self._build_central()
        self._build_statusbar()
        # If the app launched with the manual override already off (it does not at
        # startup), the OS appearance drives native widgets directly; a later toggle
        # pins the colour scheme via _apply_color_scheme.

        # -- Window-level Escape: cancel placement/wire regardless of focus ----
        # The view's keyPressEvent also handles Escape when the view has focus,
        # but clicking a palette entry shifts focus to the palette widget.  A
        # window-level QShortcut fires regardless of which child widget is focused.
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WindowShortcut)
        esc.activated.connect(self._scene.cancel_current)

        # -- Wire signals ---------------------------------------------------
        self._connect_signals()

        # -- Follow the OS light/dark appearance live (§10) -----------------
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            self._on_color_scheme_changed
        )

        # -- Dependency warnings (non-blocking) ----------------------------
        self._check_and_warn_dependencies()

    # ------------------------------------------------------------------
    # Theme (light / dark, follows the OS appearance) — §10
    # ------------------------------------------------------------------

    def _apply_window_palette(self) -> None:
        """Set the window background to the active surface colour so the central
        area, splitter gaps, and status bar match the themed chrome. Based on the
        live **application** palette (which reflects the colour scheme set in
        _apply_color_scheme), so native child widgets still inherit the right
        Base/Text/Button roles for the active light/dark mode."""
        pal = QApplication.palette()
        pal.setColor(QPalette.Window, QColor(theme.SURFACE))
        pal.setColor(QPalette.WindowText, QColor(theme.TEXT))
        self.setPalette(pal)

    def _themed_icon(self, action: QAction, name: str) -> None:
        """Tint *action*'s icon with the current ``theme.ICON`` and remember the
        (action, name) pair so it can be re-tinted on a theme change."""
        action.setIcon(qta.icon(name, color=theme.ICON))
        self._themed_icons.append((action, name))

    def _on_color_scheme_changed(self, _scheme=None) -> None:
        # Only follow the OS while the user hasn't overridden the theme manually.
        if not self._follow_system:
            return
        dark = _system_is_dark()
        if dark != self._dark:
            self._dark = dark
            self._apply_theme()

    def _toggle_dark(self, checked: bool) -> None:
        """Toolbar light/dark toggle: pin the chosen mode and stop following the
        OS appearance (a one-way opt-out — there is no 'auto' state to return to
        within a session)."""
        self._follow_system = False
        if checked != self._dark:
            self._dark = checked
            self._apply_theme()
        else:
            self._sync_dark_action()

    def _sync_dark_action(self) -> None:
        """Reflect the current dark state on the toolbar toggle: a sun icon (to
        switch back to light) when dark, a moon (to switch to dark) when light."""
        act = getattr(self, "_act_dark", None)
        if act is None:
            return
        act.blockSignals(True)
        act.setChecked(self._dark)
        act.blockSignals(False)
        name = "fa5s.sun" if self._dark else "fa5s.moon"
        act.setIcon(qta.icon(name, color=theme.ICON))
        act.setToolTip("Switch to light mode" if self._dark else "Switch to dark mode")

    def _apply_color_scheme(self) -> None:
        """Drive the **application colour scheme** so all **native** widgets — form
        controls, dialogs, message boxes, tooltips, scrollbars, tab bars, and the
        window background — follow the chosen light/dark mode natively, instead of
        being restyled (which looked non-native). While following the OS we leave it
        ``Unknown`` (the OS already drives native widgets directly); a manual toggle
        pins ``Dark``/``Light``. Requires Qt 6.8+ (``QStyleHints.setColorScheme``)."""
        if self._follow_system:
            return  # the OS appearance drives native widgets; don't override it
        sh = QGuiApplication.styleHints()
        target = Qt.ColorScheme.Dark if self._dark else Qt.ColorScheme.Light
        if sh.colorScheme() != target:
            sh.setColorScheme(target)

    def _apply_theme(self) -> None:
        """Re-theme every surface for the current ``self._dark`` state: drive the
        native colour scheme, swap the canvas + chrome palettes, re-apply the
        toolbar/panel stylesheets, re-tint icons, and repaint."""
        theme.set_dark(self._dark)
        style.set_dark(self._dark)
        self._apply_color_scheme()    # native widgets follow (before the window palette)
        self._apply_window_palette()
        if self._toolbar is not None:
            self._toolbar.setStyleSheet(theme.top_toolbar_qss())
        if self._ribbon is not None:
            self._ribbon.setStyleSheet(theme.ribbon_qss())
        for action, name in self._themed_icons:
            action.setIcon(qta.icon(name, color=theme.ICON))
        self._sync_dark_action()  # state-dependent icon (sun/moon), re-tinted too
        self._view.setStyleSheet(theme.scrollbar_qss())  # canvas scrollbars
        # Side panels rebuild their own theme-token stylesheets.
        for panel in (self._palette, self._props, self._doc_props,
                      self._source_panel, self._preview_panel):
            if hasattr(panel, "apply_theme"):
                panel.apply_theme()
        # The preview is rendered by LaTeX with a matching dark/light page; if one
        # is already shown, recompile it in the new theme.
        self._preview_worker.set_dark(self._dark)
        if self._preview_panel.has_image():
            self._on_auto_compile()
        # Repaint the canvas (items read style.COLOR_* at paint time).
        self._scene.update()
        self._view.viewport().update()

    def _on_document_props_changed(self) -> None:
        """A live edit in the Document inspector changed a style: re-place the
        on-canvas ± signs / arrows and refresh the source panel + preview (same
        flow the old Document Settings dialog used)."""
        self._scene.relayout_annotations()
        self._scene.schematic_changed.emit()

    # ------------------------------------------------------------------
    # Palette keyboard shortcuts (§10.2)
    # ------------------------------------------------------------------

    def keyPressEvent(self, event) -> None:  # noqa: N802, ANN001
        """Window-level palette shortcuts: a letter selects a component category,
        digits 1–9/0 place the Nth component of the active category.

        This only fires for keys no focused child consumed, so text inputs keep
        their typing and the canvas keeps R/S/W/P (rotate/tools) while it is
        focused — no fragile focus checks needed."""
        if not self._handle_palette_shortcut(event):
            super().keyPressEvent(event)

    def _handle_palette_shortcut(self, event) -> bool:  # noqa: ANN001
        # Plain keys only — never shadow menu/app accelerators.
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        # Belt-and-suspenders: never act while a text input is focused (a
        # read-only QPlainTextEdit can ignore letters, which would propagate here).
        if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QAbstractSpinBox)):
            return False

        key = event.key()
        if Qt.Key_1 <= key <= Qt.Key_9:
            return self._palette.place_active_index(key - Qt.Key_1)
        if key == Qt.Key_0:
            return self._palette.place_active_index(9)

        text = event.text().upper()
        if len(text) == 1 and text.isalpha():
            return self._palette.select_category_by_letter(text)
        return False

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

        self._build_examples_menu(file_menu)

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

        self._act_export_svg = QAction("Export to S&VG…", self)
        self._act_export_svg.triggered.connect(self._on_export_svg)
        file_menu.addAction(self._act_export_svg)

        self._act_export_png = QAction("Export to PN&G…", self)
        self._act_export_png.triggered.connect(self._on_export_png)
        file_menu.addAction(self._act_export_png)

        file_menu.addSeparator()

        self._act_copy_png = QAction("Copy Figure as PN&G", self)
        self._act_copy_png.setShortcut(QKeySequence("Ctrl+Shift+C"))
        self._act_copy_png.triggered.connect(self._on_copy_png)
        file_menu.addAction(self._act_copy_png)

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

        # Per-document CircuiTikZ conventions live in the inspector's Document tab
        # (the old Edit ▸ Document Settings… dialog was replaced by it).

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

        # Tools menu — the Component Editor authors/aligns CircuiTikZ symbols and
        # shells out to the latex + dvisvgm developer toolchain, so it only appears
        # when that toolchain is present (a source checkout, not a packaged
        # end-user build).  The menu holds only this item, so skip it entirely
        # otherwise rather than show an empty menu.
        if _component_editor_available():
            tools_menu = mb.addMenu("&Tools")
            act_comp_editor = QAction("&Component Editor…", self)
            act_comp_editor.setToolTip("Author / align CircuiTikZ component symbols")
            act_comp_editor.triggered.connect(self._on_component_editor)
            tools_menu.addAction(act_comp_editor)

        # Help menu.
        help_menu = mb.addMenu("&Help")
        self._act_help = QAction("&Keyboard Shortcuts && Gestures", self)
        self._act_help.setShortcut(QKeySequence.HelpContents)   # F1 on most platforms
        self._act_help.triggered.connect(self._on_help_shortcuts)
        help_menu.addAction(self._act_help)
        help_menu.addSeparator()
        self._act_report_bug = QAction("&Report a Bug…", self)
        self._act_report_bug.setToolTip("Open the GitHub issues page to report a bug")
        self._act_report_bug.triggered.connect(self._on_report_bug)
        help_menu.addAction(self._act_report_bug)
        help_menu.addSeparator()
        act_about = QAction("&About Heaviside", self)
        act_about.triggered.connect(self._on_about)
        help_menu.addAction(act_about)

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = QToolBar("Main")
        self._toolbar = tb
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.setStyleSheet(theme.top_toolbar_qss())
        self.addToolBar(tb)

        self._themed_icon(self._act_new, "fa5s.file")
        self._themed_icon(self._act_open, "fa5s.folder-open")
        self._themed_icon(self._act_save, "fa5s.save")
        self._themed_icon(self._act_undo, "fa5s.undo")
        self._themed_icon(self._act_redo, "fa5s.redo")

        tb.addAction(self._act_new)
        tb.addAction(self._act_open)
        tb.addAction(self._act_save)
        tb.addSeparator()
        tb.addAction(self._act_undo)
        tb.addAction(self._act_redo)
        tb.addSeparator()

        compile_btn = QAction("Compile", self)
        self._themed_icon(compile_btn, "fa5s.play")
        compile_btn.setShortcut(QKeySequence("Ctrl+Return"))
        compile_btn.triggered.connect(self._on_compile_now)
        tb.addAction(compile_btn)

        # Right-aligned help button: a spacer pushes it to the far right.
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        # Light/dark toggle. Its icon/tooltip track the state (sun↔moon), so it is
        # refreshed via _sync_dark_action rather than the name-based re-tint list.
        self._act_dark = QAction("Toggle dark mode", self)
        self._act_dark.setCheckable(True)
        self._act_dark.toggled.connect(self._toggle_dark)
        tb.addAction(self._act_dark)
        self._sync_dark_action()

        self._themed_icon(self._act_help, "fa5s.question-circle")
        self._act_help.setToolTip("Keyboard shortcuts & gestures (F1)")
        tb.addAction(self._act_help)

        self._themed_icon(self._act_report_bug, "fa5s.bug")
        self._act_report_bug.setToolTip("Report a bug (opens GitHub issues)")
        tb.addAction(self._act_report_bug)

        for action in (self._act_new, self._act_open, self._act_save,
                       self._act_undo, self._act_redo, compile_btn,
                       self._act_dark, self._act_help, self._act_report_bug):
            btn = tb.widgetForAction(action)
            if btn:
                btn.setCursor(Qt.PointingHandCursor)

    # ------------------------------------------------------------------
    # Tool ribbon (left vertical strip: Select | Wire | Pan)
    # ------------------------------------------------------------------

    def _build_tool_ribbon(self) -> None:
        ribbon = QToolBar("Tools")
        self._ribbon = ribbon
        ribbon.setMovable(False)
        ribbon.setToolButtonStyle(Qt.ToolButtonIconOnly)
        ribbon.setIconSize(QSize(22, 22))
        ribbon.setStyleSheet(theme.ribbon_qss())
        self.addToolBar(Qt.LeftToolBarArea, ribbon)

        group = QActionGroup(self)
        group.setExclusive(True)

        self._tool_select = QAction("Select", self)
        self._themed_icon(self._tool_select, "fa5s.mouse-pointer")
        self._tool_select.setToolTip("Select  [S / Esc]")
        self._tool_select.setCheckable(True)
        self._tool_select.setChecked(True)
        self._tool_select.triggered.connect(self._scene.enter_select_mode)
        group.addAction(self._tool_select)
        ribbon.addAction(self._tool_select)

        self._tool_wire = QAction("Wire", self)
        self._themed_icon(self._tool_wire, "fa5s.pen")
        self._tool_wire.setToolTip("Wire  [W]")
        self._tool_wire.setCheckable(True)
        self._tool_wire.triggered.connect(self._scene.enter_wire_mode)
        group.addAction(self._tool_wire)
        ribbon.addAction(self._tool_wire)

        self._tool_pan = QAction("Pan", self)
        self._themed_icon(self._tool_pan, "fa5s.hand-paper")
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

        # Top-level layout: a full-height palette on the left, and everything
        # else (canvas + properties over the source/preview strip) on the right —
        # so the CircuiTikZ source and LaTeX preview no longer run underneath the
        # palette.
        outer = QHBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Left: palette, spanning the full window height.
        self._palette = ComponentPalette()
        self._palette.set_scene(self._scene)
        outer.addWidget(self._palette)

        # Right: a vertical stack of (canvas | properties) over (source | preview).
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Top of the right region: canvas (stretch) + properties (fixed).
        top_split = QSplitter(Qt.Horizontal)
        top_split.setHandleWidth(4)

        from PySide6.QtWidgets import QStackedWidget
        self._canvas_stack = QStackedWidget()
        self._canvas_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._canvas_stack.addWidget(_WelcomeScreen())   # index 0
        self._canvas_stack.addWidget(self._view)          # index 1
        # Theme the canvas scrollbars to match the chrome (they would otherwise
        # stay the native light look when the toolbar toggle forces dark mode).
        self._view.setStyleSheet(theme.scrollbar_qss())
        self._canvas_stack.setCurrentIndex(0)
        top_split.addWidget(self._canvas_stack)

        # Inspector tabs: the per-object Properties inspector and the per-document
        # Document inspector (the latter replaces the old Document Settings dialog).
        from PySide6.QtWidgets import QTabWidget
        self._props = PropertiesPanel()
        self._props.set_scene(self._scene)
        self._doc_props = DocumentPropertiesPanel()
        self._doc_props.set_scene(self._scene)
        self._doc_props.document_changed.connect(self._on_document_props_changed)
        # Native QTabWidget (no custom stylesheet) so the tabs and the form
        # controls inside follow the OS appearance / colour scheme natively.
        self._inspector_tabs = QTabWidget()
        self._inspector_tabs.addTab(self._props, "Properties")
        self._inspector_tabs.addTab(self._doc_props, "Document")
        # Nothing is selected at startup, so surface the Document tab (the
        # Properties tab has nothing to show until something is selected).
        self._inspector_tabs.setCurrentWidget(self._doc_props)
        top_split.addWidget(self._inspector_tabs)

        top_split.setStretchFactor(0, 1)   # canvas: stretch
        top_split.setStretchFactor(1, 0)   # props: fixed
        right_layout.addWidget(top_split, 1)

        # Bottom of the right region: source panel (left) + preview panel (right),
        # in a draggable splitter. The CircuiTikZ source lines are short, so the
        # preview gets the larger initial share of the width; the user can drag
        # the handle to rebalance.
        bottom = QWidget()
        bottom.setFixedHeight(264)
        bottom_layout = QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(6, 2, 6, 6)
        bottom_layout.setSpacing(0)

        bottom_split = QSplitter(Qt.Horizontal)
        bottom_split.setHandleWidth(8)   # gap between the two cards
        bottom_split.setChildrenCollapsible(False)

        self._source_panel = SourcePanel(preferences=self._prefs)
        self._source_panel.set_scene(self._scene)
        bottom_split.addWidget(self._source_panel)

        self._preview_panel = _PreviewPanel()
        self._preview_panel.copy_png_requested.connect(self._on_copy_png)
        bottom_split.addWidget(self._preview_panel)

        # Source stays only as wide as it needs; preview takes the extra room.
        bottom_split.setStretchFactor(0, 0)
        bottom_split.setStretchFactor(1, 1)
        bottom_split.setSizes([440, 840])

        bottom_layout.addWidget(bottom_split)
        right_layout.addWidget(bottom)

        outer.addWidget(right, 1)

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
        wire_ids = self._scene.selected_wire_ids()
        if len(comp_ids) == 1 and not wire_ids:
            self._props.show_component(comp_ids[0])
        elif len(wire_ids) == 1 and not comp_ids:
            self._props.show_wire(wire_ids[0])

    def _on_selection_changed(self, comp_ids: list[str]) -> None:
        # comp_ids comes from the signal; query wires directly (the signal only
        # carries component ids).
        wire_ids = self._scene.selected_wire_ids()
        total = len(comp_ids) + len(wire_ids)
        # With nothing selected the per-object Properties tab has nothing to show,
        # so surface the Document tab; selecting anything returns to Properties.
        self._inspector_tabs.setCurrentWidget(
            self._doc_props if total == 0 else self._props
        )
        if total == 0:
            self._props.clear()
        elif total == 1 and len(comp_ids) == 1:
            self._props.show_component(comp_ids[0])
        elif total == 1 and len(wire_ids) == 1:
            self._props.show_wire(wire_ids[0])
        elif len(comp_ids) >= 2 and not wire_ids:
            # Several components, no wires → bulk-edit if they're all one kind
            # (show_components falls back to a count for a mixed selection).
            self._props.show_components(comp_ids)
        else:
            self._props.show_multi_select(total)

    def _on_component_double_clicked(self, comp_id: str) -> None:
        self._props.show_component(comp_id)

    def _on_auto_compile(self) -> None:
        if self._scene.is_gesture_in_progress:
            return
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                            mark_line_hops=self._prefs.line_hops)
        except Exception:
            return
        self._preview_worker.request_compile(source)

    def _on_compile_now(self) -> None:
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                            mark_line_hops=self._prefs.line_hops)
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
        self._doc_props.refresh()
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
        self._doc_props.refresh()
        self._status_compile.setText("Ready")
        # Fit the view to the loaded circuit. Deferred so it runs after the
        # welcome→canvas switch and layout pass, when the viewport has its final
        # size (fitInView needs a valid viewport size to compute the zoom).
        QTimer.singleShot(0, self._view.fit_to_schematic)

    def _build_examples_menu(self, file_menu) -> None:
        """Add an **Open Example ▸** submenu listing the bundled example files.

        Examples ship under ``examples/`` (bundled into the .app via
        heaviside.spec) and are resolved through ``resource_path`` so the same
        code works from a source checkout and when frozen. Each ``*.hv`` becomes
        a menu item that loads it as a starting point (see ``_open_example``).
        """
        examples_dir = resource_path("examples")
        try:
            files = sorted(examples_dir.glob("*.hv")) if examples_dir.is_dir() else []
        except OSError:
            files = []

        submenu = file_menu.addMenu("Open &Example")
        if not files:
            placeholder = submenu.addAction("(no examples available)")
            placeholder.setEnabled(False)
            return
        for path in files:
            act = submenu.addAction(path.stem)
            # Bind the path per-iteration (default-arg avoids the late-binding trap).
            act.triggered.connect(lambda _checked=False, p=path: self._open_example(p))

    def _open_example(self, path: Path) -> None:
        """Load a bundled example as a fresh, unsaved document.

        Unlike **Open**, the current path is left unset: the example file lives
        inside the (read-only) app bundle, so **Save** should prompt for a new
        location rather than overwrite it — the example acts as a template.
        """
        if not self._confirm_discard():
            return
        try:
            schematic = load(str(path))
        except SchematicLoadError as exc:
            QMessageBox.critical(self, "Load Error", str(exc))
            return
        self._show_canvas()
        self._scene.set_schematic(schematic)
        self._current_path = None        # template: Save → Save As (don't touch the bundle)
        self._modified = False
        self._update_title()
        self._props.clear()
        self._doc_props.refresh()
        self._status_compile.setText(f"Loaded example: {path.stem}")
        QTimer.singleShot(0, self._view.fit_to_schematic)

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
        """Write sibling TeX/PDF/EPS/SVG files next to *path* if enabled in Preferences.

        Runs after a successful save so an ``\\includegraphics`` (or ``\\input``)
        of the sibling file stays in sync with the schematic (§10.8).  The ``.tex``
        snippet is pure Python (no compile); the image formats share a single
        ``pdflatex`` compile.  Failures are reported in the status bar only — a
        modal dialog on every save would be intrusive — and never block the save.
        """
        want_tex = self._prefs.auto_export_tex
        want_pdf = self._prefs.auto_export_pdf
        want_eps = self._prefs.auto_export_eps
        want_svg = self._prefs.auto_export_svg
        want_png = self._prefs.auto_export_png
        if not (want_tex or want_pdf or want_eps or want_svg or want_png):
            return

        self._status_compile.setText("Auto-exporting…")
        written: list[str] = []

        # TeX snippet: generated directly, no LaTeX install needed.
        if want_tex:
            try:
                source = generate(self._scene.schematic, y_flip=True,
                                mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                                mark_line_hops=self._prefs.line_hops)
                tex_path = path.with_suffix(".tex")
                tex_path.write_text(build_snippet(source), encoding="utf-8")
                written.append(tex_path.name)
            except (OSError, ValueError) as exc:
                self._status_compile.setText(f"Auto-export failed: {exc}")
                return

        # Image formats: one compile shared by PDF/EPS/SVG/PNG.
        if want_pdf or want_eps or want_svg or want_png:
            pdf_bytes = self._compile_to_pdf(quiet=True)
            if pdf_bytes is None:
                self._status_compile.setText("Auto-export failed (see Compile)")
                return
            try:
                if want_pdf:
                    pdf_path = path.with_suffix(".pdf")
                    pdf_path.write_bytes(pdf_bytes)
                    written.append(pdf_path.name)
                if want_eps:
                    eps_path = path.with_suffix(".eps")
                    eps_path.write_bytes(pdf_to_eps(pdf_bytes))
                    written.append(eps_path.name)
                if want_svg:
                    svg_path = path.with_suffix(".svg")
                    svg_path.write_bytes(pdf_to_svg(pdf_bytes))
                    written.append(svg_path.name)
                if want_png:
                    png_path = path.with_suffix(".png")
                    image = pdf_to_qimage(pdf_bytes, dpi=self._prefs.png_dpi)
                    if not image.save(str(png_path), "PNG"):
                        raise OSError(f"could not write {png_path.name}")
                    written.append(png_path.name)
            except (OSError, CompileError, RuntimeError) as exc:
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
            self._scene.set_line_hops(self._prefs.line_hops)
            # Apply configured tool paths first so the engine choice, re-typeset,
            # and recompile below all see the updated discovery (§8.7 / §10.8).
            tools.set_tool_paths(self._prefs.tool_paths)
            # Apply the label-render engine choice and re-typeset existing labels
            # so a ziamath toggle (or a new latex path) is reflected immediately.
            mathrender.set_force_ziamath(self._prefs.force_ziamath)
            self._scene.retypeset_labels()
            self._source_panel.refresh()
            self._on_auto_compile()
            # Surface any still-missing required tool (silent when now resolved).
            self._check_and_warn_dependencies()

    def _on_export_tex(self) -> None:
        """Export the schematic as an includable CircuiTikZ ``.tex`` snippet.

        The snippet uses ``y_flip=True`` so the included figure renders in the
        same orientation as the canvas (see §8.5).
        """
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                            mark_line_hops=self._prefs.line_hops)
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
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                            mark_line_hops=self._prefs.line_hops)
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

    def _on_export_svg(self) -> None:
        """Export the schematic as an SVG image (compile then convert, §8.6)."""
        self._status_compile.setText("Compiling…")
        pdf_bytes = self._compile_to_pdf()
        if pdf_bytes is None:
            self._status_compile.setText("Export failed")
            return
        try:
            svg_bytes = pdf_to_svg(pdf_bytes)
        except CompileError as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            self._status_compile.setText("Export failed")
            return
        default_name = (self._current_path.stem if self._current_path else "untitled") + ".svg"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to SVG", default_name, "SVG Image (*.svg);;All Files (*)"
        )
        if not path:
            return
        if not path.endswith(".svg"):
            path += ".svg"
        try:
            Path(path).write_bytes(svg_bytes)
        except OSError as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            return
        self._status_compile.setText(f"Exported to {Path(path).name}")

    def _on_export_png(self) -> None:
        """Export the schematic as a raster PNG at the configured DPI (§8.6)."""
        self._status_compile.setText("Compiling…")
        pdf_bytes = self._compile_to_pdf()
        if pdf_bytes is None:
            self._status_compile.setText("Export failed")
            return
        try:
            image = pdf_to_qimage(pdf_bytes, dpi=self._prefs.png_dpi)
        except (CompileError, RuntimeError) as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            self._status_compile.setText("Export failed")
            return
        default_name = (self._current_path.stem if self._current_path else "untitled") + ".png"
        path, _ = QFileDialog.getSaveFileName(
            self, "Export to PNG", default_name, "PNG Image (*.png);;All Files (*)"
        )
        if not path:
            return
        if not path.endswith(".png"):
            path += ".png"
        if not image.save(path, "PNG"):
            QMessageBox.critical(self, "Export Error", f"Could not write {path}")
            return
        self._status_compile.setText(f"Exported to {Path(path).name}")

    # ------------------------------------------------------------------
    # Copy figure to clipboard
    # ------------------------------------------------------------------

    def _on_copy_png(self) -> None:
        """Copy the compiled figure to the clipboard as a raster image (PNG).

        The schematic is compiled to PDF and rendered to a QImage (QtPdf, no
        Poppler) at the configured PNG resolution (Preferences → PNG resolution,
        default 300 dpi), then placed on the clipboard so it can be pasted into
        slides, docs, chat, etc. Requires ``pdflatex``.

        Only PNG is offered (not PDF/SVG): the common paste targets — Word,
        PowerPoint, Google Docs — rasterize a pasted figure anyway, so a single
        high-resolution PNG is the honest, useful option. Vector output stays
        available via File ▸ Export.
        """
        self._status_compile.setText("Compiling…")
        pdf_bytes = self._compile_to_pdf()
        if pdf_bytes is None:
            self._status_compile.setText("Copy failed")
            return
        try:
            image = pdf_to_qimage(pdf_bytes, dpi=self._prefs.png_dpi)
        except (CompileError, RuntimeError) as exc:
            QMessageBox.critical(self, "Copy Error", str(exc))
            self._status_compile.setText("Copy failed")
            return
        QGuiApplication.clipboard().setImage(image)
        self._status_compile.setText(
            f"Copied figure to clipboard (PNG, {self._prefs.png_dpi} dpi)"
        )

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

    def _on_component_editor(self) -> None:
        """Open the standalone component editor (kept referenced so it persists)."""
        from app.componenteditor.window import ComponentEditorWindow
        self._component_editor = ComponentEditorWindow(self)
        self._component_editor.setWindowFlag(Qt.Window, True)
        self._component_editor.show()
        self._component_editor.raise_()

    def _on_help_shortcuts(self) -> None:
        _HelpDialog(self).exec()

    def _on_report_bug(self) -> None:
        """Open the project's GitHub issues page in the default browser."""
        QDesktopServices.openUrl(QUrl(_ISSUES_URL))

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
# Help: keyboard-shortcut & gesture reference (shown in the Help dialog)
# ---------------------------------------------------------------------------

# Each group is (title, [(keys/gesture, detailed description), ...]).
_HELP_SHORTCUT_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("File", [
        ("Ctrl+N",        "Start a new, empty schematic."),
        ("Ctrl+O",        "Open a saved .hv schematic file."),
        ("Ctrl+S",        "Save the current schematic."),
        ("Ctrl+Shift+S",  "Save the schematic to a new file."),
        ("Ctrl+E",        "Export the generated CircuiTikZ to a .tex file."),
        ("Ctrl+Q",        "Quit the application."),
    ]),
    ("Edit", [
        ("Ctrl+Z",            "Undo the last change."),
        ("Ctrl+Shift+Z",      "Redo the last undone change."),
        ("Ctrl+C",            "Copy the selected components and wires."),
        ("Ctrl+V",            "Paste the copied items."),
        ("Ctrl+A",            "Select every component and wire."),
        ("Del / Backspace",   "Delete the selection (and wires on its pins)."),
        ("Ctrl+,",            "Open the Preferences dialog."),
    ]),
    ("View", [
        ("Ctrl+Return",     "Compile the LaTeX preview now."),
        ("Ctrl+0",          "Zoom and pan to fit the whole schematic."),
        ("Ctrl++ / Ctrl+-", "Zoom in / zoom out."),
    ]),
    ("Tools & canvas", [
        ("S",            "Select tool — pick, move, and edit items."),
        ("W",            "Wire tool — click to route an orthogonal wire."),
        ("P",            "Pan mode — left-drag the canvas to pan (persistent)."),
        ("Space + drag", "Pan the canvas without leaving the current tool."),
        ("R",            "Rotate the selection 90° clockwise."),
        ("Arrows",       "Nudge the selection 0.25 units (one minor-grid cell)."),
        ("Esc",          "Cancel placing/wiring and return to the Select tool."),
    ]),
    ("Component palette", [
        ("Ctrl+/",       "Focus the palette search box."),
        ("Letter key",   "Jump to a category by its keycap letter (R=Resistors, "
                         "C=Capacitors, L=Inductors, D=Diodes, …). The canvas keeps "
                         "R/S/W/P while it is focused."),
        ("1 – 9, 0",     "Place the 1st–10th component of the active category."),
    ]),
    ("Tab — cycle the item under the cursor", [
        ("Tab (over a label)",
            "Cycle the endpoint label's position: off-end → above/left → below/right."),
        ("Tab (over an endpoint)",
            "Cycle the endpoint's arrowhead: none → arrow → stealth → open → bar."),
        ("Tab (over a wire)",
            "Cycle the wire's line style: solid → dashed → dotted → dash-dot."),
        ("Shift+Tab",
            "Step any of the above cycles backward."),
    ]),
]

_HELP_GESTURE_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("Selecting", [
        ("Click an item",        "Select a component or wire."),
        ("Shift-click an item",  "Add or remove it from the selection (multi-select)."),
        ("Drag on empty canvas", "Rubber-band select everything inside the box."),
        ("Click empty canvas",   "Clear the current selection."),
    ]),
    ("Moving & resizing", [
        ("Drag a component body", "Move it; connected wires follow."),
        ("Drag a corner handle",  "Resize a rectangle, circle, or bipole."),
        ("Drag a wire vertex",    "Reshape the wire (stays orthogonal)."),
        ("Drag a junction",       "Move it; every wire meeting there follows."),
        ("Drag a wire endpoint",  "Move it; drag off a pin/edge to disconnect."),
        ("Drag a mid-wire label", "Slide the label along the wire."),
    ]),
    ("Wiring", [
        ("Double-click a wire",            "Split it and start a new wire there."),
        ("Double-click empty canvas",      "Start a new wire from that grid point."),
        ("Click a free pin or edge dot",   "Start a wire from that connection point."),
        ("Click while wiring",             "Drop a vertex / corner."),
        ("Double-click while wiring",      "Finish the wire (or click a pin)."),
    ]),
    ("Editing text", [
        ("Double-click a component",        "Edit its label / options in place."),
        ("Double-click a wire endpoint",    "Edit that endpoint's label."),
        ("Alt + double-click a wire",       "Edit the wire's middle label."),
        ("Double-click any rendered label", "Edit the label text in place."),
    ]),
    ("Navigating", [
        ("Scroll wheel / pinch", "Zoom the canvas in and out."),
        ("Middle-button drag",   "Pan the canvas."),
        ("Space + left-drag",    "Pan the canvas."),
    ]),
]

# Colours for the welcome screen
_C_BG    = QColor(245, 247, 250)        # solid background
_C_STEP  = QColor( 80, 120, 175, 200)   # step-function line
_C_AXIS  = QColor(160, 175, 190, 180)   # axis lines
_C_LABEL = QColor(100, 130, 170, 210)   # H(t) / 1 / t annotations
_C_HINT  = QColor(120, 140, 165, 200)   # hint line


class _WelcomeScreen(QWidget):
    """
    Solid welcome screen shown in the canvas slot before any document is
    active.  Draws only the Heaviside unit step function H(t) as a centred
    diagram, with a faint hint pointing to the Help dialog (the full keyboard-
    shortcut and gesture reference lives in **Help ▸ Keyboard Shortcuts &
    Gestures**, see :class:`_HelpDialog`).

    Replaced by the live SchematicView (via QStackedWidget) as soon as the
    user creates/opens a document or begins component placement.
    """

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w, h = float(self.width()), float(self.height())
        # Background follows the canvas paper colour (light/dark); the muted-blue
        # step graphic reads on either.
        painter.fillRect(self.rect(), QColor(style.COLOR_BACKGROUND))

        # ---- step function (centred) -----------------------------------
        step_w  = min(w * 0.40, 260.0)
        step_h  = min(h * 0.26, 150.0)
        cx   = w / 2.0
        step_cy  = h / 2.0
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
        ann_font.setPointSizeF(12.5)
        ann_font.setItalic(True)
        painter.setFont(ann_font)
        painter.setPen(QPen(_C_LABEL))
        painter.drawText(QPointF(right_x + 8, one_y + 5), "H(t)")
        ann_font.setItalic(False)
        ann_font.setPointSizeF(11.0)
        painter.setFont(ann_font)
        painter.drawText(QPointF(origin_x - 16, one_y + 5),  "1")
        painter.drawText(QPointF(right_x + 22,  zero_y + 5), "t")

        # ---- hint pointing to the Help dialog --------------------------
        hint_font = QFont()
        hint_font.setPointSizeF(10.0)
        painter.setFont(hint_font)
        painter.setPen(QPen(_C_HINT))
        painter.drawText(
            QRectF(0, zero_y + 40, w, 20),
            Qt.AlignHCenter | Qt.AlignTop,
            "Help ▸ Keyboard Shortcuts & Gestures  (F1)",
        )


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

_APP_VERSION = __version__
_ASSETS_DIR = resource_path("assets")

_HEAVISIDE_QUOTE = (
    "“The best result of mathematics is to be able to do without it.”"
    "\n— Oliver Heaviside"
)


class _RefTable(QTableWidget):
    """Read-only two-column reference table (keys/gesture | description).

    The description column **wraps** onto multiple lines and the whole table
    auto-sizes its height to its content (its own scrollbars are off — the
    enclosing :class:`_HelpDialog` ``QScrollArea`` scrolls everything). Groups
    are rendered as full-width bold header rows. Row heights and the table's
    fixed height are recomputed on every resize so wrapping stays correct.
    """

    def __init__(
        self, groups: list, mono: bool, parent: QWidget | None = None
    ) -> None:
        super().__init__(0, 2, parent)
        self.setWordWrap(True)
        self.setShowGrid(False)
        self.horizontalHeader().setVisible(False)
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setStyleSheet("QTableWidget { background: transparent; }")
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)

        key_font = QFont("Menlo" if mono else "")
        if mono:
            key_font.setStyleHint(QFont.TypeWriter)
        else:
            key_font.setWeight(QFont.DemiBold)
        key_color = QColor("#4a6f9c")
        desc_color = QColor("#333333")
        hdr_color = QColor("#7b8aa0")
        hdr_bg = QColor(235, 240, 246)
        hdr_font = QFont()
        hdr_font.setPointSizeF(hdr_font.pointSizeF() - 1)
        hdr_font.setWeight(QFont.DemiBold)

        for title, rows in groups:
            r = self.rowCount()
            self.insertRow(r)
            head = QTableWidgetItem(title.upper())
            head.setFont(hdr_font)
            head.setForeground(hdr_color)
            head.setBackground(hdr_bg)
            head.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.setItem(r, 0, head)
            self.setSpan(r, 0, 1, 2)
            blank = QTableWidgetItem("")
            blank.setBackground(hdr_bg)
            self.setItem(r, 1, blank)   # keeps the header band full-width
            for keys, desc in rows:
                r = self.rowCount()
                self.insertRow(r)
                ki = QTableWidgetItem(keys)
                ki.setFont(key_font)
                ki.setForeground(key_color)
                ki.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                di = QTableWidgetItem(desc)
                di.setForeground(desc_color)
                di.setTextAlignment(Qt.AlignLeft | Qt.AlignTop)
                self.setItem(r, 0, ki)
                self.setItem(r, 1, di)

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        # Column 1's width is now known → re-wrap and re-measure row heights,
        # then pin the table to its content height for the outer scroll area.
        self.resizeRowsToContents()
        total = sum(self.rowHeight(i) for i in range(self.rowCount()))
        self.setFixedHeight(total + 2)


class _HelpDialog(QDialog):
    """Scrollable reference of every keyboard shortcut and mouse gesture.

    Opened from **Help ▸ Keyboard Shortcuts & Gestures** (F1) or the toolbar
    help button. Two :class:`_RefTable` tables (keys | wrapping description)
    are stacked in a `QScrollArea` so the dialog stays usable when the lists
    exceed the window height; descriptions wrap rather than being clipped.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Keyboard Shortcuts & Gestures")
        self.resize(600, 640)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        v = QVBoxLayout(content)
        v.setContentsMargins(20, 16, 20, 16)
        v.setSpacing(4)

        self._add_section_title(v, "Keyboard Shortcuts")
        v.addWidget(_RefTable(_HELP_SHORTCUT_GROUPS, mono=True))
        v.addSpacing(10)
        self._add_section_title(v, "Mouse & Gestures")
        v.addWidget(_RefTable(_HELP_GESTURE_GROUPS, mono=False))
        v.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(16, 8, 16, 12)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        btn_row.addStretch(1)
        btn_row.addWidget(buttons)
        outer.addLayout(btn_row)

    @staticmethod
    def _add_section_title(layout: QVBoxLayout, text: str) -> None:
        lbl = QLabel(text)
        lbl.setStyleSheet(
            "font-size: 15px; font-weight: bold; color: #2c3e57;"
            " margin-top: 6px; margin-bottom: 2px;"
        )
        layout.addWidget(lbl)


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

        llm_label = QLabel("Built with the assistance of Large Language Models (LLMs),\nincluding Claude Sonnet 4.6 and Claude Opus 4.8.")
        llm_label.setAlignment(Qt.AlignCenter)
        llm_label.setStyleSheet("font-size: 11px; color: #888;")
        layout.addWidget(llm_label)

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

    copy_png_requested = Signal()

    _MIN_W = 240
    _HEADER_H = 30   # shared with the source panel so the title bars line up

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # Resizable: the panel lives in a splitter and re-renders to fit (see
        # resizeEvent). A minimum width keeps it from collapsing to nothing.
        self.setObjectName("prevPanel")
        self.setMinimumWidth(self._MIN_W)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        from PySide6.QtWidgets import QLabel as _QLabel
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header strip: "LaTeX Preview" with the Copy PNG button inline on the
        # right as an icon-only button (§10.5), same fixed height as the source
        # panel header so the two title bars align.
        self._header = QWidget()
        self._header.setObjectName("panelHeader")
        self._header.setFixedHeight(self._HEADER_H)
        header_row = QHBoxLayout(self._header)
        header_row.setContentsMargins(10, 2, 6, 2)
        header_row.setSpacing(2)
        self._title = _QLabel("LaTeX Preview")
        header_row.addWidget(self._title)
        header_row.addStretch(1)

        # Only PNG is offered for clipboard copy: PDF/SVG always paste as a raster
        # into the common targets (Word/PowerPoint/Docs) anyway, so the extra
        # buttons were misleading. Vector output is still available via Export.
        self._copy_icons = ["fa5s.image"]
        btn = QToolButton()
        btn.setAutoRaise(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setToolTip("Copy the figure to the clipboard as a PNG image")
        btn.clicked.connect(self.copy_png_requested)
        self._copy_buttons = [btn]
        header_row.addWidget(btn)
        layout.addWidget(self._header)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        self._img_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(self._img_label, 1)

        self._error_label = QLabel()
        self._error_label.setWordWrap(True)
        self._error_label.setStyleSheet("color: red; font-size: 10px; border: none; padding: 4px 8px;")
        self._error_label.hide()
        layout.addWidget(self._error_label)

        self._raw_image: QImage | None = None
        self.apply_theme()

    def apply_theme(self) -> None:
        """Follow light/dark with a card frame matching the source panel; the
        image area uses the figure's page colour (``style.COLOR_BACKGROUND``) so
        the rendered schematic blends in."""
        self.setStyleSheet(theme.panel_frame_qss("prevPanel"))
        self._header.setStyleSheet(theme.panel_header_qss())
        self._title.setStyleSheet(theme.panel_title_qss())
        self._img_label.setStyleSheet(
            "border: none; background: %s;" % style.COLOR_BACKGROUND
        )
        for btn, icon_name in zip(self._copy_buttons, self._copy_icons):
            btn.setIcon(qta.icon(icon_name, color=theme.ICON))
            btn.setStyleSheet(theme.icon_button_qss())

    def has_image(self) -> bool:
        return self._raw_image is not None

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
