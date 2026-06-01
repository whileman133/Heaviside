"""
Preview pipeline helpers (spec §8).

Public API
----------
build_tex(circuitikz_source: str) -> str
    Wrap a CircuiTikZ environment in the minimal standalone template (§8.3).

compile_tex(tex_source: str, *, timeout: int = 30) -> bytes
    Write *tex_source* to a temp directory, run pdflatex, and return the PDF
    bytes on success.  Raises CompileError on pdflatex failure or timeout.

pdf_to_qimage(pdf_bytes: bytes, dpi: int = 150) -> QImage
    Convert the first page of a PDF (given as bytes) to a QImage using
    pdf2image / pdftoppm.

check_dependencies() -> list[str]
    Return a list of human-readable warning strings for each missing system
    dependency (pdflatex, pdftoppm).  Empty list means all present.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class CompileError(Exception):
    """Raised when pdflatex exits non-zero or times out."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


# ---------------------------------------------------------------------------
# LaTeX templates (spec §8.3)
# ---------------------------------------------------------------------------

_SCHEMATIC_TEMPLATE = r"""\documentclass[border=4pt]{standalone}
\usepackage[american]{circuitikz}
\ctikzset{voltage=american, current=american, resistor=american}
\begin{document}
% CIRCUITIKZ_SOURCE
\end{document}
"""


def build_tex(circuitikz_source: str) -> str:
    """
    Wrap a CircuiTikZ environment string in the minimal standalone template.

    The source must already be in CircuiTikZ Y-up convention — i.e. generated
    with ``generate(schematic, y_flip=True)``.  This function is a pure
    template wrapper; Y-negation and rotation correction are handled in the
    codegen layer, not here.
    """
    return _SCHEMATIC_TEMPLATE.replace("% CIRCUITIKZ_SOURCE", circuitikz_source)


# ---------------------------------------------------------------------------
# Compilation
# ---------------------------------------------------------------------------

def compile_tex(tex_source: str, *, timeout: int = 30) -> bytes:
    """
    Compile *tex_source* with pdflatex and return the resulting PDF as bytes.

    The compilation happens in a fresh temporary directory that is removed
    afterward regardless of outcome.

    Parameters
    ----------
    tex_source:
        Complete .tex document source.
    timeout:
        Maximum wall-clock seconds to wait for pdflatex (default 30).

    Raises
    ------
    CompileError
        If pdflatex is not found on PATH, exits with a non-zero status, or
        exceeds *timeout*.
    """
    if shutil.which("pdflatex") is None:
        raise CompileError(
            "pdflatex not found on PATH. "
            "Install TeX Live (texlive-full) or MiKTeX."
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tex_file = tmp_path / "schematic.tex"
        tex_file.write_text(tex_source, encoding="utf-8")

        try:
            result = subprocess.run(
                [
                    "pdflatex",
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    str(tex_file),
                ],
                cwd=tmp,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompileError(
                f"pdflatex timed out after {timeout}s."
            ) from exc

        if result.returncode != 0:
            log = result.stdout.decode("utf-8", errors="replace")
            raise CompileError(
                f"pdflatex exited with code {result.returncode}.",
                log=log,
            )

        pdf_file = tmp_path / "schematic.pdf"
        if not pdf_file.exists():
            log = result.stdout.decode("utf-8", errors="replace")
            raise CompileError("pdflatex produced no PDF output.", log=log)

        return pdf_file.read_bytes()


# ---------------------------------------------------------------------------
# PDF → QImage
# ---------------------------------------------------------------------------

def pdf_to_qimage(pdf_bytes: bytes, dpi: int = 150):  # -> QImage
    """
    Convert the first page of *pdf_bytes* to a ``QImage``.

    Uses ``pdf2image`` (which wraps ``pdftoppm`` from Poppler).  Import of
    ``pdf2image`` is deferred so that the module is importable in test
    environments where pdf2image is not installed (the function itself would
    fail, but module-level imports do not).

    Parameters
    ----------
    pdf_bytes:
        Raw PDF file content.
    dpi:
        Rendering resolution (default 150 dpi for preview quality).

    Returns
    -------
    QImage
        A ``PySide6.QtGui.QImage`` of the first page.

    Raises
    ------
    CompileError
        If pdf2image / pdftoppm is unavailable or conversion fails.
    RuntimeError
        If the PDF has no pages.
    """
    try:
        from pdf2image import convert_from_bytes  # type: ignore[import]
    except ImportError as exc:
        raise CompileError(
            "pdf2image is not installed. Run: uv add pdf2image"
        ) from exc

    try:
        images = convert_from_bytes(pdf_bytes, dpi=dpi, first_page=1, last_page=1)
    except Exception as exc:
        raise CompileError(f"pdf2image conversion failed: {exc}") from exc

    if not images:
        raise RuntimeError("PDF has no pages.")

    pil_image = images[0]

    # Convert PIL Image → QImage.
    try:
        from PySide6.QtGui import QImage  # type: ignore[import]
    except ImportError as exc:
        raise ImportError("PySide6 is required for pdf_to_qimage.") from exc

    pil_image = pil_image.convert("RGBA")
    data = pil_image.tobytes("raw", "RGBA")
    qimage = QImage(
        data,
        pil_image.width,
        pil_image.height,
        pil_image.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    # Keep a reference to `data` alive for the duration of `qimage`'s life.
    qimage._pil_data = data  # type: ignore[attr-defined]
    return qimage


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def check_dependencies() -> list[str]:
    """
    Return a list of warning strings for missing system dependencies.

    An empty list means all required tools are present on PATH.

    Checked tools:
    - ``pdflatex`` — LaTeX compiler (spec §8.4)
    - ``pdftoppm``  — PDF-to-image converter used by pdf2image (spec §8.4)
    """
    missing: list[str] = []
    if shutil.which("pdflatex") is None:
        missing.append(
            "pdflatex not found on PATH. "
            "Install TeX Live (texlive-full) or MiKTeX to enable preview."
        )
    if shutil.which("pdftoppm") is None:
        missing.append(
            "pdftoppm not found on PATH. "
            "Install Poppler (poppler-utils) to enable preview."
        )
    return missing
