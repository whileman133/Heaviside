"""Measure each multi-terminal node's CircuiTikZ ``text`` anchor and bake it into
``components/definitions.json`` as ``text_anchor`` (GU, canvas y-down).

CircuiTikZ anchors a node's inline ``{…}`` text *west* at the shape's ``text``
anchor (its left edge sits there, extending right) — and that anchor is offset
from the node centre by a per-shape amount (a transistor's a hair east, an
op-amp's at the centre, a transformer's a unit north). To draw node text on the
canvas exactly where the compiled figure puts it, we need that offset per kind.

This script renders the *same* node the code generator emits (keyword + the
grid-alignment xscale/yscale, plus any gate-height ``\\ctikzset``), reads the
``text`` and ``center`` anchors via ``\\pgfpointanchor``, and writes the
difference (centre → text) into each multi-terminal entry. Re-run after a
CircuiTikZ upgrade or a component regeneration (it is idempotent).

Requires ``pdflatex`` on PATH. Run from the project root:

    python -m components.add_text_anchors
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from app.codegen import circuitikz as cg
from app.components.registry import REGISTRY
from app.preview import latex

_TEXPT_PER_GU = 28.45297  # CircuiTikZ default: 1 GU == 1 cm
_DEFS = Path(__file__).with_name("definitions.json")
_OFFSET_RE = re.compile(r"TXTANCHOR ([^:]+):\s*(-?[\d.]+)pt\s*\|\s*(-?[\d.]+)pt")


def _node_body(k: str) -> tuple[str, str]:
    """Return ``(node_id, latex_lines)`` rendering kind *k*'s node the way the
    generator does (multi-terminal: keyword + scale + any gate-height ``\\ctikzset``;
    single-terminal: a plain ``node[kind]``), with a named node and dummy text."""
    defn = REGISTRY[k]
    comp = defn.component_class(
        id=str(uuid.uuid4()), kind=k, position=(0.0, 0.0),
        rotation=0, options="", node_text="X",
    )
    node_id = f"node_{comp.id[:8]}"
    lines: list[str] = []
    if k in cg._MULTI_TERMINAL_KINDS:
        for s in cg._node_group_ctikzset(comp):
            lines.append(rf"  \ctikzset{{{s}}}")
        lines.append(rf"  \draw {cg._multi_terminal_line(comp)};")
    else:  # single-terminal node (ground, power rail): a plain named node
        lines.append(rf"  \draw (0,0) node[{defn.tikz_keyword}] ({node_id}) {{X}};")
    return node_id, "\n".join(lines)


def _measure_doc(kinds: list[str]) -> str:
    """A circuitikz body that emits each kind's node and typeouts
    ``(node.text) - (node.center)`` in pt."""
    body: list[str] = []
    for k in kinds:
        node_id, node_lines = _node_body(k)
        body.append(node_lines)
        body.append(
            rf"  \path let \p1=({node_id}.center), \p2=({node_id}.text) in "
            rf"\pgfextra{{\typeout{{TXTANCHOR {k}: "
            rf"\the\dimexpr\x2-\x1\relax | \the\dimexpr\y2-\y1\relax}}}};"
        )
    return "\\begin{circuitikz}\n" + "\n".join(body) + "\n\\end{circuitikz}"


def measure(kinds: list[str]) -> dict[str, tuple[float, float]]:
    """Return ``{kind: (dx, dy)}`` in GU (canvas y-down) for each kind."""
    tex = latex.build_tex(_measure_doc(kinds))
    out: dict[str, tuple[float, float]] = {}
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "m.tex"
        p.write_text(tex, encoding="utf-8")
        r = subprocess.run(
            ["pdflatex", "-interaction=nonstopmode", p.name],
            cwd=td, capture_output=True, text=True,
        )
        for k, xs, ys in _OFFSET_RE.findall(r.stdout):
            dx = round(float(xs) / _TEXPT_PER_GU, 4)
            dy = round(-float(ys) / _TEXPT_PER_GU, 4)   # y-up (TeX) -> y-down (canvas)
            out[k.strip()] = (dx, dy)
    return out


def main() -> None:
    doc = json.loads(_DEFS.read_text(encoding="utf-8"))
    data = doc["components"]
    kinds = sorted(
        k for k in (cg._MULTI_TERMINAL_KINDS | cg._NODE_KINDS)
        if k in REGISTRY and k in data
    )
    print(f"measuring text anchor for {len(kinds)} node-style kinds…")
    offsets = measure(kinds)
    missing = [k for k in kinds if k not in offsets]
    if missing:
        raise SystemExit(f"failed to measure: {missing}")

    changed = 0
    for k in kinds:
        dx, dy = offsets[k]
        prev = data[k].get("text_anchor")
        # Omit a (near-)zero anchor to keep the file tidy; the loader defaults it.
        if abs(dx) < 1e-4 and abs(dy) < 1e-4:
            if "text_anchor" in data[k]:
                del data[k]["text_anchor"]
                changed += 1
            continue
        new = [dx, dy]
        if prev != new:
            data[k]["text_anchor"] = new
            changed += 1
        print(f"  {k:24} text_anchor = ({dx:+.4f}, {dy:+.4f})")

    _DEFS.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"updated {changed} entr{'y' if changed == 1 else 'ies'} in {_DEFS.name}")


if __name__ == "__main__":
    main()
