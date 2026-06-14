"""
Application preferences (spec §10.8).

Persistent user settings are stored via ``QSettings`` (backed by the platform
native store, keyed by the organization/application names set in ``main.py``).

``Preferences`` is a thin typed wrapper around a ``QSettings`` instance so the
rest of the app never touches raw string keys.  It accepts an optional
``QSettings`` for testability; production code uses the default.

``PreferencesDialog`` is the modal editor shown by **Edit → Preferences…**.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from app.components.registry import REGISTRY
from app.preview import tools as _tools
from app.ui import theme

# QSettings keys.
_KEY_AUTO_TEX = "export/auto_tex_on_save"
_KEY_AUTO_PDF = "export/auto_pdf_on_save"
_KEY_AUTO_EPS = "export/auto_eps_on_save"
_KEY_AUTO_SVG = "export/auto_svg_on_save"
_KEY_AUTO_PNG = "export/auto_png_on_save"
_KEY_PNG_DPI = "export/png_dpi"

#: Default raster resolution for PNG copy/export — 300 dpi is publication grade.
_DEFAULT_PNG_DPI = 300
#: Valid PNG DPI range (matches the Preferences dialog's spinbox bounds).
_PNG_DPI_MIN = 72
_PNG_DPI_MAX = 1200
_KEY_MARK_OPEN_PINS = "display/mark_unconnected_pins"
_KEY_LINE_HOPS = "display/line_hops"
_KEY_DARK_OVERRIDE = "display/dark_override"
_KEY_FORCE_ZIAMATH = "render/force_ziamath"
_KEY_CHECK_UPDATES = "updates/check_on_startup"
_KEY_SKIPPED_VERSION = "updates/skipped_version"
_KEY_COMPONENT_SHORTCUTS = "keybindings/component_shortcuts"

#: Plain letters the canvas reserves for tools (select/wire/pan), so they can't be
#: bound to component placement. ``r`` is *not* reserved — it is context-sensitive
#: (rotate when there's a selection/ghost, else place its bound component).
RESERVED_SHORTCUT_KEYS = frozenset({"s", "w", "p"})

#: Built-in key → component-kind placement map (spec §10.2). Pressing the key from
#: the Select tool starts placing that component; user-overridable in Preferences.
#: ``v``/``i`` map to the voltage/current annotations (kinds ``open``/``short``).
DEFAULT_COMPONENT_SHORTCUTS: dict[str, str] = {
    "r": "R", "c": "C", "l": "L", "d": "D",
    "g": "ground", "t": "npn", "v": "open", "i": "short",
}


def _sanitize_shortcuts(mapping: object) -> dict[str, str]:
    """Coerce a stored/loaded shortcut map to a clean ``{single-letter: kind}`` dict:
    drop keys that aren't one ``a``–``z`` letter, reserved tool keys, and values that
    aren't a known component kind. Keeps the app safe against a hand-edited setting."""
    if not isinstance(mapping, dict):
        return {}
    clean: dict[str, str] = {}
    for raw_key, kind in mapping.items():
        key = str(raw_key).lower()
        if len(key) != 1 or not ("a" <= key <= "z"):
            continue
        if key in RESERVED_SHORTCUT_KEYS:
            continue
        if kind in REGISTRY:
            clean[key] = kind
    return clean


def _hint_qss() -> str:
    """Stylesheet for the dialog's small hint labels.

    Reads the active theme token at call time so a dialog opened in dark mode
    gets readable (light) hint ink instead of the old hardcoded ``#666``. The
    dialog is constructed fresh on every open, so this tracks theme switches.
    """
    return f"color: {theme.ICON_MUTED}; font-size: 11px;"


def _to_bool(value: object, default: bool = False) -> bool:
    """Coerce a QSettings value (often a string) to bool.

    QSettings on some platforms returns booleans as the strings "true"/"false".
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if value is None:
        return default
    return bool(value)


class Preferences:
    """Typed accessor for persistent user settings."""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings if settings is not None else QSettings()

    # -- Auto-export on save -------------------------------------------------

    @property
    def auto_export_tex(self) -> bool:
        # On by default: the CircuiTikZ `.tex` fragment is the primary output for
        # the LaTeX/Overleaf/LyX audience (\input it into a paper), and unlike
        # SVG/EPS it needs no pdflatex or converter — codegen always succeeds. With
        # PDF + PNG it forms the default sibling set; EPS/SVG stay opt-in.
        return _to_bool(self._settings.value(_KEY_AUTO_TEX), default=True)

    @auto_export_tex.setter
    def auto_export_tex(self, value: bool) -> None:
        self._settings.setValue(_KEY_AUTO_TEX, bool(value))

    @property
    def auto_export_pdf(self) -> bool:
        # On by default: PDF is the figure format LyX/Overleaf/pdflatex include
        # natively and it needs nothing beyond the pdflatex compile the preview
        # already requires. A missing pdflatex fails the export non-fatally
        # (status-bar notice), never the save itself.
        return _to_bool(self._settings.value(_KEY_AUTO_PDF), default=True)

    @auto_export_pdf.setter
    def auto_export_pdf(self, value: bool) -> None:
        self._settings.setValue(_KEY_AUTO_PDF, bool(value))

    @property
    def auto_export_eps(self) -> bool:
        return _to_bool(self._settings.value(_KEY_AUTO_EPS), default=False)

    @auto_export_eps.setter
    def auto_export_eps(self, value: bool) -> None:
        self._settings.setValue(_KEY_AUTO_EPS, bool(value))

    @property
    def auto_export_svg(self) -> bool:
        # Off by default: SVG is for destinations *outside* LaTeX (Office, web,
        # vector editors) and is the only format needing a PDF→vector converter
        # (pdftocairo, or Inkscape as the automatic fallback) — defaulting it on
        # made every save by a converter-less user fail the export with a
        # status-bar notice. The core LaTeX workflow (.tex + PDF/PNG) needs no
        # converter; users who want .svg siblings opt in here.
        return _to_bool(self._settings.value(_KEY_AUTO_SVG), default=False)

    @auto_export_svg.setter
    def auto_export_svg(self, value: bool) -> None:
        self._settings.setValue(_KEY_AUTO_SVG, bool(value))

    @property
    def auto_export_png(self) -> bool:
        # On by default: a raster preview for quick sharing/slides. Needs pdflatex
        # (same non-fatal failure handling as SVG). EPS/SVG stay off by default
        # (converter-dependent).
        return _to_bool(self._settings.value(_KEY_AUTO_PNG), default=True)

    @auto_export_png.setter
    def auto_export_png(self, value: bool) -> None:
        self._settings.setValue(_KEY_AUTO_PNG, bool(value))

    @property
    def png_dpi(self) -> int:
        """Raster resolution (dots per inch) for PNG copy and export.

        Clamped to the dialog's 72–1200 range so a corrupt/hand-edited settings
        value can never trigger an absurd (multi-GB) render on every save.
        """
        try:
            dpi = int(self._settings.value(_KEY_PNG_DPI, _DEFAULT_PNG_DPI))
        except (TypeError, ValueError):
            return _DEFAULT_PNG_DPI
        return max(_PNG_DPI_MIN, min(_PNG_DPI_MAX, dpi))

    @png_dpi.setter
    def png_dpi(self, value: int) -> None:
        self._settings.setValue(_KEY_PNG_DPI, int(value))

    # -- Display -------------------------------------------------------------

    @property
    def mark_unconnected_pins(self) -> bool:
        return _to_bool(self._settings.value(_KEY_MARK_OPEN_PINS), default=False)

    @mark_unconnected_pins.setter
    def mark_unconnected_pins(self, value: bool) -> None:
        self._settings.setValue(_KEY_MARK_OPEN_PINS, bool(value))

    @property
    def line_hops(self) -> bool:
        # Defaults on: drawing a hop at a non-connecting crossing is the
        # schematic-drawing convention (spec §6.4).
        return _to_bool(self._settings.value(_KEY_LINE_HOPS), default=True)

    @line_hops.setter
    def line_hops(self, value: bool) -> None:
        self._settings.setValue(_KEY_LINE_HOPS, bool(value))

    @property
    def dark_override(self) -> bool | None:
        """The user's pinned theme, or ``None`` to follow the OS appearance.

        ``None`` (the unset default) means "track the system light/dark setting";
        once the user flips the toolbar toggle, an explicit ``True`` (dark) /
        ``False`` (light) is stored and restored on the next launch."""
        val = self._settings.value(_KEY_DARK_OVERRIDE)
        if val is None or val == "":
            return None
        if isinstance(val, str) and val.lower() in ("dark", "light"):
            return val.lower() == "dark"
        return _to_bool(val, default=False)

    def set_dark_override(self, value: bool | None) -> None:
        """Pin the theme to dark/light, or pass ``None`` to clear it (follow OS)."""
        if value is None:
            self._settings.remove(_KEY_DARK_OVERRIDE)
        else:
            self._settings.setValue(_KEY_DARK_OVERRIDE, "dark" if value else "light")

    # -- Rendering -----------------------------------------------------------

    @property
    def force_ziamath(self) -> bool:
        # Debug aid: force the pure-Python ziamath label renderer even when a
        # LaTeX install is present (which would otherwise be preferred).
        return _to_bool(self._settings.value(_KEY_FORCE_ZIAMATH), default=False)

    @force_ziamath.setter
    def force_ziamath(self, value: bool) -> None:
        self._settings.setValue(_KEY_FORCE_ZIAMATH, bool(value))

    # -- Updates -------------------------------------------------------------

    @property
    def check_updates_on_startup(self) -> bool:
        # Defaults on: a single read-only GitHub Releases check at launch keeps
        # alpha users current. Opt out here or via Help ▸ Check for Updates.
        return _to_bool(self._settings.value(_KEY_CHECK_UPDATES), default=True)

    @check_updates_on_startup.setter
    def check_updates_on_startup(self, value: bool) -> None:
        self._settings.setValue(_KEY_CHECK_UPDATES, bool(value))

    @property
    def skipped_update_version(self) -> str:
        """A version the user chose to skip; the startup check won't re-prompt
        for it (a manual check still will)."""
        return str(self._settings.value(_KEY_SKIPPED_VERSION, "") or "")

    @skipped_update_version.setter
    def skipped_update_version(self, value: str) -> None:
        self._settings.setValue(_KEY_SKIPPED_VERSION, str(value or ""))

    # -- External tool paths -------------------------------------------------
    #
    # Explicit paths to pdflatex/latex/dvisvgm/pdftocairo/inkscape. Empty means
    # "discover on PATH" (the default). Consumed by app.preview.tools via
    # set_tool_paths.

    def tool_path(self, name: str) -> str:
        return str(self._settings.value(f"tools/{name}", "") or "")

    def set_tool_path(self, name: str, value: str) -> None:
        self._settings.setValue(f"tools/{name}", str(value or "").strip())

    @property
    def tool_paths(self) -> dict[str, str]:
        return {name: self.tool_path(name) for name in _tools.TOOLS}

    # -- Component placement shortcuts ---------------------------------------

    @property
    def component_shortcuts(self) -> dict[str, str]:
        """The key → component-kind placement map (spec §10.2).

        Returns :data:`DEFAULT_COMPONENT_SHORTCUTS` when unset (first run), and the
        stored map otherwise — always sanitized, so a corrupt/hand-edited value can
        never bind a reserved key or an unknown kind."""
        raw = self._settings.value(_KEY_COMPONENT_SHORTCUTS)
        if raw is None or raw == "":
            return dict(DEFAULT_COMPONENT_SHORTCUTS)
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return dict(DEFAULT_COMPONENT_SHORTCUTS)
        return _sanitize_shortcuts(parsed)

    @component_shortcuts.setter
    def component_shortcuts(self, value: dict[str, str]) -> None:
        self._settings.setValue(
            _KEY_COMPONENT_SHORTCUTS, json.dumps(_sanitize_shortcuts(value))
        )


class PreferencesDialog(QDialog):
    """Modal preferences editor.

    Reads current values from *prefs* on open and writes them back only when the
    user accepts (OK).  Cancel discards changes.
    """

    def __init__(self, prefs: Preferences, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._prefs = prefs
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.setMinimumWidth(520)  # room for the tool path fields + Browse button

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        tabs = QTabWidget()
        layout.addWidget(tabs)

        def _page(*widgets: QWidget) -> QWidget:
            """A tab page that stacks *widgets* at the top with trailing stretch."""
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(6, 12, 6, 6)
            page_layout.setSpacing(12)
            for w in widgets:
                page_layout.addWidget(w)
            page_layout.addStretch(1)
            return page

        group = QGroupBox("Auto-export on save")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(6)

        self._chk_tex = QCheckBox("Export a TeX snippet next to the schematic file")
        self._chk_tex.setChecked(prefs.auto_export_tex)
        group_layout.addWidget(self._chk_tex)

        self._chk_pdf = QCheckBox("Export a PDF next to the schematic file")
        self._chk_pdf.setChecked(prefs.auto_export_pdf)
        group_layout.addWidget(self._chk_pdf)

        self._chk_eps = QCheckBox("Export an EPS next to the schematic file")
        self._chk_eps.setChecked(prefs.auto_export_eps)
        group_layout.addWidget(self._chk_eps)

        self._chk_svg = QCheckBox("Export an SVG next to the schematic file")
        self._chk_svg.setChecked(prefs.auto_export_svg)
        group_layout.addWidget(self._chk_svg)

        self._chk_png = QCheckBox("Export a PNG next to the schematic file")
        self._chk_png.setChecked(prefs.auto_export_png)
        group_layout.addWidget(self._chk_png)

        # PNG resolution (shared by Copy PNG and Export/auto-export PNG).
        dpi_row = QHBoxLayout()
        dpi_row.setContentsMargins(0, 0, 0, 0)
        dpi_row.addWidget(QLabel("PNG resolution:"))
        self._spin_dpi = QSpinBox()
        self._spin_dpi.setRange(_PNG_DPI_MIN, _PNG_DPI_MAX)
        self._spin_dpi.setSingleStep(50)
        self._spin_dpi.setSuffix(" dpi")
        self._spin_dpi.setValue(prefs.png_dpi)
        dpi_row.addWidget(self._spin_dpi)
        dpi_row.addStretch(1)
        group_layout.addLayout(dpi_row)

        hint = QLabel(
            "When saving <name>.hv, also write <name>.tex / <name>.pdf / <name>.eps "
            "/ <name>.svg / <name>.png to the same folder so an \\input or "
            "\\includegraphics in your LaTeX document stays up to date.  The TeX "
            "snippet needs nothing; PDF/EPS/SVG/PNG require pdflatex (EPS/SVG also "
            "need pdftocairo or Inkscape). PNG resolution applies to both Copy PNG "
            "and PNG export (300 dpi is publication grade)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(_hint_qss())
        group_layout.addWidget(hint)

        display_group = QGroupBox("Display")
        display_layout = QVBoxLayout(display_group)
        display_layout.setSpacing(6)

        self._chk_open_pins = QCheckBox("Mark unconnected component pins with open circles")
        self._chk_open_pins.setChecked(prefs.mark_unconnected_pins)
        display_layout.addWidget(self._chk_open_pins)

        pins_hint = QLabel(
            "Draws a small open circle (ocirc) at every component terminal that "
            "has no wire attached, in the preview, source, and exports."
        )
        pins_hint.setWordWrap(True)
        pins_hint.setStyleSheet(_hint_qss())
        display_layout.addWidget(pins_hint)

        self._chk_line_hops = QCheckBox("Draw line-hops where wires cross without connecting")
        self._chk_line_hops.setChecked(prefs.line_hops)
        display_layout.addWidget(self._chk_line_hops)

        hops_hint = QLabel(
            "Draws a small semicircular bump on one wire where two wires cross "
            "but do not connect, so the crossing reads unambiguously. The wire "
            "with the higher z-order hops over the other."
        )
        hops_hint.setWordWrap(True)
        hops_hint.setStyleSheet(_hint_qss())
        display_layout.addWidget(hops_hint)

        render_group = QGroupBox("Rendering")
        render_layout = QVBoxLayout(render_group)
        render_layout.setSpacing(6)

        self._chk_force_ziamath = QCheckBox(
            "Force the built-in (ziamath) label renderer"
        )
        self._chk_force_ziamath.setChecked(prefs.force_ziamath)
        render_layout.addWidget(self._chk_force_ziamath)

        ziamath_hint = QLabel(
            "Typeset on-canvas equation labels with the bundled, pure-Python "
            "renderer instead of a system LaTeX install. Used automatically when "
            "LaTeX is unavailable; enable here to force it (e.g. for debugging) "
            "even when LaTeX is installed."
        )
        ziamath_hint.setWordWrap(True)
        ziamath_hint.setStyleSheet(_hint_qss())
        render_layout.addWidget(ziamath_hint)

        # Single-section tab: the tab label is the heading, so no group box.
        self._chk_check_updates = QCheckBox("Check for updates on startup")
        self._chk_check_updates.setChecked(prefs.check_updates_on_startup)

        updates_hint = QLabel(
            "On launch, make a single read-only request to the GitHub Releases "
            "page to see whether a newer version exists, and notify you if so. "
            "Heaviside never downloads or installs anything automatically, and "
            "sends no information about you. You can also check any time from "
            "Help ▸ Check for Updates."
        )
        updates_hint.setWordWrap(True)
        updates_hint.setStyleSheet(_hint_qss())

        # Single-section tab: the tab label is the heading, so no group box.
        tools_group = QWidget()
        tools_grid = QGridLayout(tools_group)
        tools_grid.setContentsMargins(0, 0, 0, 0)
        tools_grid.setHorizontalSpacing(8)
        tools_grid.setVerticalSpacing(4)
        tools_grid.setColumnStretch(1, 1)  # the path field column takes the slack
        tools_intro = QLabel(
            "Explicit paths to the external tools. Leave blank to auto-detect on "
            "your <tt>PATH</tt>. Set a path only if a tool isn't found or you want "
            "a specific install."
        )
        tools_intro.setWordWrap(True)
        tools_intro.setStyleSheet(_hint_qss())
        tools_grid.addWidget(tools_intro, 0, 0, 1, 3)

        self._tool_edits: dict[str, QLineEdit] = {}
        self._tool_status: dict[str, QLabel] = {}
        for i, name in enumerate(_tools.TOOLS):
            # Two grid rows per tool: [name | path field | Browse] then a status
            # line spanning the field+button so it never squashes the edit.
            r = 1 + i * 2
            edit = QLineEdit(prefs.tool_path(name))
            edit.setPlaceholderText("auto-detect on PATH")
            edit.setMinimumWidth(280)
            edit.setClearButtonEnabled(True)
            browse = QPushButton("Browse…")
            browse.clicked.connect(lambda _checked=False, n=name: self._browse_tool(n))
            status = QLabel()
            status.setStyleSheet(_hint_qss())
            tools_grid.addWidget(QLabel(name), r, 0, Qt.AlignRight)
            tools_grid.addWidget(edit, r, 1)
            tools_grid.addWidget(browse, r, 2)
            tools_grid.addWidget(status, r + 1, 1, 1, 2)
            self._tool_edits[name] = edit
            self._tool_status[name] = status
            edit.textChanged.connect(lambda _t="", n=name: self._update_tool_status(n))
            self._update_tool_status(name)

        tabs.addTab(_page(group), "Export")
        tabs.addTab(_page(display_group, render_group), "Appearance")
        tabs.addTab(self._build_shortcuts_tab(), "Shortcuts")
        tabs.addTab(_page(tools_group), "Tools")
        tabs.addTab(_page(self._chk_check_updates, updates_hint), "Updates")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # -- Shortcuts tab -------------------------------------------------------

    def _build_shortcuts_tab(self) -> QWidget:
        """A key → component table for the placement shortcuts (spec §10.2)."""
        # (label, kind) once, ordered by category then display name, so every row's
        # component combo is built from the same sorted list.
        self._kind_choices = [
            (f"{d.display_name} ({k})", k)
            for k, d in sorted(
                REGISTRY.items(),
                key=lambda kv: (kv[1].category, kv[1].display_name.lower()),
            )
        ]

        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(6, 12, 6, 6)
        v.setSpacing(8)

        intro = QLabel(
            "Press a key from the Select tool to start placing a component. "
            "<b>r</b> is context-sensitive — it rotates when something is selected "
            "(or while you're placing), and places its component only when nothing "
            "is selected. <b>s</b>, <b>w</b>, and <b>p</b> are reserved for the "
            "Select / Wire / Pan tools."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(_hint_qss())
        v.addWidget(intro)

        self._shortcut_table = QTableWidget(0, 2)
        self._shortcut_table.setHorizontalHeaderLabels(["Key", "Component"])
        self._shortcut_table.verticalHeader().setVisible(False)
        self._shortcut_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._shortcut_table.setSelectionMode(QAbstractItemView.SingleSelection)
        header = self._shortcut_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        v.addWidget(self._shortcut_table, 1)

        btn_row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(lambda: self._add_shortcut_row("", ""))
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._remove_selected_shortcut)
        restore = QPushButton("Restore defaults")
        restore.clicked.connect(
            lambda: self._populate_shortcuts(DEFAULT_COMPONENT_SHORTCUTS)
        )
        btn_row.addWidget(add)
        btn_row.addWidget(remove)
        btn_row.addStretch(1)
        btn_row.addWidget(restore)
        v.addLayout(btn_row)

        self._populate_shortcuts(self._prefs.component_shortcuts)
        return page

    def _add_shortcut_row(self, key: str, kind: str) -> None:
        table = self._shortcut_table
        row = table.rowCount()
        table.insertRow(row)

        key_edit = QLineEdit(key)
        key_edit.setMaxLength(1)
        key_edit.setPlaceholderText("key")
        # Lower-case as the user types so the stored map is canonical.
        key_edit.textChanged.connect(
            lambda text, e=key_edit: e.text() != text.lower() and e.setText(text.lower())
        )
        table.setCellWidget(row, 0, key_edit)

        combo = QComboBox()
        for label, k in self._kind_choices:
            combo.addItem(label, k)
        if kind:
            idx = combo.findData(kind)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        table.setCellWidget(row, 1, combo)

    def _remove_selected_shortcut(self) -> None:
        rows = {idx.row() for idx in self._shortcut_table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self._shortcut_table.removeRow(row)

    def _populate_shortcuts(self, mapping: dict[str, str]) -> None:
        self._shortcut_table.setRowCount(0)
        for key in sorted(mapping):
            self._add_shortcut_row(key, mapping[key])

    def _collect_shortcuts(self) -> tuple[dict[str, str] | None, str | None]:
        """Read the table into a clean map, or return ``(None, error)`` on a bad row
        (non-letter key, a reserved tool key, or a duplicate key)."""
        reserved_names = {"s": "Select", "w": "Wire", "p": "Pan"}
        mapping: dict[str, str] = {}
        table = self._shortcut_table
        for row in range(table.rowCount()):
            key = table.cellWidget(row, 0).text().strip().lower()
            kind = table.cellWidget(row, 1).currentData()
            if not key:
                continue  # an empty key row is simply ignored
            if len(key) != 1 or not ("a" <= key <= "z"):
                return None, f"“{key}” is not a single letter a–z."
            if key in RESERVED_SHORTCUT_KEYS:
                return None, f"“{key}” is reserved for the {reserved_names[key]} tool."
            if key in mapping:
                return None, f"“{key}” is bound to more than one component."
            mapping[key] = kind
        return mapping, None

    def _browse_tool(self, name: str) -> None:
        """Pick an executable for tool *name* via a file dialog."""
        start = self._tool_edits[name].text().strip()
        path, _ = QFileDialog.getOpenFileName(self, f"Locate {name}", start)
        if path:
            self._tool_edits[name].setText(path)

    def _update_tool_status(self, name: str) -> None:
        """Reflect where tool *name* would resolve given the field's current text."""
        value = self._tool_edits[name].text().strip()
        if value:
            if _tools.is_runnable(value):
                self._tool_status[name].setText("✓ will use this path")
            else:
                self._tool_status[name].setText(
                    "⚠ not an executable file — will fall back to PATH"
                )
        else:
            found = _tools.path_on_path(name)
            self._tool_status[name].setText(
                f"✓ found on PATH: {found}" if found else "✗ not found on PATH"
            )

    def _on_accept(self) -> None:
        """Persist the checkbox state to the Preferences store and close."""
        # Validate the shortcut table first so an invalid binding keeps the dialog
        # open (and nothing is persisted) rather than silently dropping a row.
        shortcuts, error = self._collect_shortcuts()
        if error is not None:
            QMessageBox.warning(self, "Invalid shortcut", error)
            return
        self._prefs.component_shortcuts = shortcuts
        self._prefs.auto_export_tex = self._chk_tex.isChecked()
        self._prefs.auto_export_pdf = self._chk_pdf.isChecked()
        self._prefs.auto_export_eps = self._chk_eps.isChecked()
        self._prefs.auto_export_svg = self._chk_svg.isChecked()
        self._prefs.auto_export_png = self._chk_png.isChecked()
        self._prefs.png_dpi = self._spin_dpi.value()
        self._prefs.mark_unconnected_pins = self._chk_open_pins.isChecked()
        self._prefs.line_hops = self._chk_line_hops.isChecked()
        self._prefs.force_ziamath = self._chk_force_ziamath.isChecked()
        self._prefs.check_updates_on_startup = self._chk_check_updates.isChecked()
        for name, edit in self._tool_edits.items():
            self._prefs.set_tool_path(name, edit.text())
        self.accept()
