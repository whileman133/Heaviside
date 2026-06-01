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

Panel architecture
------------------
``PropertiesPanel`` is a thin outer shell (header + separator + QStackedWidget).
Each component type gets its own panel subclass that only builds the widgets it
needs:

  _CircuitPanel   – plain circuit components (opts, hint, rotation, mirror)
  _DiodePanel     – _CircuitPanel + filled-variant checkbox
  _MosfetPanel    – _CircuitPanel + body-diode checkbox
  _TextNodePanel  – text content, font controls, z-order, rotation
  _RectPanel      – line style/width/fill, move-to-front/back, z-order

``PropertiesPanel`` maps ``type(comp)`` → panel subclass instance and swaps the
stack to the matching panel on every selection change.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QTimer, Signal
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
    QStackedWidget,
)

from app.canvas.scene import SchematicScene
from app.components.model import BipoleComponent, DiodeComponent, MosfetComponent, RectComponent, TextNodeComponent
from app.components.registry import REGISTRY
from app.schematic.model import Component

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
_TIKZ_FILL_TO_LABEL: dict[str, str] = {tikz: label for label, tikz in _FILL_OPTIONS}


# ── Rect options string helpers ───────────────────────────────────────────────

def _parse_rect_options(options: str) -> tuple[str, float, str]:
    """Parse a rect options string into (line_style, line_width_pt, fill).

    Returns defaults (``""``, 0.4, ``""``) for any missing part.
    """
    opts = options.strip()

    lw_match = re.search(r"line\s+width\s*=\s*([\d.]+)\s*pt", opts)
    line_width = float(lw_match.group(1)) if lw_match else 0.4
    opts_no_lw = re.sub(r",?\s*line\s+width\s*=\s*[\d.]+\s*pt", "", opts).strip(", ")

    fill_match = re.search(r"fill\s*=\s*([^,]+)", opts_no_lw)
    fill = fill_match.group(1).strip() if fill_match else ""
    opts_no_fill = re.sub(r",?\s*fill\s*=\s*[^,]+", "", opts_no_lw).strip(", ")

    line_style = opts_no_fill.strip()
    return line_style, line_width, fill


def _compose_rect_options(line_style: str, line_width: float, fill: str) -> str:
    """Compose a TikZ draw-options string from individual rect properties."""
    parts: list[str] = []
    if line_style:
        parts.append(line_style)
    if abs(line_width - 0.4) > 1e-6:
        lw_str = f"{line_width:.1f}" if line_width == round(line_width, 1) else f"{line_width:.2f}"
        parts.append(f"line width={lw_str}pt")
    if fill:
        parts.append(f"fill={fill}")
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# Shared font-controls widget
# ---------------------------------------------------------------------------

_FF_LABELS = ["", "serif", "sans", "mono"]
_FF_DISPLAY = ["Default", "Serif", "Sans-serif", "Monospace"]
_FF_MAP = {label: i for i, label in enumerate(_FF_LABELS)}


class _FontControls(QWidget):
    """Reusable font-control group: size spinbox, bold/italic checkboxes, family combo.

    Emits ``size_committed(float)`` after a 300 ms debounce when the size spinbox
    changes, and ``style_committed(bool, bool, str)`` immediately when bold, italic,
    or family changes.  Call :meth:`load` to populate without triggering signals.
    """

    size_committed = Signal(float)
    style_committed = Signal(bool, bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        fs_row = QHBoxLayout()
        fs_row.setSpacing(6)
        fs_row.addWidget(QLabel("Size (pt)"))
        self._size_spin = QSpinBox()
        self._size_spin.setRange(4, 72)
        self._size_spin.setValue(12)
        self._size_spin.valueChanged.connect(self._on_size_changed)
        fs_row.addWidget(self._size_spin)
        fs_row.addStretch(1)
        layout.addLayout(fs_row)

        bi_row = QHBoxLayout()
        bi_row.setSpacing(6)
        self._bold_cb = QCheckBox("Bold")
        self._italic_cb = QCheckBox("Italic")
        self._bold_cb.stateChanged.connect(self._emit_style)
        self._italic_cb.stateChanged.connect(self._emit_style)
        bi_row.addWidget(self._bold_cb)
        bi_row.addWidget(self._italic_cb)
        bi_row.addStretch(1)
        layout.addLayout(bi_row)

        ff_row = QHBoxLayout()
        ff_row.setSpacing(6)
        ff_row.addWidget(QLabel("Family"))
        self._family_combo = QComboBox()
        for lbl in _FF_DISPLAY:
            self._family_combo.addItem(lbl)
        self._family_combo.currentIndexChanged.connect(self._emit_style)
        ff_row.addWidget(self._family_combo, 1)
        layout.addLayout(ff_row)

        self._size_timer = QTimer(self)
        self._size_timer.setSingleShot(True)
        self._size_timer.setInterval(300)
        self._size_timer.timeout.connect(self._emit_size)

    def load(self, font_size: float, bold: bool, italic: bool, family: str) -> None:
        """Populate controls from model values without emitting signals."""
        self._size_spin.blockSignals(True)
        self._size_spin.setValue(int(round(font_size)))
        self._size_spin.blockSignals(False)

        for cb, val in ((self._bold_cb, bold), (self._italic_cb, italic)):
            cb.blockSignals(True)
            cb.setChecked(val)
            cb.blockSignals(False)

        self._family_combo.blockSignals(True)
        self._family_combo.setCurrentIndex(_FF_MAP.get(family, 0))
        self._family_combo.blockSignals(False)

    def set_enabled(self, enabled: bool) -> None:
        for w in (self._size_spin, self._bold_cb, self._italic_cb, self._family_combo):
            w.setEnabled(enabled)

    def _on_size_changed(self) -> None:
        self._size_timer.start()

    def _emit_size(self) -> None:
        self.size_committed.emit(float(self._size_spin.value()))

    def _emit_style(self) -> None:
        self.style_committed.emit(
            self._bold_cb.isChecked(),
            self._italic_cb.isChecked(),
            _FF_LABELS[self._family_combo.currentIndex()],
        )


# ---------------------------------------------------------------------------
# Per-type panel base
# ---------------------------------------------------------------------------

class _BasePanel(QWidget):
    """Common interface for all per-type property panels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene: SchematicScene | None = None
        self._current_comp_id: str | None = None

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene

    def show_component(self, comp: Component) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        self._current_comp_id = None


# ---------------------------------------------------------------------------
# Circuit component panel (plain + diode subclass)
# ---------------------------------------------------------------------------

def _make_section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight: bold; font-size: 11px; color: #555;")
    return lbl


_ROT_BTN_WIDTH = 52
_Z_ORDER_TOOLTIP = (
    "Negative = behind circuit elements; 0 = default; positive = in front"
)


def _make_separator() -> QFrame:
    """A sunken horizontal rule used between panel sections."""
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFrameShadow(QFrame.Sunken)
    return sep


def _make_rotation_row(
    owner: QWidget, on_rotate: "Callable[[int], None]"
) -> "tuple[QHBoxLayout, dict[int, QPushButton]]":
    """Build the exclusive 0/90/180/270° rotation button row.

    Returns the row layout and the ``{angle: button}`` map (the caller stores the
    map for selection/enable updates). *on_rotate* is invoked with the angle when
    a button is clicked. The owning widget parents the QButtonGroup so it stays
    alive.
    """
    row = QHBoxLayout()
    row.setSpacing(4)
    buttons: dict[int, QPushButton] = {}
    group = QButtonGroup(owner)
    group.setExclusive(True)
    for angle in (0, 90, 180, 270):
        btn = QPushButton(f"{angle}°")
        btn.setCheckable(True)
        btn.setFixedWidth(_ROT_BTN_WIDTH)
        group.addButton(btn)
        buttons[angle] = btn
        btn.clicked.connect(lambda checked, a=angle: on_rotate(a))
        row.addWidget(btn)
    return row, buttons


def _make_z_order_row(
    on_changed: "Callable[[int], None]"
) -> "tuple[QHBoxLayout, QSpinBox]":
    """Build the "Z-order" label + spin-box row (range -99..99).

    Returns the row layout and the spin box (the caller stores it for value
    updates). *on_changed* is connected to ``valueChanged``.
    """
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(QLabel("Z-order"))
    spin = QSpinBox()
    spin.setRange(-99, 99)
    spin.setToolTip(_Z_ORDER_TOOLTIP)
    spin.valueChanged.connect(on_changed)
    row.addWidget(spin)
    row.addStretch(1)
    return row, spin


class _CircuitPanel(_BasePanel):
    """
    Panel for plain circuit components.

    Layout: options field → hint → separator → rotation buttons → mirror →
    (subclass extras via _build_extra_controls) → stretch.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_make_section_label("CircuiTikZ options"))

        self._opts_field = QLineEdit()
        self._opts_field.setPlaceholderText("e.g. l=$R_1$, v=$V_s$")
        self._opts_field.textChanged.connect(self._on_options_changed)
        layout.addWidget(self._opts_field)

        self._hint_label = QLabel()
        self._hint_label.setStyleSheet("color: #888; font-size: 10px;")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        layout.addWidget(_make_separator())

        layout.addWidget(_make_section_label("Rotation"))

        rot_row, self._rot_buttons = _make_rotation_row(self, self._on_rotate)
        layout.addLayout(rot_row)

        self._mirror_cb = QCheckBox("Mirror (horizontal)")
        self._mirror_cb.stateChanged.connect(self._on_mirror)
        layout.addWidget(self._mirror_cb)

        self._build_extra_controls(layout)
        layout.addStretch(1)

        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(300)
        self._commit_timer.timeout.connect(self._do_commit)

        self._set_enabled(False)

    def _build_extra_controls(self, layout: QVBoxLayout) -> None:
        """Override in subclasses to append extra widgets before the stretch."""

    def show_component(self, comp: Component) -> None:
        self._current_comp_id = comp.id
        defn = REGISTRY[comp.kind]

        self._opts_field.blockSignals(True)
        self._opts_field.setText(comp.options)
        self._opts_field.blockSignals(False)

        self._hint_label.setText(
            "Slots: " + ", ".join(defn.label_slots) if defn.label_slots else ""
        )

        btn = self._rot_buttons.get(comp.rotation)
        if btn:
            btn.setChecked(True)

        self._mirror_cb.blockSignals(True)
        self._mirror_cb.setChecked(comp.mirror)
        self._mirror_cb.blockSignals(False)

        self._set_enabled(True)

    def clear(self) -> None:
        super().clear()
        self._opts_field.blockSignals(True)
        self._opts_field.clear()
        self._opts_field.blockSignals(False)
        self._hint_label.clear()
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        self._opts_field.setEnabled(enabled)
        for btn in self._rot_buttons.values():
            btn.setEnabled(enabled)
        self._mirror_cb.setEnabled(enabled)

    def _on_options_changed(self, text: str) -> None:
        self._commit_timer.start()

    def _do_commit(self) -> None:
        if self._scene and self._current_comp_id:
            self._scene.edit_component_options(
                self._current_comp_id, self._opts_field.text().strip()
            )

    def _on_rotate(self, angle: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.rotate_component(self._current_comp_id, angle)

    def _on_mirror(self, state: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.mirror_component(self._current_comp_id, bool(state))


class _DiodePanel(_CircuitPanel):
    """Circuit panel extended with a filled-variant checkbox."""

    def _build_extra_controls(self, layout: QVBoxLayout) -> None:
        self._filled_cb = QCheckBox("Filled")
        self._filled_cb.stateChanged.connect(self._on_filled_changed)
        layout.addWidget(self._filled_cb)

    def show_component(self, comp: DiodeComponent) -> None:  # type: ignore[override]
        super().show_component(comp)
        self._filled_cb.blockSignals(True)
        self._filled_cb.setChecked(comp.filled)
        self._filled_cb.blockSignals(False)

    def _set_enabled(self, enabled: bool) -> None:
        super()._set_enabled(enabled)
        if hasattr(self, "_filled_cb"):
            self._filled_cb.setEnabled(enabled)

    def _on_filled_changed(self, state: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_component_filled(self._current_comp_id, bool(state))


class _MosfetPanel(_CircuitPanel):
    """Circuit panel extended with a body-diode checkbox."""

    def _build_extra_controls(self, layout: QVBoxLayout) -> None:
        self._body_diode_cb = QCheckBox("Body diode")
        self._body_diode_cb.stateChanged.connect(self._on_body_diode_changed)
        layout.addWidget(self._body_diode_cb)

    def show_component(self, comp: MosfetComponent) -> None:  # type: ignore[override]
        super().show_component(comp)
        self._body_diode_cb.blockSignals(True)
        self._body_diode_cb.setChecked(comp.body_diode)
        self._body_diode_cb.blockSignals(False)

    def _set_enabled(self, enabled: bool) -> None:
        super()._set_enabled(enabled)
        if hasattr(self, "_body_diode_cb"):
            self._body_diode_cb.setEnabled(enabled)

    def _on_body_diode_changed(self, state: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_component_body_diode(self._current_comp_id, bool(state))


# ---------------------------------------------------------------------------
# Text node panel
# ---------------------------------------------------------------------------

class _TextNodePanel(_BasePanel):
    """Panel for text annotations: text content, font controls, z-order, rotation."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_make_section_label("Text content"))

        self._text_field = QLineEdit()
        self._text_field.setPlaceholderText("Your text here")
        self._text_field.textChanged.connect(self._on_text_changed)
        layout.addWidget(self._text_field)

        layout.addWidget(_make_separator())

        layout.addWidget(_make_section_label("Font"))
        self._font_ctrl = _FontControls()
        self._font_ctrl.size_committed.connect(self._on_font_size)
        self._font_ctrl.style_committed.connect(self._on_font_style)
        layout.addWidget(self._font_ctrl)

        layout.addWidget(_make_separator())

        z_row, self._z_order_spin = _make_z_order_row(self._on_z_order_changed)
        layout.addLayout(z_row)

        layout.addWidget(_make_separator())

        layout.addWidget(_make_section_label("Rotation"))

        rot_row, self._rot_buttons = _make_rotation_row(self, self._on_rotate)
        layout.addLayout(rot_row)

        layout.addStretch(1)

        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.setInterval(300)
        self._commit_timer.timeout.connect(self._do_commit_text)

        self._set_enabled(False)

    def show_component(self, comp: TextNodeComponent) -> None:  # type: ignore[override]
        self._current_comp_id = comp.id

        self._text_field.blockSignals(True)
        self._text_field.setText(comp.options)
        self._text_field.blockSignals(False)

        self._font_ctrl.load(comp.font_size, comp.font_bold, comp.font_italic, comp.font_family)

        self._z_order_spin.blockSignals(True)
        self._z_order_spin.setValue(comp.z_order)
        self._z_order_spin.blockSignals(False)

        btn = self._rot_buttons.get(comp.rotation)
        if btn:
            btn.setChecked(True)

        self._set_enabled(True)

    def clear(self) -> None:
        super().clear()
        self._text_field.blockSignals(True)
        self._text_field.clear()
        self._text_field.blockSignals(False)
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        self._text_field.setEnabled(enabled)
        self._font_ctrl.set_enabled(enabled)
        self._z_order_spin.setEnabled(enabled)
        for btn in self._rot_buttons.values():
            btn.setEnabled(enabled)

    def _on_text_changed(self, _text: str) -> None:
        self._commit_timer.start()

    def _do_commit_text(self) -> None:
        if self._scene and self._current_comp_id:
            self._scene.edit_component_options(
                self._current_comp_id, self._text_field.text().strip()
            )

    def _on_font_size(self, size: float) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_font_size(self._current_comp_id, size)

    def _on_font_style(self, bold: bool, italic: bool, family: str) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_font_style(self._current_comp_id, bold, italic, family)

    def _on_z_order_changed(self, value: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_component_z_order(self._current_comp_id, value)

    def _on_rotate(self, angle: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.rotate_component(self._current_comp_id, angle)


# ---------------------------------------------------------------------------
# Rect panel
# ---------------------------------------------------------------------------

class _RectPanel(_BasePanel):
    """Panel for rectangle annotations: line style/width/fill, z-order."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_make_section_label("Rectangle style"))

        ls_row = QHBoxLayout()
        ls_row.setSpacing(6)
        ls_row.addWidget(QLabel("Line style"))
        self._line_style_combo = QComboBox()
        for label, _ in _LINE_STYLE_OPTIONS:
            self._line_style_combo.addItem(label)
        self._line_style_combo.currentIndexChanged.connect(self._on_rect_changed)
        ls_row.addWidget(self._line_style_combo, 1)
        layout.addLayout(ls_row)

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
        layout.addLayout(lw_row)

        fill_row = QHBoxLayout()
        fill_row.setSpacing(6)
        fill_row.addWidget(QLabel("Fill"))
        self._fill_combo = QComboBox()
        for label, _ in _FILL_OPTIONS:
            self._fill_combo.addItem(label)
        self._fill_combo.currentIndexChanged.connect(self._on_rect_changed)
        fill_row.addWidget(self._fill_combo, 1)
        layout.addLayout(fill_row)

        layer_row = QHBoxLayout()
        layer_row.setSpacing(6)
        self._move_front_btn = QPushButton("Move to front")
        self._move_front_btn.clicked.connect(self._on_move_to_front)
        self._move_back_btn = QPushButton("Move to back")
        self._move_back_btn.clicked.connect(self._on_move_to_back)
        layer_row.addWidget(self._move_front_btn)
        layer_row.addWidget(self._move_back_btn)
        layout.addLayout(layer_row)

        layout.addWidget(_make_separator())

        z_row, self._z_order_spin = _make_z_order_row(self._on_z_order_changed)
        layout.addLayout(z_row)

        layout.addStretch(1)

        self._rect_timer = QTimer(self)
        self._rect_timer.setSingleShot(True)
        self._rect_timer.setInterval(300)
        self._rect_timer.timeout.connect(self._do_commit_rect)

        self._set_enabled(False)

    def show_component(self, comp: RectComponent) -> None:  # type: ignore[override]
        self._current_comp_id = comp.id

        line_style, line_width, fill = _parse_rect_options(comp.options)

        self._line_style_combo.blockSignals(True)
        label = _TIKZ_TO_LABEL_STYLE.get(line_style, "Solid")
        self._line_style_combo.setCurrentIndex(max(0, self._line_style_combo.findText(label)))
        self._line_style_combo.blockSignals(False)

        self._line_width_spin.blockSignals(True)
        self._line_width_spin.setValue(line_width)
        self._line_width_spin.blockSignals(False)

        self._fill_combo.blockSignals(True)
        fill_label = _TIKZ_FILL_TO_LABEL.get(fill, "None")
        self._fill_combo.setCurrentIndex(max(0, self._fill_combo.findText(fill_label)))
        self._fill_combo.blockSignals(False)

        self._z_order_spin.blockSignals(True)
        self._z_order_spin.setValue(comp.z_order)
        self._z_order_spin.blockSignals(False)

        self._set_enabled(True)

    def clear(self) -> None:
        super().clear()
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        self._line_style_combo.setEnabled(enabled)
        self._line_width_spin.setEnabled(enabled)
        self._fill_combo.setEnabled(enabled)
        self._move_front_btn.setEnabled(enabled)
        self._move_back_btn.setEnabled(enabled)
        self._z_order_spin.setEnabled(enabled)

    def _on_rect_changed(self) -> None:
        self._rect_timer.start()

    def _do_commit_rect(self) -> None:
        if self._scene and self._current_comp_id:
            tikz_style = _LABEL_TO_TIKZ_STYLE.get(self._line_style_combo.currentText(), "")
            fill = _LABEL_TO_TIKZ_FILL.get(self._fill_combo.currentText(), "")
            options = _compose_rect_options(tikz_style, self._line_width_spin.value(), fill)
            self._scene.edit_component_options(self._current_comp_id, options)

    def _on_move_to_front(self) -> None:
        if self._scene and self._current_comp_id:
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
        if self._scene and self._current_comp_id:
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
        if self._scene and self._current_comp_id:
            self._scene.set_component_z_order(self._current_comp_id, value)


# ---------------------------------------------------------------------------
# Bipole panel helpers
# ---------------------------------------------------------------------------

def _extract_bipole_label(options: str) -> str:
    """Return the value of the t= slot in a bipole options string."""
    m = re.search(r'\bt\s*=\s*([^,]+)', options)
    return m.group(1).strip() if m else ""


def _replace_bipole_label(options: str, label: str) -> str:
    """Replace (or insert) the t= slot in options, returning the new string."""
    stripped = re.sub(r'\bt\s*=\s*[^,]+(,\s*)?', '', options).strip(', ')
    if label:
        return f"t={label}" + (f", {stripped}" if stripped else "")
    return stripped


# ---------------------------------------------------------------------------
# Bipole panel
# ---------------------------------------------------------------------------

class _BipolePanel(_BasePanel):
    """Panel for the bipole: label text, CircuiTikZ options, rotation, mirror."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        layout.addWidget(_make_section_label("Bipole label (t=)"))

        self._label_field = QLineEdit()
        self._label_field.setPlaceholderText("e.g. Processor")
        self._label_field.textChanged.connect(self._on_label_changed)
        layout.addWidget(self._label_field)

        layout.addWidget(_make_separator())

        layout.addWidget(_make_section_label("Other CircuiTikZ options"))

        self._opts_field = QLineEdit()
        self._opts_field.setPlaceholderText("e.g. l=$H(s)$, v=$V_o$")
        self._opts_field.textChanged.connect(self._on_opts_changed)
        layout.addWidget(self._opts_field)

        hint = QLabel("Slots: l, l_, v, v^, i, i_")
        hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(hint)

        layout.addWidget(_make_separator())

        layout.addWidget(_make_section_label("Font"))
        self._font_ctrl = _FontControls()
        self._font_ctrl.size_committed.connect(self._on_font_size)
        self._font_ctrl.style_committed.connect(self._on_font_style)
        layout.addWidget(self._font_ctrl)

        layout.addWidget(_make_separator())

        layout.addWidget(_make_section_label("Appearance"))

        fill_row = QHBoxLayout()
        fill_row.setSpacing(6)
        fill_row.addWidget(QLabel("Fill"))
        self._fill_combo = QComboBox()
        for label, _ in _FILL_OPTIONS:
            self._fill_combo.addItem(label)
        self._fill_combo.currentIndexChanged.connect(self._on_fill_changed)
        fill_row.addWidget(self._fill_combo, 1)
        layout.addLayout(fill_row)

        bw_row = QHBoxLayout()
        bw_row.setSpacing(6)
        bw_row.addWidget(QLabel("Border width (pt)"))
        self._border_width_spin = QDoubleSpinBox()
        self._border_width_spin.setRange(0.1, 10.0)
        self._border_width_spin.setSingleStep(0.2)
        self._border_width_spin.setDecimals(1)
        self._border_width_spin.setValue(0.4)
        self._border_width_spin.valueChanged.connect(self._on_border_width_changed)
        bw_row.addWidget(self._border_width_spin, 1)
        layout.addLayout(bw_row)

        layout.addWidget(_make_separator())

        layout.addWidget(_make_section_label("Rotation"))

        rot_row, self._rot_buttons = _make_rotation_row(self, self._on_rotate)
        layout.addLayout(rot_row)

        self._mirror_cb = QCheckBox("Mirror (horizontal)")
        self._mirror_cb.stateChanged.connect(self._on_mirror)
        layout.addWidget(self._mirror_cb)

        layout.addWidget(_make_separator())

        z_row, self._z_order_spin = _make_z_order_row(self._on_z_order_changed)
        layout.addLayout(z_row)

        layout.addStretch(1)

        self._label_timer = QTimer(self)
        self._label_timer.setSingleShot(True)
        self._label_timer.setInterval(300)
        self._label_timer.timeout.connect(self._do_commit)

        self._opts_timer = QTimer(self)
        self._opts_timer.setSingleShot(True)
        self._opts_timer.setInterval(300)
        self._opts_timer.timeout.connect(self._do_commit)

        self._set_enabled(False)

    def _current_options(self) -> str:
        """Compose the full options string from label + other fields."""
        return _replace_bipole_label(self._opts_field.text().strip(), self._label_field.text().strip())

    def show_component(self, comp: BipoleComponent) -> None:  # type: ignore[override]
        self._current_comp_id = comp.id

        label = _extract_bipole_label(comp.options)
        other = re.sub(r'\bt\s*=\s*[^,]+(,\s*)?', '', comp.options).strip(', ')

        self._label_field.blockSignals(True)
        self._label_field.setText(label)
        self._label_field.blockSignals(False)

        self._opts_field.blockSignals(True)
        self._opts_field.setText(other)
        self._opts_field.blockSignals(False)

        self._font_ctrl.load(comp.font_size, comp.font_bold, comp.font_italic, comp.font_family)

        self._fill_combo.blockSignals(True)
        fill_label = _TIKZ_FILL_TO_LABEL.get(comp.fill_color, "None")
        self._fill_combo.setCurrentIndex(max(0, self._fill_combo.findText(fill_label)))
        self._fill_combo.blockSignals(False)

        self._border_width_spin.blockSignals(True)
        self._border_width_spin.setValue(comp.border_width)
        self._border_width_spin.blockSignals(False)

        btn = self._rot_buttons.get(comp.rotation)
        if btn:
            btn.setChecked(True)

        self._mirror_cb.blockSignals(True)
        self._mirror_cb.setChecked(comp.mirror)
        self._mirror_cb.blockSignals(False)

        self._z_order_spin.blockSignals(True)
        self._z_order_spin.setValue(comp.z_order)
        self._z_order_spin.blockSignals(False)

        self._set_enabled(True)

    def clear(self) -> None:
        super().clear()
        self._label_field.blockSignals(True)
        self._label_field.clear()
        self._label_field.blockSignals(False)
        self._opts_field.blockSignals(True)
        self._opts_field.clear()
        self._opts_field.blockSignals(False)
        self._set_enabled(False)

    def _set_enabled(self, enabled: bool) -> None:
        self._label_field.setEnabled(enabled)
        self._opts_field.setEnabled(enabled)
        self._font_ctrl.set_enabled(enabled)
        self._fill_combo.setEnabled(enabled)
        self._border_width_spin.setEnabled(enabled)
        self._mirror_cb.setEnabled(enabled)
        self._z_order_spin.setEnabled(enabled)
        for btn in self._rot_buttons.values():
            btn.setEnabled(enabled)

    def _on_label_changed(self, _text: str) -> None:
        self._label_timer.start()

    def _on_opts_changed(self, _text: str) -> None:
        self._opts_timer.start()

    def _do_commit(self) -> None:
        if self._scene and self._current_comp_id:
            self._scene.edit_component_options(self._current_comp_id, self._current_options())

    def _on_rotate(self, angle: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.rotate_component(self._current_comp_id, angle)

    def _on_font_size(self, size: float) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_font_size(self._current_comp_id, size)

    def _on_font_style(self, bold: bool, italic: bool, family: str) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_font_style(self._current_comp_id, bold, italic, family)

    def _on_fill_changed(self, _index: int) -> None:
        if self._scene and self._current_comp_id:
            fill = _LABEL_TO_TIKZ_FILL.get(self._fill_combo.currentText(), "")
            self._scene.set_bipole_fill_color(self._current_comp_id, fill)

    def _on_border_width_changed(self, value: float) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_bipole_border_width(self._current_comp_id, value)

    def _on_mirror(self, state: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.mirror_component(self._current_comp_id, bool(state))

    def _on_z_order_changed(self, value: int) -> None:
        if self._scene and self._current_comp_id:
            self._scene.set_component_z_order(self._current_comp_id, value)


# ---------------------------------------------------------------------------
# Outer container
# ---------------------------------------------------------------------------

class PropertiesPanel(QWidget):
    """
    Right-panel properties editor (spec §10.3).

    Thin shell: header label + separator + QStackedWidget.  Each component
    type has a dedicated panel subclass; the stack is swapped on selection.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self._header = QLabel("No selection")
        self._header.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._header.setWordWrap(True)
        outer.addWidget(self._header)

        outer.addWidget(_make_separator())

        self._stack = QStackedWidget()
        outer.addWidget(self._stack)

        self._empty = QWidget()
        self._circuit = _CircuitPanel()
        self._diode = _DiodePanel()
        self._mosfet = _MosfetPanel()
        self._text_node = _TextNodePanel()
        self._rect = _RectPanel()
        self._bipole = _BipolePanel()

        for panel in (self._empty, self._circuit, self._diode, self._mosfet,
                      self._text_node, self._rect, self._bipole):
            self._stack.addWidget(panel)

        self._type_to_panel: dict[type, _BasePanel] = {
            DiodeComponent:    self._diode,
            MosfetComponent:   self._mosfet,
            TextNodeComponent: self._text_node,
            RectComponent:     self._rect,
            BipoleComponent:    self._bipole,
        }

        self._stack.setCurrentWidget(self._empty)

    def set_scene(self, scene: SchematicScene) -> None:
        for panel in (self._circuit, self._diode, self._mosfet, self._text_node, self._rect, self._bipole):
            panel.set_scene(scene)
        self._scene = scene

    def show_component(self, comp_id: str) -> None:
        """Populate the panel for the component with the given id."""
        comp = next(
            (c for c in self._scene.schematic.components if c.id == comp_id), None
        )
        if comp is None:
            self.clear()
            return

        defn = REGISTRY[comp.kind]
        self._header.setText(f"{defn.display_name}\n({comp.kind})")

        panel = self._type_to_panel.get(type(comp), self._circuit)
        self._stack.setCurrentWidget(panel)
        panel.show_component(comp)

    def clear(self) -> None:
        """Show 'No selection' state."""
        self._header.setText("No selection")
        self._stack.setCurrentWidget(self._empty)

    def show_multi_select(self, count: int) -> None:
        self._header.setText(f"{count} components selected")
        self._stack.setCurrentWidget(self._empty)
