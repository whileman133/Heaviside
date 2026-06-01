"""
Component palette panel (spec §10.2).

A fixed-width left panel listing all component types grouped by category.
Each entry shows a 32×32 thumbnail (rendered from the component's ComponentItem)
and the display_name.  Clicking an entry calls scene.start_placement(kind).

A search field at the top filters entries by display_name.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLineEdit,
    QScrollArea,
    QLabel,
    QSizePolicy,
    QFrame,
)

from app.canvas.scene import SchematicScene
from app.canvas.items import ITEM_CLASSES, ComponentItem
from app.canvas.style import GRID_PX
from app.components.registry import REGISTRY
from app.schematic.model import Component

_THUMB_SIZE = 32
_PALETTE_WIDTH = 190


def _render_thumbnail(kind: str) -> QPixmap:
    """Render a 32×32 thumbnail for *kind* using its ComponentItem.paint()."""
    defn = REGISTRY[kind]
    # Place at origin so item coords are centred on bounding box.
    comp = Component(
        id="__thumb__",
        kind=kind,
        position=(0.0, 0.0),
        rotation=0,
        mirror=False,
        options="",
    )
    cls = ITEM_CLASSES.get(kind, ComponentItem)
    item = cls(comp)

    # Compute the item's bounding rect in scene (pixel) coords.
    brect = item.boundingRect()

    # Create a pixmap scaled to fit _THUMB_SIZE × _THUMB_SIZE.
    w = max(brect.width(), 1.0)
    h = max(brect.height(), 1.0)
    scale = _THUMB_SIZE / max(w, h) * 0.85  # leave a small border

    pix = QPixmap(_THUMB_SIZE, _THUMB_SIZE)
    pix.fill(QColor(0, 0, 0, 0))  # transparent background

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    # Translate so the item's bounding rect centre lands at the pixmap centre.
    painter.translate(
        _THUMB_SIZE / 2 - (brect.left() + w / 2) * scale,
        _THUMB_SIZE / 2 - (brect.top() + h / 2) * scale,
    )
    painter.scale(scale, scale)
    # Paint the item directly — no scene needed.
    from PySide6.QtWidgets import QStyleOptionGraphicsItem
    opt = QStyleOptionGraphicsItem()
    item.paint(painter, opt, None)
    painter.end()

    return pix


class _PaletteEntry(QFrame):
    """A single clickable component entry (thumbnail + label)."""

    def __init__(self, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._scene: SchematicScene | None = None

        defn = REGISTRY[kind]
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{defn.display_name} ({kind})")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(40)
        self.setStyleSheet(
            "QFrame { border-radius: 4px; padding: 2px; }"
            "QFrame:hover { background: #e8f0fe; }"
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(0)

        row_widget = QWidget()
        from PySide6.QtWidgets import QHBoxLayout
        row = QHBoxLayout(row_widget)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        thumb_label = QLabel()
        thumb_label.setFixedSize(_THUMB_SIZE, _THUMB_SIZE)
        try:
            pix = _render_thumbnail(kind)
            thumb_label.setPixmap(pix)
        except Exception:
            thumb_label.setText("□")

        name_label = QLabel(defn.display_name)
        name_label.setWordWrap(False)

        row.addWidget(thumb_label)
        row.addWidget(name_label, 1)

        layout.addWidget(row_widget)
        self.setLayout(layout)

        # Store reference so filtering can show/hide.
        self._display_name = defn.display_name.lower()

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.button() == Qt.LeftButton and self._scene is not None:
            self._scene.start_placement(self._kind)
        super().mousePressEvent(event)

    def matches(self, query: str) -> bool:
        return query in self._display_name


class ComponentPalette(QWidget):
    """Left-panel palette of all v1 component types (spec §10.2)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_PALETTE_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._scene: SchematicScene | None = None
        self._entries: list[_PaletteEntry] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        # Search field.
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        outer.addWidget(self._search)

        # Scrollable content area.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll, 1)

        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(2)
        scroll.setWidget(content)

        self._build_entries()

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene
        for entry in self._entries:
            entry.set_scene(scene)

    def _build_entries(self) -> None:
        """Populate the palette grouped by category in a fixed display order."""
        # Fixed category order (spec §5.4).
        category_order = ["Passives", "Amplifiers", "Sources", "MOSFETs", "BJTs", "Nodes", "Annotations", "Drawing"]

        # Group kinds by category preserving insertion order.
        from collections import defaultdict
        by_cat: dict[str, list[str]] = defaultdict(list)
        for kind, defn in REGISTRY.items():
            by_cat[defn.category].append(kind)

        for cat in category_order:
            if cat not in by_cat:
                continue
            # Category header.
            header = QLabel(cat)
            header.setStyleSheet(
                "QLabel { font-weight: bold; color: #555; "
                "padding: 4px 2px 2px 2px; font-size: 11px; }"
            )
            self._content_layout.addWidget(header)

            for kind in by_cat[cat]:
                entry = _PaletteEntry(kind)
                self._entries.append(entry)
                self._content_layout.addWidget(entry)

        self._content_layout.addStretch(1)

    def _on_search(self, text: str) -> None:
        q = text.strip().lower()
        for entry in self._entries:
            entry.setVisible(entry.matches(q) if q else True)
