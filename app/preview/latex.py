r"""
Preview pipeline helpers (spec §8).

Public API
----------
build_tex(circuitikz_source: str) -> str
    Wrap a CircuiTikZ environment in the minimal standalone template (§8.3).

build_snippet(circuitikz_source: str) -> str
    Prepend a preamble-requirements comment to a CircuiTikZ environment so the
    result can be \input into an existing LaTeX document (§8.5).

compile_tex(tex_source: str, *, timeout: int = 30) -> bytes
    Write *tex_source* to a temp directory, run pdflatex, and return the PDF
    bytes on success.  Raises CompileError on pdflatex failure or timeout.

pdf_to_qimage(pdf_bytes: bytes, dpi: int = 150) -> QImage
    Render the first page of a PDF (given as bytes) to a QImage using Qt's own
    PDF engine (QtPdf) — no external process, no Poppler.

pdf_to_eps(pdf_bytes: bytes, *, timeout: int = 30) -> bytes
    Convert a PDF (given as bytes) to an EPS with a tight bounding box using
    pdftocairo.  Raises CompileError if pdftocairo is missing or fails (§8.6).

check_dependencies() -> list[str]
    Return human-readable warnings for missing *required* tools (just pdflatex;
    the preview no longer needs Poppler).  Empty list means all present.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Tool discovery (PATH augmentation)
# ---------------------------------------------------------------------------

# Directories where TeX and Poppler binaries commonly live on macOS but which a
# GUI app launched from Finder/Dock does NOT inherit: such an app gets only a
# minimal PATH (``/usr/bin:/bin:/usr/sbin:/sbin``), so pdflatex (and pdftocairo
# for EPS export) appear "missing" even when installed. We append these to PATH so
# the tools are found regardless of how the app was launched. Appending (not
# prepending) preserves any working PATH from a terminal launch.
_MAC_TOOL_DIRS = (
    "/Library/TeX/texbin",   # MacTeX / BasicTeX
    "/usr/local/bin",        # Intel Homebrew, MacPorts symlinks
    "/opt/homebrew/bin",     # Apple Silicon Homebrew
    "/opt/local/bin",        # MacPorts
)


def _ensure_tool_dirs_on_path() -> None:
    """Append common macOS TeX/Poppler bin dirs to PATH if absent.

    Idempotent; only adds directories that exist and are not already on PATH.
    No-op off macOS. Called before any ``shutil.which`` / ``subprocess`` use so
    the dependency check and compilation behave the same whether the app was
    launched from a terminal or from Finder/Dock.
    """
    if platform.system() != "Darwin":
        return
    parts = os.environ.get("PATH", "").split(os.pathsep)
    extra = [d for d in _MAC_TOOL_DIRS if os.path.isdir(d) and d not in parts]
    if extra:
        os.environ["PATH"] = os.pathsep.join([p for p in parts if p] + extra)


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
\usetikzlibrary{arrows.meta}
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


_SNIPPET_HEADER = r"""% CircuiTikZ schematic exported from Heaviside.
% Include in your document with \input{<this file>}.
% Your document preamble must contain:
%   \usepackage[american]{circuitikz}
%   \usetikzlibrary{arrows.meta}
%   \ctikzset{voltage=american, current=american, resistor=american}
"""


def build_snippet(circuitikz_source: str) -> str:
    r"""
    Return an includable ``.tex`` snippet for *circuitikz_source*.

    The result is a comment block listing the required preamble packages
    followed by the bare ``circuitikz`` environment, suitable for ``\input``
    into an existing LaTeX document.  Unlike :func:`build_tex`, it adds no
    ``\documentclass`` or ``\begin{document}`` so it does not stand alone.

    The source must already be in CircuiTikZ Y-up convention — i.e. generated
    with ``generate(schematic, y_flip=True)`` — so the included figure renders
    in the same orientation as the canvas.
    """
    return _SNIPPET_HEADER + circuitikz_source + "\n"


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
    _ensure_tool_dirs_on_path()
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
# PDF → EPS
# ---------------------------------------------------------------------------

def pdf_to_eps(pdf_bytes: bytes, *, timeout: int = 30) -> bytes:
    """
    Convert *pdf_bytes* to an EPS document and return the EPS as bytes.

    Uses ``pdftocairo -eps`` (from Poppler).  ``-eps`` emits Encapsulated
    PostScript with a tight bounding box derived from the PDF's crop box, which
    is exactly what ``\\includegraphics`` expects.  This is the *only* feature
    that still needs Poppler — the preview is rendered by Qt (see
    :func:`pdf_to_qimage`).  A clear error is raised on demand if ``pdftocairo``
    is missing, so users who never export EPS are unaffected.

    The conversion happens in a fresh temporary directory that is removed
    afterward regardless of outcome.

    Raises
    ------
    CompileError
        If ``pdftocairo`` is not found on PATH, exits non-zero, times out, or
        produces no EPS output.
    """
    _ensure_tool_dirs_on_path()
    if shutil.which("pdftocairo") is None:
        raise CompileError(
            "pdftocairo not found on PATH. "
            "Install Poppler (poppler-utils) to enable EPS export."
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf_file = tmp_path / "schematic.pdf"
        eps_file = tmp_path / "schematic.eps"
        pdf_file.write_bytes(pdf_bytes)

        try:
            result = subprocess.run(
                ["pdftocairo", "-eps", str(pdf_file), str(eps_file)],
                cwd=tmp,
                capture_output=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise CompileError(f"pdftocairo timed out after {timeout}s.") from exc

        if result.returncode != 0:
            log = result.stderr.decode("utf-8", errors="replace")
            raise CompileError(
                f"pdftocairo exited with code {result.returncode}.", log=log
            )

        if not eps_file.exists():
            raise CompileError("pdftocairo produced no EPS output.")

        return eps_file.read_bytes()


# ---------------------------------------------------------------------------
# PDF → QImage
# ---------------------------------------------------------------------------

def pdf_to_qimage(pdf_bytes: bytes, dpi: int = 150):  # -> QImage
    """
    Render the first page of *pdf_bytes* to a ``QImage``.

    Uses Qt's own PDF engine (``PySide6.QtPdf.QPdfDocument``) — no external
    process and no Poppler dependency.  The PDF is loaded from an in-memory
    buffer and the page is rendered at *dpi* (page point-size × dpi ÷ 72).

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
        If Qt cannot load or render the PDF.
    RuntimeError
        If the PDF has no pages.
    """
    try:
        from PySide6.QtCore import QBuffer, QByteArray, QSize
        from PySide6.QtPdf import QPdfDocument
    except ImportError as exc:  # pragma: no cover - PySide6/QtPdf always present in app
        raise CompileError(
            "PySide6 QtPdf is required to render the preview."
        ) from exc

    # Load from an in-memory buffer. QBuffer references its QByteArray without
    # copying, so the QByteArray must stay alive for the buffer's lifetime —
    # hold it in a named local, not a temporary. The buffer must also stay open
    # for the duration of the render() call (the document reads from it lazily).
    data = QByteArray(pdf_bytes)
    buffer = QBuffer(data)
    buffer.open(QBuffer.OpenModeFlag.ReadOnly)
    try:
        doc = QPdfDocument()
        status = doc.load(buffer)
        if doc.pageCount() < 1:
            raise RuntimeError("PDF has no pages.")

        point_size = doc.pagePointSize(0)   # 1 pt = 1/72 inch
        width = max(1, round(point_size.width() * dpi / 72.0))
        height = max(1, round(point_size.height() * dpi / 72.0))

        image = doc.render(0, QSize(width, height))
        if image.isNull():
            raise CompileError(f"QtPdf failed to render the PDF page (status: {status}).")
        # render() returns a self-contained QImage (owns its pixels), so it
        # remains valid after the document and buffer are released.
        return image
    finally:
        buffer.close()


# ---------------------------------------------------------------------------
# Dependency check
# ---------------------------------------------------------------------------

def check_dependencies() -> list[str]:
    """
    Return a list of warning strings for missing system dependencies.

    An empty list means all required tools are present on PATH.

    Only ``pdflatex`` is required for normal use: the preview is rendered by
    Qt's own PDF engine (no Poppler).  ``pdftocairo`` (Poppler) is needed *only*
    for EPS export and is checked on demand in :func:`pdf_to_eps`, so it is not
    reported here — a missing Poppler should not warn users who never export EPS.
    """
    _ensure_tool_dirs_on_path()
    missing: list[str] = []
    if shutil.which("pdflatex") is None:
        missing.append(
            "pdflatex not found on PATH. "
            "Install TeX Live (texlive-full) or MiKTeX to enable preview."
        )
    return missing
