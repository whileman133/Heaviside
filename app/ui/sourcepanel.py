"""
Source Panel (spec §10.4).

A bottom panel showing the current CircuiTikZ source in a read-only
``QPlainTextEdit``.  Updates live (300 ms debounce) after any schematic
change.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPlainTextEdit,
    QSizePolicy,
)

from app.canvas.scene import SchematicScene
from app.codegen.circuitikz import generate
from app.ui import theme

_DEBOUNCE_MS = 300


class SourcePanel(QWidget):
    """Bottom panel with live CircuiTikZ source (spec §10.4)."""

    def __init__(self, parent: QWidget | None = None, preferences=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._scene: SchematicScene | None = None
        self._prefs = preferences
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._refresh)

        self.setObjectName("srcPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header strip: title with padding + a hairline bottom divider (matches
        # the LaTeX preview panel, §10.5).
        from PySide6.QtWidgets import QLabel
        self._header = QWidget()
        self._header.setObjectName("panelHeader")
        self._header.setFixedHeight(30)   # matches _PreviewPanel._HEADER_H
        header_row = QHBoxLayout(self._header)
        header_row.setContentsMargins(10, 2, 10, 2)
        self._title = QLabel("CircuiTikZ Source")
        header_row.addWidget(self._title)
        header_row.addStretch(1)
        layout.addWidget(self._header)

        # Read-only source text area (the panel frame provides the border).
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        self._text.setFrameShape(QPlainTextEdit.NoFrame)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.TypeWriter)
        mono.setPointSize(10)
        self._text.setFont(mono)
        layout.addWidget(self._text, 1)

        self.apply_theme()

    def apply_theme(self) -> None:
        """Re-apply the theme-token stylesheets so the panel follows light/dark."""
        self.setStyleSheet(theme.panel_frame_qss("srcPanel"))
        self._header.setStyleSheet(theme.panel_header_qss())
        self._title.setStyleSheet(theme.panel_title_qss())
        self._text.setStyleSheet(
            f"QPlainTextEdit {{ background: {theme.SURFACE}; color: {theme.TEXT}; "
            f"border: none; padding: 4px 6px; }}" + theme.scrollbar_qss()
        )

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene
        scene.schematic_changed.connect(self._on_changed)
        self._refresh()

    def _on_changed(self) -> None:
        """Debounce source refresh to avoid regenerating on every mouse-move."""
        self._debounce.start()

    def refresh(self) -> None:
        """Regenerate the source immediately (e.g. after a preference change)."""
        self._refresh()

    def _refresh(self) -> None:
        if self._scene is None:
            return
        mark_pins = bool(self._prefs and self._prefs.mark_unconnected_pins)
        try:
            source = generate(self._scene.schematic, mark_unconnected_pins=mark_pins)
        except Exception as exc:
            source = f"% Error generating source: {exc}"
        self._text.setPlainText(source)

