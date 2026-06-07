"""
Document Settings dialog (spec §10).

Edits per-document CircuiTikZ conventions stored on the :class:`Schematic` — the
voltage and current **label styles** (american / european).  Unlike Preferences
(which are app-wide, via QSettings), these live in the ``.hv`` file and travel
with the document.

The dialog mutates the passed-in ``Schematic`` only on accept; the caller is
responsible for marking the document modified and recompiling (the main window
emits ``schematic_changed``, which refreshes the source panel and preview).
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from app.schematic.model import LABEL_STYLES, Schematic

_STYLE_LABELS = {"american": "American", "european": "European"}


class DocumentSettingsDialog(QDialog):
    """Modal editor for the document's CircuiTikZ label conventions.

    Reads the current styles from *schematic* on open and writes them back only
    when the user accepts (OK); Cancel leaves the document untouched.  Returns
    ``True`` from :meth:`changed` when accept actually altered a value, so the
    caller can skip a needless recompile.
    """

    def __init__(self, schematic: Schematic, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._schematic = schematic
        self._changed = False
        self.setWindowTitle("Document Settings")
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)
        self._voltage = self._style_combo(schematic.voltage_style)
        self._current = self._style_combo(schematic.current_style)
        form.addRow("Voltage labels", self._voltage)
        form.addRow("Current labels", self._current)
        layout.addLayout(form)

        hint = QLabel(
            "Sets the CircuiTikZ arrow convention for voltage (<tt>v=</tt>) and "
            "current (<tt>i=</tt>) labels for this document — emitted as a "
            "picture-scoped <tt>\\ctikzset{voltage=…, current=…}</tt>, so it also "
            "applies to the exported figure. Stored in the .hv file."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 11px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _style_combo(self, current: str) -> QComboBox:
        combo = QComboBox()
        for style in LABEL_STYLES:
            combo.addItem(_STYLE_LABELS.get(style, style), style)
        idx = combo.findData(current if current in LABEL_STYLES else "american")
        combo.setCurrentIndex(max(0, idx))
        return combo

    def changed(self) -> bool:
        """True when accepting the dialog actually changed a style value."""
        return self._changed

    def _on_accept(self) -> None:
        voltage = self._voltage.currentData()
        current = self._current.currentData()
        self._changed = (
            voltage != self._schematic.voltage_style
            or current != self._schematic.current_style
        )
        self._schematic.voltage_style = voltage
        self._schematic.current_style = current
        self.accept()
