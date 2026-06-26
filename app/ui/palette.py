"""
Component palette panel (spec §10.2).

A left panel for picking components, organised as collapsible sections:

  * **In use in document** — quick access to the distinct kinds already placed
    (updates as the schematic changes); hidden when the document is empty.
  * **CircuiTikZ** — a 3-column grid of cards for the CircuiTikZ component
    categories; clicking one makes it the *active* category.
  * **TikZ** — the same, for our own (vanilla-TikZ) drawing primitives (rectangle,
    circle, text — see ``_VANILLA_CATEGORIES``).
  * **<active category>** — the components in the active category (from either
    section above).

Components are shown as icon-only tiles (a thumbnail rendered from the component's
own ``ComponentItem``); the display name + kind appear as a hover tooltip rather
than inline, to keep the panel compact.  A search box at the top (focus with
``Ctrl+/``) switches to a flat grid of matching components across all categories.

Clicking a tile calls ``scene.start_placement(kind)``.
"""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPen, QPixmap, QShortcut,
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
from app.components.registry import REGISTRY, display_rank
from app.resources import component_lib
from app.ui import theme

# Preview glyph + tile sizes — enlarged so the symbols read clearly. The tile
# leaves a little room around the glyph for the hover outline.
_THUMB_SIZE = 48
_TILE_SIZE = 64
# Wide enough for a 3-column grid of category cards whose names ("Transducers",
# "Instruments") fit without clipping; the component tiles fill it 4-across.
_PALETTE_WIDTH = 340
_ITEM_COLS = 4
# Height cap (px) for the pinned "in use in document" panel — about three rows of
# tiles; beyond that it scrolls independently rather than crowding the categories.
_IN_USE_MAX_H = 232
# Approximate height (px) of a _CollapsibleSection header (the bold toggle row),
# used to size the pinned panel deterministically from its tile-grid geometry.
_IN_USE_HEADER_H = 26

# Preferred category order (spec §5.4) — engineer-facing groups, not the
# CircuiTikZ bipole/tripole classification. The palette splits the raw "Logic"
# and "Sources" registry categories into finer groups (see _palette_category).
# Listed row-by-row to match the 3-column card grid (_build_categories fills it
# left-to-right), so each triple below is one row of the palette.
_CATEGORY_ORDER = [
    "Resistors", "Inductors", "Capacitors",
    "Diodes", "Transistors", "Switches",
    "Sources", "Supplies", "Grounds",
    "Annotations", "Amplifiers", "Instruments",
    "Gates (Am)", "Gates (Eu)", "Logic",
    "Blocks", "Tubes", "Antennas",
    "Transducers", "Misc", "Drawing",
]

# Palette categories that are NOT CircuiTikZ symbols but our own **vanilla-TikZ**
# drawing primitives (plain ``\draw`` rectangles/circles and ``\node`` text). They
# get their own top-level palette section, separate from the CircuiTikZ component
# categories; everything not listed here is treated as CircuiTikZ. Add any future
# custom drawing category here.
_VANILLA_CATEGORIES: frozenset[str] = frozenset({"Drawing"})

# Raw-"Logic" kinds that are *blocks* rather than gates (flip-flops, mux/demux,
# ALU, adder). These split into their own palette "Logic" category; the boolean
# gates split into the style-grouped "Gates (Am)" / "Gates (Eu)" (see
# _palette_category). Keyed on the CircuiTikZ shape keyword so it tracks the data.
_LOGIC_BLOCK_TIKZ = ("flipflop", "muxdemux", "ALU", "one bit adder")

# Power-supply kinds (rails + batteries) split out of the raw "Sources" registry
# category into the palette-only "Supplies" group; the actual sources stay put.
_SUPPLY_KINDS = frozenset({"vcc", "vdd", "vee", "vss", "battery", "battery1"})


def _is_logic_block(kind: str) -> bool:
    """True for a raw-"Logic" kind that is a *block* (flip-flop, mux/demux, ALU,
    adder) rather than a boolean gate — so the palette can give the blocks their
    own "Logic" category and keep the gates in "Gates (Am)" / "Gates (Eu)"."""
    return REGISTRY[kind].tikz_keyword.startswith(_LOGIC_BLOCK_TIKZ)


def _palette_category(kind: str, raw: str) -> str:
    """Refine a component's registry category into its palette group: split the raw
    Logic category into the boolean **Gates** (by american/european style) and the
    **Logic** blocks (flip-flops, mux/demux, ALU, adder), and move the supply
    rails/batteries into Supplies. (Category is a palette-grouping concern, §5.4,
    so this stays UI-side.)

    These refinements are tuned to the *curated* library's kinds. The manual-generated
    library carries the manual's own categories verbatim (only shortened for display),
    so we use them as-is there and don't introduce extra palette-only groups."""
    if component_lib() != "curated":
        return raw
    if raw == "Logic":
        if _is_logic_block(kind):
            return "Logic"
        return "Gates (Eu)" if _is_european_style(kind) else "Gates (Am)"
    if raw == "Sources" and kind in _SUPPLY_KINDS:
        return "Supplies"
    return raw


# A curated representative component **kind** per category. Its actual symbol is
# rendered as the category-card icon (see _category_pixmap), so the icons match the
# components — no decorative stand-ins that don't fit. This is only a *preference*:
# any category not listed (e.g. one introduced by the manual-generated library) falls
# back to its first member automatically (see _category_rep), so new categories never
# show a blank icon.
_CATEGORY_REP = {
    "Resistors": "R",
    "Capacitors": "C",
    "Inductors": "L",
    "Diodes": "D",
    "Transistors": "npn",
    "Tubes": "triode",
    "Amplifiers": "op amp",
    "Blocks": "amp",
    "Gates (Am)": "and",
    "Gates (Eu)": "eand",
    "Logic": "ALU",
    "Switches": "nos",
    "Sources": "V",
    "Supplies": "battery",
    "Instruments": "voltmeter",
    "Grounds": "ground",
    "Transducers": "loudspeaker",
    "Antennas": "antenna",
    "Misc": "lamp",
    "Annotations": "open",
    "Drawing": "rect",
}


# ---------------------------------------------------------------------------
# Online-manual documentation links (spec §10.2)
# ---------------------------------------------------------------------------

# The CircuiTikZ component reference is the "The components: list" page of the
# multi-page online manual; each category subsection carries a stable semantic
# ``#sec:<label>`` anchor (the LaTeX ``\label``), which we link to directly.
_MANUAL_BASE_URL = "https://rmano.github.io/circuitikz/node-The-components-list.html"

# Palette category -> (full manual subsection title, ``sec:`` fragment anchor).
# Keyed by the *palette* category name; the title is the manual's own (long)
# subsection heading, used as the link tooltip.  Most keys are the short manual
# categories (see components/generate_library.py ``_CATEGORY_MAP``); a few
# curated-library names (Capacitors/Inductors, the gate groups, Supplies,
# Antennas) alias the same section so the curated palette links too.
#
# The ``sec:`` anchors are the manual's own LaTeX labels (stable across builds),
# read from the category-level (``4.x``) subsection headings of the components
# page.  Refresh from ``_MANUAL_BASE_URL`` (grep ``id="sec:`` on the page) if the
# manual ever renames a label.
_CATEGORY_DOC: dict[str, tuple[str, str]] = {
    # short manual categories
    "Grounds":      ("Grounds and supply voltages", "sec:grounds-and-supply"),
    "Resistors":    ("Resistive bipoles", "sec:resistive-bipoles"),
    "Cap/Ind":      ("Capacitors and inductors: dynamical bipoles", "sec:capacitors-and-inductors"),
    "Diodes":       ("Diodes and such", "sec:diodes-and-such"),
    "Sources":      ("Sources and generators", "sec:sources-and-generators"),
    "Instruments":  ("Instruments", "sec:instruments"),
    "Mechanical":   ("Mechanical Analogy", "sec:mechanical-analogy"),
    "Misc":         ("Miscellaneous bipoles and symbols", "sec:miscellaneous-bipoles-and"),
    "Buses":        ("Multiple wires (buses)", "sec:multiple-wires-buses"),
    "Crossings":    ("Crossings", "sec:crossings"),
    "Arrows":       ("Arrows (fake and real)", "sec:arrows"),
    "Terminals":    ("Terminal shapes", "sec:terminals"),
    "Connectors":   ("Connectors", "sec:connectors"),
    "Blocks":       ("Block diagram components", "sec:block-diagram-components"),
    "Transistors":  ("Transistors", "sec:transistors"),
    "Tubes":        ("Electronic Tubes", "sec:electronic-tubes"),
    "RF":           ("RF components", "sec:RF"),
    "Electromech":  ("Electro-Mechanical Devices", "sec:electro-mechanical-devices"),
    "Transformers": ("Double bipoles (transformers)", "sec:transformers"),
    "Amplifiers":   ("Amplifiers", "sec:amplifiers"),
    "Switches":     ("Switches, buttons and jumpers", "sec:switches-buttons-and"),
    "Logic":        ("Logic gates", "sec:logic-gates"),
    "Flip-flops":   ("Flip-flops", "sec:flipflops"),
    "Mux/Demux":    ("Multiplexer and de-multiplexer", "sec:muxdemuxes"),
    "Chips":        ("Chips (integrated circuits)", "sec:chips"),
    "Displays":     ("Seven segment displays", "sec:seven-segment-displays"),
    # curated-library category aliases (same manual sections)
    "Capacitors":   ("Capacitors and inductors: dynamical bipoles", "sec:capacitors-and-inductors"),
    "Inductors":    ("Capacitors and inductors: dynamical bipoles", "sec:capacitors-and-inductors"),
    "Gates (Am)":   ("Logic gates", "sec:logic-gates"),
    "Gates (Eu)":   ("Logic gates", "sec:logic-gates"),
    "Supplies":     ("Grounds and supply voltages", "sec:grounds-and-supply"),
    "Antennas":     ("RF components", "sec:RF"),
}


def _category_doc(category: str) -> tuple[str, str] | None:
    """Return ``(full_title, url)`` for *category*'s manual section, or ``None``
    if the category has no documented section."""
    entry = _CATEGORY_DOC.get(category)
    if entry is None:
        return None
    title, anchor = entry
    return title, f"{_MANUAL_BASE_URL}#{anchor}"


def _doc_link_icon(size: int = 14) -> QPixmap:
    """A small themed 'external link' glyph (a page with an arrow leaving its
    top-right corner) signalling a link out to the online manual."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(theme.ICON))
    pen.setWidthF(max(1.2, size / 11.0))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = float(size)
    # page (lower-left rounded box)
    p.drawRoundedRect(QRectF(s * 0.10, s * 0.34, s * 0.52, s * 0.52),
                      s * 0.10, s * 0.10)
    # arrow leaving the top-right
    p.drawLine(QPointF(s * 0.46, s * 0.54), QPointF(s * 0.86, s * 0.14))
    p.drawLine(QPointF(s * 0.86, s * 0.14), QPointF(s * 0.60, s * 0.14))
    p.drawLine(QPointF(s * 0.86, s * 0.14), QPointF(s * 0.86, s * 0.40))
    p.end()
    return pix


# ---------------------------------------------------------------------------
# Thumbnail rendering (cached)
# ---------------------------------------------------------------------------

_thumb_cache: dict[tuple, QPixmap] = {}

# Active document symbol style for thumbnails (§5.4). The palette updates this so its
# tiles/category icons render in the document's american/european/cute style; cached
# per (kind, value) so styles coexist without re-rendering.
_thumb_style: dict[str, str] = {}


def _thumb_style_value(kind: str) -> str:
    """The thumbnail style value for *kind* (the value on its style axis, or ``""`` if
    the kind doesn't vary with style — so unstyled tiles share one cache entry)."""
    from app.components import library
    axis = library.style_axis(kind)
    return library.style_value(axis, _thumb_style) if axis else ""


def _render_thumbnail(kind: str, size: int = _THUMB_SIZE) -> QPixmap:
    """Render a *size*×*size* thumbnail for *kind* from its ``ComponentItem``, in the
    active document symbol style (``_thumb_style``)."""
    defn = REGISTRY[kind]
    comp = defn.component_class(
        id="__thumb__", kind=kind, position=(0.0, 0.0),
        rotation=0, mirror=False, options="",
    )
    item = ITEM_CLASSES.get(kind, ComponentItem)(comp)
    item._style_override = _thumb_style   # thumbnail has no scene → carry the style
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
    key = (kind, _thumb_style_value(kind))
    if key not in _thumb_cache:
        try:
            _thumb_cache[key] = _render_thumbnail(kind)
        except Exception:  # noqa: BLE001 - never let a bad symbol break the panel
            _thumb_cache[key] = QPixmap(_THUMB_SIZE, _THUMB_SIZE)
            _thumb_cache[key].fill(QColor(0, 0, 0, 0))
    return _thumb_cache[key]


def _category_rep(category: str, kinds: list[str]) -> str | None:
    """The component kind whose symbol illustrates *category*: the curated pick from
    ``_CATEGORY_REP`` when it's set and present in the active registry, otherwise the
    first (most canonical, since *kinds* is pre-sorted) member of the category.

    Auto-deriving the fallback means a category the curated map doesn't know about —
    e.g. one added by the manual-generated library, or after the curated map drifts —
    always gets an icon instead of a blank card."""
    rep = _CATEGORY_REP.get(category)
    if rep and rep in REGISTRY:
        return rep
    return kinds[0] if kinds else None


def _category_pixmap(kind: str | None, size: int) -> QPixmap:
    """Render *kind*'s symbol as a *size*×*size* category icon (Retina-crisp), or an
    empty pixmap if there's no usable kind."""
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


def _within_category_key(kind: str):
    """Sort key for the order of kinds *within* a palette category: kinds in the
    explicit display order come first, in that order; the rest fall after,
    american-style before european (then alphabetically)."""
    rank = display_rank(kind)
    if rank is not None:
        return (0, rank, "")
    return (1, _is_european_style(kind), kind.lower())


# ---------------------------------------------------------------------------
# Widgets
# ---------------------------------------------------------------------------

class _ClickableLabel(QLabel):
    """A QLabel that emits ``clicked`` on a left press (so a wrapping section title
    can collapse its section like the arrow toggle does)."""

    clicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: ANN001
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _CollapsibleSection(QWidget):
    """A titled section whose body can be collapsed by clicking the header. The
    title is a **word-wrapping** label so long manual section names (the active
    category shows the full name) wrap within the palette pane instead of clipping."""

    _TITLE_QSS = ("QLabel { font-weight: bold; color: %s; font-size: 11px; "
                  "padding: 4px 2px; }")

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)

        # Arrow toggle (collapse control) kept beside a separate title label so the
        # title can wrap; the arrow stays a single line, top-aligned with the text.
        self._toggle = QToolButton()
        self._toggle.setCheckable(True)
        self._toggle.setChecked(True)
        self._toggle.setArrowType(Qt.DownArrow)
        self._toggle.setAutoRaise(True)
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.setFocusPolicy(Qt.NoFocus)
        self._toggle.setStyleSheet("QToolButton { border: none; padding: 4px 0; }")
        self._toggle.toggled.connect(self._on_toggled)

        self._title = _ClickableLabel(title)
        self._title.setWordWrap(True)
        self._title.setCursor(Qt.PointingHandCursor)
        self._title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._title.setStyleSheet(self._TITLE_QSS % theme.ICON)
        self._title.clicked.connect(self._toggle.toggle)

        # Header row: arrow + wrapping title + an optional trailing documentation-
        # link button (shown for sections that map to a CircuiTikZ manual section;
        # see ``set_doc_link``). The arrow and link stay top-aligned as the title wraps.
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(0)
        header.addWidget(self._toggle, 0, Qt.AlignTop)
        header.addWidget(self._title, 1)
        self._doc_url: str | None = None
        self._doc_btn = QToolButton()
        self._doc_btn.setAutoRaise(True)
        self._doc_btn.setCursor(Qt.PointingHandCursor)
        self._doc_btn.setFocusPolicy(Qt.NoFocus)
        self._doc_btn.setIconSize(QSize(14, 14))
        self._doc_btn.setIcon(QIcon(_doc_link_icon(14)))
        self._doc_btn.setStyleSheet(
            "QToolButton { border: none; padding: 4px 4px; }"
            "QToolButton:hover { background: %s; border-radius: 4px; }" % theme.HOVER
        )
        self._doc_btn.setVisible(False)
        self._doc_btn.clicked.connect(self._open_doc_link)
        header.addWidget(self._doc_btn, 0, Qt.AlignTop)
        self._layout.addLayout(header)

        self._body = QWidget()
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(2, 0, 2, 4)
        self._body_layout.setSpacing(2)
        self._layout.addWidget(self._body)

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def title(self) -> str:
        return self._title.text()

    def set_doc_link(self, title: str | None, url: str | None) -> None:
        """Show (or hide, when *url* is falsy) the documentation-link button next
        to the title. *title* becomes the button's tooltip (the full manual
        section name)."""
        self._doc_url = url or None
        if self._doc_url is None:
            self._doc_btn.setVisible(False)
            return
        self._doc_btn.setToolTip(f"Open “{title}” in the CircuiTikZ manual")
        self._doc_btn.setVisible(True)

    def _open_doc_link(self) -> None:
        if self._doc_url:
            QDesktopServices.openUrl(QUrl(self._doc_url))

    def body_layout(self) -> QVBoxLayout:
        return self._body_layout

    def _on_toggled(self, expanded: bool) -> None:
        self._toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self._body.setVisible(expanded)

    def apply_theme(self) -> None:
        self._title.setStyleSheet(self._TITLE_QSS % theme.ICON)
        self._doc_btn.setIcon(QIcon(_doc_link_icon(14)))
        self._doc_btn.setStyleSheet(
            "QToolButton { border: none; padding: 4px 4px; }"
            "QToolButton:hover { background: %s; border-radius: 4px; }" % theme.HOVER
        )


class _ComponentTile(QToolButton):
    """Icon-only, clickable component tile; the name is a hover tooltip."""

    def __init__(self, kind: str, place,  # noqa: ANN001
                 parent: QWidget | None = None,
                 tile_size: int = _TILE_SIZE, thumb_size: int = _THUMB_SIZE) -> None:
        super().__init__(parent)
        defn = REGISTRY[kind]
        self.setIcon(QIcon(_thumbnail(kind)))
        self.setIconSize(QSize(thumb_size, thumb_size))
        self.setFixedSize(tile_size, tile_size)
        self.setAutoRaise(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(f"{defn.display_name} ({kind})")
        self.setStyleSheet(
            "QToolButton { border: 1px solid transparent; border-radius: 6px; }"
            "QToolButton:hover { background: %s; border-color: %s; }"
            % (theme.HOVER, theme.HOVER_BORDER)
        )
        self.clicked.connect(lambda: place(kind))


# The canvas-side quick bar holds the **Terminals** category — the junction-dot /
# pole connection markers (circ/ocirc, diamondpole/…, squarepole/…). Sourced by
# category so it tracks the library: empty (and hidden) where there is none.
WIRING_CATEGORY = "Terminals"
# Compact tiles for the quick bar (the markers are tiny — no need for full palette
# tile size): a snug icon with minimal padding.
_WIRING_TILE = 30
_WIRING_THUMB = 26


class WiringQuickBar(QWidget):
    """A slim vertical strip of the **Terminals** connection markers docked at the
    right edge of the canvas, so they're a single click away without a trip to the
    category palette. Sourced from the active library by category."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene: SchematicScene | None = None
        self._kinds = [k for k, d in REGISTRY.items() if d.category == WIRING_CATEGORY]
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(2, 4, 2, 4)
        self._layout.setSpacing(2)
        self.setFixedWidth(_WIRING_TILE + 4)
        self.setVisible(bool(self._kinds))
        self._rebuild()

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene

    def _place(self, kind: str) -> None:
        if self._scene is not None:
            self._scene.start_placement(kind)

    def _rebuild(self) -> None:
        while self._layout.count():
            w = self._layout.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        for kind in self._kinds:
            self._layout.addWidget(
                _ComponentTile(kind, self._place,
                               tile_size=_WIRING_TILE, thumb_size=_WIRING_THUMB))
        self._layout.addStretch(1)
        self.setStyleSheet(
            "WiringQuickBar { background: %s; border-left: 1px solid %s; }"
            % (theme.SURFACE, theme.BORDER_SOFT)
        )

    def apply_theme(self) -> None:
        self._rebuild()


class _CategoryCard(QFrame):
    """A clickable category card: icon + name + component count."""

    def __init__(self, category: str, count: int, select,  # noqa: ANN001
                 rep_kind: str | None = None, parent=None) -> None:
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
        icon.setPixmap(_category_pixmap(rep_kind, 20))
        # Transparent so the card's own fill shows through (a stylesheet'd QLabel
        # would otherwise paint an opaque palette box behind the glyph/text).
        icon.setStyleSheet("background: transparent; border: none;")
        row.addWidget(icon)
        text = QLabel(f"{category}")
        text.setToolTip(f"{count} component{'s' if count != 1 else ''}")
        # Explicit theme ink so the name follows a light/dark swap (the cards are
        # rebuilt by _build_categories on apply_theme, picking up the new token);
        # transparent background so it doesn't draw a box over the card.
        text.setStyleSheet(
            "border: none; background: transparent; font-size: 11px; color: %s;" % theme.TEXT
        )
        row.addWidget(text, 1)

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
        self.setStyleSheet(
            "ComponentPalette { background-color: %s; }" % theme.SURFACE
        )

        self._scene: SchematicScene | None = None
        self._by_cat: dict[str, list[str]] = defaultdict(list)
        for kind, defn in REGISTRY.items():
            self._by_cat[_palette_category(kind, defn.category)].append(kind)
        # Within each category, an explicit display order wins (so the Inductors
        # group reads inductors → transformers → choke); kinds *not* in the order
        # fall after, american-style first then european, instead of jumbled.
        for kinds in self._by_cat.values():
            kinds.sort(key=_within_category_key)
        # Curated: the spec §5.4 engineer-facing order. Manual: the manual's own
        # section sequence — registry/definitions.json order is not the manual order,
        # so we sort by ``_CATEGORY_DOC`` (authored in manual order); bespoke
        # categories with no manual section (Annotations, Drawing) fall after.
        if component_lib() == "curated":
            order = _CATEGORY_ORDER
        else:
            order = list(_CATEGORY_DOC)
        self._ordered_cats = [c for c in order if c in self._by_cat] + [
            c for c in self._by_cat if c not in order
        ]
        # Split into the two top-level palette sections (each preserves the order
        # above): CircuiTikZ symbols vs our vanilla-TikZ drawing primitives.
        self._circuitikz_cats = [c for c in self._ordered_cats
                                 if c not in _VANILLA_CATEGORIES]
        self._vanilla_cats = [c for c in self._ordered_cats
                              if c in _VANILLA_CATEGORIES]
        # Open a CircuiTikZ category first (the common case), not a drawing one.
        first = self._circuitikz_cats or self._ordered_cats
        self._active_cat = first[0] if first else ""
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
        self._search.setStyleSheet(theme.line_edit_qss())
        self._search.textChanged.connect(self._on_search)
        outer.addWidget(self._search)
        focus = QShortcut(QKeySequence("Ctrl+/"), self)
        focus.setContext(Qt.WindowShortcut)
        focus.activated.connect(self._search.setFocus)

        # Top (scrolling) region: categories / active-category / search results.
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

        self._circuitikz = _CollapsibleSection("CIRCUITIKZ")
        self._vanilla = _CollapsibleSection("TIKZ")
        self._active = _CollapsibleSection(self._active_cat.upper())
        self._results = _CollapsibleSection("SEARCH RESULTS")
        for sec in (self._circuitikz, self._vanilla, self._active, self._results):
            self._content.addWidget(sec)
        self._content.addStretch(1)

        # Bottom (pinned) region: "in use in document", scrolling independently of
        # the categories above so the user always has the placed kinds at hand
        # (spec §10.2). A hairline divider separates it from the scroll region; the
        # whole panel hides when there is nothing in use (or while searching).
        self._in_use = _CollapsibleSection("IN USE IN DOCUMENT")
        self._in_use_expanded_h = _IN_USE_HEADER_H
        self._in_use._toggle.toggled.connect(self._on_in_use_toggled)
        self._in_use_panel = QWidget()
        panel_v = QVBoxLayout(self._in_use_panel)
        panel_v.setContentsMargins(0, 0, 0, 0)
        panel_v.setSpacing(0)
        self._in_use_divider = QFrame()
        self._in_use_divider.setFrameShape(QFrame.HLine)
        self._in_use_divider.setFixedHeight(1)
        self._in_use_divider.setStyleSheet("background-color: %s; border: none;" % theme.DIVIDER)
        panel_v.addWidget(self._in_use_divider)

        in_use_scroll = QScrollArea()
        self._in_use_scroll = in_use_scroll
        in_use_scroll.setWidgetResizable(True)
        in_use_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        in_use_scroll.setFrameShape(QFrame.NoFrame)
        in_use_scroll.viewport().setStyleSheet("background-color: %s;" % theme.SURFACE)
        in_use_scroll.setStyleSheet(theme.scrollbar_qss())
        in_use_host = QWidget()
        self._in_use_host = in_use_host
        in_use_host.setObjectName("in_use_content")
        in_use_host.setStyleSheet(
            "QWidget#in_use_content { background-color: %s; }" % theme.SURFACE
        )
        in_use_v = QVBoxLayout(in_use_host)
        in_use_v.setContentsMargins(0, 0, 2, 0)
        in_use_v.setSpacing(0)
        in_use_v.addWidget(self._in_use)
        in_use_scroll.setWidget(in_use_host)
        panel_v.addWidget(in_use_scroll)
        outer.addWidget(self._in_use_panel)   # unstretched → pinned at the bottom

        self._build_categories()
        self._rebuild_active()
        self._results.setVisible(False)
        self._in_use_panel.setVisible(False)

    # -- public API ------------------------------------------------------

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene
        scene.schematic_changed.connect(self._refresh_in_use)
        # Track the document symbol style across load/new/undo (no-op when unchanged).
        scene.schematic_changed.connect(
            lambda: self.set_symbol_style(
                getattr(self._scene.schematic, "symbol_style", {}) or {}))
        self.set_symbol_style(getattr(scene.schematic, "symbol_style", {}) or {})
        self._refresh_in_use()

    def set_symbol_style(self, style: dict) -> None:
        """Render the palette tiles/icons in the document's symbol style (§5.4). Sets
        the thumbnail style and rebuilds the visible grids; a no-op if unchanged."""
        global _thumb_style
        if dict(style) == _thumb_style:
            return
        _thumb_style = dict(style)
        self._build_categories()      # category icons may be styled
        self._rebuild_active()
        self._on_search(self._search.text())   # active/results/in-use grids

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
        self._cards = {}
        self._build_category_grid(self._circuitikz, self._circuitikz_cats)
        self._build_category_grid(self._vanilla, self._vanilla_cats)
        # A section with no categories (e.g. no drawing primitives) stays hidden.
        self._vanilla.setVisible(bool(self._vanilla_cats))

    def _build_category_grid(self, section: "_CollapsibleSection",
                             cats: list[str]) -> None:
        """Fill *section* with a 3-column grid of *cats*' cards (left-to-right)."""
        body = section.body_layout()
        self._clear(body)
        grid_host = QWidget()
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(3)
        for i, cat in enumerate(cats):
            card = _CategoryCard(cat, len(self._by_cat[cat]), self._select_category,
                                 _category_rep(cat, self._by_cat[cat]))
            card.set_active(cat == self._active_cat)
            self._cards[cat] = card
            grid.addWidget(card, i // 3, i % 3)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        body.addWidget(grid_host)

    def _select_category(self, category: str) -> None:
        if category == self._active_cat:
            return
        for cat, card in self._cards.items():
            card.set_active(cat == category)
        self._active_cat = category
        self._rebuild_active()

    def _rebuild_active(self) -> None:
        # Title with the manual's full section name where one exists (the cards use
        # short names to fit the grid; the open category has room for the full one).
        doc = _category_doc(self._active_cat)
        self._active.set_title((doc[0] if doc else self._active_cat).upper())
        self._active.set_doc_link(*(doc if doc else (None, None)))
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
        # The pinned bottom panel is hidden when empty, and while a search is
        # active (the results grid takes over the scroll region above).
        visible = bool(kinds) and not self._search.text().strip()
        self._in_use_panel.setVisible(visible)
        if visible:
            # Size the bottom scroll to its content, capped so it never crowds the
            # categories; past the cap it scrolls on its own. Computed from the
            # tile-grid geometry (header + rows of tiles) rather than a sizeHint,
            # which is unreliable before the panel has been laid out/shown.
            rows = (len(kinds) + _ITEM_COLS - 1) // _ITEM_COLS
            content_h = _IN_USE_HEADER_H + rows * (_TILE_SIZE + 2) + 6
            self._in_use_expanded_h = min(content_h, _IN_USE_MAX_H)
            self._on_in_use_toggled(self._in_use._toggle.isChecked())

    def _on_in_use_toggled(self, expanded: bool) -> None:
        """Shrink the pinned panel to just its header when collapsed, so a
        collapsed section doesn't leave a tall band of empty space."""
        self._in_use_scroll.setFixedHeight(
            self._in_use_expanded_h if expanded else _IN_USE_HEADER_H
        )

    # -- search ----------------------------------------------------------

    def _on_search(self, text: str) -> None:
        q = text.strip().lower()
        searching = bool(q)
        # Category/active/in-use views give way to a flat results grid on search.
        self._circuitikz.setVisible(not searching)
        self._vanilla.setVisible(not searching and bool(self._vanilla_cats))
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
        self._search.setStyleSheet(theme.line_edit_qss())
        self._scroll.viewport().setStyleSheet("background-color: %s;" % theme.SURFACE)
        self._scroll.setStyleSheet(theme.scrollbar_qss())
        self._content_host.setStyleSheet(
            "QWidget#palette_content { background-color: %s; }" % theme.SURFACE
        )
        self._in_use_scroll.viewport().setStyleSheet("background-color: %s;" % theme.SURFACE)
        self._in_use_scroll.setStyleSheet(theme.scrollbar_qss())
        self._in_use_host.setStyleSheet(
            "QWidget#in_use_content { background-color: %s; }" % theme.SURFACE
        )
        self._in_use_divider.setStyleSheet("background-color: %s; border: none;" % theme.DIVIDER)
        for sec in (self._in_use, self._circuitikz, self._vanilla,
                    self._active, self._results):
            sec.apply_theme()
        self._build_categories()
        self._rebuild_active()
        self._on_search(self._search.text())  # refreshes in-use + results grids
