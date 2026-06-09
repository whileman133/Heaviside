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

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.preview import tools as _tools

# QSettings keys.
_KEY_AUTO_TEX = "export/auto_tex_on_save"
_KEY_AUTO_PDF = "export/auto_pdf_on_save"
_KEY_AUTO_EPS = "export/auto_eps_on_save"
_KEY_AUTO_SVG = "export/auto_svg_on_save"
_KEY_AUTO_PNG = "export/auto_png_on_save"
_KEY_PNG_DPI = "export/png_dpi"

#: Default raster resolution for PNG copy/export — 300 dpi is publication grade.
_DEFAULT_PNG_DPI = 300
_KEY_MARK_OPEN_PINS = "display/mark_unconnected_pins"
_KEY_LINE_HOPS = "display/line_hops"
_KEY_FORCE_ZIAMATH = "render/force_ziamath"


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
        return _to_bool(self._settings.value(_KEY_AUTO_TEX), default=False)

    @auto_export_tex.setter
    def auto_export_tex(self, value: bool) -> None:
        self._settings.setValue(_KEY_AUTO_TEX, bool(value))

    @property
    def auto_export_pdf(self) -> bool:
        return _to_bool(self._settings.value(_KEY_AUTO_PDF), default=False)

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
        return _to_bool(self._settings.value(_KEY_AUTO_SVG), default=False)

    @auto_export_svg.setter
    def auto_export_svg(self, value: bool) -> None:
        self._settings.setValue(_KEY_AUTO_SVG, bool(value))

    @property
    def auto_export_png(self) -> bool:
        return _to_bool(self._settings.value(_KEY_AUTO_PNG), default=False)

    @auto_export_png.setter
    def auto_export_png(self, value: bool) -> None:
        self._settings.setValue(_KEY_AUTO_PNG, bool(value))

    @property
    def png_dpi(self) -> int:
        """Raster resolution (dots per inch) for PNG copy and export."""
        try:
            return int(self._settings.value(_KEY_PNG_DPI, _DEFAULT_PNG_DPI))
        except (TypeError, ValueError):
            return _DEFAULT_PNG_DPI

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

    # -- Rendering -----------------------------------------------------------

    @property
    def force_ziamath(self) -> bool:
        # Debug aid: force the pure-Python ziamath label renderer even when a
        # LaTeX install is present (which would otherwise be preferred).
        return _to_bool(self._settings.value(_KEY_FORCE_ZIAMATH), default=False)

    @force_ziamath.setter
    def force_ziamath(self, value: bool) -> None:
        self._settings.setValue(_KEY_FORCE_ZIAMATH, bool(value))

    # -- External tool paths -------------------------------------------------
    #
    # Explicit paths to pdflatex/latex/dvisvgm/pdftocairo. Empty means "discover
    # on PATH" (the default). Consumed by app.preview.tools via set_tool_paths.

    def tool_path(self, name: str) -> str:
        return str(self._settings.value(f"tools/{name}", "") or "")

    def set_tool_path(self, name: str, value: str) -> None:
        self._settings.setValue(f"tools/{name}", str(value or "").strip())

    @property
    def tool_paths(self) -> dict[str, str]:
        return {name: self.tool_path(name) for name in _tools.TOOLS}


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
        self._spin_dpi.setRange(72, 1200)
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
            "snippet needs nothing; PDF/EPS/SVG/PNG require pdflatex (and pdftocairo "
            "for EPS/SVG). PNG resolution applies to both Copy PNG and PNG export "
            "(300 dpi is publication grade)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        group_layout.addWidget(hint)

        layout.addWidget(group)

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
        pins_hint.setStyleSheet("color: #666; font-size: 11px;")
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
        hops_hint.setStyleSheet("color: #666; font-size: 11px;")
        display_layout.addWidget(hops_hint)

        layout.addWidget(display_group)

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
        ziamath_hint.setStyleSheet("color: #666; font-size: 11px;")
        render_layout.addWidget(ziamath_hint)

        layout.addWidget(render_group)

        tools_group = QGroupBox("Tools")
        tools_grid = QGridLayout(tools_group)
        tools_grid.setHorizontalSpacing(8)
        tools_grid.setVerticalSpacing(4)
        tools_grid.setColumnStretch(1, 1)  # the path field column takes the slack
        tools_intro = QLabel(
            "Explicit paths to the external tools. Leave blank to auto-detect on "
            "your <tt>PATH</tt>. Set a path only if a tool isn't found or you want "
            "a specific install."
        )
        tools_intro.setWordWrap(True)
        tools_intro.setStyleSheet("color: #666; font-size: 11px;")
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
            status.setStyleSheet("color: #666; font-size: 11px;")
            tools_grid.addWidget(QLabel(name), r, 0, Qt.AlignRight)
            tools_grid.addWidget(edit, r, 1)
            tools_grid.addWidget(browse, r, 2)
            tools_grid.addWidget(status, r + 1, 1, 1, 2)
            self._tool_edits[name] = edit
            self._tool_status[name] = status
            edit.textChanged.connect(lambda _t="", n=name: self._update_tool_status(n))
            self._update_tool_status(name)

        layout.addWidget(tools_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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
        self._prefs.auto_export_tex = self._chk_tex.isChecked()
        self._prefs.auto_export_pdf = self._chk_pdf.isChecked()
        self._prefs.auto_export_eps = self._chk_eps.isChecked()
        self._prefs.auto_export_svg = self._chk_svg.isChecked()
        self._prefs.auto_export_png = self._chk_png.isChecked()
        self._prefs.png_dpi = self._spin_dpi.value()
        self._prefs.mark_unconnected_pins = self._chk_open_pins.isChecked()
        self._prefs.line_hops = self._chk_line_hops.isChecked()
        self._prefs.force_ziamath = self._chk_force_ziamath.isChecked()
        for name, edit in self._tool_edits.items():
            self._prefs.set_tool_path(name, edit.text())
        self.accept()
