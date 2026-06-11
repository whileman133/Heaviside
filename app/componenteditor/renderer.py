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
from app.components import library, render
from app.resources import resource_path

# Fixed bounding-box half-extent (GU); diode body scale (single-sourced in
# app/components/library.py, shared with app/codegen/circuitikz.py); standalone
# border.
BBOX = 3.0
DIODE_SCALE = library.DIODE_SYMBOL_SCALE
BORDER_PT = 2

DEFINITIONS_PATH = Path(resource_path("components", "definitions.json"))
GEOMETRY_PATH = Path(resource_path("components", "geometry.json"))


# ---------------------------------------------------------------------------
# TeX body construction
# ---------------------------------------------------------------------------

def _tex(off: list[float]) -> str:
    """Qt (y-down) grid offset -> CircuiTikZ (y-up) coordinate string."""
    return f"({off[0]:g},{-off[1]:g})"


# Canonical definition lives in the Qt-free component library; re-exported here
# under the same public name for the generator scripts and the editor.
geometry_key = library.geometry_key


def is_diode(entry: dict) -> bool:
    return any(v["name"] == "filled" for v in entry.get("variants", []))


def ctikzset(entry: dict) -> list[str]:
    """Shape settings applied before the node — the diode body scale, plus any
    explicit ``ctikzset`` on the entry (e.g. a logic-gate ``…/height``)."""
    cs = list(entry.get("ctikzset", []))
    if is_diode(entry):
        cs.append(f"diodes/scale={DIODE_SCALE:g}")
    return cs


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

    if emission == "path":
        return bbox + "\n" + rf"\draw (0,0) to[{tikz}{suffix}] {_tex(pins[1]['offset'])};"
    if not library.is_multi_terminal_entry(entry):
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
    if not library.is_multi_terminal_entry(entry):
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
    measured = render.measure_anchors(entry["tikz"], anchors, ctikzset=ctikzset(entry))
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
    if library.is_multi_terminal_entry(entry):
        out["anchor_pin"] = entry.get("anchor_pin")
        if entry.get("scale"):
            out["scale"] = [round(float(s), 4) for s in entry["scale"]]
        out["leads"] = entry_leads(entry)
        if entry.get("ctikzset"):    # static shape settings (e.g. inductor=cute)
            out["ctikzset"] = list(entry["ctikzset"])
    if entry.get("variants"):
        keep = ("name", "token", "mode", "label", "offset")
        out["variants"] = [{k: v[k] for k in keep if k in v} for v in entry["variants"]]
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
    if not library.is_multi_terminal_entry(entry):
        return entry
    scale, leads = fit_alignment(entry)
    out = {**entry, "leads": leads}
    if scale is not None:
        out["scale"] = scale
    else:
        out.pop("scale", None)
    return out


# ---------------------------------------------------------------------------
# Parametric components (variable pin count — e.g. logic gates).  A parametric
# entry declares a ``param`` block; the generator renders one geometry per value
# (keyed ``kind:N``), derives per-N scale/bbox, and computes the pins from the
# value.  At its *default* value it is an ordinary ``multi_terminal`` record, so
# static consumers (registry, palette, codegen) need no special handling — only
# the canvas/codegen/inspector consult the parameter to vary N.
# ---------------------------------------------------------------------------

def is_parametric(entry: dict) -> bool:
    return "param" in entry


def param_geometry_key(kind: str, n: int) -> str:
    return f"{geometry_key(kind)}:{n}"


def param_pins(entry: dict, n: int) -> list[dict]:
    """Pins for a parametric instance with value *n* (delegates to the library so
    the generator and runtime share one pin-layout implementation)."""
    from app.components import library
    return library.param_pins(entry["param"], n)


def _param_entry_at(entry: dict, n: int) -> dict:
    """A concrete (non-parametric) entry for value *n* — param tikz + computed pins."""
    p = entry["param"]
    base = {k: v for k, v in entry.items() if k != "param"}
    return {**base,
            "tikz": entry["tikz"] + ", " + p["option"].format(n=n),
            "pins": param_pins(entry, n),
            "anchor_pin": p["output"]["name"]}


def _gate_height(entry: dict, n: int, height_key: str, target_pitch: float) -> float:
    """The CircuiTikZ gate ``height`` that spaces *n* inputs at *target_pitch* GU.

    The native input pitch is linear in the gate height, so measure it at height 1
    and solve.  Setting the height (instead of a node yscale) keeps the inversion
    bubble round and the generated code idiomatic."""
    kw = entry["tikz"] + ", " + entry["param"]["option"].format(n=n)
    m = render.measure_anchors(kw, ["in 1", "in 2"], ctikzset=[f"{height_key}=1.0"])
    pitch_at_1 = abs(m["in 1"][1] - m["in 2"][1])
    return round(target_pitch / pitch_at_1, 4)


def render_parametric(kind: str, entry: dict, origin) -> tuple[dict, dict]:
    """Render every value of a parametric component.

    Returns ``(geometry_by_key, data_entry)``.  The data entry is an ordinary
    multi_terminal record at the default value plus a ``param`` block carrying the
    declaration and per-N ``scale``/``leads``/``bbox`` (geometry keyed ``kind:N``,
    with the default also aliased under the plain key for static lookups)."""
    p = entry["param"]
    height_key = p.get("height_key")            # gates: set body height (round bubble)
    target_pitch = p["input"]["pitch"]
    geoms: dict[str, dict] = {}
    n_data: dict[str, dict] = {}
    for n in range(int(p["min"]), int(p["max"]) + 1):
        e_n = _param_entry_at(entry, n)
        height = None
        if height_key:
            # Size the body so inputs land at the grid pitch — no node yscale, so
            # the inversion bubble stays round and the code stays idiomatic.
            height = _gate_height(entry, n, height_key, target_pitch)
            e_n = {**e_n, "ctikzset": [f"{height_key}={height}"]}
        scale, leads = fit_alignment(e_n)
        g = geometry({**e_n, "scale": scale, "leads": leads})
        geoms[param_geometry_key(kind, n)] = g
        nd = {"scale": scale, "leads": leads, "bbox": compute_bbox(g, origin, e_n["pins"])}
        if height is not None:
            nd["height"] = height
        n_data[str(n)] = nd
    default = int(p["default"])
    geoms[geometry_key(kind)] = geoms[param_geometry_key(kind, default)]  # static alias
    e_def = _param_entry_at(entry, default)
    # Store the *base* tikz keyword (not the concrete "…, number inputs=2"); codegen
    # re-appends the param option per instance, so storing the concrete form would
    # double it on the next regeneration.
    de = data_entry(kind, {**e_def, "tikz": entry["tikz"],
                           "scale": n_data[str(default)]["scale"],
                           "leads": n_data[str(default)]["leads"]})
    de["bbox"] = n_data[str(default)]["bbox"]
    de["param"] = {**{k: v for k, v in p.items() if k != "n_data"}, "n_data": n_data}
    return geoms, de


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
        if is_parametric(authored[kind]):
            geoms, de = render_parametric(kind, authored[kind], origin)
            geometry.update(geoms)
            components[kind] = de
            continue
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


# ---------------------------------------------------------------------------
# Multi-parameter mux/demux (CircuiTikZ ``muxdemux`` shape).
#
# A mux/demux carries TWO integer parameters — its data-line count and its
# select-line count — so the geometry is rendered for each value combination and
# the pins are *measured* (the configurable trapezoid's anchors don't sit on the
# grid) and baked into ``n_data`` per combo, along with the concrete ``muxdemux
# def`` option codegen re-emits. The shape is centre-placed (``anchor_pin`` null,
# like the op amp); a wire snaps onto the off-grid pin via the magnet.
# ---------------------------------------------------------------------------

def best_alignment_scale(measured: dict[str, tuple]) -> float:
    """A single uniform scale that lands **as many** of the measured anchor
    coordinates as possible on the 0.25-GU grid — the best-effort grid alignment
    for a symbol whose native anchors are off-grid (flip-flops, mux/demux).

    Candidates are each non-trivial coordinate's snap-ratio (the factor that puts
    that coordinate on the grid); the winner aligns the most coordinates, ties
    broken toward 1.0 (least distortion). Pins the scale can't bring on-grid (a
    mux's slanted select pins) just stay off-grid and connect via the magnet."""
    def _snap(v: float) -> float:
        return 0.0 if v == 0 else (1.0 if v > 0 else -1.0) * math.floor(abs(v) / 0.25 + 0.5) * 0.25

    cands = {1.0}
    for (x, y) in measured.values():
        for v in (x, y):
            if abs(v) > 0.1:
                cands.add(round(_snap(v) / v, 6))

    def aligned(s: float) -> int:
        return sum(1 for (x, y) in measured.values() for v in (x * s, y * s)
                   if abs(v) > 1e-9 and abs(v - _snap(v)) < 1e-3)

    return max(cands, key=lambda s: (aligned(s), -abs(s - 1.0)))


def _muxdemux_combo(role: str, data: int, sel: int) -> tuple[str, dict, list[tuple[str, str]]]:
    """The concrete ``muxdemux def`` option, the measured (unscaled) anchors, and
    the (pin-name, anchor) pairs for one (data, select) combo of a *mux*/*demux*.
    ``Lh``/``Rh`` track the data count (2× keeps the data-pin pitch constant);
    ``w`` tracks the select count so the bottom pins don't crowd."""
    w = sel + 1
    if role == "mux":      # data inputs on the left, one output right, selects below
        defstr = (f"muxdemux def={{Lh={2 * data}, Rh=2, NL={data}, "
                  f"NR=1, NB={sel}, NT=0, w={w}}}")
        pairs = ([(f"in{i}", f"lpin {i + 1}") for i in range(data)]
                 + [("out", "rpin 1")])
    else:                  # demux: one input left, data outputs right, selects below
        defstr = (f"muxdemux def={{Lh=2, Rh={2 * data}, NL=1, "
                  f"NR={data}, NB={sel}, NT=0, w={w}}}")
        pairs = ([("in", "lpin 1")]
                 + [(f"out{i}", f"rpin {i + 1}") for i in range(data)])
    pairs += [(f"sel{j}", f"bpin {j + 1}") for j in range(sel)]
    measured = render.measure_anchors(f"muxdemux, {defstr}", [a for _, a in pairs])
    return defstr, measured, [p for p in pairs if p[1] in measured]


def render_muxdemux(kind: str, entry: dict, origin) -> tuple[dict, dict]:
    """Render every (data, select) combo of a mux/demux. Returns
    ``(geometry_by_key, data_entry)`` — geometry keyed ``kind:data:select`` (the
    default aliased under the plain key), and a multi-parameter data entry whose
    ``n_data[<data>,<select>]`` holds the baked option, alignment scale, measured
    pins, and bbox. A best-effort uniform scale lands the data pins on the grid;
    the slanted select pins stay off-grid (magnet)."""
    rec = entry["muxdemux"]
    role, dname, sname = rec["role"], rec["data_param"], rec["select_param"]
    specs = {s["name"]: s for s in entry["params"]}
    dspec, sspec = specs[dname], specs[sname]
    base = {k: v for k, v in entry.items() if k not in ("params", "muxdemux")}

    geoms: dict[str, dict] = {}
    n_data: dict[str, dict] = {}
    for d in range(int(dspec["min"]), int(dspec["max"]) + 1):
        for s in range(int(sspec["min"]), int(sspec["max"]) + 1):
            opt, measured, pairs = _muxdemux_combo(role, d, s)
            sc = best_alignment_scale(measured)
            pins = [{"name": nm, "anchor": a,
                     "offset": [round(measured[a][0] * sc, 4), round(measured[a][1] * sc, 4)]}
                    for nm, a in pairs]
            ce = {**base, "tikz": "muxdemux", "emission": "node", "anchor_pin": None,
                  "pins": pins, "leads": [], "scale": [round(sc, 6), round(sc, 6)]}
            g = geometry(ce, option=", " + opt)
            geoms[f"{geometry_key(kind)}:{d}:{s}"] = g
            n_data[f"{d},{s}"] = {"option": opt, "scale": [round(sc, 6), round(sc, 6)],
                                  "pins": pins, "bbox": compute_bbox(g, origin, pins)}
    dd, sd = int(dspec["default"]), int(sspec["default"])
    geoms[geometry_key(kind)] = geoms[f"{geometry_key(kind)}:{dd}:{sd}"]   # static alias
    default = n_data[f"{dd},{sd}"]
    de = {
        "display_name": entry["display_name"], "category": entry["category"],
        "emission": "node", "tikz": "muxdemux", "labels": [], "anchor_pin": None,
        "pins": default["pins"], "bbox": default["bbox"],
        "params": entry["params"], "n_data": n_data,
    }
    return geoms, de


def save_muxdemux(kind: str, entry: dict) -> None:
    """Render and merge a parametric mux/demux into the data files (incremental —
    does not re-render the rest of the library)."""
    data = json.loads(DEFINITIONS_PATH.read_text(encoding="utf-8"))
    components = data["components"]
    origin = tuple(data["origin_svg"])
    geometry = json.loads(GEOMETRY_PATH.read_text(encoding="utf-8"))
    geoms, de = render_muxdemux(kind, entry, origin)
    geometry.update(geoms)
    components[kind] = de
    write_store(geometry, components, origin)
