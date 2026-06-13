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
  VariantSection      – one checkbox per boolean variant the kind declares
                        (e.g. diode "filled", MOSFET "body diode")
  FontSection         – FontedComponent (text_node, bipole)
  FillBorderSection   – StyledComponent (rect, circle, bipole) — fill + line style
  StrokeWidthSection  – the unified stroke/outline width, every kind but text_node
                        (symbols and blocks share one line_width)
  ScaleSection        – logic-gate size multiplier (scale), grid-safe dropdown
  TransformSection    – rotation (all but rect, whose rotation is a codegen no-op)
                        + mirror (circuit + bipole only)
  LayerSection        – DrawingComponent (z-order + move front/back)

All edits funnel through SchematicScene methods, which push undoable commands.
"""

from __future__ import annotations

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
    QFormLayout,
    QPushButton,
    QSpinBox,
    QFrame,
    QScrollArea,
    QSizePolicy,
    QButtonGroup,
)

from app.canvas.commands import SetDocumentPropertiesCommand
from app.canvas.scene import SchematicScene
from app.components.style import split_top_level
from app.ui import theme
from app.components.model import (
    BipoleComponent,
    CircleComponent,
    DrawingComponent,
    FontedComponent,
    RectComponent,
    StyledComponent,
    TextNodeComponent,
)
from app.components.registry import REGISTRY
from app.schematic.model import Component, LABEL_STYLES

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

# Endpoint-label placement: UI label ↔ Wire.*_label_placement value. The two
# side options sit beside the wire (perpendicular), tucked at the endpoint: for a
# horizontal wire that reads as above/below; for a vertical wire as left/right.
_WIRE_LABEL_PLACEMENT_OPTIONS: list[tuple[str, str]] = [
    ("Off end",      ""),
    ("Above / left", "above"),
    ("Below / right", "below"),
]
_LABEL_TO_PLACEMENT = {label: val for label, val in _WIRE_LABEL_PLACEMENT_OPTIONS}
_PLACEMENT_TO_LABEL = {val: label for label, val in _WIRE_LABEL_PLACEMENT_OPTIONS}

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

# Wire hop_mode ↔ tri-state checkbox: dash (partial) = default, empty = never,
# checked = always.
_HOP_STATE_TO_MODE = {
    Qt.PartiallyChecked: "",
    Qt.Unchecked: "never",
    Qt.Checked: "always",
}
_HOP_MODE_TO_STATE = {mode: state for state, mode in _HOP_STATE_TO_MODE.items()}


class _HopModeCheckBox(QCheckBox):
    """Tri-state checkbox whose click cycles default → never → always.

    Maps `Wire.hop_mode`: partially-checked (dash) = "" (follow the global
    line-hops preference and z-order), unchecked = "never", checked = "always".
    The cycle order on click matches that listing (default → no hops → hops).
    """

    _ORDER = (Qt.PartiallyChecked, Qt.Unchecked, Qt.Checked)

    def __init__(self, text: str, parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setTristate(True)

    def nextCheckState(self) -> None:  # noqa: N802
        try:
            i = self._ORDER.index(self.checkState())
        except ValueError:
            i = 0
        self.setCheckState(self._ORDER[(i + 1) % len(self._ORDER)])


# ---------------------------------------------------------------------------
# Small widget factories (shared across sections)
# ---------------------------------------------------------------------------

def _make_section_label(text: str) -> QLabel:
    lbl = QLabel(text)
    # Tagged so PropertiesPanel.apply_theme can re-ink it on a light/dark swap
    # (a stylesheet pins the colour, so it would otherwise not follow the theme).
    lbl.setObjectName("sectionLabel")
    lbl.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {theme.ICON};")
    return lbl


def _make_separator() -> QFrame:
    """A sunken horizontal rule used between panel sections."""
    sep = QFrame()
    sep.setFrameShape(QFrame.HLine)
    sep.setFrameShadow(QFrame.Sunken)
    return sep


def _reink_themed_labels(widget: QWidget) -> None:
    """Re-apply the themed colour to every tagged label under *widget* on a
    light/dark swap. A stylesheet pins each label's colour (so it won't follow the
    window palette), so the header / section / hint labels are restyled in place by
    object-name tag. Field-row labels carry no stylesheet and follow the palette."""
    for lbl in widget.findChildren(QLabel):
        name = lbl.objectName()
        if name == "headerLabel":
            lbl.setStyleSheet(f"font-weight: bold; font-size: 13px; color: {theme.TEXT};")
        elif name == "sectionLabel":
            lbl.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {theme.ICON};")
        elif name == "hintLabel":
            lbl.setStyleSheet(f"color: {theme.ICON_MUTED}; font-size: 10px;")


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


def _make_line_edit_combo_row(
    label: str, placeholder: str, items: list[str]
) -> tuple[QHBoxLayout, QLineEdit, QComboBox]:
    """Build a ``label: [line edit] [combo]`` row (text + selector side-by-side).

    The caller connects the field's ``editingFinished`` and the combo's
    ``currentIndexChanged`` itself. The field gets the larger stretch.
    """
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(QLabel(label))
    field = QLineEdit()
    field.setPlaceholderText(placeholder)
    row.addWidget(field, 2)
    combo = QComboBox()
    for it in items:
        combo.addItem(it)
    row.addWidget(combo, 1)
    return row, field, combo


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


def _set_combo_value(
    combo: QComboBox, value: str, value_to_label: dict[str, str]
) -> None:
    """Select the item matching the model *value* without emitting signals.

    A value the combo's preset list cannot represent (e.g. a hand-authored
    ``fill=red!40`` in the .hv file) is **inserted as an extra item** showing the
    raw value, so a later section commit round-trips it instead of silently
    overwriting it with the index-0 preset. The extra item is removed again when
    the loaded value is representable. Commit paths must map an unknown display
    text back to itself (``mapping.get(text, text)``).
    """
    combo.blockSignals(True)
    # Drop any stale extra item from a previous unrepresentable value.
    for i in range(combo.count() - 1, -1, -1):
        if combo.itemData(i, Qt.UserRole) is True:
            combo.removeItem(i)
    label = value_to_label.get(value)
    if label is None:
        combo.addItem(value)
        idx = combo.count() - 1
        combo.setItemData(idx, True, Qt.UserRole)   # marks the extra item
        combo.setCurrentIndex(idx)
    else:
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

    def flush_pending(self) -> None:
        """Commit a debounced size edit that is still pending, if any."""
        if self._size_timer.isActive():
            self._size_timer.stop()
            self._emit_size()

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

def _is_t_slot(segment: str) -> bool:
    """True when a top-level option *segment* is the bipole ``t=…`` slot."""
    key, eq, _val = segment.partition("=")
    return bool(eq) and key.strip() == "t"


def _extract_bipole_label(options: str) -> str:
    """Return the value of the t= slot in a bipole options string.

    Splits at the **top level** (``split_top_level``) so a label containing a
    comma inside math/braces — e.g. ``t=$f(a,b)$`` — is returned whole rather
    than truncated at the comma.
    """
    for seg in split_top_level(options):
        if _is_t_slot(seg):
            return seg.partition("=")[2].strip()
    return ""


def _strip_bipole_label(options: str) -> str:
    """*options* with the ``t=`` slot removed (top-level comma aware)."""
    rest = [s.strip() for s in split_top_level(options)
            if s.strip() and not _is_t_slot(s)]
    return ", ".join(rest)


def _replace_bipole_label(options: str, label: str) -> str:
    """Replace (or insert) the t= slot in options, returning the new string."""
    stripped = _strip_bipole_label(options)
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

    # Whether this section is safe to bind across a **mixed-kind** multi-selection
    # (e.g. a resistor + a capacitor). True only for sections that edit a uniform,
    # kind-independent capability field (font, fill/border, stroke, rotation,
    # layer). Sections that edit kind-specific free-text (options, t= label, text
    # content) or kind-structural state (variants, param count) stay False — they
    # bind only when the whole selection shares one kind. See
    # ``PropertiesPanel.show_components``.
    multi_kind_safe: bool = False

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene: SchematicScene | None = None
        # Bound component id(s). Single-select binds one; multi-select (all of the
        # same kind) binds several and an edit applies to all (see _apply). Reads
        # (_load / _target) use the first as the representative.
        self._comp_ids: list[str] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self._top_sep = _make_separator()
        outer.addWidget(self._top_sep)
        self._title_label = _make_section_label(self.title) if self.title else None
        if self._title_label is not None:
            outer.addWidget(self._title_label)
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
        self._comp_ids = [comp.id]
        self._load(comp)
        self.show()

    def bind_multi(self, comps: list[Component], scene: SchematicScene) -> None:
        """Bind several components (all the same kind); edits apply to all of them.

        Widgets load from the first as the representative — values may differ
        across the selection, but editing a field writes it to every component.
        """
        self._scene = scene
        self._comp_ids = [c.id for c in comps]
        self._load(comps[0])
        self.show()

    def unbind(self) -> None:
        # Commit any debounced edit still pending FIRST: clearing _comp_ids
        # before the timer fired would silently drop the user's last keystrokes
        # (and a save could then serialise stale state).
        self.flush_pending_edits()
        self._comp_ids = []
        self.hide()

    def flush_pending_edits(self) -> None:
        """Commit a debounced edit whose timer is still pending, if any.

        The convention across sections is a single-shot ``self._timer`` whose
        timeout calls ``self._commit``; flush by stopping the timer and
        committing immediately. Sections with extra debounced controls (e.g.
        :class:`FontSection`) extend this.
        """
        timer = getattr(self, "_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
            commit = getattr(self, "_commit", None)
            if callable(commit):
                commit()

    def set_top_separator_visible(self, visible: bool) -> None:
        self._top_sep.setVisible(visible)

    # --- helper for write callbacks -------------------------------------
    def _target(self) -> tuple[SchematicScene, str] | None:
        """Scene + the representative (first) component id, for reads."""
        if self._scene is not None and self._comp_ids:
            return self._scene, self._comp_ids[0]
        return None

    def _apply(self, label: str, fn) -> None:  # noqa: ANN001
        """Apply ``fn(scene, comp_id)`` to every bound component as one undo step.

        Wraps the edits in ``scene.batch`` so a multi-select change is a single
        MacroCommand; for a single-select it is just that one command.
        """
        if self._scene is None or not self._comp_ids:
            return
        with self._scene.batch(label):
            for cid in self._comp_ids:
                fn(self._scene, cid)


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
        self._hint.setObjectName("hintLabel")
        self._hint.setStyleSheet(f"color: {theme.ICON_MUTED}; font-size: 10px;")
        self._hint.setWordWrap(True)
        self.body.addWidget(self._hint)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

    def applies_to(self, comp: Component) -> bool:
        return not isinstance(comp, DrawingComponent)

    def _load(self, comp: Component) -> None:
        # Don't clobber a field the user is actively typing in — a programmatic
        # reload (any concurrent schematic change re-binds the section) would
        # replace the text and jump the cursor to the end.
        if not self._field.hasFocus():
            self._field.blockSignals(True)
            self._field.setText(comp.options)
            self._field.blockSignals(False)
        slots = REGISTRY[comp.kind].label_slots
        self._hint.setText("Slots: " + ", ".join(slots) if slots else "")

    def _commit(self) -> None:
        text = self._field.text().strip()
        self._apply("Options", lambda s, cid: s.edit_component_options(cid, text))


class TextContentSection(InspectorSection):
    """Text content (stored in ``options``) for text_node and rect."""

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
        return isinstance(comp, (TextNodeComponent, RectComponent, CircleComponent))

    def _load(self, comp: Component) -> None:
        # Same focus guard as OptionsSection: don't clobber in-progress typing.
        if not self._field.hasFocus():
            self._field.blockSignals(True)
            self._field.setText(comp.options)
            self._field.blockSignals(False)

    def _commit(self) -> None:
        text = self._field.text().strip()
        self._apply("Options", lambda s, cid: s.edit_component_options(cid, text))


class BipoleLabelSection(InspectorSection):
    """Bipole ``t=`` label + other CircuiTikZ options, recomposed into ``options``."""

    title = "Bipole label (t=)"

    def _build(self) -> None:
        self._label_field = QLineEdit()
        self._label_field.setPlaceholderText("e.g. Processor")
        self._label_field.textChanged.connect(lambda _t: self._timer.start())
        self.body.addWidget(self._label_field)

        self._opts_sublabel = _make_section_label("Other CircuiTikZ options")
        self.body.addWidget(self._opts_sublabel)
        self._opts_field = QLineEdit()
        self._opts_field.setPlaceholderText("e.g. l=$H(s)$, v=$V_o$")
        self._opts_field.textChanged.connect(lambda _t: self._timer.start())
        self.body.addWidget(self._opts_field)

        self._hint = QLabel("Slots: l, l_, v, v^, i, i_")
        self._hint.setObjectName("hintLabel")
        self._hint.setStyleSheet(f"color: {theme.ICON_MUTED}; font-size: 10px;")
        self.body.addWidget(self._hint)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, BipoleComponent)

    def _load(self, comp: Component) -> None:
        label = _extract_bipole_label(comp.options)
        other = _strip_bipole_label(comp.options)
        for field, val in ((self._label_field, label), (self._opts_field, other)):
            # Same focus guard as OptionsSection: don't clobber in-progress typing.
            if field.hasFocus():
                continue
            field.blockSignals(True)
            field.setText(val)
            field.blockSignals(False)

    def _commit(self) -> None:
        options = _replace_bipole_label(
            self._opts_field.text().strip(), self._label_field.text().strip()
        )
        self._apply("Label", lambda s, cid: s.edit_component_options(cid, options))


class VariantSection(InspectorSection):
    """A checkbox per boolean variant the component's *kind* declares.

    Generic over any variant in ``components/definitions.json`` (e.g. a diode's
    ``filled``, a MOSFET's ``body_diode``).  The checkboxes are rebuilt on
    :meth:`_load` because the set of variants depends on the component's kind.
    """

    title = None

    def _build(self) -> None:
        self._checks: dict[str, "QCheckBox"] = {}
        self._container = QVBoxLayout()
        self._container.setSpacing(6)
        self.body.addLayout(self._container)

    def applies_to(self, comp: Component) -> bool:
        from app.components import library
        return bool(library.variant_specs(comp.kind))

    def _load(self, comp: Component) -> None:
        from app.components import library
        for cb in self._checks.values():
            cb.setParent(None)
            cb.deleteLater()
        self._checks.clear()
        for v in library.variant_specs(comp.kind):
            name = v["name"]
            cb = QCheckBox(v.get("label") or name.replace("_", " ").capitalize())
            cb.setChecked(bool(comp.variants.get(name)))
            cb.stateChanged.connect(lambda state, n=name: self._on_changed(n, state))
            self._container.addWidget(cb)
            self._checks[name] = cb

    def _on_changed(self, name: str, state: int) -> None:
        on = bool(state)
        self._apply("Variant", lambda s, cid: s.set_component_variant(cid, name, on))


class ParamSection(InspectorSection):
    """One spinbox per integer parameter a *parametric* kind declares — a logic
    gate's input count, or a mux/demux's data-line and select-line counts.
    Generic over the ``param``/``params`` block(s) in
    ``components/definitions.json``; rebuilt on :meth:`_load` per kind."""

    title = None

    def _build(self) -> None:
        # One spinbox per parameter, keyed by parameter name.
        self._spins: "dict[str, QSpinBox]" = {}
        self._rows: "list[QWidget]" = []
        self._kind: str | None = None
        self._container = QVBoxLayout()
        self._container.setSpacing(6)
        self.body.addLayout(self._container)

    def applies_to(self, comp: Component) -> bool:
        from app.components import library
        return library.is_parametric(comp.kind)

    def _load(self, comp: Component) -> None:
        from app.components import library
        specs = library.param_specs(comp.kind)
        if not specs:
            self._teardown()
            return
        values = library.param_values(comp)
        # Same kind: just refresh the values.  Rebuilding on every re-bind (which
        # happens after each spinner step, via the SetParamCommand) would leak
        # duplicate labels/spinboxes each time.
        if self._kind == comp.kind and self._spins:
            for name, spin in self._spins.items():
                spin.blockSignals(True)
                spin.setValue(values[name])
                spin.blockSignals(False)
            return
        self._teardown()
        self._kind = comp.kind
        for spec in specs:
            name = spec["name"]
            row_w = QWidget()
            row = QHBoxLayout(row_w)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(QLabel(name.capitalize()))
            spin = QSpinBox()
            spin.setRange(int(spec["min"]), int(spec["max"]))
            spin.setValue(values[name])
            spin.valueChanged.connect(lambda v, nm=name: self._on_changed(nm, v))
            row.addWidget(spin)
            self._container.addWidget(row_w)
            self._rows.append(row_w)
            self._spins[name] = spin

    def _teardown(self) -> None:
        for row in self._rows:
            row.setParent(None)
            row.deleteLater()
        self._rows = []
        self._spins = {}
        self._kind = None

    def _on_changed(self, name: str, value: int) -> None:
        n = int(value)
        self._apply(name.capitalize(),
                    lambda s, cid: s.set_component_param(cid, name, n))


class FontSection(InspectorSection):
    """Font controls for any FontedComponent (text_node, bipole)."""

    title = "Font"
    multi_kind_safe = True

    def _build(self) -> None:
        self._font = _FontControls()
        self._font.size_committed.connect(self._on_size)
        self._font.style_committed.connect(self._on_style)
        self.body.addWidget(self._font)

    def applies_to(self, comp: Component) -> bool:
        return isinstance(comp, FontedComponent)

    def _load(self, comp: Component) -> None:
        self._font.load(comp.font_size, comp.font_bold, comp.font_italic, comp.font_family)

    def flush_pending_edits(self) -> None:
        # The debounce lives inside _FontControls (size spinbox), not a
        # section-level _timer; flush it while _comp_ids is still bound.
        if self._comp_ids:
            self._font.flush_pending()

    def _on_size(self, size: float) -> None:
        self._apply("Font size", lambda s, cid: s.set_font_size(cid, size))

    def _on_style(self, bold: bool, italic: bool, family: str) -> None:
        self._apply("Font style", lambda s, cid: s.set_font_style(cid, bold, italic, family))


class FillBorderSection(InspectorSection):
    """Fill color + line style for any StyledComponent (rect, circle, bipole).

    Reads/writes the StyledComponent fields directly via the generic per-field
    scene setters — no string parsing, no per-type branching. The outline
    **width** is edited by the shared :class:`StrokeWidthSection` (the same
    control circuit symbols use), so it is not duplicated here.
    """

    title = "Fill & line style"
    multi_kind_safe = True

    def _build(self) -> None:
        self._ls_row, self._line_style = _make_combo_row(
            "Line style", [lbl for lbl, _ in _LINE_STYLE_OPTIONS], lambda _i: self._timer.start()
        )
        self.body.addLayout(self._ls_row)

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
        # Unrepresentable values (e.g. a hand-authored fill=red!40) are inserted
        # as extra items so an unrelated edit in this section round-trips them.
        _set_combo_value(self._line_style, comp.line_style, _TIKZ_TO_LABEL_STYLE)
        _set_combo_value(self._fill, comp.fill_color, _TIKZ_FILL_TO_LABEL)

    def _commit(self) -> None:
        # Per-field undoable commands (no-ops when unchanged); for a multi-select
        # both fields across all components collapse into one undo step. An extra
        # item's display text IS the raw TikZ value, so it maps to itself.
        style_text = self._line_style.currentText()
        fill_text = self._fill.currentText()
        style = _LABEL_TO_TIKZ_STYLE.get(style_text, style_text)
        fill = _LABEL_TO_TIKZ_FILL.get(fill_text, fill_text)

        def apply(s, cid):  # noqa: ANN001
            s.set_line_style(cid, style)
            s.set_fill_color(cid, fill)

        self._apply("Style", apply)


class StrokeWidthSection(InspectorSection):
    """Unified stroke/outline width (``line_width``, pt) for any drawable kind.

    This is the single width control for **both** circuit symbols and block
    components (rect/circle/bipole) — `border_width` was merged into
    ``line_width`` — so a mixed selection of, say, a resistor and a rectangle can
    set their widths together. It applies to every kind except pure text
    (``text_node``), which has no stroke.
    """

    title = "Stroke"
    multi_kind_safe = True

    def _build(self) -> None:
        sw_row, self._width = _make_double_spin_row(
            "Stroke width (pt)", 0.1, 10.0, 0.2, 1, 0.4, lambda _v: self._timer.start()
        )
        self.body.addLayout(sw_row)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

    def applies_to(self, comp: Component) -> bool:
        return not isinstance(comp, TextNodeComponent)

    def _load(self, comp: Component) -> None:
        self._width.blockSignals(True)
        self._width.setValue(comp.line_width)
        self._width.blockSignals(False)

    def _commit(self) -> None:
        width = self._width.value()
        self._apply(
            "Stroke width", lambda s, cid: s.set_component_line_width(cid, width)
        )


class ScaleSection(InspectorSection):
    """Size multiplier (``Component.scale``) as a dropdown, for scalable kinds —
    logic gates and the digital blocks (flip-flops, mux/demux, ALU, adder).

    A scalable symbol's pins are grid-aligned (best-effort) at 100 %; other
    multipliers may push them off-grid, where a wire still connects via the pin
    magnet (so any choice stays wire-connectable).
    """

    title = "Size"
    multi_kind_safe = True

    def _build(self) -> None:
        self._row, self._combo = _make_combo_row("Scale", [], lambda _i: self._commit())
        self.body.addLayout(self._row)
        self._values: list[float] = []

    def applies_to(self, comp: Component) -> bool:
        from app.components import library
        return library.is_scalable(comp.kind)

    def _load(self, comp: Component) -> None:
        from app.components import library
        self._values = library.gate_scale_options(comp.kind)
        self._combo.blockSignals(True)
        self._combo.clear()
        for v in self._values:
            self._combo.addItem(f"{int(round(v * 100))}%")
        if self._values:
            idx = min(range(len(self._values)),
                      key=lambda i: abs(self._values[i] - comp.scale))
            self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)

    def _commit(self) -> None:
        i = self._combo.currentIndex()
        if 0 <= i < len(self._values):
            value = self._values[i]
            self._apply("Scale", lambda s, cid: s.set_component_scale(cid, value))


class WireStyleSection(InspectorSection):
    """All properties of a selected wire, grouped into labelled blocks.

    Wires are not Components, so this section binds to a wire id explicitly via
    :meth:`bind_wire` (managed by the panel) rather than the component loop. The
    panel header already reads "Wire", so this section has no separate title and
    organises its controls under sub-headers: Line, Endpoint arrows, Endpoint
    labels, Connection dots.
    """

    title = None  # panel header already says "Wire"; use sub-headers below

    _PLACEMENT_TOOLTIP = (
        "Where the label sits: Off end (beyond the endpoint along the wire), or "
        "tucked beside the wire at the endpoint — above/below a horizontal wire, "
        "left/right of a vertical wire."
    )

    def _build(self) -> None:
        marker_labels = [lbl for lbl, _ in _WIRE_MARKER_OPTIONS]
        placement_labels = [lbl for lbl, _ in _WIRE_LABEL_PLACEMENT_OPTIONS]

        # The line-style combo and width spinbox debounce; labels commit on
        # editingFinished (Enter / focus-out), and combos/checkboxes immediately.
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(_DEBOUNCE_MS)
        self._timer.timeout.connect(self._commit)

        # — Line —
        self.body.addWidget(_make_section_label("Line"))
        ls_row, self._line_style = _make_combo_row(
            "Style", [lbl for lbl, _ in _LINE_STYLE_OPTIONS],
            lambda _i: self._timer.start(),
        )
        self.body.addLayout(ls_row)
        lw_row, self._width = _make_double_spin_row(
            "Width (pt)", 0.1, 10.0, 0.2, 1, 0.4, lambda _v: self._timer.start()
        )
        self.body.addLayout(lw_row)

        # — Endpoint arrows —
        self.body.addWidget(_make_section_label("Endpoint arrows"))
        start_row, self._start_marker = _make_combo_row(
            "Start", marker_labels, lambda _i: self._on_start_marker()
        )
        self.body.addLayout(start_row)
        end_row, self._end_marker = _make_combo_row(
            "End", marker_labels, lambda _i: self._on_end_marker()
        )
        self.body.addLayout(end_row)
        for combo in (self._start_marker, self._end_marker):
            combo.setToolTip(
                "Arrowhead/terminal at this wire end — independent of the "
                "automatic junction/termination dots. Use Arrow for block "
                "diagrams. Tab on the canvas cycles it (incl. ends on a rect/"
                "circle)."
            )

        # — Endpoint labels — (text + position selector side-by-side)
        self.body.addWidget(_make_section_label("Endpoint labels (text / $math$)"))
        srow, self._start_label, self._start_label_pos = _make_line_edit_combo_row(
            "Start", "e.g. $x(t)$", placement_labels
        )
        self._start_label.editingFinished.connect(self._on_start_label)
        self._start_label_pos.currentIndexChanged.connect(
            lambda _i: self._on_start_label_placement()
        )
        self._start_label_pos.setToolTip(self._PLACEMENT_TOOLTIP)
        self.body.addLayout(srow)

        erow, self._end_label, self._end_label_pos = _make_line_edit_combo_row(
            "End", "e.g. $y(t)$", placement_labels
        )
        self._end_label.editingFinished.connect(self._on_end_label)
        self._end_label_pos.currentIndexChanged.connect(
            lambda _i: self._on_end_label_placement()
        )
        self._end_label_pos.setToolTip(self._PLACEMENT_TOOLTIP)
        self.body.addLayout(erow)

        mid_row, self._mid_label = _make_line_edit_row("Middle", "e.g. $V_{bus}$")
        self._mid_label.setToolTip(
            "Label drawn over the wire with a solid background; drag it along the "
            "wire on the canvas to reposition (or double-click the wire to edit)."
        )
        self._mid_label.editingFinished.connect(self._on_mid_label)
        self.body.addLayout(mid_row)

        # — Connection dots —
        self.body.addWidget(_make_section_label("Connection dots"))
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

        # — Layer —
        self.body.addWidget(_make_section_label("Layer"))
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
        self._z_spin.setToolTip(
            _Z_ORDER_TOOLTIP
            + ". Also decides which wire hops at a crossing: the higher z-order "
            "wire arcs over the other."
        )
        self._z_spin.valueChanged.connect(self._on_z_changed)
        z_row.addWidget(self._z_spin)
        z_row.addStretch(1)
        self.body.addLayout(z_row)

        self._hop_mode = _HopModeCheckBox("Line hops")
        self._hop_mode.setToolTip(
            "Per-wire line-hops (click to cycle):\n"
            "• Dash — follow the global Line-hops preference and z-order (default)\n"
            "• Unchecked — never hop (a crossing wire may still hop over this one)\n"
            "• Checked — always hop over crossing wires (ignores the global "
            "preference and z-order)"
        )
        self._hop_mode.stateChanged.connect(self._on_hop_mode)
        self.body.addWidget(self._hop_mode)

        # Bound wire id(s). Single-select binds one; multi-select binds several
        # and an edit applies to all of them as one undo step (see _apply_wires).
        # Reads (bind_wires / _move) use the first as the representative.
        self._wire_ids: list[str] = []

    def applies_to(self, comp: Component) -> bool:
        return False  # bound explicitly for wires, not via the component loop

    def _load(self, comp: Component) -> None:  # pragma: no cover - never called
        pass

    def _apply_wires(self, label: str, fn) -> None:  # noqa: ANN001
        """Apply ``fn(scene, wire_id)`` to every bound wire as one undo step."""
        if self._scene is None or not self._wire_ids:
            return
        with self._scene.batch(label):
            for wid in self._wire_ids:
                fn(self._scene, wid)

    def bind_wire(self, wire, scene: SchematicScene) -> None:  # noqa: ANN001
        self.bind_wires([wire], scene)

    def bind_wires(self, wires: list, scene: SchematicScene) -> None:  # noqa: ANN001
        """Bind one or more wires; edits apply to all of them. Widgets load from
        the first wire as the representative (values may differ across the
        selection, but editing a field writes it to every bound wire)."""
        self._scene = scene
        self._wire_ids = [w.id for w in wires]
        wire = wires[0]
        _set_combo_value(self._line_style, wire.line_style, _TIKZ_TO_LABEL_STYLE)
        self._width.blockSignals(True)
        self._width.setValue(wire.line_width)
        self._width.blockSignals(False)
        self._no_dots.blockSignals(True)
        self._no_dots.setChecked(wire.no_junction_dots)
        self._no_dots.blockSignals(False)
        self._no_term.blockSignals(True)
        self._no_term.setChecked(wire.no_termination_dots)
        self._no_term.blockSignals(False)
        self._z_spin.blockSignals(True)
        self._z_spin.setValue(wire.z_order)
        self._z_spin.blockSignals(False)
        self._hop_mode.blockSignals(True)
        self._hop_mode.setCheckState(
            _HOP_MODE_TO_STATE.get(wire.hop_mode, Qt.PartiallyChecked)
        )
        self._hop_mode.blockSignals(False)
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
        _set_combo(
            self._start_label_pos,
            _PLACEMENT_TO_LABEL.get(wire.start_label_placement, "Off end"),
        )
        _set_combo(
            self._end_label_pos,
            _PLACEMENT_TO_LABEL.get(wire.end_label_placement, "Off end"),
        )
        self.set_top_separator_visible(False)
        self.show()

    def unbind(self) -> None:
        # Flush a pending debounced edit before the wire ids are cleared (same
        # data-loss guard as the component sections).
        self.flush_pending_edits()
        self._wire_ids = []
        self.hide()

    def flush_pending_edits(self) -> None:
        if self._timer.isActive():
            self._timer.stop()
            self._commit()

    def _commit(self) -> None:
        style_text = self._line_style.currentText()
        style = _LABEL_TO_TIKZ_STYLE.get(style_text, style_text)
        width = self._width.value()

        def fn(s, wid):  # noqa: ANN001
            s.set_wire_line_style(wid, style)
            s.set_wire_line_width(wid, width)

        self._apply_wires("Wire style", fn)

    def _on_no_dots(self, state: int) -> None:
        # Checkbox commits immediately (no debounce), like other boolean toggles.
        self._apply_wires(
            "No junction dots",
            lambda s, wid: s.set_wire_no_junction_dots(wid, bool(state)),
        )

    def _on_no_term(self, state: int) -> None:
        self._apply_wires(
            "No termination dots",
            lambda s, wid: s.set_wire_no_termination_dots(wid, bool(state)),
        )

    def _on_z_changed(self, value: int) -> None:
        self._apply_wires("Z-order", lambda s, wid: s.set_wire_z_order(wid, value))

    def _move(self, *, to_front: bool) -> None:
        if self._scene is None or not self._wire_ids:
            return
        new_z = 0
        with self._scene.batch("Move to front" if to_front else "Move to back"):
            for wid in self._wire_ids:
                new_z = (
                    self._scene.bring_to_front(wid)
                    if to_front
                    else self._scene.send_to_back(wid)
                )
        self._z_spin.blockSignals(True)
        self._z_spin.setValue(new_z)
        self._z_spin.blockSignals(False)

    def _on_hop_mode(self, _state: int) -> None:
        mode = _HOP_STATE_TO_MODE.get(self._hop_mode.checkState(), "")
        self._apply_wires("Line hops", lambda s, wid: s.set_wire_hop_mode(wid, mode))

    def _on_start_marker(self) -> None:
        # Combo selection is a discrete action — commit immediately (no debounce).
        kind = _LABEL_TO_MARKER.get(self._start_marker.currentText(), "")
        self._apply_wires("Start marker", lambda s, wid: s.set_wire_start_marker(wid, kind))

    def _on_end_marker(self) -> None:
        kind = _LABEL_TO_MARKER.get(self._end_marker.currentText(), "")
        self._apply_wires("End marker", lambda s, wid: s.set_wire_end_marker(wid, kind))

    def _on_start_label(self) -> None:
        # editingFinished fires on Enter or focus-out; the scene setter is a
        # no-op when the text is unchanged, so the double-fire is harmless.
        text = self._start_label.text()
        self._apply_wires("Start label", lambda s, wid: s.set_wire_start_label(wid, text))

    def _on_end_label(self) -> None:
        text = self._end_label.text()
        self._apply_wires("End label", lambda s, wid: s.set_wire_end_label(wid, text))

    def _on_mid_label(self) -> None:
        text = self._mid_label.text()
        self._apply_wires("Middle label", lambda s, wid: s.set_wire_mid_label(wid, text))

    def _on_start_label_placement(self) -> None:
        # Combo selection is discrete — commit immediately (no debounce).
        val = _LABEL_TO_PLACEMENT.get(self._start_label_pos.currentText(), "")
        self._apply_wires(
            "Start label placement",
            lambda s, wid: s.set_wire_start_label_placement(wid, val),
        )

    def _on_end_label_placement(self) -> None:
        val = _LABEL_TO_PLACEMENT.get(self._end_label_pos.currentText(), "")
        self._apply_wires(
            "End label placement",
            lambda s, wid: s.set_wire_end_label_placement(wid, val),
        )


class TransformSection(InspectorSection):
    """Rotation buttons + mirror checkbox.

    Rotation applies to everything except rect (whose rotation is a codegen
    no-op).  Mirror is only meaningful for path-emitted circuit components and
    the bipole node, so it is shown for those and hidden otherwise.
    """

    title = "Rotation"
    multi_kind_safe = True

    def _build(self) -> None:
        rot_row, self._rot_buttons = _make_rotation_row(self, self._on_rotate)
        self.body.addLayout(rot_row)

        self._mirror_cb = QCheckBox("Mirror (horizontal)")
        self._mirror_cb.stateChanged.connect(self._on_mirror)
        self.body.addWidget(self._mirror_cb)

    def applies_to(self, comp: Component) -> bool:
        # rect and circle are axis-aligned boxes — rotation is a codegen no-op.
        return not isinstance(comp, (RectComponent, CircleComponent))

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
        self._apply("Rotate", lambda s, cid: s.rotate_component(cid, angle))

    def _on_mirror(self, state: int) -> None:
        on = bool(state)
        self._apply("Mirror", lambda s, cid: s.mirror_component(cid, on))


class LayerSection(InspectorSection):
    """Z-order spinbox + move-to-front/back buttons for any DrawingComponent."""

    title = "Layer"
    multi_kind_safe = True

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
        self._apply("Z-order", lambda s, cid: s.set_component_z_order(cid, value))

    def _move(self, *, to_front: bool) -> None:
        t = self._target()
        if not t:
            return
        scene = t[0]
        new_z = 0
        with scene.batch("Move to front" if to_front else "Move to back"):
            for cid in self._comp_ids:
                new_z = scene.bring_to_front(cid) if to_front else scene.send_to_back(cid)
        self._z_spin.blockSignals(True)
        self._z_spin.setValue(new_z)
        self._z_spin.blockSignals(False)


# ---------------------------------------------------------------------------
# Document properties (per-document CircuiTikZ conventions)
# ---------------------------------------------------------------------------

_STYLE_LABELS = {"american": "American", "european": "European"}


class DocumentPropertiesPanel(QWidget):
    """Per-document properties — the CircuiTikZ voltage/current **label styles**
    (american / european), stored on the :class:`Schematic` and travelling with
    the ``.hv`` file (spec §10.3 / §7.2).

    Replaces the former modal *Document Settings* dialog: edits apply **live**
    (each change mutates the schematic and emits :attr:`document_changed`, which
    the main window turns into a relayout + recompile). It is the second tab of
    the inspector, shown automatically when nothing is selected.
    """

    document_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(_PANEL_WIDTH)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        self._scene: SchematicScene | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self._header = QLabel("Document")
        self._header.setObjectName("headerLabel")
        self._header.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {theme.TEXT};"
        )
        outer.addWidget(self._header)
        outer.addWidget(_make_separator())

        outer.addWidget(_make_section_label("CircuiTikZ conventions"))
        form = QFormLayout()
        form.setSpacing(8)
        self._voltage = self._style_combo()
        self._current = self._style_combo()
        form.addRow("Voltage labels", self._voltage)
        form.addRow("Current labels", self._current)
        outer.addLayout(form)

        hint = QLabel(
            "Arrow convention for voltage (<tt>v=</tt>) and current (<tt>i=</tt>) "
            "labels in this document — emitted as a picture-scoped "
            "<tt>\\ctikzset{voltage=…, current=…}</tt>, so it also applies to the "
            "exported figure. Stored in the .hv file."
        )
        hint.setObjectName("hintLabel")
        hint.setStyleSheet(f"color: {theme.ICON_MUTED}; font-size: 10px;")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        outer.addStretch(1)

        self._voltage.currentIndexChanged.connect(self._on_change)
        self._current.currentIndexChanged.connect(self._on_change)

    def _style_combo(self) -> QComboBox:
        combo = QComboBox()
        for style in LABEL_STYLES:
            combo.addItem(_STYLE_LABELS.get(style, style), style)
        return combo

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene
        self.refresh()

    def refresh(self) -> None:
        """Reload the combos from the current document (e.g. after New/Open)."""
        if self._scene is None:
            return
        sch = self._scene.schematic
        for combo, value in ((self._voltage, sch.voltage_style),
                             (self._current, sch.current_style)):
            combo.blockSignals(True)
            idx = combo.findData(value if value in LABEL_STYLES else "american")
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

    def apply_theme(self) -> None:
        _reink_themed_labels(self)

    def _on_change(self) -> None:
        if self._scene is None:
            return
        sch = self._scene.schematic
        voltage = self._voltage.currentData()
        current = self._current.currentData()
        if voltage == sch.voltage_style and current == sch.current_style:
            return
        # Undoable, like every other edit: route through the scene's stack (so
        # Ctrl+Z reverts it and the modified-state tracking sees it) instead of
        # mutating the schematic directly.
        self._scene._push(SetDocumentPropertiesCommand(
            new_voltage=voltage,
            new_current=current,
            old_voltage=sch.voltage_style,
            old_current=sch.current_style,
        ))
        self.document_changed.emit()


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
        self._header.setObjectName("headerLabel")
        self._header.setStyleSheet(
            f"font-weight: bold; font-size: 13px; color: {theme.TEXT};"
        )
        self._header.setWordWrap(True)
        outer.addWidget(self._header)

        outer.addWidget(_make_separator())

        # Scrollable column of sections. The QScrollArea otherwise paints an
        # opaque `Base` fill (white on the native style) for its viewport, which
        # reads as a distinct inset box; the Document tab looks "clear" because it
        # is a plain transparent widget showing the tab pane. We make the viewport
        # and content transparent and theme the scrollbar — but **without a
        # stylesheet on the scroll area or its content**: a stylesheet on any
        # ancestor switches the whole descendant subtree to Qt's stylesheet
        # rendering, which draws the form controls compact/non-native (they look
        # squished). Object-name scoping does NOT prevent that — the mere presence
        # of the sheet does it. So transparency comes from autoFillBackground and
        # the scrollbar is themed on the scrollbar widget itself (_apply_scroll_style).
        scroll = QScrollArea()
        self._scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("inspViewport")
        outer.addWidget(scroll)

        content = QWidget()
        self._content = content
        content.setObjectName("inspContent")
        col = QVBoxLayout(content)
        # Reserve room on the right so the vertical scrollbar never overlaps the
        # section fields (macOS overlay scrollbars float over the content).
        col.setContentsMargins(0, 0, 10, 0)
        col.setSpacing(6)

        # Ordered section list — order is the visual order in the panel.
        self._sections: list[InspectorSection] = [
            OptionsSection(),
            TextContentSection(),
            BipoleLabelSection(),
            VariantSection(),
            ParamSection(),
            FontSection(),
            FillBorderSection(),
            StrokeWidthSection(),
            ScaleSection(),
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
        self._apply_scroll_style()

    def _apply_scroll_style(self) -> None:
        """Theme the scrollbar and make the body transparent (so it shows the tab
        pane like the Document tab) **without putting a stylesheet on the scroll
        area or its content** — a stylesheet on any ancestor of the form controls
        forces them into Qt's compact stylesheet rendering (they look squished).
        So: transparency via ``autoFillBackground(False)``, and the scrollbar
        styled on the scrollbar widget itself. Re-applied on a theme swap so the
        scrollbar colours follow."""
        for w in (self._scroll, self._scroll.viewport(), self._content):
            w.setAutoFillBackground(False)
        self._scroll.verticalScrollBar().setStyleSheet(theme.scrollbar_qss())
        self._scroll.horizontalScrollBar().setStyleSheet(theme.scrollbar_qss())

    def set_scene(self, scene: SchematicScene) -> None:
        self._scene = scene

    def flush_pending_edits(self) -> None:
        """Commit every section's pending debounced edit immediately.

        Called by MainWindow before save / export / the unsaved-changes check so
        an edit typed within the debounce window is never lost or serialised
        stale (§10.3).
        """
        for sec in self._sections:
            sec.flush_pending_edits()
        self._wire_section.flush_pending_edits()

    def apply_theme(self) -> None:
        """Re-ink the inspector's themed text and refresh the scroll style for a
        light/dark swap (called by MainWindow._apply_theme)."""
        _reink_themed_labels(self)
        self._apply_scroll_style()

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

    def show_components(self, comp_ids: list[str]) -> None:
        """Bind the inspector to several selected components; edits hit them all.

        Two cases, both editing every selected component as one undo step (see
        ``InspectorSection._apply``):

        * **Same kind** — every section that applies to the kind is shown, so the
          full per-kind editor is available across the selection.
        * **Mixed kinds** (e.g. a resistor + a capacitor) — only the *shared*,
          kind-independent capability sections (``multi_kind_safe``: font,
          fill/border, stroke, rotation, layer) are shown, and only those that
          apply to **every** selected component. Kind-specific sections (options,
          labels, variants, param count) are suppressed because their value has a
          different meaning per kind.

        Widgets load from the first component as the representative. Falls back to
        a count-only view if the scene/components are unavailable, or for a mixed
        selection that shares no editable capability.
        """
        if self._scene is None:
            self.clear()
            return
        by_id = {c.id: c for c in self._scene.schematic.components}
        comps = [by_id[cid] for cid in comp_ids if cid in by_id]
        if len(comps) < 2:
            self.show_multi_select(len(comp_ids))
            return

        kinds = {c.kind for c in comps}
        same_kind = len(kinds) == 1

        def applicable(sec: InspectorSection) -> bool:
            if same_kind:
                return sec.applies_to(comps[0])
            return sec.multi_kind_safe and all(sec.applies_to(c) for c in comps)

        # A mixed selection with no shared capability has nothing to edit.
        if not same_kind and not any(applicable(sec) for sec in self._sections):
            self.show_multi_select(len(comps))
            return

        self._wire_section.unbind()
        if same_kind:
            defn = REGISTRY[comps[0].kind]
            self._header.setText(
                f"{len(comps)} × {defn.display_name}\n({comps[0].kind})"
            )
        else:
            self._header.setText(
                f"{len(comps)} items selected\n({len(kinds)} types — shared properties)"
            )

        first_visible = True
        for sec in self._sections:
            if applicable(sec):
                sec.bind_multi(comps, self._scene)
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

    def show_wires(self, wire_ids: list[str]) -> None:
        """Bind the wire-style inspector to several selected wires; an edit applies
        to all of them as one undo step. Widgets load from the first wire."""
        if self._scene is None:
            self.clear()
            return
        by_id = {w.id: w for w in self._scene.schematic.wires}
        wires = [by_id[wid] for wid in wire_ids if wid in by_id]
        if len(wires) < 2:
            self.show_multi_select(len(wire_ids))
            return
        for sec in self._sections:
            sec.unbind()
        self._header.setText(f"{len(wires)} wires selected")
        self._wire_section.bind_wires(wires, self._scene)

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
