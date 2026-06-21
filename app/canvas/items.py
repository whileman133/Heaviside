"""
Canvas items — one QGraphicsItem subclass per component type.

Symbols are **not** hand-drawn.  Each component's geometry is translated from
the CircuiTikZ SVG export recorded in ``components/geometry.json``
(see :mod:`app.canvas.svgsym`).  The base :class:`ComponentItem` strokes/fills
the translated :class:`QPainterPath` set; subclasses exist only to provide the
per-``kind`` identity required by :data:`ITEM_CLASSES`, plus any extra terminal
lead stubs needed where a multi-terminal symbol's drawn terminals do not sit
exactly on the registry pin grid points.

Every ComponentItem:
  • Stores a reference to the Component data-model object.
  • Implements boundingRect() from the ComponentDef bbox x GRID_PX.
  • Implements paint() by stroking/filling the SVG-derived QPainterPaths.
  • Draws pin indicator dots at every PinDef offset.
  • Adjusts pen/brush color for normal / selected / hover / ghost states.
  • Renders component labels as typeset math (vector, via app.preview.mathrender),
    placed per-slot on conventional sides; raw text is the fallback (see §5.8).

ITEM_CLASSES at the bottom of this module is registered into
app.components.registry.ITEM_CLASSES so the rest of the application can map a
component kind to its item class without importing Qt everywhere.
"""

from __future__ import annotations

import math
import re

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem
from shiboken6 import isValid

# Colors are read module-qualified (style.COLOR_*) so the light/dark palette
# swap in app/canvas/style.set_dark() takes effect on the next repaint.
from app.canvas import style
from app.canvas.style import (
    GRID_PX,
    LINE_W,
    LINE_W_THICK,
    OPEN_ANNOTATION_OPACITY,
    PIN_R,
)
from app.canvas.svgsym import is_thick, symbol_paths
from app.components.registry import REGISTRY
from app.schematic.model import HOP_ARC_RADIUS_GU, HOP_HALF_GU

if TYPE_CHECKING:
    from app.components.model import BipoleComponent, Component, DrawingComponent, TextNodeComponent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pen(color: str, width: float, style: Qt.PenStyle = Qt.SolidLine) -> QPen:
    p = QPen(QColor(color))
    p.setWidthF(width)
    p.setStyle(style)
    p.setCapStyle(Qt.RoundCap)
    p.setJoinStyle(Qt.RoundJoin)
    return p


#: Line-hop geometry in pixels (shared GU source with the LaTeX generator). The
#: hump radius matches the CircuiTikZ ``jump crossing`` arc; ``HOP_HALF_PX`` is
#: the half-width out to its anchors (used only for bounding-box margins).
HOP_R: float = HOP_ARC_RADIUS_GU * GRID_PX
HOP_HALF_PX: float = HOP_HALF_GU * GRID_PX
#: Cubic-Bezier control-point offset that makes one cubic approximate a 180°
#: bump whose apex reaches exactly the radius (3/4 · 4/3 = 1).
_HOP_KAPPA: float = 4.0 / 3.0


def _manhattan(a: QPointF, b: QPointF) -> float:
    return abs(a.x() - b.x()) + abs(a.y() - b.y())


def _append_hop_bump(
    path: QPainterPath,
    center: QPointF,
    along: QPointF,
    bulge: QPointF,
    r: float,
) -> None:
    """Append a semicircular bump to *path*, centred at *center*.

    *along* is the unit travel direction along the segment; *bulge* the unit
    perpendicular the bump arcs toward. The path's current point must already be
    at ``center - r·along``; on return it is at ``center + r·along``. The bump is
    one cubic Bézier whose apex reaches exactly *r* on the *bulge* side, so no
    Qt arc-angle bookkeeping (and its y-down sign traps) is involved.
    """
    p0 = QPointF(center.x() - r * along.x(), center.y() - r * along.y())
    p3 = QPointF(center.x() + r * along.x(), center.y() + r * along.y())
    c1 = QPointF(p0.x() + _HOP_KAPPA * r * bulge.x(), p0.y() + _HOP_KAPPA * r * bulge.y())
    c2 = QPointF(p3.x() + _HOP_KAPPA * r * bulge.x(), p3.y() + _HOP_KAPPA * r * bulge.y())
    path.lineTo(p0)
    path.cubicTo(c1, c2, p3)


def _polyline_with_hops(pts: list[QPointF], hops: list) -> QPainterPath:
    """A polyline through *pts* (px) with a semicircular bump at each hop.

    *hops* is a list of objects carrying a ``.point`` (GU). Each is matched to
    the segment it lies on by coordinate (orientation derived from the segment),
    so hops that don't fall on the polyline are ignored — letting callers feed
    committed *or* live-preview geometry with the same hop list. Bumps bulge up
    over a horizontal segment and left over a vertical one, matching the
    CircuiTikZ ``jump crossing`` (rotated 90° for a vertical hopper). Shared by
    :class:`WireItem` and :class:`WirePreviewItem`.
    """
    path = QPainterPath()
    if not pts:
        return path
    path.moveTo(pts[0])
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        horizontal = abs(a.y() - b.y()) < 1e-6
        seg = []
        for hop in hops:
            hx, hy = hop.point[0] * GRID_PX, hop.point[1] * GRID_PX
            if horizontal:
                if abs(hy - a.y()) < 1e-6 and min(a.x(), b.x()) < hx < max(a.x(), b.x()):
                    seg.append(QPointF(hx, hy))
            else:
                if abs(hx - a.x()) < 1e-6 and min(a.y(), b.y()) < hy < max(a.y(), b.y()):
                    seg.append(QPointF(hx, hy))
        if not seg:
            path.lineTo(b)
            continue
        if horizontal:
            along = QPointF(1.0 if b.x() >= a.x() else -1.0, 0.0)
            bulge = QPointF(0.0, -1.0)                     # bump upward
            seg.sort(key=lambda p: p.x() * along.x())
        else:
            along = QPointF(0.0, 1.0 if b.y() >= a.y() else -1.0)
            bulge = QPointF(-1.0, 0.0)                     # bump left (matches
            #                          a CircuiTikZ jump crossing rotated 90°)
            seg.sort(key=lambda p: p.y() * along.y())
        # Clamp each bump's radius so it never overruns a neighbour/endpoint.
        stops = [a] + seg + [b]
        for k, c in enumerate(seg):
            prev, nxt = stops[k], stops[k + 2]
            gap = min(_manhattan(prev, c), _manhattan(c, nxt))
            r = min(HOP_R, 0.45 * gap)
            _append_hop_bump(path, c, along, bulge, r)
        path.lineTo(b)
    return path


# ---------------------------------------------------------------------------
# Label child item
# ---------------------------------------------------------------------------

# LaTeX points per grid unit (1 GU = 1 cm). Canvas pixels = pt * GRID_PX / this.
_PT_PER_GU = 28.35

# Vector-math rendering: mathrender returns paths in LaTeX pt at TEMPLATE_PT
# (10 pt).  Multiply by _VEC_SCALE to convert pt -> canvas px (same factor that
# sizes label QFonts), then by font_size/_VEC_TEMPLATE_PT for the actual size.
_VEC_SCALE = GRID_PX / _PT_PER_GU
_VEC_TEMPLATE_PT = 10.0

# Z-value applied to the edited item's parent while in-place LaTeX editing is
# active, so the editor (and its solid backdrop) floats above all other items.
_EDIT_Z = 10_000.0

# Options-label sizing. The label is sized in canvas *pixels* derived from a
# LaTeX point size the same way text-node fonts are (see _fonted_qfont), so the
# on-canvas label matches the proportions of the compiled output rather than
# rendering at a small fixed point size.
_LABEL_FONT_PT = 10.0
_LABEL_FONT_PX = max(1, round(_LABEL_FONT_PT * GRID_PX / _PT_PER_GU))  # ≈ 21 px
_LABEL_LINE_H = _LABEL_FONT_PX + 4   # px height per label row (font + leading)
_LABEL_GAP = 8       # px gap between bbox top edge and bottom of label block
# Padding (px) of the opaque backdrop drawn behind axis-centred labels so the
# annotation line does not appear to run into the text.
_LABEL_BG_PAD = 3.0
# Voltage sources whose default (unsuffixed) `v=` label sits on the opposite
# side from passives — CircuiTikZ's source voltage convention (see
# ComponentItem._slot_direction).  Current sources (I/cI/isourcesin) follow the
# passive default and are NOT listed.
_VOLTAGE_SOURCE_KINDS = frozenset({"V", "cV", "vsourcesin"})

# Bodyless components: a current (`i=`) arrow is centred on the wire's midpoint
# (like CircuiTikZ) rather than ridden out on the exit lead, since there is no
# body in the middle to clear. ``open`` also centres its label; ``short`` does not.
_CURRENT_CENTERED_KINDS = frozenset({"short", "open"})

# Annotation decoration geometry (canvas px) — the CircuiTikZ-style ± signs and
# direction arrows drawn alongside the v=/i= text labels (§5.8). Sizes are in
# screen pixels (the decoration is counter-rotated so ± glyphs stay upright).
_SIGN_LEN = 8.0          # arm length of a drawn + / - glyph
# ± glyph and arrow-shaft strokes track the wire width so they read at the same
# weight as CircuiTikZ's annotations (base line width), not bolder.
_SIGN_STROKE = LINE_W    # ± glyph stroke width
_SIGN_OFFSET = 7.0       # ± glyph clearance off the body, toward the label side
_SIGN_INSET = 0.80       # ± glyphs sit at ±inset·half-span (just inside the pins)
_ARROW_STROKE = LINE_W   # voltage arrow shaft width
_ARROW_HEAD = 8.0        # arrowhead length (european voltage arrow)
_ARROW_HEAD_W = 6.0      # arrowhead full width
# The current (`i=`) arrowhead is drawn larger than the voltage arrow to match
# CircuiTikZ's prominent current-flow arrow.
_CUR_ARROW_HEAD = 13.0   # current arrowhead length
_CUR_ARROW_HEAD_W = 11.0 # current arrowhead full width
_ARROW_SPAN = 0.62       # voltage arrow shaft spans ±span·half-length about centre
# Perpendicular band the european-voltage arrow reserves between the body and its
# text label so the two never overlap.
_DEC_BAND = 13.0
# Current (`i=`) annotation — drawn as a bare arrowHEAD on the wire near the exit
# (second-pin) lead, pointing toward pin1 (the current direction). Only the head
# is drawn (no shaft), so the arrow never overlaps the component body; the value
# label is centred directly over the head. _CUR_ARROW_TIP is the head-tip position
# as a fraction of the half-span (0 = centre, 1 = second pin).
_CUR_ARROW_TIP = 0.82

# Fallback family lists passed to QFont.setFamilies() — Qt walks the list and
# uses the first installed face, so at least one matches on any platform.
_FONT_FAMILY_LISTS: dict[str, list[str]] = {
    "serif": ["Georgia", "Times New Roman", "Times", "DejaVu Serif"],
    "sans":  ["Arial", "Helvetica", "Liberation Sans", "DejaVu Sans"],
    "mono":  ["Courier New", "Courier", "Liberation Mono", "DejaVu Sans Mono"],
}


def _fonted_qfont(comp) -> "QFont":  # noqa: ANN001
    """Build a QFont from a FontedComponent's size/bold/italic/family.

    Font size is converted from LaTeX points to canvas pixels. An empty
    ``font_family`` means the LaTeX document default (serif / Computer Modern),
    so it falls back to the serif list to keep the canvas matching the output.
    """
    font = QFont()
    font.setPixelSize(max(1, round(comp.font_size * GRID_PX / _PT_PER_GU)))
    font.setBold(comp.font_bold)
    font.setItalic(comp.font_italic)
    families = _FONT_FAMILY_LISTS.get(comp.font_family or "serif")
    if families:
        font.setFamilies(families)
    return font


class LabelTextItem(QGraphicsTextItem):
    """In-place editor for a component's raw options string (e.g. ``l=$R_1$``).

    Display of labels is handled by :class:`_SlotLabel` (typeset math, §5.8);
    this item is shown only while editing.  Call :meth:`begin_edit` to activate
    editing; it commits via its callback on focus-loss, Enter, or Return, and
    Escape cancels.  While editing it paints a solid backdrop (see :meth:`paint`)
    and is not draggable.

    (The drag-move and vector-display code below is retained but dormant: the
    item is no longer movable, and slot display lives in :class:`_SlotLabel`.)
    """

    def __init__(self, parent: "ComponentItem") -> None:
        super().__init__(parent)
        self._editing = False
        self._hovered = False
        self._saved_text: str = ""
        self._commit_cb = None  # callable(text: str) -> None
        self._end_cb = None     # callable() -> None, fired when editing ends
        self._move_cb = None    # callable(QPointF) -> None
        self._drag_origin: QPointF | None = None  # pos at mouse-press
        self._saved_parent_z: float | None = None  # parent z restored after edit
        # Vector-math preview: rendered QPainterPath (pt units) for the current
        # displayable fragment, painted instead of raw text when not editing.
        self._vec_path: QPainterPath | None = None
        self._vec_fragment: str | None = None

        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        f = self.font()
        f.setPixelSize(_LABEL_FONT_PX)
        self.setFont(f)
        self.setDefaultTextColor(QColor(style.COLOR_NORMAL))

    def set_commit_callback(self, cb) -> None:  # noqa: ANN001
        self._commit_cb = cb

    def set_end_callback(self, cb) -> None:  # noqa: ANN001
        """Set a callback fired whenever editing ends (commit *or* cancel)."""
        self._end_cb = cb

    def set_move_callback(self, cb) -> None:  # noqa: ANN001
        """Set callback fired with the final QPointF position after a drag."""
        self._move_cb = cb

    @property
    def is_editing(self) -> bool:
        return self._editing

    def begin_edit(self) -> None:
        """Activate the text editor, selecting all existing text."""
        self._saved_text = self.toPlainText()
        self._editing = True
        # Float the whole component (and this editor) above other items so the
        # editor's solid backdrop isn't painted over by overlapping elements.
        parent = self.parentItem()
        if parent is not None:
            self._saved_parent_z = parent.zValue()
            parent.setZValue(_EDIT_Z)
        self._apply_text_color()
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setFocus(Qt.MouseFocusReason)
        cursor = self.textCursor()
        cursor.select(cursor.SelectionType.Document)
        self.setTextCursor(cursor)

    def end_edit(self, commit: bool = True) -> None:
        """Deactivate editing, optionally committing the new text."""
        if not self._editing:
            return
        self._editing = False
        new_text = self.toPlainText().strip()
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.clearFocus()
        parent = self.parentItem()
        if parent is not None and self._saved_parent_z is not None:
            parent.setZValue(self._saved_parent_z)
        self._saved_parent_z = None
        self._apply_text_color()
        if commit:
            if self._commit_cb is not None:
                self._commit_cb(new_text)
        else:
            self.setPlainText(self._saved_text)
        if self._end_cb is not None:
            self._end_cb()

    def focusOutEvent(self, event) -> None:  # noqa: N802
        self.end_edit(commit=True)
        super().focusOutEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            # Shift+Return inserts a newline (one option per line); plain Return
            # commits.
            if event.modifiers() & Qt.ShiftModifier:
                cursor = self.textCursor()
                cursor.insertText("\n")
                self.setTextCursor(cursor)
                return
            self.end_edit(commit=True)
            return
        if event.key() == Qt.Key_Escape:
            self.end_edit(commit=False)
            return
        super().keyPressEvent(event)

    def _text_qcolor(self) -> QColor:
        """The colour for the label given current hover/edit/selection state."""
        parent = self.parentItem()
        draggable = bool(self.flags() & QGraphicsItem.ItemIsMovable)
        parent_selected = parent is not None and parent.isSelected()
        show_hover = (
            self._hovered
            and draggable
            and not parent_selected
            and not self._editing
        )
        return QColor(style.COLOR_HOVER if show_hover else style.COLOR_NORMAL)

    def _apply_text_color(self) -> None:
        """Set text colour based on current interactive/hover/edit state."""
        self.setDefaultTextColor(self._text_qcolor())
        self.update()  # repaint the vector preview if one is shown

    # ------------------------------------------------------------------
    # Vector-math preview
    # ------------------------------------------------------------------

    def request_vector(self, fragment: str) -> None:
        """Request (async) a vector render of *fragment* to show instead of raw
        text.  No-op if the same fragment is already requested or rendered."""
        fragment = (fragment or "").strip()
        if fragment == (self._vec_fragment or ""):
            return
        self._vec_fragment = fragment or None
        if not fragment:
            self.prepareGeometryChange()
            self._vec_path = None
            self.update()
            return

        from app.preview.mathrender import render_async

        def _on_done(path, frag=fragment):  # noqa: ANN001
            # The delivery is queued: this item's C++ object may have been
            # deleted (scene torn down) by the time it fires.
            if not isValid(self) or frag != (self._vec_fragment or ""):
                return  # deleted, or a newer request superseded this one
            self.prepareGeometryChange()
            self._vec_path = path
            self.update()

        render_async(fragment, _on_done)

    def retypeset(self) -> None:
        """Re-render the current fragment with the active math engine (e.g. after
        the ziamath debug preference changed).  No-op when nothing is shown."""
        frag = self._vec_fragment
        if frag:
            self._vec_fragment = None  # defeat request_vector's same-fragment guard
            self.request_vector(frag)

    def _vec_rect(self) -> QRectF:
        """Scaled bounding rect of the vector path in item coordinates."""
        r = self._vec_path.boundingRect()
        return QRectF(0.0, 0.0, r.width() * _VEC_SCALE, r.height() * _VEC_SCALE)

    def boundingRect(self) -> QRectF:  # noqa: N802
        if self._vec_path is not None and not self._editing:
            return self._vec_rect().adjusted(-2.0, -2.0, 2.0, 2.0)
        return super().boundingRect()

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001, N802
        if self._editing:
            # Solid backdrop + border so the raw LaTeX stays readable over any
            # underlying wires, symbols, or other labels.
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.setPen(_pen(style.COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor(style.COLOR_LABEL_BG)))
            painter.drawRoundedRect(
                self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5), 3.0, 3.0
            )
            painter.restore()
            super().paint(painter, option, widget)
            return
        if self._vec_path is not None:
            painter.setRenderHint(QPainter.Antialiasing, True)
            painter.save()
            painter.scale(_VEC_SCALE, _VEC_SCALE)
            painter.fillPath(self._vec_path, QBrush(self._text_qcolor()))
            painter.restore()
            return
        super().paint(painter, option, widget)

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._apply_text_color()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._apply_text_color()
        super().hoverLeaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self._editing:
            self._drag_origin = self.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if not self._editing and self._drag_origin is not None:
            new_pos = self.pos()
            if new_pos != self._drag_origin:
                if self._move_cb is not None:
                    self._move_cb(new_pos)
            self._drag_origin = None


# ---------------------------------------------------------------------------
# Per-side annotation label (display only)
# ---------------------------------------------------------------------------

def _reissue_vector_render(item) -> None:  # noqa: ANN001
    """Re-render *item*'s current fragment with the active math engine.

    Shared by the baseline-anchored label items (``_SlotLabel`` / ``_WireEndLabel``
    / ``_WireMidLabel``), which all hold ``_fragment``/``_path`` and a
    ``_reposition``.  Used to refresh labels after the math engine changes (e.g.
    the ziamath debug preference).  No-op when the item shows nothing.
    """
    frag = getattr(item, "_fragment", None)
    if not frag:
        return
    from app.preview.mathrender import render_async

    def _done(path, f=frag):  # noqa: ANN001
        if not isValid(item) or f != (getattr(item, "_fragment", None) or ""):
            return
        item.prepareGeometryChange()
        item._path = path
        item._reposition()
        item.update()

    render_async(frag, _done)


class _SlotLabel(QGraphicsItem):
    """Non-interactive, baseline-anchored vector render of one annotation slot.

    The parent :class:`ComponentItem` positions it on a side of the component
    body (via :meth:`configure`) and applies a counter-rotation so the text
    stays upright.  It shows nothing until its async render lands; the path is
    baseline-normalised (baseline at y=0), so siblings on the same side share a
    baseline.
    """

    def __init__(self, parent: "ComponentItem") -> None:
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(True)
        self._path: QPainterPath | None = None
        self._fragment: str | None = None
        # Placement geometry (screen-relative to the item origin), set by the
        # parent in _layout_slots and applied once the path is known.
        self._dir = QPointF(0.0, -1.0)        # unit offset direction (screen)
        self._center_rel = QPointF(0.0, 0.0)  # comp centre, screen-rel to origin
        self._base_dist = 0.0                 # clearance from centre along _dir
        self._step = 0.0                      # stacking offset along _dir
        self._inv = QTransform()              # screen-rel -> parent-local
        self._centered = False                # centre on the axis vs. beside it

    def retypeset(self) -> None:
        """Re-render with the active math engine (see _reissue_vector_render)."""
        _reissue_vector_render(self)

    def configure(
        self,
        fragment: str,
        direction: QPointF,
        center_rel: QPointF,
        base_dist: float,
        step: float,
        inv: QTransform,
        centered: bool = False,
    ) -> None:
        """Set this slot's text and screen-space offset direction/placement.

        When ``centered`` is set the label centre is pinned to ``center_rel``
        (offset only by ``step`` for stacking), so it sits *over* the axis
        rather than clearing it by half the label's extent.
        """
        self._dir = direction
        self._center_rel = center_rel
        self._base_dist = base_dist
        self._step = step
        self._inv = inv
        self._centered = centered
        if fragment != (self._fragment or ""):
            self._fragment = fragment or None
            if not fragment:
                self.prepareGeometryChange()
                self._path = None
            else:
                from app.preview.mathrender import render_async

                def _done(path, frag=fragment):  # noqa: ANN001
                    if not isValid(self) or frag != (self._fragment or ""):
                        return
                    self.prepareGeometryChange()
                    self._path = path
                    self._reposition()
                    self.update()

                render_async(fragment, _done)
        self._reposition()
        self.update()

    def _reposition(self) -> None:
        if self._path is None:
            return
        r = self._path.boundingRect()
        s = _VEC_SCALE
        w, h = r.width() * s, r.height() * s
        u = self._dir
        # The upright label's half-extent along the offset direction.
        label_half = abs(u.x()) * w / 2.0 + abs(u.y()) * h / 2.0
        # Centred labels sit on the axis (no half-extent clearance); otherwise
        # clear the body/centre by the label's half-extent plus the base gap.
        dist = self._step if self._centered else self._base_dist + label_half + self._step
        # Label centre, screen-relative to the item origin.
        cx = self._center_rel.x() + u.x() * dist
        cy = self._center_rel.y() + u.y() * dist
        # Anchor (baseline-left of the upright text) = centre - (w/2, midY).
        anchor = QPointF(cx - w / 2.0, cy - r.center().y() * s)
        self.setPos(self._inv.map(anchor))

    def _scaled_rect(self) -> QRectF:
        """The rendered glyph bounds in item-local px (path bounds × scale)."""
        r = self._path.boundingRect()
        s = _VEC_SCALE
        return QRectF(r.left() * s, r.top() * s, r.width() * s, r.height() * s)

    def boundingRect(self) -> QRectF:  # noqa: N802
        if self._path is None:
            return QRectF()
        pad = _LABEL_BG_PAD if self._centered else 1.0
        return self._scaled_rect().adjusted(-pad, -pad, pad, pad)

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        parent = self.parentItem()
        if hasattr(parent, "_set_hovered"):
            parent._set_hovered(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        parent = self.parentItem()
        if hasattr(parent, "_set_hovered"):
            parent._set_hovered(False)
        super().hoverLeaveEvent(event)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001, N802
        if self._path is None:
            return
        parent = self.parentItem()
        color = parent._label_color() if hasattr(parent, "_label_color") else QColor(style.COLOR_NORMAL)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # Axis-centred labels (e.g. the voltage annotation) sit on top of the
        # line, so give them an opaque backdrop with a little padding to keep
        # the line from appearing to run into the text.
        if self._centered:
            painter.fillRect(
                self._scaled_rect().adjusted(
                    -_LABEL_BG_PAD, -_LABEL_BG_PAD, _LABEL_BG_PAD, _LABEL_BG_PAD
                ),
                QColor(style.COLOR_LABEL_BG),
            )
        painter.save()
        painter.scale(_VEC_SCALE, _VEC_SCALE)
        painter.fillPath(self._path, QBrush(color))
        painter.restore()


# ---------------------------------------------------------------------------
# Voltage / current decoration (display only)
# ---------------------------------------------------------------------------

class _AnnotationDecoration(QGraphicsItem):
    """CircuiTikZ-style voltage/current decoration for one annotation slot (§5.8).

    Draws, in the component's on-screen frame:

      * ``v`` (american voltage) — a ``+`` and ``−`` glyph at the two terminals
        (``+`` at the first-traversed pin, ``−`` at the second), matching the
        compiled (Y-flipped) CircuiTikZ output that the canvas mirrors.
      * ``v`` (european voltage) — a shaft+arrow alongside the body, pointing
        from the ``+`` terminal toward the ``−`` (first → second pin).
      * ``i`` (current) — a shaft+arrow along the lead axis in the traversal
        (first → second pin) direction.

    Like :class:`_SlotLabel` it is counter-rotated so the ± glyphs stay upright;
    the arrows follow the on-screen lead axis. The text value itself is rendered
    separately by the slot's :class:`_SlotLabel`, stacked just outside this
    decoration's perpendicular band.
    """

    def __init__(self, parent: "ComponentItem") -> None:
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self._mode = ""                  # "" | "v_american" | "v_european" | "current"
        self._axis = QPointF(1.0, 0.0)   # unit vector, first→second pin (screen)
        self._side = QPointF(0.0, -1.0)  # unit vector toward the annotation side
        self._half_len = 0.0             # half the on-screen terminal span
        self._perp = 0.0                 # distance from the lead axis to the glyphs
        self._reversed = False           # `<` modifier: flip current dir / v polarity
        self._centered = False           # current arrow centred on the line (open)

    def configure(
        self,
        mode: str,
        axis_unit: QPointF,
        half_len: float,
        side: QPointF,
        perp: float,
        center_rel: QPointF,
        inv: QTransform,
        reversed: bool = False,
        centered: bool = False,
    ) -> None:
        """Place this decoration. ``center_rel`` is the component centre and
        ``inv`` the screen→parent-local transform (both from ``_slot_geometry``);
        the decoration is counter-rotated and pinned at the centre, drawing its
        glyphs at screen-space offsets from there. ``reversed`` flips the current
        arrow direction / the voltage polarity (the CircuiTikZ ``<`` modifier);
        ``centered`` draws the current arrowhead at the line's midpoint (the
        ``open`` annotation) instead of out on the exit lead."""
        self.prepareGeometryChange()
        self._mode = mode
        self._axis = axis_unit
        self._half_len = half_len
        self._side = side
        self._perp = perp
        self._reversed = reversed
        self._centered = centered
        self.setTransform(inv)
        self.setPos(inv.map(center_rel))
        self.setVisible(bool(mode))
        self.update()

    def boundingRect(self) -> QRectF:  # noqa: N802
        if not self._mode:
            return QRectF()
        ext = (self._half_len + self._perp + _DEC_BAND
               + max(_ARROW_HEAD, _CUR_ARROW_HEAD) + 4.0)
        return QRectF(-ext, -ext, 2.0 * ext, 2.0 * ext)

    def _draw_plus(self, painter: QPainter, c: QPointF) -> None:
        h = _SIGN_LEN / 2.0
        painter.drawLine(QPointF(c.x() - h, c.y()), QPointF(c.x() + h, c.y()))
        painter.drawLine(QPointF(c.x(), c.y() - h), QPointF(c.x(), c.y() + h))

    def _draw_minus(self, painter: QPainter, c: QPointF) -> None:
        h = _SIGN_LEN / 2.0
        painter.drawLine(QPointF(c.x() - h, c.y()), QPointF(c.x() + h, c.y()))

    def _draw_arrowhead(self, painter: QPainter, color: QColor,
                        tip: QPointF, ux: float, uy: float, *,
                        length: float = _ARROW_HEAD,
                        width: float = _ARROW_HEAD_W) -> None:
        """Filled triangular arrowhead at *tip*, pointing along unit (ux, uy)."""
        px, py = -uy, ux  # perpendicular
        base = QPointF(tip.x() - ux * length, tip.y() - uy * length)
        hw = width / 2.0
        left = QPointF(base.x() + px * hw, base.y() + py * hw)
        right = QPointF(base.x() - px * hw, base.y() - py * hw)
        head_path = QPainterPath(tip)
        head_path.lineTo(left)
        head_path.lineTo(right)
        head_path.closeSubpath()
        painter.fillPath(head_path, QBrush(color))

    def _draw_curved_arrow(self, painter: QPainter, color: QColor,
                           tail: QPointF, head: QPointF, ctrl: QPointF) -> None:
        """A quadratic-Bézier arc from *tail* to *head* bowing through *ctrl*, with
        a filled arrowhead at *head* tangent to the curve — CircuiTikZ's european
        voltage arrow shape."""
        pen = QPen(color)
        pen.setWidthF(_ARROW_STROKE)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        path = QPainterPath(tail)
        path.quadTo(ctrl, head)
        painter.drawPath(path)
        d = head - ctrl  # tangent at the head end
        ln = math.hypot(d.x(), d.y()) or 1.0
        self._draw_arrowhead(painter, color, head, d.x() / ln, d.y() / ln)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001, N802
        if not self._mode:
            return
        parent = self.parentItem()
        color = (parent._label_color() if hasattr(parent, "_label_color")
                 else QColor(style.COLOR_NORMAL))
        painter.setRenderHint(QPainter.Antialiasing, True)
        a, s, L = self._axis, self._side, self._half_len
        off = QPointF(s.x() * self._perp, s.y() * self._perp)

        # Voltage polarity follows the compiled (Y-flipped) CircuiTikZ output,
        # which is the canvas's visual ground truth: the `+` sits at the
        # first-traversed pin (pin0, at -axis) and the `−` at the second (pin1,
        # at +axis). The european arrow runs from `+` to `−` (pin0 → pin1).
        if self._mode == "v_american":
            pen = QPen(color)
            pen.setWidthF(_SIGN_STROKE)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            d = _SIGN_INSET * L
            first = QPointF(-a.x() * d + off.x(), -a.y() * d + off.y())   # first pin
            second = QPointF(a.x() * d + off.x(), a.y() * d + off.y())    # second pin
            # Default polarity: + at the first pin, − at the second. `v<` (reversed)
            # swaps them, matching CircuiTikZ.
            if self._reversed:
                self._draw_minus(painter, first)
                self._draw_plus(painter, second)
            else:
                self._draw_plus(painter, first)
                self._draw_minus(painter, second)
        elif self._mode == "v_european":
            # CircuiTikZ draws the european voltage as a curved arc bowing toward
            # the label side, the arrowhead at the head end (toward the − terminal;
            # `v<` reverses it). The endpoints sit near the body and the arc bows
            # out to the decoration's perpendicular depth (`self._perp`) at the
            # middle.
            span = _ARROW_SPAN * L
            perp_end = self._perp * 0.4                 # endpoints near the body
            end_off = QPointF(s.x() * perp_end, s.y() * perp_end)
            ctrl_perp = 2.0 * self._perp - perp_end     # quad midpoint -> self._perp
            ctrl = QPointF(s.x() * ctrl_perp, s.y() * ctrl_perp)
            p_first = QPointF(-a.x() * span + end_off.x(), -a.y() * span + end_off.y())
            p_second = QPointF(a.x() * span + end_off.x(), a.y() * span + end_off.y())
            if self._reversed:
                self._draw_curved_arrow(painter, color, p_second, p_first, ctrl)
            else:
                self._draw_curved_arrow(painter, color, p_first, p_second, ctrl)
        elif self._mode == "current":
            # A bare arrowhead on the wire (no shaft, so it never overlaps the
            # body). Default: near the exit lead, pointing toward the second pin.
            # `i<` reverses the direction and moves it to the entry lead; the
            # `open` annotation centres the head on the line instead (_centered).
            # The label is centred over the head (see _layout_slots).
            sign = -1.0 if self._reversed else 1.0
            along = sign * (_CUR_ARROW_HEAD / 2.0 if self._centered
                            else _CUR_ARROW_TIP * L)
            tip = QPointF(a.x() * along + off.x(), a.y() * along + off.y())
            self._draw_arrowhead(painter, color, tip, sign * a.x(), sign * a.y(),
                                 length=_CUR_ARROW_HEAD, width=_CUR_ARROW_HEAD_W)


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class ComponentItem(QGraphicsItem):
    """
    Base for all component graphics items.

    Painting is fully data-driven: the symbol geometry comes from the SVG
    geometry via :func:`app.canvas.svgsym.symbol_paths`.  Subclasses only set
    the component ``kind`` (implicitly, via the Component they wrap).

    A single child :class:`LabelTextItem` shows the component's raw options
    string (e.g. ``l=$R_1$, v=$V_s$``) above the bbox when non-empty.
    Double-clicking the label or the component body activates in-place editing.
    """

    def __init__(self, component: "Component", parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self._component = component
        self._defn = REGISTRY[component.kind]

        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)

        self._hovered = False
        self._ghost = False   # True during placement preview

        x, y = component.position
        self.setPos(x * GRID_PX, y * GRID_PX)
        # Apply rotation and optional horizontal mirror via QTransform.
        # QTransform.scale/rotate pre-multiply the coordinate system, so for a
        # *point* the operations apply in reverse of the call order: the point is
        # rotated first, then the global Flip-X (scale(-1,1)). Mirror is thus a
        # global horizontal flip of the already-rotated component — the
        # rotate-then-mirror order that component_pin_positions and the code
        # generator match, keeping a mirrored vertical bipole's terminals in place
        # (§7 Mirror).
        t = QTransform()
        if component.mirror:
            t.scale(-1.0, 1.0)
        t.rotate(component.rotation)
        self.setTransform(t)

        # Layer: every component carries a z_order (front/back). Drawing
        # annotations sit on the shared wire/component z-stack; plain circuit
        # symbols default to 0 and move only when sent to front/back.
        self.setZValue(component.z_order)

        # The LabelTextItem is now the in-place *editor* only (double-click);
        # display is handled by per-side _SlotLabel children.  It is hidden when
        # not editing and is not draggable (labels auto-place on their sides).
        self._options_item = LabelTextItem(self)
        self._options_item.setFlag(QGraphicsItem.ItemIsMovable, False)
        self._options_item.set_commit_callback(self._on_options_commit)
        self._slot_items: list[_SlotLabel] = []
        self._decoration_items: list[_AnnotationDecoration] = []
        self._sync_options_item()

    # ------------------------------------------------------------------
    # component property — setter syncs the options child item
    # ------------------------------------------------------------------

    @property
    def component(self) -> "Component":
        return self._component

    # Instance-resolved geometry/pins/bbox — identical to the registry defaults
    # for a fixed kind, but vary with the parameter value for a parametric kind
    # (logic gates).  Painting, hit-testing, and labels go through these.
    def _resolved_pins(self) -> list:
        from app.components import library
        return library.resolved_pins(self._component)

    def _instance_bbox(self) -> tuple:
        from app.components import library
        nd = library.param_n_data(self._component)
        return tuple(nd["bbox"]) if nd else self._defn.bbox

    def _geometry_kind(self) -> str:
        """Geometry key for this instance: kind + parametric suffix + variant suffix."""
        from app.components import library
        c = self._component
        return c.kind + library.param_geometry_suffix(c) + library.variant_geometry_suffix(
            c.kind, c.variants)

    def _gate_scale(self) -> float:
        """The body size multiplier (``Component.scale``) for a scalable kind — a
        logic gate or digital block; 1.0 for every other kind. The body is scaled
        about the placement origin (a gate's ``out`` pin, a block's centre)."""
        from app.components import library
        c = self._component
        return float(getattr(c, "scale", 1.0)) if library.is_scalable(c.kind) else 1.0

    @component.setter
    def component(self, comp: "Component") -> None:
        if self._options_item.is_editing:
            self._options_item.end_edit(commit=False)
        self.prepareGeometryChange()
        self._component = comp
        self.setZValue(comp.z_order)   # keep the canvas layer in sync (front/back)
        self._sync_options_item()

    def _on_options_commit(self, text: str) -> None:
        """Called by the LabelTextItem when the user commits an in-place edit."""
        scene = self.scene()
        if scene is not None and hasattr(scene, "edit_component_options"):
            scene.edit_component_options(
                self._component.id, self._options_from_editable(text)
            )

    # Options are edited one slot per line for readability; converted to/from the
    # stored comma-separated form. TextNodeItem overrides these to identity.
    def _options_to_editable(self, options: str) -> str:
        from app.preview.mathrender import options_to_editable
        return options_to_editable(options)

    def _options_from_editable(self, text: str) -> str:
        from app.preview.mathrender import editable_to_options
        return editable_to_options(text)

    def _editor_center_pos(self) -> QPointF:
        """Local pos that centres the in-place editor over the component body."""
        p0, p1 = self._lead_terminals_local()
        center_local = QPointF((p0.x() + p1.x()) / 2.0, (p0.y() + p1.y()) / 2.0)
        t = self.transform()
        inv, _ = t.inverted()
        er = self._options_item.boundingRect()
        target = t.map(center_local)
        anchor = QPointF(target.x() - er.width() / 2.0, target.y() - er.height() / 2.0)
        return inv.map(anchor)

    def _label_counter_transform(self) -> QTransform:
        """Inverse of this item's own transform, so labels stay horizontal."""
        inv, _ = self.transform().inverted()
        return inv

    def apply_transform(self, t: QTransform) -> None:
        """Set this item's transform and re-lay out the labels."""
        self.setTransform(t)
        self._sync_options_item()

    def _sync_options_item(self) -> None:
        """Hide the editor (unless active) and lay out the per-side slot labels."""
        if self._options_item.is_editing:
            return
        self._options_item.setVisible(False)
        self._layout_slots()

    def _labels_centered_on_axis(self) -> bool:
        """Whether annotation labels sit *over* the lead axis instead of beside it.

        Default: labels clear the body on their conventional side.  Resizable
        annotation lines (open) override this so the label is centred on the
        middle of the line, matching where CircuiTikZ places the arrow label.
        """
        return False

    def _doc_styles(self) -> tuple[str, str]:
        """The document's (voltage_style, current_style) — `american`/`european`
        (§7.2). Defaults to american when there is no scene/schematic yet (ghosts,
        thumbnails), matching the codegen default."""
        scene = self.scene()
        sch = getattr(scene, "schematic", None) if scene is not None else None
        v = getattr(sch, "voltage_style", "american") or "american"
        i = getattr(sch, "current_style", "american") or "american"
        return v, i

    def _decoration_mode(self, key: str, centered: bool,
                         v_style: str, i_style: str) -> str:
        """The voltage decoration to draw for a non-current slot key: the american
        ± signs / european arrow for a `v=` slot, nothing otherwise. Drawn for both
        side-placed voltages and the centred `open` annotation (which shows ± at
        its terminals, like CircuiTikZ's ``to[open, v=…]``); the value label stays
        centred on the line either way. Current (`i=`) is handled directly in
        ``_layout_slots`` (its arrow is drawn even for `open`), so it never reaches
        here."""
        if key.startswith("v"):
            return "v_european" if v_style == "european" else "v_american"
        return ""

    def _layout_slots(self) -> None:
        """Render each annotation slot on its conventional side of the body.

        Placement is perpendicular to the component's *on-screen* lead axis, so
        labels land on the correct side regardless of rotation/mirror.  Voltage
        (`v=`) and current (`i=`) slots also get a CircuiTikZ-style decoration —
        ± signs / a voltage arrow / a current arrow (:class:`_AnnotationDecoration`).
        """
        from app.preview.mathrender import _slot_family, slot_fragments, slot_reversed

        slots = [] if self._ghost else slot_fragments(self._component.options)
        while len(self._slot_items) < len(slots):
            self._slot_items.append(_SlotLabel(self))
        while len(self._decoration_items) < len(slots):
            self._decoration_items.append(_AnnotationDecoration(self))

        geom = self._slot_geometry()
        counter = self._label_counter_transform()
        centered = self._labels_centered_on_axis()
        v_style, i_style = self._doc_styles()
        # Per-direction running "outer edge" so stacked labels never overlap: a
        # slot clears the body, but is always pushed out far enough to clear any
        # sibling already placed on the same side (so two `above` labels stack
        # instead of colliding). Current (`i=`) is the exception — it is placed
        # off-centre over the exit lead (see below), so it does not participate.
        outer_edge: dict[tuple[float, float], float] = {}
        for idx, item in enumerate(self._slot_items):
            dec = self._decoration_items[idx]
            if idx >= len(slots):
                item.configure("", geom["left"], geom["center_rel"], 0.0, 0.0, geom["inv"])
                item.setVisible(False)
                dec.configure("", geom["axis_unit"], geom["half_len"],
                              geom["left"], 0.0, geom["center_rel"], geom["inv"])
                continue

            key, latex = slots[idx]
            direction = self._slot_direction(key, geom)
            item.setTransform(counter)

            if _slot_family(key) == "i":
                # CircuiTikZ draws `i=` as an arrowhead on the wire, with the label
                # centred over that head. The head sits on the thin lead (perp
                # offset 0), so the label clears the ARROWHEAD, not the component
                # body (using body half-thickness floated it above the wire). The
                # `<` modifier reverses the direction and moves the arrow to the
                # *entry* lead; for the centred `open` annotation it stays on the
                # midpoint and only flips direction (label always above the head).
                rev = slot_reversed(key)
                sign = -1.0 if rev else 1.0
                axis = geom["axis_unit"]
                # Bodyless parts (short/open) centre the arrow on the midpoint;
                # a component with a body rides the entry/exit lead.
                cur_centered = self._component.kind in _CURRENT_CENTERED_KINDS
                along = 0.0 if cur_centered else sign * _CUR_ARROW_TIP * geom["half_len"]
                cur_center = QPointF(geom["center_rel"].x() + axis.x() * along,
                                     geom["center_rel"].y() + axis.y() * along)
                item.configure(latex, direction, cur_center,
                               _CUR_ARROW_HEAD_W / 2.0 + _LABEL_GAP, 0.0,
                               geom["inv"], False)
                item.setVisible(True)
                dec.configure("current", geom["axis_unit"], geom["half_len"],
                              direction, 0.0, geom["center_rel"], geom["inv"],
                              reversed=rev, centered=cur_centered)
                continue

            mode = self._decoration_mode(key, centered, v_style, i_style)

            dk = (round(direction.x(), 3), round(direction.y(), 3))
            preferred = 0.0 if centered else geom["perp_thickness"] + _LABEL_GAP
            base = max(preferred, outer_edge.get(dk, 0.0))
            # A european voltage arrow reserves a perpendicular band between the
            # body and its text; american ± signs sit at the terminals (no band).
            band = _DEC_BAND if mode == "v_european" else 0.0
            label_base = base + band
            outer_edge[dk] = label_base + _LABEL_LINE_H
            # A european voltage label sits *beside* its curved arrow, even on the
            # otherwise axis-centred `open` annotation (where an american label
            # stays centred on the line between the ± signs).
            label_centered = centered and mode != "v_european"
            item.configure(latex, direction, geom["center_rel"],
                           label_base, 0.0, geom["inv"], label_centered)
            item.setVisible(True)
            if mode == "v_american":
                # `open` has no body, so the ± signs sit just off the line (at its
                # terminals); a real component clears its body's perpendicular edge.
                perp = (_SIGN_OFFSET if centered
                        else geom["perp_thickness"] + _SIGN_OFFSET)
            elif mode == "v_european":
                perp = base + band / 2.0
            else:
                perp = 0.0
            dec.configure(mode, geom["axis_unit"], geom["half_len"],
                          direction, perp, geom["center_rel"], geom["inv"],
                          reversed=slot_reversed(key))

    def _slot_direction(self, key: str, geom: dict) -> QPointF:
        """Screen-space offset direction for a slot, relative to the lead axis.

        Every family (`l`/`v`/`i`/`a`) is placed on its *traversal-relative*
        side: the ``^`` (and default-`l`) form sits left of the lead direction,
        the ``_`` (and default-`v`) form sits right.  Because the preview's
        Y-flip makes the rendered PDF a faithful visual match of the canvas, the
        on-screen left/right side equals the side CircuiTikZ draws the
        annotation on — for both horizontal and rotated elements alike.
        :func:`slot_side` encodes the per-family default (`l` above, `v` below)
        and the ``^``/``_`` overrides.

        Exception: a **voltage source** places its *default* (unsuffixed) ``v``
        label on the opposite side from passives — CircuiTikZ's source voltage
        convention (the ``+`` terminal leads).  The explicit ``v^``/``v_`` forms
        are component-independent, so only the bare ``v`` is flipped.
        """
        from app.preview.mathrender import slot_side

        left, right = geom["left"], geom["right"]
        side = slot_side(key)
        if key == "v" and self._component.kind in _VOLTAGE_SOURCE_KINDS:
            side = "above"
        return left if side == "above" else right

    def _lead_terminals_local(self) -> tuple[QPointF, QPointF]:
        """The component's two lead terminals in local px (origin + endpoint).

        Used to centre slot labels and derive the lead axis.  Resizable
        components (open/short/bipole) override this so the centre tracks the
        *actual* span, not the default registry bbox.
        """
        # A scaled logic gate's visible body shrinks about its origin, so the
        # label-centring terminals scale with it.
        s = self._gate_scale()
        pins = self._defn.pins
        if len(pins) >= 2:
            (x0, y0), (x1, y1) = pins[0].offset, pins[1].offset
            return (
                QPointF(x0 * GRID_PX * s, y0 * GRID_PX * s),
                QPointF(x1 * GRID_PX * s, y1 * GRID_PX * s),
            )
        bx0, by0, bx1, by1 = self._defn.bbox
        c = QPointF((bx0 + bx1) / 2 * GRID_PX * s, (by0 + by1) / 2 * GRID_PX * s)
        return c, QPointF(c.x() + GRID_PX * s, c.y())

    def _slot_geometry(self) -> dict:
        """Screen-space placement basis for the slot labels.

        Returns the component centre (screen-relative to the item origin), unit
        vectors for the left/right sides of the on-screen lead axis, the body's
        half-thickness perpendicular to the leads, and the inverse transform
        mapping screen-relative points back to parent-local.
        """
        t = self.transform()  # rotation + mirror (no translation)
        inv, _ = t.inverted()
        x0, y0, x1, y1 = self._defn.bbox

        # Centre and lead axis from the *actual* terminals (tracks resize).
        p0, p1 = self._lead_terminals_local()
        center_local = QPointF((p0.x() + p1.x()) / 2.0, (p0.y() + p1.y()) / 2.0)
        center_rel = t.map(center_local)
        axis = t.map(QPointF(p1.x() - p0.x(), p1.y() - p0.y()))
        if axis.x() == 0.0 and axis.y() == 0.0:
            axis = QPointF(1.0, 0.0)
        alen = (axis.x() ** 2 + axis.y() ** 2) ** 0.5 or 1.0
        ax, ay = axis.x() / alen, axis.y() / alen
        # Left/right of the traversal direction (screen, y-down).
        left = QPointF(ay, -ax)
        right = QPointF(-ay, ax)

        # Half-thickness perpendicular to the *registry* lead direction — the
        # bbox half-height for horizontal leads, half-width for vertical ones —
        # so resizable/rotated bodies stay tight (the default bbox width is not
        # projected onto the perpendicular).
        s = self._gate_scale()
        hw, hh = (x1 - x0) / 2 * GRID_PX * s, (y1 - y0) / 2 * GRID_PX * s
        pins = self._defn.pins
        vertical_leads = (
            len(pins) >= 2
            and abs(pins[1].offset[1] - pins[0].offset[1])
            > abs(pins[1].offset[0] - pins[0].offset[0])
        )
        perp_thickness = hw if vertical_leads else hh
        return {
            "center_rel": center_rel,
            "left": left,
            "right": right,
            "perp_thickness": perp_thickness,
            "inv": inv,
            # Unit lead axis (first→second pin, screen-space) and half the
            # terminal span — used to place the voltage/current decorations.
            "axis_unit": QPointF(ax, ay),
            "half_len": alen / 2.0,
        }

    def begin_options_edit(self) -> None:
        """Show and activate in-place editing of the full options string.

        The editor shows one option per line and is centred over the component
        body, regardless of where the slot labels sit.
        """
        if self._options_item.is_editing:
            return
        for it in self._slot_items:
            it.setVisible(False)
        self._options_item.setPlainText(self._options_to_editable(self._component.options))
        self._options_item.setTransform(self._label_counter_transform())
        self._options_item.setPos(self._editor_center_pos())
        self._options_item.setVisible(True)
        self._options_item.begin_edit()

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def set_label_interactive(self, interactive: bool) -> None:
        """No-op: labels auto-place on their sides and are not draggable."""
        return

    def set_ghost(self, ghost: bool) -> None:
        self._ghost = ghost
        self._options_item.setVisible(False)
        self._sync_options_item()  # re-lay out (slots hidden while ghost)
        self.update()

    def hoverEnterEvent(self, event):  # noqa: N802
        self._set_hovered(True)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self._set_hovered(False)
        super().hoverLeaveEvent(event)

    def _set_hovered(self, hovered: bool) -> None:
        """Set hover state and repaint the body and all slot labels together,
        so hovering the component or any of its labels highlights the group."""
        if self._hovered == hovered:
            return
        self._hovered = hovered
        self.update()
        for it in self._slot_items:
            it.update()

    # ------------------------------------------------------------------
    # Color selection
    # ------------------------------------------------------------------

    def _label_color(self) -> QColor:
        """Colour for the per-side slot labels: hover-highlight with the body."""
        if self._ghost:
            return QColor(style.COLOR_GHOST)
        if self._hovered:
            return QColor(style.COLOR_HOVER)
        return QColor(style.COLOR_NORMAL)

    def _body_color(self) -> str:
        if self._ghost:
            return style.COLOR_GHOST
        if self.isSelected():
            return style.COLOR_SELECTED
        if self._hovered:
            return style.COLOR_HOVER
        return style.COLOR_NORMAL

    def _pin_pen(self) -> QPen:
        if self._ghost:
            return _pen(style.COLOR_GHOST, 1.0)
        return _pen(style.COLOR_PIN, 1.0)

    def _pin_brush(self) -> QBrush:
        if self._ghost:
            return QBrush(QColor(style.COLOR_GHOST))
        return QBrush(QColor(style.COLOR_PIN))

    # ------------------------------------------------------------------
    # Vector-math label preview (shared by inline-label items)
    # ------------------------------------------------------------------
    #
    # Items that draw their label *inline* (TextNodeItem, BipoleItem) render the
    # label's LaTeX to a QPainterPath and paint it instead of raw text once it is
    # ready.  Defaults live on the class so they exist during __init__, which
    # calls _sync_options_item before any subclass body runs.

    _vec_path: QPainterPath | None = None
    _vec_fragment: str | None = None

    def _vec_scale(self) -> float:
        """pt -> px factor for this item's font size against the template size."""
        return self._component.font_size * _VEC_SCALE / _VEC_TEMPLATE_PT

    def _request_vector(self, fragment: str) -> None:
        """Request (async) a vector render of *fragment* for inline painting."""
        fragment = (fragment or "").strip()
        if fragment == (self._vec_fragment or ""):
            return
        self._vec_fragment = fragment or None
        if not fragment:
            self.prepareGeometryChange()
            self._vec_path = None
            self.update()
            return

        from app.preview.mathrender import render_async

        def _on_done(path, frag=fragment):  # noqa: ANN001
            # Queued delivery: guard against the item being deleted meanwhile.
            if not isValid(self) or frag != (self._vec_fragment or ""):
                return
            self.prepareGeometryChange()
            self._vec_path = path
            self.update()

        render_async(fragment, _on_done)

    def retypeset(self) -> None:
        """Re-render the inline label with the active math engine (e.g. after the
        ziamath debug preference changed).  No-op when nothing is shown."""
        frag = self._vec_fragment
        if frag:
            self._vec_fragment = None  # defeat _request_vector's same-fragment guard
            self._request_vector(frag)

    # ------------------------------------------------------------------
    # QGraphicsItem interface
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        x0, y0, x1, y1 = self._instance_bbox()
        s = self._gate_scale()
        if s != 1.0:
            # Scaled gate: the body shrinks about the origin, but the scaled pins
            # may sit just outside the scaled body — include them so nothing is
            # clipped.
            from app.components import library
            x0, y0, x1, y1 = x0 * s, y0 * s, x1 * s, y1 * s
            gate = library.gate_layout(self._component)
            if gate is not None:
                xs = [g["pin_offset"][0] for g in gate] + [x0, x1]
                ys = [g["pin_offset"][1] for g in gate] + [y0, y1]
                x0, x1 = min(xs), max(xs)
                y0, y1 = min(ys), max(ys)
        margin = LINE_W_THICK * max(1.0, self._component.line_width / 0.4)
        return QRectF(
            x0 * GRID_PX - margin,
            y0 * GRID_PX - margin,
            (x1 - x0) * GRID_PX + 2 * margin,
            (y1 - y0) * GRID_PX + 2 * margin,
        )

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = self._body_color()
        from app.components import library

        # Per-component stroke width scales the canvas pen relative to the
        # CircuiTikZ default (0.4 pt), mirroring the emitted `line width=` option.
        lw_scale = self._component.line_width / 0.4
        s = self._gate_scale()
        gate = library.gate_layout(self._component)

        # --- symbol body: stroke/fill each SVG-derived path ---------------
        # A scaled logic gate draws its body shrunk about the origin (out pin);
        # its pins sit at the true scaled anchor (no lead stubs — a wire connects
        # there directly via the magnet).
        painter.save()
        if s != 1.0:
            painter.scale(s, s)
        for sym in symbol_paths(self._geometry_kind()):
            lw = (LINE_W_THICK if is_thick(sym.stroke_width) else LINE_W) * lw_scale
            # Counter the body scale so the stroke keeps its on-screen weight.
            pen = _pen(color, lw / s)
            painter.setPen(pen)
            if sym.filled:
                painter.setBrush(QBrush(QColor(color)))
            else:
                painter.setBrush(Qt.NoBrush)
            painter.drawPath(sym.path)
        painter.restore()

        # --- transformer polarity dots -----------------------------------
        # A checked dot variant (§5.4) draws a filled circle (CircuiTikZ ``circ``)
        # at its inner-dot anchor; the offsets are in GU at the symbol's scale.
        marks = library.dot_marks(self._component)
        if marks:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(color)))
            for m in marks:
                dx, dy = m["offset"]
                painter.drawEllipse(QPointF(dx * GRID_PX * s, dy * GRID_PX * s),
                                    PIN_R, PIN_R)
            painter.setPen(Qt.NoPen)

        # --- pin indicator dots ------------------------------------------
        if not self._ghost:
            painter.setPen(self._pin_pen())
            painter.setBrush(self._pin_brush())
            # Use the snapped grid pins for a scaled gate, else the base offsets.
            if gate is not None:
                pin_offsets = [g["pin_offset"] for g in gate]
            else:
                pin_offsets = [pdef.offset for pdef in self._resolved_pins()]
            for dx, dy in pin_offsets:
                painter.drawEllipse(
                    QPointF(dx * GRID_PX, dy * GRID_PX), PIN_R, PIN_R
                )


# ---------------------------------------------------------------------------
# Passives, diodes, amplifiers, sources, BJTs, grounds, and rails need no
# special item behaviour — the base ``ComponentItem`` paints any kind from its
# geometry, and ``ITEM_CLASSES.get(kind, ComponentItem)`` falls back to it.  Only
# kinds that override behaviour (MOSFETs, resizable annotations, drawing
# primitives) get a dedicated subclass below and an explicit ``ITEM_CLASSES`` row.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# MOSFET (extends boundingRect when the body_diode variant is active)
# ---------------------------------------------------------------------------

# Extra x1 extent (GU) added to the bounding rect when body_diode is enabled.
# The body diode symbol adds ~11 pt = 0.39 GU; rounded up to 0.45 GU for margin.
_BODYDIODE_EXTRA_X = 0.45


class _MosfetItem(ComponentItem):
    """Base for MOSFET items — extends boundingRect when body_diode is active."""

    def boundingRect(self) -> QRectF:
        x0, y0, x1, y1 = self._defn.bbox
        if self.component.variants.get("body_diode"):
            x1 = x1 + _BODYDIODE_EXTRA_X
        margin = LINE_W_THICK
        return QRectF(
            x0 * GRID_PX - margin,
            y0 * GRID_PX - margin,
            (x1 - x0) * GRID_PX + 2 * margin,
            (y1 - y0) * GRID_PX + 2 * margin,
        )


# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

#: Size of the square endpoint drag handle in pixels (half-side).
_HANDLE_HALF = 5.0


class _ResizableTwoTerminalItem(ComponentItem):
    """Base for resizable two-terminal components (open, short).

    Subclasses inherit span tracking, resize handle rendering, and live-drag
    preview.  They only need to implement :meth:`_draw_body`.
    """

    def _effective_span(self) -> tuple[float, float]:
        so = self._component.span_override
        return so if so is not None else self._defn.default_span

    def _endpoint_px(self) -> QPointF:
        dx, dy = self._effective_span()
        return QPointF(dx * GRID_PX, dy * GRID_PX)

    def _lead_terminals_local(self) -> tuple[QPointF, QPointF]:
        # Centre slot labels on the *actual* span, not the default registry bbox.
        return QPointF(0.0, 0.0), self._endpoint_px()

    def boundingRect(self) -> QRectF:
        ep = self._endpoint_px()
        x0, y0 = min(0.0, ep.x()), min(0.0, ep.y())
        x1, y1 = max(0.0, ep.x()), max(0.0, ep.y())
        m = _HANDLE_HALF + LINE_W_THICK
        return QRectF(x0 - m, y0 - m, (x1 - x0) + 2 * m, (y1 - y0) + 2 * m)

    def shape(self) -> QPainterPath:
        ep = self._endpoint_px()
        stroker = QPainterPathStroker()
        stroker.setWidth(8.0)
        line = QPainterPath()
        line.moveTo(QPointF(0.0, 0.0))
        line.lineTo(ep)
        path = stroker.createStroke(line)
        h = QPainterPath()
        h.addRect(ep.x() - _HANDLE_HALF - 2, ep.y() - _HANDLE_HALF - 2,
                  (_HANDLE_HALF + 2) * 2, (_HANDLE_HALF + 2) * 2)
        return path.united(h)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = self._body_color()
        ep = self._endpoint_px()
        self._draw_body(painter, color, ep)
        if not self._ghost:
            painter.setPen(self._pin_pen())
            painter.setBrush(self._pin_brush())
            for pt in (QPointF(0.0, 0.0), ep):
                painter.drawEllipse(pt, PIN_R, PIN_R)
        if self.isSelected() and not self._ghost:
            painter.setPen(_pen(style.COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor(style.COLOR_SELECTED)))
            handles = [ep]
            if self._origin_draggable():
                handles.append(QPointF(0.0, 0.0))
            for h in handles:
                painter.drawRect(
                    h.x() - _HANDLE_HALF, h.y() - _HANDLE_HALF,
                    _HANDLE_HALF * 2, _HANDLE_HALF * 2,
                )

    def _draw_body(self, painter: QPainter, color: str, ep: QPointF) -> None:
        raise NotImplementedError

    def _origin_draggable(self) -> bool:
        """Whether the *origin* endpoint can be dragged independently.

        True for line annotations (open / short / bipole), whose two endpoints
        are symmetric — either may be grabbed and moved while the other stays
        put. Boxes (rect / circle) resize as an anchored scale about their fixed
        corner and expose only the terminal handle, so they override to False.
        """
        return True

    def set_preview_span(self, span: tuple[float, float]) -> None:
        import dataclasses
        self._component = dataclasses.replace(self._component, span_override=span)
        self.prepareGeometryChange()
        self.update()

    def terminal_handle_hit(self, local_pt: QPointF) -> bool:
        ep = self._endpoint_px()
        return (abs(local_pt.x() - ep.x()) <= _HANDLE_HALF + 2 and
                abs(local_pt.y() - ep.y()) <= _HANDLE_HALF + 2)

    def endpoint_handle_index_at(self, local_pt: QPointF) -> int | None:
        """Return which resize handle *local_pt* is over: 1 = terminal, 0 = origin
        (only when :meth:`_origin_draggable`), or None. The terminal wins a tie at
        a zero-length annotation so a fresh annotation can still be stretched out."""
        if self.terminal_handle_hit(local_pt):
            return 1
        if self._origin_draggable() and (
            abs(local_pt.x()) <= _HANDLE_HALF + 2
            and abs(local_pt.y()) <= _HANDLE_HALF + 2
        ):
            return 0
        return None


class OpenItem(_ResizableTwoTerminalItem):
    """Voltage annotation — translucent line between two resizable endpoints.

    Drawn as a mostly-opaque (translucent) solid line rather than dashed so it
    is not confused with a dashed wire; the annotation label is centred over
    the middle of the line (see :meth:`_labels_centered_on_axis`) to mirror the
    LaTeX output, where the voltage/current arrow sits across the element.
    """

    def _draw_body(self, painter: QPainter, color: str, ep: QPointF) -> None:
        painter.save()
        painter.setOpacity(OPEN_ANNOTATION_OPACITY)
        painter.setPen(_pen(color, LINE_W))
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(0.0, 0.0), ep)
        painter.restore()

    def _labels_centered_on_axis(self) -> bool:
        return True


class ShortItem(_ResizableTwoTerminalItem):
    """Current annotation — solid line between two resizable endpoints."""

    def _draw_body(self, painter: QPainter, color: str, ep: QPointF) -> None:
        painter.setPen(_pen(color, LINE_W))
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(0.0, 0.0), ep)


# ---------------------------------------------------------------------------
# Nodes (single-terminal ground symbols)
# ---------------------------------------------------------------------------

# Grounds and power rails (single-terminal nodes) need no special behaviour —
# their boundingRect is exactly the base ``ComponentItem``'s — so they fall back
# to it via ``ITEM_CLASSES.get(kind, ComponentItem)``.


# ---------------------------------------------------------------------------
# Wire item
# ---------------------------------------------------------------------------

#: Clearance (px) between a wire endpoint and the near edge of its label, so the
#: label clears the wire end / arrow tip. Mirrors codegen's ``_WIRE_LABEL_GAP``.
_WIRE_LABEL_GAP_PX = 6.0


class _WireEndLabel(QGraphicsItem):
    """Async-rendered text/math label sitting just beyond a wire endpoint.

    A child of :class:`WireItem`. Mirrors :class:`_SlotLabel`: it shows nothing
    until its vector render lands, then places its (baseline-normalised) path so
    the near edge clears the endpoint by ``_WIRE_LABEL_GAP_PX`` along the wire's
    outward direction.  All coordinates are parent-local pixels.
    """

    def __init__(self, parent: "WireItem", end: str = "end") -> None:
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.IBeamCursor)     # hint: double-click to edit
        self.end = end                     # "start" or "end" (which wire endpoint)
        self._path: QPainterPath | None = None
        self._fragment: str | None = None
        self._center = QPointF(0.0, 0.0)   # endpoint, parent-local px
        self._out = QPointF(1.0, 0.0)      # unit outward direction (canvas)
        self._placement = ""               # "" = off-end, "above", "below"

    def retypeset(self) -> None:
        """Re-render with the active math engine (see _reissue_vector_render)."""
        _reissue_vector_render(self)

    def configure(
        self, fragment: str, center: QPointF, out: QPointF, placement: str = ""
    ) -> None:
        """Set the label text, its endpoint, the outward direction, and placement.

        *placement* is ``""`` (off the end, along *out*), ``"above"``, or
        ``"below"`` — the latter two centre the label over/under the endpoint.
        """
        self._center = center
        self._out = out
        self._placement = placement or ""
        if fragment != (self._fragment or ""):
            self._fragment = fragment or None
            if not fragment:
                self.prepareGeometryChange()
                self._path = None
            else:
                from app.preview.mathrender import render_async

                def _done(path, frag=fragment):  # noqa: ANN001
                    if not isValid(self) or frag != (self._fragment or ""):
                        return
                    self.prepareGeometryChange()
                    self._path = path
                    self._reposition()
                    self.update()

                render_async(fragment, _done)
        self._reposition()
        self.update()

    def _reposition(self) -> None:
        if self._path is None:
            return
        r = self._path.boundingRect()
        s = _VEC_SCALE
        w, h = r.width() * s, r.height() * s
        if self._placement in ("above", "below"):
            # Tuck the label beside the wire at the endpoint, extending inward
            # (back along the terminal segment), so it never crosses the endpoint
            # into a connected rect/circle. Mirrors codegen; canvas Y is down.
            out = self._out
            gap = _WIRE_LABEL_GAP_PX
            if abs(out.x()) >= abs(out.y()):
                # Horizontal segment: inward along x; side is up/down.
                box_x = -1.0 if out.x() >= 0 else 1.0
                box_y = -1.0 if self._placement == "above" else 1.0  # up = -Y
            else:
                # Vertical segment: inward along y; side left/right
                # (above → left, below → right).
                box_x = -1.0 if self._placement == "above" else 1.0
                box_y = -1.0 if out.y() >= 0 else 1.0
            # Anchor the box corner at the endpoint+gap, ink centre half a box away.
            px = self._center.x() + box_x * gap
            py = self._center.y() + box_y * gap
            cx = px + box_x * w / 2.0
            cy = py + box_y * h / 2.0
        else:
            u = self._out
            # Half-extent of the label along the outward direction (it is axis-
            # aligned, so only one of |u.x|, |u.y| is 1).
            half = abs(u.x()) * w / 2.0 + abs(u.y()) * h / 2.0
            dist = _WIRE_LABEL_GAP_PX + half
            cx = self._center.x() + u.x() * dist
            cy = self._center.y() + u.y() * dist
        # Position so the path's ink centre maps to (cx, cy) after painter.scale.
        self.setPos(QPointF(cx - r.center().x() * s, cy - r.center().y() * s))

    def _scaled_rect(self) -> QRectF:
        r = self._path.boundingRect()
        s = _VEC_SCALE
        return QRectF(r.left() * s, r.top() * s, r.width() * s, r.height() * s)

    def boundingRect(self) -> QRectF:  # noqa: N802
        if self._path is None:
            return QRectF()
        return self._scaled_rect().adjusted(-1.0, -1.0, 1.0, 1.0)

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001, N802
        if self._path is None:
            return
        parent = self.parentItem()
        color = parent.label_color() if hasattr(parent, "label_color") else QColor(style.COLOR_NORMAL)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.save()
        painter.scale(_VEC_SCALE, _VEC_SCALE)
        painter.fillPath(self._path, QBrush(color))
        painter.restore()


class _WireMidLabel(QGraphicsItem):
    """Async-rendered text/math label centred *over* a wire, with an opaque
    backdrop so the line does not run through it.

    A child of :class:`WireItem`. The drag along the wire is driven by the scene
    (see ``_mid_label_drag``); double-clicking it edits in place. Coordinates are
    parent-local pixels. A non-None ``_preview_center`` (set during a drag) is
    painted instead of the committed centre.
    """

    def __init__(self, parent: "WireItem") -> None:
        super().__init__(parent)
        self.setAcceptedMouseButtons(Qt.NoButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.SizeAllCursor)   # hint: drag along the wire
        self._path: QPainterPath | None = None
        self._fragment: str | None = None
        self._center = QPointF(0.0, 0.0)        # committed centre, parent-local px
        self._preview_center: QPointF | None = None

    def retypeset(self) -> None:
        """Re-render with the active math engine (see _reissue_vector_render)."""
        _reissue_vector_render(self)

    def configure(self, fragment: str, center: QPointF) -> None:
        self._center = center
        if fragment != (self._fragment or ""):
            self._fragment = fragment or None
            if not fragment:
                self.prepareGeometryChange()
                self._path = None
            else:
                from app.preview.mathrender import render_async

                def _done(path, frag=fragment):  # noqa: ANN001
                    if not isValid(self) or frag != (self._fragment or ""):
                        return
                    self.prepareGeometryChange()
                    self._path = path
                    self._reposition()
                    self.update()

                render_async(fragment, _done)
        self._reposition()
        self.update()

    def set_preview_center(self, center: QPointF) -> None:
        self._preview_center = center
        self._reposition()
        self.update()

    def clear_preview_center(self) -> None:
        self._preview_center = None
        self._reposition()
        self.update()

    def _reposition(self) -> None:
        if self._path is None:
            return
        r = self._path.boundingRect()
        s = _VEC_SCALE
        c = self._preview_center if self._preview_center is not None else self._center
        self.setPos(c.x() - r.center().x() * s, c.y() - r.center().y() * s)

    def _scaled_rect(self) -> QRectF:
        r = self._path.boundingRect()
        s = _VEC_SCALE
        return QRectF(r.left() * s, r.top() * s, r.width() * s, r.height() * s)

    def boundingRect(self) -> QRectF:  # noqa: N802
        if self._path is None:
            return QRectF()
        return self._scaled_rect().adjusted(
            -_LABEL_BG_PAD, -_LABEL_BG_PAD, _LABEL_BG_PAD, _LABEL_BG_PAD
        )

    def paint(self, painter, option, widget=None) -> None:  # noqa: ANN001, N802
        if self._path is None:
            return
        parent = self.parentItem()
        color = parent.label_color() if hasattr(parent, "label_color") else QColor(style.COLOR_NORMAL)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # Opaque backdrop so the wire doesn't run through the text.
        painter.fillRect(
            self._scaled_rect().adjusted(
                -_LABEL_BG_PAD, -_LABEL_BG_PAD, _LABEL_BG_PAD, _LABEL_BG_PAD
            ),
            QColor(style.COLOR_LABEL_BG),
        )
        painter.save()
        painter.scale(_VEC_SCALE, _VEC_SCALE)
        painter.fillPath(self._path, QBrush(color))
        painter.restore()


#: Half-length (px) of each arm of the red ✕ drawn for a degenerate (single-point)
#: wire — see WireItem._paint_degenerate.
_DEGEN_X_R = 7.0


class WireItem(QGraphicsItem):
    """A polyline wire drawn as a Manhattan path.

    Points are stored in *schematic grid units*; paint() converts to pixels.

    A **degenerate** wire — one with fewer than two points (no segment to draw) —
    should never be created by the editor (the move/split/vertex commands all drop
    or refuse a wire that collapses to a point). As a safety net for an old or
    hand-edited file that still contains one, a single-point wire is drawn as a red
    ✕ marker that is selectable and deletable, so the user can find and remove it.
    """

    def __init__(self, wire, parent: QGraphicsItem | None = None):  # noqa: ANN001
        super().__init__(parent)
        self.wire = wire
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self.setZValue(wire.z_order)             # layer per z_order (spec §6.4)
        self._hovered = False
        # Indices of vertices that are NOT draggable (endpoints on a pin). The
        # scene updates this on rebuild so the handles match the live model.
        self.locked_indices: set[int] = set()
        # Line-hops this wire owns (bumps where it arcs over a crossing wire).
        # The scene recomputes and assigns this on every rebuild, like
        # locked_indices; empty when line-hops are disabled or none apply.
        self.hops: list = []
        # Live drag preview: when set, painted instead of self.wire.points.
        self._preview_points: list[tuple[float, float]] | None = None
        # Text/math labels at each end (children); configured by refresh_labels.
        self._start_label_item = _WireEndLabel(self, end="start")
        self._end_label_item = _WireEndLabel(self, end="end")
        # Mid-wire label drawn over the line with a solid backdrop.
        self._mid_label_item = _WireMidLabel(self)
        # Shared in-place editor for the labels (hidden unless editing), mirroring
        # ComponentItem/TextNodeItem's options editor.
        self._label_editor = LabelTextItem(self)
        self._label_editor.setFlag(QGraphicsItem.ItemIsMovable, False)
        self._label_editor.set_commit_callback(self._on_label_commit)
        self._label_editor.set_end_callback(self._on_label_edit_end)
        self._label_editor.setVisible(False)
        self._editing_end: str | None = None
        self.refresh_labels()

    # -- drag preview -----------------------------------------------------

    def set_preview_points(self, points: list[tuple[float, float]]) -> None:
        self.prepareGeometryChange()
        self._preview_points = list(points)
        self.refresh_labels()
        self.update()

    def clear_preview_points(self) -> None:
        self.prepareGeometryChange()
        self._preview_points = None
        self.refresh_labels()
        self.update()

    @property
    def preview_points(self) -> list[tuple[float, float]] | None:
        """The current preview point list, or None if no preview is active."""
        return self._preview_points

    def _draw_points(self) -> list[tuple[float, float]]:
        return self._preview_points if self._preview_points is not None else self.wire.points

    # -- endpoint labels --------------------------------------------------

    def label_color(self) -> QColor:
        """Current paint colour for endpoint labels (follows selection/hover)."""
        if self.isSelected():
            return QColor(style.COLOR_SELECTED)
        if self._hovered:
            return QColor(style.COLOR_HOVER)
        return QColor(style.COLOR_NORMAL)

    @staticmethod
    def _outward(tip: tuple[float, float], neighbour: tuple[float, float]) -> QPointF:
        """Unit direction (canvas px space) pointing outward from *neighbour*."""
        dx = tip[0] - neighbour[0]
        dy = tip[1] - neighbour[1]
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return QPointF(1.0, 0.0)
        return QPointF(dx / length, dy / length)

    def refresh_labels(self) -> None:
        """Re-bind the start/end/mid label children to the wire's geometry."""
        pts = self._draw_points()
        start_text = getattr(self.wire, "start_label", "")
        end_text = getattr(self.wire, "end_label", "")
        mid_text = getattr(self.wire, "mid_label", "")
        if len(pts) < 2:
            self._start_label_item.configure("", QPointF(), QPointF(1.0, 0.0))
            self._end_label_item.configure("", QPointF(), QPointF(1.0, 0.0))
            self._mid_label_item.configure("", QPointF())
            return
        start_nb = next((p for p in pts[1:] if p != pts[0]), pts[-1])
        end_nb = next((p for p in reversed(pts[:-1]) if p != pts[-1]), pts[0])
        self._start_label_item.configure(
            start_text,
            QPointF(pts[0][0] * GRID_PX, pts[0][1] * GRID_PX),
            self._outward(pts[0], start_nb),
            getattr(self.wire, "start_label_placement", ""),
        )
        self._end_label_item.configure(
            end_text,
            QPointF(pts[-1][0] * GRID_PX, pts[-1][1] * GRID_PX),
            self._outward(pts[-1], end_nb),
            getattr(self.wire, "end_label_placement", ""),
        )
        self._mid_label_item.configure(mid_text, self._mid_center_px(pts))

    def _mid_center_px(self, pts: list[tuple[float, float]]) -> QPointF:
        from app.schematic.model import wire_point_at_fraction
        mx, my = wire_point_at_fraction(pts, getattr(self.wire, "mid_label_pos", 0.5))
        return QPointF(mx * GRID_PX, my * GRID_PX)

    def preview_mid_label(self, frac: float) -> None:
        """Live-preview the mid-label at fractional position *frac* (during drag)."""
        pts = self._draw_points()
        if len(pts) < 2:
            return
        from app.schematic.model import wire_point_at_fraction
        mx, my = wire_point_at_fraction(pts, frac)
        self._mid_label_item.set_preview_center(QPointF(mx * GRID_PX, my * GRID_PX))

    def clear_mid_label_preview(self) -> None:
        self._mid_label_item.clear_preview_center()

    def itemChange(self, change, value):  # noqa: ANN001, N802
        if change == QGraphicsItem.ItemSelectedChange:
            self._start_label_item.update()
            self._end_label_item.update()
            self._mid_label_item.update()
        return super().itemChange(change, value)

    # -- inline label editing ---------------------------------------------

    def begin_label_edit(self, which: str) -> None:
        """Start in-place editing of the *which* label ("start"/"end"/"mid").

        Shows the raw LaTeX fragment in a text editor positioned at the label,
        mirroring how a component's rendered label opens its options editor.
        """
        pts = self._draw_points()
        if len(pts) < 2:
            return
        ed = self._label_editor
        ed.setTransform(QTransform())

        if which == "mid":
            text = getattr(self.wire, "mid_label", "")
            self._mid_label_item.setVisible(False)
            ed.setPlainText(text)
            er = ed.boundingRect()
            center = self._mid_center_px(pts)
            ed.setPos(center.x() - er.width() / 2.0, center.y() - er.height() / 2.0)
        else:
            if which == "start":
                tip = pts[0]
                neighbour = next((p for p in pts[1:] if p != pts[0]), pts[-1])
                text = getattr(self.wire, "start_label", "")
                self._start_label_item.setVisible(False)
            else:
                tip = pts[-1]
                neighbour = next((p for p in reversed(pts[:-1]) if p != pts[-1]), pts[0])
                text = getattr(self.wire, "end_label", "")
                self._end_label_item.setVisible(False)
            out = self._outward(tip, neighbour)
            ed.setPlainText(text)
            er = ed.boundingRect()
            tip_px = QPointF(tip[0] * GRID_PX, tip[1] * GRID_PX)
            x = tip_px.x() + out.x() * _WIRE_LABEL_GAP_PX
            y = tip_px.y() + out.y() * _WIRE_LABEL_GAP_PX
            # The editor box grows right/down from its top-left, so shift it so the
            # edge facing the wire sits at the gap, and centre it across the wire.
            if out.x() < 0:
                x -= er.width()
            if out.y() < 0:
                y -= er.height()
            if abs(out.x()) >= abs(out.y()):
                y -= er.height() / 2.0
            else:
                x -= er.width() / 2.0
            ed.setPos(x, y)

        self._editing_end = which
        ed.setVisible(True)
        ed.begin_edit()

    def _on_label_commit(self, text: str) -> None:
        scene = self.scene()
        if scene is None or self._editing_end is None:
            return
        if self._editing_end == "start" and hasattr(scene, "set_wire_start_label"):
            scene.set_wire_start_label(self.wire.id, text)
        elif self._editing_end == "end" and hasattr(scene, "set_wire_end_label"):
            scene.set_wire_end_label(self.wire.id, text)
        elif self._editing_end == "mid" and hasattr(scene, "set_wire_mid_label"):
            scene.set_wire_mid_label(self.wire.id, text)

    def _on_label_edit_end(self) -> None:
        # Fired on commit and cancel; restore the display labels and re-bind.
        self._editing_end = None
        self._label_editor.setVisible(False)
        self._start_label_item.setVisible(True)
        self._end_label_item.setVisible(True)
        self._mid_label_item.setVisible(True)
        self.refresh_labels()

    # -- style ------------------------------------------------------------

    def _line_width_px(self) -> float:
        """Pen width in px, proportional to ``line_width`` (0.4 pt -> LINE_W)."""
        return LINE_W * (getattr(self.wire, "line_width", 0.4) / 0.4)

    def _line_pen_style(self) -> Qt.PenStyle:
        return _resolve_pen_style(getattr(self.wire, "line_style", ""))

    #: Endpoint-marker geometry (px): length back from the tip and half the base
    #: width. Mirrors the relative proportions of the exported arrows.meta tips.
    ARROW_LEN: float = 9.0
    ARROW_HALF_W: float = 4.0

    def _draw_markers(self, painter: QPainter, pts: list[QPointF], color: QColor) -> None:
        """Paint custom endpoint markers for the wire, one per marked end.

        The on-canvas glyphs approximate the exported ``arrows.meta`` tips:
        ``arrow`` → filled triangle (Latex), ``stealth`` → concave filled
        (Stealth), ``open`` → outlined triangle (Latex[open]), ``bar`` → a
        perpendicular terminal bar (Bar).
        """
        if len(pts) < 2:
            return
        ends = (
            (getattr(self.wire, "start_marker", ""), pts[0], pts[0] - pts[1]),
            (getattr(self.wire, "end_marker", ""), pts[-1], pts[-1] - pts[-2]),
        )
        for marker, tip, outward in ends:
            if marker:
                self._paint_marker(painter, marker, tip, outward, color)

    def _paint_marker(
        self, painter: QPainter, kind: str, tip: QPointF, outward: QPointF, color: QColor
    ) -> None:
        """Paint a single endpoint marker of *kind* at *tip*, aimed along *outward*."""
        length = math.hypot(outward.x(), outward.y())
        if length < 1e-9:
            return
        ux, uy = outward.x() / length, outward.y() / length  # unit, outward
        px, py = -uy, ux  # unit perpendicular
        L, W = self.ARROW_LEN, self.ARROW_HALF_W
        base = QPointF(tip.x() - ux * L, tip.y() - uy * L)
        left = QPointF(base.x() + px * W, base.y() + py * W)
        right = QPointF(base.x() - px * W, base.y() - py * W)

        if kind == "bar":
            # A perpendicular terminal bar centred on the endpoint.
            a = QPointF(tip.x() + px * W, tip.y() + py * W)
            b = QPointF(tip.x() - px * W, tip.y() - py * W)
            painter.setPen(_pen(color, self._line_width_px()))
            painter.drawLine(a, b)
            return

        if kind == "open":
            # Outlined triangle (no fill).
            painter.setPen(_pen(color, self._line_width_px()))
            painter.setBrush(Qt.NoBrush)
            painter.drawPolygon(QPolygonF([tip, left, right]))
            return

        painter.setPen(_pen(color, 1.0))
        painter.setBrush(QBrush(color))
        if kind == "stealth":
            # Filled triangle with a notch in its back edge, recessed toward the
            # tip — the sharper Stealth silhouette.
            notch = QPointF(tip.x() - ux * (L * 0.55), tip.y() - uy * (L * 0.55))
            painter.drawPolygon(QPolygonF([tip, left, notch, right]))
            return

        # Default ("arrow"): filled triangle (Latex).
        painter.drawPolygon(QPolygonF([tip, left, right]))

    # -- events -----------------------------------------------------------

    def hoverEnterEvent(self, event):  # noqa: N802
        self._hovered = True
        self.update()
        self._start_label_item.update()
        self._end_label_item.update()
        self._mid_label_item.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self.update()
        self._start_label_item.update()
        self._end_label_item.update()
        self._mid_label_item.update()
        super().hoverLeaveEvent(event)

    def boundingRect(self) -> QRectF:
        pts = self._draw_points()
        if not pts:
            return QRectF()
        if len(pts) == 1:  # degenerate: a red ✕ marker around the lone point
            cx, cy = pts[0][0] * GRID_PX, pts[0][1] * GRID_PX
            m = _DEGEN_X_R + self.HIT_TOL + 2.0
            return QRectF(cx - m, cy - m, 2 * m, 2 * m)
        xs = [p[0] * GRID_PX for p in pts]
        ys = [p[1] * GRID_PX for p in pts]
        # +HOP_R so a hop bump (which bulges perpendicular to the wire) is never
        # clipped from the repaint region.
        margin = max(LINE_W, self._line_width_px()) + max(PIN_R, self.ARROW_LEN) + 3 + HOP_R
        return QRectF(
            min(xs) - margin,
            min(ys) - margin,
            max(xs) - min(xs) + 2 * margin,
            max(ys) - min(ys) + 2 * margin,
        )

    def _build_wire_path(self, pts: list[QPointF]) -> QPainterPath:
        """Polyline through *pts* (px), with a semicircular bump at each hop.

        Shared by paint() and shape() so the clickable band follows the bumps.
        Hops are applied to whatever geometry is being drawn (committed *or*
        live preview): each hop is matched to its segment by coordinate, so a
        stale hop that no longer lies on the polyline is simply skipped — the
        scene keeps ``self.hops`` in sync with the preview during a drag.
        """
        return _polyline_with_hops(pts, self.hops)

    #: Half-width (px) of the clickable band around each wire segment.
    HIT_TOL: float = 6.0

    def shape(self) -> QPainterPath:
        """Clickable region = a thin band along the segments (not the bbox).

        QGraphicsItem's default hit area is ``boundingRect``, which for a wire
        is the whole rectangle spanning its endpoints — that overlaps nearby
        components and steals their clicks. Returning a stroked path along the
        actual polyline (plus the draggable vertex handles) makes the wire
        selectable only near the line itself.
        """
        pts_gu = self._draw_points()
        if len(pts_gu) == 1:  # degenerate: clickable disc around the ✕ marker
            cx, cy = pts_gu[0][0] * GRID_PX, pts_gu[0][1] * GRID_PX
            path = QPainterPath()
            path.addEllipse(QPointF(cx, cy), _DEGEN_X_R + self.HIT_TOL, _DEGEN_X_R + self.HIT_TOL)
            return path
        if len(pts_gu) < 2:
            return QPainterPath()
        pts = [QPointF(x * GRID_PX, y * GRID_PX) for x, y in pts_gu]
        line = self._build_wire_path(pts)

        stroker = QPainterPathStroker()
        stroker.setWidth(self.HIT_TOL * 2.0)
        hit = stroker.createStroke(line)

        # Include the draggable vertex handles so they remain grabbable even if
        # a handle sticks slightly outside the stroked band.
        for i, pt in enumerate(pts):
            if i in self.locked_indices:
                continue
            handle = QPainterPath()
            handle.addEllipse(pt, PIN_R + 1.0, PIN_R + 1.0)
            hit = hit.united(handle)
        return hit

    def _paint_degenerate(self, painter: QPainter, pt_gu: tuple[float, float]) -> None:
        """Draw a red ✕ for a degenerate single-point wire (see the class doc)."""
        painter.setRenderHint(QPainter.Antialiasing, True)
        cx, cy = pt_gu[0] * GRID_PX, pt_gu[1] * GRID_PX
        r = _DEGEN_X_R
        if self.isSelected():
            color = style.COLOR_SELECTED
        elif self._hovered:
            color = style.COLOR_HOVER
        else:
            color = style.COLOR_PIN          # red, theme-aware
        pen = QPen(QColor(color))
        pen.setWidthF(2.0)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawLine(QPointF(cx - r, cy - r), QPointF(cx + r, cy + r))
        painter.drawLine(QPointF(cx - r, cy + r), QPointF(cx + r, cy - r))

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        pts_gu = self._draw_points()
        if len(pts_gu) == 1:
            self._paint_degenerate(painter, pts_gu[0])
            return
        if len(pts_gu) < 2:
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.isSelected():
            color = style.COLOR_SELECTED
        elif self._hovered:
            color = style.COLOR_HOVER
        else:
            color = style.COLOR_NORMAL
        painter.setPen(_pen(color, self._line_width_px(), self._line_pen_style()))
        painter.setBrush(Qt.NoBrush)
        pts = [QPointF(x * GRID_PX, y * GRID_PX) for x, y in pts_gu]
        painter.drawPath(self._build_wire_path(pts))

        # Custom endpoint markers (e.g. arrowheads for block diagrams).
        self._draw_markers(painter, pts, color)

        # Draw draggable vertex handles when the wire is selected or hovered,
        # so the user can see which nodes can be moved. Locked endpoints (on a
        # pin) are not drawn as grab handles.
        if self.isSelected() or self._hovered:
            painter.setPen(_pen(style.COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor(style.COLOR_LABEL_BG)))
            for i, pt in enumerate(pts):
                if i in self.locked_indices:
                    continue
                painter.drawEllipse(pt, PIN_R, PIN_R)


# ---------------------------------------------------------------------------
# Wire preview ghost (WIRE mode)
# ---------------------------------------------------------------------------

class WirePreviewItem(QGraphicsItem):
    """Semi-transparent preview of a wire being routed in WIRE mode.

    Paints, in ghost colour:
      • the already-committed in-progress legs (``self.points``), and
      • the pending leg from the last committed vertex to the cursor.

    A marker is drawn at the snap end: a hollow ring when the cursor is snapped
    to a component pin, or a small filled dot for a bare grid-node anchor.
    Vertex dots mark each committed anchor so multi-leg routes read clearly.

    All coordinates are stored in **schematic grid units** and converted to
    pixels at paint time, matching :class:`WireItem`.
    """

    def __init__(self, parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self.points: list[tuple[float, float]] = []   # committed vertices (GU)
        self.cursor: tuple[float, float] | None = None  # pending endpoint (GU)
        self.cursor_is_pin: bool = False
        # Live line-hops where the in-progress wire crosses existing wires; the
        # scene recomputes these as the cursor moves (set before set_path so the
        # geometry-change covers the bumps).
        self.hops: list = []
        self.setZValue(1000)               # above components, like the place ghost
        self.setAcceptedMouseButtons(Qt.NoButton)  # never steal clicks

    # -- update API -------------------------------------------------------

    def set_path(
        self,
        points: list[tuple[float, float]],
        cursor: tuple[float, float] | None,
        cursor_is_pin: bool = False,
    ) -> None:
        self.prepareGeometryChange()
        self.points = list(points)
        self.cursor = cursor
        self.cursor_is_pin = cursor_is_pin
        self.update()

    # -- geometry ---------------------------------------------------------

    def _all_pts_px(self) -> list[QPointF]:
        pts = list(self.points)
        if self.cursor is not None:
            pts = pts + [self.cursor]
        return [QPointF(x * GRID_PX, y * GRID_PX) for x, y in pts]

    def boundingRect(self) -> QRectF:
        pts = self._all_pts_px()
        if not pts:
            return QRectF()
        xs = [p.x() for p in pts]
        ys = [p.y() for p in pts]
        margin = LINE_W + PIN_R + 4 + HOP_R          # +HOP_R so a bump isn't clipped
        return QRectF(
            min(xs) - margin,
            min(ys) - margin,
            max(xs) - min(xs) + 2 * margin,
            max(ys) - min(ys) + 2 * margin,
        )

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)

        # --- the polyline (committed legs + pending leg) ------------------
        pts = self._all_pts_px()
        if len(pts) >= 2:
            pen = _pen(style.COLOR_GHOST, LINE_W, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(_polyline_with_hops(pts, self.hops))

        # --- committed vertex anchors (small ghost dots) ------------------
        if self.points:
            painter.setPen(_pen(style.COLOR_GHOST, 1.0))
            painter.setBrush(QBrush(QColor(style.COLOR_GHOST)))
            for x, y in self.points:
                painter.drawEllipse(QPointF(x * GRID_PX, y * GRID_PX), PIN_R, PIN_R)

        # --- snap-end marker ---------------------------------------------
        if self.cursor is not None:
            cx, cy = self.cursor[0] * GRID_PX, self.cursor[1] * GRID_PX
            if self.cursor_is_pin:
                # Hollow ring: snapping to a pin.
                painter.setPen(_pen(style.COLOR_SELECTED, LINE_W))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), PIN_R + 2.5, PIN_R + 2.5)
            else:
                # Small filled dot: a bare grid-node anchor.
                painter.setPen(_pen(style.COLOR_GHOST, 1.0))
                painter.setBrush(QBrush(QColor(style.COLOR_GHOST)))
                painter.drawEllipse(QPointF(cx, cy), PIN_R, PIN_R)


# ---------------------------------------------------------------------------
# Junction dot
# ---------------------------------------------------------------------------

class JunctionItem(QGraphicsItem):
    """A solid connection dot drawn where 3+ wires (or a pin + 2 wires) meet.

    Non-interactive (it carries no selection / hit area) and drawn above wires
    so it reads clearly. Position is set by the scene in scene coordinates; the
    dot is painted at the item origin.
    """

    R: float = PIN_R + 1.0   # slightly larger than a pin dot, for visibility
    HOVER_GROW: float = 2.5  # extra radius when hovered (matches JunctionDragItem)

    def __init__(self, parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self.setZValue(50)                       # above wires, below ghosts
        self.setAcceptedMouseButtons(Qt.NoButton)
        # Hover feedback: the dot grows + highlights to signal it's draggable.
        self.setAcceptHoverEvents(True)
        self._hover = False

    def _radius(self) -> float:
        return self.R + (self.HOVER_GROW if self._hover else 0.0)

    def hoverEnterEvent(self, event) -> None:  # noqa: ANN001, N802
        self._hover = True
        self.prepareGeometryChange()
        self.update()

    def hoverLeaveEvent(self, event) -> None:  # noqa: ANN001, N802
        self._hover = False
        self.prepareGeometryChange()
        self.update()

    def boundingRect(self) -> QRectF:
        m = self.R + self.HOVER_GROW + 1.0
        return QRectF(-m, -m, 2 * m, 2 * m)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = style.COLOR_SELECTED if self._hover else style.COLOR_NORMAL
        painter.setPen(_pen(color, 1.0))
        painter.setBrush(QBrush(QColor(color)))
        painter.drawEllipse(QPointF(0.0, 0.0), self._radius(), self._radius())


class JunctionDragItem(QGraphicsItem):
    """A highlighted, enlarged junction dot shown while a junction is being
    dragged — makes it clear the junction (with all its wires) is moving."""

    R: float = JunctionItem.R + 2.5   # noticeably bigger than a resting dot

    def __init__(self, parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self.setZValue(1100)                     # above wires, ghosts, dots
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        m = self.R + 1.0
        return QRectF(-m, -m, 2 * m, 2 * m)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(_pen(style.COLOR_SELECTED, 1.0))
        painter.setBrush(QBrush(QColor(style.COLOR_SELECTED)))
        painter.drawEllipse(QPointF(0.0, 0.0), self.R, self.R)


# ---------------------------------------------------------------------------
# Open-circle node (unconnected wire endpoint)
# ---------------------------------------------------------------------------

class OpenCircleItem(QGraphicsItem):
    """An open circle drawn at a wire endpoint that is not connected to any pin.

    Mirrors the CircuiTikZ \\node[ocirc] marker. Non-interactive (no selection /
    hit area); drawn above wires. Position is set by the scene in scene
    coordinates; the circle is painted at the item origin.
    """

    R: float = PIN_R + 1.0   # same radius as JunctionItem for visual consistency

    def __init__(self, parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self.setZValue(50)                       # above wires, below ghosts
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        m = self.R + 1.0
        return QRectF(-m, -m, 2 * m, 2 * m)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(_pen(style.COLOR_NORMAL, LINE_W))
        painter.setBrush(QColor(style.COLOR_BACKGROUND))
        painter.drawEllipse(QPointF(0.0, 0.0), self.R, self.R)


# ---------------------------------------------------------------------------
# Drawing annotations (non-circuit visual elements)
# ---------------------------------------------------------------------------

class _DrawingAnnotationBase(ComponentItem):
    """Base for drawing annotations (text_node, rect, circle, bipole).

    A thin marker subclass: z_order → Qt z-value is now handled by the base
    :class:`ComponentItem` (every component carries a layer), so this only exists
    to group the drawing-annotation kinds and host their shared style helpers.
    """


_RECT_STYLE_MAP: dict[str, Qt.PenStyle] = {
    "":               Qt.SolidLine,
    "solid":          Qt.SolidLine,
    "dashed":         Qt.DashLine,
    "dotted":         Qt.DotLine,
    "dash dot":       Qt.DashDotLine,
}


def _resolve_pen_style(line_style: str) -> Qt.PenStyle:
    """Map a StyledComponent ``line_style`` token to a Qt pen style.

    Shared by RectItem and BipoleItem so both render the same line styles
    (unknown/empty → solid).
    """
    return _RECT_STYLE_MAP.get(line_style.strip().lower(), Qt.SolidLine)


class TextNodeItem(_DrawingAnnotationBase):
    """Text annotation placed at a point on the canvas.

    ``component.options`` holds the text string; ``component.font_size``
    overrides the font size in points (default 12 pt).  No circuit pins —
    invisible to the connectivity model.
    """

    def _build_font(self) -> QFont:
        from app.components.model import TextNodeComponent
        comp = self._component
        assert isinstance(comp, TextNodeComponent)
        return _fonted_qfont(comp)

    # Text-node content is free text, not key=value options — edit it verbatim
    # (no comma<->newline conversion).
    def _options_to_editable(self, options: str) -> str:
        return options

    def _options_from_editable(self, text: str) -> str:
        return text

    def _sync_options_item(self) -> None:
        # When not editing: text is drawn inline in paint(); hide the label.
        if not self._options_item.is_editing:
            self._options_item.setVisible(False)
        if not self._ghost:
            self._request_vector(self._component.options)

    def begin_options_edit(self) -> None:
        """Activate inline editing of the text content on the canvas body."""
        if self._options_item.is_editing:
            return
        font = self._build_font()
        self._options_item.setFont(font)
        self._options_item.setPlainText(self._component.options)
        # Position at item centre, no counter-rotation (editor rotates with body).
        rect = self.boundingRect()
        self._options_item.setTransform(QTransform())
        # Centre the editor within the bounding rect.
        er = self._options_item.boundingRect()
        self._options_item.setPos(
            rect.center().x() - er.width() / 2,
            rect.center().y() - er.height() / 2,
        )
        self._options_item.setVisible(True)
        self._options_item.begin_edit()

    def boundingRect(self) -> QRectF:
        from app.components.model import TextNodeComponent
        comp = self._component
        assert isinstance(comp, TextNodeComponent)
        if self._vec_path is not None and not self._options_item.is_editing:
            scale = self._vec_scale()
            r = self._vec_path.boundingRect()
            w, h = r.width() * scale, r.height() * scale
            m = 2.0
            return QRectF(-w / 2.0 - m, -h / 2.0 - m, w + 2 * m, h + 2 * m)
        text = comp.options or "T"
        fs_px = max(1, round(comp.font_size * GRID_PX / _PT_PER_GU))
        bold_factor = 1.08 if comp.font_bold else 1.0
        approx_w = max(fs_px * 2.0, len(text) * fs_px * 0.65 * bold_factor)
        h = fs_px * 1.8
        return QRectF(-approx_w / 2.0, -h / 2.0, approx_w, h)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = self._body_color()

        painter.setFont(self._build_font())

        text = self._component.options
        rect = self.boundingRect()

        # Suppress drawn text while the inline editor is active.
        if self._options_item.is_editing:
            painter.setPen(_pen(color, LINE_W, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
        elif text and self._vec_path is not None:
            # Vector math preview, centred on the item origin.  The path is
            # baseline-normalised, so centre its ink bbox (offset by r.center()).
            scale = self._vec_scale()
            r = self._vec_path.boundingRect()
            painter.save()
            painter.scale(scale, scale)
            painter.translate(-r.center().x(), -r.center().y())
            painter.fillPath(self._vec_path, QBrush(QColor(color)))
            painter.restore()
        elif text:
            painter.setPen(_pen(color, LINE_W))
            painter.setBrush(Qt.NoBrush)
            painter.drawText(rect, Qt.AlignCenter, text)
        else:
            # Empty text: draw a dashed placeholder box with "Text" hint.
            painter.setPen(_pen(color, LINE_W, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            if not self._ghost:
                painter.drawText(rect, Qt.AlignCenter, "Text")

        if self.isSelected() and not self._ghost:
            painter.setPen(_pen(style.COLOR_SELECTED, 1.0, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)


def _resolve_tikz_color(tikz: str) -> QColor:
    """Convert a TikZ color string (e.g. ``"yellow!20"``) to a :class:`QColor`.

    Handles plain named colors and the ``color!percent`` mixing-with-white
    syntax used by the rect fill palette.  Falls back to Qt's own parser for
    any string it doesn't recognise.
    """
    _NAMED: dict[str, tuple[int, int, int]] = {
        "white":   (255, 255, 255),
        "black":   (0,   0,   0),
        "gray":    (128, 128, 128),
        "red":     (255, 0,   0),
        "green":   (0,   255, 0),
        "blue":    (0,   0,   255),
        "cyan":    (0,   255, 255),
        "magenta": (255, 0,   255),
        "yellow":  (255, 255, 0),
        "orange":  (255, 165, 0),
    }
    tikz = tikz.strip()
    if "!" in tikz:
        parts = tikz.split("!", 1)
        base_name = parts[0].strip().lower()
        try:
            pct = float(parts[1].strip()) / 100.0
        except ValueError:
            pct = 1.0
        base = _NAMED.get(base_name, (0, 0, 0))
        # Mix base color with white: result = white*(1-pct) + base*pct
        r = int(round(255 * (1 - pct) + base[0] * pct))
        g = int(round(255 * (1 - pct) + base[1] * pct))
        b = int(round(255 * (1 - pct) + base[2] * pct))
        return QColor(r, g, b)
    lower = tikz.lower()
    if lower in _NAMED:
        return QColor(*_NAMED[lower])
    c = QColor(tikz)
    return c if c.isValid() else QColor(Qt.white)


# Connection-point dots drawn around rect/circle perimeters: smaller and more
# muted than a component pin (PIN_R), to read as a subtle "connection rail".
_CONN_DOT_R = PIN_R * 0.55
_CONN_DOT_COLOR = QColor(200, 60, 60, 120)   # translucent red


class RectItem(_DrawingAnnotationBase, _ResizableTwoTerminalItem):
    """Rectangle drawing element.

    ``component.position`` is the first corner; ``component.span_override`` (or
    ``default_span``) gives the offset (dx, dy) to the opposite corner.
    Fill and line style come from the StyledComponent fields (``fill_color``,
    ``line_style``); the outline width is the unified ``Component.line_width``.
    No circuit pins.
    """

    def _origin_draggable(self) -> bool:
        # A box resizes as an anchored scale about its first corner, so only the
        # opposite-corner (terminal) handle is draggable.
        return False

    def _sync_options_item(self) -> None:
        # The centred text label (component.options) is drawn inline in
        # _draw_body(); hide the floating editor child unless it is active.
        if not self._options_item.is_editing:
            self._options_item.setVisible(False)
        if not self._ghost:
            self._request_vector(self._component.options)

    # Rect text is free text, not key=value options — edit it verbatim
    # (no comma<->newline conversion), like TextNodeItem.
    def _options_to_editable(self, options: str) -> str:
        return options

    def _options_from_editable(self, text: str) -> str:
        return text

    def begin_options_edit(self) -> None:
        """Activate inline editing of the centred text, centred in the box."""
        if self._options_item.is_editing:
            return
        self._options_item.setFont(_fonted_qfont(self._component))
        self._options_item.setTransform(QTransform())
        self._options_item.setPlainText(self._component.options)
        ep = self._endpoint_px()
        cx = (min(0.0, ep.x()) + max(0.0, ep.x())) / 2
        cy = (min(0.0, ep.y()) + max(0.0, ep.y())) / 2
        er = self._options_item.boundingRect()
        self._options_item.setPos(cx - er.width() / 2, cy - er.height() / 2)
        self._options_item.setVisible(True)
        self._options_item.begin_edit()

    def boundingRect(self) -> QRectF:
        base = super().boundingRect()
        if self._vec_path is not None and not self._options_item.is_editing:
            ep = self._endpoint_px()
            cx = (min(0.0, ep.x()) + max(0.0, ep.x())) / 2
            cy = (min(0.0, ep.y()) + max(0.0, ep.y())) / 2
            scale = self._vec_scale()
            r = self._vec_path.boundingRect()
            w, h = r.width() * scale, r.height() * scale
            m = 2.0
            text_rect = QRectF(cx - w / 2 - m, cy - h / 2 - m, w + 2 * m, h + 2 * m)
            return base.united(text_rect)
        return base

    def _parse_options(self) -> tuple[Qt.PenStyle, float, str]:
        """Return (pen_style, line_width_px, fill_color_name) from the style fields."""
        comp = self._component
        # Convert pt to pixels: 1 pt ≈ 1.333 px at 96 dpi; keep proportional.
        line_width_px = comp.line_width * 1.333
        return _resolve_pen_style(comp.line_style), line_width_px, comp.fill_color

    def shape(self) -> QPainterPath:
        """Full rectangle area (interior + border) as the hit region.

        The base class returns a stroked path along the diagonal (treating the
        rect like a two-terminal wire), which makes only a narrow strip near the
        centre line selectable.  Override to include the entire rectangle so the
        user can click anywhere inside or on the border.
        """
        ep = self._endpoint_px()
        x0 = min(0.0, ep.x())
        y0 = min(0.0, ep.y())
        x1 = max(0.0, ep.x())
        y1 = max(0.0, ep.y())
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)

        # Stroked border band.
        stroker = QPainterPathStroker()
        stroker.setWidth(8.0)
        border = QPainterPath()
        border.addRect(rect)
        path = stroker.createStroke(border)

        # Include the interior so clicking anywhere inside selects the rect.
        interior = QPainterPath()
        interior.addRect(rect)
        path = path.united(interior)

        # Include the resize handle at the far corner.
        handle = QPainterPath()
        handle.addRect(
            ep.x() - _HANDLE_HALF - 2, ep.y() - _HANDLE_HALF - 2,
            (_HANDLE_HALF + 2) * 2, (_HANDLE_HALF + 2) * 2,
        )
        return path.united(handle)

    def _draw_shape(self, painter: QPainter, color: str, rect: QRectF) -> None:
        """Stroke/fill the outline shape. Overridden by CircleItem (ellipse)."""
        pen_style, line_width_px, fill = self._parse_options()
        painter.setPen(_pen(color, line_width_px, pen_style))
        if fill and not self._ghost:
            painter.setBrush(QBrush(_resolve_tikz_color(fill)))
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

    def _draw_body(self, painter: QPainter, color: str, ep: QPointF) -> None:
        x0 = min(0.0, ep.x())
        y0 = min(0.0, ep.y())
        x1 = max(0.0, ep.x())
        y1 = max(0.0, ep.y())
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)

        self._draw_shape(painter, color, rect)

        # Centred text label (component.options); typeset math when rendered,
        # raw text otherwise.  Suppressed while the inline editor is active.
        text = self._component.options
        if text and not self._ghost and not self._options_item.is_editing:
            if self._vec_path is not None:
                scale = self._vec_scale()
                pr = self._vec_path.boundingRect()
                painter.save()
                painter.translate(rect.center().x(), rect.center().y())
                painter.scale(scale, scale)
                painter.translate(-pr.center().x(), -pr.center().y())
                painter.fillPath(self._vec_path, QBrush(QColor(color)))
                painter.restore()
            else:
                painter.setFont(_fonted_qfont(self._component))
                painter.setPen(_pen(color, LINE_W))
                painter.setBrush(Qt.NoBrush)
                painter.drawText(rect, Qt.AlignCenter, text)

    def _connection_dots_local(self) -> list[QPointF]:
        """Local-px connection points to mark with dots — the full perimeter at
        0.25 GU (overridden by :class:`CircleItem` to the four cardinal points)."""
        ep = self._endpoint_px()
        x0, y0 = min(0.0, ep.x()), min(0.0, ep.y())
        x1, y1 = max(0.0, ep.x()), max(0.0, ep.y())
        step = 0.25 * GRID_PX
        nx = max(1, round((x1 - x0) / step))
        ny = max(1, round((y1 - y0) / step))
        seen: set[tuple[float, float]] = set()
        out: list[QPointF] = []

        def add(x: float, y: float) -> None:
            k = (round(x, 3), round(y, 3))
            if k not in seen:
                seen.add(k)
                out.append(QPointF(x, y))

        for i in range(nx + 1):
            x = x0 + i * step
            add(x, y0)
            add(x, y1)
        for j in range(ny + 1):
            y = y0 + j * step
            add(x0, y)
            add(x1, y)
        return out

    def _draw_connection_dots(self, painter: QPainter) -> None:
        if self._ghost:
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(_CONN_DOT_COLOR))
        for p in self._connection_dots_local():
            painter.drawEllipse(p, _CONN_DOT_R, _CONN_DOT_R)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = self._body_color()
        ep = self._endpoint_px()
        self._draw_body(painter, color, ep)
        # Muted connection-point dots around the perimeter (where wires attach).
        self._draw_connection_dots(painter)
        # Resize handle at the far corner when selected.
        if self.isSelected() and not self._ghost:
            painter.setPen(_pen(style.COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor(style.COLOR_SELECTED)))
            painter.drawRect(
                ep.x() - _HANDLE_HALF, ep.y() - _HANDLE_HALF,
                _HANDLE_HALF * 2, _HANDLE_HALF * 2,
            )


class CircleItem(RectItem):
    """Circle/ellipse drawing element — a :class:`RectItem` that draws an ellipse
    inscribed in the (width, height) bounding box (a circle when square).

    Everything else — centred text, inline editing, fill/border, resize handle,
    boundingRect — is inherited from :class:`RectItem`.  Only the painted outline
    and the selection hit region differ.  Wire connections are restricted to the
    four cardinal points (N/S/E/W) by the model (`circle_connection_points`),
    not by this item.
    """

    def _draw_shape(self, painter: QPainter, color: str, rect: QRectF) -> None:
        pen_style, line_width_px, fill = self._parse_options()
        painter.setPen(_pen(color, line_width_px, pen_style))
        if fill and not self._ghost:
            painter.setBrush(QBrush(_resolve_tikz_color(fill)))
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawEllipse(rect)

    def shape(self) -> QPainterPath:
        """Ellipse interior + resize handle as the hit region."""
        ep = self._endpoint_px()
        x0 = min(0.0, ep.x())
        y0 = min(0.0, ep.y())
        x1 = max(0.0, ep.x())
        y1 = max(0.0, ep.y())
        path = QPainterPath()
        path.addEllipse(QRectF(x0, y0, x1 - x0, y1 - y0))
        handle = QPainterPath()
        handle.addRect(
            ep.x() - _HANDLE_HALF - 2, ep.y() - _HANDLE_HALF - 2,
            (_HANDLE_HALF + 2) * 2, (_HANDLE_HALF + 2) * 2,
        )
        return path.united(handle)

    def _connection_dots_local(self) -> list[QPointF]:
        """Only the four cardinal points (N/S/E/W) — the circle's connections."""
        ep = self._endpoint_px()
        x0, y0 = min(0.0, ep.x()), min(0.0, ep.y())
        x1, y1 = max(0.0, ep.x()), max(0.0, ep.y())
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        return [
            QPointF(cx, y0),   # N
            QPointF(cx, y1),   # S
            QPointF(x1, cy),   # E
            QPointF(x0, cy),   # W
        ]


# ---------------------------------------------------------------------------
# Bipole element
# ---------------------------------------------------------------------------


_BIPOLE_HALF_H = 0.25  # half-height of bipole body in GU (matches standard bipole height)


def _extract_bipole_label(options: str) -> str:
    """Extract the t= value from a bipole options string."""
    m = re.search(r'\bt\s*=\s*([^,]+)', options)
    return m.group(1).strip() if m else ""


class BipoleItem(_DrawingAnnotationBase, _ResizableTwoTerminalItem):
    """Generic labelled bipole: a resizable rectangle with centered label text.

    The body spans from the origin pin (left midpoint) to the terminal pin
    (right midpoint) with a fixed half-height of ``_BIPOLE_HALF_H`` GU above
    and below the connecting line.  The ``t=`` option value is drawn centred
    inside the rectangle.
    """

    def _sync_options_item(self) -> None:
        # Label text is drawn inline; hide the floating options child unless editing.
        if not self._options_item.is_editing:
            self._options_item.setVisible(False)
        if not self._ghost:
            self._request_vector(_extract_bipole_label(self._component.options))

    def begin_options_edit(self) -> None:
        """Activate inline editing of the t= label text centred inside the box."""
        if self._options_item.is_editing:
            return
        from app.components.model import BipoleComponent as _BipoleComponent
        comp = self._component
        assert isinstance(comp, _BipoleComponent)
        font = _fonted_qfont(comp)
        self._options_item.setFont(font)
        self._options_item.setTransform(QTransform())
        label = _extract_bipole_label(comp.options)
        self._options_item.setPlainText(label)
        ep = self._endpoint_px()
        h = _BIPOLE_HALF_H * GRID_PX
        x0 = min(0.0, ep.x())
        x1 = max(0.0, ep.x())
        cx = (x0 + x1) / 2
        er = self._options_item.boundingRect()
        self._options_item.setPos(cx - er.width() / 2, -er.height() / 2)
        self._options_item.setVisible(True)
        self._options_item.begin_edit()

    def _on_options_commit(self, text: str) -> None:
        """Wrap the edited label text back into the full options string."""
        scene = self.scene()
        if scene is None or not hasattr(scene, "edit_component_options"):
            return
        old_opts = self._component.options
        # Replace (or insert) the t= slot; preserve all other slots.
        stripped = re.sub(r'\bt\s*=\s*[^,]+(,\s*)?', '', old_opts).strip(', ')
        new_opts = (f"t={text}" + (f", {stripped}" if stripped else "")) if text else stripped
        scene.edit_component_options(self._component.id, new_opts)

    def boundingRect(self) -> QRectF:
        ep = self._endpoint_px()
        h = _BIPOLE_HALF_H * GRID_PX
        m = _HANDLE_HALF + LINE_W_THICK
        x0 = min(0.0, ep.x()) - m
        x1 = max(0.0, ep.x()) + m
        return QRectF(x0, -h - m, x1 - x0, 2 * h + 2 * m)

    def shape(self) -> QPainterPath:
        ep = self._endpoint_px()
        h = _BIPOLE_HALF_H * GRID_PX
        x0 = min(0.0, ep.x())
        x1 = max(0.0, ep.x())
        rect = QRectF(x0, -h, x1 - x0, 2 * h)
        path = QPainterPath()
        path.addRect(rect)
        handle = QPainterPath()
        handle.addRect(
            ep.x() - _HANDLE_HALF - 2, ep.y() - _HANDLE_HALF - 2,
            (_HANDLE_HALF + 2) * 2, (_HANDLE_HALF + 2) * 2,
        )
        return path.united(handle)

    def _draw_body(self, painter: QPainter, color: str, ep: QPointF) -> None:
        from app.components.model import BipoleComponent as _BipoleComponent
        h = _BIPOLE_HALF_H * GRID_PX
        x0 = min(0.0, ep.x())
        x1 = max(0.0, ep.x())
        rect = QRectF(x0, -h, x1 - x0, 2 * h)
        comp = self._component
        assert isinstance(comp, _BipoleComponent)
        bw_px = comp.line_width * GRID_PX / _PT_PER_GU
        painter.setPen(_pen(color, bw_px, _resolve_pen_style(comp.line_style)))
        if comp.fill_color and not self._ghost:
            painter.setBrush(QBrush(_resolve_tikz_color(comp.fill_color)))
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        label = _extract_bipole_label(self._component.options)
        if label and not self._ghost and not self._options_item.is_editing:
            if self._vec_path is not None:
                scale = self._vec_scale()
                pr = self._vec_path.boundingRect()
                painter.save()
                painter.translate(rect.center().x(), rect.center().y())
                painter.scale(scale, scale)
                painter.translate(-pr.center().x(), -pr.center().y())
                painter.fillPath(self._vec_path, QBrush(QColor(color)))
                painter.restore()
            else:
                from app.components.model import BipoleComponent as _BipoleComponent
                comp = self._component
                assert isinstance(comp, _BipoleComponent)
                painter.setFont(_fonted_qfont(comp))
                painter.drawText(rect, Qt.AlignCenter, label)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = self._body_color()
        ep = self._endpoint_px()
        self._draw_body(painter, color, ep)
        if not self._ghost:
            painter.setPen(self._pin_pen())
            painter.setBrush(self._pin_brush())
            for pt in (QPointF(0.0, 0.0), ep):
                painter.drawEllipse(pt, PIN_R, PIN_R)
        if self.isSelected() and not self._ghost:
            painter.setPen(_pen(style.COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor(style.COLOR_SELECTED)))
            handles = [ep]
            if self._origin_draggable():
                handles.append(QPointF(0.0, 0.0))
            for h in handles:
                painter.drawRect(
                    h.x() - _HANDLE_HALF, h.y() - _HANDLE_HALF,
                    _HANDLE_HALF * 2, _HANDLE_HALF * 2,
                )


# ---------------------------------------------------------------------------
# ITEM_CLASSES mapping — registered into the component registry
# ---------------------------------------------------------------------------

# Only kinds whose item overrides base behaviour are listed; every other
# registry kind (passives, diodes, sources, amplifiers, BJTs, grounds, rails)
# resolves to the base ``ComponentItem`` via ``ITEM_CLASSES.get(kind, ComponentItem)``
# at the lookup sites (scene.py, palette.py).  Adding a plain CircuiTikZ symbol
# therefore needs no entry here — just a ``definitions.json`` record.
ITEM_CLASSES: dict[str, type[ComponentItem]] = {
    "nigfete":   _MosfetItem,   # extends boundingRect for the body_diode variant
    "nigfetd":   _MosfetItem,
    "pigfete":   _MosfetItem,
    "pigfetd":   _MosfetItem,
    "nfet":      _MosfetItem,
    "pfet":      _MosfetItem,
    "open":      OpenItem,      # resizable two-terminal annotations
    "short":     ShortItem,
    "text_node": TextNodeItem,  # drawing primitives
    "rect":      RectItem,
    "circle":    CircleItem,
    "bipole":    BipoleItem,
}

# Push into the registry so other modules can look up item classes without
# importing Qt (they import ITEM_CLASSES from app.components.registry).
from app.components.registry import ITEM_CLASSES as _REG_ITEM_CLASSES  # noqa: E402
_REG_ITEM_CLASSES.update(ITEM_CLASSES)
