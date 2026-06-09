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

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import (
    QColor, QFont, QIcon, QKeySequence, QPainter, QPixmap, QShortcut,
)
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

from app.canvas.items import ITEM_CLASSES, ComponentItem
from app.canvas.scene import SchematicScene
from app.components.registry import REGISTRY
from app.ui import theme

# Preview glyph + tile sizes — enlarged (and 3 columns instead of 4) so the
# symbols read clearly. The tile leaves room around the glyph for the hover
# outline and the keyboard-number badge.
_THUMB_SIZE = 48
_TILE_SIZE = 64
_PALETTE_WIDTH = 272
_ITEM_COLS = 3

# Preferred category order (spec §5.4) — engineer-facing groups, not the
# CircuiTikZ bipole/tripole classification. The palette splits the raw "Logic"
# and "Sources" registry categories into finer groups (see _palette_category).
_CATEGORY_ORDER = [
    "Resistors", "Capacitors", "Inductors", "Diodes", "Transistors",
    "Amplifiers", "Logic (Am)", "Logic (Eu)", "Switches",
    "Sources", "Supplies", "Instruments", "Grounds",
    "Misc", "Annotations", "Drawing",
]

# Power-supply kinds (rails + batteries) split out of the raw "Sources" registry
# category into the palette-only "Supplies" group; the actual sources stay put.
_SUPPLY_KINDS = frozenset({"vcc", "vdd", "vee", "vss", "battery", "battery1"})


def _palette_category(kind: str, raw: str) -> str:
    """Refine a component's registry category into its palette group: split Logic
    by american/european style and move the supply rails/batteries into Supplies.
    (Category is a palette-grouping concern, §5.4, so this stays UI-side.)"""
    if raw == "Logic":
        return "Logic (Eu)" if _is_european_style(kind) else "Logic (Am)"
    if raw == "Sources" and kind in _SUPPLY_KINDS:
        return "Supplies"
    return raw


# A representative component **kind** per category. Its actual symbol is rendered
# as the category-card icon (see _category_pixmap), so the icons always match the
# components — no decorative stand-ins that don't fit.
_CATEGORY_REP = {
    "Resistors": "R",
    "Capacitors": "C",
    "Inductors": "L",
    "Diodes": "D",
    "Transistors": "npn",
    "Amplifiers": "op amp",
    "Logic (Am)": "and",
    "Logic (Eu)": "eand",
    "Switches": "nos",
    "Sources": "V",
    "Supplies": "battery",
    "Instruments": "voltmeter",
    "Grounds": "ground",
    "Misc": "lamp",
    "Annotations": "open",
    "Drawing": "rect",
}

# Keyboard shortcut letter per category (unique; the canvas keeps R/S/W/P while it
# is focused — see MainWindow._handle_palette_shortcut). Shown as a subtle badge
# on each card; pressing 1–9/0 then places the Nth component.
_CATEGORY_LETTERS = {
    "Resistors": "R", "Capacitors": "C", "Inductors": "L", "Diodes": "D",
    "Transistors": "T", "Amplifiers": "A",
    "Logic (Am)": "O", "Logic (Eu)": "E",
    "Switches": "W", "Sources": "V", "Supplies": "U",
    "Instruments": "M", "Grounds": "G", "Misc": "X",
    "Annotations": "N", "Drawing": "B",
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


def _category_pixmap(category: str, size: int) -> QPixmap:
    """Render the category's representative component symbol as a *size*×*size*
    icon (Retina-crisp). Falls back to an empty pixmap if the kind is unknown."""
    kind = _CATEGORY_REP.get(category)
    if not (kind and kind in REGISTRY):
        return QPixmap()
    try:
        pm = _render_thumbnail(kind, size * 2)  # 2× for a sharp Retina icon
        pm.setDevicePixelRatio(2.0)
        return pm
    except Exception:  # noqa: BLE001 - never let a bad symbol break the panel
        return QPixmap()


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
            "QToolButton { border: none; font-weight: bold; color: %s; "
            "font-size: 11px; padding: 4px 2px; text-align: left; }" % theme.ICON
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

    def apply_theme(self) -> None:
        self._toggle.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; color: %s; "
            "font-size: 11px; padding: 4px 2px; text-align: left; }" % theme.ICON
        )


class _ComponentTile(QToolButton):
    """Icon-only, clickable component tile; the name is a hover tooltip.

    When *number* is set (1–9, or 0 for the tenth), a small keyboard-hint badge is
    painted in the corner — the digit that places this component (§10.2)."""

    def __init__(self, kind: str, place, number: int | None = None,  # noqa: ANN001
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        defn = REGISTRY[kind]
        self._number = number
        self.setIcon(QIcon(_thumbnail(kind)))
        self.setIconSize(QSize(_THUMB_SIZE, _THUMB_SIZE))
        self.setFixedSize(_TILE_SIZE, _TILE_SIZE)
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        tip = f"{defn.display_name} ({kind})"
        if number is not None:
            tip += f"  ·  press {number}"
        self.setToolTip(tip)
        self.setStyleSheet(
            "QToolButton { border: 1px solid transparent; border-radius: 6px; }"
            "QToolButton:hover { background: %s; border-color: %s; }"
            % (theme.HOVER, theme.HOVER_BORDER)
        )
        self.clicked.connect(lambda: place(kind))

    def paintEvent(self, event) -> None:  # noqa: N802, ANN001
        super().paintEvent(event)
        if self._number is None:
            return
        # A subtle digit in the top-right corner (no box), the keyboard hint.
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        f = QFont(); f.setPointSize(8); f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.ICON_MUTED))
        p.drawText(0, 2, self.width() - 4, 12,
                   Qt.AlignRight | Qt.AlignTop, str(self._number))
        p.end()


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
        row.setContentsMargins(5, 4, 5, 4)
        row.setSpacing(5)
        icon = QLabel()
        icon.setFixedSize(20, 20)
        icon.setAlignment(Qt.AlignCenter)
        icon.setPixmap(_category_pixmap(category, 20))
        row.addWidget(icon)
        text = QLabel(f"{category}")
        text.setToolTip(f"{count} component{'s' if count != 1 else ''}")
        text.setStyleSheet("border: none; font-size: 11px;")
        row.addWidget(text, 1)
        letter = _CATEGORY_LETTERS.get(category)
        if letter:
            key = QLabel(letter)
            key.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            key.setFixedWidth(14)
            key.setToolTip(f"Shortcut: {letter}")
            key.setStyleSheet(
                "border: none; background: transparent; color: %s; "
                "font-size: 11px; font-weight: bold;" % theme.ICON_MUTED
            )
            row.addWidget(key)

    def _set_active(self, active: bool) -> None:
        border, bg = (theme.ACCENT, theme.HOVER) if active else (theme.BORDER_SOFT, theme.SURFACE_ALT)
        self.setStyleSheet(
            "QFrame#catcard { border: 1px solid %s; border-radius: 5px; "
            "background: %s; }"
            "QFrame#catcard:hover { background: %s; }"
            % (border, bg, theme.HOVER)
        )

    def set_active(self, active: bool) -> None:
        self._set_active(active)

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.button() == Qt.LeftButton:
            self._select(self._category)
        super().mousePressEvent(event)


def _grid(kinds: list[str], place, cols: int = _ITEM_COLS,  # noqa: ANN001
          numbered: bool = False) -> QWidget:
    """A grid of component tiles, *cols* per row, left-aligned.

    When *numbered* is True, the first ten tiles get a 1–9/0 keyboard-hint badge.
    """
    host = QWidget()
    grid = QGridLayout(host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(2)
    for i, kind in enumerate(kinds):
        num = (i + 1) % 10 if (numbered and i < 10) else None  # 1..9 then 0
        grid.addWidget(_ComponentTile(kind, place, number=num), i // cols, i % cols)
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
        self.setStyleSheet(
            "ComponentPalette { background-color: %s; }" % theme.SURFACE
        )

        self._scene: SchematicScene | None = None
        self._by_cat: dict[str, list[str]] = defaultdict(list)
        for kind, defn in REGISTRY.items():
            self._by_cat[_palette_category(kind, defn.category)].append(kind)
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
        # Click-focus only, so the search box doesn't grab focus at startup and
        # swallow the palette letter/number hotkeys; Ctrl+/ still focuses it
        # (programmatic setFocus works regardless of the click-focus policy).
        self._search.setFocusPolicy(Qt.ClickFocus)
        self._search.textChanged.connect(self._on_search)
        outer.addWidget(self._search)
        focus = QShortcut(QKeySequence("Ctrl+/"), self)
        focus.setContext(Qt.WindowShortcut)
        focus.activated.connect(self._search.setFocus)

        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.viewport().setStyleSheet("background-color: %s;" % theme.SURFACE)
        scroll.setStyleSheet(theme.scrollbar_qss())   # clean themed scrollbar
        outer.addWidget(scroll, 1)

        content = QWidget()
        self._content_host = content
        content.setObjectName("palette_content")
        content.setStyleSheet(
            "QWidget#palette_content { background-color: %s; }" % theme.SURFACE
        )
        self._content = QVBoxLayout(content)
        # A little right padding so the cards/tiles clear the scrollbar.
        self._content.setContentsMargins(0, 0, 2, 0)
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
        body.addWidget(
            _grid(self._by_cat.get(self._active_cat, []), self._place, numbered=True)
        )

    # -- keyboard shortcuts (driven by MainWindow's key handling) --------

    def select_category_by_letter(self, letter: str) -> bool:
        """Make the category bound to *letter* active. Returns True if handled."""
        for cat, ltr in _CATEGORY_LETTERS.items():
            if ltr == letter.upper() and cat in self._by_cat:
                if self._search.text().strip():
                    self._search.clear()  # leave search mode so the category shows
                self._select_category(cat)
                # _select_category no-ops if already active; ensure the section shows.
                self._active.setVisible(True)
                self._categories.setVisible(True)
                return True
        return False

    def place_active_index(self, index: int) -> bool:
        """Place the *index*-th (0-based) component of the active category."""
        kinds = self._by_cat.get(self._active_cat, [])
        if 0 <= index < len(kinds):
            self._place(kinds[index])
            return True
        return False

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

    # -- theme -----------------------------------------------------------

    def apply_theme(self) -> None:
        """Re-theme for a light/dark swap: invalidate the (ink-coloured) thumbnail
        cache, re-style the containers and section headers, then rebuild the tile
        grids so every symbol re-renders in the new ink colour."""
        _thumb_cache.clear()
        self.setStyleSheet(
            "ComponentPalette { background-color: %s; }" % theme.SURFACE
        )
        self._scroll.viewport().setStyleSheet("background-color: %s;" % theme.SURFACE)
        self._scroll.setStyleSheet(theme.scrollbar_qss())
        self._content_host.setStyleSheet(
            "QWidget#palette_content { background-color: %s; }" % theme.SURFACE
        )
        for sec in (self._in_use, self._categories, self._active, self._results):
            sec.apply_theme()
        self._build_categories()
        self._rebuild_active()
        self._on_search(self._search.text())  # refreshes in-use + results grids
