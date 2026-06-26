"""
Stroke-width mapping for the canvas symbol painter (app/canvas/items._stroke_px).

The two common CircuiTikZ stroke widths map to the binary LINE_W / LINE_W_THICK
weights, but genuinely thick art (seven-segment spines, cute-switch contacts,
battery/source bars) keeps its true proportional width so it matches the compiled
figure instead of being thinned to the body weight.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - host-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.canvas import items  # noqa: E402
from app.canvas.style import LINE_W, LINE_W_THICK  # noqa: E402


def test_common_widths_match_binary_weights():
    """The dominant 0.3985 / 0.797 pt strokes map exactly to LINE_W / LINE_W_THICK,
    so ordinary line art is unchanged by the proportional path."""
    assert items._stroke_px(0.3985) == pytest.approx(LINE_W, rel=1e-3)
    assert items._stroke_px(0.797) == pytest.approx(LINE_W_THICK, rel=1e-3)


def test_extra_thick_art_keeps_true_width():
    """A seven-segment spine (~3.985 pt) renders far thicker than the body weight
    (proportional to its true width), not snapped down to LINE_W_THICK."""
    seven_seg = items._stroke_px(3.985)
    assert seven_seg == pytest.approx(3.985 * items._PX_PER_PT, rel=1e-6)
    assert seven_seg > LINE_W_THICK * 2          # visibly fatter than a body stroke


def test_line_width_scale_applies():
    """The per-component line-width multiplier scales the pen width."""
    assert items._stroke_px(0.3985, 2.0) == pytest.approx(2.0 * LINE_W, rel=1e-3)
