"""
Properties Panel (spec §10.3).

Shows the selected component's CircuiTikZ options string, rotation controls,
and mirror toggle.  Double-clicking a component on the canvas calls
``show_component(comp_id)`` on this panel (wired up by MainWindow).

For circuit components the options field accepts an arbitrary CircuiTikZ option
string exactly as it would appear inside ``to[KIND, ...]`` or ``node[KIND, ...]``.

For drawing annotations the panel shows kind-specific controls:
  - text_node: text-content field + font-size spinbox + z-order spinbox.
  - rect:      line-style combo + line-width spinbox + fill combo + z-order spinbox.

Rect visual properties (line style, line width, fill) are stored together in
``Component.options`` as a comma-separated TikZ draw-options string that the
code generator passes verbatim into ``\\draw[...] ... rectangle ...;``.
The panel parses and recomposes this string from individual controls.

Z-order is stored in the dedicated ``Component.z_order`` field (not in options).
"""

from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QPushButton,
    QSpinBox,
    QFrame,
    QSizePolicy,
    QButtonGroup,
)

from app.canvas.scene import SchematicScene
from app.components.model import DiodeComponent, DrawingComponent, RectComponent, TextNodeComponent
from app.components.registry import REGISTRY

_PANEL_WIDTH = 250

# ── Rect: line style ─────────────────────────────────────────────────────────
_LINE_STYLE_OPTIONS: list[tuple[str, str]] = [
    ("Solid",     ""),
    ("Dashed",    "dashed"),
    ("Dotted",    "dotted"),
    ("Dash-dot",  "dash dot"),
]
_LABEL_TO_TIKZ_STYLE = {label: tikz for label, tikz in _LINE_STYLE_OPTIONS}
_TIKZ_TO_LABEL_STYLE = {tikz: label for label, tikz in _LINE_STYLE_OPTIONS}

# ── Rect: fill color ─────────────────────────────────────────────────────────
_FILL_OPTIONS: list[tuple[str, str]] = [
    ("None",       ""),
    ("White",      "white"),
    ("Light gray", "gray!15"),
    ("Yellow",     "yellow!20"),
    ("Blue",       "cyan!15"),
    ("Green",      "green!15"),
    ("Red",        "red!15"),
]
_LABEL_TO_TIKZ_FILL = {label: tikz for label, tikz in _FILL_OPTIONS}
_TIKZ_TO_LABEL_FILL = {tikz: label for _, tikz in _FILL_OPTIONS
                       for label, t in _FILL_OPTIONS if t == tikz}
# Reverse map: tikz value → display label.
_TIKZ_FILL_TO_LABEL: dict[str, str] = {tikz: label for label, tikz in _FILL_OPTIONS}


# ── Rect options string helpers ───────────────────────────────────────────────

def _parse_rect_options(options: str) -> tuple[str, float, str]:
    """Parse a rect options string into (line_style, line_width_pt, fill).

    Returns defaults (``""``, 0.4, ``""``) for any missing part.
    """
    opts = options.strip()

    # Extract line width.
    lw_match = re.search(r"line\s+width\s*=\s*([\d.]+)\s*pt", opts)
    line_width = float(lw_match.group(1)) if lw_match else 0.4
    opts_no_lw = re.sub(r",?\s*line\s+width\s*=\s*[\d.]+\s*pt", "", opts).strip(", ")

    # Extract fill.
    fill_match = re.search(r"fill\s*=\s*([^,]+)", opts_no_lw)
    fill = fill_match.group(1).strip() if fill_match else ""
    opts_no_fill = re.sub(r",?\s*fill\s*=\s*[^,]+", "", opts_no_lw).strip(", ")

    # What remains is the line style keyword (e.g. "dashed", "dotted", "dash dot", "").
    line_style = opts_no_fill.strip()

    return line_style, line_width, fill


def _compose_rect_options(line_style: str, line_width: float, fill: str) -> str:
    """Compose a TikZ draw-options string from individual rect properties."""
    parts: list[str] = []
    if line_style:
        parts.append(line_style)
    # Only emit line width when it differs meaningfully from the 0.4 pt default.
    if abs(line_width - 0.4) > 1e-6:
        # Format without trailing zero.
        lw_str = f"{line_width:.1f}" if line_width == round(line_width, 1) else f"{line_width:.2f}"
        parts.append(f"line width={lw_str}pt")
    if fill:
        parts.append(f"fill={fill}")
    return ", ".join(parts)


class PropertiesPanel(QWidget):
    """Right-panel properties editor (spec §10.3)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._scene: SchematicScene | None = None
        self._current_comp_id: str | None = None
        self._current_kind: str | None = None

        # Debounce timers.
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(300)
        self._commit_timer.timeout.connect(self._do_commit)

        self._font_size_timer = QTimer(self)
        self._font_size_timer.setSingleShot(True)
        self._font_size_timer.setInterval(300)
        self._font_size_timer.timeout.connect(self._do_commit_font_size)

        self._rect_timer = QTimer(self)
        self._rect_timer.setSingleShot(True)
        self._rect_timer.setInterval(300)
        self._rect_timer.timeout.connect(self._do_commit_rect)

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

        # ── Options / text field (circuit + text_node) ────────────────────
        self._opts_label = QLabel("CircuiTikZ options")
        self._opts_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #555;")
        outer.addWidget(self._opts_label)

        self._opts_field = QLineEdit()
        self._opts_field.setPlaceholderText("e.g. l=$R_1$, v=$V_s$")
        self._opts_field.textChanged.connect(self._on_options_changed)
        outer.addWidget(self._opts_field)

        self._hint_label = QLabel()
        self._hint_label.setStyleSheet("color: #888; font-size: 10px;")
        self._hint_label.setWordWrap(True)
        outer.addWidget(self._hint_label)

        # ── Font controls (text_node) ─────────────────────────────────────
        self._font_section = QWidget()
        font_vbox = QVBoxLayout(self._font_section)
        font_vbox.setContentsMargins(0, 0, 0, 0)
        font_vbox.setSpacing(4)

        # Font size.
        fs_row = QHBoxLayout()
        fs_row.setSpacing(6)
        fs_row.addWidget(QLabel("Font size (pt)"))
        self._font_size_spin = QSpinBox()
        self._font_size_spin.setRange(6, 72)
        self._font_size_spin.setValue(12)
        self._font_size_spin.valueChanged.connect(self._on_font_size_changed)
        fs_row.addWidget(self._font_size_spin)
        fs_row.addStretch(1)
        font_vbox.addLayout(fs_row)

        # Bold / italic checkboxes.
        bi_row = QHBoxLayout()
        bi_row.setSpacing(6)
        self._bold_cb = QCheckBox("Bold")
        self._bold_cb.stateChanged.connect(self._on_text_style_changed)
        self._italic_cb = QCheckBox("Italic")
        self._italic_cb.stateChanged.connect(self._on_text_style_changed)
        bi_row.addWidget(self._bold_cb)
        bi_row.addWidget(self._italic_cb)
        bi_row.addStretch(1)
        font_vbox.addLayout(bi_row)

        # Font family combo.
        ff_row = QHBoxLayout()
        ff_row.setSpacing(6)
        ff_row.addWidget(QLabel("Font family"))
        self._font_family_combo = QComboBox()
        for label in ("Default", "Serif", "Sans-serif", "Monospace"):
            self._font_family_combo.addItem(label)
        self._font_family_combo.currentIndexChanged.connect(self._on_text_style_changed)
        ff_row.addWidget(self._font_family_combo, 1)
        font_vbox.addLayout(ff_row)

        outer.addWidget(self._font_section)
        self._font_section.setVisible(False)

        # Keep a backwards-compatible alias used by _set_enabled.
        self._font_size_row = self._font_section

        # ── Rect visual properties ────────────────────────────────────────
        self._rect_section = QWidget()
        rect_vbox = QVBoxLayout(self._rect_section)
        rect_vbox.setContentsMargins(0, 0, 0, 0)
        rect_vbox.setSpacing(4)

        # Line style.
        ls_row = QHBoxLayout()
        ls_row.setSpacing(6)
        ls_row.addWidget(QLabel("Line style"))
        self._line_style_combo = QComboBox()
        for label, _ in _LINE_STYLE_OPTIONS:
            self._line_style_combo.addItem(label)
        self._line_style_combo.currentIndexChanged.connect(self._on_rect_changed)
        ls_row.addWidget(self._line_style_combo, 1)
        rect_vbox.addLayout(ls_row)

        # Line width.
        lw_row = QHBoxLayout()
        lw_row.setSpacing(6)
        lw_row.addWidget(QLabel("Line width (pt)"))
        self._line_width_spin = QDoubleSpinBox()
        self._line_width_spin.setRange(0.1, 10.0)
        self._line_width_spin.setSingleStep(0.2)
        self._line_width_spin.setDecimals(1)
        self._line_width_spin.setValue(0.4)
        self._line_width_spin.valueChanged.connect(self._on_rect_changed)
        lw_row.addWidget(self._line_width_spin, 1)
        rect_vbox.addLayout(lw_row)

        # Fill color.
        fill_row = QHBoxLayout()
        fill_row.setSpacing(6)
        fill_row.addWidget(QLabel("Fill"))
        self._fill_combo = QComboBox()
        for label, _ in _FILL_OPTIONS:
            self._fill_combo.addItem(label)
        self._fill_combo.currentIndexChanged.connect(self._on_rect_changed)
        fill_row.addWidget(self._fill_combo, 1)
        rect_vbox.addLayout(fill_row)

        # Move to front / back buttons.
        layer_row = QHBoxLayout()
        layer_row.setSpacing(6)
        self._move_front_btn = QPushButton("Move to front")
        self._move_front_btn.clicked.connect(self._on_move_to_front)
        self._move_back_btn = QPushButton("Move to back")
        self._move_back_btn.clicked.connect(self._on_move_to_back)
        layer_row.addWidget(self._move_front_btn)
        layer_row.addWidget(self._move_back_btn)
        rect_vbox.addLayout(layer_row)

        outer.addWidget(self._rect_section)
        self._rect_section.setVisible(False)

        # ── Z-order (drawing annotations only) ───────────────────────────
        self._z_order_row = QWidget()
        z_layout = QHBoxLayout(self._z_order_row)
        z_layout.setContentsMargins(0, 0, 0, 0)
        z_layout.setSpacing(6)
        z_layout.addWidget(QLabel("Z-order"))
        self._z_order_spin = QSpinBox()
        self._z_order_spin.setRange(-99, 99)
        self._z_order_spin.setValue(0)
        self._z_order_spin.setToolTip(
            "Negative = behind circuit elements; 0 = default; positive = in front"
        )
        self._z_order_spin.valueChanged.connect(self._on_z_order_changed)
        z_layout.addWidget(self._z_order_spin)
        z_layout.addStretch(1)
        outer.addWidget(self._z_order_row)
        self._z_order_row.setVisible(False)

        # ── Filled variant (diodes) ──────────────────────────────────────
        self._filled_cb = QCheckBox("Filled")
        self._filled_cb.stateChanged.connect(self._on_filled_changed)
        outer.addWidget(self._filled_cb)
        self._filled_cb.setVisible(False)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.HLine)
        sep2.setFrameShadow(QFrame.Sunken)
        outer.addWidget(sep2)

        # ── Rotation / mirror (circuit components only) ───────────────────
        self._rot_section = QWidget()
        rot_vbox = QVBoxLayout(self._rot_section)
        rot_vbox.setContentsMargins(0, 0, 0, 0)
        rot_vbox.setSpacing(4)

        rot_label = QLabel("Rotation")
        rot_label.setStyleSheet("font-weight: bold; font-size: 11px; color: #555;")
        rot_vbox.addWidget(rot_label)

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
        rot_vbox.addLayout(rot_row)

        self._mirror_cb = QCheckBox("Mirror (horizontal)")
        self._mirror_cb.stateChanged.connect(self._on_mirror)
        rot_vbox.addWidget(self._mirror_cb)

        outer.addWidget(self._rot_section)

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
        self._current_kind = comp.kind
        defn = REGISTRY[comp.kind]
        self._header.setText(f"{defn.display_name}\n({comp.kind})")

        is_drawing = isinstance(comp, DrawingComponent)

        # ── Options / text field ─────────────────────────────────────────
        if isinstance(comp, TextNodeComponent):
            self._opts_label.setText("Text content")
            self._opts_field.setPlaceholderText("Your text here")
        elif is_drawing:
            self._opts_label.setText("Options")
            self._opts_field.setPlaceholderText("")
        else:
            self._opts_label.setText("CircuiTikZ options")
            self._opts_field.setPlaceholderText("e.g. l=$R_1$, v=$V_s$")

        self._opts_field.blockSignals(True)
        self._opts_field.setText(comp.options)
        self._opts_field.blockSignals(False)

        self._opts_label.setVisible(not isinstance(comp, RectComponent))
        self._opts_field.setVisible(not isinstance(comp, RectComponent))

        if defn.label_slots:
            self._hint_label.setText("Slots: " + ", ".join(defn.label_slots))
        else:
            self._hint_label.setText("")
        self._hint_label.setVisible(not is_drawing)

        # ── Font controls (text_node) ────────────────────────────────────
        self._font_section.setVisible(isinstance(comp, TextNodeComponent))
        if isinstance(comp, TextNodeComponent):
            self._font_size_spin.blockSignals(True)
            self._font_size_spin.setValue(int(round(comp.font_size)))
            self._font_size_spin.blockSignals(False)

            self._bold_cb.blockSignals(True)
            self._bold_cb.setChecked(comp.font_bold)
            self._bold_cb.blockSignals(False)

            self._italic_cb.blockSignals(True)
            self._italic_cb.setChecked(comp.font_italic)
            self._italic_cb.blockSignals(False)

            _ff_map = {"": 0, "serif": 1, "sans": 2, "mono": 3}
            self._font_family_combo.blockSignals(True)
            self._font_family_combo.setCurrentIndex(_ff_map.get(comp.font_family, 0))
            self._font_family_combo.blockSignals(False)

        # ── Rect visual properties ───────────────────────────────────────
        self._rect_section.setVisible(isinstance(comp, RectComponent))
        if isinstance(comp, RectComponent):
            line_style, line_width, fill = _parse_rect_options(comp.options)

            self._line_style_combo.blockSignals(True)
            label = _TIKZ_TO_LABEL_STYLE.get(line_style, "Solid")
            idx = self._line_style_combo.findText(label)
            self._line_style_combo.setCurrentIndex(max(0, idx))
            self._line_style_combo.blockSignals(False)

            self._line_width_spin.blockSignals(True)
            self._line_width_spin.setValue(line_width)
            self._line_width_spin.blockSignals(False)

            self._fill_combo.blockSignals(True)
            fill_label = _TIKZ_FILL_TO_LABEL.get(fill, "None")
            fidx = self._fill_combo.findText(fill_label)
            self._fill_combo.setCurrentIndex(max(0, fidx))
            self._fill_combo.blockSignals(False)

        # ── Z-order (drawing annotations) ────────────────────────────────
        self._z_order_row.setVisible(is_drawing)
        if is_drawing:
            self._z_order_spin.blockSignals(True)
            self._z_order_spin.setValue(comp.z_order)
            self._z_order_spin.blockSignals(False)

        # ── Filled variant ───────────────────────────────────────────────
        self._filled_cb.setVisible(isinstance(comp, DiodeComponent))
        if isinstance(comp, DiodeComponent):
            self._filled_cb.blockSignals(True)
            self._filled_cb.setChecked(comp.filled)
            self._filled_cb.blockSignals(False)

        # ── Rotation / mirror ────────────────────────────────────────────
        # text_node supports rotation but not mirror; circuit components support both.
        show_rot = not is_drawing or isinstance(comp, TextNodeComponent)
        self._rot_section.setVisible(show_rot)
        self._mirror_cb.setVisible(not isinstance(comp, TextNodeComponent))
        if show_rot:
            btn = self._rot_buttons.get(comp.rotation)
            if btn:
                btn.setChecked(True)
            if not isinstance(comp, TextNodeComponent):
                self._mirror_cb.blockSignals(True)
                self._mirror_cb.setChecked(comp.mirror)
                self._mirror_cb.blockSignals(False)

        self._set_enabled(True)

    def clear(self) -> None:
        """Show 'No selection' state."""
        self._current_comp_id = None
        self._current_kind = None
        self._header.setText("No selection")
        self._opts_field.blockSignals(True)
        self._opts_field.clear()
        self._opts_field.blockSignals(False)
        self._hint_label.setText("")
        self._font_section.setVisible(False)
        self._rect_section.setVisible(False)
        self._z_order_row.setVisible(False)
        self._filled_cb.setVisible(False)
        self._opts_label.setVisible(True)
        self._opts_field.setVisible(True)
        self._hint_label.setVisible(True)
        self._rot_section.setVisible(True)
        self._set_enabled(False)

    def show_multi_select(self, count: int) -> None:
        self._current_comp_id = None
        self._current_kind = None
        self._header.setText(f"{count} components selected")
        self._opts_field.blockSignals(True)
        self._opts_field.clear()
        self._opts_field.blockSignals(False)
        self._hint_label.setText("")
        self._font_section.setVisible(False)
        self._rect_section.setVisible(False)
        self._z_order_row.setVisible(False)
        self._filled_cb.setVisible(False)
        self._opts_label.setVisible(True)
        self._opts_field.setVisible(True)
        self._hint_label.setVisible(True)
        self._rot_section.setVisible(True)
        self._set_enabled(False)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _set_enabled(self, enabled: bool) -> None:
        self._opts_field.setEnabled(enabled)
        self._font_size_spin.setEnabled(enabled)
        self._bold_cb.setEnabled(enabled)
        self._italic_cb.setEnabled(enabled)
        self._font_family_combo.setEnabled(enabled)
        self._line_style_combo.setEnabled(enabled)
        self._line_width_spin.setEnabled(enabled)
        self._fill_combo.setEnabled(enabled)
        self._move_front_btn.setEnabled(enabled)
        self._move_back_btn.setEnabled(enabled)
        self._z_order_spin.setEnabled(enabled)
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

    def _on_font_size_changed(self, value: int) -> None:
        self._font_size_timer.start()

    def _do_commit_font_size(self) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        self._scene.set_text_node_font_size(
            self._current_comp_id, float(self._font_size_spin.value())
        )

    def _on_text_style_changed(self) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        bold = self._bold_cb.isChecked()
        italic = self._italic_cb.isChecked()
        _ff_labels = ["", "serif", "sans", "mono"]
        family = _ff_labels[self._font_family_combo.currentIndex()]
        self._scene.set_text_node_style(self._current_comp_id, bold, italic, family)

    def _on_rect_changed(self) -> None:
        """Any rect visual property changed — recompose and debounce."""
        self._rect_timer.start()

    def _do_commit_rect(self) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        label = self._line_style_combo.currentText()
        tikz_style = _LABEL_TO_TIKZ_STYLE.get(label, "")
        line_width = self._line_width_spin.value()
        fill_label = self._fill_combo.currentText()
        fill = _LABEL_TO_TIKZ_FILL.get(fill_label, "")
        options = _compose_rect_options(tikz_style, line_width, fill)
        self._scene.edit_component_options(self._current_comp_id, options)

    def _on_move_to_front(self) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        z_orders = [
            c.z_order for c in self._scene.schematic.components
            if c.id != self._current_comp_id
        ]
        new_z = (max(z_orders) + 1) if z_orders else 1
        self._scene.set_component_z_order(self._current_comp_id, new_z)
        self._z_order_spin.blockSignals(True)
        self._z_order_spin.setValue(new_z)
        self._z_order_spin.blockSignals(False)

    def _on_move_to_back(self) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        z_orders = [
            c.z_order for c in self._scene.schematic.components
            if c.id != self._current_comp_id
        ]
        new_z = (min(z_orders) - 1) if z_orders else -1
        self._scene.set_component_z_order(self._current_comp_id, new_z)
        self._z_order_spin.blockSignals(True)
        self._z_order_spin.setValue(new_z)
        self._z_order_spin.blockSignals(False)

    def _on_z_order_changed(self, value: int) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        self._scene.set_component_z_order(self._current_comp_id, value)

    def _on_rotate(self, angle: int) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        self._scene.rotate_component(self._current_comp_id, angle)

    def _on_mirror(self, state: int) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        self._scene.mirror_component(self._current_comp_id, bool(state))

    def _on_filled_changed(self, state: int) -> None:
        if self._scene is None or self._current_comp_id is None:
            return
        self._scene.set_component_filled(self._current_comp_id, bool(state))

    def shutdown(self) -> None:
        """Stop background threads.  Call before application exits."""
        pass
