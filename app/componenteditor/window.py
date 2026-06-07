"""
Standalone component-editor window (Qt).

A form-driven editor for CircuiTikZ symbols: enter the keyword, emission, pins,
and metadata; **Measure** reads the CircuiTikZ pin anchors automatically;
**Bake & preview** renders the symbol; **Save** writes it into the component data
files (``components.json`` + ``manifest.json``) via :mod:`app.componenteditor.baker`.

The window is a thin shell over the Qt-free :mod:`app.componenteditor.draft` /
``baker`` core (which the tests exercise head-less).  Launch with
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
from app.components import bake, library
from app.componenteditor import baker, draft

_PIN_COLS = ("Pin name", "X (GU)", "Y (GU)", "CircuiTikZ anchor")


class ComponentEditorWindow(QMainWindow):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Heaviside — Component Editor")
        self.resize(1000, 700)
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
                            ("Bake && preview", self._on_bake),
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
        if variants:
            entry["variants"] = variants
        return self._kind.text().strip(), entry

    def _entry_to_form(self, kind: str, entry: dict) -> None:
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
            self._existing.addItems(sorted(baker.load_authored()))
        except Exception:  # noqa: BLE001 - no store yet is fine
            pass
        self._existing.blockSignals(False)

    def _load_existing(self, kind: str) -> None:
        if not kind or kind.startswith("—"):
            return
        try:
            entry = baker.load_authored()[kind]
        except Exception:  # noqa: BLE001
            return
        self._entry_to_form(kind, entry)

    # -- actions ---------------------------------------------------------
    def _log(self, text: str) -> None:
        self._out.setPlainText(text)

    def _on_measure(self) -> None:
        _kind, entry = self._form_to_entry()
        try:
            anchors = draft.measured_anchors(entry)
        except bake.BakeError as exc:
            self._log(f"Measurement failed:\n{exc}\n\n{exc.log[-1500:]}")
            return
        if not anchors:
            self._log("No CircuiTikZ anchors to measure (set the keyword and pin anchors).")
            return
        lines = ["Measured anchors (GU offset from origin, Qt y-down):"]
        lines += [f"  {name}: ({x:+.3f}, {y:+.3f})  → snap to 0.25" for name, (x, y) in anchors.items()]
        self._log("\n".join(lines))

    def _on_bake(self) -> None:
        kind, entry = self._form_to_entry()
        errs = draft.validate_entry(kind, entry)
        report = ["VALID ✓" if not errs else "Problems:"] + [f"  • {e}" for e in errs]
        try:
            geom = baker.geometry(entry)
        except bake.BakeError as exc:
            self._log("\n".join(report) + f"\n\nBake failed:\n{exc}\n\n{exc.log[-1200:]}")
            self._scene.clear()
            return
        self._render_preview(geom, entry)
        cdef = draft.derived_component_def(kind, entry)
        report.append("")
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
            baker.save_component(kind, entry)
        except bake.BakeError as exc:
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

        # faint grid
        grid_pen = QPen(QColor("#E0E0E0"))
        for g in range(-3, 4):
            self._scene.addLine(g * GRID_PX, -3 * GRID_PX, g * GRID_PX, 3 * GRID_PX, grid_pen)
            self._scene.addLine(-3 * GRID_PX, g * GRID_PX, 3 * GRID_PX, g * GRID_PX, grid_pen)

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
