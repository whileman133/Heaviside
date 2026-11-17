"""Derive which library kinds have a *fillable body* (a box, triangle, circle...).

Developer tool (needs ``pdflatex`` + ``circuitikz``, ``pdftoppm``, numpy, Pillow).
Each kind is rendered once with ``fill=yellow`` on its ``\\draw``; a kind counts as
"shaped" when the yellow region is a real body rather than a sliver (capacitor
plates fill too, but are not an enclosed shape). The result is the data in
``app/components/shapes.py``; re-run this after regenerating the library and paste
the printed ``SHAPED_KINDS`` set there.

    uv run python scripts/find_fillable_kinds.py [--json out.json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.codegen import circuitikz as cg  # noqa: E402
from app.components.model import SymbolComponent  # noqa: E402
from app.components.registry import REGISTRY  # noqa: E402
from app.schematic.model import Component, Schematic  # noqa: E402

DPI = 60
# A kind is "shaped" when fill visibly paints something (>= MIN_PIXELS) and that
# region is compact rather than a sliver: CircuiTikZ also fills capacitor plates,
# but they are not an enclosed shape. A sliver has min/max bbox side < MIN_ASPECT
# (capacitor plates ~0.25; diode triangles, op-amps, boxes >= 0.3).
MIN_ASPECT = 0.3
MIN_PIXELS = 12


def _source(kind: str) -> str:
    comp = Component(id=f"k_{kind}", kind=kind, position=(0.0, 0.0), rotation=0, options="")
    sch = Schematic(version="0.1", name="k", components=[comp], wires=[])
    return cg.generate(sch, y_flip=True)


def _compile(kinds: list[str], sources: dict[str, str], tmp: Path) -> list[Image.Image] | None:
    body = "\n".join(sources[k] for k in kinds)
    tex = (
        "\\documentclass[multi=circuitikz,border=2pt]{standalone}\n"
        "\\usepackage[siunitx]{circuitikz}\n\\begin{document}\n" + body + "\n\\end{document}\n"
    )
    (tmp / "b.tex").write_text(tex)
    for f in tmp.glob("b*.p*m"):
        f.unlink()
    pdf = tmp / "b.pdf"
    if pdf.exists():
        pdf.unlink()
    try:
        subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-no-shell-escape", "b.tex"],
            cwd=tmp, capture_output=True, timeout=25,
        )
    except subprocess.TimeoutExpired:  # a kind that hangs TeX: caller bisects
        return None
    if not pdf.exists():
        return None
    r = subprocess.run(["pdftoppm", "-r", str(DPI), "b.pdf", "b"], cwd=tmp, capture_output=True)
    pages = sorted(tmp.glob("b-*.ppm"))
    if r.returncode != 0:  # a kind produced a broken page: caller bisects
        for p in pages:
            p.unlink()
        pdf.unlink()
        return None
    if len(pages) != len(kinds):
        for p in pages:
            p.unlink()
        return None
    imgs = [Image.open(p).convert("RGB").copy() for p in pages]
    for p in pages:
        p.unlink()
    pdf.unlink()
    return imgs


def _measure(img: Image.Image) -> dict:
    a = np.asarray(img).astype(int)
    ink = a.sum(axis=2) < 700
    yel = (a[..., 0] > 200) & (a[..., 1] > 200) & (a[..., 2] < 120)

    def bbox(m):
        ys, xs = np.where(m)
        return (0, 0) if len(xs) == 0 else (int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))

    iw, ih = bbox(ink)
    yw, yh = bbox(yel)
    extent = max(iw, ih, 1)
    return {"yellow_px": int(yel.sum()), "yw": yw, "yh": yh, "extent": extent,
            "aspect": round(min(yw, yh) / max(yw, yh, 1), 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json")
    args = ap.parse_args()

    # Emit ``fill=yellow`` for every symbol regardless of the shape table.
    cg._symbol_style_opts = lambda comp: ["fill=yellow"]

    kinds = [k for k, d in REGISTRY.items() if d.component_class is SymbolComponent]
    sources, failed = {}, []
    for k in kinds:
        try:
            sources[k] = _source(k)
        except Exception as exc:  # noqa: BLE001
            failed.append((k, f"codegen: {exc}"))
    kinds = [k for k in kinds if k in sources]

    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)

        def run(chunk: list[str]) -> None:
            if not chunk:
                return
            print(f"[{len(results)}/{len(kinds)}] batch of {len(chunk)}", file=sys.stderr, flush=True)
            imgs = _compile(chunk, sources, tmp)
            if imgs is not None:
                for k, im in zip(chunk, imgs):
                    results[k] = _measure(im)
            elif len(chunk) == 1:
                failed.append((chunk[0], "latex"))
            else:
                mid = len(chunk) // 2
                run(chunk[:mid])
                run(chunk[mid:])

        for i in range(0, len(kinds), 60):
            run(kinds[i:i + 60])

    shaped = sorted(k for k, m in results.items()
                    if m["yellow_px"] >= MIN_PIXELS and m["aspect"] >= MIN_ASPECT)
    if args.json:
        Path(args.json).write_text(json.dumps({"results": results, "failed": failed}, indent=1))
    print(f"# {len(shaped)} shaped of {len(results)} measured; {len(failed)} failed")
    for k, why in failed:
        print(f"# FAILED {k}: {why}")
    print("SHAPED_KINDS = frozenset({")
    for k in shaped:
        print(f'    "{k}",')
    print("})")


if __name__ == "__main__":
    main()
