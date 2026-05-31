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
  • Renders component labels as plain text (LaTeX verbatim on canvas).

ITEM_CLASSES at the bottom of this module is registered into
app.components.registry.ITEM_CLASSES so the rest of the application can map a
component kind to its item class without importing Qt everywhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPainterPath,
    QPainterPathStroker,
    QPen,
    QTransform,
)
from PySide6.QtWidgets import QGraphicsItem, QGraphicsTextItem

from app.canvas.style import (
    COLOR_GHOST,
    COLOR_HOVER,
    COLOR_NORMAL,
    COLOR_PIN,
    COLOR_SELECTED,
    GRID_PX,
    LINE_W,
    LINE_W_THICK,
    PIN_R,
)
from app.canvas.svgsym import is_thick, symbol_paths
from app.components.registry import REGISTRY

if TYPE_CHECKING:
    from app.schematic.model import Component


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

_LABEL_FONT_SIZE = 10.0
_LABEL_LINE_H = 17   # px height per label row for 10pt font
_LABEL_GAP = 4       # px gap between bbox top edge and bottom of label block


class LabelTextItem(QGraphicsTextItem):
    """Editable, draggable options label child of a ComponentItem.

    Displays ``comp.options`` verbatim (e.g. ``l=$R_1$, v=$V_s$``).
    Call :meth:`begin_edit` to activate in-place editing.  Commits via its
    callback on focus-loss, Enter, or Return; Escape cancels without changes.

    The label can be dragged freely within the parent's coordinate system.
    After a drag completes the move callback is fired with the new QPointF
    position so the scene can persist the offset via an undoable command.
    """

    def __init__(self, parent: "ComponentItem") -> None:
        super().__init__(parent)
        self._editing = False
        self._hovered = False
        self._saved_text: str = ""
        self._commit_cb = None  # callable(text: str) -> None
        self._move_cb = None    # callable(QPointF) -> None
        self._drag_origin: QPointF | None = None  # pos at mouse-press

        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setFlag(QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptHoverEvents(True)
        f = self.font()
        f.setPointSizeF(_LABEL_FONT_SIZE)
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
            self.end_edit(commit=True)
            return
        if event.key() == Qt.Key_Escape:
            self.end_edit(commit=False)
            return
        super().keyPressEvent(event)

    def _apply_text_color(self) -> None:
        """Set text colour based on current interactive/hover/edit state."""
        parent = self.parentItem()
        draggable = bool(self.flags() & QGraphicsItem.ItemIsMovable)
        parent_selected = parent is not None and parent.isSelected()
        show_hover = (
            self._hovered
            and draggable
            and not parent_selected
            and not self._editing
        )
        self.setDefaultTextColor(QColor(COLOR_HOVER if show_hover else COLOR_NORMAL))

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
# Base class
# ---------------------------------------------------------------------------

class ComponentItem(QGraphicsItem):
    """
    Base for all component graphics items.

    Painting is fully data-driven: the symbol geometry comes from the SVG
    manifest via :func:`app.canvas.svgsym.symbol_paths`.  Subclasses only set
    the component ``kind`` (implicitly, via the Component they wrap) and may
    override :meth:`extra_leads` to add connector stubs.

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

        # Single child item for the whole options string.
        self._options_item = LabelTextItem(self)
        self._options_item.set_commit_callback(self._on_options_commit)
        self._options_item.set_move_callback(self._on_options_label_moved)
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
        self._component = comp
        self._sync_options_item()

    def _on_options_commit(self, text: str) -> None:
        """Called by the LabelTextItem when the user commits an in-place edit."""
        scene = self.scene()
        if scene is not None and hasattr(scene, "edit_component_options"):
            scene.edit_component_options(self._component.id, text)

    def _on_options_label_moved(self, new_pos: QPointF) -> None:
        """Called by the LabelTextItem after the user drags it to a new position."""
        scene = self.scene()
        if scene is not None and hasattr(scene, "move_options_label"):
            scene.move_options_label(self._component.id, (new_pos.x(), new_pos.y()))

    def _default_label_pos(self) -> QPointF:
        """Default above-centre position for the options label (component-local px)."""
        x0, y0, x1, y1 = self._defn.bbox
        cx = (x0 + x1) / 2 * GRID_PX
        bbox_top = y0 * GRID_PX
        w = self._options_item.boundingRect().width()
        return QPointF(cx - w / 2, bbox_top - _LABEL_GAP - _LABEL_LINE_H)

    def _sync_options_item(self) -> None:
        """Update position and visibility of the child options LabelTextItem."""
        if self._options_item.is_editing:
            return
        options = self._component.options
        if options and not self._ghost:
            self._options_item.setPlainText(options)
            if self._component.label_offset is not None:
                dx, dy = self._component.label_offset
                self._options_item.setPos(dx, dy)
            else:
                self._options_item.setPos(self._default_label_pos())
            self._options_item.setVisible(True)
        else:
            self._options_item.setVisible(False)

    def begin_options_edit(self) -> None:
        """Show and activate in-place editing for the options string."""
        if self._options_item.is_editing:
            return
        if not self._options_item.isVisible():
            self._options_item.setPlainText("")
            self._options_item.setPos(self._default_label_pos())
            self._options_item.setVisible(True)
        self._options_item.begin_edit()

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def set_label_interactive(self, interactive: bool) -> None:
        """Allow or block label dragging (mirrors parent's SELECT-mode flag)."""
        self._options_item.setFlag(QGraphicsItem.ItemIsMovable, interactive)
        self._options_item._apply_text_color()

    def set_ghost(self, ghost: bool) -> None:
        self._ghost = ghost
        if ghost:
            self._options_item.setVisible(False)
        elif self._component.options:
            self._options_item.setVisible(True)
        self.update()

    def hoverEnterEvent(self, event):  # noqa: N802
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    # ------------------------------------------------------------------
    # Color selection
    # ------------------------------------------------------------------

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
        for sym in symbol_paths(self.component.kind):
            lw = LINE_W_THICK if is_thick(sym.stroke_width) else LINE_W
            pen = _pen(color, lw)
            painter.setPen(pen)
            if sym.filled:
                painter.setBrush(QBrush(QColor(color)))
            else:
                painter.setBrush(Qt.NoBrush)
            painter.drawPath(sym.path)

        # --- connector lead stubs (multi-terminal symbols) ----------------
        leads = self.extra_leads()
        if leads:
            painter.setPen(_pen(color, LINE_W))
            painter.setBrush(Qt.NoBrush)
            for a, b in leads:
                painter.drawLine(a, b)

        # --- pin indicator dots ------------------------------------------
        if not self._ghost:
            painter.setPen(self._pin_pen())
            painter.setBrush(self._pin_brush())
            for pdef in self._defn.pins:
                dx, dy = pdef.offset
                painter.drawEllipse(
                    QPointF(dx * GRID_PX, dy * GRID_PX), PIN_R, PIN_R
                )


# ------------------------------------------------------------------
    # Subclass hook
    # ------------------------------------------------------------------

    def extra_leads(self) -> list[tuple[QPointF, QPointF]]:
        """Extra connector segments (pixel coords) bridging pins to symbol
        terminals.  Two-terminal devices need none (their SVG leads already
        reach the pins)."""
        return []


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
    """Diode: triangle + cathode bar (SVG: D)."""


# ---------------------------------------------------------------------------
# Amplifiers
# ---------------------------------------------------------------------------

class OpAmpItem(ComponentItem):
    """Op-amp triangle (SVG: op amp).

    The op-amp SVG is exported with grid-aligned terminal leads (input +/-,
    output, and vs+/vs- power rails all routed to half-grid points — see
    ``tools/export_circuitikz_svgs.sh``), so the base ``paint`` renders every
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

class NigfeteItem(ComponentItem):
    """N-channel enhancement MOSFET (SVG: nigfete).

    The nigfete SVG is exported with grid-aligned terminal leads (gate, drain,
    and source each routed to a half-grid point — see
    ``tools/export_circuitikz_svgs.sh``), so the base ``paint`` renders every
    terminal directly onto its registry pin.  No bridging required.
    """


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
        margin = LINE_W + PIN_R + 3
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
        painter.setPen(_pen(color, LINE_W))
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
# ITEM_CLASSES mapping — registered into the component registry
# ---------------------------------------------------------------------------

ITEM_CLASSES: dict[str, type[ComponentItem]] = {
    "R":        ResistorItem,
    "C":        CapacitorItem,
    "L":        InductorItem,
    "D":        DiodeItem,
    "op amp":   OpAmpItem,
    "nigfete":  NigfeteItem,
    "V":        VoltageSourceItem,
    "I":        CurrentSourceItem,
    "vsource":  AcVoltageSourceItem,
    "isource":  AcCurrentSourceItem,
    "cV":       VcvsItem,
    "cI":       VccsItem,
}

# Push into the registry so other modules can look up item classes without
# importing Qt (they import ITEM_CLASSES from app.components.registry).
from app.components.registry import ITEM_CLASSES as _REG_ITEM_CLASSES  # noqa: E402
_REG_ITEM_CLASSES.update(ITEM_CLASSES)
