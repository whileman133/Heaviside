"""
Canvas items — one QGraphicsItem subclass per component type.

Symbols are **not** hand-drawn.  Each component's geometry is translated from
the CircuiTikZ SVG export recorded in ``tools/circuitikz_svgs/manifest.json``
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
    QTransform,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem
from shiboken6 import isValid

from app.canvas.style import (
    COLOR_GHOST,
    COLOR_HOVER,
    COLOR_NORMAL,
    COLOR_PIN,
    COLOR_SELECTED,
    GRID_PX,
    LINE_W,
    LINE_W_THICK,
    OPEN_ANNOTATION_OPACITY,
    PIN_R,
)
from app.canvas.svgsym import is_thick, symbol_paths
from app.components.registry import REGISTRY

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
_LABEL_GAP = 4       # px gap between bbox top edge and bottom of label block
# Current annotations (`i=`) hug the wire (lead axis) instead of clearing the
# whole body, matching where CircuiTikZ draws the current label.
_CURRENT_GAP = 3.0
# Padding (px) of the opaque backdrop drawn behind axis-centred labels so the
# annotation line does not appear to run into the text.
_LABEL_BG_PAD = 3.0
# Voltage sources whose default (unsuffixed) `v=` label sits on the opposite
# side from passives — CircuiTikZ's source voltage convention (see
# ComponentItem._slot_direction).  Current sources (I/cI/isourcesin) follow the
# passive default and are NOT listed.
_VOLTAGE_SOURCE_KINDS = frozenset({"V", "cV", "vsourcesin"})

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
        self.setDefaultTextColor(QColor(COLOR_NORMAL))

    def set_commit_callback(self, cb) -> None:  # noqa: ANN001
        self._commit_cb = cb

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
        return QColor(COLOR_HOVER if show_hover else COLOR_NORMAL)

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
            painter.setPen(_pen(COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor("#FFFFFFFF")))
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
        color = parent._label_color() if hasattr(parent, "_label_color") else QColor(COLOR_NORMAL)
        painter.setRenderHint(QPainter.Antialiasing, True)
        # Axis-centred labels (e.g. the voltage annotation) sit on top of the
        # line, so give them an opaque backdrop with a little padding to keep
        # the line from appearing to run into the text.
        if self._centered:
            painter.fillRect(
                self._scaled_rect().adjusted(
                    -_LABEL_BG_PAD, -_LABEL_BG_PAD, _LABEL_BG_PAD, _LABEL_BG_PAD
                ),
                QColor("#FFFFFFFF"),
            )
        painter.save()
        painter.scale(_VEC_SCALE, _VEC_SCALE)
        painter.fillPath(self._path, QBrush(color))
        painter.restore()


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class ComponentItem(QGraphicsItem):
    """
    Base for all component graphics items.

    Painting is fully data-driven: the symbol geometry comes from the SVG
    manifest via :func:`app.canvas.svgsym.symbol_paths`.  Subclasses only set
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
        # mirror is applied first (xscale=-1), then rotation — matching the
        # spec §4.2 "mirror before rotation" convention.
        t = QTransform()
        if component.mirror:
            t.scale(-1.0, 1.0)
        t.rotate(component.rotation)
        self.setTransform(t)

        # The LabelTextItem is now the in-place *editor* only (double-click);
        # display is handled by per-side _SlotLabel children.  It is hidden when
        # not editing and is not draggable (labels auto-place on their sides).
        self._options_item = LabelTextItem(self)
        self._options_item.setFlag(QGraphicsItem.ItemIsMovable, False)
        self._options_item.set_commit_callback(self._on_options_commit)
        self._slot_items: list[_SlotLabel] = []
        self._sync_options_item()

    # ------------------------------------------------------------------
    # component property — setter syncs the options child item
    # ------------------------------------------------------------------

    @property
    def component(self) -> "Component":
        return self._component

    @component.setter
    def component(self, comp: "Component") -> None:
        if self._options_item.is_editing:
            self._options_item.end_edit(commit=False)
        self.prepareGeometryChange()
        self._component = comp
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

    def _layout_slots(self) -> None:
        """Render each annotation slot on its conventional side of the body.

        Placement is perpendicular to the component's *on-screen* lead axis, so
        labels land on the correct side regardless of rotation/mirror.
        """
        from app.preview.mathrender import slot_fragments

        slots = [] if self._ghost else slot_fragments(self._component.options)
        while len(self._slot_items) < len(slots):
            self._slot_items.append(_SlotLabel(self))

        geom = self._slot_geometry()
        counter = self._label_counter_transform()
        centered = self._labels_centered_on_axis()
        counts: dict[tuple[float, float], int] = {}
        for idx, item in enumerate(self._slot_items):
            if idx < len(slots):
                key, latex = slots[idx]
                direction = self._slot_direction(key, geom)
                # Stack labels that share a direction outward.
                dk = (round(direction.x(), 3), round(direction.y(), 3))
                i = counts.get(dk, 0)
                counts[dk] = i + 1
                # Currents hug the wire (lead axis through the centre); other
                # slots clear the body's perpendicular thickness.  When labels
                # are centred on the axis, there is no clearance — the first
                # slot sits on the line and any siblings stack off it.
                base = 0.0 if centered else (
                    _CURRENT_GAP if key.startswith("i")
                    else geom["perp_thickness"] + _LABEL_GAP
                )
                item.setTransform(counter)
                item.configure(
                    latex, direction, geom["center_rel"],
                    base, _LABEL_LINE_H * i, geom["inv"], centered,
                )
                item.setVisible(True)
            else:
                item.configure(
                    "", geom["left"], geom["center_rel"], 0.0, 0.0, geom["inv"],
                )
                item.setVisible(False)

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
        pins = self._defn.pins
        if len(pins) >= 2:
            (x0, y0), (x1, y1) = pins[0].offset, pins[1].offset
            return (
                QPointF(x0 * GRID_PX, y0 * GRID_PX),
                QPointF(x1 * GRID_PX, y1 * GRID_PX),
            )
        bx0, by0, bx1, by1 = self._defn.bbox
        c = QPointF((bx0 + bx1) / 2 * GRID_PX, (by0 + by1) / 2 * GRID_PX)
        return c, QPointF(c.x() + GRID_PX, c.y())

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
        hw, hh = (x1 - x0) / 2 * GRID_PX, (y1 - y0) / 2 * GRID_PX
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
            return QColor(COLOR_GHOST)
        if self._hovered:
            return QColor(COLOR_HOVER)
        return QColor(COLOR_NORMAL)

    def _body_color(self) -> str:
        if self._ghost:
            return COLOR_GHOST
        if self.isSelected():
            return COLOR_SELECTED
        if self._hovered:
            return COLOR_HOVER
        return COLOR_NORMAL

    def _pin_pen(self) -> QPen:
        if self._ghost:
            return _pen(COLOR_GHOST, 1.0)
        return _pen(COLOR_PIN, 1.0)

    def _pin_brush(self) -> QBrush:
        if self._ghost:
            return QBrush(QColor(COLOR_GHOST))
        return QBrush(QColor(COLOR_PIN))

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

    # ------------------------------------------------------------------
    # QGraphicsItem interface
    # ------------------------------------------------------------------

    def boundingRect(self) -> QRectF:
        x0, y0, x1, y1 = self._defn.bbox
        margin = LINE_W_THICK
        return QRectF(
            x0 * GRID_PX - margin,
            y0 * GRID_PX - margin,
            (x1 - x0) * GRID_PX + 2 * margin,
            (y1 - y0) * GRID_PX + 2 * margin,
        )

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = self._body_color()

        # --- symbol body: stroke/fill each SVG-derived path ---------------
        from app.components.model import DiodeComponent, MosfetComponent
        if isinstance(self.component, DiodeComponent) and self.component.filled:
            svg_kind = self.component.kind + "*"
        elif isinstance(self.component, MosfetComponent) and self.component.body_diode:
            svg_kind = self.component.kind + "_bodydiode"
        else:
            svg_kind = self.component.kind
        for sym in symbol_paths(svg_kind):
            lw = LINE_W_THICK if is_thick(sym.stroke_width) else LINE_W
            pen = _pen(color, lw)
            painter.setPen(pen)
            if sym.filled:
                painter.setBrush(QBrush(QColor(color)))
            else:
                painter.setBrush(Qt.NoBrush)
            painter.drawPath(sym.path)

        # --- pin indicator dots ------------------------------------------
        if not self._ghost:
            painter.setPen(self._pin_pen())
            painter.setBrush(self._pin_brush())
            for pdef in self._defn.pins:
                dx, dy = pdef.offset
                painter.drawEllipse(
                    QPointF(dx * GRID_PX, dy * GRID_PX), PIN_R, PIN_R
                )


# ---------------------------------------------------------------------------
# Passives  (SVG leads reach the pins exactly — no overrides needed)
# ---------------------------------------------------------------------------

class ResistorItem(ComponentItem):
    """American zigzag resistor (SVG: R)."""


class CapacitorItem(ComponentItem):
    """Parallel-plate capacitor (SVG: C)."""


class InductorItem(ComponentItem):
    """American hump inductor (SVG: L)."""


class DiodeItem(ComponentItem):
    """Diode: triangle + cathode bar (SVG: D / D*)."""


class ZenerDiodeItem(ComponentItem):
    """Zener diode: diode + bent cathode bar (SVG: zD / zD*)."""


class SchottkyDiodeItem(ComponentItem):
    """Schottky diode: diode + S-shaped cathode bar (SVG: sD / sD*)."""


class TunnelDiodeItem(ComponentItem):
    """Tunnel diode: diode + double bar cathode (SVG: tD / tD*)."""


class TVSDiodeItem(ComponentItem):
    """TVS/bidirectional Zener diode (SVG: zzD / zzD*)."""


class LEDItem(ComponentItem):
    """LED: diode + emission arrows (SVG: leD / leD*)."""


# ---------------------------------------------------------------------------
# Amplifiers
# ---------------------------------------------------------------------------

class OpAmpItem(ComponentItem):
    """Op-amp triangle (SVG: op amp).

    The op-amp SVG is exported with grid-aligned terminal leads (input +/-,
    output, and vs+/vs- power rails all routed to half-grid points — see
    ``tools/export_circuitikz_svgs.py``), so the base ``paint`` renders every
    terminal directly onto its registry pin.  No bridging required.
    """


# ---------------------------------------------------------------------------
# Sources (fixed + AC)
# ---------------------------------------------------------------------------

class VoltageSourceItem(ComponentItem):
    """DC voltage source circle with +/- (SVG: V)."""


class CurrentSourceItem(ComponentItem):
    """DC current source circle with arrow (SVG: I)."""


class AcVoltageSourceItem(ComponentItem):
    """AC voltage source circle (SVG: vsource)."""


class AcCurrentSourceItem(ComponentItem):
    """AC current source circle (SVG: isource)."""


# ---------------------------------------------------------------------------
# Dependent sources
# ---------------------------------------------------------------------------

class VcvsItem(ComponentItem):
    """VCVS diamond with +/- (SVG: cV)."""


class VccsItem(ComponentItem):
    """VCCS diamond with arrow (SVG: cI)."""


# ---------------------------------------------------------------------------
# MOSFET
# ---------------------------------------------------------------------------

class NpnItem(ComponentItem):
    """NPN BJT (SVG: npn). Base left, collector top-right, emitter bottom-right."""


class PnpItem(ComponentItem):
    """PNP BJT (SVG: pnp). Base left, emitter top-right, collector bottom-right."""


# Extra x1 extent (GU) added to the bounding rect when body_diode is enabled.
# The body diode symbol adds ~11 pt = 0.39 GU; rounded up to 0.45 GU for margin.
_BODYDIODE_EXTRA_X = 0.45


class _MosfetItem(ComponentItem):
    """Base for MOSFET items — extends boundingRect when body_diode is active."""

    def boundingRect(self) -> QRectF:
        from app.components.model import MosfetComponent
        x0, y0, x1, y1 = self._defn.bbox
        if isinstance(self.component, MosfetComponent) and self.component.body_diode:
            x1 = x1 + _BODYDIODE_EXTRA_X
        margin = LINE_W_THICK
        return QRectF(
            x0 * GRID_PX - margin,
            y0 * GRID_PX - margin,
            (x1 - x0) * GRID_PX + 2 * margin,
            (y1 - y0) * GRID_PX + 2 * margin,
        )


class NigfeteItem(_MosfetItem):
    """N-channel enhancement MOSFET (SVG: nigfete)."""


class NigfetdItem(_MosfetItem):
    """N-channel depletion MOSFET (SVG: nigfetd). Solid channel line."""


class PigfeteItem(_MosfetItem):
    """P-channel enhancement MOSFET (SVG: pigfete). Source at top."""


class PigfetdItem(_MosfetItem):
    """P-channel depletion MOSFET (SVG: pigfetd). Source at top, solid channel."""


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
            painter.setPen(_pen(COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor(COLOR_SELECTED)))
            painter.drawRect(
                ep.x() - _HANDLE_HALF, ep.y() - _HANDLE_HALF,
                _HANDLE_HALF * 2, _HANDLE_HALF * 2,
            )

    def _draw_body(self, painter: QPainter, color: str, ep: QPointF) -> None:
        raise NotImplementedError

    def set_preview_span(self, span: tuple[float, float]) -> None:
        import dataclasses
        self._component = dataclasses.replace(self._component, span_override=span)
        self.prepareGeometryChange()
        self.update()

    def terminal_handle_hit(self, local_pt: QPointF) -> bool:
        ep = self._endpoint_px()
        return (abs(local_pt.x() - ep.x()) <= _HANDLE_HALF + 2 and
                abs(local_pt.y() - ep.y()) <= _HANDLE_HALF + 2)


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

class _GroundBase(ComponentItem):
    """Base for single-terminal node components drawn from their SVG export."""

    def boundingRect(self) -> QRectF:
        x0, y0, x1, y1 = self._defn.bbox
        m = LINE_W_THICK
        return QRectF(x0 * GRID_PX - m, y0 * GRID_PX - m,
                      (x1 - x0) * GRID_PX + 2 * m, (y1 - y0) * GRID_PX + 2 * m)


class GroundItem(_GroundBase):
    """Standard ground node (SVG: ground)."""

class RgroundItem(_GroundBase):
    """Reference ground node (SVG: rground)."""

class SgroundItem(_GroundBase):
    """Signal ground node (SVG: sground)."""

class NgroundItem(_GroundBase):
    """Noiseless ground node (SVG: nground)."""

class PgroundItem(_GroundBase):
    """Protective earth node (SVG: pground)."""

class CgroundItem(_GroundBase):
    """Chassis/frame ground node (SVG: cground)."""

class EgroundItem(_GroundBase):
    """Earth ground node (SVG: eground)."""


# ---------------------------------------------------------------------------
# Power rails (single-terminal, positive supplies point up, negative down)
# ---------------------------------------------------------------------------

class VccItem(_GroundBase):
    """VCC power rail node (SVG: vcc)."""

class VddItem(_GroundBase):
    """VDD power rail node (SVG: vdd)."""

class VeeItem(_GroundBase):
    """VEE power rail node (SVG: vee)."""

class VssItem(_GroundBase):
    """VSS power rail node (SVG: vss)."""


# ---------------------------------------------------------------------------
# Wire item
# ---------------------------------------------------------------------------

class WireItem(QGraphicsItem):
    """A polyline wire drawn as a Manhattan path.

    Points are stored in *schematic grid units*; paint() converts to pixels.
    """

    def __init__(self, wire, parent: QGraphicsItem | None = None):  # noqa: ANN001
        super().__init__(parent)
        self.wire = wire
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setAcceptHoverEvents(True)
        self._hovered = False
        # Indices of vertices that are NOT draggable (endpoints on a pin). The
        # scene updates this on rebuild so the handles match the live model.
        self.locked_indices: set[int] = set()
        # Live drag preview: when set, painted instead of self.wire.points.
        self._preview_points: list[tuple[float, float]] | None = None

    # -- drag preview -----------------------------------------------------

    def set_preview_points(self, points: list[tuple[float, float]]) -> None:
        self.prepareGeometryChange()
        self._preview_points = list(points)
        self.update()

    def clear_preview_points(self) -> None:
        self.prepareGeometryChange()
        self._preview_points = None
        self.update()

    @property
    def preview_points(self) -> list[tuple[float, float]] | None:
        """The current preview point list, or None if no preview is active."""
        return self._preview_points

    def _draw_points(self) -> list[tuple[float, float]]:
        return self._preview_points if self._preview_points is not None else self.wire.points

    # -- style ------------------------------------------------------------

    def _line_width_px(self) -> float:
        """Pen width in px, proportional to ``line_width`` (0.4 pt -> LINE_W)."""
        return LINE_W * (getattr(self.wire, "line_width", 0.4) / 0.4)

    def _line_pen_style(self) -> Qt.PenStyle:
        return _resolve_pen_style(getattr(self.wire, "line_style", ""))

    # -- events -----------------------------------------------------------

    def hoverEnterEvent(self, event):  # noqa: N802
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def boundingRect(self) -> QRectF:
        pts = self._draw_points()
        if not pts:
            return QRectF()
        xs = [p[0] * GRID_PX for p in pts]
        ys = [p[1] * GRID_PX for p in pts]
        margin = max(LINE_W, self._line_width_px()) + PIN_R + 3
        return QRectF(
            min(xs) - margin,
            min(ys) - margin,
            max(xs) - min(xs) + 2 * margin,
            max(ys) - min(ys) + 2 * margin,
        )

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
        if len(pts_gu) < 2:
            return QPainterPath()
        pts = [QPointF(x * GRID_PX, y * GRID_PX) for x, y in pts_gu]
        line = QPainterPath()
        line.moveTo(pts[0])
        for pt in pts[1:]:
            line.lineTo(pt)

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

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        pts_gu = self._draw_points()
        if len(pts_gu) < 2:
            return
        painter.setRenderHint(QPainter.Antialiasing, True)
        if self.isSelected():
            color = COLOR_SELECTED
        elif self._hovered:
            color = COLOR_HOVER
        else:
            color = COLOR_NORMAL
        painter.setPen(_pen(color, self._line_width_px(), self._line_pen_style()))
        painter.setBrush(Qt.NoBrush)
        pts = [QPointF(x * GRID_PX, y * GRID_PX) for x, y in pts_gu]
        path = QPainterPath()
        path.moveTo(pts[0])
        for pt in pts[1:]:
            path.lineTo(pt)
        painter.drawPath(path)

        # Draw draggable vertex handles when the wire is selected or hovered,
        # so the user can see which nodes can be moved. Locked endpoints (on a
        # pin) are not drawn as grab handles.
        if self.isSelected() or self._hovered:
            painter.setPen(_pen(COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor("#FFFFFFFF")))
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
        margin = LINE_W + PIN_R + 4
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
            pen = _pen(COLOR_GHOST, LINE_W, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            path = QPainterPath()
            path.moveTo(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            painter.drawPath(path)

        # --- committed vertex anchors (small ghost dots) ------------------
        if self.points:
            painter.setPen(_pen(COLOR_GHOST, 1.0))
            painter.setBrush(QBrush(QColor(COLOR_GHOST)))
            for x, y in self.points:
                painter.drawEllipse(QPointF(x * GRID_PX, y * GRID_PX), PIN_R, PIN_R)

        # --- snap-end marker ---------------------------------------------
        if self.cursor is not None:
            cx, cy = self.cursor[0] * GRID_PX, self.cursor[1] * GRID_PX
            if self.cursor_is_pin:
                # Hollow ring: snapping to a pin.
                painter.setPen(_pen(COLOR_SELECTED, LINE_W))
                painter.setBrush(Qt.NoBrush)
                painter.drawEllipse(QPointF(cx, cy), PIN_R + 2.5, PIN_R + 2.5)
            else:
                # Small filled dot: a bare grid-node anchor.
                painter.setPen(_pen(COLOR_GHOST, 1.0))
                painter.setBrush(QBrush(QColor(COLOR_GHOST)))
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

    def __init__(self, parent: QGraphicsItem | None = None):
        super().__init__(parent)
        self.setZValue(50)                       # above wires, below ghosts
        self.setAcceptedMouseButtons(Qt.NoButton)

    def boundingRect(self) -> QRectF:
        m = self.R + 1.0
        return QRectF(-m, -m, 2 * m, 2 * m)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(_pen(COLOR_NORMAL, 1.0))
        painter.setBrush(QBrush(QColor(COLOR_NORMAL)))
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
        painter.setPen(_pen(COLOR_NORMAL, LINE_W))
        painter.setBrush(Qt.white)
        painter.drawEllipse(QPointF(0.0, 0.0), self.R, self.R)


# ---------------------------------------------------------------------------
# Drawing annotations (non-circuit visual elements)
# ---------------------------------------------------------------------------

class _DrawingAnnotationBase(ComponentItem):
    """Base for drawing annotations (text_node, rect).

    Applies ``component.z_order`` to the item's Qt z-value on construction and
    whenever the component property is updated, so the canvas layer stays in
    sync with the model without the scene needing to know about drawing kinds
    specifically.
    """

    def __init__(self, component: "Component", parent=None) -> None:
        super().__init__(component, parent)
        self.setZValue(component.z_order)

    @ComponentItem.component.setter  # type: ignore[misc]
    def component(self, comp: "Component") -> None:
        # Call the base setter (prepareGeometryChange + model update + label sync)
        ComponentItem.component.fset(self, comp)  # type: ignore[attr-defined]
        self.setZValue(comp.z_order)


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
            painter.setPen(_pen(COLOR_SELECTED, 1.0, Qt.DashLine))
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


class RectItem(_DrawingAnnotationBase, _ResizableTwoTerminalItem):
    """Rectangle drawing element.

    ``component.position`` is the first corner; ``component.span_override`` (or
    ``default_span``) gives the offset (dx, dy) to the opposite corner.
    Fill, border width, and line style come from the StyledComponent fields
    (``fill_color``, ``border_width``, ``line_style``).  No circuit pins.
    """

    def _sync_options_item(self) -> None:
        self._options_item.setVisible(False)

    def _parse_options(self) -> tuple[Qt.PenStyle, float, str]:
        """Return (pen_style, line_width_px, fill_color_name) from the style fields."""
        comp = self._component
        # Convert pt to pixels: 1 pt ≈ 1.333 px at 96 dpi; keep proportional.
        line_width_px = comp.border_width * 1.333
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

    def _draw_body(self, painter: QPainter, color: str, ep: QPointF) -> None:
        pen_style, line_width_px, fill = self._parse_options()
        painter.setPen(_pen(color, line_width_px, pen_style))

        x0 = min(0.0, ep.x())
        y0 = min(0.0, ep.y())
        x1 = max(0.0, ep.x())
        y1 = max(0.0, ep.y())
        rect = QRectF(x0, y0, x1 - x0, y1 - y0)

        if fill and not self._ghost:
            painter.setBrush(QBrush(_resolve_tikz_color(fill)))
        else:
            painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: ANN001
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = self._body_color()
        ep = self._endpoint_px()
        self._draw_body(painter, color, ep)
        # Resize handle at the far corner when selected (no circuit pin dots).
        if self.isSelected() and not self._ghost:
            painter.setPen(_pen(COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor(COLOR_SELECTED)))
            painter.drawRect(
                ep.x() - _HANDLE_HALF, ep.y() - _HANDLE_HALF,
                _HANDLE_HALF * 2, _HANDLE_HALF * 2,
            )


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
        bw_px = comp.border_width * GRID_PX / _PT_PER_GU
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
            painter.setPen(_pen(COLOR_SELECTED, 1.0))
            painter.setBrush(QBrush(QColor(COLOR_SELECTED)))
            painter.drawRect(
                ep.x() - _HANDLE_HALF, ep.y() - _HANDLE_HALF,
                _HANDLE_HALF * 2, _HANDLE_HALF * 2,
            )


# ---------------------------------------------------------------------------
# ITEM_CLASSES mapping — registered into the component registry
# ---------------------------------------------------------------------------

ITEM_CLASSES: dict[str, type[ComponentItem]] = {
    "R":        ResistorItem,
    "C":        CapacitorItem,
    "L":        InductorItem,
    "D":        DiodeItem,
    "zD":       ZenerDiodeItem,
    "sD":       SchottkyDiodeItem,
    "tD":       TunnelDiodeItem,
    "zzD":      TVSDiodeItem,
    "leD":      LEDItem,
    "op amp":   OpAmpItem,
    "npn":      NpnItem,
    "pnp":      PnpItem,
    "nigfete":  NigfeteItem,
    "nigfetd":  NigfetdItem,
    "pigfete":  PigfeteItem,
    "pigfetd":  PigfetdItem,
    "V":        VoltageSourceItem,
    "I":        CurrentSourceItem,
    "vsourcesin": AcVoltageSourceItem,
    "isourcesin": AcCurrentSourceItem,
    "cV":       VcvsItem,
    "cI":       VccsItem,
    "open":       OpenItem,
    "short":      ShortItem,
    "text_node":  TextNodeItem,
    "rect":       RectItem,
    "bipole":     BipoleItem,
    "ground":     GroundItem,
    "rground":  RgroundItem,
    "sground":  SgroundItem,
    "nground":  NgroundItem,
    "pground":  PgroundItem,
    "cground":  CgroundItem,
    "eground":  EgroundItem,
    "vcc":      VccItem,
    "vdd":      VddItem,
    "vee":      VeeItem,
    "vss":      VssItem,
}

# Push into the registry so other modules can look up item classes without
# importing Qt (they import ITEM_CLASSES from app.components.registry).
from app.components.registry import ITEM_CLASSES as _REG_ITEM_CLASSES  # noqa: E402
_REG_ITEM_CLASSES.update(ITEM_CLASSES)
