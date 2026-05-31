#!/usr/bin/env python3
"""
Manual visual-inspection script for Phase 5.

Renders every ComponentItem in the registry into a PNG grid so each symbol
can be inspected without launching the full application.

Usage:
    uv run python render_components.py            # saves component_gallery.png
    uv run python render_components.py --show     # also opens the image

The script uses Qt's offscreen platform so no display server is required.
"""

import os
import sys

# Force offscreen rendering (must happen before QApplication is created)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import argparse

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap, QFont, QImage
from PySide6.QtWidgets import QApplication, QGraphicsScene

# Bootstrap Qt before importing our modules
app = QApplication.instance() or QApplication(sys.argv)

from app.components.registry import REGISTRY                   # noqa: E402
from app.canvas.items import ITEM_CLASSES                      # noqa: E402
from app.canvas.style import GRID_PX                           # noqa: E402
from app.schematic.model import Component                      # noqa: E402

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------

CELL_W  = 200     # pixels per cell (width)
CELL_H  = 160     # pixels per cell (height)
COLS    = 4
MARGIN  = 10      # padding inside each cell
LABEL_H = 22      # height reserved for the kind/name label at bottom

BG_COLOR      = QColor("#FFFFFF")
BORDER_COLOR  = QColor("#CCCCCC")
LABEL_COLOR   = QColor("#333333")
HEADING_COLOR = QColor("#000000")


def make_component(kind: str) -> Component:
    """Create a minimal Component instance with an example label."""
    defn = REGISTRY[kind]
    labels = {}
    if "l" in defn.label_slots:
        labels["l"] = f"${kind.replace(' ', '_')}$"
    return Component(
        id=f"test-{kind}",
        kind=kind,
        position=(0.0, 0.0),
        rotation=0,
        labels=labels,
    )


def render_item_to_pixmap(kind: str, w: int, h: int) -> QPixmap:
    """
    Render one ComponentItem centred in a w×h pixmap.
    Returns the pixmap.
    """
    defn = REGISTRY[kind]
    item_cls = ITEM_CLASSES[kind]
    component = make_component(kind)

    # Scene just to hold the item (provides painter transform context)
    scene = QGraphicsScene()
    item = item_cls(component)
    item.set_ghost(False)
    scene.addItem(item)

    # Determine bounding box of the item in scene coords
    br = item.mapToScene(item.boundingRect()).boundingRect()
    # Add a small margin
    pad = GRID_PX * 0.3
    view_rect = br.adjusted(-pad, -pad, pad, pad)

    pixmap = QPixmap(w, h)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.TextAntialiasing)

    scene.render(
        painter,
        QRectF(0, 0, w, h),   # target rect in pixmap
        view_rect,              # source rect in scene
    )
    painter.end()

    scene.removeItem(item)
    return pixmap


def build_gallery(output_path: str) -> None:
    kinds = list(REGISTRY.keys())
    rows = (len(kinds) + COLS - 1) // COLS

    total_w = COLS * CELL_W
    total_h = rows * CELL_H + LABEL_H  # extra row for title

    # Create output image
    image = QImage(total_w, total_h, QImage.Format_ARGB32)
    image.fill(BG_COLOR)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    # Title
    title_font = QFont("sans-serif", 11, QFont.Bold)
    painter.setFont(title_font)
    painter.setPen(HEADING_COLOR)
    painter.drawText(
        8, LABEL_H - 6,
        f"Heaviside — Component Gallery  ({len(kinds)} components)",
    )

    label_font = QFont("sans-serif", 8)

    for idx, kind in enumerate(kinds):
        col = idx % COLS
        row = idx // COLS

        cx = col * CELL_W
        cy = row * CELL_H + LABEL_H

        # Cell background & border
        painter.fillRect(cx, cy, CELL_W, CELL_H, BG_COLOR)
        painter.setPen(BORDER_COLOR)
        painter.drawRect(cx, cy, CELL_W - 1, CELL_H - 1)

        # Render the component symbol
        sym_h = CELL_H - LABEL_H - MARGIN * 2
        sym_w = CELL_W - MARGIN * 2
        try:
            sym_px = render_item_to_pixmap(kind, sym_w, sym_h)
            painter.drawPixmap(cx + MARGIN, cy + MARGIN, sym_px)
        except Exception as exc:
            painter.setPen(QColor("#CC0000"))
            painter.drawText(
                cx + MARGIN, cy + CELL_H // 2,
                f"ERROR: {exc}",
            )

        # Label: display_name + kind
        defn = REGISTRY[kind]
        painter.setFont(label_font)
        painter.setPen(LABEL_COLOR)
        painter.drawText(
            cx + MARGIN,
            cy + CELL_H - MARGIN,
            f"{defn.display_name}  [{kind}]",
        )

    painter.end()

    image.save(output_path)
    print(f"Saved → {output_path}  ({total_w}×{total_h} px)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render component gallery")
    parser.add_argument(
        "-o", "--output",
        default="component_gallery.png",
        help="Output PNG path (default: component_gallery.png)",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Open the image after rendering (uses system viewer)",
    )
    args = parser.parse_args()

    build_gallery(args.output)

    if args.show:
        import subprocess, platform
        if platform.system() == "Darwin":
            subprocess.run(["open", args.output])
        elif platform.system() == "Linux":
            subprocess.run(["xdg-open", args.output])
        else:
            subprocess.run(["start", args.output], shell=True)


if __name__ == "__main__":
    main()
