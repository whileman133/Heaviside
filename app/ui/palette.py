"""
Component palette panel (spec §10.2).

A left panel for picking components, organised as three collapsible sections:

  * **In use in document** — quick access to the distinct kinds already placed
    (updates as the schematic changes); hidden when the document is empty.
  * **Categories** — a 2-column grid of category cards; clicking one makes it the
    *active* category.
  * **<active category>** — the components in the active category.

Components are shown as icon-only tiles (a thumbnail rendered from the component's
own ``ComponentItem``); the display name + kind appear as a hover tooltip rather
than inline, to keep the panel compact.  A search box at the top (focus with
``Ctrl+/``) switches to a flat grid of matching components across all categories.

Clicking a tile calls ``scene.start_placement(kind)``.
"""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

import qtawesome as qta

from app.canvas.items import ITEM_CLASSES, ComponentItem
from app.canvas.scene import SchematicScene
from app.components.registry import REGISTRY

_THUMB_SIZE = 34
_TILE_SIZE = 46
_PALETTE_WIDTH = 256
_ITEM_COLS = 4

# Preferred category order (spec §5.4) — engineer-facing groups, not the
# CircuiTikZ bipole/tripole classification.
_CATEGORY_ORDER = [
    "Resistors", "Capacitors", "Inductors", "Diodes", "Transistors",
    "Amplifiers", "Logic", "Switches", "Sources", "Instruments", "Grounds",
    "Misc", "Annotations", "Drawing",
]

# A representative qtawesome (Font Awesome 5 solid) icon per category. Purely
# decorative aids on the category cards; resolved safely (see _category_icon).
_CATEGORY_ICONS = {
    "Resistors": "fa5s.wave-square",
    "Capacitors": "fa5s.grip-lines-vertical",
    "Inductors": "fa5s.water",
    "Diodes": "fa5s.play",
    "Transistors": "fa5s.microchip",
    "Amplifiers": "fa5s.bullhorn",
    "Logic": "fa5s.sitemap",
    "Sources": "fa5s.bolt",
    "Instruments": "fa5s.tachometer-alt",
    "Grounds": "fa5s.arrow-down",
    "Supplies": "fa5s.battery-full",
    "Misc": "fa5s.shapes",
    "Annotations": "fa5s.font",
    "Drawing": "fa5s.draw-polygon",
}


# ---------------------------------------------------------------------------
# Thumbnail rendering (cached)
# ---------------------------------------------------------------------------

_thumb_cache: dict[str, QPixmap] = {}


def _render_thumbnail(kind: str, size: int = _THUMB_SIZE) -> QPixmap:
    """Render a *size*×*size* thumbnail for *kind* from its ``ComponentItem``."""
    defn = REGISTRY[kind]
    comp = defn.component_class(
        id="__thumb__", kind=kind, position=(0.0, 0.0),
        rotation=0, mirror=False, options="",
    )
    item = ITEM_CLASSES.get(kind, ComponentItem)(comp)
    brect = item.boundingRect()
    w = max(brect.width(), 1.0)
    h = max(brect.height(), 1.0)
    scale = size / max(w, h) * 0.85  # leave a small border

    pix = QPixmap(size, size)
    pix.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.translate(
        size / 2 - (brect.left() + w / 2) * scale,
        size / 2 - (brect.top() + h / 2) * scale,
    )
    painter.scale(scale, scale)
    from PySide6.QtWidgets import QStyleOptionGraphicsItem
    item.paint(painter, QStyleOptionGraphicsItem(), None)
    painter.end()
    return pix


def _thumbnail(kind: str) -> QPixmap:
    if kind not in _thumb_cache:
        try:
            _thumb_cache[kind] = _render_thumbnail(kind)
        except Exception:  # noqa: BLE001 - never let a bad symbol break the panel
            _thumb_cache[kind] = QPixmap(_THUMB_SIZE, _THUMB_SIZE)
            _thumb_cache[kind].fill(QColor(0, 0, 0, 0))
    return _thumb_cache[kind]


def _category_icon(category: str) -> QIcon:
    name = _CATEGORY_ICONS.get(category, "fa5s.cube")
    try:
        return qta.icon(name, color="#444")
    except Exception:  # noqa: BLE001 - unknown icon name → no icon
        return QIcon()


def _is_european_style(kind: str) -> bool:
    """True for a european-style component, so the palette can group american
    symbols together (first) and european ones after. Derived from the CircuiTikZ
    keyword (every european kind uses an ``european …`` shape keyword)."""
    return "european" in REGISTRY[kind].tikz_keyword.lower()


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class _CollapsibleSection(QWidget):
    """A titled section whose body can be collapsed by clicking the header."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._toggle.setArrowType(Qt.DownArrow)
        self._toggle.setText(title)
        self._toggle.setAutoRaise(True)
        self._toggle.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._toggle.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; color: #555; "
            "font-size: 11px; padding: 4px 2px; text-align: left; }"
        )
        self._toggle.toggled.connect(self._on_toggled)
        self._layout.addWidget(self._toggle)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(2, 0, 2, 4)
        self._body_layout.setSpacing(2)
        self._layout.addWidget(self._body)

    def set_title(self, title: str) -> None:
        self._toggle.setText(title)

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def _on_toggled(self, expanded: bool) -> None:
        self._toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._body.setVisible(expanded)


class _ComponentTile(QToolButton):
    """Icon-only, clickable component tile; the name is a hover tooltip."""

    def __init__(self, kind: str, place, parent: QWidget | None = None) -> None:  # noqa: ANN001
        super().__init__(parent)
        defn = REGISTRY[kind]
        self.setIcon(QIcon(_thumbnail(kind)))
        from PySide6.QtCore import QSize
        self.setIconSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
        self.setFixedSize(_TILE_SIZE, _TILE_SIZE)
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{defn.display_name} ({kind})")
        self.setStyleSheet(
            "QToolButton { border: 1px solid transparent; border-radius: 5px; }"
            "QToolButton:hover { background: #e8f0fe; border-color: #c5d9fb; }"
        )
        self.clicked.connect(lambda: place(kind))


class _CategoryCard(QFrame):
    """A clickable category card: icon + name + component count."""

    def __init__(self, category: str, count: int, select, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._category = category
        self._select = select
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setObjectName("catcard")
        self._set_active(False)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 5, 6, 5)
        row.setSpacing(6)
        icon = QLabel()
        pm = _category_icon(category).pixmap(16, 16)
        icon.setPixmap(pm)
        row.addWidget(icon)
        text = QLabel(f"{category}")
        text.setStyleSheet("border: none; font-size: 11px;")
        row.addWidget(text, 1)
        cnt = QLabel(str(count))
        cnt.setStyleSheet("border: none; color: #999; font-size: 10px;")
        row.addWidget(cnt)

    def _set_active(self, active: bool) -> None:
        self.setStyleSheet(
            "QFrame#catcard { border: 1px solid %s; border-radius: 5px; "
            "background: %s; }"
            "QFrame#catcard:hover { background: #e8f0fe; }"
            % (("#5b87f0", "#e8f0fe") if active else ("#dadada", "#fafafa"))
        )

    def set_active(self, active: bool) -> None:
        self._set_active(active)

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.button() == Qt.LeftButton:
            self._select(self._category)
        super().mousePressEvent(event)


def _grid(kinds: list[str], place, cols: int = _ITEM_COLS) -> QWidget:  # noqa: ANN001
    """A grid of component tiles, *cols* per row, left-aligned."""
    host = QWidget()
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(2)
    for i, kind in enumerate(kinds):
        grid.addWidget(_ComponentTile(kind, place), i // cols, i % cols)
    # Push tiles to the top-left so partial last rows don't stretch.
    grid.setColumnStretch(cols, 1)
    return host


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------

class ComponentPalette(QWidget):
    """Left-panel palette of all component types (spec §10.2)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_PALETTE_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self.setStyleSheet("ComponentPalette { background-color: #ffffff; }")

        self._scene: SchematicScene | None = None
        self._by_cat: dict[str, list[str]] = defaultdict(list)
        for kind, defn in REGISTRY.items():
            self._by_cat[defn.category].append(kind)
        # Within each category, keep american-style components together (first),
        # then european-style ones, instead of jumbling them by registry order.
        for kinds in self._by_cat.values():
            kinds.sort(key=lambda k: (_is_european_style(k),))  # stable: keeps order within each group
        self._ordered_cats = [c for c in _CATEGORY_ORDER if c in self._by_cat] + [
            c for c in self._by_cat if c not in _CATEGORY_ORDER
        ]
        self._active_cat = self._ordered_cats[0] if self._ordered_cats else ""
        self._cards: dict[str, _CategoryCard] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(4)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search components…  (Ctrl+/)")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._on_search)
        outer.addWidget(self._search)
        focus = QShortcut(QKeySequence("Ctrl+/"), self)
        focus.setContext(Qt.WindowShortcut)
        focus.activated.connect(self._search.setFocus)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.viewport().setStyleSheet("background-color: #ffffff;")
        outer.addWidget(scroll, 1)

        content = QWidget()
        content.setObjectName("palette_content")
        content.setStyleSheet("QWidget#palette_content { background-color: #ffffff; }")
        self._content = QVBoxLayout(content)
        self._content.setContentsMargins(0, 0, 0, 0)
        self._content.setSpacing(4)
        scroll.setWidget(content)

        self._in_use = _CollapsibleSection("IN USE IN DOCUMENT")
        self._categories = _CollapsibleSection("CATEGORIES")
        self._active = _CollapsibleSection(self._active_cat.upper())
        self._results = _CollapsibleSection("SEARCH RESULTS")
        for sec in (self._in_use, self._categories, self._active, self._results):
            self._content.addWidget(sec)
        self._content.addStretch(1)

        self._build_categories()
        self._rebuild_active()
        self._results.setVisible(False)
        self._in_use.setVisible(False)

    # -- public API ------------------------------------------------------

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene
        scene.schematic_changed.connect(self._refresh_in_use)
        self._refresh_in_use()

    # -- placement -------------------------------------------------------

    def _place(self, kind: str) -> None:
        if self._scene is not None:
            self._scene.start_placement(kind)

    # -- section builders ------------------------------------------------

    def _clear(self, layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def _build_categories(self) -> None:
        body = self._categories.body_layout()
        self._clear(body)
        self._cards = {}
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        for i, cat in enumerate(self._ordered_cats):
            card = _CategoryCard(cat, len(self._by_cat[cat]), self._select_category)
            card.set_active(cat == self._active_cat)
            self._cards[cat] = card
            grid.addWidget(card, i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        body.addWidget(grid_host)

    def _select_category(self, category: str) -> None:
        if category == self._active_cat:
            return
        for cat, card in self._cards.items():
            card.set_active(cat == category)
        self._active_cat = category
        self._rebuild_active()

    def _rebuild_active(self) -> None:
        self._active.set_title(self._active_cat.upper())
        body = self._active.body_layout()
        self._clear(body)
        body.addWidget(_grid(self._by_cat.get(self._active_cat, []), self._place))

    def _refresh_in_use(self) -> None:
        if self._scene is None:
            return
        used = {c.kind for c in self._scene.schematic.components if c.kind in REGISTRY}
        order = {k: i for i, k in enumerate(
            [k for cat in self._ordered_cats for k in self._by_cat[cat]])}
        kinds = sorted(used, key=lambda k: order.get(k, 1_000_000))
        body = self._in_use.body_layout()
        self._clear(body)
        if kinds:
            body.addWidget(_grid(kinds, self._place))
        # Hidden when empty, and while a search is active (results take over).
        self._in_use.setVisible(bool(kinds) and not self._search.text().strip())

    # -- search ----------------------------------------------------------

    def _on_search(self, text: str) -> None:
        q = text.strip().lower()
        searching = bool(q)
        # Category/active/in-use views give way to a flat results grid on search.
        self._categories.setVisible(not searching)
        self._active.setVisible(not searching)
        self._refresh_in_use()  # also re-evaluates its own visibility
        self._results.setVisible(searching)
        if not searching:
            return
        matches = [
            kind for kind in (k for cat in self._ordered_cats for k in self._by_cat[cat])
            if q in kind.lower() or q in REGISTRY[kind].display_name.lower()
        ]
        self._results.set_title(f"SEARCH RESULTS ({len(matches)})")
        body = self._results.body_layout()
        self._clear(body)
        body.addWidget(_grid(matches, self._place))
