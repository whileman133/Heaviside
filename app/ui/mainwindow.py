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

import sys
from dataclasses import dataclass
from pathlib import Path

import qtawesome as qta

from shiboken6 import isValid as _qt_is_valid

from PySide6.QtCore import (
    QObject, QPointF, QRectF, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QDesktopServices, QFont, QGuiApplication,
    QImage, QKeySequence, QPainter, QPalette, QPen, QPixmap, QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
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

from app import update as _update
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
from app.components.style import contains_dangerous_latex
from app.preview import mathrender, tools
from app.preview.worker import PreviewWorker
from app.schematic import io as schematic_io
from app.schematic.io import SchematicLoadError, SchematicSaveError, load, save
from app.schematic.model import Schematic
from app.ui.palette import ComponentPalette
from app.ui import theme
from app.ui.preferences import Preferences, PreferencesDialog
from app.ui.properties import DocumentPropertiesPanel, PropertiesPanel
from app.ui.sourcepanel import SourcePanel

_WINDOW_TITLE = "Heaviside — CircuiTikZ Editor"
#: GitHub issues page — opened by Help ▸ Report a Bug and the toolbar bug button.
_ISSUES_URL = "https://github.com/whileman133/Heaviside/issues"
#: Fallback delay before re-enabling Help ▸ Check for Updates if the async
#: probe never reports back (it normally re-enables in the result callback).
_UPDATE_CHECK_REENABLE_MS = 30_000


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


# The application palette as the platform provided it, captured before the
# first explicit-palette fallback so System/honoured modes can restore it.
_native_palette: QPalette | None = None


def _token_palette() -> QPalette:
    """An explicit application palette built from the current theme tokens.

    Used only when the platform theme ignores ``QStyleHints.setColorScheme``
    (Qt's offscreen platform; bare Linux sessions) — native widgets then take
    their Window/Base/Button/Text/Highlight roles from this instead of being
    stuck on the platform's light defaults. ``theme.set_dark`` must already
    reflect the target mode (it runs first in ``_apply_theme``)."""
    pal = QPalette()
    groups = (QPalette.Active, QPalette.Inactive, QPalette.Disabled)
    roles = {
        QPalette.Window: theme.SURFACE,
        QPalette.WindowText: theme.TEXT,
        QPalette.Base: theme.SURFACE_ALT,
        QPalette.AlternateBase: theme.SURFACE,
        QPalette.Text: theme.TEXT,
        QPalette.PlaceholderText: theme.ICON_MUTED,
        QPalette.Button: theme.BUTTON_BG,
        QPalette.ButtonText: theme.TEXT,
        QPalette.Highlight: theme.ACCENT,
        QPalette.HighlightedText: "#ffffff",
        QPalette.ToolTipBase: theme.SURFACE_ALT,
        QPalette.ToolTipText: theme.TEXT,
        QPalette.Link: theme.ACCENT,
        QPalette.Mid: theme.BORDER,
        QPalette.Dark: theme.DIVIDER,
        QPalette.Light: theme.SURFACE_ALT,
    }
    for group in groups:
        for role, color in roles.items():
            pal.setColor(group, role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(theme.ICON_MUTED))
    return pal


# ---------------------------------------------------------------------------
# Background auto-export (runs the post-save sibling-file exports off the UI
# thread; see MainWindow._auto_export)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _AutoExportJob:
    """An immutable snapshot of everything one auto-export run needs.

    Captured on the UI thread at save time so the worker never reads the live
    schematic or preferences (which the user may be mutating meanwhile).
    """

    path: Path
    source: str          # generated CircuiTikZ source (pure data)
    want_tex: bool
    want_pdf: bool
    want_eps: bool
    want_svg: bool
    want_png: bool
    png_dpi: int
    siunitx: bool        # document preamble settings (snapshotted with source)
    preamble: str


@dataclass
class _AutoExportResult:
    """What an auto-export run produced. ``png`` is a **deferred** render: the
    worker leaves the ``QImage`` construction to the UI thread (PROJECT_SPEC §8.1
    — no Qt objects on worker threads), so it carries only the Qt-free PDF bytes,
    target path, and dpi for the UI thread to render. ``written``/``failed`` are
    mutated on the UI thread once the PNG is rendered."""

    written: list[str]
    failed: list[str]
    compile_error: str | None = None
    png: "tuple[bytes, Path, int] | None" = None


def _run_auto_export_job(job: _AutoExportJob) -> _AutoExportResult:
    """Execute *job* (worker thread) and return its result.

    Each format is independent: with several enabled (TeX/PDF/PNG default on), a
    failure in one — e.g. SVG when Poppler is absent — must not skip the others.
    The PNG is **not rendered here**: building its ``QImage`` on a worker thread
    would race the UI garbage collector (§8.1), so the result carries the compiled
    PDF bytes for the UI thread to render (see ``_on_auto_export_finished``).
    """
    written: list[str] = []
    failed: list[str] = []

    def _export(suffix: str, produce) -> None:  # noqa: ANN001
        target = job.path.with_suffix(suffix)
        try:
            produce(target)
            written.append(target.name)
        except (OSError, ValueError, CompileError, RuntimeError) as exc:
            failed.append(f"{target.name} ({exc})")

    # TeX snippet: written directly, no LaTeX install needed.
    if job.want_tex:
        _export(".tex", lambda t: t.write_text(
            build_snippet(job.source, siunitx=job.siunitx,
                          extra_preamble=job.preamble),
            encoding="utf-8"))

    png: "tuple[bytes, Path, int] | None" = None
    # Image formats share a single pdflatex compile.
    if job.want_pdf or job.want_eps or job.want_svg or job.want_png:
        try:
            pdf_bytes = compile_tex(build_tex(
                job.source, siunitx=job.siunitx, extra_preamble=job.preamble))
        except (CompileError, OSError, RuntimeError) as exc:
            return _AutoExportResult(written, failed, compile_error=str(exc))
        if job.want_pdf:
            _export(".pdf", lambda t: t.write_bytes(pdf_bytes))
        if job.want_eps:
            _export(".eps", lambda t: t.write_bytes(pdf_to_eps(pdf_bytes)))
        if job.want_svg:
            _export(".svg", lambda t: t.write_bytes(pdf_to_svg(pdf_bytes)))
        if job.want_png:
            png = (pdf_bytes, job.path.with_suffix(".png"), job.png_dpi)

    return _AutoExportResult(written, failed, png=png)


def _format_export_message(result: _AutoExportResult) -> str:
    """Status-bar message for a finished auto-export (PNG already rendered)."""
    if result.compile_error is not None:
        if result.written:
            return ("Auto-exported " + ", ".join(result.written)
                    + f" · compile failed ({result.compile_error})")
        return f"Auto-export failed ({result.compile_error})"
    msg = "Auto-exported " + ", ".join(result.written) if result.written else ""
    if result.failed:
        msg = (msg + " · " if msg else "") + "failed: " + "; ".join(result.failed)
    return msg or "Auto-export: nothing written"


class _AutoExportSignals(QObject):
    """Queued bridge from the worker task back to the UI thread."""

    finished = Signal(object)   # an _AutoExportResult (Qt-free data)


class _AutoExportTask(QRunnable):
    """QRunnable wrapper running one :func:`_run_auto_export_job`."""

    def __init__(self, job: _AutoExportJob, signals: _AutoExportSignals) -> None:
        super().__init__()
        self._job = job
        self._signals = signals

    def run(self) -> None:  # noqa: D102 — QRunnable hook
        try:
            result = _run_auto_export_job(self._job)
        except Exception as exc:  # noqa: BLE001 — never let a job kill the pool
            result = _AutoExportResult([], [f"auto-export ({exc})"])
        try:
            self._signals.finished.emit(result)
        except RuntimeError:
            pass   # signals object destroyed during app shutdown


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
        # Theme mode is three-state: "system" (follow the OS appearance live),
        # "light", or "dark". It is persisted as dark_override (None / False /
        # True) and resolved here, before the UI is built, so chrome/canvas
        # construct with the right tokens. The user's saved choice wins; absent
        # one, the app follows the OS — restored on the next launch.
        self._prefs = Preferences()
        _override = self._prefs.dark_override
        self._theme_mode = (
            "system" if _override is None else ("dark" if _override else "light")
        )
        self._follow_system = self._theme_mode == "system"
        self._dark = (
            _system_is_dark() if self._follow_system else self._theme_mode == "dark"
        )
        # True while the explicit token palette substitutes for an ignored
        # QStyleHints.setColorScheme (see _apply_color_scheme).
        self._palette_fallback_active = False
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
        # Modified state is derived from the undo stack's save point (see the
        # _modified property); _manual_dirty is OR'd in for any mutation that
        # bypasses the stack (none known today — kept as a safety net).
        self._manual_dirty = False
        # Background auto-export bookkeeping (single-flight; see _auto_export).
        self._auto_export_busy = False
        self._auto_export_pending: _AutoExportJob | None = None
        self._auto_export_signals: _AutoExportSignals | None = None
        # Non-blocking "this file contains risky LaTeX" box (kept referenced).
        self._latex_warning_box: QMessageBox | None = None
        # (self._prefs is created above, before the theme is resolved.)
        # Apply configured external-tool paths before any discovery (dependency
        # check, preview, math-label engine) so they honour the user's settings.
        tools.set_tool_paths(self._prefs.tool_paths)
        self._scene.set_mark_unconnected_pins(self._prefs.mark_unconnected_pins)
        self._scene.set_line_hops(self._prefs.line_hops)
        self._view.set_placement_shortcuts(self._prefs.component_shortcuts)
        mathrender.set_force_ziamath(self._prefs.force_ziamath)

        # -- Build UI -------------------------------------------------------
        self._build_menu()
        self._build_toolbar()
        self._build_tool_ribbon()
        self._build_central()
        self._build_statusbar()
        # Pin a saved Light/Dark override for native widgets at startup —
        # previously this happened only on a later toggle, so launching with a
        # pinned theme that disagreed with the OS left native widgets on the
        # OS appearance. System mode leaves the scheme Unknown so the OS
        # drives native widgets directly. The explicit-palette fallback inside
        # _apply_color_scheme covers platforms that ignore scheme forcing.
        if not self._follow_system:
            self._apply_color_scheme()

        # -- Window-level Escape: cancel placement/wire regardless of focus ----
        # The view's keyPressEvent also handles Escape when the view has focus,
        # but clicking a palette entry shifts focus to the palette widget.  A
        # window-level QShortcut fires regardless of which child widget is focused.
        esc = QShortcut(QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WindowShortcut)
        esc.activated.connect(self._scene.cancel_current)

        # -- Window-level rotate: Ctrl+R (⌘R on macOS) rotates the selection or the
        # placement ghost 90° CW. A window QShortcut so it fires regardless of focus,
        # and a modified key so the plain letters stay free for placement (§10.2).
        self._rotate_shortcut = QShortcut(QKeySequence("Ctrl+R"), self)
        self._rotate_shortcut.setContext(Qt.WindowShortcut)
        self._rotate_shortcut.activated.connect(self._scene.rotate_selected_cw)

        # -- Wire signals ---------------------------------------------------
        self._connect_signals()
        self._refresh_action_states()   # Undo/Redo start disabled (empty stack)

        # -- Follow the OS light/dark appearance live (§10) -----------------
        QGuiApplication.styleHints().colorSchemeChanged.connect(
            self._on_color_scheme_changed
        )

        # -- LaTeX toolchain notice ----------------------------------------
        # No startup dialog: if pdflatex is absent, the preview pane itself shows
        # a light, centred "LaTeX not found" notice with install guidance (set
        # here so it is visible the moment the editor is shown, and refreshed on
        # every compile attempt).
        if check_dependencies():
            self._preview_panel.show_no_latex()

        # -- Update check (non-blocking, opt-out) --------------------------
        # Deferred so it never delays first paint; the network probe itself runs
        # on a worker thread (app.update.check_async).
        QTimer.singleShot(0, self._maybe_check_for_updates_on_startup)

    # ------------------------------------------------------------------
    # Modified state (derived from the undo stack's save point)
    # ------------------------------------------------------------------

    @property
    def _modified(self) -> bool:
        """True when the document differs from its last-saved state.

        Derived from the undo stack's save point, so undoing back to the saved
        state clears the dirty dot (spec §10.1). ``_manual_dirty`` is OR'd in as
        a safety net for any mutation that bypasses the stack.
        """
        return self._manual_dirty or self._scene.undo_stack.is_modified()

    @_modified.setter
    def _modified(self, value: bool) -> None:
        if value:
            self._manual_dirty = True
        else:
            self._manual_dirty = False
            self._scene.undo_stack.mark_save_point()

    def _refresh_action_states(self) -> None:
        """Enable/disable Undo & Redo to match the stack's actual state."""
        stack = self._scene.undo_stack
        self._act_undo.setEnabled(stack.can_undo())
        self._act_redo.setEnabled(stack.can_redo())

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

    @staticmethod
    def _themed_qicon(name: str):
        """A qtawesome icon tinted for the current theme.

        The **disabled** state is pinned to ``theme.ICON_MUTED`` explicitly.
        Without it, qtawesome defaults ``color_disabled`` to
        ``palette(Disabled, Text)`` resolved *at icon-creation time* — and toolbar
        icons are built before the dark palette is applied at a dark-persisted
        launch, so disabled buttons (undo/redo) captured the light palette's dark
        ink and rendered near-black on the dark toolbar.
        """
        return qta.icon(name, color=theme.ICON, color_disabled=theme.ICON_MUTED)

    def _themed_icon(self, action: QAction, name: str) -> None:
        """Tint *action*'s icon for the current theme and remember the
        (action, name) pair so it can be re-tinted on a theme change."""
        action.setIcon(self._themed_qicon(name))
        self._themed_icons.append((action, name))

    def _on_color_scheme_changed(self, _scheme=None) -> None:
        # Only follow the OS while the user hasn't overridden the theme manually.
        if not self._follow_system:
            return
        dark = _system_is_dark()
        if dark != self._dark:
            self._dark = dark
            self._apply_theme()

    #: Display order for the toolbar theme radio group.
    _THEME_MODE_ORDER = ("system", "light", "dark")
    _THEME_MODE_ICON = {"system": "fa5s.desktop", "light": "fa5s.sun", "dark": "fa5s.moon"}
    _THEME_MODE_LABEL = {"system": "System", "light": "Light", "dark": "Dark"}

    def _set_theme_mode(self, mode: str) -> None:
        """Apply and persist a theme *mode* (``"system"``/``"light"``/``"dark"``).

        ``"system"`` follows the OS appearance live (and is restored if the OS
        later changes); ``"light"``/``"dark"`` pin it. The choice is saved
        (`dark_override`: ``None``/``False``/``True``) so it survives a relaunch.
        """
        self._theme_mode = mode
        self._follow_system = mode == "system"
        self._prefs.set_dark_override(None if mode == "system" else (mode == "dark"))
        new_dark = _system_is_dark() if self._follow_system else (mode == "dark")
        if new_dark != self._dark:
            self._dark = new_dark
            self._apply_theme()          # full re-theme (also re-syncs the button)
        else:
            # The light/dark ink didn't change (e.g. Light → System while the OS
            # is light), but native-widget following and the button still must.
            self._apply_color_scheme()
            self._sync_theme_action()

    def _sync_theme_action(self) -> None:
        """Check the toolbar radio button matching the active theme mode, leaving
        the other two unchecked (the exclusive group enforces this, but a
        programmatic mode change must drive it explicitly)."""
        acts = getattr(self, "_theme_actions", None)
        if not acts:
            return
        act = acts.get(self._theme_mode)
        if act is not None and not act.isChecked():
            act.setChecked(True)

    def _apply_color_scheme(self) -> None:
        """Drive the **application colour scheme** so all **native** widgets — form
        controls, dialogs, message boxes, tooltips, scrollbars, tab bars, and the
        window background — follow the chosen light/dark mode natively, instead of
        being restyled (which looked non-native). In **System** mode we set it back
        to ``Unknown`` so the OS drives native widgets directly (and a previously
        pinned scheme is released); **Light**/**Dark** pin it. Requires Qt 6.8+
        (``QStyleHints.setColorScheme``).

        Not every platform theme supports scheme forcing — Qt's ``offscreen``
        platform (headless screenshots/tests) and bare Linux sessions without a
        desktop theme silently ignore ``setColorScheme``, leaving native
        widgets (inspector fields, combos, tab pages) on the light palette
        while everything custom-painted goes dark. When the request is not
        honoured, fall back to an explicit application palette built from the
        theme tokens; when the platform takes over again (or System mode is
        chosen), restore the pristine native palette."""
        global _native_palette
        sh = QGuiApplication.styleHints()
        target = (
            Qt.ColorScheme.Unknown if self._follow_system
            else (Qt.ColorScheme.Dark if self._dark else Qt.ColorScheme.Light)
        )
        if sh.colorScheme() != target:
            sh.setColorScheme(target)
        if _native_palette is None:
            _native_palette = QPalette(QApplication.palette())
        honoured = target == Qt.ColorScheme.Unknown or sh.colorScheme() == target
        if honoured:
            if self._palette_fallback_active:
                QApplication.setPalette(_native_palette)
                self._palette_fallback_active = False
        else:
            QApplication.setPalette(_token_palette())
            self._palette_fallback_active = True

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
        divider = getattr(self, "_theme_divider", None)
        if divider is not None:
            divider.setStyleSheet(theme.toolbar_dotted_divider_qss())
        for action, name in self._themed_icons:
            action.setIcon(self._themed_qicon(name))
        self._sync_theme_action()  # state-dependent icon (monitor/sun/moon), re-tinted
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
        # Status-bar separators carry a pinned stylesheet — re-ink them.
        sb = self.statusBar()
        if sb is not None:
            for sep in sb.findChildren(QWidget, "statusSeparator"):
                sep.setStyleSheet(f"background: {theme.DIVIDER};")
        # Repaint the canvas (items read style.COLOR_* at paint time) and the
        # welcome screen (its diagram inks read theme tokens at paint time).
        self._scene.update()
        self._view.viewport().update()
        welcome = self._canvas_stack.widget(0)
        if welcome is not None:
            welcome.update()

    def _on_document_props_changed(self) -> None:
        """A live edit in the Document inspector changed a style: re-place the
        on-canvas ± signs / arrows. The edit itself is now an undoable command
        pushed through the scene (which already emitted ``schematic_changed``,
        refreshing source/preview/modified-state), so only the annotation
        relayout remains here.

        The siunitx toggle also changes how labels typeset (it loads the package
        that defines ``\\qty`` / ``\\unit``), so mirror it into the canvas label
        renderer and re-render existing labels."""
        self._scene.sync_label_preamble()
        self._scene.retypeset_labels()
        self._scene.relayout_annotations()

    def keyPressEvent(self, event) -> None:  # noqa: N802, ANN001
        """Window-level component-placement shortcuts (§10.2). A key that no focused
        child consumed bubbles up here; delegate it to the canvas view's placement
        handler so the shortcuts work regardless of which widget holds focus (the
        view already covers the canvas-focused case). The handler guards text inputs
        and the in-place label editor, so typing is never hijacked."""
        if self._view.handle_placement_key(event):
            return
        super().keyPressEvent(event)

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

        self._act_cut = QAction("Cu&t", self)
        self._act_cut.setShortcut(QKeySequence.Cut)
        self._act_cut.triggered.connect(self._scene.cut_selection)
        edit_menu.addAction(self._act_cut)

        self._act_copy = QAction("&Copy", self)
        self._act_copy.setShortcut(QKeySequence.Copy)
        self._act_copy.triggered.connect(self._scene.copy_selection)
        edit_menu.addAction(self._act_copy)

        self._act_paste = QAction("&Paste", self)
        self._act_paste.setShortcut(QKeySequence.Paste)
        # Wrap in a lambda so QAction.triggered's `checked` bool is not bound to
        # paste()'s `at` parameter (which would take the "paste here" branch and
        # subscript a bool). The menu/shortcut path pastes at the default offset.
        self._act_paste.triggered.connect(lambda: self._scene.paste())
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
        # macOS: PreferencesRole relocates this to the application menu (Heaviside ▸
        # Preferences), the native convention. Off macOS we force NoRole: some Linux
        # desktops with a global/unified menu bar honour the role and pull the item
        # out of the in-window Edit menu into an app menu that isn't shown, making
        # Preferences unreachable. NoRole keeps it put in Edit on every platform.
        self._act_preferences.setMenuRole(
            QAction.PreferencesRole if sys.platform == "darwin" else QAction.NoRole
        )
        self._act_preferences.triggered.connect(self._on_preferences)
        edit_menu.addAction(self._act_preferences)

        # Off macOS, also surface Preferences in the File menu (above Quit) — a
        # second home for the same action, since users coming from Windows/Linux
        # apps often look in File first. On macOS it lives in the application menu
        # (PreferencesRole above), so the File duplicate is skipped there.
        if sys.platform != "darwin":
            file_menu.insertAction(act_quit, self._act_preferences)
            file_menu.insertSeparator(act_quit)

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
        self._act_check_updates = QAction("Check for &Updates…", self)
        self._act_check_updates.setToolTip("Check GitHub for a newer version")
        self._act_check_updates.triggered.connect(self._on_check_updates_manual)
        help_menu.addAction(self._act_check_updates)
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

        # Theme radio group: System / Light / Dark — all three icons (monitor/sun/
        # moon) are visible, grouped in an exclusive QActionGroup so exactly one is
        # active at a time. They render as plain *flat* toolbar buttons, identical to
        # the others, with the active one carrying the standard soft-blue :checked
        # tint; only the tight spacing inside their container reads them as a set.
        # The icons re-tint via the standard _themed_icon list; _sync_theme_action
        # only flips which is checked.
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        self._theme_actions: dict[str, QAction] = {}

        group_box = QWidget()
        group_layout = QHBoxLayout(group_box)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(0)   # tighter than the toolbar's 3px → reads as a group
        for mode in self._THEME_MODE_ORDER:
            act = QAction(self._THEME_MODE_LABEL[mode], self)
            self._themed_icon(act, self._THEME_MODE_ICON[mode])
            act.setCheckable(True)
            act.setChecked(mode == self._theme_mode)
            act.setToolTip(f"Theme: {self._THEME_MODE_LABEL[mode]}")
            act.triggered.connect(lambda _checked, m=mode: self._set_theme_mode(m))
            self._theme_group.addAction(act)
            self._theme_actions[mode] = act

            btn = QToolButton(group_box)
            btn.setDefaultAction(act)
            btn.setAutoRaise(True)
            btn.setIconSize(tb.iconSize())   # match the toolbar's own action buttons
            btn.setCursor(Qt.PointingHandCursor)
            group_layout.addWidget(btn)
        self._theme_group_box = group_box
        tb.addWidget(group_box)
        self._sync_theme_action()

        # Dotted vertical divider separating the theme group from the help/bug
        # buttons on its right (distinct from the solid separators elsewhere).
        divider = QWidget()
        divider.setObjectName("toolbarDottedDivider")
        divider.setFixedWidth(9)
        # Stretch the dotted line to almost the full toolbar height.
        divider.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._theme_divider = divider
        tb.addWidget(divider)
        divider.setStyleSheet(theme.toolbar_dotted_divider_qss())

        self._themed_icon(self._act_help, "fa5s.question-circle")
        self._act_help.setToolTip("Keyboard shortcuts & gestures (F1)")
        tb.addAction(self._act_help)

        self._themed_icon(self._act_report_bug, "fa5s.bug")
        self._act_report_bug.setToolTip("Report a bug (opens GitHub issues)")
        tb.addAction(self._act_report_bug)

        for action in (self._act_new, self._act_open, self._act_save,
                       self._act_undo, self._act_redo, compile_btn,
                       self._act_help, self._act_report_bug):
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
        # Modified state is derived from the undo stack (see _modified), so a
        # change only needs the title + action states refreshed — undoing back
        # to the save point correctly clears the dirty dot.
        self._update_title()
        self._refresh_action_states()
        # Undo/redo can also revert Document-tab edits; reload its combos.
        self._doc_props.refresh()
        # Keep the properties panel in sync when a single component is selected
        # (e.g. after an in-place options edit that doesn't change the selection).
        comp_ids = self._scene.selected_component_ids()
        wire_ids = self._scene.selected_wire_ids()
        if len(comp_ids) == 1 and not wire_ids:
            self._props.show_component(comp_ids[0])
        elif len(wire_ids) == 1 and not comp_ids:
            self._props.show_wire(wire_ids[0])
        elif len(wire_ids) >= 2 and not comp_ids:
            self._props.show_wires(wire_ids)

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
            # Several components, no wires → bulk-edit. Same kind shows the full
            # per-kind editor; mixed kinds show the shared capability sections
            # (show_components falls back to a count when nothing is shared).
            self._props.show_components(comp_ids)
        elif len(wire_ids) >= 2 and not comp_ids:
            # Several wires, no components → bulk-edit their shared wire properties.
            self._props.show_wires(wire_ids)
        else:
            self._props.show_multi_select(total)

    def _on_component_double_clicked(self, comp_id: str) -> None:
        self._props.show_component(comp_id)

    def _on_auto_compile(self) -> None:
        if self._scene.is_gesture_in_progress:
            return
        if check_dependencies():   # pdflatex missing → show the notice, skip compile
            self._preview_panel.show_no_latex()
            self._status_compile.setText("LaTeX not found")
            return
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                            mark_line_hops=self._prefs.line_hops)
        except Exception as exc:  # noqa: BLE001 — keep the preview alive, but visible
            self._status_compile.setText(f"Preview update failed: {exc}")
            return
        sch = self._scene.schematic
        self._preview_worker.set_preamble(sch.siunitx, sch.preamble)
        self._preview_worker.request_compile(source)

    def _on_compile_now(self) -> None:
        if check_dependencies():   # pdflatex missing → show the notice, skip compile
            self._preview_panel.show_no_latex()
            self._status_compile.setText("LaTeX not found")
            return
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                            mark_line_hops=self._prefs.line_hops)
        except Exception as exc:
            self._status_compile.setText(f"Error: {exc}")
            return
        sch = self._scene.schematic
        self._preview_worker.set_preamble(sch.siunitx, sch.preamble)
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
        self._scene.set_schematic(
            Schematic(version=schematic_io._FORMAT_VERSION, name="untitled")
        )
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
        self.load_path(path)

    def load_path(self, path: str | Path) -> bool:
        """Load the schematic at *path* into the editor (no discard prompt).

        Shared by **File ▸ Open** and the **command-line / file-association** launch
        (double-clicking a ``.hv`` file passes its path as ``argv[1]``; see
        ``main.py``). Shows a load-error dialog and returns ``False`` on failure,
        ``True`` on success. Callers that need to guard unsaved work should call
        :meth:`_confirm_discard` first (the menu path does; a fresh-launch open
        has nothing to discard).
        """
        try:
            schematic = load(str(path))
        except SchematicLoadError as exc:
            QMessageBox.critical(self, "Load Error", str(exc))
            return False
        except Exception as exc:  # noqa: BLE001 — belt & suspenders: io should
            # convert everything to SchematicLoadError, but an unexpected bug
            # must show the error dialog rather than crash the app.
            QMessageBox.critical(self, "Load Error", f"Unexpected error: {exc}")
            return False
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
        # Security heads-up (once per load): labels are compiled as raw LaTeX
        # outside Heaviside (exports), so flag risky primitives in the file.
        self._warn_dangerous_latex(schematic)
        return True

    def _warn_dangerous_latex(self, schematic: Schematic) -> bool:
        """Warn (non-blocking) when any label/text/option field of *schematic*
        contains a high-risk LaTeX primitive (``\\write18``, ``\\input``, …).

        Heaviside itself always compiles with ``-no-shell-escape``, but an
        exported snippet may be compiled elsewhere — so a file from an untrusted
        source deserves a review prompt. Returns True when a warning was shown.
        """
        fields = [c.options for c in schematic.components]
        for w in schematic.wires:
            fields += [w.start_label, w.end_label, w.mid_label]
        if not any(contains_dangerous_latex(f or "") for f in fields):
            return False
        self._status_compile.setText(
            "Warning: this file's labels contain potentially dangerous LaTeX"
        )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Potentially Dangerous LaTeX")
        box.setText(
            "This file's labels contain LaTeX commands (such as \\write18 or "
            "\\input) that could execute code or read files when compiled "
            "outside Heaviside — review them before exporting.\n\n"
            "Previews inside Heaviside always compile with shell escape "
            "disabled."
        )
        box.setStandardButtons(QMessageBox.Ok)
        box.setAttribute(Qt.WA_DeleteOnClose, True)
        box.open()   # non-blocking: the user can keep working
        self._latex_warning_box = box
        return True

    def _build_examples_menu(self, file_menu) -> None:
        """Add an **Open Example ▸** submenu listing the bundled examples.

        Examples ship under ``examples/`` (bundled into the .app via
        heaviside.spec) and are resolved through ``resource_path`` so the same
        code works from a source checkout and when frozen. Each **subdirectory**
        becomes a category submenu of its ``*.hv`` files; any ``*.hv`` directly
        under ``examples/`` is listed (uncategorised) at the top level. Loading an
        example uses it as a starting template (see ``_open_example``).
        """
        examples_dir = resource_path("examples")
        submenu = file_menu.addMenu("Open &Example")

        def _add(menu, files) -> None:
            for path in sorted(files):
                # Bind the path per-iteration (default-arg avoids the late-binding
                # trap); escape '&' so it isn't taken as a menu mnemonic.
                act = menu.addAction(path.stem.replace("&", "&&"))
                act.triggered.connect(
                    lambda _checked=False, p=path: self._open_example(p)
                )

        added = False
        try:
            if examples_dir.is_dir():
                for cat in sorted(d for d in examples_dir.iterdir() if d.is_dir()):
                    files = list(cat.glob("*.hv"))
                    if files:
                        _add(submenu.addMenu(cat.name.replace("&", "&&")), files)
                        added = True
                loose = list(examples_dir.glob("*.hv"))
                if loose:
                    if added:
                        submenu.addSeparator()
                    _add(submenu, loose)
                    added = True
        except OSError:
            pass

        if not added:
            placeholder = submenu.addAction("(no examples available)")
            placeholder.setEnabled(False)

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

    def _flush_inspector_edits(self) -> None:
        """Commit any pending (debounced) inspector edit before serialising.

        Called at the start of save/export and before the unsaved-changes check
        so the last keystrokes within the debounce window are never lost or
        written stale (§10.3).
        """
        self._props.flush_pending_edits()

    def _on_save(self) -> bool:
        """Save (Save As when untitled). Returns True when the file was written."""
        self._flush_inspector_edits()
        if self._current_path is None:
            return self._on_save_as()
        return self._do_save(self._current_path)

    def _on_save_as(self) -> bool:
        """Prompt for a path and save. Returns False when cancelled or failed."""
        self._flush_inspector_edits()
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Schematic", "", "Heaviside Schematics (*.hv);;All Files (*)"
        )
        if not path:
            return False
        if not path.endswith(".hv"):
            path += ".hv"
        return self._do_save(Path(path))

    def _do_save(self, path: Path) -> bool:
        """Write the .hv synchronously; report success. Auto-export runs in the
        background afterwards (it must never block or fail the save itself)."""
        self._flush_inspector_edits()
        try:
            save(self._scene.schematic, path)
        except SchematicSaveError as exc:
            # save() validates before writing, so the file on disk is untouched.
            QMessageBox.critical(
                self,
                "Save Error",
                f"{exc}\n\nThe existing file was not modified.",
            )
            return False
        except OSError as exc:
            QMessageBox.critical(self, "Save Error", str(exc))
            return False
        self._current_path = path
        self._modified = False
        self._update_title()
        self._auto_export(path)
        return True

    def _auto_export(self, path: Path) -> None:
        """Write sibling TeX/PDF/EPS/SVG/PNG files next to *path* if enabled in
        Preferences — on a **background thread**.

        Runs after a successful save so an ``\\includegraphics`` (or ``\\input``)
        of the sibling file stays in sync with the schematic (§10.8). The
        CircuiTikZ source and all settings are snapshotted **here, on the UI
        thread**; the pdflatex compile and format conversions then run in a
        ``QThreadPool`` task so Ctrl+S never freezes the UI. Jobs are
        single-flight: a save while an export is running queues (and replaces)
        the pending job rather than overlapping. Failures are reported in the
        status bar only and never block or fail the save itself.
        """
        want_tex = self._prefs.auto_export_tex
        want_pdf = self._prefs.auto_export_pdf
        want_eps = self._prefs.auto_export_eps
        want_svg = self._prefs.auto_export_svg
        want_png = self._prefs.auto_export_png
        if not (want_tex or want_pdf or want_eps or want_svg or want_png):
            return

        # Snapshot on the UI thread — the worker never touches the live model.
        try:
            source = generate(self._scene.schematic, y_flip=True,
                              mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                              mark_line_hops=self._prefs.line_hops)
        except Exception as exc:  # noqa: BLE001 — never block the save
            self._status_compile.setText(f"Auto-export failed ({exc})")
            return

        sch = self._scene.schematic
        job = _AutoExportJob(
            path=Path(path), source=source,
            want_tex=want_tex, want_pdf=want_pdf, want_eps=want_eps,
            want_svg=want_svg, want_png=want_png,
            png_dpi=self._prefs.png_dpi,
            siunitx=sch.siunitx, preamble=sch.preamble,
        )
        if self._auto_export_busy:
            # Single-flight: keep only the newest job; it is dispatched when
            # the running one finishes (see _on_auto_export_finished).
            self._auto_export_pending = job
            return
        self._dispatch_auto_export(job)

    def _dispatch_auto_export(self, job: _AutoExportJob) -> None:
        self._auto_export_busy = True
        self._status_compile.setText("Auto-exporting…")
        signals = _AutoExportSignals()
        signals.finished.connect(self._on_auto_export_finished)
        self._auto_export_signals = signals   # keep alive until the task reports
        QThreadPool.globalInstance().start(_AutoExportTask(job, signals))

    def _on_auto_export_finished(self, result: _AutoExportResult) -> None:
        # The app may be closing while a job runs: never touch destroyed widgets.
        if not _qt_is_valid(self):
            return
        # Render the PNG HERE, on the UI thread — Qt objects must not be built on a
        # worker (§8.1). The worker handed back only the Qt-free PDF bytes.
        if result.png is not None:
            pdf_bytes, target, dpi = result.png
            try:
                if pdf_to_qimage(pdf_bytes, dpi=dpi).save(str(target), "PNG"):
                    result.written.append(target.name)
                else:
                    result.failed.append(f"{target.name} (could not write)")
            except Exception as exc:  # noqa: BLE001 — never block on a bad render
                result.failed.append(f"{target.name} ({exc})")
        self._auto_export_busy = False
        self._auto_export_signals = None
        pending = self._auto_export_pending
        if pending is not None:
            self._auto_export_pending = None
            self._dispatch_auto_export(pending)
            return
        self._status_compile.setText(_format_export_message(result))

    def _on_preferences(self) -> None:
        """Open the modal Preferences dialog (§10.8).

        On accept, refresh the source panel and recompile the preview so a
        display change (e.g. marking unconnected pins) is reflected immediately.
        """
        if PreferencesDialog(self._prefs, self).exec() == QDialog.Accepted:
            self._scene.set_mark_unconnected_pins(self._prefs.mark_unconnected_pins)
            self._scene.set_line_hops(self._prefs.line_hops)
            self._view.set_placement_shortcuts(self._prefs.component_shortcuts)
            # Apply configured tool paths first so the engine choice, re-typeset,
            # and recompile below all see the updated discovery (§8.7 / §10.8).
            tools.set_tool_paths(self._prefs.tool_paths)
            # Apply the label-render engine choice and re-typeset existing labels
            # so a ziamath toggle (or a new latex path) is reflected immediately.
            mathrender.set_force_ziamath(self._prefs.force_ziamath)
            self._scene.retypeset_labels()
            self._source_panel.refresh()
            # Recompiles, or refreshes the no-LaTeX notice if a tool is still
            # missing (or clears it once a newly-configured path resolves).
            self._on_auto_compile()

    def _on_export_tex(self) -> None:
        """Export the schematic as an includable CircuiTikZ ``.tex`` snippet.

        The snippet uses ``y_flip=True`` so the included figure renders in the
        same orientation as the canvas (see §8.5).
        """
        self._flush_inspector_edits()
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
        sch = self._scene.schematic
        try:
            Path(path).write_text(
                build_snippet(source, siunitx=sch.siunitx,
                              extra_preamble=sch.preamble),
                encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Export Error", str(exc))
            return
        self._status_compile.setText(f"Exported to {Path(path).name}")

    def _compile_to_pdf(self, *, quiet: bool = False) -> bytes | None:
        """Generate source and compile it to PDF bytes for image export.

        Returns the PDF bytes, or None on failure (invalid schematic or
        ``pdflatex`` error).  When *quiet* is False, failures raise a modal
        error dialog; when True, they are silent so the caller can report via
        the status bar instead.
        """
        self._flush_inspector_edits()
        try:
            source = generate(self._scene.schematic, y_flip=True,
                            mark_unconnected_pins=self._prefs.mark_unconnected_pins,
                            mark_line_hops=self._prefs.line_hops)
        except Exception as exc:
            if not quiet:
                QMessageBox.critical(self, "Export Error", f"Cannot generate source:\n{exc}")
            return None
        sch = self._scene.schematic
        try:
            return compile_tex(build_tex(
                source, siunitx=sch.siunitx, extra_preamble=sch.preamble))
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
        """Return True if it is safe to replace/close the current document.

        Offers the standard **Save / Don't Save / Cancel** triad (Save is the
        default). Save runs the normal save flow — including Save As for an
        untitled document; if the user cancels that dialog or the save fails,
        the whole operation is treated as Cancel so no work is lost.
        """
        # A pending debounced inspector edit counts as a modification — commit
        # it before evaluating the modified state.
        self._flush_inspector_edits()
        if not self._modified:
            return True
        result = QMessageBox.question(
            self,
            "Unsaved changes",
            "The current schematic has unsaved changes.",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
            QMessageBox.Save,
        )
        if result == QMessageBox.Save:
            return self._on_save()
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

    def _on_help_shortcuts(self) -> None:
        _HelpDialog(self).exec()

    def _on_report_bug(self) -> None:
        """Open the project's GitHub issues page in the default browser."""
        QDesktopServices.openUrl(QUrl(_ISSUES_URL))

    # ------------------------------------------------------------------
    # Update check (opt-out; see app/update.py)
    # ------------------------------------------------------------------

    def _maybe_check_for_updates_on_startup(self) -> None:
        """Run the startup update probe when the preference is on (default)."""
        if not self._prefs.check_updates_on_startup:
            return
        # 0.0.0 means version resolution failed (e.g. a mis-packaged bundle) —
        # every release then looks "newer" and the prompt nags on each launch.
        # Skip the automatic probe; Help ▸ Check for Updates still works.
        if __version__ == "0.0.0":
            return
        _update.check_async(__version__, self._on_startup_update_result)

    def _on_startup_update_result(self, info) -> None:  # noqa: ANN001
        """Startup probe finished. Notify only for a not-yet-skipped version."""
        if info is None or info.version == self._prefs.skipped_update_version:
            return
        self._show_update_available(info, allow_skip=True)

    def _on_check_updates_manual(self) -> None:
        """Help ▸ Check for Updates — always probes and always reports."""
        self._act_check_updates.setEnabled(False)
        # Safety net: if the async result is ever lost (worker crash, dropped
        # signal), re-enable the menu item after a timeout so it can't stay
        # dead for the whole session.
        QTimer.singleShot(_UPDATE_CHECK_REENABLE_MS, self._reenable_check_updates)
        _update.check_async(__version__, self._on_manual_update_result)

    def _reenable_check_updates(self) -> None:
        if _qt_is_valid(self) and _qt_is_valid(self._act_check_updates):
            self._act_check_updates.setEnabled(True)

    def _on_manual_update_result(self, info) -> None:  # noqa: ANN001
        if not _qt_is_valid(self):
            return   # window closed while the probe ran
        # Re-enable first (and unconditionally), so a failure below — or an
        # exception while showing the result — can't leave the action disabled.
        self._act_check_updates.setEnabled(True)
        if info is None:
            QMessageBox.information(
                self,
                "Check for Updates",
                f"You're up to date.\n\nHeaviside {__version__} is the latest "
                f"version (or no newer release could be reached).",
            )
            return
        # A manual check honours the user's explicit request over any earlier skip.
        self._show_update_available(info, allow_skip=False)

    def _show_update_available(self, info, *, allow_skip: bool) -> None:  # noqa: ANN001
        """Non-blocking 'update available' prompt with Download / Skip / Later."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle("Update Available")
        label = f"Heaviside {info.version}" + (" (pre-release)" if info.prerelease else "")
        box.setText(f"{label} is available.")
        box.setInformativeText(
            f"You have {__version__}. Heaviside does not update itself — the "
            f"Download button opens the release page in your browser."
        )
        download_btn = box.addButton("Download…", QMessageBox.AcceptRole)
        skip_btn = (
            box.addButton("Skip This Version", QMessageBox.DestructiveRole)
            if allow_skip else None
        )
        box.addButton("Later", QMessageBox.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is download_btn:
            QDesktopServices.openUrl(QUrl(info.url or _update.RELEASES_PAGE_URL))
        elif skip_btn is not None and clicked is skip_btn:
            self._prefs.skipped_update_version = info.version

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
        ("Ctrl+R",       "Rotate the selection (or the placement ghost) 90° "
                         "clockwise (⌘R on macOS)."),
        ("Arrows",       "Nudge the selection 0.25 units (one minor-grid cell)."),
        ("Esc",          "Cancel placing/wiring and return to the Select tool."),
    ]),
    ("Component palette", [
        ("Ctrl+/",       "Focus the palette search box."),
        ("Letter keys",  "Place a component by its key — from the Select tool, or "
                         "while a ghost is up (pressing another key swaps the kind). "
                         "Defaults: r/c/l/d=resistor/capacitor/inductor/diode, "
                         "g=ground, t=transistor, v/i=voltage/current annotation. "
                         "Customize in Preferences ▸ Shortcuts."),
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

# Welcome-screen colours come from the active theme palette (read at paint
# time, so a light/dark switch repaints correctly — the background already
# follows style.COLOR_BACKGROUND, and now the diagram inks follow too).


class _WelcomeScreen(QWidget):
    """
    Solid welcome screen shown in the canvas slot before any document is
    active.  Draws only the Heaviside unit step function H(t) as a centred
    diagram. (The full keyboard-shortcut and gesture reference lives in
    **Help ▸ Keyboard Shortcuts & Gestures**, see :class:`_HelpDialog`.)

    Replaced by the live SchematicView (via QStackedWidget) as soon as the
    user creates/opens a document or begins component placement.
    """

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        w, h = float(self.width()), float(self.height())
        # Background follows the canvas paper colour (light/dark); the diagram
        # inks read the theme tokens live so they flip with it.
        painter.fillRect(self.rect(), QColor(style.COLOR_BACKGROUND))
        c_step = QColor(theme.WELCOME_STEP)
        c_axis = QColor(theme.WELCOME_AXIS)
        c_label = QColor(theme.WELCOME_LABEL)

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

        ax_pen = QPen(c_axis, 1.2, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(ax_pen)
        painter.drawLine(QPointF(left_x - 10, zero_y), QPointF(right_x + 20, zero_y))
        painter.drawLine(QPointF(origin_x, zero_y + 12), QPointF(origin_x, one_y - 20))
        _arrow_right(painter, c_axis, QPointF(right_x + 20, zero_y), size=7)
        _arrow_up   (painter, c_axis, QPointF(origin_x, one_y - 20), size=7)
        painter.drawLine(QPointF(origin_x - 5, one_y), QPointF(origin_x + 5, one_y))

        step_pen = QPen(c_step, 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        painter.setPen(step_pen)
        painter.drawLine(QPointF(left_x,   zero_y), QPointF(origin_x, zero_y))
        painter.drawLine(QPointF(origin_x, zero_y), QPointF(origin_x, one_y))
        painter.drawLine(QPointF(origin_x, one_y),  QPointF(right_x,  one_y))
        _open_dot  (painter, c_step, QPointF(origin_x, zero_y), r=4.5)
        _filled_dot(painter, c_step, QPointF(origin_x, one_y),  r=4.5)

        ann_font = QFont()
        ann_font.setPointSizeF(12.5)
        ann_font.setItalic(True)
        painter.setFont(ann_font)
        painter.setPen(QPen(c_label))
        painter.drawText(QPointF(right_x + 8, one_y + 5), "H(t)")
        ann_font.setItalic(False)
        ann_font.setPointSizeF(11.0)
        painter.setFont(ann_font)
        painter.drawText(QPointF(origin_x - 16, one_y + 5),  "1")
        painter.drawText(QPointF(right_x + 22,  zero_y + 5), "t")


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
        # Theme tokens (read at construction; the dialog is rebuilt on every
        # open, so a light/dark switch is picked up the next time it shows).
        key_color = QColor(theme.TABLE_KEY)
        desc_color = QColor(theme.TEXT)
        hdr_color = QColor(theme.ICON_MUTED)
        hdr_bg = QColor(theme.TABLE_HEADER_BG)
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
            f"font-size: 15px; font-weight: bold; color: {theme.HEADING};"
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
        sub_label.setStyleSheet(f"font-size: 12px; color: {theme.TEXT_MUTED};")
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
        llm_label.setStyleSheet(f"font-size: 11px; color: {theme.ICON_MUTED};")
        layout.addWidget(llm_label)

        layout.addSpacing(12)

        # Third-party acknowledgements. The full notices (Qt/PySide6 LGPLv3,
        # bundled math/icon fonts, etc.) ship in the licenses/ folder inside the
        # application; the button opens it.
        ack_label = QLabel(
            "Uses Qt/PySide6 (LGPLv3), ziamath, qtawesome, and bundled fonts "
            "(STIX Two Math, DejaVu Sans, Font Awesome, and others)."
        )
        ack_label.setWordWrap(True)
        ack_label.setAlignment(Qt.AlignCenter)
        ack_label.setStyleSheet(f"font-size: 10px; color: {theme.ICON_MUTED};")
        layout.addWidget(ack_label)

        layout.addSpacing(20)

        # Divider
        divider = QWidget()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background: {theme.DIVIDER};")
        layout.addWidget(divider)

        layout.addSpacing(16)

        # Quote
        quote_label = QLabel(_HEAVISIDE_QUOTE)
        quote_label.setWordWrap(True)
        quote_label.setAlignment(Qt.AlignCenter)
        quote_label.setStyleSheet(f"font-size: 11px; color: {theme.TEXT_MUTED}; font-style: italic;")
        layout.addWidget(quote_label)

        layout.addSpacing(20)

        # OK button, plus a button that reveals the bundled third-party license
        # texts (Qt/PySide6 LGPLv3, fonts, etc.) so attributions are discoverable
        # from the GUI, not only on disk.
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        licenses_btn = buttons.addButton(
            "Third-Party Licenses…", QDialogButtonBox.ActionRole
        )
        licenses_btn.clicked.connect(self._open_licenses)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

    @staticmethod
    def _open_licenses() -> None:
        """Open the bundled licenses/ folder in the platform file browser."""
        licenses_dir = resource_path("licenses")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(licenses_dir)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _separator() -> QWidget:
    """A thin vertical separator for the status bar (theme-aware; restyled on a
    light/dark switch by MainWindow._apply_theme)."""
    sep = QWidget()
    sep.setObjectName("statusSeparator")
    sep.setFixedWidth(1)
    sep.setStyleSheet(f"background: {theme.DIVIDER};")
    return sep


def _latex_install_hint() -> str:
    """A one-line, OS-appropriate LaTeX-distribution recommendation (rich text
    with links) for the no-LaTeX preview notice. Distributions are chosen to
    include — or make installable — the ``circuitikz`` package the preview needs."""
    if sys.platform == "darwin":
        return ('Install <a href="https://www.tug.org/mactex/">MacTeX</a> (or the '
                'smaller BasicTeX), then restart Heaviside.')
    if sys.platform.startswith("win"):
        return ('Install <a href="https://miktex.org/">MiKTeX</a> or '
                '<a href="https://tug.org/texlive/">TeX&nbsp;Live</a>, then restart '
                'Heaviside.')
    # Linux / other Unix — apt covers Debian/Ubuntu/Raspberry Pi OS, where
    # circuitikz lives in the texlive-pictures package.
    return ('Install <a href="https://tug.org/texlive/">TeX&nbsp;Live</a> — on '
            'Debian/Ubuntu/Raspberry&nbsp;Pi&nbsp;OS: '
            '<code>sudo apt install texlive texlive-pictures</code> — then restart '
            'Heaviside.')


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

        # Centered, *light* notice shown when the LaTeX toolchain is absent — an
        # icon above a rich-text message with OS-specific install guidance. This
        # is the "pdflatex not found" state (a missing install, distinct from a
        # document compile error), so it deliberately avoids the alarming red of
        # `_error_label` below. Given the whole preview area (image hidden) so it
        # sits centred in the middle.
        self._notice = QWidget()
        self._notice.setObjectName("prevNotice")
        notice_col = QVBoxLayout(self._notice)
        notice_col.setContentsMargins(24, 24, 24, 24)
        notice_col.setSpacing(12)
        notice_col.setAlignment(Qt.AlignCenter)
        self._notice_icon = QLabel()
        self._notice_icon.setAlignment(Qt.AlignCenter)
        self._notice_text = QLabel()
        self._notice_text.setAlignment(Qt.AlignCenter)
        self._notice_text.setWordWrap(True)
        self._notice_text.setTextFormat(Qt.RichText)
        self._notice_text.setOpenExternalLinks(True)
        self._notice_text.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self._notice_text.setMaximumWidth(380)
        notice_col.addWidget(self._notice_icon, 0, Qt.AlignHCenter)
        notice_col.addWidget(self._notice_text, 0, Qt.AlignHCenter)
        self._notice.hide()
        layout.addWidget(self._notice, 1)

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
        # The no-LaTeX notice sits on the figure's page colour like the image; its
        # icon and text follow the theme's muted tokens. Re-render the rich text
        # while visible so its embedded colours track a light/dark switch.
        self._notice.setStyleSheet(
            "QWidget#prevNotice { border: none; background: %s; }" % style.COLOR_BACKGROUND
        )
        self._notice_icon.setPixmap(
            qta.icon("fa5s.info-circle", color=theme.ICON_MUTED).pixmap(40, 40)
        )
        # Keep the install links legible in both themes (the default link blue is
        # too dark on the dark page colour).
        npal = self._notice_text.palette()
        npal.setColor(QPalette.Link, QColor(theme.ACCENT))
        self._notice_text.setPalette(npal)
        if self._notice.isVisible():
            self._notice_text.setText(self._no_latex_html())
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
        self._notice.hide()
        self._img_label.show()
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
        self._img_label.show()
        self._notice.hide()
        self._error_label.setText(error[:400])
        self._error_label.show()

    def show_no_latex(self) -> None:
        """Show the centered, light "LaTeX not found" notice in place of the
        preview — the missing-`pdflatex` state, with OS-specific install guidance.
        Replaces both the red error label and the old startup warning dialog."""
        self._raw_image = None
        self._img_label.clear()
        self._img_label.hide()        # hand the whole area to the centred notice
        self._error_label.hide()
        self._notice_text.setText(self._no_latex_html())
        self._notice.show()

    def _no_latex_html(self) -> str:
        """Rich-text body for the no-LaTeX notice, themed and OS-aware."""
        return (
            f'<div style="color:{theme.HEADING}; font-size:14px; '
            'font-weight:600;">LaTeX not found</div>'
            f'<div style="color:{theme.TEXT_MUTED}; font-size:11px; '
            'margin-top:8px;">Heaviside could not find <b>pdflatex</b> on your '
            'PATH. The live preview and PDF/PNG export need a LaTeX distribution '
            'with the <b>circuitikz</b> package.</div>'
            f'<div style="color:{theme.TEXT_MUTED}; font-size:11px; '
            f'margin-top:8px;">{_latex_install_hint()}</div>'
            f'<div style="color:{theme.TEXT_MUTED}; font-size:11px; '
            'margin-top:8px;">Already installed? Set its path in '
            '<b>Preferences ▸ Tools</b>.</div>'
        )

    def clear(self) -> None:
        self._raw_image = None
        self._img_label.clear()
        self._img_label.show()
        self._error_label.hide()
        self._notice.hide()
