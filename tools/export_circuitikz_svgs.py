#!/usr/bin/env python3
r"""
Deterministic CircuiTikZ symbol export pipeline.

ONE tool that (1) renders every component Heaviside uses to a normalised SVG via
``latex`` + ``dvisvgm``, and (2) compiles those SVGs into a **self-contained**
``manifest.json``.  The application reads only the manifest at run time
(``app/canvas/svgsym.py``) — it never touches the ``.svg`` files, so the manifest
bakes in everything needed, including the resolved ``+``/``−`` glyph geometry.

Usage
-----
    python tools/export_circuitikz_svgs.py             # render SVGs + build manifest
    python tools/export_circuitikz_svgs.py --no-render  # rebuild manifest from existing SVGs
    OUT_DIR=/tmp/svgs python tools/export_circuitikz_svgs.py

Requirements: ``latex``, ``dvisvgm`` (needs Ghostscript for the filled-path
PostScript specials), ``python3``.  If ``dvisvgm`` cannot find Ghostscript, set
``LIBGS`` (Homebrew: ``$(brew --prefix ghostscript)/lib/libgs.dylib``).

Determinism
-----------
``latex``/``dvisvgm`` output is byte-stable for fixed input (dvisvgm writes no
timestamp), the component tables below are processed in a fixed order, and the
manifest is dumped with ``indent=2`` in insertion order.  Re-running on the same
toolchain reproduces identical ``.svg`` files and ``manifest.json``.

Manifest schema (per component key)
-----------------------------------
    {
      "kind":     "bipole" | "tripole" | "node",
      "name":     "<human-readable name>",
      "viewBox":  "...", "width_pt": "...pt", "height_pt": "...pt",
      "paths":  [ {"d", "stroke_width", "fill", "stroke"}, ... ],   # real geometry
      "glyphs": [ {"d", "matrix": [a,b,c,d,e,f], "stroke_width"}, ... ]  # +/- marks
    }

* ``paths`` holds the stroked/filled body geometry in SVG point coordinates.
  A **bare** ``<path>`` (no ``fill`` attribute) records ``fill='#000'`` — the SVG
  default solid fill — so ``svgsym`` fills it; an explicit ``fill='none'`` is
  stroked only.
* ``glyphs`` holds text marks (the ``+``/``−`` of sources) that dvisvgm emits as
  ``<use>`` references.  Each is resolved against ``<defs>`` and baked as the
  glyph's path ``d`` plus the composed affine ``matrix`` (enclosing-group matrix
  ∘ ``<use>`` translation).  ``svgsym`` paints ``QTransform(*matrix).map(d)``,
  so no ``<use>``/``<defs>`` indirection survives into the manifest.

To add a symbol: add it to the relevant table below and re-run.  Multi-terminal
parts also need a ``Placement`` anchor in ``app/canvas/svgsym.py`` (§5.5).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Diode bodies are visually large; shrink them to match the other bipoles. This
# MUST match DIODE_SYMBOL_SCALE in app/codegen/circuitikz.py (§5.3).
DIODE_SCALE = 0.8

SCRIPT_DIR = Path(__file__).resolve().parent
OUT_DIR = Path(os.environ.get("OUT_DIR", SCRIPT_DIR / "circuitikz_svgs"))

_SVG_NS = "{http://www.w3.org/2000/svg}"
_HREF = "{http://www.w3.org/1999/xlink}href"

# ---------------------------------------------------------------------------
# Component tables — the exact set the application uses (registry-backed).
# Each entry: (manifest_key, tikz_spec, lead_draws).  `tikz_spec` is the bare
# CircuiTikZ keyword/node spec; `lead_draws` (tripoles only) routes each named
# terminal anchor out to a clean grid coordinate so the exported terminals land
# on the registry pin grid.
# ---------------------------------------------------------------------------

# Two-terminal devices, drawn `\draw (0,0) to[KIND] (2,0);`.  The diode family
# (and its filled `*` variants) is rendered at DIODE_SCALE.
BIPOLES: list[str] = [
    "R", "C", "L",
    "D", "D*", "zD", "zD*", "sD", "sD*", "tD", "tD*", "zzD", "zzD*", "leD", "leD*",
    "V", "I", "vsourcesin", "isourcesin", "cV", "cI",
]
_DIODE_BIPOLES = {"D", "D*", "zD", "zD*", "sD", "sD*", "tD", "tD*",
                  "zzD", "zzD*", "leD", "leD*"}

# Single-terminal node symbols, drawn `\draw (0,0) node[KIND] {};`.
NODES: list[str] = [
    "ground", "rground", "sground", "nground", "pground", "cground", "eground",
    "vcc", "vdd", "vee", "vss",
]

# Multi-terminal parts: (manifest_key, node_spec, leads).  `leads` extends each
# named anchor to a grid coordinate (relative to the node center).
_IGFET_N = (r"\draw (X.drain)  -- (0.0164,0.7295);"
            r"\draw (X.source) -- (0.0164,-0.7705);"
            r"\draw (X.gate)   -| (-0.9836,-0.2705);")
_IGFET_P = (r"\draw (X.drain)  -- (0.0164,-0.7295);"
            r"\draw (X.source) -- (0.0164,0.7705);"
            r"\draw (X.gate)   -| (-0.9836,0.2705);")
TRIPOLES: list[tuple[str, str, str]] = [
    ("op_amp", "op amp",
     r"\draw (X.out) -- (1.5,0);\draw (X.+) -| (-1.5,-0.5);\draw (X.-) -| (-1.5,0.5);"),
    ("npn", "npn", r"\draw (X.C) -- (0.0129,1);\draw (X.E) -- (0.0129,-1);\draw (X.B) -- (-1,0);"),
    ("pnp", "pnp", r"\draw (X.E) -- (0.0129,1);\draw (X.C) -- (0.0129,-1);\draw (X.B) -- (-1,0);"),
    ("nigfete", "nigfete", _IGFET_N),
    ("nigfetd", "nigfetd", _IGFET_N),
    ("pigfete", "pigfete", _IGFET_P),
    ("pigfetd", "pigfetd", _IGFET_P),
    # Body-diode variants: same lead routing, '+ bodydiode' option, underscore key.
    ("nigfete_bodydiode", "nigfete, bodydiode", _IGFET_N),
    ("nigfetd_bodydiode", "nigfetd, bodydiode", _IGFET_N),
    ("pigfete_bodydiode", "pigfete, bodydiode", _IGFET_P),
    ("pigfetd_bodydiode", "pigfetd, bodydiode", _IGFET_P),
]

_DOC = r"""\documentclass[border=%s]{standalone}
\usepackage[american]{circuitikz}
\begin{document}
\begin{circuitikz}
%s
\end{circuitikz}
\end{document}
"""


# ---------------------------------------------------------------------------
# Rendering (latex -> dvi -> dvisvgm -> svg)
# ---------------------------------------------------------------------------

def _render_one(key: str, body: str, border: str, out_subdir: Path, work: Path) -> Path | None:
    """Render one circuitikz `body` to ``out_subdir/key.svg``; return the path."""
    tex = work / f"{key}.tex"
    tex.write_text(_DOC % (border, body))
    r = subprocess.run(
        ["latex", "-interaction=nonstopmode", "-output-directory", str(work), str(tex)],
        capture_output=True, text=True,
    )
    dvi = work / f"{key}.dvi"
    if not dvi.exists():
        print(f"  [WARN] latex failed for {key!r}\n{r.stdout[-400:]}")
        return None
    svg = out_subdir / f"{key}.svg"
    r = subprocess.run(
        ["dvisvgm", "--no-fonts", str(dvi), "-o", str(svg)],
        capture_output=True, text=True,
    )
    if not svg.exists() or "0pt x 0pt" in r.stderr:
        print(f"  [WARN] dvisvgm failed for {key!r} (is LIBGS set?)\n{r.stderr[-400:]}")
        return None
    return svg


def render_all() -> None:
    """Render every component SVG into OUT_DIR/{bipoles,nodes,tripoles}/."""
    for sub in ("bipoles", "nodes", "tripoles"):
        d = OUT_DIR / sub
        d.mkdir(parents=True, exist_ok=True)
        for f in d.glob("*.svg"):    # clean: output is exactly what we render
            f.unlink()

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        print("=== Rendering bipoles ===")
        for key in BIPOLES:
            scale = (r"\ctikzset{diodes/scale=%s}" % DIODE_SCALE) if key in _DIODE_BIPOLES else ""
            body = f"  {scale}\n  \\draw (0,0) to[{key}] (2,0);"
            print(f"  {key} ...", "OK" if _render_one(key, body, "2pt", OUT_DIR / "bipoles", work) else "FAIL")
        print("=== Rendering nodes ===")
        for key in NODES:
            body = f"  \\draw (0,0) node[{key}] {{}};"
            print(f"  {key} ...", "OK" if _render_one(key, body, "2pt", OUT_DIR / "nodes", work) else "FAIL")
        print("=== Rendering tripoles ===")
        for key, spec, leads in TRIPOLES:
            body = f"  \\node[{spec}] (X) at (0,0) {{}};\n  {leads}"
            print(f"  {key} ...", "OK" if _render_one(key, body, "10pt", OUT_DIR / "tripoles", work) else "FAIL")


# ---------------------------------------------------------------------------
# SVG -> manifest entry
# ---------------------------------------------------------------------------

def _tag(el: ET.Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _is_geometry(d: str) -> bool:
    """True if *d* is real path data (begins with a move command)."""
    return bool(re.match(r"^\s*[Mm]", d))


def _parse_matrix(transform: str | None) -> tuple[float, float, float, float, float, float]:
    """Parse a ``matrix(a b c d e f)`` transform; identity if absent/unmatched."""
    if transform:
        m = re.search(
            r"matrix\(\s*([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s+"
            r"([-\d.eE]+)\s+([-\d.eE]+)\s+([-\d.eE]+)\s*\)",
            transform,
        )
        if m:
            return tuple(float(v) for v in m.groups())  # type: ignore[return-value]
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_svg(svg_text: str, kind: str, name: str) -> dict:
    """Compile one dvisvgm SVG into a self-contained manifest entry."""
    root = ET.fromstring(svg_text)
    viewbox = root.get("viewBox", "")
    width = root.get("width", "")
    height = root.get("height", "")

    # Glyph definitions (``<path id='g..' d='..'>``), keyed by id.
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
                if child.get("id") or not _is_geometry(d):
                    continue  # glyph definition / non-geometry — not body
                sw = child.get("stroke-width")
                fill = child.get("fill")
                paths.append({
                    "d": d,
                    "stroke_width": float(sw) if sw is not None else 0.3985,
                    # Absent fill = SVG default solid black, NOT 'none'.
                    "fill": fill if fill is not None else "#000",
                    "stroke": child.get("stroke", "#000"),
                })
            elif tag == "use":
                ref = (child.get(_HREF) or child.get("href") or "").lstrip("#")
                d = glyph_defs.get(ref)
                if not d:
                    continue
                ux, uy = float(child.get("x", "0")), float(child.get("y", "0"))
                a, b, c, dd, e, f = _parse_matrix(child.get("transform", inherited))
                # Compose group-matrix ∘ translate(ux,uy):  p -> M·(p + (ux,uy)).
                glyphs.append({
                    "d": d,
                    "matrix": [a, b, c, dd, a * ux + c * uy + e, b * ux + dd * uy + f],
                    "stroke_width": 0.3985,
                })

    walk(root, None)
    return {
        "kind": kind,
        "name": name,
        "viewBox": viewbox,
        "width_pt": width,
        "height_pt": height,
        "paths": paths,
        "glyphs": glyphs,
    }


def build_manifest() -> None:
    """Parse every rendered SVG into OUT_DIR/manifest.json (insertion order)."""
    manifest: dict[str, dict] = {}
    plan = (
        [("bipoles", "bipole", k, k) for k in BIPOLES]
        + [("nodes", "node", k, k) for k in NODES]
        + [("tripoles", "tripole", key, spec) for key, spec, _ in TRIPOLES]
    )
    for sub, kind, key, name in plan:
        svg = OUT_DIR / sub / f"{key}.svg"
        if not svg.exists():
            print(f"  [WARN] missing SVG for {key!r}; skipping")
            continue
        manifest[key] = parse_svg(svg.read_text(encoding="utf-8"), kind, name)
    out = OUT_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"=== Wrote {len(manifest)} entries -> {out} ===")


def main() -> int:
    if "--no-render" not in sys.argv:
        render_all()
    build_manifest()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
