#!/usr/bin/env python3
r"""
*** ONE-SHOT BOOTSTRAP TOOL — review output, never paste blindly ***

This prototype generates *candidate* entries from its own authored tables. It
does not write ``components/definitions.json`` itself, but pasting its output
over existing entries reverts any later component-editor GUI or hand edits.
``components/generate_components.py`` is the sanctioned re-render path: it
treats definitions.json as the source of truth and only regenerates geometry.

Prototype family importer (offline / dry-run).

Demonstrates how much of a CircuiTikZ component *family* can be imported into
``components/definitions.json`` automatically, and where human curation is still
needed.  It generates **candidate** entries, render-verifies each, derives their
alignment/bbox, and prints a review report + ready-to-paste JSON.  It does **not**
write ``definitions.json`` — review the candidates, then paste the good ones and
run ``components/generate_components.py``.

    python components/import_family.py

Requires ``latex`` + ``dvisvgm`` (the same toolchain the generator needs).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.components import library, render            # noqa: E402
from app.componenteditor import renderer              # noqa: E402

ORIGIN = library.origin_svg()


def snap(v: float, step: float = 0.25) -> float:
    return round(round(v / step) * step, 4)


# ---------------------------------------------------------------------------
# Family 1 — two-terminal bipoles.  The ENTIRE per-component input is a keyword
# and a display name: pins are the draw endpoints (0,0)->(2,0), no anchors, no
# alignment.  Everything else (geometry, bbox) is derived.
# ---------------------------------------------------------------------------

BIPOLES: dict[str, str] = {
    "vR": "Variable Resistor", "eC": "Polarized Capacitor", "pC": "Polar Capacitor",
    "fuse": "Fuse", "lamp": "Lamp", "ammeter": "Ammeter", "voltmeter": "Voltmeter",
    "ohmmeter": "Ohmmeter", "battery1": "Battery", "varcap": "Varactor",
    "memristor": "Memristor", "thermistor": "Thermistor", "photodiode": "Photodiode",
    "tline": "Transmission Line", "jumper": "Jumper",
}
_BIPOLE_LABELS = ["l", "l_", "v", "v^", "i", "i_"]


def bipole_candidate(keyword: str, display: str) -> dict:
    return {
        "display_name": display, "category": "Misc", "emission": "two_terminal",
        "tikz": keyword, "labels": list(_BIPOLE_LABELS),
        "pins": [{"name": "in", "offset": [0, 0], "anchor": None},
                 {"name": "out", "offset": [2, 0], "anchor": None}],
    }


# ---------------------------------------------------------------------------
# Family 2 — multi-terminal transistors.  Terminals are *discovered* by position
# (render.discover_terminals); the family supplies only the naming convention
# (candidate-anchor order) and which terminal anchors the node (anchor_pin).
# ---------------------------------------------------------------------------

# keyword: (display, candidate-anchor order [canonical names first], anchor-pin name)
_FET_NAMES = ["gate", "drain", "source", "G", "D", "S"]
_BJT_NAMES = ["base", "collector", "emitter", "B", "C", "E"]
TRANSISTORS: dict[str, tuple[str, list[str], str]] = {
    "nfet": ("N-MOSFET", _FET_NAMES, "gate"),
    "pfet": ("P-MOSFET", _FET_NAMES, "gate"),
    "njfet": ("N-JFET", _FET_NAMES, "gate"),
    "pjfet": ("P-JFET", _FET_NAMES, "gate"),
}


def transistor_candidate(keyword: str, display: str, names: list[str], anchor_pin: str):
    """Discover terminals, snap to the 0.25 grid, and build a candidate entry."""
    terminals = render.discover_terminals(keyword, names)
    if anchor_pin not in terminals:
        return None, f"anchor pin {anchor_pin!r} not among discovered {list(terminals)}"
    ox, oy = terminals[anchor_pin]
    pins = []
    for name, (x, y) in terminals.items():
        pins.append({"name": name, "offset": [snap(x - ox), snap(y - oy)], "anchor": name})
    entry = {
        "display_name": display, "category": "Transistors", "emission": "multi_terminal",
        "tikz": keyword, "labels": ["l"], "anchor_pin": anchor_pin, "pins": pins,
    }
    scale, leads = renderer.fit_alignment(entry)        # derive alignment
    if scale is not None:
        entry["scale"] = scale
    entry["leads"] = leads
    return entry, None


# ---------------------------------------------------------------------------

def verify(entry: dict) -> tuple[dict | None, str | None]:
    """Render-verify a candidate; return (info, error)."""
    try:
        geom = renderer.geometry(entry)
    except render.RenderError as exc:
        return None, str(exc).splitlines()[0]
    bbox = renderer.compute_bbox(geom, ORIGIN, entry["pins"])
    return {"paths": len(geom["paths"]), "bbox": bbox}, None


def main() -> int:
    write = "--write" in sys.argv
    have = set(library.load_library())               # already in the library
    accepted: dict[str, dict] = {}
    clean = curate = failed = 0

    print("=== Two-terminal bipoles (input = keyword + name; everything else derived) ===")
    for kw, name in BIPOLES.items():
        if kw in have:
            print(f"  {kw:12} — already in library, skipped"); continue
        entry = bipole_candidate(kw, name)
        info, err = verify(entry)
        if err:
            print(f"  {kw:12} FAIL  {err}"); failed += 1; continue
        print(f"  {kw:12} ok   paths={info['paths']:<2} bbox={info['bbox']}")
        accepted[kw] = entry; clean += 1

    print("\n=== Multi-terminal transistors (terminals discovered; names = convention) ===")
    for kw, (name, names, ap) in TRANSISTORS.items():
        if kw in have:
            print(f"  {kw:12} — already in library, skipped"); continue
        entry, err = transistor_candidate(kw, name, names, ap)
        if err:
            print(f"  {kw:12} FAIL  {err}"); failed += 1; continue
        info, verr = verify(entry)
        if verr:
            print(f"  {kw:12} FAIL  {verr}"); failed += 1; continue
        pin_str = ", ".join(f"{p['name']}@{tuple(p['offset'])}" for p in entry["pins"])
        print(f"  {kw:12} ok   scale={entry.get('scale')} leads={len(entry['leads'])}  pins: {pin_str}")
        accepted[kw] = entry; curate += 1

    print("\n=== Summary ===")
    print(f"  bipoles auto-imported clean (zero curation): {clean}")
    print(f"  transistors needing a naming convention + grid review: {curate}")
    print(f"  failed to compile (need manual modelling): {failed}")

    if not write:
        print("\n=== Candidate JSON (review) ===")
        print(json.dumps(accepted, indent=2, ensure_ascii=False))
        print("\nRe-run with --write to merge these into definitions.json and regenerate.")
        return 0

    # --write: merge the accepted candidates into definitions.json (the authored
    # input) and regenerate everything.  render_store re-derives bbox + alignment
    # for every component, so the new kinds get the same treatment as the rest.
    authored = renderer.load_authored()
    new = {kw: entry for kw, entry in accepted.items() if kw not in authored}
    authored.update(new)
    geometry, components, origin = renderer.render_store(authored)
    renderer.write_store(geometry, components, origin)
    print(f"\nWrote {len(new)} new components; definitions.json now has {len(components)}.")
    print("Added:", ", ".join(sorted(new)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
