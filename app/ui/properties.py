"""
Properties Panel (spec §10.3).

The panel is built from **capability-based inspector sections** rather than one
monolithic panel per component type.  Each :class:`InspectorSection` edits one
capability (CircuiTikZ options, font, fill/border, layer, …) and declares which
components it ``applies_to`` (by ``isinstance`` against the model hierarchy and
its capability mixins ``FontedComponent`` / ``StyledComponent``).

``PropertiesPanel`` holds an ordered list of sections in a scroll area.  On
every selection change it walks the list, ``bind``-ing the sections that apply
to the selected component (which shows them) and ``unbind``-ing the rest (which
hides them).  Adding a component type that is, say, "fonted + filled" needs no
new panel — the existing sections compose automatically.

Section → applicability map:
  OptionsSection      – plain circuit components (not DrawingComponent)
  TextContentSection  – text_node
  BipoleLabelSection  – bipole
  DiodeSection        – diode (filled checkbox)
  MosfetSection       – mosfet (body-diode checkbox)
  FontSection         – FontedComponent (text_node, bipole)
  FillBorderSection   – StyledComponent (rect, bipole)
  TransformSection    – rotation (all but rect, whose rotation is a codegen no-op)
                        + mirror (circuit + bipole only)
  LayerSection        – DrawingComponent (z-order + move front/back)

All edits funnel through SchematicScene methods, which push undoable commands.
"""

from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import Qt, QTimer, Signal
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
    QScrollArea,
    QSizePolicy,
    QButtonGroup,
)

from app.canvas.scene import SchematicScene
from app.components.model import (
    BipoleComponent,
    DiodeComponent,
    DrawingComponent,
    FontedComponent,
    MosfetComponent,
    RectComponent,
    StyledComponent,
    TextNodeComponent,
)
from app.components.registry import REGISTRY
from app.schematic.model import Component

_PANEL_WIDTH = 250

# ── Line style ────────────────────────────────────────────────────────────────
_LINE_STYLE_OPTIONS: list[tuple[str, str]] = [
    ("Solid",     ""),
    ("Dashed",    "dashed"),
    ("Dotted",    "dotted"),
    ("Dash-dot",  "dash dot"),
]
_LABEL_TO_TIKZ_STYLE = {label: tikz for label, tikz in _LINE_STYLE_OPTIONS}
_TIKZ_TO_LABEL_STYLE = {tikz: label for label, tikz in _LINE_STYLE_OPTIONS}

# ── Wire endpoint markers ─────────────────────────────────────────────────────
# Custom decorations a user can place at a wire's start/end point — distinct
# from the automatic junction/termination dots. "Arrow" supports block diagrams.
_WIRE_MARKER_OPTIONS: list[tuple[str, str]] = [
    ("None",       ""),
    ("Arrow",      "arrow"),
    ("Stealth",    "stealth"),
    ("Open arrow", "open"),
    ("Bar",        "bar"),
]
_LABEL_TO_MARKER = {label: kind for label, kind in _WIRE_MARKER_OPTIONS}
_MARKER_TO_LABEL = {kind: label for label, kind in _WIRE_MARKER_OPTIONS}

# ── Fill color ──────────────────────────────────────────────────────────────
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

_ROT_BTN_WIDTH = 52
_Z_ORDER_TOOLTIP = "Negative = behind circuit elements; 0 = default; positive = in front"
_DEBOUNCE_MS = 300


# ---------------------------------------------------------------------------
# Small widget factories (shared across sections)
# ---------------------------------------------------------------------------

def _make_section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet("font-weight: bold; font-size: 11px; color: #555;")
    return lbl


def _make_separator() -> QFrame:
    """A sunken horizontal rule used between panel sections."""
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFrameShadow(QFrame.Sunken)
    return sep


def _make_rotation_row(
    owner: QWidget, on_rotate: Callable[[int], None]
) -> tuple[QHBoxLayout, dict[int, QPushButton]]:
    """Build the exclusive 0/90/180/270° rotation button row.

    Returns the row layout and the ``{angle: button}`` map. *on_rotate* is
    invoked with the angle when a button is clicked. *owner* parents the
    QButtonGroup so it stays alive.
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


def _make_combo_row(
    label: str, items: list[str], on_change: Callable[[int], None]
) -> tuple[QHBoxLayout, QComboBox]:
    """Build a ``label: [combo]`` row populated with *items*."""
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(QLabel(label))
    combo = QComboBox()
    for it in items:
        combo.addItem(it)
    combo.currentIndexChanged.connect(on_change)
    row.addWidget(combo, 1)
    return row, combo


def _make_line_edit_row(
    label: str, placeholder: str, on_change: Callable[[str], None] | None = None
) -> tuple[QHBoxLayout, QLineEdit]:
    """Build a ``label: [line edit]`` row.

    *on_change* (if given) is connected to ``textChanged`` for live updates;
    omit it when the caller wants to commit only on ``editingFinished``.
    """
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(QLabel(label))
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    if on_change is not None:
        field.textChanged.connect(on_change)
    row.addWidget(field, 1)
    return row, field


def _make_double_spin_row(
    label: str, lo: float, hi: float, step: float, decimals: int,
    default: float, on_change: Callable[[float], None],
) -> tuple[QHBoxLayout, QDoubleSpinBox]:
    """Build a ``label: [double spinbox]`` row."""
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(QLabel(label))
    spin = QDoubleSpinBox()
    spin.setRange(lo, hi)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setValue(default)
    spin.valueChanged.connect(on_change)
    row.addWidget(spin, 1)
    return row, spin


def _set_combo(combo: QComboBox, label: str) -> None:
    """Select *label* in *combo* without emitting signals (falls back to index 0)."""
    combo.blockSignals(True)
    combo.setCurrentIndex(max(0, combo.findText(label)))
    combo.blockSignals(False)


# ---------------------------------------------------------------------------
# Shared font-controls widget
# ---------------------------------------------------------------------------

_FF_LABELS = ["", "serif", "sans", "mono"]
_FF_DISPLAY = ["Default", "Serif", "Sans-serif", "Monospace"]
_FF_MAP = {label: i for i, label in enumerate(_FF_LABELS)}


class _FontControls(QWidget):
    """Reusable font-control group: size spinbox, bold/italic checkboxes, family combo.

    Emits ``size_committed(float)`` after a debounce when the size spinbox
    changes, and ``style_committed(bool, bool, str)`` immediately when bold,
    italic, or family changes.  Call :meth:`load` to populate without signals.
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
        self._size_timer.setInterval(_DEBOUNCE_MS)
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
# Bipole label helpers
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
# Inspector section base
# ---------------------------------------------------------------------------

class InspectorSection(QWidget):
    """Self-contained editor for one capability of a component.

    Subclasses set :attr:`title`, build widgets into ``self.body`` in
    :meth:`_build`, implement :meth:`applies_to` and :meth:`_load`, and wire
    their own controls to scene-write callbacks.  The base owns the binding
    lifecycle: :meth:`bind` populates + shows, :meth:`unbind` hides.

    The leading separator is part of the section (so show/hide toggles it with
    the section) but the owning panel hides it on the first visible section via
    :meth:`set_top_separator_visible` to avoid a double rule under the header.
    """

    title: str | None = None

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene: SchematicScene | None = None
        self._comp_id: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._top_sep = _make_separator()
        outer.addWidget(self._top_sep)
        if self.title:
            outer.addWidget(_make_section_label(self.title))
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        outer.addLayout(self.body)

        self._build()
        self.hide()

    # --- subclass contract ----------------------------------------------
    def _build(self) -> None:
        """Construct the section's widgets into ``self.body``."""
        raise NotImplementedError

    def applies_to(self, comp: Component) -> bool:
        raise NotImplementedError

    def _load(self, comp: Component) -> None:
        """Populate widgets from *comp* (signals already safe to block)."""
        raise NotImplementedError

    # --- lifecycle ------------------------------------------------------
    def bind(self, comp: Component, scene: SchematicScene) -> None:
        self._scene = scene
        self._comp_id = comp.id
        self._load(comp)
        self.show()

    def unbind(self) -> None:
        self._comp_id = None
        self.hide()

    def set_top_separator_visible(self, visible: bool) -> None:
        self._top_sep.setVisible(visible)

    # --- helper for write callbacks -------------------------------------
    def _target(self) -> tuple[SchematicScene, str] | None:
        if self._scene is not None and self._comp_id is not None:
            return self._scene, self._comp_id
        return None


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

class OptionsSection(InspectorSection):
    """CircuiTikZ options string + slot hint, for plain circuit components."""

    title = "CircuiTikZ options"

    def _build(self) -> None:
        self._field = QLineEdit()
        self._field.setPlaceholderText("e.g. l=$R_1$, v=$V_s$")
        self._field.textChanged.connect(lambda _t: self._timer.start())
        self.body.addWidget(self._field)

        self._hint = QLabel()
        self._hint.setStyleSheet("color: #888; font-size: 10px;")
        self._hint.setWordWrap(True)
        self.body.addWidget(self._hint)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

    def applies_to(self, comp: Component) -> bool:
        return not isinstance(comp, DrawingComponent)

    def _load(self, comp: Component) -> None:
        self._field.blockSignals(True)
        self._field.setText(comp.options)
        self._field.blockSignals(False)
        slots = REGISTRY[comp.kind].label_slots
        self._hint.setText("Slots: " + ", ".join(slots) if slots else "")

    def _commit(self) -> None:
        t = self._target()
        if t:
            t[0].edit_component_options(t[1], self._field.text().strip())


class TextContentSection(InspectorSection):
    """Text content (stored in ``options``) for text_node."""

    title = "Text content"

    def _build(self) -> None:
        self._field = QLineEdit()
        self._field.setPlaceholderText("Your text here")
        self._field.textChanged.connect(lambda _t: self._timer.start())
        self.body.addWidget(self._field)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, TextNodeComponent)

    def _load(self, comp: Component) -> None:
        self._field.blockSignals(True)
        self._field.setText(comp.options)
        self._field.blockSignals(False)

    def _commit(self) -> None:
        t = self._target()
        if t:
            t[0].edit_component_options(t[1], self._field.text().strip())


class BipoleLabelSection(InspectorSection):
    """Bipole ``t=`` label + other CircuiTikZ options, recomposed into ``options``."""

    title = "Bipole label (t=)"

    def _build(self) -> None:
        self._label_field = QLineEdit()
        self._label_field.setPlaceholderText("e.g. Processor")
        self._label_field.textChanged.connect(lambda _t: self._timer.start())
        self.body.addWidget(self._label_field)

        self.body.addWidget(_make_section_label("Other CircuiTikZ options"))
        self._opts_field = QLineEdit()
        self._opts_field.setPlaceholderText("e.g. l=$H(s)$, v=$V_o$")
        self._opts_field.textChanged.connect(lambda _t: self._timer.start())
        self.body.addWidget(self._opts_field)

        hint = QLabel("Slots: l, l_, v, v^, i, i_")
        hint.setStyleSheet("color: #888; font-size: 10px;")
        self.body.addWidget(hint)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, BipoleComponent)

    def _load(self, comp: Component) -> None:
        label = _extract_bipole_label(comp.options)
        other = re.sub(r'\bt\s*=\s*[^,]+(,\s*)?', '', comp.options).strip(', ')
        for field, val in ((self._label_field, label), (self._opts_field, other)):
            field.blockSignals(True)
            field.setText(val)
            field.blockSignals(False)

    def _commit(self) -> None:
        t = self._target()
        if t:
            options = _replace_bipole_label(
                self._opts_field.text().strip(), self._label_field.text().strip()
            )
            t[0].edit_component_options(t[1], options)


class DiodeSection(InspectorSection):
    """Filled-variant checkbox for diodes."""

    title = None

    def _build(self) -> None:
        self._cb = QCheckBox("Filled")
        self._cb.stateChanged.connect(self._on_changed)
        self.body.addWidget(self._cb)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, DiodeComponent)

    def _load(self, comp: Component) -> None:
        self._cb.blockSignals(True)
        self._cb.setChecked(comp.filled)
        self._cb.blockSignals(False)

    def _on_changed(self, state: int) -> None:
        t = self._target()
        if t:
            t[0].set_component_filled(t[1], bool(state))


class MosfetSection(InspectorSection):
    """Body-diode checkbox for MOSFETs."""

    title = None

    def _build(self) -> None:
        self._cb = QCheckBox("Body diode")
        self._cb.stateChanged.connect(self._on_changed)
        self.body.addWidget(self._cb)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, MosfetComponent)

    def _load(self, comp: Component) -> None:
        self._cb.blockSignals(True)
        self._cb.setChecked(comp.body_diode)
        self._cb.blockSignals(False)

    def _on_changed(self, state: int) -> None:
        t = self._target()
        if t:
            t[0].set_component_body_diode(t[1], bool(state))


class FontSection(InspectorSection):
    """Font controls for any FontedComponent (text_node, bipole)."""

    title = "Font"

    def _build(self) -> None:
        self._font = _FontControls()
        self._font.size_committed.connect(self._on_size)
        self._font.style_committed.connect(self._on_style)
        self.body.addWidget(self._font)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, FontedComponent)

    def _load(self, comp: Component) -> None:
        self._font.load(comp.font_size, comp.font_bold, comp.font_italic, comp.font_family)

    def _on_size(self, size: float) -> None:
        t = self._target()
        if t:
            t[0].set_font_size(t[1], size)

    def _on_style(self, bold: bool, italic: bool, family: str) -> None:
        t = self._target()
        if t:
            t[0].set_font_style(t[1], bold, italic, family)


class FillBorderSection(InspectorSection):
    """Fill color, border width, and line style for any StyledComponent (rect, bipole).

    Reads/writes the StyledComponent fields directly via the generic per-field
    scene setters — no string parsing, no per-type branching.
    """

    title = "Fill & border"

    def _build(self) -> None:
        self._ls_row, self._line_style = _make_combo_row(
            "Line style", [lbl for lbl, _ in _LINE_STYLE_OPTIONS], lambda _i: self._timer.start()
        )
        self.body.addLayout(self._ls_row)

        bw_row, self._width = _make_double_spin_row(
            "Border width (pt)", 0.1, 10.0, 0.2, 1, 0.4, lambda _v: self._timer.start()
        )
        self.body.addLayout(bw_row)

        self._fill_row, self._fill = _make_combo_row(
            "Fill", [lbl for lbl, _ in _FILL_OPTIONS], lambda _i: self._timer.start()
        )
        self.body.addLayout(self._fill_row)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, StyledComponent)

    def _load(self, comp: Component) -> None:
        _set_combo(self._line_style, _TIKZ_TO_LABEL_STYLE.get(comp.line_style, "Solid"))
        self._width.blockSignals(True)
        self._width.setValue(comp.border_width)
        self._width.blockSignals(False)
        _set_combo(self._fill, _TIKZ_FILL_TO_LABEL.get(comp.fill_color, "None"))

    def _commit(self) -> None:
        t = self._target()
        if not t:
            return
        scene, cid = t
        # Per-field undoable commands (no-ops when unchanged).
        scene.set_line_style(cid, _LABEL_TO_TIKZ_STYLE.get(self._line_style.currentText(), ""))
        scene.set_border_width(cid, self._width.value())
        scene.set_fill_color(cid, _LABEL_TO_TIKZ_FILL.get(self._fill.currentText(), ""))


class WireStyleSection(InspectorSection):
    """Line style + width for a selected wire.

    Wires are not Components, so this section binds to a wire id explicitly via
    :meth:`bind_wire` (managed by the panel) rather than the component loop.
    """

    title = "Wire style"

    def _build(self) -> None:
        self._ls_row, self._line_style = _make_combo_row(
            "Line style", [lbl for lbl, _ in _LINE_STYLE_OPTIONS],
            lambda _i: self._timer.start(),
        )
        self.body.addLayout(self._ls_row)

        lw_row, self._width = _make_double_spin_row(
            "Line width (pt)", 0.1, 10.0, 0.2, 1, 0.4, lambda _v: self._timer.start()
        )
        self.body.addLayout(lw_row)

        self._no_dots = QCheckBox("No junction dots")
        self._no_dots.setToolTip(
            "Don't draw connection dots where this wire meets others — use for "
            "annotation wires that aren't real electrical connections."
        )
        self._no_dots.stateChanged.connect(self._on_no_dots)
        self.body.addWidget(self._no_dots)

        self._no_term = QCheckBox("No termination dots")
        self._no_term.setToolTip(
            "Don't draw open-circle terminals at this wire's unconnected ends."
        )
        self._no_term.stateChanged.connect(self._on_no_term)
        self.body.addWidget(self._no_term)

        marker_labels = [lbl for lbl, _ in _WIRE_MARKER_OPTIONS]
        start_row, self._start_marker = _make_combo_row(
            "Start endpoint", marker_labels, lambda _i: self._on_start_marker()
        )
        self.body.addLayout(start_row)
        self._start_marker.setToolTip(
            "Custom decoration at the wire's first point — independent of the "
            "automatic junction/termination dots. Use Arrow for block diagrams."
        )

        end_row, self._end_marker = _make_combo_row(
            "End endpoint", marker_labels, lambda _i: self._on_end_marker()
        )
        self.body.addLayout(end_row)
        self._end_marker.setToolTip(
            "Custom decoration at the wire's last point — independent of the "
            "automatic junction/termination dots. Use Arrow for block diagrams."
        )

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

        # Labels commit on editingFinished (Enter / focus-out), NOT on every
        # keystroke: a live commit re-binds the panel mid-edit, which calls
        # setText and jerks the cursor to the end of the field.
        self.body.addWidget(_make_section_label("Endpoint labels (text / $math$)"))
        start_lbl_row, self._start_label = _make_line_edit_row("Start", "e.g. $x(t)$")
        self._start_label.editingFinished.connect(self._on_start_label)
        self.body.addLayout(start_lbl_row)
        end_lbl_row, self._end_label = _make_line_edit_row("End", "e.g. $y(t)$")
        self._end_label.editingFinished.connect(self._on_end_label)
        self.body.addLayout(end_lbl_row)
        mid_lbl_row, self._mid_label = _make_line_edit_row("Middle", "e.g. $V_{bus}$")
        self._mid_label.setToolTip(
            "Label drawn over the wire with a solid background; drag it along the "
            "wire on the canvas to reposition."
        )
        self._mid_label.editingFinished.connect(self._on_mid_label)
        self.body.addLayout(mid_lbl_row)

        self._wire_id: str | None = None

    def applies_to(self, comp: Component) -> bool:
        return False  # bound explicitly for wires, not via the component loop

    def _load(self, comp: Component) -> None:  # pragma: no cover - never called
        pass

    def bind_wire(self, wire, scene: SchematicScene) -> None:  # noqa: ANN001
        self._scene = scene
        self._wire_id = wire.id
        _set_combo(self._line_style, _TIKZ_TO_LABEL_STYLE.get(wire.line_style, "Solid"))
        self._width.blockSignals(True)
        self._width.setValue(wire.line_width)
        self._width.blockSignals(False)
        self._no_dots.blockSignals(True)
        self._no_dots.setChecked(wire.no_junction_dots)
        self._no_dots.blockSignals(False)
        self._no_term.blockSignals(True)
        self._no_term.setChecked(wire.no_termination_dots)
        self._no_term.blockSignals(False)
        _set_combo(self._start_marker, _MARKER_TO_LABEL.get(wire.start_marker, "None"))
        _set_combo(self._end_marker, _MARKER_TO_LABEL.get(wire.end_marker, "None"))
        # Don't clobber a label field the user is actively editing — re-setting
        # its text would jump the cursor to the end. A re-bind while typing can
        # be triggered by any concurrent schematic change, not just our own.
        if not self._start_label.hasFocus():
            self._start_label.setText(wire.start_label)
        if not self._end_label.hasFocus():
            self._end_label.setText(wire.end_label)
        if not self._mid_label.hasFocus():
            self._mid_label.setText(wire.mid_label)
        self.set_top_separator_visible(False)
        self.show()

    def unbind(self) -> None:
        self._wire_id = None
        self.hide()

    def _commit(self) -> None:
        if self._scene is None or self._wire_id is None:
            return
        self._scene.set_wire_line_style(
            self._wire_id, _LABEL_TO_TIKZ_STYLE.get(self._line_style.currentText(), "")
        )
        self._scene.set_wire_line_width(self._wire_id, self._width.value())

    def _on_no_dots(self, state: int) -> None:
        # Checkbox commits immediately (no debounce), like other boolean toggles.
        if self._scene is not None and self._wire_id is not None:
            self._scene.set_wire_no_junction_dots(self._wire_id, bool(state))

    def _on_no_term(self, state: int) -> None:
        if self._scene is not None and self._wire_id is not None:
            self._scene.set_wire_no_termination_dots(self._wire_id, bool(state))

    def _on_start_marker(self) -> None:
        # Combo selection is a discrete action — commit immediately (no debounce).
        if self._scene is not None and self._wire_id is not None:
            kind = _LABEL_TO_MARKER.get(self._start_marker.currentText(), "")
            self._scene.set_wire_start_marker(self._wire_id, kind)

    def _on_end_marker(self) -> None:
        if self._scene is not None and self._wire_id is not None:
            kind = _LABEL_TO_MARKER.get(self._end_marker.currentText(), "")
            self._scene.set_wire_end_marker(self._wire_id, kind)

    def _on_start_label(self) -> None:
        # editingFinished fires on Enter or focus-out; the scene setter is a
        # no-op when the text is unchanged, so the double-fire is harmless.
        if self._scene is not None and self._wire_id is not None:
            self._scene.set_wire_start_label(self._wire_id, self._start_label.text())

    def _on_end_label(self) -> None:
        if self._scene is not None and self._wire_id is not None:
            self._scene.set_wire_end_label(self._wire_id, self._end_label.text())

    def _on_mid_label(self) -> None:
        if self._scene is not None and self._wire_id is not None:
            self._scene.set_wire_mid_label(self._wire_id, self._mid_label.text())


class TransformSection(InspectorSection):
    """Rotation buttons + mirror checkbox.

    Rotation applies to everything except rect (whose rotation is a codegen
    no-op).  Mirror is only meaningful for path-emitted circuit components and
    the bipole node, so it is shown for those and hidden otherwise.
    """

    title = "Rotation"

    def _build(self) -> None:
        rot_row, self._rot_buttons = _make_rotation_row(self, self._on_rotate)
        self.body.addLayout(rot_row)

        self._mirror_cb = QCheckBox("Mirror (horizontal)")
        self._mirror_cb.stateChanged.connect(self._on_mirror)
        self.body.addWidget(self._mirror_cb)

    def applies_to(self, comp: Component) -> bool:
        return not isinstance(comp, RectComponent)

    @staticmethod
    def _mirror_applies(comp: Component) -> bool:
        return not isinstance(comp, DrawingComponent) or isinstance(comp, BipoleComponent)

    def _load(self, comp: Component) -> None:
        for angle, btn in self._rot_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(angle == comp.rotation)
            btn.blockSignals(False)

        if self._mirror_applies(comp):
            self._mirror_cb.show()
            self._mirror_cb.blockSignals(True)
            self._mirror_cb.setChecked(comp.mirror)
            self._mirror_cb.blockSignals(False)
        else:
            self._mirror_cb.hide()

    def _on_rotate(self, angle: int) -> None:
        t = self._target()
        if t:
            t[0].rotate_component(t[1], angle)

    def _on_mirror(self, state: int) -> None:
        t = self._target()
        if t:
            t[0].mirror_component(t[1], bool(state))


class LayerSection(InspectorSection):
    """Z-order spinbox + move-to-front/back buttons for any DrawingComponent."""

    title = "Layer"

    def _build(self) -> None:
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._front_btn = QPushButton("Move to front")
        self._front_btn.clicked.connect(lambda: self._move(to_front=True))
        self._back_btn = QPushButton("Move to back")
        self._back_btn.clicked.connect(lambda: self._move(to_front=False))
        btn_row.addWidget(self._front_btn)
        btn_row.addWidget(self._back_btn)
        self.body.addLayout(btn_row)

        z_row = QHBoxLayout()
        z_row.setSpacing(6)
        z_row.addWidget(QLabel("Z-order"))
        self._z_spin = QSpinBox()
        self._z_spin.setRange(-99, 99)
        self._z_spin.setToolTip(_Z_ORDER_TOOLTIP)
        self._z_spin.valueChanged.connect(self._on_z_changed)
        z_row.addWidget(self._z_spin)
        z_row.addStretch(1)
        self.body.addLayout(z_row)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, DrawingComponent)

    def _load(self, comp: Component) -> None:
        self._z_spin.blockSignals(True)
        self._z_spin.setValue(comp.z_order)
        self._z_spin.blockSignals(False)

    def _on_z_changed(self, value: int) -> None:
        t = self._target()
        if t:
            t[0].set_component_z_order(t[1], value)

    def _move(self, *, to_front: bool) -> None:
        t = self._target()
        if not t:
            return
        scene, cid = t
        z_orders = [c.z_order for c in scene.schematic.components if c.id != cid]
        if to_front:
            new_z = (max(z_orders) + 1) if z_orders else 1
        else:
            new_z = (min(z_orders) - 1) if z_orders else -1
        scene.set_component_z_order(cid, new_z)
        self._z_spin.blockSignals(True)
        self._z_spin.setValue(new_z)
        self._z_spin.blockSignals(False)


# ---------------------------------------------------------------------------
# Outer container
# ---------------------------------------------------------------------------

class PropertiesPanel(QWidget):
    """
    Right-panel properties editor (spec §10.3).

    Header label + a scrollable column of capability sections.  On selection the
    sections that apply to the component are bound (shown) and the rest unbound
    (hidden); see module docstring for the section → applicability map.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)

        self._scene: SchematicScene | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self._header = QLabel("No selection")
        self._header.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._header.setWordWrap(True)
        outer.addWidget(self._header)

        outer.addWidget(_make_separator())

        # Scrollable column of sections.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        col = QVBoxLayout(content)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)

        # Ordered section list — order is the visual order in the panel.
        self._sections: list[InspectorSection] = [
            OptionsSection(),
            TextContentSection(),
            BipoleLabelSection(),
            DiodeSection(),
            MosfetSection(),
            FontSection(),
            FillBorderSection(),
            TransformSection(),
            LayerSection(),
        ]
        for sec in self._sections:
            col.addWidget(sec)

        # Wire inspector — managed separately (wires are not Components).
        self._wire_section = WireStyleSection()
        col.addWidget(self._wire_section)

        col.addStretch(1)
        scroll.setWidget(content)

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene

    def show_component(self, comp_id: str) -> None:
        """Bind every section that applies to the selected component; hide the rest."""
        comp = None
        if self._scene is not None:
            comp = next(
                (c for c in self._scene.schematic.components if c.id == comp_id), None
            )
        if comp is None or self._scene is None:
            self.clear()
            return

        self._wire_section.unbind()
        defn = REGISTRY[comp.kind]
        self._header.setText(f"{defn.display_name}\n({comp.kind})")

        first_visible = True
        for sec in self._sections:
            if sec.applies_to(comp):
                sec.bind(comp, self._scene)
                sec.set_top_separator_visible(not first_visible)
                first_visible = False
            else:
                sec.unbind()

    def show_wire(self, wire_id: str) -> None:
        """Bind the wire-style inspector for the selected wire."""
        wire = None
        if self._scene is not None:
            wire = next(
                (w for w in self._scene.schematic.wires if w.id == wire_id), None
            )
        if wire is None or self._scene is None:
            self.clear()
            return
        for sec in self._sections:
            sec.unbind()
        self._header.setText("Wire")
        self._wire_section.bind_wire(wire, self._scene)

    def clear(self) -> None:
        """Show 'No selection' state."""
        self._header.setText("No selection")
        for sec in self._sections:
            sec.unbind()
        self._wire_section.unbind()

    def show_multi_select(self, count: int) -> None:
        self._header.setText(f"{count} items selected")
        for sec in self._sections:
            sec.unbind()
        self._wire_section.unbind()
