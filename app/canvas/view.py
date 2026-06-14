"""
SchematicView — the QGraphicsView wrapping a SchematicScene (spec §3.4, §6).

Responsibilities:
  * Continuous zoom via scroll wheel (Ctrl+wheel or plain wheel) and Ctrl+±.
  * Pan via middle-mouse drag or Space+left-drag (spec §6.1).
  * "Fit to schematic" framing all placed items with a margin (spec §3.4).
  * Translating key presses into scene mode changes / commands:
        W        → wire mode
        Escape   → cancel / select mode
        arrows   → nudge selection by 0.25 GU (one minor-grid cell)
        Del/Bksp → delete selection
        Ctrl+Z / Ctrl+Shift+Z → undo / redo
  * Reporting the live zoom level via the ``zoom_changed`` signal.

Zoom and pan never change grid-unit size or snap behaviour — those remain in
schematic coordinates; only the view transform changes (spec §3.4).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QPainter
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QGraphicsView,
    QLineEdit,
    QPlainTextEdit,
)

from app.canvas.geometry import NUDGE_GU
from app.canvas.items import LabelTextItem
from app.canvas.scene import Mode, SchematicScene

_ZOOM_STEP = 1.15
_ZOOM_MIN = 0.1
_ZOOM_MAX = 8.0
_FIT_MARGIN_PX = 40.0


class SchematicView(QGraphicsView):
    """Pan/zoom view over a :class:`SchematicScene`."""

    zoom_changed = Signal(float)
    """Emitted with the new absolute zoom factor (1.0 == 1:1)."""

    def __init__(self, scene: SchematicScene | None = None, parent=None):
        super().__init__(parent)
        self._scene = scene or SchematicScene()
        self.setScene(self._scene)

        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.RubberBandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setMouseTracking(True)

        self._zoom = 1.0
        self._space_down = False
        self._pan_active = False
        self._pan_anchor = QPointF()
        # key → component-kind placement map (configured in Preferences, §10.2);
        # consulted from the Select tool in keyPressEvent. Empty until set.
        self._placement_shortcuts: dict[str, str] = {}

        self._scene.mode_changed.connect(self._on_mode_changed)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def schematic_scene(self) -> SchematicScene:
        return self._scene

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_placement_shortcuts(self, mapping: dict[str, str]) -> None:
        """Install the key → component-kind placement map (spec §10.2). Keys are
        normalised to lowercase so the lookup in ``keyPressEvent`` is canonical."""
        self._placement_shortcuts = {k.lower(): v for k, v in mapping.items()}

    # ------------------------------------------------------------------
    # Mode sync
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: Mode) -> None:
        self.setDragMode(
            QGraphicsView.ScrollHandDrag if mode == Mode.PAN
            else QGraphicsView.RubberBandDrag
        )
        self._apply_mode_cursor()

    def _apply_mode_cursor(self) -> None:
        """Set the canvas cursor for the current mode, so the active tool reads at a
        glance: a **crosshair** while wiring or placing (both are click-on-canvas-to-
        add modes — WIRE in particular has no ghost to signal itself), an open hand
        for pan, and the default arrow for select. Routed through here (rather than a
        bare ``unsetCursor``) so a transient Space-pan restores the mode's cursor
        instead of resetting to the arrow."""
        mode = self._scene.mode
        if mode == Mode.PAN:
            self.setCursor(Qt.OpenHandCursor)
        elif mode in (Mode.WIRE, Mode.PLACE):
            self.setCursor(Qt.CrossCursor)
        else:
            self.unsetCursor()

    # ------------------------------------------------------------------
    # Zoom
    # ------------------------------------------------------------------

    def _apply_zoom(self, factor: float) -> None:
        new_zoom = self._zoom * factor
        new_zoom = max(_ZOOM_MIN, min(_ZOOM_MAX, new_zoom))
        factor = new_zoom / self._zoom
        if factor == 1.0:
            return
        self._zoom = new_zoom
        self.scale(factor, factor)
        self.zoom_changed.emit(self._zoom)

    def zoom_in(self) -> None:
        self._apply_zoom(_ZOOM_STEP)

    def zoom_out(self) -> None:
        self._apply_zoom(1.0 / _ZOOM_STEP)

    def reset_zoom(self) -> None:
        if self._zoom != 1.0:
            self.scale(1.0 / self._zoom, 1.0 / self._zoom)
            self._zoom = 1.0
            self.zoom_changed.emit(self._zoom)

    def wheelEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.angleDelta().y() > 0:
            self.zoom_in()
        else:
            self.zoom_out()
        event.accept()

    # ------------------------------------------------------------------
    # Fit to schematic
    # ------------------------------------------------------------------

    def fit_to_schematic(self) -> None:
        """Zoom/pan so all placed items are visible with a fixed margin."""
        items = self._scene.items()
        drawable = [it for it in items if it.isVisible() and it.boundingRect().isValid()]
        if not drawable:
            self.reset_zoom()
            self.centerOn(0, 0)
            return
        # Union only the *drawable* items' scene bounds. Using the scene's
        # itemsBoundingRect() would include invisible/empty helper items pinned
        # at the scene origin (hidden label editors, empty wire-label items),
        # which inflates the rect from the origin to the schematic and makes the
        # fit zoom way out (regression).
        rect = QRectF()
        for it in drawable:
            rect = rect.united(it.sceneBoundingRect())
        rect = rect.adjusted(-_FIT_MARGIN_PX, -_FIT_MARGIN_PX, _FIT_MARGIN_PX, _FIT_MARGIN_PX)
        self.fitInView(rect, Qt.KeepAspectRatio)
        # Recover the absolute zoom from the resulting transform.
        self._zoom = self.transform().m11()
        self.zoom_changed.emit(self._zoom)

    # ------------------------------------------------------------------
    # Pan (middle-drag or Space+left-drag)
    # ------------------------------------------------------------------

    def _begin_pan(self, pos: QPointF) -> None:
        self._pan_active = True
        self._pan_anchor = pos
        self._scene.set_panning(True)
        self.setCursor(Qt.ClosedHandCursor)

    def _end_pan(self) -> None:
        self._pan_active = False
        self._scene.set_panning(False)
        # Still holding Space → stay pan-ready (open hand); otherwise restore the
        # mode's cursor (crosshair while wiring/placing, arrow in select).
        if self._space_down:
            self.setCursor(Qt.OpenHandCursor)
        else:
            self._apply_mode_cursor()

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.button() == Qt.MiddleButton or (
            self._space_down and event.button() == Qt.LeftButton
        ):
            self._begin_pan(event.position())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._pan_active:
            delta = event.position() - self._pan_anchor
            self._pan_anchor = event.position()
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setValue(h.value() - int(delta.x()))
            v.setValue(v.value() - int(delta.y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802, ANN001
        if self._pan_active and event.button() in (Qt.MiddleButton, Qt.LeftButton):
            self._end_pan()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------

    def event(self, event) -> bool:  # noqa: N802, ANN001
        # Intercept Tab / Shift+Tab *before* Qt consumes it for focus navigation
        # (which happens in QWidget.event, ahead of keyPressEvent). While the
        # cursor hovers a wire endpoint, Tab cycles that endpoint's marker; over
        # a wire body it cycles the line style. Shift+Tab steps backward.
        if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            if not isinstance(self._scene.focusItem(), LabelTextItem):
                backward = (
                    event.key() == Qt.Key_Backtab
                    or bool(event.modifiers() & Qt.ShiftModifier)
                )
                scene_pt = self.mapToScene(self.mapFromGlobal(QCursor.pos()))
                if self._scene.cycle_at(scene_pt, backward):
                    event.accept()
                    return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802, ANN001
        # When a label is being in-place edited, let all key events route to
        # the focused QGraphicsTextItem instead of being intercepted here.
        if isinstance(self._scene.focusItem(), LabelTextItem):
            super().keyPressEvent(event)
            return

        key = event.key()
        mods = event.modifiers()

        if key == Qt.Key_Space and not event.isAutoRepeat():
            self._space_down = True
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return

        if key == Qt.Key_Escape:
            self._scene.cancel_current()
            event.accept()
            return

        if key == Qt.Key_S and mods == Qt.NoModifier:
            self._scene.enter_select_mode()
            event.accept()
            return

        if key == Qt.Key_W and mods == Qt.NoModifier:
            self._scene.enter_wire_mode()
            event.accept()
            return

        if key == Qt.Key_P and mods == Qt.NoModifier:
            self._scene.enter_pan_mode()
            event.accept()
            return

        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self._scene.delete_selected()
            event.accept()
            return

        # Arrow-key nudge by one minor-grid cell (NUDGE_GU = 0.25) in any
        # direction; connected wires follow and stay grid-valid (spec §3.1).
        nudges = {
            Qt.Key_Left: (-NUDGE_GU, 0.0),
            Qt.Key_Right: (NUDGE_GU, 0.0),
            Qt.Key_Up: (0.0, -NUDGE_GU),
            Qt.Key_Down: (0.0, NUDGE_GU),
        }
        if key in nudges and self._scene.selected_component_ids():
            dx, dy = nudges[key]
            self._scene.nudge_selected(dx, dy)
            event.accept()
            return

        # Undo / redo.
        if key == Qt.Key_Z and mods & Qt.ControlModifier:
            if mods & Qt.ShiftModifier:
                self._scene.redo()
            else:
                self._scene.undo()
            event.accept()
            return
        if key == Qt.Key_Y and mods & Qt.ControlModifier:
            self._scene.redo()
            event.accept()
            return

        # Zoom shortcuts.
        if key in (Qt.Key_Plus, Qt.Key_Equal) and mods & Qt.ControlModifier:
            self.zoom_in()
            event.accept()
            return
        if key == Qt.Key_Minus and mods & Qt.ControlModifier:
            self.zoom_out()
            event.accept()
            return
        if key == Qt.Key_0 and mods & Qt.ControlModifier:
            self.fit_to_schematic()
            event.accept()
            return

        # Component placement shortcuts (§10.2). Same handler MainWindow delegates
        # to, so the keys work whether or not the canvas holds focus; it guards text
        # inputs / the label editor itself.
        if self.handle_placement_key(event):
            return

        super().keyPressEvent(event)

    def handle_placement_key(self, event) -> bool:  # noqa: ANN001
        """Window-wide component-placement dispatch (spec §10.2). Returns True iff it
        consumed the key.

        Plain keys only (rotate lives on ``Ctrl/⌘+R`` now, so the letters are free
        for placement). Skips keys while a text field or an in-place label editor is
        focused, so it never eats typed letters. A mapped key starts placing its
        component — from the **Select** tool, or while a ghost is already up
        (**Place** mode), where it **swaps** the active ghost to the new kind. It
        stays inert while routing a wire or panning. Called from this view's
        ``keyPressEvent`` (canvas focused) and from ``MainWindow.keyPressEvent``
        (keys that bubble up from elsewhere)."""
        if event.modifiers() != Qt.NoModifier:
            return False
        if isinstance(self._scene.focusItem(), LabelTextItem):
            return False
        if isinstance(QApplication.focusWidget(), (QLineEdit, QPlainTextEdit, QAbstractSpinBox)):
            return False
        char = event.text().lower()
        if len(char) != 1:
            return False
        if self._scene.mode not in (Mode.SELECT, Mode.PLACE):
            return False
        kind = self._placement_shortcuts.get(char)
        if kind is None:
            return False
        self._scene.start_placement(kind)
        event.accept()
        return True

    def keyReleaseEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            if not self._pan_active:
                self._apply_mode_cursor()  # restore the mode cursor (crosshair/arrow/hand)
            event.accept()
            return
        super().keyReleaseEvent(event)
