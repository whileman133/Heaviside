"""
Properties Panel (spec §10.3).

Shows the selected component's CircuiTikZ options string, rotation controls,
and mirror toggle.  Double-clicking a component on the canvas calls
``show_component(comp_id)`` on this panel (wired up by MainWindow).

The options field accepts an arbitrary CircuiTikZ option string exactly as it
would appear inside ``to[KIND, ...]`` or ``node[KIND, ...]``, e.g.
``l=$R_1$, v=$V_s$, color=red``.  Changes are committed as undoable
EditCommand / RotateCommand / MirrorCommand via the scene.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QPushButton,
    QFrame,
    QSizePolicy,
    QButtonGroup,
)

from app.canvas.scene import SchematicScene
from app.components.registry import REGISTRY

_PANEL_WIDTH = 250


class PropertiesPanel(QWidget):
    """Right-panel properties editor (spec §10.3)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._scene: SchematicScene | None = None
        self._current_comp_id: str | None = None

        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(300)
        self._commit_timer.timeout.connect(self._do_commit)

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

        # CircuiTikZ options field.
        opts_label = QLabel("CircuiTikZ options")
        opts_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #555;")
        outer.addWidget(opts_label)

        self._opts_field = QLineEdit()
        self._opts_field.setPlaceholderText("e.g. l=$R_1$, v=$V_s$")
        self._opts_field.textChanged.connect(self._on_options_changed)
        outer.addWidget(self._opts_field)

        self._hint_label = QLabel()
        self._hint_label.setStyleSheet("color: #888; font-size: 10px;")
        self._hint_label.setWordWrap(True)
        outer.addWidget(self._hint_label)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        outer.addWidget(sep2)

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

        outer.addStretch(1)

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

        # Populate options field without triggering commit.
        self._opts_field.blockSignals(True)
        self._opts_field.setText(comp.options)
        self._opts_field.blockSignals(False)

        # Show valid label slots as a hint.
        if defn.label_slots:
            self._hint_label.setText("Slots: " + ", ".join(defn.label_slots))
        else:
            self._hint_label.setText("")

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
        self._opts_field.blockSignals(True)
        self._opts_field.clear()
        self._opts_field.blockSignals(False)
        self._hint_label.setText("")
        self._set_enabled(False)

    def show_multi_select(self, count: int) -> None:
        self._current_comp_id = None
        self._header.setText(f"{count} components selected")
        self._opts_field.blockSignals(True)
        self._opts_field.clear()
        self._opts_field.blockSignals(False)
        self._hint_label.setText("")
        self._set_enabled(False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_enabled(self, enabled: bool) -> None:
        self._opts_field.setEnabled(enabled)
        for btn in self._rot_buttons.values():
            btn.setEnabled(enabled)
        self._mirror_cb.setEnabled(enabled)

    def _on_options_changed(self, text: str) -> None:
        self._commit_timer.start()

    def _do_commit(self) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        self._scene.edit_component_options(
            self._current_comp_id, self._opts_field.text().strip()
        )

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
        pass  # no worker threads in the simplified panel
