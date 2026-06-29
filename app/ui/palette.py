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
    QColor, QDesktopServices, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
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

# Palette categories that are NOT CircuiTikZ symbols but our own **vanilla-TikZ**
# drawing primitives (plain ``\draw`` rectangles/circles and ``\node`` text). They
# get their own top-level palette section, separate from the CircuiTikZ component
# categories; everything not listed here is treated as CircuiTikZ. Add any future
# custom drawing category here.
_VANILLA_CATEGORIES: frozenset[str] = frozenset({"Drawing"})

# ---------------------------------------------------------------------------
# Online-manual documentation links (spec §10.2)
# ---------------------------------------------------------------------------

# The CircuiTikZ component reference is the "The components: list" page of the
# multi-page online manual; each category subsection carries a stable semantic
# ``#sec:<label>`` anchor (the LaTeX ``\label``), which we link to directly.
_MANUAL_BASE_URL = "https://rmano.github.io/circuitikz/node-The-components-list.html"

# Palette category -> (full manual subsection title, ``sec:`` fragment anchor).
# Keyed by the category name; the title is the manual's own (long) subsection
# heading, used as the link tooltip.  Keys are the short manual categories (see
# components/generate_library.py ``_CATEGORY_MAP``).
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
    """The component kind whose symbol illustrates *category*: the first member of the
    category in manual order (the manual's lead component for the section), so every
    category gets an icon instead of a blank card."""
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


# The annotation kinds offered in the quick bar, with the single-letter glyph that
# denotes each: ``short`` is the **current** annotation (i), ``open`` the **voltage**
# annotation (v) — the same i/v mnemonics as their placement keys.
_ANNOTATION_QUICK: tuple[tuple[str, str], ...] = (("short", "i"), ("open", "v"))


def _wire_quick_pixmap(size: int) -> QPixmap:
    """A small Manhattan-wire glyph (an orthogonal stepped line) for the quick bar's
    wire-tool tile."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(theme.ICON))
    pen.setWidthF(max(1.6, size / 11.0))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = float(size)
    # A two-corner orthogonal step: ╴┐_╶ from lower-left up to upper-right.
    p.drawPolyline([QPointF(s * 0.16, s * 0.72), QPointF(s * 0.5, s * 0.72),
                    QPointF(s * 0.5, s * 0.28), QPointF(s * 0.84, s * 0.28)])
    p.end()
    return pix


def _laplata_quick_pixmap(size: int) -> QPixmap:
    """A small La Plata-wire glyph (a horizontal leg, a 45° diagonal, a horizontal
    leg) for the quick bar's diagonal-routing tile."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(theme.ICON))
    pen.setWidthF(max(1.6, size / 11.0))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    s = float(size)
    # ╴╱╶ : in low-left, a 45° rise, out high-right.
    p.drawPolyline([QPointF(s * 0.16, s * 0.72), QPointF(s * 0.4, s * 0.72),
                    QPointF(s * 0.6, s * 0.28), QPointF(s * 0.84, s * 0.28)])
    p.end()
    return pix


def _letter_pixmap(letter: str, size: int) -> QPixmap:
    """A small italic-serif letter glyph (``i``/``v``) for the annotation tiles — the
    math-italic look of a CircuiTikZ current/voltage label."""
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    font = QFont("Georgia, Times New Roman, serif")
    font.setItalic(True)
    font.setPixelSize(int(size * 0.72))
    p.setFont(font)
    p.setPen(QColor(theme.ICON))
    p.drawText(pix.rect(), Qt.AlignCenter, letter)
    p.end()
    return pix


class WiringQuickBar(QWidget):
    """A slim vertical strip docked at the right edge of the canvas for the most
    common wiring gestures, a single click away without a trip to the category
    palette: the **Manhattan wire** tool at the top, then the **current** (``short``,
    *i*) and **voltage** (``open``, *v*) annotations, then the **Terminals**
    connection markers (sourced from the active library by category)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene: SchematicScene | None = None
        self._kinds = [k for k, d in REGISTRY.items() if d.category == WIRING_CATEGORY]
        # Annotation tiles for kinds the active library actually carries (bespoke
        # open/short are always present, but stay defensive).
        self._annotations = [(k, g) for k, g in _ANNOTATION_QUICK if k in REGISTRY]
        # Selected wire routing style (the active tool indicated in the bar). The bar
        # owns the selection and pushes it to the scene; rebuilt views restore it.
        self._routing = "manhattan"
        self._mode_btns: dict[str, QToolButton] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(2, 4, 2, 4)
        self._layout.setSpacing(2)
        self.setFixedWidth(_WIRING_TILE + 4)
        self._rebuild()   # always shown: it always has the wire + annotation tools

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene
        scene.set_wire_routing(self._routing)   # keep the scene in sync with the bar

    def _place(self, kind: str) -> None:
        if self._scene is not None:
            self._scene.start_placement(kind)

    def _select_routing(self, style: str) -> None:
        """Pick a routing style (Manhattan / La Plata): mark it active in the bar, set
        it on the scene, and enter Wire mode so the next drag draws in that style."""
        self._routing = style
        for s, b in self._mode_btns.items():
            b.setChecked(s == style)
        if self._scene is not None:
            self._scene.set_wire_routing(style)
            self._scene.enter_wire_mode()

    def _tool_tile(self, icon: QPixmap, tooltip: str, on_click) -> QToolButton:  # noqa: ANN001
        """A quick-bar tile carrying a hand-drawn *icon* (not a component thumbnail),
        matching the component tiles' look but invoking an arbitrary action."""
        b = QToolButton()
        b.setIcon(QIcon(icon))
        b.setIconSize(QSize(_WIRING_THUMB, _WIRING_THUMB))
        b.setFixedSize(_WIRING_TILE, _WIRING_TILE)
        b.setAutoRaise(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tooltip)
        b.setStyleSheet(
            "QToolButton { border: 1px solid transparent; border-radius: 6px; }"
            "QToolButton:hover { background: %s; border-color: %s; }"
            % (theme.HOVER, theme.HOVER_BORDER)
        )
        b.clicked.connect(on_click)
        return b

    def _mode_tile(self, icon: QPixmap, tooltip: str, style: str) -> QToolButton:
        """A **checkable** routing-mode tile (Manhattan / La Plata). The active mode is
        shown checked (accent fill + border); clicking selects it and enters Wire mode."""
        b = QToolButton()
        b.setIcon(QIcon(icon))
        b.setIconSize(QSize(_WIRING_THUMB, _WIRING_THUMB))
        b.setFixedSize(_WIRING_TILE, _WIRING_TILE)
        b.setCheckable(True)
        b.setCursor(Qt.PointingHandCursor)
        b.setToolTip(tooltip)
        b.setStyleSheet(
            "QToolButton { border: 1px solid transparent; border-radius: 6px; }"
            "QToolButton:hover { background: %s; border-color: %s; }"
            "QToolButton:checked { background: %s; border-color: %s; }"
            % (theme.HOVER, theme.HOVER_BORDER, theme.HOVER, theme.ACCENT)
        )
        b.clicked.connect(lambda _=False, s=style: self._select_routing(s))
        return b

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background-color: %s; border: none;" % theme.DIVIDER)
        return line

    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        # Wire routing modes (top): Manhattan (axis-only) and La Plata (45°), a
        # mutually-exclusive pair whose checked tile marks the active routing style.
        self._mode_btns = {}
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        for style, icon, tip in (
            ("manhattan", _wire_quick_pixmap(_WIRING_THUMB), "Manhattan wire (90°)  [W]"),
            ("laplata", _laplata_quick_pixmap(_WIRING_THUMB), "La Plata wire (45°)"),
        ):
            b = self._mode_tile(icon, tip, style)
            b.setChecked(style == self._routing)
            self._mode_btns[style] = b
            self._mode_group.addButton(b)
            self._layout.addWidget(b)
        self._layout.addWidget(self._divider())
        # Current/voltage annotations (short → i, open → v).
        for kind, glyph in self._annotations:
            self._layout.addWidget(self._tool_tile(
                _letter_pixmap(glyph, _WIRING_THUMB),
                f"{REGISTRY[kind].display_name} ({kind})",
                lambda _=False, k=kind: self._place(k)))
        # Terminals connection markers (library-sourced), below a divider.
        if self._kinds:
            self._layout.addWidget(self._divider())
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
        # Within each category the kinds keep their **manual order**: REGISTRY is built
        # in the CircuiTikZ manual's scrape sequence, so iterating it and appending
        # per-category preserves, within each category, the order the components appear
        # in the manual — no re-sort.
        for kind, defn in REGISTRY.items():
            self._by_cat[defn.category].append(kind)
        # Categories follow the manual's own section sequence — registry/
        # definitions.json order is not the manual order, so we sort by
        # ``_CATEGORY_DOC`` (authored in manual order); a bespoke category with no
        # manual section (Drawing) falls after.
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
