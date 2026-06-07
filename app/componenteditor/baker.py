"""
Render/save core for component definitions (Qt-free).

Renders a CircuiTikZ symbol in the fixed-bounding-box / origin-at-zero /
lead-to-grid scheme (spec ``spec/component-editor.md`` §2–§4) and writes the two
data files: the geometry ``manifest.json`` and the registry/codegen
``components.json`` (plus the single ``origin_svg`` constant).

Shared by ``tools/generate_components.py`` (batch: re-render everything) and the
GUI's Save (``render_one`` / ``save_component`` for a single component), so there
is exactly one renderer.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.components import bake
from app.resources import resource_path

# Fixed bounding-box half-extent (GU); diode body scale (matches
# DIODE_SYMBOL_SCALE in app/codegen/circuitikz.py); standalone border.
BBOX = 3.0
DIODE_SCALE = 0.8
BORDER_PT = 2

COMPONENTS_PATH = Path(resource_path("components", "components.json"))
MANIFEST_PATH = Path(resource_path("tools", "circuitikz_svgs", "manifest.json"))


# ---------------------------------------------------------------------------
# TeX body construction
# ---------------------------------------------------------------------------

def _tex(off: list[float]) -> str:
    """Qt (y-down) grid offset -> CircuiTikZ (y-up) coordinate string."""
    return f"({off[0]:g},{-off[1]:g})"


def manifest_key(kind: str) -> str:
    return kind.replace(" ", "_")


def is_diode(entry: dict) -> bool:
    return any(v["name"] == "filled" for v in entry.get("variants", []))


def ctikzset(entry: dict) -> list[str]:
    return [f"diodes/scale={DIODE_SCALE:g}"] if is_diode(entry) else []


def lead_pins(entry: dict) -> list[dict]:
    """Pins that get a bridge lead: every pin except the placement origin
    (all pins when ``anchor_pin`` is null, e.g. the centre-placed op amp)."""
    ap = entry.get("anchor_pin")
    if ap is None:
        return list(entry["pins"])
    return [p for p in entry["pins"] if p["name"] != ap]


def render_body(entry: dict, *, suffix: str = "", option: str = "") -> str:
    """Build the TikZ body: origin pin at (0,0), leads to grid offsets."""
    tikz, emission, pins = entry["tikz"], entry["emission"], entry["pins"]
    bbox = rf"\useasboundingbox ({-BBOX},{-BBOX}) rectangle ({BBOX},{BBOX});"

    if emission == "two_terminal":
        return bbox + "\n" + rf"\draw (0,0) to[{tikz}{suffix}] {_tex(pins[1]['offset'])};"
    if emission == "node":
        return bbox + "\n" + rf"\draw (0,0) node[{tikz}] {{}};"

    ap = entry.get("anchor_pin")
    head = tikz + option
    if ap is not None:
        oa = next(p["anchor"] for p in pins if p["name"] == ap)
        node = rf"\node[{head}, anchor={oa}] (X) at (0,0) {{}};"
    else:
        node = rf"\node[{head}] (X) at (0,0) {{}};"
    leads = "".join(
        rf"\draw (X.{p['anchor']}) -- {_tex(p['offset'])};" for p in lead_pins(entry)
    )
    return bbox + "\n" + node + leads


def geometry(entry: dict, *, suffix: str = "", option: str = "") -> dict:
    """Render and parse one (variant of a) symbol into a manifest geometry dict."""
    svg, _ = bake.render(render_body(entry, suffix=suffix, option=option),
                         border_pt=BORDER_PT, ctikzset=ctikzset(entry))
    return bake.parse_geometry(svg)


def variant_key(kind: str, variant: dict) -> str:
    """Manifest key for a variant: suffix ``D``->``D*``; option ``nigfete``->``nigfete_bodydiode``."""
    if variant["mode"] == "suffix":
        return f"{kind}{variant['token']}"
    return f"{kind}_{variant['token']}"


def _variant_render_args(variant: dict) -> dict:
    if variant["mode"] == "suffix":
        return {"suffix": variant["token"]}
    return {"option": f", {variant['token']}"}


def measure_origin(sample: dict) -> tuple[float, float]:
    """Measure the constant SVG point that TeX origin (the origin pin) maps to.

    Renders *sample* with and without a tiny dot at (0,0) and diffs the paths;
    the dot's centroid is the placement anchor.  Constant for all symbols.
    """
    import re
    body = render_body(sample)
    cs = ctikzset(sample)
    plain, _ = bake.render(body, border_pt=BORDER_PT, ctikzset=cs)
    marked, _ = bake.render(body + r"\fill (0,0) circle (0.6pt);", border_pt=BORDER_PT, ctikzset=cs)
    seen = {p["d"] for p in bake.parse_geometry(plain)["paths"]}
    extra = [p for p in bake.parse_geometry(marked)["paths"] if p["d"] not in seen]
    if len(extra) != 1:
        raise bake.BakeError(f"origin calibration found {len(extra)} marks")
    nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", extra[0]["d"])]
    xs, ys = nums[0::2], nums[1::2]
    return (round((min(xs) + max(xs)) / 2, 4), round((min(ys) + max(ys)) / 2, 4))


# ---------------------------------------------------------------------------
# Per-component data + geometry
# ---------------------------------------------------------------------------

def data_entry(kind: str, entry: dict) -> dict:
    """The components.json record for *kind*: authored fields + computed leads."""
    out: dict = {
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
        out["anchor_pin"] = entry.get("anchor_pin")
        out["leads"] = [
            {"anchor": p["anchor"], "to": list(p["offset"])} for p in lead_pins(entry)
        ]
    if entry.get("variants"):
        out["variants"] = [
            {"name": v["name"], "token": v["token"], "mode": v["mode"]}
            for v in entry["variants"]
        ]
    return out


def manifest_entries(kind: str, entry: dict) -> dict[str, dict]:
    """Geometry for *kind* and each of its variants, keyed by manifest key."""
    out = {manifest_key(kind): geometry(entry)}
    for v in entry.get("variants", []):
        out[variant_key(kind, v)] = geometry(entry, **_variant_render_args(v))
    return out


def render_store(authored: dict[str, dict]) -> tuple[dict, dict, tuple[float, float]]:
    """Render every component: returns (manifest, components_data, origin_svg)."""
    origin = measure_origin(authored["R"]) if "R" in authored else measure_origin(
        next(iter(authored.values()))
    )
    manifest: dict[str, dict] = {}
    components: dict[str, dict] = {}
    for kind in sorted(authored):
        manifest.update(manifest_entries(kind, authored[kind]))
        components[kind] = data_entry(kind, authored[kind])
    return manifest, components, origin


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_authored() -> dict[str, dict]:
    """The authored component records from components.json (new or old format)."""
    data = json.loads(COMPONENTS_PATH.read_text(encoding="utf-8"))
    return data.get("components", data)


def write_store(manifest: dict, components: dict, origin: tuple[float, float]) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    COMPONENTS_PATH.write_text(
        json.dumps({"origin_svg": list(origin), "components": components},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def save_component(kind: str, entry: dict) -> None:
    """Add/replace one component: merge its geometry into the manifest and its
    record into components.json (re-using the existing origin_svg)."""
    data = json.loads(COMPONENTS_PATH.read_text(encoding="utf-8"))
    components = data.get("components", data)
    origin = tuple(data["origin_svg"]) if "origin_svg" in data else measure_origin(entry)
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

    manifest.update(manifest_entries(kind, entry))
    components[kind] = data_entry(kind, entry)
    write_store(manifest, components, origin)
