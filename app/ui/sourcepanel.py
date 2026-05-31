"""
Source Panel (spec §10.4).

A collapsible bottom panel showing the current CircuiTikZ source in a
read-only ``QPlainTextEdit``.  Updates live (300 ms debounce) after any
schematic change.  A **Copy** button copies the full source to clipboard.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QApplication,
)

from app.canvas.scene import SchematicScene
from app.codegen.circuitikz import generate

_DEBOUNCE_MS = 300


class SourcePanel(QWidget):
    """Bottom panel with live CircuiTikZ source (spec §10.4)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(130)

        self._scene: SchematicScene | None = None
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._refresh)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 4)
        layout.setSpacing(2)

        # Header row: label + copy button.
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)

        from PySide6.QtWidgets import QLabel
        title = QLabel("CircuiTikZ Source")
        title.setStyleSheet("font-weight: bold; font-size: 11px; color: #555;")
        header_row.addWidget(title)
        header_row.addStretch(1)

        copy_btn = QPushButton("Copy")
        copy_btn.setFixedWidth(60)
        copy_btn.setFixedHeight(22)
        copy_btn.clicked.connect(self._copy_source)
        header_row.addWidget(copy_btn)
        layout.addLayout(header_row)

        # Read-only source text area.
        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        self._text.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFont("Monospace")
        mono.setStyleHint(QFont.TypeWriter)
        mono.setPointSize(10)
        self._text.setFont(mono)
        self._text.setStyleSheet("background: #f8f8f8; border: 1px solid #ddd;")
        layout.addWidget(self._text, 1)

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene
        scene.schematic_changed.connect(self._on_changed)
        self._refresh()

    def _on_changed(self) -> None:
        """Debounce source refresh to avoid regenerating on every mouse-move."""
        self._debounce.start()

    def _refresh(self) -> None:
        if self._scene is None:
            return
        try:
            source = generate(self._scene.schematic)
        except Exception as exc:
            source = f"% Error generating source: {exc}"
        self._text.setPlainText(source)

    def _copy_source(self) -> None:
        QApplication.clipboard().setText(self._text.toPlainText())
