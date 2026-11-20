"""Measure per-component colour, body fill, and dash support in CircuiTikZ.

Requires pdflatex with CircuiTikZ and standalone, pdftoppm, and Pillow.
Run with uv run python scripts/find_fillable_kinds.py [--json out.json].
Copy the printed tables into app/components/shapes.py after library updates.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageChops

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


# The project only exposes appearance supported across its target renders.
# Some CircuiTikZ versions can colour auxiliary strokes of bodyless symbols or
# fill stroke-only shapes. Those effects are outside the symbol styling contract.
_COLOR_EXCLUSIONS = {
    "short", "crossing", "bare jumper", "noise voltage source",
    "noise current source", "thermocouple", "bare7seg",
}
_FILL_EXCLUSIONS = {
    "cpe", "damper", "sinusoidal current source", "viscoe",  # no closed canvas body
    "ldR", "memristor", "mov", "phR", "thR", "thRn", "thRp", "varistor",
}


def _supported(kind: str, measured: dict[str, bool]) -> dict[str, bool]:
    measured = measured.copy()
    if kind in _COLOR_EXCLUSIONS:
        measured["COLORABLE_KINDS"] = False
    if kind in _FILL_EXCLUSIONS or REGISTRY[kind].category in {"Diodes", "Transistors"}:
        measured["FILLABLE_KINDS"] = False
    return measured


def _source(kind: str, option: str) -> str:
    comp = Component(id=f"k_{kind}", kind=kind, position=(0.0, 0.0), rotation=0, options="")
    sch = Schematic(version="0.1", name="k", components=[comp], wires=[])
    with patch.object(cg, "_symbol_style_opts", return_value=[option] if option else []):
        return cg.generate(sch, y_flip=True)


def _compile(keys: list[tuple[str, str]], sources: dict[tuple[str, str], str],
             tmp: Path) -> list[Image.Image] | None:
    body = "\n".join(sources[key] for key in keys)
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
        result = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", "-halt-on-error",
             "-no-shell-escape", "b.tex"],
            cwd=tmp, capture_output=True, timeout=25,
        )
    except subprocess.TimeoutExpired:  # a kind that hangs TeX: caller bisects
        return None
    if result.returncode != 0 or not pdf.exists():
        return None
    r = subprocess.run(
        ["pdftoppm", "-r", str(DPI), "b.pdf", "b"],
        cwd=tmp, capture_output=True, timeout=25,
    )
    pages = sorted(tmp.glob("b-*.ppm"))
    if r.returncode != 0 or len(pages) != len(keys):
        return None  # caller bisects the batch
    images = []
    for page in pages:
        with Image.open(page) as source:
            images.append(source.convert("RGB"))
    return images


def _measure(plain: Image.Image, color: Image.Image, fill: Image.Image,
             dashed: Image.Image) -> dict[str, bool]:
    yellow = Image.new("L", fill.size)
    yellow.putdata([255 if r > 200 and g > 200 and b < 120 else 0
                    for r, g, b in fill.get_flattened_data()])
    box = yellow.getbbox()
    fillable = False
    if box is not None:
        width, height = box[2] - box[0], box[3] - box[1]
        fillable = (yellow.histogram()[255] >= MIN_PIXELS
                    and min(width, height) / max(width, height) >= MIN_ASPECT)

    return {
        "COLORABLE_KINDS": any(r > 150 and g < 100 and b < 100
                               for r, g, b in color.get_flattened_data()),
        "FILLABLE_KINDS": fillable,
        "DASHABLE_KINDS": (plain.size != dashed.size
                           or ImageChops.difference(plain, dashed).getbbox() is not None),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    kinds = sorted(k for k, d in REGISTRY.items() if d.component_class is SymbolComponent)
    options = ("", "color=red", "fill=yellow", "dashed")
    sources = {(kind, option): _source(kind, option) for kind in kinds for option in options}
    results = {}
    failed = []

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)

        def run(batch: list[str]) -> None:
            keys = [(kind, option) for kind in batch for option in options]
            images = _compile(keys, sources, tmp)
            if images is not None:
                for index, kind in enumerate(batch):
                    start = len(options) * index
                    measured = _measure(*images[start:start + len(options)])
                    results[kind] = _supported(kind, measured)
            elif len(batch) == 1:
                failed.append(batch[0])
            else:
                middle = len(batch) // 2
                run(batch[:middle])
                run(batch[middle:])

        for index in range(0, len(kinds), 25):
            print(f"Measuring {index + 1}/{len(kinds)}", file=sys.stderr, flush=True)
            run(kinds[index:index + 25])

    if args.json:
        args.json.write_text(json.dumps({"results": results, "failed": failed}, indent=2))
    print(f"# Measured {len(results)} kinds. Failed: {', '.join(failed) or 'none'}.")
    for capability in ("COLORABLE_KINDS", "FILLABLE_KINDS", "DASHABLE_KINDS"):
        print(f"{capability} = frozenset({{")
        for kind, supported in results.items():
            if supported[capability]:
                print(f"    {kind!r},")
        print("})")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
