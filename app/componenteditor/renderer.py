"""
Render/save core for component definitions (Qt-free).

Renders a CircuiTikZ symbol in the fixed-bounding-box / origin-at-zero /
lead-to-grid scheme (spec ``spec/component-editor.md`` §2–§4) and writes the two
data files: the geometry ``geometry.json`` and the registry/codegen
``definitions.json`` (plus the single ``origin_svg`` constant).

Shared by ``components/generate_components.py`` (batch: re-render everything) and the
GUI's Save (``render_one`` / ``save_component`` for a single component), so there
is exactly one renderer.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from app.canvas.style import SVG_PT_PER_GU
from app.components import render
from app.resources import resource_path

# Fixed bounding-box half-extent (GU); diode body scale (matches
# DIODE_SYMBOL_SCALE in app/codegen/circuitikz.py); standalone border.
BBOX = 3.0
DIODE_SCALE = 0.8
BORDER_PT = 2

DEFINITIONS_PATH = Path(resource_path("components", "definitions.json"))
GEOMETRY_PATH = Path(resource_path("components", "geometry.json"))


# ---------------------------------------------------------------------------
# TeX body construction
# ---------------------------------------------------------------------------

def _tex(off: list[float]) -> str:
    """Qt (y-down) grid offset -> CircuiTikZ (y-up) coordinate string."""
    return f"({off[0]:g},{-off[1]:g})"


def geometry_key(kind: str) -> str:
    return kind.replace(" ", "_")


def is_diode(entry: dict) -> bool:
    return any(v["name"] == "filled" for v in entry.get("variants", []))


def ctikzset(entry: dict) -> list[str]:
    return [f"diodes/scale={DIODE_SCALE:g}"] if is_diode(entry) else []


def lead_pins(entry: dict) -> list[dict]:
    """Pins that get a bridge lead by default: every pin except the placement
    origin (all pins when ``anchor_pin`` is null, e.g. the centre-placed op amp)."""
    ap = entry.get("anchor_pin")
    if ap is None:
        return list(entry["pins"])
    return [p for p in entry["pins"] if p["name"] != ap]


def entry_leads(entry: dict) -> list[dict]:
    """The leads to draw/emit: the explicit ``leads`` list if present (computed
    residual leads after scaling), else a lead to every non-origin pin (the
    lead-only default for un-scaled kinds like the op amp)."""
    if "leads" in entry:
        return [{"anchor": ld["anchor"], "to": list(ld["to"])} for ld in entry["leads"]]
    return [{"anchor": p["anchor"], "to": list(p["offset"])} for p in lead_pins(entry)]


def _scale_opt(entry: dict) -> str:
    """``", xscale=…, yscale=…"`` node option for the entry's scale (or "")."""
    scale = entry.get("scale")
    if not scale:
        return ""
    parts = []
    if abs(scale[0] - 1.0) > 1e-9:
        parts.append(f"xscale={scale[0]:g}")
    if abs(scale[1] - 1.0) > 1e-9:
        parts.append(f"yscale={scale[1]:g}")
    return (", " + ", ".join(parts)) if parts else ""


def render_body(entry: dict, *, suffix: str = "", option: str = "") -> str:
    """Build the TikZ body: origin pin at (0,0), optional scale, leads to grid."""
    tikz, emission, pins = entry["tikz"], entry["emission"], entry["pins"]
    bbox = rf"\useasboundingbox ({-BBOX},{-BBOX}) rectangle ({BBOX},{BBOX});"

    if emission == "two_terminal":
        return bbox + "\n" + rf"\draw (0,0) to[{tikz}{suffix}] {_tex(pins[1]['offset'])};"
    if emission == "node":
        return bbox + "\n" + rf"\draw (0,0) node[{tikz}] {{}};"

    ap = entry.get("anchor_pin")
    head = tikz + option + _scale_opt(entry)
    if ap is not None:
        oa = next(p["anchor"] for p in pins if p["name"] == ap)
        node = rf"\node[{head}, anchor={oa}] (X) at (0,0) {{}};"
    else:
        node = rf"\node[{head}] (X) at (0,0) {{}};"
    leads = "".join(
        rf"\draw (X.{ld['anchor']}) -- {_tex(ld['to'])};" for ld in entry_leads(entry)
    )
    return bbox + "\n" + node + leads


def geometry(entry: dict, *, suffix: str = "", option: str = "") -> dict:
    """Render and parse one (variant of a) symbol into a geometry dict."""
    svg, _ = render.render_svg(render_body(entry, suffix=suffix, option=option),
                         border_pt=BORDER_PT, ctikzset=ctikzset(entry))
    return render.parse_geometry(svg)


def variant_key(kind: str, variant: dict) -> str:
    """Geometry key for a variant: suffix ``D``->``D*``; option ``nigfete``->``nigfete_bodydiode``."""
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
    plain, _ = render.render_svg(body, border_pt=BORDER_PT, ctikzset=cs)
    marked, _ = render.render_svg(body + r"\fill (0,0) circle (0.6pt);", border_pt=BORDER_PT, ctikzset=cs)
    seen = {p["d"] for p in render.parse_geometry(plain)["paths"]}
    extra = [p for p in render.parse_geometry(marked)["paths"] if p["d"] not in seen]
    if len(extra) != 1:
        raise render.RenderError(f"origin calibration found {len(extra)} marks")
    nums = [float(x) for x in re.findall(r"-?\d+\.?\d*", extra[0]["d"])]
    xs, ys = nums[0::2], nums[1::2]
    return (round((min(xs) + max(xs)) / 2, 4), round((min(ys) + max(ys)) / 2, 4))


# ---------------------------------------------------------------------------
# Alignment computation (for the migration / editor)
# ---------------------------------------------------------------------------

def compute_alignment(measured: dict, targets: dict, *, tol: float = 0.01):
    """Compute a per-axis scale that lands pins on the grid, plus residual leads.

    *measured* / *targets* map each (non-origin) pin name to its offset from the
    origin pin — measured CircuiTikZ anchor vs. desired grid position.  Returns
    ``((sx, sy), [residual_pin_names])``: ``(sx, sy)`` is the common
    target/measured ratio per axis when it is consistent across all pins (so a
    single scale lands them, e.g. the BJT), else 1.0 for that axis; the residual
    list is the pins still off-grid after scaling (they need a bridge lead, e.g.
    the MOSFET drain in y).
    """
    names = [n for n in targets if n in measured]

    def axis_scale(i: int) -> float:
        movable = [n for n in names if abs(measured[n][i]) > 1e-6]
        ratios = [targets[n][i] / measured[n][i] for n in movable]
        if not ratios:
            return 1.0
        if max(ratios) - min(ratios) <= 1e-2:   # consistent → one scale lands all (BJT)
            return sum(ratios) / len(ratios)
        # Inconsistent (e.g. MOSFET drain vs source in y): pick the candidate scale
        # that minimises the worst residual; the rest get a short bridge lead.
        return min(ratios, key=lambda s: max(abs(measured[n][i] * s - targets[n][i]) for n in movable))

    sx, sy = axis_scale(0), axis_scale(1)
    residual = [
        n for n in names
        if abs(measured[n][0] * sx - targets[n][0]) > tol
        or abs(measured[n][1] * sy - targets[n][1]) > tol
    ]
    return (round(sx, 4), round(sy, 4)), residual


def fit_alignment(entry: dict) -> tuple[list[float] | None, list[dict]]:
    """Measure *entry*'s CircuiTikZ anchors and derive its alignment.

    Returns ``(scale, leads)`` where ``scale`` is ``[sx, sy]`` (or ``None`` when
    no stretch is needed) and ``leads`` bridges each residual pin to its grid
    target.  This is the single source of the alignment used by both the editor's
    **Fit pins to grid** and the batch generator — so a CircuiTikZ change reflows
    the alignment automatically on re-generation.  Requires the toolchain (it
    renders to measure); a non-multi-terminal entry returns ``(None, [])``.
    """
    if entry.get("emission") != "multi_terminal":
        return None, []
    pins = entry["pins"]
    anchor_of = {p["name"]: p.get("anchor") for p in pins}
    anchors = [a for a in anchor_of.values() if a]
    if not anchors:
        return None, []

    ap = entry.get("anchor_pin")
    if ap is None:
        # Centre-placed: every pin is chosen outward of the body, so bridge each
        # with a clean axis-aligned lead.  Scaling here would distort the symbol's
        # form (e.g. stretch the op-amp triangle), so we deliberately don't.
        return None, [{"anchor": anchor_of[p["name"]], "to": list(p["offset"])}
                      for p in pins if p.get("anchor")]

    # Anchor-pinned: stretch the symbol so its anchors land on the grid pins, and
    # bridge whatever residual a single scale can't remove (e.g. the MOSFET source).
    measured = render.measure_anchors(entry["tikz"], anchors)
    ox, oy = measured[anchor_of[ap]]
    other = [p for p in pins if p["name"] != ap]
    rel = {p["name"]: (round(measured[p["anchor"]][0] - ox, 4),
                       round(measured[p["anchor"]][1] - oy, 4))
           for p in other if p.get("anchor") in measured}
    targets = {p["name"]: tuple(p["offset"]) for p in other if p["name"] in rel}

    (sx, sy), residual = compute_alignment(rel, targets)
    scale = None if (abs(sx - 1.0) <= 1e-9 and abs(sy - 1.0) <= 1e-9) else [sx, sy]
    leads = [{"anchor": anchor_of[n], "to": list(targets[n])} for n in residual]
    return scale, leads


# ---------------------------------------------------------------------------
# Per-component data + geometry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Bounding box — derived from the rendered ink extent (never hand-typed).
#
# The fixed-bbox + origin-pin-at-(0,0) scheme means TeX origin maps to a constant
# SVG point (``origin_svg``), so local GU = (svg - origin) / SVG_PT_PER_GU.  The
# bbox is the extent of the drawn geometry (paths + glyphs) unioned with the pin
# positions, rounded outward to a clean grid step.
# ---------------------------------------------------------------------------

_PATH_TOKENS = re.compile(r"[MLHVCSQTZ]|-?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?")


def _path_points(d: str):
    """Yield every coordinate (endpoints + bezier control points) in an absolute
    SVG path ``d``.  Control points give a conservative (>=) bound — fine for a
    bounding box.  Handles the M/L/H/V/C/S/Z subset dvisvgm emits."""
    toks = _PATH_TOKENS.findall(d)
    i, n, cmd, cx, cy = 0, len(toks), None, 0.0, 0.0

    def num(k: int) -> float:
        return float(toks[k])

    while i < n:
        t = toks[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd == "Z":
                cmd = None
            continue
        if cmd in ("M", "L", "T"):
            cx, cy = num(i), num(i + 1); i += 2; yield (cx, cy)
        elif cmd == "H":
            cx = num(i); i += 1; yield (cx, cy)
        elif cmd == "V":
            cy = num(i); i += 1; yield (cx, cy)
        elif cmd == "C":
            yield (num(i), num(i + 1)); yield (num(i + 2), num(i + 3))
            cx, cy = num(i + 4), num(i + 5); i += 6; yield (cx, cy)
        elif cmd in ("S", "Q"):
            yield (num(i), num(i + 1))
            cx, cy = num(i + 2), num(i + 3); i += 4; yield (cx, cy)
        else:  # pragma: no cover - defensive
            i += 1


def _glyph_points(glyph: dict):
    """Yield the glyph's path points transformed by its placement matrix."""
    a, b, c, d, e, f = glyph.get("matrix", [1.0, 0.0, 0.0, 1.0, 0.0, 0.0])
    for (x, y) in _path_points(glyph["d"]):
        yield (a * x + c * y + e, b * x + d * y + f)


def compute_bbox(geom: dict, origin: tuple[float, float], pins: list[dict],
                 *, step: float = 0.05) -> list[float]:
    """Bounding box (GU) = rendered ink extent ∪ pin positions, rounded outward to
    *step*.  Derived from the geometry, so it is never hand-typed."""
    ox, oy = origin
    xs: list[float] = []
    ys: list[float] = []

    def add(x: float, y: float) -> None:
        xs.append((x - ox) / SVG_PT_PER_GU)
        ys.append((y - oy) / SVG_PT_PER_GU)

    for p in geom.get("paths", []):
        for (x, y) in _path_points(p["d"]):
            add(x, y)
    for g in geom.get("glyphs", []):
        for (x, y) in _glyph_points(g):
            add(x, y)
    for p in pins:
        xs.append(float(p["offset"][0]))
        ys.append(float(p["offset"][1]))
    if not xs:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        round(math.floor(min(xs) / step) * step, 2),
        round(math.floor(min(ys) / step) * step, 2),
        round(math.ceil(max(xs) / step) * step, 2),
        round(math.ceil(max(ys) / step) * step, 2),
    ]


def data_entry(kind: str, entry: dict) -> dict:
    """The definitions.json record for *kind*: authored fields + computed leads."""
    out: dict = {
        "display_name": entry["display_name"],
        "category": entry["category"],
        "emission": entry["emission"],
        "tikz": entry["tikz"],
        "labels": list(entry.get("labels", [])),
        # Placeholder; render_store/save_component overwrite this with the
        # bbox computed from the rendered ink extent (compute_bbox).
        "bbox": list(entry.get("bbox", [0.0, 0.0, 0.0, 0.0])),
        "pins": [
            {"name": p["name"], "offset": list(p["offset"]), "anchor": p.get("anchor")}
            for p in entry["pins"]
        ],
    }
    if entry["emission"] == "multi_terminal":
        out["anchor_pin"] = entry.get("anchor_pin")
        if entry.get("scale"):
            out["scale"] = [round(float(s), 4) for s in entry["scale"]]
        out["leads"] = entry_leads(entry)
    if entry.get("variants"):
        out["variants"] = [
            {"name": v["name"], "token": v["token"], "mode": v["mode"]}
            for v in entry["variants"]
        ]
    return out


def geometry_entries(kind: str, entry: dict) -> dict[str, dict]:
    """Geometry for *kind* and each of its variants, keyed by geometry key."""
    out = {geometry_key(kind): geometry(entry)}
    for v in entry.get("variants", []):
        out[variant_key(kind, v)] = geometry(entry, **_variant_render_args(v))
    return out


def realigned(entry: dict) -> dict:
    """Return *entry* with its ``scale``/``leads`` re-derived from a fresh anchor
    measurement (no-op for non-multi-terminal kinds).  Makes the alignment a
    *computed* property of the current CircuiTikZ library rather than a stored
    constant, so re-generation reflows it automatically (see ``fit_alignment``)."""
    if entry.get("emission") != "multi_terminal":
        return entry
    scale, leads = fit_alignment(entry)
    out = {**entry, "leads": leads}
    if scale is not None:
        out["scale"] = scale
    else:
        out.pop("scale", None)
    return out


def render_store(authored: dict[str, dict]) -> tuple[dict, dict, tuple[float, float]]:
    """Render every component: returns (geometry, components_data, origin_svg).

    Alignment (``scale``/``leads``) is re-derived per multi-terminal component, so
    the output reflects the current CircuiTikZ library, not the stored values."""
    origin = measure_origin(authored["R"]) if "R" in authored else measure_origin(
        next(iter(authored.values()))
    )
    geometry: dict[str, dict] = {}
    components: dict[str, dict] = {}
    for kind in sorted(authored):
        entry = realigned(authored[kind])
        mes = geometry_entries(kind, entry)
        geometry.update(mes)
        de = data_entry(kind, entry)
        de["bbox"] = compute_bbox(mes[geometry_key(kind)], origin, entry["pins"])
        components[kind] = de
    return geometry, components, origin


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def load_authored() -> dict[str, dict]:
    """The authored component records from definitions.json (new or old format)."""
    data = json.loads(DEFINITIONS_PATH.read_text(encoding="utf-8"))
    return data.get("components", data)


def write_store(geometry: dict, components: dict, origin: tuple[float, float]) -> None:
    GEOMETRY_PATH.write_text(json.dumps(geometry, indent=2) + "\n", encoding="utf-8")
    DEFINITIONS_PATH.write_text(
        json.dumps({"origin_svg": list(origin), "components": components},
                   indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def save_component(kind: str, entry: dict) -> None:
    """Add/replace one component: merge its geometry into geometry.json and its
    record into definitions.json (re-using the existing origin_svg)."""
    data = json.loads(DEFINITIONS_PATH.read_text(encoding="utf-8"))
    components = data.get("components", data)
    origin = tuple(data["origin_svg"]) if "origin_svg" in data else measure_origin(entry)
    geometry = json.loads(GEOMETRY_PATH.read_text(encoding="utf-8"))

    mes = geometry_entries(kind, entry)
    geometry.update(mes)
    de = data_entry(kind, entry)
    de["bbox"] = compute_bbox(mes[geometry_key(kind)], origin, entry["pins"])
    components[kind] = de
    write_store(geometry, components, origin)
