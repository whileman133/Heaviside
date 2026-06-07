"""
Tests for app/canvas/svgsym.py — symbol geometry, including glyph (+/-) marks.

Symbols whose CircuiTikZ output contains text marks (the +/- of a voltage or
controlled source, op-amp labels, etc.) record those as opaque <use> glyph
references in the geometry. svgsym reconstructs them by reading the original
.svg file. These tests guard that reconstruction so the marks don't silently
disappear (a regression that also surfaced as a packaging bug when the .svg
files were not bundled).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtGui", reason="PySide6 not importable")

from PySide6.QtGui import QGuiApplication  # noqa: E402

try:
    _APP = QGuiApplication.instance() or QGuiApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

import json  # noqa: E402

from app.canvas.style import GEOMETRY_PATH  # noqa: E402
from app.canvas.svgsym import geometry_key, symbol_paths  # noqa: E402


def test_geometry_is_self_contained_for_glyph_kind() -> None:
    """The controlled-source's +/- marks are baked into the geometry (`glyphs`),
    so the app needs no .svg access at run time."""
    with open(GEOMETRY_PATH, encoding="utf-8") as fh:
        geometry = json.load(fh)
    entry = geometry[geometry_key("cV")]
    assert entry["glyphs"], "cV must carry baked glyph marks"
    g = entry["glyphs"][0]
    assert g["d"].lstrip()[:1] in "Mm"          # real path geometry, not a placeholder
    assert len(g["matrix"]) == 6                 # baked affine transform


def test_cV_paths_all_real_geometry() -> None:
    """Every path returned for cV has real geometry — no opaque glyph-ref leaks.

    The +/- glyph marks are resolved into concrete filled paths; if svgsym let
    one through unresolved it would be an empty/degenerate path. All returned
    paths must carry actual elements.
    """
    paths = symbol_paths("cV")
    assert len(paths) >= 4   # diamond + strokes + the two resolved glyph marks
    for sp in paths:
        assert sp.path.elementCount() > 0


def test_cV_has_more_paths_than_plain_diamond() -> None:
    """cV resolves its glyph marks: it has strictly more paths than the bare
    geometry would (the diamond + its connecting strokes alone)."""
    cv = symbol_paths("cV")
    # A plain resistor carries no glyph marks — sanity that glyph kinds add paths.
    assert len(cv) > len(symbol_paths("R"))


def test_plain_resistor_unaffected() -> None:
    """A glyph-free symbol still renders its strokes."""
    r = symbol_paths("R")
    assert len(r) >= 1
    assert all(sp.path.elementCount() > 0 for sp in r)


def test_filled_diode_body_is_filled() -> None:
    """A filled diode (`D*`) has a filled body path; the plain diode (`D`) does
    not — so toggling the filled option visibly changes the canvas (regression).

    The fill is a bare dvisvgm `<path>` (SVG default black fill); recording it as
    `fill='none'` previously made `D` and `D*` render identically.
    """
    assert not any(sp.filled for sp in symbol_paths("D")), "plain D must be unfilled"
    assert any(sp.filled for sp in symbol_paths("D*")), "D* must have a filled body"


def test_stroke_only_symbols_not_filled() -> None:
    """Pure outline symbols (inductor, capacitor, resistor) have no filled paths
    — a guard that the 'bare path = fill' rule does not over-fill stroked bodies."""
    for kind in ("L", "C", "R"):
        assert not any(sp.filled for sp in symbol_paths(kind)), kind
