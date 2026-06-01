"""
SchematicView — the QGraphicsView wrapping a SchematicScene (spec §3.4, §6).

Responsibilities:
  * Continuous zoom via scroll wheel (Ctrl+wheel or plain wheel) and Ctrl+±.
  * Pan via middle-mouse drag or Space+left-drag (spec §6.1).
  * "Fit to schematic" framing all placed items with a margin (spec §3.4).
  * Translating key presses into scene mode changes / commands:
        W        → wire mode
        Escape   → cancel / select mode
        arrows   → nudge selection by 0.5 GU
        Del/Bksp → delete selection
        Ctrl+Z / Ctrl+Shift+Z → undo / redo
  * Reporting the live zoom level via the ``zoom_changed`` signal.

Zoom and pan never change grid-unit size or snap behaviour — those remain in
schematic coordinates; only the view transform changes (spec §3.4).
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QGraphicsView

from app.canvas.items import LabelTextItem
from app.canvas.scene import Mode, SchematicScene
from app.canvas.style import GRID_PX

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

    # ------------------------------------------------------------------
    # Mode sync
    # ------------------------------------------------------------------

    def _on_mode_changed(self, mode: Mode) -> None:
        if mode == Mode.PAN:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.RubberBandDrag)
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
        rect = self._scene.itemsBoundingRect()
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
        if self._scene.mode != Mode.PAN:
            self.unsetCursor()

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

        if key == Qt.Key_R and mods == Qt.NoModifier:
            self._scene.rotate_selected_cw()
            event.accept()
            return

        if key in (Qt.Key_Delete, Qt.Key_Backspace):
            self._scene.delete_selected()
            event.accept()
            return

        # Arrow-key nudge by 0.5 GU.
        nudges = {
            Qt.Key_Left: (-0.5, 0.0),
            Qt.Key_Right: (0.5, 0.0),
            Qt.Key_Up: (0.0, -0.5),
            Qt.Key_Down: (0.0, 0.5),
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

        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._space_down = False
            if not self._pan_active and self._scene.mode != Mode.PAN:
                self.unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)
