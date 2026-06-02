"""
Tests for the QtPdf-based preview renderer (app/preview/latex.pdf_to_qimage).

These need a Qt application (QImage / QtPdf) and a real compiled PDF, so they
require pdflatex and are skipped without it. No Poppler is involved.
"""

from __future__ import annotations

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")
pytest.importorskip("PySide6.QtPdf", reason="QtPdf not available")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.codegen.circuitikz import generate  # noqa: E402
from app.preview.latex import (  # noqa: E402
    CompileError,
    build_tex,
    compile_tex,
    pdf_to_qimage,
)
from app.schematic.model import Component, Schematic  # noqa: E402


def _resistor_pdf() -> bytes:
    r = Component(id="r1", kind="R", position=(0.0, 0.0), rotation=0,
                  options="l=$R_1$", mirror=False)
    src = generate(Schematic(version="0.1", name="t", components=[r], wires=[]),
                   y_flip=True)
    return compile_tex(build_tex(src))


pytestmark = pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="requires pdflatex"
)


def test_pdf_to_qimage_renders_non_null() -> None:
    """A compiled PDF renders to a non-null QImage with sensible dimensions."""
    img = pdf_to_qimage(_resistor_pdf(), dpi=150)
    assert not img.isNull()
    assert img.width() > 10 and img.height() > 10


def test_pdf_to_qimage_dpi_scales_size() -> None:
    """Higher dpi yields a proportionally larger raster (same source page)."""
    pdf = _resistor_pdf()
    low = pdf_to_qimage(pdf, dpi=75)
    high = pdf_to_qimage(pdf, dpi=300)
    # 4x the dpi → ~4x the width (allow rounding slack).
    assert high.width() > low.width() * 3


def test_pdf_to_qimage_bad_pdf_raises() -> None:
    """Garbage input does not crash — it raises a clean error."""
    with pytest.raises((CompileError, RuntimeError)):
        pdf_to_qimage(b"not a pdf at all")
