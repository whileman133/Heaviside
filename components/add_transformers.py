#!/usr/bin/env python3
r"""
*** ONE-SHOT BOOTSTRAP TOOL — DO NOT RE-RUN CASUALLY ***

This script carries its own authored component tables and OVERWRITES the
corresponding entries in ``components/definitions.json`` when run, silently
reverting any later edits made through the component-editor GUI or by hand.
``components/generate_components.py`` is the sanctioned re-render path: it
treats definitions.json as the source of truth and only regenerates geometry.

Author the transformer components (air-core + iron-core) into
``components/definitions.json`` + ``components/geometry.json``.

CircuiTikZ transformers are **quadpoles** (`transformer`, `transformer core`)
with four winding terminals — primary `A1`/`A2` (left) and secondary `B1`/`B2`
(right). Like the op amp and the digital blocks, each is a centre-placed
multi-terminal node (`anchor_pin` null); its native anchors sit at ±1.05 GU, so a
best-effort uniform grid-alignment scale (`renderer.best_alignment_scale`, ≈0.952)
is baked into the geometry to land all four terminals on the 0.25-GU grid (±1.0).
They reject the bipole ``l=`` quick key, so they carry no label slot — caption a
transformer with a nearby text annotation (and the winding dots show polarity).

Run after a fresh checkout or a CircuiTikZ change:

    python components/add_transformers.py

Requires ``latex`` + ``dvisvgm`` (the same toolchain as generate_components.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.components import render          # noqa: E402
from app.components import generate as renderer   # noqa: E402

CATEGORY = "Inductors"

# pin name -> CircuiTikZ quadpole anchor; primary p1/p2 (left), secondary s1/s2 (right).
_PINS = [("p1", "A1"), ("p2", "A2"), ("s1", "B1"), ("s2", "B2")]

# Polarity-dot variants (independent checkboxes): a dot at any of the four
# winding ends, drawn at the CircuiTikZ ``inner dot …`` anchor.
_DOTS = [
    ("dot_p1", "Dot: primary top",     "inner dot A1"),
    ("dot_p2", "Dot: primary bottom",  "inner dot A2"),
    ("dot_s1", "Dot: secondary top",   "inner dot B1"),
    ("dot_s2", "Dot: secondary bottom","inner dot B2"),
]


def _transformer(display: str, tikz: str, ctikzset: list[str] | None = None) -> dict:
    """A centre-placed transformer entry: measure the four winding terminals, bake
    a best-effort grid-alignment scale, pins at the scaled (grid) anchors, plus the
    four polarity-dot variants (measured ``inner dot`` positions).

    *ctikzset* selects the coil shape (``inductor=cute``/``inductor=european``).
    The cute/european keyword only takes effect as a scoped ``\\ctikzset`` (a node
    option doesn't reach the european rectangle), so it is stored on the entry and
    codegen wraps the node in its own group (`circuitikz._node_group_lines`)."""
    cs = ctikzset or []
    anchors = [a for _, a in _PINS] + [t for _, _, t in _DOTS]
    measured = render.measure_anchors(tikz, anchors, ctikzset=cs)
    sc = renderer.best_alignment_scale({a: measured[a] for _, a in _PINS})

    def scaled(a):
        return [round(measured[a][0] * sc, 4), round(measured[a][1] * sc, 4)]

    entry = {
        "display_name": display, "category": CATEGORY, "emission": "node",
        "tikz": tikz, "labels": [], "anchor_pin": None, "leads": [],
        "scale": [round(sc, 6), round(sc, 6)],
        "pins": [{"name": n, "anchor": a, "offset": scaled(a)}
                 for n, a in _PINS if a in measured],
        "variants": [{"name": n, "label": lbl, "token": tok, "mode": "dot",
                      "offset": scaled(tok)}
                     for n, lbl, tok in _DOTS if tok in measured],
    }
    if cs:
        entry["ctikzset"] = list(cs)
    return entry


ENTRIES: dict[str, dict] = {
    "transformer": _transformer("Transformer", "transformer"),
    "transformer core": _transformer("Transformer (Iron Core)", "transformer core"),
    "cute transformer": _transformer(
        "Transformer (Cute)", "transformer", ["inductor=cute"]),
    "cute transformer core": _transformer(
        "Transformer (Cute, Iron Core)", "transformer core", ["inductor=cute"]),
    "european transformer": _transformer(
        "Transformer (European)", "transformer", ["inductor=european"]),
    "european transformer core": _transformer(
        "Transformer (European, Iron Core)", "transformer core", ["inductor=european"]),
}


def main() -> int:
    for kind, entry in ENTRIES.items():
        renderer.save_component(kind, entry)
        print(f"  + {kind:18s} {entry['display_name']}  (scale {entry['scale'][0]})")
    print(f"Authored {len(ENTRIES)} transformer components.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
