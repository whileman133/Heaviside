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


def test_resolve_tikz_color_custom_rgb_and_xcolor_names():
    """Issue #49: the canvas resolves a custom {rgb,255:…} pick and uses xcolor's
    values for named colours (so the canvas matches the LaTeX render)."""
    from app.canvas.items import resolve_tikz_color

    assert resolve_tikz_color("{rgb,255:red,10;green,20;blue,30}").getRgb()[:3] == (10, 20, 30)
    assert resolve_tikz_color("orange").getRgb()[:3] == (255, 128, 0)
    assert resolve_tikz_color("violet!50").getRgb()[:3] == (192, 128, 192)



@pytest.mark.parametrize("rotation", [0, 45, 90, 180, 270])
@pytest.mark.parametrize("mirror", [False, True])
def test_upright_glyph_cancels_item_rotation_and_mirror(rotation, mirror):
    """Issue #49: a flip-flop letter pre-transformed by upright_glyph and then
    drawn under the item transform is the original glyph, merely moved: no
    rotation, no reflection (so it reads like CircuiTikZ's upright letters)."""
    from PySide6.QtGui import QPainterPath, QTransform
    from app.canvas.items import upright_glyph

    glyph = QPainterPath()
    glyph.moveTo(10, 5); glyph.lineTo(14, 5); glyph.lineTo(10, 12); glyph.closeSubpath()
    t = QTransform()
    if mirror:
        t.scale(-1.0, 1.0)
    t.rotate(rotation)
    shown = t.map(upright_glyph(glyph, t))
    c0, c1 = glyph.boundingRect().center(), shown.boundingRect().center()
    dx, dy = c1.x() - c0.x(), c1.y() - c0.y()
    for i in range(glyph.elementCount()):
        a, b = glyph.elementAt(i), shown.elementAt(i)
        assert b.x == pytest.approx(a.x + dx, abs=1e-6)
        assert b.y == pytest.approx(a.y + dy, abs=1e-6)
    # …and it stays where the item transform puts the glyph's centre.
    mapped_c = t.map(c0)
    assert (c1.x(), c1.y()) == (pytest.approx(mapped_c.x()), pytest.approx(mapped_c.y()))


def test_text_kinds_have_tagged_glyphs():
    from app.canvas.svgsym import symbol_paths
    from app.components import library

    for kind in ("flipflop D", "latch", "dipchip", "dsp", "adc", "thRn"):
        assert any(sp.glyph for sp in symbol_paths(kind)), kind
    assert library.has_upright_letters("flipflop D")
    assert library.has_upright_letters("dsp")
    assert library.text_unflip_category("thRn") == "body"
    assert library.text_unflip_category("op amp") is None
    assert library.text_unflip_category("flipflop") is None


@pytest.mark.parametrize("kind, labels", [
    ("latch", 4), ("flipflop JK", 4), ("flipflop D", 3), ("flipflop SR", 4),
    ("flipflop T", 3), ("dsp", 1), ("fft", 1), ("adc", 2), ("dac", 2),
    ("phaseshifter", 1), ("vphaseshifter", 1), ("thRn", 1), ("thRp", 1),
    ("dipchip", 8), ("qfpchip", 16),
])
def test_glyphs_group_into_printed_labels(kind, labels):
    """Glyphs group into one cluster per printed label, so a label turns as a
    unit: "DSP" (letters up to ~0.4× height apart), Q̄ (a Q plus a separate
    overline rule), two-digit chip numbers — but adc's A and D stay apart."""
    from app.canvas.items import _glyph_clusters
    from app.canvas.svgsym import symbol_paths

    rects = [sp.path.boundingRect() for sp in symbol_paths(kind) if sp.glyph]
    assert len(_glyph_clusters(rects)) == labels


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_every_text_kind_paints_mirrored(rotation, capfd):
    """Paint each listed kind mirrored (both canvas text paths: upright and
    body) — a regression guard for the paint code itself. Items are painted by
    direct call, which raises (PySide only *prints* an exception from a paint it
    calls itself, e.g. via scene.render)."""
    from PySide6.QtGui import QImage, QPainter
    from app.canvas.scene import SchematicScene
    from app.components import library
    from app.components.registry import REGISTRY

    scene = SchematicScene()
    kinds = sorted(k for k, d in REGISTRY.items() if d.tikz_keyword in library.TEXT_UNFLIP)
    for i, k in enumerate(kinds):
        c = scene.place_component(k, (6.0 * i, 0.0))
        scene.rotate_component(c.id, rotation)
        scene.mirror_component(c.id, True)
    img = QImage(400, 100, QImage.Format_ARGB32)
    p = QPainter(img)
    try:
        for item in list(scene._comp_items.values()):
            item.paint(p, None, None)     # a direct call raises on a paint-code error
    finally:
        p.end()
    err = capfd.readouterr().err
    assert "Error calling Python override" not in err, err


@pytest.mark.parametrize("rotation", [0, 45, 90, 180, 270])
def test_body_text_unmirrors_and_stays_most_upright(rotation):
    """Text that turns with the part (a thermistor's ϑ): under a mirroring item
    transform each label is flipped in place so its net transform is a pure
    rotation (un-mirrored), the most upright of the two candidates."""
    import math
    from PySide6.QtGui import QPainterPath, QTransform
    from app.canvas.items import unmirrored_glyphs

    glyph = QPainterPath()
    glyph.moveTo(0, 0); glyph.lineTo(6, 0); glyph.lineTo(0, 9); glyph.closeSubpath()
    t = QTransform()
    t.scale(-1.0, 1.0)
    t.rotate(rotation)
    out = unmirrored_glyphs([glyph], t, rotation)[0]
    # recover the linear part acting on the glyph: shown = t ∘ (local flip)
    a, b = glyph.elementAt(1), glyph.elementAt(2)        # (6,0), (0,9) from (0,0)
    o = out.elementAt(0)
    u = out.elementAt(1); v = out.elementAt(2)
    m = QTransform((u.x - o.x) / 6, (u.y - o.y) / 6, (v.x - o.x) / 9, (v.y - o.y) / 9, 0, 0) * t
    assert m.determinant() > 0                             # no reflection left
    ang = abs((math.degrees(math.atan2(m.m12(), m.m11())) + 180) % 360 - 180)
    assert ang <= 90 + 1e-6                                # the more upright choice
