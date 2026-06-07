"""
Standalone component-editor window (Qt).

A form-driven editor for CircuiTikZ symbols: enter the keyword, emission, pins,
and metadata; **Measure** reads the CircuiTikZ pin anchors automatically;
**Render & preview** renders the symbol; **Save** writes it into the component data
files (``components.json`` + ``manifest.json``) via :mod:`app.componenteditor.renderer`.

The window is a thin shell over the Qt-free :mod:`app.componenteditor.draft` /
``renderer`` core (which the tests exercise head-less).  Launch with
``python -m app.componenteditor``.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.canvas.style import GRID_PX, SVG_PT_PER_GU
from app.components import library, render
from app.componenteditor import draft, renderer

_PIN_COLS = ("Pin", "X (GU)", "Y (GU)", "Anchor")


class ComponentEditorWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Heaviside — Component Editor")
        self.resize(1000, 700)
        # Residual bridge leads a multi-terminal symbol uses (computed by "Fit pins
        # to grid", preserved across the form round-trip).  The node scale lives in
        # the editable xscale/yscale spin boxes built in _build_ui.
        self._leads: list[dict] | None = None
        self._build_ui()
        self._refresh_existing()

    # -- construction ----------------------------------------------------
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)
        self.setCentralWidget(splitter)

        # --- left: the form (scrollable) ---
        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setLabelAlignment(Qt.AlignRight)

        self._existing = QComboBox()
        self._existing.currentTextChanged.connect(self._load_existing)
        form.addRow("Load existing", self._existing)

        self._kind = QLineEdit()
        self._display = QLineEdit()
        self._category = QComboBox()
        self._category.setEditable(True)
        self._category.addItems(draft.CATEGORIES)
        self._emission = QComboBox()
        self._emission.addItems(draft.EMISSIONS)
        self._tikz = QLineEdit()
        self._labels = QLineEdit()
        self._labels.setPlaceholderText("comma-separated, e.g. l, l_, v")
        self._anchor_pin = QLineEdit()
        self._anchor_pin.setPlaceholderText("multi-terminal only; blank = place by centre")
        form.addRow("Kind", self._kind)
        form.addRow("Display name", self._display)
        form.addRow("Category", self._category)
        form.addRow("Emission", self._emission)
        form.addRow("CircuiTikZ keyword", self._tikz)
        form.addRow("Label slots", self._labels)
        form.addRow("Anchor pin", self._anchor_pin)

        self._bbox = [QDoubleSpinBox() for _ in range(4)]
        bbox_row = QHBoxLayout()
        for sb, lbl in zip(self._bbox, ("x0", "y0", "x1", "y1")):
            sb.setRange(-20, 20)
            sb.setSingleStep(0.25)
            sb.setDecimals(2)
            bbox_row.addWidget(QLabel(lbl))
            bbox_row.addWidget(sb)
        bbox_w = QWidget()
        bbox_w.setLayout(bbox_row)
        form.addRow("Bounding box", bbox_w)

        # Editable node scale (multi-terminal alignment).  "Fit pins to grid"
        # fills these; they can also be set by hand.
        self._scale_x = QDoubleSpinBox()
        self._scale_y = QDoubleSpinBox()
        scale_row = QHBoxLayout()
        for sb, lbl in ((self._scale_x, "xscale"), (self._scale_y, "yscale")):
            sb.setRange(0.05, 20.0)
            sb.setSingleStep(0.01)
            sb.setDecimals(4)
            sb.setValue(1.0)
            scale_row.addWidget(QLabel(lbl))
            scale_row.addWidget(sb)
        scale_w = QWidget()
        scale_w.setLayout(scale_row)
        form.addRow("Scale (node)", scale_w)

        self._pins = QTableWidget(0, len(_PIN_COLS))
        self._pins.setHorizontalHeaderLabels(_PIN_COLS)
        self._pins.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        pin_btns = QHBoxLayout()
        add_pin = QPushButton("Add pin")
        add_pin.clicked.connect(lambda: self._add_pin_row())
        del_pin = QPushButton("Remove pin")
        del_pin.clicked.connect(self._remove_pin_row)
        pin_btns.addWidget(add_pin)
        pin_btns.addWidget(del_pin)
        pin_btns_w = QWidget()
        pin_btns_w.setLayout(pin_btns)
        form.addRow("Pins", self._pins)
        form.addRow("", pin_btns_w)

        self._variants = QLineEdit()
        self._variants.setPlaceholderText("e.g. filled:*:suffix  body_diode:bodydiode:option")
        form.addRow("Variants", self._variants)

        actions = QHBoxLayout()
        for label, slot in (("Measure anchors", self._on_measure),
                            ("Fit pins to grid", self._on_fit),
                            ("Render && preview", self._on_render),
                            ("Save", self._on_save)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            actions.addWidget(b)
        actions_w = QWidget()
        actions_w.setLayout(actions)
        form.addRow("", actions_w)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_host)
        splitter.addWidget(scroll)

        # --- right: preview (top) + output (bottom) ---
        right = QWidget()
        rlay = QVBoxLayout(right)
        self._scene = QGraphicsScene()
        self._view = QGraphicsView(self._scene)
        self._view.setMinimumHeight(280)
        rlay.addWidget(QLabel("Preview"))
        rlay.addWidget(self._view, stretch=3)
        rlay.addWidget(QLabel("Output / validation / log"))
        self._out = QPlainTextEdit()
        self._out.setReadOnly(True)
        rlay.addWidget(self._out, stretch=2)
        splitter.addWidget(right)
        splitter.setSizes([460, 540])

    # -- pins table helpers ----------------------------------------------
    def _add_pin_row(self, name: str = "", x: float = 0.0, y: float = 0.0,
                     anchor: str | None = None) -> None:
        r = self._pins.rowCount()
        self._pins.insertRow(r)
        self._pins.setItem(r, 0, QTableWidgetItem(name))
        self._pins.setItem(r, 1, QTableWidgetItem(f"{x:g}"))
        self._pins.setItem(r, 2, QTableWidgetItem(f"{y:g}"))
        self._pins.setItem(r, 3, QTableWidgetItem(anchor or ""))

    def _remove_pin_row(self) -> None:
        r = self._pins.currentRow()
        if r >= 0:
            self._pins.removeRow(r)

    # -- form <-> entry --------------------------------------------------
    def _form_to_entry(self) -> tuple[str, dict]:
        def _cell(r: int, c: int) -> str:
            it = self._pins.item(r, c)
            return it.text().strip() if it else ""

        pins = []
        for r in range(self._pins.rowCount()):
            name = _cell(r, 0)
            if not name:
                continue
            try:
                x, y = float(_cell(r, 1) or 0), float(_cell(r, 2) or 0)
            except ValueError:
                x, y = 0.0, 0.0
            anchor = _cell(r, 3) or None
            pins.append({"name": name, "offset": [x, y], "anchor": anchor})

        variants = []
        for tok in self._variants.text().split():
            parts = tok.split(":")
            if len(parts) == 3:
                variants.append({"name": parts[0], "token": parts[1], "mode": parts[2]})

        labels = [s.strip() for s in self._labels.text().split(",") if s.strip()]
        entry: dict = {
            "display_name": self._display.text().strip() or self._kind.text().strip(),
            "category": self._category.currentText().strip(),
            "emission": self._emission.currentText(),
            "tikz": self._tikz.text().strip(),
            "labels": labels,
            "bbox": [sb.value() for sb in self._bbox],
            "pins": pins,
        }
        ap = self._anchor_pin.text().strip()
        if entry["emission"] == "multi_terminal":
            entry["anchor_pin"] = ap or None
            sx, sy = self._get_scale()
            if abs(sx - 1.0) > 1e-9 or abs(sy - 1.0) > 1e-9:
                entry["scale"] = [sx, sy]
            if self._leads is not None:
                entry["leads"] = [dict(ld) for ld in self._leads]
        if variants:
            entry["variants"] = variants
        return self._kind.text().strip(), entry

    def _get_scale(self) -> list[float]:
        return [round(self._scale_x.value(), 4), round(self._scale_y.value(), 4)]

    def _set_scale(self, sx: float, sy: float) -> None:
        for sb, v in ((self._scale_x, sx), (self._scale_y, sy)):
            sb.blockSignals(True)
            sb.setValue(float(v))
            sb.blockSignals(False)

    def _entry_to_form(self, kind: str, entry: dict) -> None:
        self._leads = [dict(ld) for ld in entry["leads"]] if "leads" in entry else None
        sx, sy = entry.get("scale") or (1.0, 1.0)
        self._set_scale(sx, sy)
        self._kind.setText(kind)
        self._display.setText(entry.get("display_name", ""))
        self._category.setCurrentText(entry.get("category", ""))
        self._emission.setCurrentText(entry.get("emission", "two_terminal"))
        self._tikz.setText(entry.get("tikz", ""))
        self._labels.setText(", ".join(entry.get("labels", [])))
        self._anchor_pin.setText(entry.get("anchor_pin") or "")
        bbox = entry.get("bbox", [0, 0, 0, 0])
        for sb, v in zip(self._bbox, bbox):
            sb.setValue(float(v))
        self._pins.setRowCount(0)
        for p in entry.get("pins", []):
            off = p["offset"]
            self._add_pin_row(p["name"], off[0], off[1], p.get("anchor"))
        self._variants.setText(" ".join(
            f"{v['name']}:{v['token']}:{v['mode']}" for v in entry.get("variants", [])
        ))

    # -- existing components ---------------------------------------------
    def _refresh_existing(self) -> None:
        self._existing.blockSignals(True)
        self._existing.clear()
        self._existing.addItem("— new component —")
        try:
            self._existing.addItems(sorted(renderer.load_authored()))
        except Exception:  # noqa: BLE001 - no store yet is fine
            pass
        self._existing.blockSignals(False)

    def _load_existing(self, kind: str) -> None:
        if not kind or kind.startswith("—"):
            self._leads = None
            self._set_scale(1.0, 1.0)
            return
        try:
            entry = renderer.load_authored()[kind]
        except Exception:  # noqa: BLE001
            return
        self._entry_to_form(kind, entry)
        self._on_render()  # render + preview immediately on selecting a component

    # -- actions ---------------------------------------------------------
    def _log(self, text: str) -> None:
        self._out.setPlainText(text)

    def _on_measure(self) -> None:
        _kind, entry = self._form_to_entry()
        try:
            anchors = draft.measured_anchors(entry)
        except render.RenderError as exc:
            self._log(f"Measurement failed:\n{exc}\n\n{exc.log[-1500:]}")
            return
        if not anchors:
            self._log("No CircuiTikZ anchors to measure (set the keyword and pin anchors).")
            return
        lines = ["Measured anchors (GU offset from origin, Qt y-down):"]
        lines += [f"  {name}: ({x:+.3f}, {y:+.3f})  → snap to 0.25" for name, (x, y) in anchors.items()]
        self._log("\n".join(lines))

    def _on_fit(self) -> None:
        """Compute a node scale (+ residual leads) that lands the pins on grid.

        Measures the CircuiTikZ anchors and derives the per-axis scale; the symbol
        is stretched onto the grid pins instead of bridged with diagonal leads.
        Use for transistors/symbols whose terminals fall between grid points.
        """
        _kind, entry = self._form_to_entry()
        if entry.get("emission") != "multi_terminal":
            self._log("Fit applies to multi-terminal components (scales the symbol "
                      "so its pins land on the grid).")
            return
        pins = entry["pins"]
        anchor_of = {p["name"]: p.get("anchor") for p in pins}
        if not any(anchor_of.values()):
            self._log("Set each pin's CircuiTikZ anchor first, then Fit.")
            return
        try:
            measured = render.measure_anchors(entry["tikz"], [a for a in anchor_of.values() if a])
        except render.RenderError as exc:
            self._log(f"Measure failed:\n{exc}\n\n{getattr(exc, 'log', '')[-800:]}")
            return
        ap = entry.get("anchor_pin")
        if ap and anchor_of.get(ap) in measured:
            ox, oy = measured[anchor_of[ap]]
            other = [p for p in pins if p["name"] != ap]
        else:  # centre-placed (no anchor pin): anchors are already centre-relative
            ox, oy = 0.0, 0.0
            other = list(pins)
        rel = {p["name"]: (round(measured[p["anchor"]][0] - ox, 4),
                           round(measured[p["anchor"]][1] - oy, 4))
               for p in other if p.get("anchor") in measured}
        targets = {p["name"]: tuple(p["offset"]) for p in other if p["name"] in rel}
        scale, residual = renderer.compute_alignment(rel, targets)
        self._set_scale(scale[0], scale[1])
        self._leads = [{"anchor": anchor_of[n], "to": list(targets[n])} for n in residual]
        self._on_render()
        self._out.setPlainText(
            f"Fit: scale={tuple(scale)}, residual leads={residual}\n\n" + self._out.toPlainText()
        )

    def _on_render(self) -> None:
        kind, entry = self._form_to_entry()
        errs = draft.validate_entry(kind, entry)
        report = ["VALID ✓" if not errs else "Problems:"] + [f"  • {e}" for e in errs]
        try:
            geom = renderer.geometry(entry)
        except render.RenderError as exc:
            self._log("\n".join(report) + f"\n\nRender failed:\n{exc}\n\n{exc.log[-1200:]}")
            self._scene.clear()
            return
        self._render_preview(geom, entry)
        cdef = draft.derived_component_def(kind, entry)
        report.append("")
        if entry.get("scale") or self._leads:
            report.append(f"Scale: {tuple(self._get_scale())}  Leads: "
                          f"{[(ld['anchor'], tuple(ld['to'])) for ld in (self._leads or [])]}")
        report.append(f"Derived ComponentDef: {cdef.kind!r}  pins="
                      f"{[(p.name, p.offset) for p in cdef.pins]}  span={cdef.default_span}")
        report.append(f"Geometry: {len(geom['paths'])} path(s), {len(geom['glyphs'])} glyph(s).")
        self._log("\n".join(report))

    def _on_save(self) -> None:
        kind, entry = self._form_to_entry()
        errs = draft.validate_entry(kind, entry)
        if errs:
            QMessageBox.warning(self, "Cannot save",
                                "Fix these first:\n\n" + "\n".join(f"• {e}" for e in errs))
            return
        try:
            renderer.save_component(kind, entry)
        except render.RenderError as exc:
            QMessageBox.critical(self, "Render failed", f"{exc}\n\n{exc.log[-1500:]}")
            return
        library._data.cache_clear()  # the saved store changed; drop the cached read
        self._refresh_existing()
        self._log(f"Saved {kind!r} to the component store. Re-open Heaviside to use it.")
        QMessageBox.information(self, "Saved", f"Component {kind!r} saved.")

    # -- preview ---------------------------------------------------------
    def _render_preview(self, geom: dict, entry: dict) -> None:
        self._scene.clear()
        # Same transform as svgsym: translate(-origin) then uniform scale.
        try:
            ox, oy = library.origin_svg()
        except Exception:  # noqa: BLE001
            ox, oy = 15.0312, 15.0312
        t = QTransform()
        t.scale(GRID_PX / SVG_PT_PER_GU, GRID_PX / SVG_PT_PER_GU)
        t.translate(-ox, -oy)

        # 0.25 GU grid (minor lines faint, integer lines darker) — pins sit on it.
        EXT, STEP = 3.0, 0.25
        minor, major = QPen(QColor("#EEEEEE")), QPen(QColor("#C8C8C8"))
        n = round(EXT / STEP)
        for i in range(-n, n + 1):
            g = i * STEP
            pen = major if abs(g - round(g)) < 1e-9 else minor
            self._scene.addLine(g * GRID_PX, -EXT * GRID_PX, g * GRID_PX, EXT * GRID_PX, pen)
            self._scene.addLine(-EXT * GRID_PX, g * GRID_PX, EXT * GRID_PX, g * GRID_PX, pen)

        # bounding box (dashed blue) — the ComponentDef.bbox, for reference.
        bbox = entry.get("bbox")
        if bbox and len(bbox) == 4:
            x0, y0, x1, y1 = (float(v) for v in bbox)
            bbox_pen = QPen(QColor("#0055CC"))
            bbox_pen.setStyle(Qt.DashLine)
            bbox_pen.setCosmetic(True)
            self._scene.addRect(min(x0, x1) * GRID_PX, min(y0, y1) * GRID_PX,
                                abs(x1 - x0) * GRID_PX, abs(y1 - y0) * GRID_PX,
                                bbox_pen, QBrush(Qt.NoBrush))

        from app.canvas.svgsym import parse_path
        body_pen = QPen(QColor("#000000"))
        body_pen.setWidthF(2.0)
        for p in geom["paths"]:
            path = t.map(parse_path(p["d"]))
            self._scene.addPath(path, body_pen,
                                QBrush(QColor("#000000")) if p.get("fill", "none") != "none" else QBrush(Qt.NoBrush))

        # pin markers at the registry offsets
        pin_pen = QPen(QColor("#CC0000"))
        pin_brush = QBrush(QColor("#CC0000"))
        for pin in entry.get("pins", []):
            px, py = pin["offset"]
            self._scene.addEllipse(px * GRID_PX - 3, py * GRID_PX - 3, 6, 6, pin_pen, pin_brush)
        self._view.fitInView(self._scene.itemsBoundingRect().adjusted(-20, -20, 20, 20),
                             Qt.KeepAspectRatio)


def launch() -> int:
    """Run the editor as a standalone app."""
    import sys

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    win = ComponentEditorWindow()
    win.show()
    return app.exec()
