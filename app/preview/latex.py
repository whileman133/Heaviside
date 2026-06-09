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

pdf_to_svg(pdf_bytes: bytes, *, timeout: int = 30) -> bytes
    Convert a PDF (given as bytes) to an SVG using pdftocairo (the same Poppler
    tool used for EPS — no extra dependency).  Raises CompileError if pdftocairo
    is missing or fails (§8.6).

check_dependencies() -> list[str]
    Return human-readable warnings for missing *required* tools (just pdflatex;
    the preview no longer needs Poppler).  Empty list means all present.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Tool discovery
# ---------------------------------------------------------------------------
#
# Which binary to run for each external tool (honouring a user-configured path,
# else PATH) is decided by app.preview.tools.  ``_ensure_tool_dirs_on_path`` is
# re-exported for the modules/tests that still import it from here.

from app.preview.tools import ensure_tool_dirs_on_path as _ensure_tool_dirs_on_path  # noqa: E402
from app.preview import tools as _tools  # noqa: E402


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

# Dark variant used **only for the on-screen preview** (never for exports): a
# dark page colour and a light default ink so the rendered figure blends into a
# dark UI instead of glaring white. The colours match the canvas dark palette
# (app/canvas/style._DARK: COLOR_BACKGROUND / COLOR_NORMAL). circuitikz draws and
# labels inherit the ambient ``\color``; elements with an explicit colour (e.g. a
# user fill) keep it. xcolor is already loaded by tikz, so no extra package.
_SCHEMATIC_TEMPLATE_DARK = r"""\documentclass[border=4pt]{standalone}
\usepackage[american]{circuitikz}
\usetikzlibrary{arrows.meta}
\ctikzset{voltage=american, current=american, resistor=american}
\definecolor{hvbg}{HTML}{1E1F22}
\definecolor{hvfg}{HTML}{E6E6E6}
\begin{document}
\pagecolor{hvbg}\color{hvfg}
% CIRCUITIKZ_SOURCE
\end{document}
"""


def build_tex(circuitikz_source: str, dark: bool = False) -> str:
    """
    Wrap a CircuiTikZ environment string in the minimal standalone template.

    The source must already be in CircuiTikZ Y-up convention — i.e. generated
    with ``generate(schematic, y_flip=True)``.  This function is a pure
    template wrapper; Y-negation and rotation correction are handled in the
    codegen layer, not here.

    When *dark* is True the document gets a dark page colour and light default
    ink — used for the **preview only** so it reads against a dark UI. Exports
    always use the default (light) template, so the distributed figure stays
    white-paper/black-ink. The light output is byte-for-byte unchanged.
    """
    template = _SCHEMATIC_TEMPLATE_DARK if dark else _SCHEMATIC_TEMPLATE
    return template.replace("% CIRCUITIKZ_SOURCE", circuitikz_source)


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
        If pdflatex is not found (PATH or a configured path), exits with a
        non-zero status, or exceeds *timeout*.
    """
    pdflatex = _tools.resolve("pdflatex")
    if pdflatex is None:
        raise CompileError(
            "pdflatex not found. Install TeX Live (texlive-full) or MiKTeX, or "
            "set its path in Preferences → Tools."
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        tex_file = tmp_path / "schematic.tex"
        tex_file.write_text(tex_source, encoding="utf-8")

        try:
            result = subprocess.run(
                [
                    pdflatex,
                    "-interaction=nonstopmode",
                    "-halt-on-error",
                    # Defence in depth: a .hv file may come from an untrusted
                    # third party, and label/text fields flow verbatim into the
                    # generated LaTeX. Explicitly disabling shell-escape ensures a
                    # crafted label can never invoke \write18 / external commands,
                    # regardless of the local TeX installation's default.
                    "-no-shell-escape",
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

def _pdf_to_vector(pdf_bytes: bytes, *, flag: str, ext: str, label: str, timeout: int) -> bytes:
    """Convert *pdf_bytes* to a vector format with ``pdftocairo`` (Poppler).

    *flag* is the pdftocairo output flag (``-eps`` / ``-svg``), *ext* the output
    file extension, and *label* the human-readable format name used in error
    messages.  Both EPS and SVG export use the same Poppler tool — SVG adds no
    dependency beyond what EPS already requires.  The preview itself is rendered
    by Qt (see :func:`pdf_to_qimage`), so a missing Poppler only affects these
    on-demand exports and is reported clearly here.

    The conversion happens in a fresh temporary directory that is removed
    afterward regardless of outcome.

    Raises
    ------
    CompileError
        If ``pdftocairo`` is not found (PATH or a configured path), exits
        non-zero, times out, or produces no output.
    """
    pdftocairo = _tools.resolve("pdftocairo")
    if pdftocairo is None:
        raise CompileError(
            "pdftocairo not found. Install Poppler (poppler-utils) to enable "
            f"{label} export, or set its path in Preferences → Tools."
        )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        pdf_file = tmp_path / "schematic.pdf"
        out_file = tmp_path / f"schematic.{ext}"
        pdf_file.write_bytes(pdf_bytes)

        try:
            result = subprocess.run(
                [pdftocairo, flag, str(pdf_file), str(out_file)],
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

        if not out_file.exists():
            raise CompileError(f"pdftocairo produced no {label} output.")

        return out_file.read_bytes()


def pdf_to_eps(pdf_bytes: bytes, *, timeout: int = 30) -> bytes:
    """
    Convert *pdf_bytes* to an EPS document and return the EPS as bytes.

    Uses ``pdftocairo -eps`` (from Poppler).  ``-eps`` emits Encapsulated
    PostScript with a tight bounding box derived from the PDF's crop box, which
    is exactly what ``\\includegraphics`` expects (§8.6).
    """
    return _pdf_to_vector(pdf_bytes, flag="-eps", ext="eps", label="EPS", timeout=timeout)


def pdf_to_svg(pdf_bytes: bytes, *, timeout: int = 30) -> bytes:
    """
    Convert *pdf_bytes* to an SVG document and return the SVG as bytes.

    Uses ``pdftocairo -svg`` (from Poppler) — the same tool as :func:`pdf_to_eps`,
    so SVG export needs no dependency beyond the one EPS already requires.  The
    standalone PDF is already cropped tight (``standalone`` class), so the SVG
    inherits that tight extent (§8.6).
    """
    return _pdf_to_vector(pdf_bytes, flag="-svg", ext="svg", label="SVG", timeout=timeout)


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

    An empty list means all required tools are present (on PATH or via a
    configured path in Preferences → Tools).

    Only ``pdflatex`` is required for normal use: the preview is rendered by
    Qt's own PDF engine (no Poppler).  ``pdftocairo`` (Poppler) is needed *only*
    for EPS/SVG export and is checked on demand, so it is not reported here — a
    missing Poppler should not warn users who never export EPS/SVG.
    """
    missing: list[str] = []
    if _tools.resolve("pdflatex") is None:
        missing.append(
            "pdflatex not found. Install TeX Live (texlive-full) or MiKTeX to "
            "enable preview, or set its path in Preferences → Tools."
        )
    return missing
