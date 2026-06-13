r"""
Measure a CircuiTikZ symbol (spec: ``spec/component-pipeline.md`` §3).

This is the foundation of the component pipeline: instead of hand-measuring pin
positions and scale/lead corrections off a compiled figure (the brittle
PROJECT_SPEC §5.5 ritual), render the symbol and read its pin **anchors**
automatically.

Three functions, all Qt-free, requiring ``latex`` + ``dvisvgm`` at run time (a
developer-tool dependency, not a shipped-app one):

* :func:`render_svg`        — circuitikz body -> (svg_text, latex_log)
* :func:`parse_geometry`— svg_text -> geometry-style ``{viewBox, paths, glyphs}``
* :func:`measure_anchors` — read ``\pgfpointanchor`` dumps -> ``{name: (gu_x, gu_y)}``
  as a **GU offset** (Qt y-down), directly comparable to a pin position.

This is the single renderer/parser: ``components/generate_components.py`` drives it to
produce both the symbol geometry and the component data file.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from app.preview import tools as _tools

# 1 GU == 1 cm in CircuiTikZ; \the\pgf@x reports a TeX-point dimension (1 cm =
# 72.27/2.54 pt).  Anchor dumps are divided by this to get GU.
TEXPT_PER_GU: float = 72.27 / 2.54  # = 28.452756

_HREF = "{http://www.w3.org/1999/xlink}href"

_DOC = r"""\documentclass[border=%(border)dpt]{standalone}
\usepackage[american]{circuitikz}
\begin{document}
\ifdefined\pgfcircversion\typeout{HVCTIKZVERSION \pgfcircversion}\fi
\begin{circuitikz}
%(ctikzset)s
%(body)s
%(dump)s
\end{circuitikz}
\end{document}
"""

# dvisvgm needs Ghostscript (LIBGS) for the filled-path specials some symbols use.
_LIBGS_CANDIDATES = (
    "/opt/homebrew/opt/ghostscript/lib/libgs.dylib",
    "/usr/local/opt/ghostscript/lib/libgs.dylib",
    "/usr/lib/libgs.so",
    "/usr/local/lib/libgs.so",
)


class RenderError(RuntimeError):
    """Raised when ``latex``/``dvisvgm`` fail; carries the captured log."""

    def __init__(self, message: str, log: str = "") -> None:
        super().__init__(message)
        self.log = log


def _render_env() -> dict[str, str]:
    env = dict(os.environ)
    if not env.get("LIBGS"):
        for cand in _LIBGS_CANDIDATES:
            if Path(cand).exists():
                env["LIBGS"] = cand
                break
    return env


def _anchor_dump(node_id: str, anchors: list[str]) -> str:
    if not anchors:
        return ""
    lines = [r"\makeatletter"]
    for name in anchors:
        lines.append(
            rf"\pgfpointanchor{{{node_id}}}{{{name}}}"
            rf"\typeout{{HVANCHOR {name} = \the\pgf@x , \the\pgf@y}}"
        )
    lines.append(r"\makeatother")
    return "\n".join(lines)


def render_svg(body: str, *, border_pt: int = 2, ctikzset: list[str] | None = None,
           node_id: str = "X", anchors: list[str] | None = None) -> tuple[str, str]:
    """Render a circuitikz ``body`` to SVG; return ``(svg_text, latex_log)``."""
    doc = _DOC % {
        "border": border_pt,
        "ctikzset": "\n".join(rf"\ctikzset{{{s}}}" for s in (ctikzset or [])),
        "body": body,
        "dump": _anchor_dump(node_id, anchors or []),
    }
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "sym.tex").write_text(doc, encoding="utf-8")
        r = subprocess.run(
            ["latex", "-no-shell-escape", "-interaction=nonstopmode",
             "-output-directory", str(work), "sym.tex"],
            capture_output=True, text=True, cwd=work, timeout=60,
            **_tools.run_kwargs(),
        )
        if not (work / "sym.dvi").exists():
            raise RenderError("latex failed to produce a DVI", r.stdout)
        svg = work / "sym.svg"
        rs = subprocess.run(
            ["dvisvgm", "--no-fonts", str(work / "sym.dvi"), "-o", str(svg)],
            capture_output=True, text=True, cwd=work, env=_render_env(), timeout=60,
            **_tools.run_kwargs(),
        )
        if not svg.exists() or "0pt x 0pt" in rs.stderr:
            raise RenderError("dvisvgm failed (Ghostscript/LIBGS?)", r.stdout + rs.stderr)
        return svg.read_text(encoding="utf-8"), r.stdout


# Anchor name may contain spaces (logic gates use ``in 1``/``in 2``/…), so capture
# the name non-greedily up to the " = " that precedes the coordinates.
_ANCHOR_RE = re.compile(r"HVANCHOR (.+?) = (-?[\d.]+)pt\s*,\s*(-?[\d.]+)pt")

# The HVCTIKZVERSION line typeset by _DOC's probe (circuitikz's own
# \pgfcircversion macro), and a fallback on the package's log banner for
# versions that predate the macro.
_CTIKZ_VERSION_RE = re.compile(r"HVCTIKZVERSION\s+(\S+)")
_CTIKZ_BANNER_RE = re.compile(r"circuitikz[^\n]*?version\s+([0-9][\w.\-]*)",
                              re.IGNORECASE)


def circuitikz_version(log: str) -> str | None:
    """The CircuiTikZ version a compile *log* reports, or ``None``.

    Every ``_DOC`` compile typesets ``HVCTIKZVERSION <\\pgfcircversion>``; the
    package banner line is the fallback. Used to stamp ``definitions.json``
    with the version the component library was generated against."""
    m = _CTIKZ_VERSION_RE.search(log) or _CTIKZ_BANNER_RE.search(log)
    return m.group(1) if m else None


def measure_anchors(tikz_keyword: str, anchors: list[str], *, border_pt: int = 10,
                    ctikzset: list[str] | None = None) -> dict[str, tuple[float, float]]:
    """Render ``\\node[tikz_keyword] (X) ...`` and measure each anchor (GU, y-down).

    The returned offsets are relative to the node's own origin; negate-y converts
    CircuiTikZ's y-up to the canvas y-down convention.  *ctikzset* applies shape
    settings (e.g. a logic-gate ``…/height`` that must be set before the node).
    """
    body = rf"\node[{tikz_keyword}] (X) at (0,0) {{}};"
    _svg, log = render_svg(body, border_pt=border_pt, node_id="X", anchors=anchors,
                           ctikzset=ctikzset or [])
    out: dict[str, tuple[float, float]] = {}
    for name, xs, ys in _ANCHOR_RE.findall(log):
        out[name] = (round(float(xs) / TEXPT_PER_GU, 4), round(-float(ys) / TEXPT_PER_GU, 4))
    return out


_NO_SUCH_ANCHOR = "hv__no_such_anchor__"


def discover_terminals(tikz_keyword: str, candidates: list[str], *,
                       border_pt: int = 10) -> dict[str, tuple[float, float]]:
    """Discover a shape's wireable terminals from a *candidates* anchor list.

    CircuiTikZ/pgf exposes no machine-readable terminal list: an undefined anchor
    name resolves to the shape **centre** (the fallback) rather than erroring, and
    aliases (``B``/``base``/``G``/``gate``) collapse to one point.  So we measure
    all candidates plus a sentinel, drop any that land on the sentinel's fallback
    point, and de-duplicate by position — keeping the first candidate name that
    reaches each distinct terminal.  Returns ``{anchor_name: (gu_x, gu_y)}``.

    The candidate *order* picks the canonical name per terminal (put your preferred
    names first).  Intended for offline import/discovery, not the runtime hot path.
    """
    probe = list(candidates) + [_NO_SUCH_ANCHOR]
    try:                                              # fast path: one render
        measured = measure_anchors(tikz_keyword, probe, border_pt=border_pt)
    except RenderError:                               # strict shape: probe one-by-one
        measured = {}
        for anchor in probe:
            try:
                measured.update(measure_anchors(tikz_keyword, [anchor], border_pt=border_pt))
            except RenderError:
                pass
    fallback = measured.get(_NO_SUCH_ANCHOR)
    by_pos: dict[tuple[float, float], str] = {}
    for name in candidates:                           # candidate order => canonical name
        pos = measured.get(name)
        if pos is None or pos == fallback:
            continue
        by_pos.setdefault(pos, name)
    return {name: pos for pos, name in by_pos.items()}


# ---------------------------------------------------------------------------
# SVG geometry parsing
# ---------------------------------------------------------------------------

def _tag(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _parse_matrix(transform: str | None):
    if transform:
        m = re.search(
            r"matrix\(\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s+"
            r"([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s*\)", transform)
        if m:
            return tuple(float(v) for v in m.groups())
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_geometry(svg_text: str) -> dict:
    """Parse a dvisvgm SVG into ``{viewBox, width_pt, height_pt, paths, glyphs}``."""
    root = ET.fromstring(svg_text)
    glyph_defs = {el.get("id"): el.get("d", "")
                  for el in root.iter() if _tag(el) == "path" and el.get("id")}
    paths: list[dict] = []
    glyphs: list[dict] = []

    def walk(el: ET.Element, inherited: str | None) -> None:
        for child in el:
            tag = _tag(child)
            if tag == "g":
                walk(child, child.get("transform", inherited))
            elif tag == "path":
                d = child.get("d", "")
                if child.get("id") or not re.match(r"^\s*[Mm]", d):
                    continue
                sw = child.get("stroke-width")
                paths.append({
                    "d": d,
                    "stroke_width": float(sw) if sw is not None else 0.3985,
                    "fill": child.get("fill") if child.get("fill") is not None else "#000",
                    "stroke": child.get("stroke", "#000"),
                })
            elif tag == "use":
                ref = (child.get(_HREF) or child.get("href") or "").lstrip("#")
                d = glyph_defs.get(ref)
                if not d:
                    continue
                ux, uy = float(child.get("x", "0")), float(child.get("y", "0"))
                a, b, c, dd, e, f = _parse_matrix(child.get("transform", inherited))
                glyphs.append({"d": d, "matrix": [a, b, c, dd, a * ux + c * uy + e, b * ux + dd * uy + f],
                               "stroke_width": 0.3985})
            elif tag == "rect":
                # dvisvgm emits a TeX rule (e.g. the overline of \ctikztextnot{Q} —
                # the flip-flop's Q̄) as a <rect> with its own transform. Capture it
                # as a filled glyph (a closed rectangle path + that matrix) so the
                # canvas paints it; otherwise the bar is silently dropped.
                x, y = float(child.get("x", "0")), float(child.get("y", "0"))
                w, h = float(child.get("width", "0")), float(child.get("height", "0"))
                a, b, c, dd, e, f = _parse_matrix(child.get("transform", inherited))
                rect_d = f"M{x} {y}L{x + w} {y}L{x + w} {y + h}L{x} {y + h}Z"
                glyphs.append({"d": rect_d, "matrix": [a, b, c, dd, e, f],
                               "stroke_width": 0.3985})

    walk(root, None)
    return {
        "viewBox": root.get("viewBox", ""),
        "width_pt": root.get("width", ""),
        "height_pt": root.get("height", ""),
        "paths": paths,
        "glyphs": glyphs,
    }
