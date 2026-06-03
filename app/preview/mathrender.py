r"""
Vector rendering of LaTeX fragments for on-canvas WYSIWYG labels (prototype).

This reuses the *exact* toolchain that produced the component symbols
(``tools/export_circuitikz_svgs.py``)::

    latex -> .dvi -> dvisvgm --no-fonts -> SVG -> svgsym.parse_path -> QPainterPath

``dvisvgm --no-fonts`` emits every glyph as a ``<path>`` outline in ``<defs>``
referenced by ``<use x y>`` placements, plus any rule geometry (fraction bars,
sqrt vinculum) as direct ``<path>`` / ``<rect>`` elements.  All coordinates are
in **LaTeX point units** (the SVG ``viewBox`` is sized in pt, 1 SVG unit = 1 pt).

The returned :class:`QPainterPath` is normalised so the fragment's bounding-rect
top-left sits at the local origin, in pt units.  Callers scale by
``GRID_PX / _PT_PER_GU`` (the same factor used to size label QFonts) so a 10 pt
fragment lands at the same on-canvas size as a 10 pt text label.

Caching is two-tier:
  * an in-process ``lru_cache`` of the parsed :class:`QPainterPath`;
  * an on-disk cache of the compiled SVG text keyed by a content hash, so a
    fragment seen in a previous session re-parses instantly without invoking
    ``latex``.

Rendering is *pure* and side-effect-free apart from the disk cache, so it can be
called from a worker thread (parsing a cached SVG is sub-millisecond; the slow
path is the one-time ``latex`` compile).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QPainterPath, QTransform

from app.canvas.svgsym import parse_path
# The TikZ-style option splitter lives in the no-Qt ``components.style`` module
# so the code generator can share it; re-exported under its historical private
# name for the canvas label parser and its tests.
from app.components.style import split_top_level as _split_top_level
from app.preview.latex import _ensure_tool_dirs_on_path

# Body font size of the render template, in LaTeX points.  Callers scale a
# rendered path by ``font_size / TEMPLATE_PT`` to reach the desired point size.
TEMPLATE_PT = 10.0

# ---------------------------------------------------------------------------
# LaTeX document template
# ---------------------------------------------------------------------------

# A minimal standalone doc whose page crops tightly to the typeset fragment.
# The default 10 pt body size matches the label/text-node font sizing in
# app/canvas/items.py (px = pt * GRID_PX / _PT_PER_GU).
#
# A leading ``\strut`` pins the *baseline* to a constant device-y across every
# fragment (it forces the standard height/depth), so render_latex can normalise
# all fragments to a shared baseline — see _baseline_y().  ``\strut`` is
# zero-width, so it adds no horizontal ink.
_TEMPLATE = r"""\documentclass[border=0pt]{standalone}
\usepackage{amsmath}
\usepackage{amssymb}
\begin{document}
\strut %FRAGMENT%
\end{document}
"""

# Bump when _TEMPLATE or the path-normalisation changes, to invalidate the
# on-disk SVG cache (keyed by fragment text, which the template change alone
# would not otherwise invalidate).
_RENDER_VERSION = 2

_HREF = "{http://www.w3.org/1999/xlink}href"
_MATRIX_RE = re.compile(
    r"matrix\(\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s+"
    r"([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s*\)"
)


# ---------------------------------------------------------------------------
# Disk cache
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _cache_dir() -> Path:
    d = Path(tempfile.gettempdir()) / "heaviside-mathcache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_key(fragment: str) -> str:
    keyed = f"{_RENDER_VERSION}\x00{fragment}"
    return hashlib.sha256(keyed.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Compilation: fragment -> SVG text  (disk-cached)
# ---------------------------------------------------------------------------

def _compile_svg(fragment: str, *, timeout: int = 20) -> str | None:
    """Return dvisvgm SVG text for *fragment*, or ``None`` on failure.

    The result is cached on disk by content hash.  A sentinel empty file marks a
    previous compile failure so we don't repeatedly shell out for a bad fragment.
    """
    cache_file = _cache_dir() / f"{_cache_key(fragment)}.svg"
    if cache_file.exists():
        text = cache_file.read_text(encoding="utf-8")
        return text or None

    _ensure_tool_dirs_on_path()
    if shutil.which("latex") is None or shutil.which("dvisvgm") is None:
        return None

    tex = _TEMPLATE.replace("%FRAGMENT%", fragment)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "m.tex").write_text(tex, encoding="utf-8")
        try:
            r = subprocess.run(
                ["latex", "-interaction=nonstopmode", "-halt-on-error", "m.tex"],
                cwd=tmp, capture_output=True, timeout=timeout,
            )
            if r.returncode != 0 or not (tmp_path / "m.dvi").exists():
                cache_file.write_text("", encoding="utf-8")
                return None
            r = subprocess.run(
                ["dvisvgm", "--no-fonts", "m.dvi", "-o", "m.svg"],
                cwd=tmp, capture_output=True, timeout=timeout,
            )
            svg_file = tmp_path / "m.svg"
            if r.returncode != 0 or not svg_file.exists():
                cache_file.write_text("", encoding="utf-8")
                return None
            text = svg_file.read_text(encoding="utf-8")
        except (subprocess.TimeoutExpired, OSError):
            return None

    cache_file.write_text(text, encoding="utf-8")
    return text


# ---------------------------------------------------------------------------
# SVG -> QPainterPath
# ---------------------------------------------------------------------------

def _matrix(transform: str | None) -> QTransform:
    if not transform:
        return QTransform()
    m = _MATRIX_RE.search(transform)
    if not m:
        return QTransform()
    a, b, c, d, e, f = (float(v) for v in m.groups())
    return QTransform(a, b, c, d, e, f)


def _svg_to_path(svg_text: str) -> QPainterPath:
    """Parse dvisvgm ``--no-fonts`` SVG into one combined QPainterPath (pt units)."""
    root = ET.fromstring(svg_text)

    def tag(el: ET.Element) -> str:
        return el.tag.rsplit("}", 1)[-1]

    # Glyph outline templates live in <defs>, keyed by id.
    glyph_defs: dict[str, str] = {}
    for el in root.iter():
        if tag(el) == "path" and el.get("id"):
            glyph_defs[el.get("id")] = el.get("d", "")

    out = QPainterPath()

    def walk(el: ET.Element, ctm: QTransform) -> None:
        for child in el:
            t = tag(child)
            local = _matrix(child.get("transform")) * ctm
            if t == "g":
                walk(child, local)
            elif t == "use":
                ref = (child.get(_HREF) or child.get("href") or "").lstrip("#")
                d = glyph_defs.get(ref)
                if not d:
                    continue
                placed = QTransform()
                placed.translate(float(child.get("x", "0")), float(child.get("y", "0")))
                out.addPath((placed * local).map(parse_path(d)))
            elif t == "path" and not child.get("id"):
                # Direct rule geometry (fraction bars, radicals).
                out.addPath(local.map(parse_path(child.get("d", ""))))
            elif t == "rect":
                rp = QPainterPath()
                rp.addRect(
                    float(child.get("x", "0")), float(child.get("y", "0")),
                    float(child.get("width", "0")), float(child.get("height", "0")),
                )
                out.addPath(local.map(rp))

    walk(root, QTransform())
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _baseline_y() -> float:
    """Device-y of the text baseline in raw render coordinates (pt).

    Because every fragment is typeset behind a ``\\strut`` (see _TEMPLATE), the
    baseline lands at the same device-y for all fragments.  We recover it from a
    calibration render of ``x`` — a glyph with zero depth, so its ink bottom *is*
    the baseline.
    """
    svg = _compile_svg("x")
    if not svg:
        return 0.0
    p = _svg_to_path(svg)
    return 0.0 if p.isEmpty() else p.boundingRect().bottom()


@lru_cache(maxsize=512)
def render_latex(fragment: str) -> QPainterPath | None:
    """Render a LaTeX *fragment* to a QPainterPath in pt units.

    The path is normalised so its **baseline is at y=0** and its **left ink edge
    at x=0**; ascenders have negative y, descenders positive y.  This lets
    callers anchor multiple fragments to a shared baseline.  Returns ``None`` if
    the fragment is empty or fails to compile.  Cached in-process; the SVG is
    cached on disk.
    """
    fragment = fragment.strip()
    if not fragment:
        return None
    svg = _compile_svg(fragment)
    if not svg:
        return None
    path = _svg_to_path(svg)
    if path.isEmpty():
        return None
    norm = QTransform()
    norm.translate(-path.boundingRect().left(), -_baseline_y())
    return norm.map(path)


# ---------------------------------------------------------------------------
# Asynchronous rendering (off the UI thread)
# ---------------------------------------------------------------------------
#
# render_latex() may shell out to ``latex`` (hundreds of ms) on a cold cache, so
# canvas items request renders asynchronously and keep showing raw text until
# the vector path arrives.  A bounded QThreadPool runs the compiles; results are
# delivered back on the caller's (UI) thread via a queued signal.

class _RenderSignals(QObject):
    done = Signal(str, object)  # fragment, QPainterPath | None


class _RenderTask(QRunnable):
    def __init__(self, fragment: str, signals: "_RenderSignals") -> None:
        super().__init__()
        self._fragment = fragment
        self._signals = signals

    def run(self) -> None:  # noqa: D401 - QRunnable hook
        try:
            path = render_latex(self._fragment)
        except Exception:  # pragma: no cover - defensive; never kill the pool
            path = None
        self._signals.done.emit(self._fragment, path)


@lru_cache(maxsize=1)
def _pool() -> QThreadPool:
    pool = QThreadPool()
    pool.setMaxThreadCount(2)  # latex is heavy; cap concurrent compiles
    return pool


# Keep signal objects alive until their task fires (else they're GC'd mid-flight).
_live_signals: set[_RenderSignals] = set()


def render_async(fragment: str, on_done) -> None:  # noqa: ANN001
    """Render *fragment* on a worker thread; call ``on_done(QPainterPath|None)``
    on the calling thread when ready.

    Cache hits still hop through the pool but return almost immediately.
    """
    fragment = (fragment or "").strip()
    if not fragment:
        on_done(None)
        return
    signals = _RenderSignals()

    def _relay(_frag: str, path) -> None:  # noqa: ANN001
        _live_signals.discard(signals)
        on_done(path)

    signals.done.connect(_relay)
    _live_signals.add(signals)
    _pool().start(_RenderTask(fragment, signals))


# ---------------------------------------------------------------------------
# Option-string -> displayable LaTeX
# ---------------------------------------------------------------------------

# CircuiTikZ label/annotation slots whose *values* are what actually render near
# a component.  Leading "key=" is stripped so the canvas shows "R_1", not "l=R_1".
_LABEL_KEYS = ("l", "l_", "l^", "v", "v_", "v^", "i", "i_", "i^", "a", "t", "f")

# Slots placed on a *side* of the component body (as opposed to ``t``, the
# in-body bipole-box text).  Used by slot_fragments() for per-side placement.
_SIDE_KEYS = ("l", "l_", "l^", "v", "v_", "v^", "i", "i_", "i^", "a", "a_", "a^")


def slot_fragments(options: str) -> list[tuple[str, str]]:
    r"""Parse *options* into ``(slot_key, latex)`` pairs for per-side placement.

    Only side-placed annotation slots contribute (``l``/``v``/``i``/``a``
    families); the in-body ``t=`` box label and styling flags are excluded.
    Empty values (e.g. ``v^=\,``) are dropped.  Order follows the options string.

        ``l=$R_1$, v=$V_s$`` -> ``[("l", "$R_1$"), ("v", "$V_s$")]``
    """
    out: list[tuple[str, str]] = []
    for seg in _split_top_level(options):
        key, eq, val = seg.partition("=")
        k = key.strip()
        if eq and k in _SIDE_KEYS and val.strip():
            out.append((k, val.strip()))
    return out


def slot_side(key: str) -> str:
    """Conventional side ('above' / 'below') for a slot key.

    ``^`` forces above, ``_`` forces below; otherwise labels (``l``) and current
    (``i``) sit above, voltage (``v``) sits below — a readable default, not a
    pixel-exact reproduction of CircuiTikZ's voltage/current arrow placement.
    """
    if key.endswith("^"):
        return "above"
    if key.endswith("_"):
        return "below"
    return "below" if key.startswith("v") else "above"


def label_display_latex(options: str) -> str:
    r"""Extract the renderable LaTeX from a bipole *options* string.

    ``l=$\bar{R}_\mathrm{dl}$, v=$V_s$`` -> ``$\bar{R}_\mathrm{dl}$\ \ $V_s$``.
    Only segments whose key is a recognised label slot (``l``/``v``/``i``/...)
    contribute; styling flags (``mirror``, ``invert``) and other options are
    dropped so they don't clutter the canvas.
    """
    parts: list[str] = []
    for seg in _split_top_level(options):
        key, eq, val = seg.partition("=")
        if eq and key.strip() in _LABEL_KEYS and val.strip():
            parts.append(val.strip())
    return r"\ \ ".join(parts)


def options_to_editable(options: str) -> str:
    """Format a comma-separated options string with one slot per line for editing.

    ``l=$R_1$, v=$V_s$`` -> ``l=$R_1$\\nv=$V_s$``.  Splits only on top-level
    commas (not those inside ``$...$`` / ``{...}``), so math like
    ``\\theta_{s,0}`` is preserved.
    """
    return "\n".join(s.strip() for s in _split_top_level(options) if s.strip())


def editable_to_options(text: str) -> str:
    """Inverse of :func:`options_to_editable`: join the edited lines back with
    ``, ``.  Blank lines are dropped."""
    return ", ".join(ln.strip() for ln in text.split("\n") if ln.strip())

