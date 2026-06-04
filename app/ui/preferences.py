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
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

# QSettings keys.
_KEY_AUTO_PDF = "export/auto_pdf_on_save"
_KEY_AUTO_EPS = "export/auto_eps_on_save"
_KEY_MARK_OPEN_PINS = "display/mark_unconnected_pins"
_KEY_LINE_HOPS = "display/line_hops"


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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        group = QGroupBox("Auto-export on save")
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(6)

        self._chk_pdf = QCheckBox("Export a PDF next to the schematic file")
        self._chk_pdf.setChecked(prefs.auto_export_pdf)
        group_layout.addWidget(self._chk_pdf)

        self._chk_eps = QCheckBox("Export an EPS next to the schematic file")
        self._chk_eps.setChecked(prefs.auto_export_eps)
        group_layout.addWidget(self._chk_eps)

        hint = QLabel(
            "When saving <name>.hv, also write <name>.pdf / <name>.eps to the "
            "same folder so an \\includegraphics in your LaTeX document stays up "
            "to date.  Requires pdflatex (and pdftocairo for EPS)."
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

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        """Persist the checkbox state to the Preferences store and close."""
        self._prefs.auto_export_pdf = self._chk_pdf.isChecked()
        self._prefs.auto_export_eps = self._chk_eps.isChecked()
        self._prefs.mark_unconnected_pins = self._chk_open_pins.isChecked()
        self._prefs.line_hops = self._chk_line_hops.isChecked()
        self.accept()
