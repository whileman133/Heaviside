"""
Tests for app/canvas/svgsym.py — symbol geometry, including glyph (+/-) marks.

Symbols whose CircuiTikZ output contains text marks (the +/- of a voltage or
controlled source, op-amp labels, etc.) record those as opaque <use> glyph
references in the manifest. svgsym reconstructs them by reading the original
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

from app.canvas.svgsym import _source_svg_path, symbol_paths  # noqa: E402


def test_source_svg_present_for_glyph_kind() -> None:
    """The controlled-source .svg (carrying its +/- glyphs) must be findable."""
    assert _source_svg_path("cV") is not None


def test_cV_paths_all_real_geometry() -> None:
    """Every path returned for cV has real geometry — no opaque glyph-ref leaks.

    The manifest stores the +/- as `g…`-style <use> placeholders; if svgsym let
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
