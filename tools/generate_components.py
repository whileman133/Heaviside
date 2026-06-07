#!/usr/bin/env python3
r"""
Unified component renderer (spec: ``spec/component-editor.md``).

ONE tool that renders every CircuiTikZ symbol and writes both:

* ``tools/circuitikz_svgs/manifest.json`` — the symbol **geometry** (paths/glyphs,
  read by ``app/canvas/svgsym.py``); and
* ``components/components.json`` — the per-component **registry + codegen data**
  (pins, bbox, leads, metadata, read by ``app/components/library.py``), plus the
  single ``origin_svg`` placement constant.

The render scheme removes every hand-stored magic number:

* each symbol is drawn inside a **fixed bounding box** so TeX origin maps to a
  constant SVG point (the ``origin_svg``) for *every* symbol — no per-component
  placement anchors;
* the symbol is placed with its **origin pin anchored at TeX (0,0)**, and a short
  **lead** is drawn from every other pin to its registry grid offset, so the pins
  land on the grid with a single uniform scale — no per-component xscale/yscale.

Adding a component: add an entry to ``components/components.json`` (measure its
pin offsets with ``app/components/bake.py``) and re-run this tool.  Requires
``latex`` + ``dvisvgm`` (+ Ghostscript via ``LIBGS`` for filled-path symbols).

    python tools/generate_components.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.components import bake  # noqa: E402

COMPONENTS = ROOT / "components" / "components.json"
MANIFEST = ROOT / "tools" / "circuitikz_svgs" / "manifest.json"

# Fixed bounding-box half-extent (GU) and the diode body scale (matches
# DIODE_SYMBOL_SCALE in app/codegen/circuitikz.py).
BBOX = 3.0
DIODE_SCALE = 0.8
BORDER_PT = 2


def _tex(off: list[float]) -> str:
    """Qt (y-down) grid offset -> CircuiTikZ (y-up) coordinate string."""
    return f"({off[0]:g},{-off[1]:g})"


def manifest_key(kind: str) -> str:
    return kind.replace(" ", "_")


def _is_diode(entry: dict) -> bool:
    return any(v["name"] == "filled" for v in entry.get("variants", []))


def _ctikzset(entry: dict) -> list[str]:
    return [f"diodes/scale={DIODE_SCALE:g}"] if _is_diode(entry) else []


def _lead_pins(entry: dict) -> list[dict]:
    """Pins that get a bridge lead: every pin except the placement origin.

    For a centre-placed node (``anchor_pin`` null, e.g. op amp) that is all pins.
    """
    ap = entry.get("anchor_pin")
    if ap is None:
        return list(entry["pins"])
    return [p for p in entry["pins"] if p["name"] != ap]


def _render_body(entry: dict, *, suffix: str = "", option: str = "") -> str:
    """Build the TikZ body: origin pin at (0,0), leads to grid offsets."""
    tikz, emission, pins = entry["tikz"], entry["emission"], entry["pins"]
    bbox = rf"\useasboundingbox ({-BBOX},{-BBOX}) rectangle ({BBOX},{BBOX});"

    if emission == "two_terminal":
        # pins[0] = origin (at 0,0); pins[1] = terminal.
        return bbox + "\n" + rf"\draw (0,0) to[{tikz}{suffix}] {_tex(pins[1]['offset'])};"
    if emission == "node":
        return bbox + "\n" + rf"\draw (0,0) node[{tikz}] {{}};"

    # multi_terminal
    ap = entry.get("anchor_pin")
    head = tikz + option
    if ap is not None:
        oa = next(p["anchor"] for p in pins if p["name"] == ap)
        node = rf"\node[{head}, anchor={oa}] (X) at (0,0) {{}};"
    else:
        node = rf"\node[{head}] (X) at (0,0) {{}};"
    leads = "".join(
        rf"\draw (X.{p['anchor']}) -- {_tex(p['offset'])};" for p in _lead_pins(entry)
    )
    return bbox + "\n" + node + leads


def _geometry(entry: dict, *, suffix: str = "", option: str = "") -> dict:
    body = _render_body(entry, suffix=suffix, option=option)
    svg, _ = bake.render(body, border_pt=BORDER_PT, ctikzset=_ctikzset(entry))
    return bake.parse_geometry(svg)


def _measure_origin(sample: dict) -> tuple[float, float]:
    """Measure the constant SVG coordinate of TeX (0,0) (the origin pin)."""
    body = _render_body(sample)
    plain, _ = bake.render(body, border_pt=BORDER_PT, ctikzset=_ctikzset(sample))
    marked, _ = bake.render(body + r"\fill (0,0) circle (0.6pt);",
                            border_pt=BORDER_PT, ctikzset=_ctikzset(sample))
    seen = {p["d"] for p in bake.parse_geometry(plain)["paths"]}
    extra = [p for p in bake.parse_geometry(marked)["paths"] if p["d"] not in seen]
    assert len(extra) == 1, f"origin calibration found {len(extra)} marks"
    nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", extra[0]["d"])]
    xs, ys = nums[0::2], nums[1::2]
    return (round((min(xs) + max(xs)) / 2, 4), round((min(ys) + max(ys)) / 2, 4))


def _variant_key(kind: str, variant: dict) -> str:
    # suffix: D -> D* ; option: nigfete -> nigfete_bodydiode
    if variant["mode"] == "suffix":
        return f"{kind}{variant['token']}"
    return f"{kind}_{variant['token']}"


def _variant_render_args(variant: dict) -> dict:
    if variant["mode"] == "suffix":
        return {"suffix": variant["token"]}
    return {"option": f", {variant['token']}"}


def _load_authored() -> dict[str, dict]:
    data = json.loads(COMPONENTS.read_text(encoding="utf-8"))
    return data.get("components", data)  # accept old flat format or new nested


def main() -> int:
    authored = _load_authored()
    manifest: dict[str, dict] = {}
    out_components: dict[str, dict] = {}

    # One origin constant for all symbols — measure it and assert it holds.
    origin = _measure_origin(authored["R"])
    print(f"origin_svg = {origin}")

    for kind in sorted(authored):
        entry = authored[kind]
        # Geometry (base + variants) into the manifest.
        manifest[manifest_key(kind)] = _geometry(entry)
        for v in entry.get("variants", []):
            manifest[_variant_key(kind, v)] = _geometry(entry, **_variant_render_args(v))

        # Rebuild the data entry: keep authored fields, recompute leads, drop scale.
        new_entry = {
            "display_name": entry["display_name"],
            "category": entry["category"],
            "emission": entry["emission"],
            "tikz": entry["tikz"],
            "labels": list(entry.get("labels", [])),
            "bbox": list(entry["bbox"]),
            "pins": [
                {"name": p["name"], "offset": list(p["offset"]), "anchor": p.get("anchor")}
                for p in entry["pins"]
            ],
        }
        if entry["emission"] == "multi_terminal":
            new_entry["anchor_pin"] = entry.get("anchor_pin")
            new_entry["leads"] = [
                {"anchor": p["anchor"], "to": list(p["offset"])} for p in _lead_pins(entry)
            ]
        if entry.get("variants"):
            new_entry["variants"] = [
                {"name": v["name"], "token": v["token"], "mode": v["mode"]}
                for v in entry["variants"]
            ]
        out_components[kind] = new_entry

    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    COMPONENTS.write_text(
        json.dumps({"origin_svg": list(origin), "components": out_components},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(out_components)} components, {len(manifest)} manifest entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
