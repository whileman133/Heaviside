"""
Properties Panel (spec §10.3).

Shows the selected component's label slots, rotation controls, and mirror
toggle.  Double-clicking a component on the canvas calls
``show_component(comp_id)`` on this panel (wired up by MainWindow).

Label fields accept arbitrary LaTeX strings.  A per-slot equation preview image
is rendered by :class:`EquationPreviewWorker` with a 500 ms debounce.
Changes are committed as undoable EditCommand / RotateCommand / MirrorCommand
via the scene.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QImage, QPixmap, QColor
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QButtonGroup,
)

from app.canvas.scene import SchematicScene
from app.components.registry import REGISTRY
from app.preview.worker import EquationPreviewWorker

_PANEL_WIDTH = 250
_PREVIEW_HEIGHT = 40  # max height for inline equation preview images


class _SlotWidget(QWidget):
    """A single label-slot row: name label + text field + equation preview."""

    def __init__(self, slot: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._slot = slot
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(300)  # fast commit debounce
        self._on_commit = None  # callable(slot, value) set by PropertiesPanel

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 2)
        layout.setSpacing(2)

        # Slot name + text field on one row.
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        name = QLabel(slot)
        name.setFixedWidth(28)
        name.setStyleSheet("color: #666; font-size: 11px;")
        self._field = QLineEdit()
        self._field.setPlaceholderText(f"LaTeX for {slot}")
        self._field.textChanged.connect(self._on_text_changed)
        row.addWidget(name)
        row.addWidget(self._field, 1)
        layout.addLayout(row)

        # Equation preview image.
        self._preview_label = QLabel()
        self._preview_label.setFixedHeight(_PREVIEW_HEIGHT)
        self._preview_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._preview_label.setStyleSheet(
            "background: white; border: 1px solid #ddd; border-radius: 2px;"
        )
        self._preview_label.hide()
        layout.addWidget(self._preview_label)

    @property
    def field(self) -> QLineEdit:
        return self._field

    def set_value(self, text: str) -> None:
        """Set the field value without triggering commit."""
        self._field.blockSignals(True)
        self._field.setText(text)
        self._field.blockSignals(False)
        self._preview_label.hide()

    def set_commit_callback(self, cb) -> None:
        self._on_commit = cb

    def show_preview(self, image: QImage) -> None:
        pix = QPixmap.fromImage(image)
        scaled = pix.scaledToHeight(_PREVIEW_HEIGHT, Qt.SmoothTransformation)
        self._preview_label.setPixmap(scaled)
        self._preview_label.show()
        self._field.setStyleSheet("")

    def show_preview_error(self) -> None:
        self._preview_label.hide()
        self._field.setStyleSheet("border: 1px solid red;")

    def clear_preview(self) -> None:
        self._preview_label.hide()
        self._field.setStyleSheet("")

    def _on_text_changed(self, text: str) -> None:
        if self._on_commit:
            self._commit_timer.start()
            self._commit_timer.timeout.connect(self._do_commit)

    def _do_commit(self) -> None:
        self._commit_timer.timeout.disconnect(self._do_commit)
        if self._on_commit:
            self._on_commit(self._slot, self._field.text())


class PropertiesPanel(QWidget):
    """Right-panel properties editor (spec §10.3)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._scene: SchematicScene | None = None
        self._current_comp_id: str | None = None
        self._slot_widgets: dict[str, _SlotWidget] = {}

        # One equation worker shared across all slots; we serialise via slot name.
        self._eq_worker = EquationPreviewWorker(self)
        self._pending_slot: str | None = None
        self._eq_worker.preview_ready.connect(self._on_eq_ready)
        self._eq_worker.preview_error.connect(self._on_eq_error)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        # Component header.
        self._header = QLabel("No selection")
        self._header.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._header.setWordWrap(True)
        outer.addWidget(self._header)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        outer.addWidget(sep)

        # Scrollable label-slot area.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll, 1)

        self._slots_container = QWidget()
        self._slots_layout = QVBoxLayout(self._slots_container)
        self._slots_layout.setContentsMargins(0, 0, 0, 0)
        self._slots_layout.setSpacing(4)
        self._slots_layout.addStretch(1)
        scroll.setWidget(self._slots_container)

        # Rotation controls.
        rot_label = QLabel("Rotation")
        rot_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #555;")
        outer.addWidget(rot_label)

        rot_row = QHBoxLayout()
        rot_row.setSpacing(4)
        self._rot_buttons: dict[int, QPushButton] = {}
        rot_group = QButtonGroup(self)
        rot_group.setExclusive(True)
        for angle in (0, 90, 180, 270):
            btn = QPushButton(f"{angle}°")
            btn.setCheckable(True)
            btn.setFixedWidth(52)
            rot_group.addButton(btn)
            self._rot_buttons[angle] = btn
            btn.clicked.connect(lambda checked, a=angle: self._on_rotate(a))
            rot_row.addWidget(btn)
        outer.addLayout(rot_row)

        # Mirror toggle.
        self._mirror_cb = QCheckBox("Mirror (horizontal)")
        self._mirror_cb.stateChanged.connect(self._on_mirror)
        outer.addWidget(self._mirror_cb)

        self._set_enabled(False)

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene

    def show_component(self, comp_id: str) -> None:
        """Populate the panel for the component with the given id."""
        if self._scene is None:
            return
        comp = next(
            (c for c in self._scene.schematic.components if c.id == comp_id), None
        )
        if comp is None:
            self.clear()
            return

        self._current_comp_id = comp_id
        defn = REGISTRY[comp.kind]
        self._header.setText(f"{defn.display_name}\n({comp.kind})")

        # Rebuild slot widgets to match this component's label_slots.
        self._rebuild_slots(defn.label_slots, comp.labels)

        # Set rotation buttons.
        btn = self._rot_buttons.get(comp.rotation)
        if btn:
            btn.setChecked(True)

        # Set mirror checkbox.
        self._mirror_cb.blockSignals(True)
        self._mirror_cb.setChecked(comp.mirror)
        self._mirror_cb.blockSignals(False)

        self._set_enabled(True)

    def clear(self) -> None:
        """Show 'No selection' state."""
        self._current_comp_id = None
        self._header.setText("No selection")
        self._rebuild_slots([], {})
        self._set_enabled(False)

    def show_multi_select(self, count: int) -> None:
        self._current_comp_id = None
        self._header.setText(f"{count} components selected")
        self._rebuild_slots([], {})
        self._set_enabled(False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_enabled(self, enabled: bool) -> None:
        for btn in self._rot_buttons.values():
            btn.setEnabled(enabled)
        self._mirror_cb.setEnabled(enabled)

    def _rebuild_slots(self, slots: list[str], labels: dict[str, str]) -> None:
        # Remove old slot widgets.
        while self._slots_layout.count() > 1:  # keep the trailing stretch
            item = self._slots_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._slot_widgets.clear()

        for slot in slots:
            sw = _SlotWidget(slot)
            sw.set_value(labels.get(slot, ""))
            sw.set_commit_callback(self._on_slot_changed)
            self._slots_widgets_insert(sw)
            self._slot_widgets[slot] = sw

    def _slots_widgets_insert(self, widget: QWidget) -> None:
        """Insert before the trailing stretch."""
        stretch_item_idx = self._slots_layout.count() - 1
        self._slots_layout.insertWidget(stretch_item_idx, widget)

    def _on_slot_changed(self, slot: str, value: str) -> None:
        """Called when a slot field is committed (after 300 ms debounce)."""
        if self._scene is None or self._current_comp_id is None:
            return
        comp = next(
            (c for c in self._scene.schematic.components if c.id == self._current_comp_id),
            None,
        )
        if comp is None:
            return
        new_labels = dict(comp.labels)
        new_labels[slot] = value
        self._scene.edit_component_labels(self._current_comp_id, new_labels)

        # Trigger equation preview for this slot.
        if value.strip():
            self._pending_slot = slot
            self._eq_worker.request_compile(value)
        else:
            sw = self._slot_widgets.get(slot)
            if sw:
                sw.clear_preview()

    def _on_eq_ready(self, image: QImage) -> None:
        if self._pending_slot and self._pending_slot in self._slot_widgets:
            self._slot_widgets[self._pending_slot].show_preview(image)

    def _on_eq_error(self) -> None:
        if self._pending_slot and self._pending_slot in self._slot_widgets:
            self._slot_widgets[self._pending_slot].show_preview_error()

    def _on_rotate(self, angle: int) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        self._scene.rotate_component(self._current_comp_id, angle)

    def _on_mirror(self, state: int) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        self._scene.mirror_component(self._current_comp_id, bool(state))

    def shutdown(self) -> None:
        """Stop background threads.  Call before application exits."""
        self._eq_worker.shutdown()
