r"""
Vector rendering of LaTeX fragments for on-canvas WYSIWYG labels.

Two engines produce a QPainterPath from a fragment; both normalise to a shared
baseline so callers can place them identically (see :func:`render_path`):

  * **latex** (the reference) reuses the *exact* toolchain that produces the
    component symbols (``components/generate_components.py`` /
    ``app/components/render.py``)::

        latex -> .dvi -> dvisvgm --no-fonts -> SVG -> svgsym.parse_path -> QPainterPath

  * **ziamath** (a pure-Python, no-install fallback that bundles STIX Two Math)
    typesets a LaTeX-math subset directly to SVG -> QPainterPath, so canvas labels
    render even with no ``latex``/``dvisvgm`` present.

The active engine is chosen by :func:`_active_engine`: LaTeX when installed, else
ziamath; a debug preference can force ziamath (:func:`set_force_ziamath`).

``dvisvgm --no-fonts`` emits every glyph as a ``<path>`` outline in ``<defs>``
referenced by ``<use x y>`` placements, plus any rule geometry (fraction bars,
sqrt vinculum) as direct ``<path>`` / ``<rect>`` elements.  All coordinates are
in **LaTeX point units** (the SVG ``viewBox`` is sized in pt, 1 SVG unit = 1 pt).

The returned :class:`QPainterPath` is normalised so the fragment's bounding-rect
top-left sits at the local origin, in pt units.  Callers scale by
``GRID_PX / _PT_PER_GU`` (the same factor used to size label QFonts) so a 10 pt
fragment lands at the same on-canvas size as a 10 pt text label.

Caching is two-tier:
  * an in-process bounded LRU memo of the parsed :class:`QPainterPath`
    (successes only — failures are retried, never cached);
  * an on-disk cache of the compiled SVG text keyed by a content hash, so a
    fragment seen in a previous session re-parses instantly without invoking
    ``latex``.

Rendering is *pure* and side-effect-free apart from the disk cache, so it can be
called from a worker thread (parsing a cached SVG is sub-millisecond; the slow
path is the one-time ``latex`` compile).
"""

from __future__ import annotations

import getpass
import hashlib
import os
import re
import subprocess
import tempfile
import threading
import xml.etree.ElementTree as ET
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    QRunnable,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QPainterPath, QTransform

from app.canvas.svgsym import parse_path
# The TikZ-style option splitter lives in the no-Qt ``components.style`` module
# so the code generator can share it; re-exported under its historical private
# name for the canvas label parser and its tests.
from app.components.style import split_top_level as _split_top_level
from app.preview import tools as _tools

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

def _cache_user() -> str:
    """A filesystem-safe identifier for the current user (cache-dir suffix)."""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name (daemon/odd env)
        user = f"uid{os.getuid()}" if hasattr(os, "getuid") else "user"
    return re.sub(r"[^A-Za-z0-9_.-]", "_", user) or "user"


@lru_cache(maxsize=1)
def _cache_dir() -> Path:
    """The on-disk SVG cache directory — **per user**, mode 0700.

    The system temp dir is world-writable, so a fixed shared path
    (``heaviside-mathcache``) would let another local user pre-create it and
    plant/replace cache entries that this process then trusts. The directory is
    therefore suffixed with the user name and created private (0700; mkdir's
    mode is a no-op on Windows, which is fine — %TEMP% is already per-user
    there). On POSIX, if the path already exists but is owned by someone else
    (a squatter), fall back to a fresh private ``mkdtemp`` — a per-session
    cache is slower but never trusts foreign content.
    """
    d = Path(tempfile.gettempdir()) / f"heaviside-mathcache-{_cache_user()}"
    try:
        d.mkdir(parents=True, exist_ok=True, mode=0o700)
        if hasattr(os, "getuid") and d.stat().st_uid != os.getuid():
            return Path(tempfile.mkdtemp(prefix="heaviside-mathcache-"))
    except OSError:
        return Path(tempfile.mkdtemp(prefix="heaviside-mathcache-"))
    return d


def _cache_key(fragment: str) -> str:
    keyed = f"{_RENDER_VERSION}\x00{fragment}"
    return hashlib.sha256(keyed.encode("utf-8")).hexdigest()[:32]


# ---------------------------------------------------------------------------
# Compilation: fragment -> SVG text  (disk-cached)
# ---------------------------------------------------------------------------

def _atomic_write(dest: Path, text: str) -> None:
    """Write *text* to *dest* via a temp file + rename, so a concurrent reader
    never observes a partial (or empty) file. A truncated cache entry would
    otherwise be read back as valid content that fails to parse — re-poisoning
    the fragment just like the empty-sentinel bug did."""
    fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, dest)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _compile_svg(fragment: str, *, timeout: int = 20) -> str | None:
    """Return dvisvgm SVG text for *fragment*, or ``None`` on failure.

    A **successful** render is cached on disk by content hash. Failures are NOT
    persisted: an empty/missing cache file is treated as a miss and retried, so a
    one-off transient failure (a momentary tooling hiccup, a race, an interrupted
    write) can never poison a perfectly good fragment forever — the bug where a
    resistor's ``l=$R$`` label silently vanished because an old empty sentinel was
    cached for ``$R$``. Within a session, ``render_path``'s in-memory cache still
    prevents re-shelling for a genuinely bad fragment.
    """
    cache_file = _cache_dir() / f"{_cache_key(fragment)}.svg"
    if cache_file.exists():
        try:
            text = cache_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = ""
        if text:
            # Trust the cached SVG only if it actually parses. A corrupted
            # non-empty entry (torn write, disk error) would otherwise be
            # returned forever, and the ET.ParseError would escape from
            # _svg_to_path on every render of that fragment.
            try:
                ET.fromstring(text)
                return text
            except ET.ParseError:
                try:
                    cache_file.unlink()
                except OSError:
                    pass
        # Empty/corrupt file: a stale failure marker from an older build (or a
        # partial write). Fall through and retry instead of failing forever.

    latex_exe = _tools.resolve("latex")
    dvisvgm_exe = _tools.resolve("dvisvgm")
    if latex_exe is None or dvisvgm_exe is None:
        return None

    tex = _TEMPLATE.replace("%FRAGMENT%", fragment)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "m.tex").write_text(tex, encoding="utf-8")
        try:
            r = subprocess.run(
                # -no-shell-escape: a label is rendered the instant a .hv file is
                # opened — no user gesture — and label text flows verbatim into
                # %FRAGMENT% (it cannot be sanitised; the whole point is to typeset
                # arbitrary math). This flag guarantees a crafted label can never
                # invoke \write18 / external commands, regardless of the local TeX
                # installation's shell_escape default. Mirrors app/preview/latex.py.
                [latex_exe, "-no-shell-escape", "-interaction=nonstopmode",
                 "-halt-on-error", "m.tex"],
                cwd=tmp, capture_output=True, timeout=timeout,
                **_tools.run_kwargs(),
            )
            if r.returncode != 0 or not (tmp_path / "m.dvi").exists():
                return None
            r = subprocess.run(
                [dvisvgm_exe, "--no-fonts", "m.dvi", "-o", "m.svg"],
                cwd=tmp, capture_output=True, timeout=timeout,
                **_tools.run_kwargs(),
            )
            svg_file = tmp_path / "m.svg"
            if r.returncode != 0 or not svg_file.exists():
                return None
            text = svg_file.read_text(encoding="utf-8")
        except (subprocess.TimeoutExpired, OSError):
            return None

    if text:
        _atomic_write(cache_file, text)
    return text or None


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
# ziamath fallback: fragment -> QPainterPath, with no LaTeX install
# ---------------------------------------------------------------------------
#
# ziamath (pure Python, bundles STIX Two Math) typesets a LaTeX-math subset to
# SVG so the canvas labels render even when ``latex``/``dvisvgm`` are absent.  Its
# SVG differs from dvisvgm's: glyphs are ``<symbol viewBox><path></symbol>``
# referenced by ``<use x y width height>`` (a scaled-symbol placement), and rule
# geometry (fraction bars, radicals) is ``<rect>`` in root coordinates.  Like
# dvisvgm, the baseline sits at y=0 and coordinates are in pt at the requested
# ``size`` — so a ``size=TEMPLATE_PT`` render drops into the same pt-based scaling
# the LaTeX path uses.

def _ziamath_svg_to_path(svg_text: str) -> QPainterPath:
    """Parse a ziamath SVG (``<symbol>``+``<use>`` glyphs, ``<rect>`` rules) into
    one combined QPainterPath in pt units."""
    root = ET.fromstring(svg_text)

    def tag(el: ET.Element) -> str:
        return el.tag.rsplit("}", 1)[-1]

    # Glyph templates: id -> (viewBox (minx,miny,w,h), path d).
    symbols: dict[str, tuple[tuple[float, float, float, float], str]] = {}
    for el in root.iter():
        if tag(el) == "symbol" and el.get("id"):
            vb = el.get("viewBox", "").split()
            d = next((c.get("d", "") for c in el if tag(c) == "path"), "")
            if len(vb) == 4:
                symbols[el.get("id")] = (tuple(float(v) for v in vb), d)  # type: ignore[assignment]

    out = QPainterPath()
    for el in root.iter():
        t = tag(el)
        if t == "use":
            ref = (el.get(_HREF) or el.get("href") or "").lstrip("#")
            sym = symbols.get(ref)
            if not sym:
                continue
            (vmx, vmy, vbw, vbh), d = sym
            x = float(el.get("x", "0"))
            y = float(el.get("y", "0"))
            w = float(el.get("width", str(vbw)))
            h = float(el.get("height", str(vbh)))
            # A <use> scales the symbol's viewBox into the (w,h) box at (x,y):
            # (px,py) -> (x + (px-vmx)*sx, y + (py-vmy)*sy).
            sx = w / vbw if vbw else 1.0
            sy = h / vbh if vbh else 1.0
            tr = QTransform(sx, 0.0, 0.0, sy, x - vmx * sx, y - vmy * sy)
            out.addPath(tr.map(parse_path(d)))
        elif t == "rect":
            rp = QPainterPath()
            rp.addRect(
                float(el.get("x", "0")), float(el.get("y", "0")),
                float(el.get("width", "0")), float(el.get("height", "0")),
            )
            out.addPath(rp)
    return out


# ziamath/ziafont lay out glyphs through shared module-level font objects and
# caches; the render pool runs two workers, so typesetting must be serialised —
# concurrent layout corrupts the shared state.
_ziamath_lock = threading.Lock()

#: Guards the import-failure warning so a broken bundle logs once, not per label.
_ziamath_warned = False


def _warn_ziamath_unavailable_once(exc: BaseException) -> None:
    """Emit a single stderr warning the first time ziamath fails to import.

    In a correctly built app this never fires. It only triggers when the
    no-LaTeX math fallback is broken — typically a packaging regression where
    ziamath/ziafont fonts or latex2mathml's ``unimathsymbols.txt`` weren't
    bundled — turning an otherwise invisible "labels just don't appear" symptom
    into something diagnosable.
    """
    global _ziamath_warned
    if _ziamath_warned:
        return
    _ziamath_warned = True
    import sys
    sys.stderr.write(
        "Heaviside: ziamath math fallback unavailable "
        f"({type(exc).__name__}: {exc}); canvas labels will not render without a "
        "LaTeX install. This usually means a packaging problem.\n"
    )


def _ziamath_path(fragment: str) -> QPainterPath | None:
    """Render *fragment* to a QPainterPath via ziamath, or ``None`` if ziamath is
    missing or the fragment fails to typeset.

    Uses ``ziamath.Text`` (mixed text with inline math delimited by ``$…$``), which
    matches the fragment convention the LaTeX engine consumes (``\\strut %FRAGMENT%``
    in a document body): plain text renders verbatim and ``$…$`` spans typeset as
    math.  (``ziamath.Latex`` is math-only — it would render the ``$`` delimiters
    as literal dollar glyphs.)
    """
    with _ziamath_lock:
        try:
            import ziamath
        except Exception as exc:  # noqa: BLE001 - missing module OR missing bundled data
            # ziamath/ziafont load their fonts (STIX Two Math, DejaVu Sans), and the
            # latex2mathml it pulls in reads `unimathsymbols.txt`, all AT IMPORT TIME.
            # A bundling slip raises FileNotFoundError here, not ImportError — which is
            # why a frozen build with the data files missing renders every label blank.
            # Returning None means "no math path"; the caller paints nothing. Warn once
            # so that silent, undiagnosable failure leaves a trail in the logs.
            _warn_ziamath_unavailable_once(exc)
            return None
        try:
            svg = ziamath.Text(fragment, size=TEMPLATE_PT).svg()
        except Exception:  # noqa: BLE001 - any parse/layout failure -> raw-text fallback
            return None
    return _ziamath_svg_to_path(svg)


# ---------------------------------------------------------------------------
# Engine selection
# ---------------------------------------------------------------------------
#
# "latex" (latex + dvisvgm) is the reference renderer; "ziamath" is the pure
# Python, no-install fallback.  Default ("auto") uses LaTeX when present and falls
# back to ziamath otherwise; a debug preference can force ziamath even when LaTeX
# is installed (see set_force_ziamath / app preferences §10.8).

_force_ziamath = False


def set_force_ziamath(value: bool) -> None:
    """Force the ziamath renderer even when system LaTeX is available.

    A debugging aid wired to a preference.  Off by default, so the engine is
    chosen automatically (LaTeX if installed, else ziamath).  Affects renders
    requested *after* the call; callers wanting an immediate visual change should
    re-typeset existing labels (the app does this on preference change).
    """
    global _force_ziamath
    _force_ziamath = bool(value)


def _latex_available() -> bool:
    return _tools.available("latex") and _tools.available("dvisvgm")


def _active_engine() -> str:
    """The renderer to use right now: ``"ziamath"`` when forced or when LaTeX is
    absent, else ``"latex"``."""
    if _force_ziamath:
        return "ziamath"
    return "latex" if _latex_available() else "ziamath"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Baseline calibration memo.  Deliberately *not* an ``lru_cache``: a transient
# calibration failure (a momentary tooling hiccup at startup) must not pin the
# 0.0 fallback for the whole session — that would vertically shift every label.
# Only a *successful* calibration is stored; a failure returns the fallback and
# is retried on the next call.  Guarded by a lock — render_path runs on the
# QThreadPool workers (render_async, max 2 threads).
_baseline_lock = threading.Lock()
_baseline_memo: dict[str, float] = {}


def _calibrated_baseline(engine: str) -> float:
    """Memoised baseline for *engine*; failures are returned but never stored."""
    with _baseline_lock:
        if engine in _baseline_memo:
            return _baseline_memo[engine]
    if engine == "ziamath":
        p = _ziamath_path("x")
    else:
        svg = _compile_svg("x")
        p = _svg_to_path(svg) if svg else None
    if p is None or p.isEmpty():
        return 0.0                      # fallback — NOT memoised; retried later
    value = p.boundingRect().bottom()
    with _baseline_lock:
        _baseline_memo[engine] = value
    return value


def _baseline_y() -> float:
    """Device-y of the text baseline in raw render coordinates (pt).

    Because every fragment is typeset behind a ``\\strut`` (see _TEMPLATE), the
    baseline lands at the same device-y for all fragments.  We recover it from a
    calibration render of ``x`` — a glyph with zero depth, so its ink bottom *is*
    the baseline.  A failed calibration falls back to 0.0 for this call only and
    is retried on the next call (see :data:`_baseline_memo`).
    """
    return _calibrated_baseline("latex")


def _ziamath_baseline_y() -> float:
    """Device-y of the text baseline for the ziamath renderer (pt).

    ziamath places the baseline at y=0 already; we recover it the same way as the
    LaTeX path — the ink bottom of ``x`` (a zero-depth glyph) — so the two engines
    normalise identically.  Like :func:`_baseline_y`, failures are not memoised.
    """
    return _calibrated_baseline("ziamath")


# In-process render memo.  A bounded LRU that — unlike the previous
# ``lru_cache(512)`` — **never stores None**, so a transient failure (tool
# hiccup, race on a cold disk cache) cannot blank a fragment for the whole
# session; the next request simply retries.  Thread-safe: render_path is called
# from the render_async QThreadPool workers (up to 2 concurrently) as well as
# the UI thread.  The slow compile runs *outside* the lock; if two threads race
# the same fragment, both compute and the last write wins (identical results).
_render_lock = threading.Lock()
_render_memo: "OrderedDict[tuple[str, str], QPainterPath]" = OrderedDict()
_RENDER_MEMO_MAX = 512


def _render_memo_clear() -> None:
    """Drop the in-memory render memo (kept API-compatible with the old
    ``lru_cache``'s ``render_path.cache_clear()``)."""
    with _render_lock:
        _render_memo.clear()
    with _baseline_lock:
        _baseline_memo.clear()


def render_path(fragment: str, engine: str) -> QPainterPath | None:
    """Render *fragment* with a specific *engine* (``"latex"`` or ``"ziamath"``).

    The path is normalised so its **baseline is at y=0** and its **left ink edge
    at x=0**; ascenders have negative y, descenders positive y.  This lets callers
    anchor multiple fragments to a shared baseline.  Returns ``None`` if the
    fragment is empty or fails to render.  Successful renders are cached
    in-process per (fragment, engine) — failures are never cached, so they are
    retried — and the LaTeX SVG is additionally cached on disk.
    """
    fragment = fragment.strip()
    if not fragment:
        return None
    key = (fragment, engine)
    with _render_lock:
        cached = _render_memo.get(key)
        if cached is not None:
            _render_memo.move_to_end(key)
            return cached
    if engine == "ziamath":
        path = _ziamath_path(fragment)
        baseline = _ziamath_baseline_y()
    else:
        svg = _compile_svg(fragment)
        path = _svg_to_path(svg) if svg else None
        baseline = _baseline_y()
    if path is None or path.isEmpty():
        return None                     # not memoised: transient failures retry
    norm = QTransform()
    norm.translate(-path.boundingRect().left(), -baseline)
    result = norm.map(path)
    with _render_lock:
        _render_memo[key] = result
        _render_memo.move_to_end(key)
        while len(_render_memo) > _RENDER_MEMO_MAX:
            _render_memo.popitem(last=False)
    return result


# Existing call sites (tests) clear the memo via the lru_cache-style attribute.
render_path.cache_clear = _render_memo_clear  # type: ignore[attr-defined]


def render_latex(fragment: str) -> QPainterPath | None:
    """Render a LaTeX *fragment* to a QPainterPath in pt units, using the active
    engine (LaTeX when available, else the ziamath fallback; forceable via
    :func:`set_force_ziamath`).  See :func:`render_path`."""
    fragment = fragment.strip()
    if not fragment:
        return None
    return render_path(fragment, _active_engine())


# ---------------------------------------------------------------------------
# Asynchronous rendering (off the UI thread)
# ---------------------------------------------------------------------------
#
# render_latex() may shell out to ``latex`` (hundreds of ms) on a cold cache, so
# canvas items request renders asynchronously and keep showing raw text until
# the vector path arrives.  A bounded QThreadPool runs the compiles; results are
# delivered back on the caller's (UI) thread via a queued signal.

class _RenderDispatcher(QObject):
    """The single, process-lifetime bridge from the render pool to the UI thread.

    Exactly **one** QObject, created lazily on the UI thread and never
    destroyed: worker threads only ever *emit* on it. (An earlier design
    created a signals QObject per request, kept alive by a module-level set
    that the relay discarded on delivery — which left the QRunnable holding
    the *last* Python reference, dropped on the **worker** thread by the
    pool's auto-delete after ``run()``. Destroying a UI-thread-affine QObject
    from a worker thread is undefined behaviour in Qt and surfaced as a
    nondeterministic segfault in the main thread's event dispatch under load.
    A permanent dispatcher removes per-request QObject lifetime entirely.)

    The token→callback map is touched only on the UI thread — ``register()``
    runs in ``render_async``'s caller and ``_dispatch`` is delivered queued —
    so it needs no lock.
    """

    done = Signal(int, object)  # token, QPainterPath | None

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: dict[int, object] = {}
        self._next_token = 0
        # AutoConnection: emitted from a pool thread, the receiver (this
        # object) lives on the UI thread, so delivery is queued.
        self.done.connect(self._dispatch)

    def register(self, on_done) -> int:  # noqa: ANN001
        self._next_token += 1
        self._callbacks[self._next_token] = on_done
        return self._next_token

    def _dispatch(self, token: int, path) -> None:  # noqa: ANN001
        cb = self._callbacks.pop(token, None)
        if cb is not None:
            cb(path)


@lru_cache(maxsize=1)
def _dispatcher() -> _RenderDispatcher:
    return _RenderDispatcher()


class _RenderTask(QRunnable):
    def __init__(self, fragment: str, token: int) -> None:
        super().__init__()
        self._fragment = fragment
        self._token = token

    def run(self) -> None:  # noqa: D401 - QRunnable hook
        try:
            path = render_latex(self._fragment)
        except Exception:  # pragma: no cover - defensive; never kill the pool
            path = None
        # The dispatcher is module-permanent (the lru_cache holds it), so this
        # emit — and the runnable's destruction right after run() returns —
        # never lets a worker thread drop a QObject's last reference.
        try:
            _dispatcher().done.emit(self._token, path)
        except RuntimeError:  # pragma: no cover - interpreter/app shutdown
            # Qt is tearing down (the dispatcher's C++ object is gone); the
            # result has no recipient anymore — drop it quietly.
            pass


@lru_cache(maxsize=1)
def _pool() -> QThreadPool:
    pool = QThreadPool()
    pool.setMaxThreadCount(2)  # latex is heavy; cap concurrent compiles
    # On app teardown, drain in-flight label renders so Qt doesn't warn
    # "QThreadPool destroyed while threads are still running". Bounded wait: at
    # most 2 threads, each a short subprocess with a 20s timeout. Mirrors the
    # explicit shutdown PreviewWorker wires for the main compile thread.
    app = QCoreApplication.instance()
    if app is not None:
        app.aboutToQuit.connect(lambda: pool.waitForDone(2000))
    return pool


def render_async(fragment: str, on_done) -> None:  # noqa: ANN001
    """Render *fragment* on a worker thread; call ``on_done(QPainterPath|None)``
    on the calling (UI) thread when ready.

    Must be called on the UI thread — the dispatcher that delivers results is
    created with that thread's affinity. Cache hits still hop through the pool
    but return almost immediately.
    """
    fragment = (fragment or "").strip()
    if not fragment:
        on_done(None)
        return
    token = _dispatcher().register(on_done)
    _pool().start(_RenderTask(fragment, token))


# ---------------------------------------------------------------------------
# Option-string -> displayable LaTeX
# ---------------------------------------------------------------------------

# CircuiTikZ annotation slots are a **family** letter optionally followed by
# position/direction modifiers: ``^`` (above) / ``_`` (below) set the side, and
# ``<`` / ``>`` set the arrow direction (and, for a bipole's current, which lead
# it rides). Examples: ``i``, ``i^``, ``i<``, ``i_>``, ``v<``. The *value* after
# ``=`` is what renders near the component (the canvas shows "R_1", not "l=R_1").
_SLOT_MODS = "^_<>"
# Families whose value renders near the component.
_LABEL_FAMILIES = frozenset("lviatf")
# Families placed on a *side* of the body (as opposed to ``t``, the in-body
# bipole-box text). Used by slot_fragments() for per-side placement.
_SIDE_FAMILIES = frozenset("lvia")


def _slot_family(key: str) -> str:
    """The family letter of a slot key, stripping ``^``/``_``/``<``/``>`` modifiers
    (e.g. ``i<`` → ``i``, ``v^`` → ``v``)."""
    return key.rstrip(_SLOT_MODS)[:1]


def slot_reversed(key: str) -> bool:
    """True when the slot's direction modifier is ``<`` (reversed); ``>`` or no
    modifier is the forward/default direction."""
    return "<" in key


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
        if eq and _slot_family(k) in _SIDE_FAMILIES and val.strip():
            out.append((k, val.strip()))
    return out


def slot_side(key: str) -> str:
    """Conventional side ('above' / 'below') for a slot key.

    ``^`` forces above, ``_`` forces below (independent of the ``<``/``>``
    direction modifier); otherwise labels (``l``) and current (``i``) sit above,
    voltage (``v``) sits below — a readable default, not a pixel-exact
    reproduction of CircuiTikZ's voltage/current arrow placement.
    """
    if "^" in key:
        return "above"
    if "_" in key:
        return "below"
    return "below" if _slot_family(key) == "v" else "above"


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
        if eq and _slot_family(key.strip()) in _LABEL_FAMILIES and val.strip():
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

