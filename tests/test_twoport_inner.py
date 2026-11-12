"""
Two-port path blocks (adc, dac, filters, converters, …): CircuiTikZ turns the box
and leads with the path but keeps the inner symbol upright (and swaps the A/D
halves on a backwards path). The canvas must draw the same thing at every
rotation, mirrored or not (app/canvas/items.twoport_inner_paths).

The expected corners below were read off the pdflatex render of
``generate(schematic, y_flip=True)`` for each rotation/mirror cell.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QTransform  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - host-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.canvas import items  # noqa: E402
from app.canvas.style import GRID_PX  # noqa: E402
from app.canvas.svgsym import symbol_paths  # noqa: E402
from app.components import library  # noqa: E402
from app.components.registry import REGISTRY  # noqa: E402


def _item_xform(rotation: int, mirror: bool) -> QTransform:
    """The ComponentItem transform: rotate, then the global Flip-X."""
    t = QTransform()
    if mirror:
        t.scale(-1.0, 1.0)
    t.rotate(rotation)
    return t


def _scene_paths(kind: str, rotation: int, mirror: bool):
    syms = symbol_paths(kind)
    t = _item_xform(rotation, mirror)
    local = items.twoport_inner_paths(kind, [s.path for s in syms], t)
    assert local is not None
    return syms, [t.map(p) for p in local]


def _diagonal_index(syms) -> int:
    """The thin, unfilled, box-sized path: the A/D split line."""
    box_h = max(s.path.boundingRect().height() for s in syms)
    return next(i for i, s in enumerate(syms)
                if not s.filled and s.stroke_width < 0.6
                and s.path.boundingRect().height() > 0.9 * box_h
                and s.path.boundingRect().width() < 2 * box_h)


def _slope_sign(path) -> str:
    """``"/"`` when the line rises to the right on screen (y down), else ``"\\"``."""
    a = path.elementAt(0)
    b = path.elementAt(path.elementCount() - 1)
    (x0, y0), (x1, y1) = sorted([(a.x, a.y), (b.x, b.y)])
    return "/" if y1 < y0 else "\\"


def _corner(rect, centre: QPointF) -> str:
    c = rect.center()
    return ("T" if c.y() < centre.y() else "B") + ("L" if c.x() < centre.x() else "R")


# (rotation, mirror) -> (diagonal, corner of "A") in the compiled export.
_ADC_EXPECTED = {
    (0, False): ("/", "TL"), (90, False): ("/", "TL"),
    (180, False): ("/", "BR"), (270, False): ("/", "BR"),
    (0, True): ("\\", "TR"), (90, True): ("\\", "TR"),
    (180, True): ("\\", "BL"), (270, True): ("\\", "BL"),
}


@pytest.mark.parametrize("rotation,mirror", sorted(_ADC_EXPECTED))
def test_adc_diagonal_and_letters_match_export(rotation, mirror):
    syms, scene = _scene_paths("adc", rotation, mirror)
    diag = _diagonal_index(syms)
    # "A" is the glyph in the upper-left half of the baked (rotation 0) symbol.
    box = syms[diag].path.boundingRect()
    a_idx = next(i for i, s in enumerate(syms)
                 if s.filled and s.path.boundingRect().center().y() < box.center().y())
    centre = scene[diag].boundingRect().center()
    want_diag, want_a = _ADC_EXPECTED[(rotation, mirror)]
    assert _slope_sign(scene[diag]) == want_diag
    assert _corner(scene[a_idx].boundingRect(), centre) == want_a


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_dac_letters_swap_like_adc(rotation):
    """dac draws D where adc draws A, with the same swap rule."""
    syms, scene = _scene_paths("dac", rotation, False)
    diag = _diagonal_index(syms)
    box = syms[diag].path.boundingRect()
    d_idx = next(i for i, s in enumerate(syms)
                 if s.filled and s.path.boundingRect().center().y() < box.center().y())
    centre = scene[diag].boundingRect().center()
    assert _slope_sign(scene[diag]) == "/"
    assert _corner(scene[d_idx].boundingRect(), centre) == _ADC_EXPECTED[(rotation, False)][1]


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_filter_waves_stay_horizontal(rotation):
    """A lowpass's waves run horizontally on screen at every rotation (they used to
    stand vertically at 90°/270°)."""
    syms, scene = _scene_paths("lowpass", rotation, False)
    lead_w = max(s.path.boundingRect().width() for s in syms)
    # The thin paths other than the lead (the box is the thick one).
    waves = [p for s, p in zip(syms, scene) if s.path.boundingRect().width() < 0.9 * lead_w
             and s.stroke_width < 0.6]
    assert waves
    for p in waves:
        r = p.boundingRect()
        assert r.width() > r.height()


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("mirror", [False, True])
def test_lead_is_untouched(rotation, mirror):
    """The full-span lead keeps running pin to pin."""
    syms = symbol_paths("adc")
    out = items.twoport_inner_paths("adc", [s.path for s in syms],
                                    _item_xform(rotation, mirror))
    lead = max(range(len(syms)), key=lambda i: syms[i].path.boundingRect().width())
    assert out[lead] is syms[lead].path or out[lead] == syms[lead].path


@pytest.mark.parametrize("kind", ["resistor", "detector", "amp", "bipole"])
def test_other_path_symbols_turn_rigidly(kind):
    syms = symbol_paths(kind)
    assert items.twoport_inner_paths(kind, [s.path for s in syms], _item_xform(90, False)) is None


@pytest.mark.parametrize("direction,expected", [
    (0, 0), (90, 270), (180, 180), (270, 90),
    # 45° ties fall the way CircuiTikZ's truncating thresholds put them.
    (45, 0), (135, 180), (225, 90), (315, 0),
    (-90, 90), (359.9999999, 0), (89.99999999, 270),
])
def test_twoport_inner_rotation_thresholds(direction, expected):
    assert library.twoport_inner_rotation(direction) == expected


def test_swap_kinds_are_twoports():
    assert set(library.TWOPORT_SWAP_ANCHORS) <= library.TWOPORT_UPRIGHT_INNER


@pytest.mark.parametrize("kind", sorted(
    k for k, d in REGISTRY.items() if d.tikz_keyword in library.TWOPORT_UPRIGHT_INNER))
def test_twoport_geometry_is_lead_plus_square_box(kind):
    """The canvas turns everything but the lead about the box centre, which is only
    right if the lead is the one full-span path and the box is a square centred on
    the symbol (so it maps onto itself)."""
    syms = symbol_paths(kind)
    rects = [s.path.boundingRect() for s in syms]
    lead = max(range(len(rects)), key=lambda i: rects[i].width())
    assert rects[lead].height() < 1e-6
    assert rects[lead].left() == pytest.approx(0.0, abs=0.5)
    assert rects[lead].right() == pytest.approx(REGISTRY[kind].default_span[0] * GRID_PX, abs=0.5)
    body = None
    for i, r in enumerate(rects):
        if i != lead:
            body = r if body is None else body.united(r)
    assert body.width() == pytest.approx(body.height(), abs=0.5)
    assert body.center().x() == pytest.approx(rects[lead].center().x(), abs=0.5)
    assert body.center().y() == pytest.approx(0.0, abs=0.5)


@pytest.mark.parametrize("kind", ["adc", "dac", "dsp", "fft", "phaseshifter"])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("mirror", [False, True])
def test_twoport_text_stays_upright_at_the_twoport_position(kind, rotation, mirror):
    """Issue #49 + two-port composition, as ComponentItem.paint does it: the
    two-port step places each label (A/D corner swap, counter-rotation), and the
    readable-text step keeps the label's *shape* upright and un-mirrored. Turning
    the already-placed glyph upright instead left the counter-rotation on it
    (sideways DSP at 90°, upside down at 180°)."""
    syms = symbol_paths(kind)
    t = _item_xform(rotation, mirror)
    placed = items.twoport_inner_paths(kind, [s.path for s in syms], t)
    idx = [i for i, s in enumerate(syms) if s.glyph]
    fixed = items.readable_glyphs(kind, [syms[i].path for i in idx], t, rotation,
                                  placed=[placed[i] for i in idx])
    for i, f in zip(idx, fixed):
        orig, shown = syms[i].path, t.map(f)
        # upright + un-mirrored: the shown glyph is the local glyph, only moved
        dx = shown.elementAt(0).x - orig.elementAt(0).x
        dy = shown.elementAt(0).y - orig.elementAt(0).y
        for k in range(orig.elementCount()):
            assert shown.elementAt(k).x == pytest.approx(orig.elementAt(k).x + dx, abs=1e-6)
            assert shown.elementAt(k).y == pytest.approx(orig.elementAt(k).y + dy, abs=1e-6)
    # …and each label sits where the two-port step put it (same label centre).
    clusters = items._glyph_clusters([syms[i].path.boundingRect() for i in idx])
    for g in clusters:
        want = placed[idx[g[0]]].boundingRect()
        got = fixed[g[0]].boundingRect()
        for j in g[1:]:
            want = want.united(placed[idx[j]].boundingRect())
            got = got.united(fixed[j].boundingRect())
        assert got.center().x() == pytest.approx(want.center().x(), abs=1e-6)
        assert got.center().y() == pytest.approx(want.center().y(), abs=1e-6)
