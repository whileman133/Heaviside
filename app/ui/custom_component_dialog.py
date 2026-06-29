"""
Custom-component creator / editor dialog (spec §custom).

The user picks a base built-in component, supplies scoped ``\\ctikzset``
customisations and extra CircuiTikZ options, and presses **Render** to capture the
component: Heaviside renders that configuration, parses its geometry, and
re-measures its anchors (via :func:`app.components.custom.build_custom`, run in a
background thread so the UI stays responsive). The dialog then shows a
**canvas-style preview** — the captured symbol with its anchor points marked — so
the result is reviewed *before* accepting. **OK** is enabled only once a render has
succeeded for the current inputs, so accepting is instant (no capture lag).

Opening the dialog with an ``editing`` spec pre-fills the fields and keeps the
component's existing *kind* on accept, so placed instances keep working.

Requires ``latex`` + ``dvisvgm`` at run time (to render/measure); the dialog reports
clearly when a render fails.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QThread, QPointF, Signal
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.canvas import svgsym
from app.canvas.style import GRID_PX
from app.components import custom, library
from app.components.model import CustomComponentSpec
from app.components.registry import REGISTRY
from app.preview import latex as _latex
from app.ui import theme

_PREVIEW_DPI = 150


def _base_kind_choices() -> list[tuple[str, str]]:
    """``(display, kind)`` for every built-in that can be a customisation base —
    the manual-library CircuiTikZ components (drawing primitives and the bespoke
    open/short annotations are excluded). Sorted by category then display name."""
    lib = library.load_library()
    rows = [
        (REGISTRY[k].display_name, k)
        for k in lib
        if k in REGISTRY and not custom.is_custom_kind(k)
    ]
    rows.sort(key=lambda r: (REGISTRY[r[1]].category, r[0].lower()))
    return rows


def _preview_circuitikz(base_kind: str, ctikzset: list[str], extra_options: str) -> str:
    """A bare ``circuitikz`` environment for the configuration — what the LaTeX
    preview compiles (no fixed bounding box, so the figure crops tightly)."""
    rec = library.load_library().get(base_kind, {})
    tikz = rec.get("tikz", base_kind)
    opt = custom._opt_suffix(extra_options)
    sets = "".join(rf"  \ctikzset{{{c}}}" + "\n" for c in ctikzset)
    if rec.get("emission") == "path":
        span = rec["pins"][1]["offset"]
        body = rf"  \draw (0,0) to[{tikz}{opt}] ({span[0]:g},{-span[1]:g});"
    else:
        body = rf"  \node[{tikz}{opt}] (X) at (0,0) {{}};"
    return "\\begin{circuitikz}\n" + sets + body + "\n\\end{circuitikz}"


class _BuildThread(QThread):
    """One-shot background Render: captures the component (``build_custom``) **and**
    compiles a literal LaTeX raster of it, so the dialog can show both the
    canvas/anchor preview and the rendered-TeX preview. Emits ``(spec, image,
    error)``: ``spec``/``image`` are ``None`` on failure of their step."""

    done = Signal(object, object, str)  # (spec | None, QImage | None, error)

    def __init__(self, *, name: str, display_name: str, category: str,
                 base_kind: str, ctikzset: list[str], extra_options: str,
                 dark: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kw = dict(name=name, display_name=display_name, category=category,
                        base_kind=base_kind, ctikzset=ctikzset,
                        extra_options=extra_options)
        self._dark = dark

    def run(self) -> None:  # pragma: no cover - exercised via the GUI
        try:
            spec = custom.build_custom(**self._kw)
        except Exception as exc:
            self.done.emit(None, None, str(exc))
            return
        image = None
        try:
            tex = _latex.build_tex(
                _preview_circuitikz(self._kw["base_kind"], self._kw["ctikzset"],
                                    self._kw["extra_options"]),
                dark=self._dark)
            image = _latex.pdf_to_qimage(_latex.compile_tex(tex, timeout=30),
                                         dpi=_PREVIEW_DPI)
        except Exception:
            image = None   # the canvas/anchor preview is still shown
        self.done.emit(spec, image, "")


class _ComponentPreview(QWidget):
    """Canvas-style preview of a captured spec: the symbol geometry with its anchor
    points marked, fit to the widget — what the component will look like when placed.
    Each anchor's **name** is shown on hover (so dense symbols stay readable)."""

    _HOVER_PX = 10.0  # cursor-to-dot distance that reveals an anchor's name

    def __init__(self, dark: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._spec: CustomComponentSpec | None = None
        self._placeholder = "Press Render to preview"
        self._dark = dark
        self._anchor_pts: list[tuple[QPointF, str]] = []  # device pts, set on paint
        self._hover = -1
        self.setMinimumHeight(220)
        self.setMouseTracking(True)   # hover without a pressed button

    def set_spec(self, spec: CustomComponentSpec | None) -> None:
        self._spec = spec
        self._hover = -1
        self.update()

    def set_placeholder(self, text: str) -> None:
        self._spec = None
        self._hover = -1
        self.update()

    def _anchor_at(self, pos: QPointF) -> int:
        """Index of the anchor whose dot is nearest *pos* within ``_HOVER_PX``, or -1."""
        nearest, best = -1, self._HOVER_PX
        for i, (pt, _name) in enumerate(self._anchor_pts):
            d = pt - pos
            dist = (d.x() ** 2 + d.y() ** 2) ** 0.5
            if dist <= best:
                best, nearest = dist, i
        return nearest

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hover = self._anchor_at(event.position())
        if hover != self._hover:
            self._hover = hover
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self._hover != -1:
            self._hover = -1
            self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bg = QColor("#1e1e1e") if self._dark else QColor("#ffffff")
        ink = QColor(theme.ICON)
        p.fillRect(self.rect(), bg)
        if self._spec is None:
            p.setPen(QColor(theme.TEXT_MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
            p.end()
            return

        spec = self._spec
        x0, y0, x1, y1 = spec.bbox
        cw = max((x1 - x0) * GRID_PX, 1.0)
        ch = max((y1 - y0) * GRID_PX, 1.0)
        margin = 28.0
        avail_w = max(self.width() - 2 * margin, 1.0)
        avail_h = max(self.height() - 2 * margin, 1.0)
        scale = min(avail_w / cw, avail_h / ch, 2.0)   # cap so tiny symbols aren't huge
        ccx = (x0 + x1) / 2 * GRID_PX
        ccy = (y0 + y1) / 2 * GRID_PX

        # local-px (svgsym) → device-px: centre the content in the widget.
        dev = QTransform()
        dev.translate(self.width() / 2, self.height() / 2)
        dev.scale(scale, scale)
        dev.translate(-ccx, -ccy)
        local = svgsym.local_transform()

        # Geometry (cosmetic pen so stroke width is constant regardless of zoom).
        # Honours an explicit stroke colour / dash (a custom component's ``color=`` /
        # ``dash=``); a default-black stroke falls back to the theme ink. Cosmetic-pen
        # dashes are in device px, so the SVG-pt lengths scale by PX_PER_PT × the fit
        # scale (see svgsym.dash_for_pen).
        ink_name = theme.ICON
        for path in spec.geometry.get("paths", []):
            qp = dev.map(local.map(svgsym.parse_path(path["d"])))
            fill = (path.get("fill") or "none").lower()
            if fill not in ("none", "#fff", "#ffffff", "white"):
                p.fillPath(qp, QBrush(QColor(svgsym.effective_color(path.get("fill", ""), ink_name))))
            width = 2.0 if float(path.get("stroke_width", 0.4)) >= 0.6 else 1.2
            pen = QPen(QColor(svgsym.effective_color(path.get("stroke", ""), ink_name)))
            pen.setCosmetic(True)
            pen.setWidthF(width)
            if path.get("dash"):
                pen.setDashPattern(
                    svgsym.dash_for_pen(path["dash"], svgsym.PX_PER_PT * scale, width))
            p.setPen(pen)
            p.drawPath(qp)

        # Anchor points: a small accent dot at each pin (names appear on hover).
        accent = QColor("#e06c5a")
        p.setBrush(QBrush(accent))
        p.setPen(QPen(accent))
        pts = [dev.map(QPointF(ox * GRID_PX, oy * GRID_PX))
               for ox, oy in (pin["offset"] for pin in spec.pins)]
        self._anchor_pts = list(zip(pts, (pin["name"] for pin in spec.pins)))
        for pt in pts:
            p.drawEllipse(pt, 3.0, 3.0)
        # The hovered anchor's name, with a small readable backdrop.
        if 0 <= self._hover < len(self._anchor_pts):
            pt, name = self._anchor_pts[self._hover]
            p.setBrush(QBrush(accent))
            p.setPen(QPen(accent))
            p.drawEllipse(pt, 4.5, 4.5)        # emphasise the hovered dot
            f = p.font()
            f.setPointSizeF(8.0)
            p.setFont(f)
            metrics = p.fontMetrics()
            label_pt = pt + QPointF(7.0, -5.0)
            rect = metrics.boundingRect(name).translated(label_pt.toPoint())
            rect.adjust(-2, -1, 2, 1)
            p.fillRect(rect, bg)
            p.setPen(QPen(QColor(theme.ICON)))
            p.drawText(label_pt, name)
        p.end()


class CustomComponentDialog(QDialog):
    """Create or edit a custom component built from a base built-in."""

    def __init__(self, *, dark: bool = False, existing: set[str] | None = None,
                 editing: CustomComponentSpec | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._editing = editing
        self.setWindowTitle("Edit Custom Component" if editing else "New Custom Component")
        self.setModal(True)
        self.setMinimumWidth(580)
        self._dark = dark
        self._existing = existing or set()
        self._spec: CustomComponentSpec | None = None
        self._build_thread: _BuildThread | None = None
        self._build_seq = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(8)
        # Let fields fill the dialog width (macOS defaults to FieldsStayAtSizeHint,
        # which leaves the \ctikzset / options boxes only half-width).
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. Iron-core transformer")
        self._name.textChanged.connect(self._invalidate)
        form.addRow("Name", self._name)

        self._base = QComboBox()
        for display, kind in _base_kind_choices():
            self._base.addItem(f"{display}  ({kind})", kind)
        # Type-to-filter: ~400 base kinds is too many to scroll, so the combo is
        # editable with a contains-match completer (search by display name or
        # CircuiTikZ keyword). NoInsert keeps free text from creating phantom items;
        # _base_kind() only accepts text that exactly matches a real item.
        self._base.setEditable(True)
        self._base.setInsertPolicy(QComboBox.NoInsert)
        self._base.lineEdit().setPlaceholderText("Type to search components…")
        completer = self._base.completer()
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._base.setCurrentIndex(0)
        self._base.currentIndexChanged.connect(self._invalidate)
        self._base.editTextChanged.connect(self._invalidate)
        form.addRow("Base component", self._base)

        self._category = QLineEdit(custom.CUSTOM_CATEGORY)
        form.addRow("Palette category", self._category)

        self._ctikzset = QPlainTextEdit()
        self._ctikzset.setPlaceholderText(
            "Scoped \\ctikzset settings, one per line\n"
            "e.g. transformers/coils/width=1.2")
        self._ctikzset.setFixedHeight(72)
        self._ctikzset.textChanged.connect(self._invalidate)
        form.addRow("\\ctikzset", self._ctikzset)

        self._options = QLineEdit()
        self._options.setPlaceholderText("Extra node/path options, e.g. core")
        self._options.textChanged.connect(self._invalidate)
        form.addRow("Extra options", self._options)

        layout.addLayout(form)

        # Two side-by-side previews: the canvas representation (geometry + anchor
        # points), and the literal LaTeX render of the component.
        previews = QHBoxLayout()
        previews.setSpacing(10)
        previews.addLayout(self._titled(
            "Canvas (anchors shown)",
            self._make_canvas_preview(dark)))
        previews.addLayout(self._titled(
            "LaTeX render",
            self._make_tex_preview(dark)))
        layout.addLayout(previews, 1)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("QLabel { color: palette(mid); }")
        layout.addWidget(self._status)

        self._render_btn = QPushButton("Render")
        self._render_btn.clicked.connect(self._render)
        self._buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self._buttons.addButton(self._render_btn, QDialogButtonBox.ActionRole)
        self._buttons.accepted.connect(self._on_accept)
        self._buttons.rejected.connect(self.reject)
        layout.addWidget(self._buttons)

        if editing is not None:
            self._prefill(editing)
        self._invalidate()  # Render is an explicit step; OK stays disabled until it succeeds

    # -- preview widgets -------------------------------------------------

    def _titled(self, title: str, widget: QWidget) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(3)
        cap = QLabel(title)
        cap.setAlignment(Qt.AlignCenter)
        cap.setStyleSheet("QLabel { color: palette(mid); font-size: 11px; }")
        col.addWidget(cap)
        col.addWidget(widget, 1)
        return col

    def _make_canvas_preview(self, dark: bool) -> QWidget:
        self._preview = _ComponentPreview(dark)
        self._preview.setStyleSheet("border: 1px solid palette(mid);")
        return self._preview

    def _make_tex_preview(self, dark: bool) -> QWidget:
        self._tex = QLabel("Press Render to preview")
        self._tex.setAlignment(Qt.AlignCenter)
        self._tex.setMinimumHeight(220)
        self._tex.setStyleSheet(
            "QLabel { border: 1px solid palette(mid); color: palette(mid); background: %s; }"
            % ("#1e1e1e" if dark else "#ffffff"))
        return self._tex

    def _set_tex_image(self, image: QImage | None, fallback: str) -> None:
        if image is None or image.isNull():
            self._tex.setText(fallback)
            return
        pix = QPixmap.fromImage(image)
        avail = self._tex.size()
        if pix.width() > avail.width() or pix.height() > avail.height():
            pix = pix.scaled(avail, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._tex.setPixmap(pix)

    # -- prefill (edit mode) ---------------------------------------------

    def _prefill(self, spec: CustomComponentSpec) -> None:
        self._name.setText(spec.display_name)
        idx = self._base.findData(spec.base_kind)
        if idx >= 0:
            self._base.setCurrentIndex(idx)
        self._category.setText(spec.category)
        self._ctikzset.setPlainText("\n".join(spec.ctikzset))
        self._options.setText(spec.extra_options)

    # -- inputs ----------------------------------------------------------

    def _base_kind(self) -> str:
        """The selected base kind, or ``""`` while the search text is partial."""
        combo = self._base
        i = combo.findText(combo.currentText().strip(), Qt.MatchFixedString)
        return combo.itemData(i) if i >= 0 else ""

    def _ctikzset_lines(self) -> list[str]:
        return [ln.strip() for ln in self._ctikzset.toPlainText().splitlines()
                if ln.strip()]

    def _target_kind(self) -> str:
        """The kind the captured component will use — the original kind in edit mode
        (so placed instances keep working), else derived from the display name."""
        if self._editing is not None:
            return self._editing.name
        return custom.make_kind(self._name.text().strip())

    def _invalidate(self) -> None:
        """An input changed: the last render no longer matches, so require a new one
        before OK. (Does not auto-render — Render is an explicit step.)"""
        self._spec = None
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        ready = bool(self._name.text().strip()) and bool(self._base_kind())
        self._render_btn.setEnabled(ready)
        if ready:
            self._preview.set_placeholder("Press Render to preview")
            self._tex.setText("Press Render to preview")

    # -- render (capture) ------------------------------------------------

    def _render(self) -> None:
        display = self._name.text().strip()
        base = self._base_kind()
        if not display or not base:
            return
        self._render_btn.setEnabled(False)
        self._status.setText("Rendering…")
        self._build_seq += 1
        seq = self._build_seq
        thread = _BuildThread(
            name=self._target_kind(), display_name=display,
            category=self._category.text().strip() or custom.CUSTOM_CATEGORY,
            base_kind=base, ctikzset=self._ctikzset_lines(),
            extra_options=self._options.text(), dark=self._dark, parent=self)
        thread.done.connect(
            lambda spec, image, err, s=seq: self._on_render_done(spec, image, err, s))
        self._build_thread = thread
        thread.start()

    def _on_render_done(self, spec: CustomComponentSpec | None, image: QImage | None,
                        error: str, seq: int) -> None:
        if seq != self._build_seq:
            return  # superseded by a newer render
        self._render_btn.setEnabled(True)
        if spec is None:
            self._spec = None
            self._buttons.button(QDialogButtonBox.Ok).setEnabled(False)
            self._preview.set_placeholder("Render failed")
            self._tex.setText("Render failed")
            self._status.setText(error or "Could not render the component.")
            return
        self._spec = spec
        self._preview.set_spec(spec)
        self._set_tex_image(image, "LaTeX preview unavailable")
        self._buttons.button(QDialogButtonBox.Ok).setEnabled(True)
        self._status.setText(
            f"Rendered: {len(spec.pins)} anchor(s). Press OK to add.")

    # -- accept ----------------------------------------------------------

    def _on_accept(self) -> None:
        if self._spec is None:
            self._status.setText("Press Render first.")
            return
        self.accept()

    def result_spec(self) -> CustomComponentSpec | None:
        """The captured spec after the dialog is accepted, else ``None``."""
        return self._spec

    def done(self, result: int) -> None:
        """Close handler: invalidate any pending render callback and wait for the
        background capture to finish, so the QThread is never destroyed mid-run."""
        self._build_seq += 1  # any in-flight callback becomes a no-op
        thread = self._build_thread
        if thread is not None and thread.isRunning():
            thread.wait(10000)
        super().done(result)
