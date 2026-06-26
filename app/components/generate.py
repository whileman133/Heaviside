"""
Render/save core for component definitions (Qt-free).

Renders a CircuiTikZ symbol in the fixed-bounding-box / origin-at-zero /
lead-to-grid scheme (spec ``spec/component-pipeline.md`` §2–§4) and writes the
two data files: the geometry ``geometry.json`` and the registry/codegen
``definitions.json`` (plus the single ``origin_svg`` constant and the
``circuitikz_version`` generation stamp).

``components/generate_components.py`` (batch: re-render everything) is the
main driver; the ``components/`` authoring scripts use the incremental
``save_component`` / ``save_muxdemux``. (Formerly ``app/componenteditor/
renderer.py`` — the GUI editor it served was removed once the automated
measurement/alignment pipeline made manual fix-ups redundant.)
"""

from __future__ import annotations

import json
import math
import re
import tomllib
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
# Generation-time configuration (spec/component-pipeline.md §4). Read once, at
# import, from components/generation.toml — a generation-only file (the running
# app never imports this module). Tunes the alignment search and the parametric
# height pre-pass.
# ---------------------------------------------------------------------------
CONFIG_PATH = Path(resource_path("components", "generation.toml"))
with open(CONFIG_PATH, "rb") as _f:
    _CONFIG = tomllib.load(_f)

GRID_GU: float = _CONFIG["alignment"]["grid_gu"]
SCALE_MIN: float = _CONFIG["alignment"]["scale_min"]
SCALE_MAX: float = _CONFIG["alignment"]["scale_max"]
SNAP_TOL_GU: float = _CONFIG["alignment"]["snap_tolerance_gu"]
SCALE_ANISOTROPY_MAX: float = _CONFIG["alignment"]["scale_anisotropy_max"]
GATE_INPUT_PITCH_GU: float = _CONFIG["gates"]["input_pitch_gu"]
MUX_DATA_PITCH_GU: float = _CONFIG["muxdemux"]["data_pitch_gu"]
MUX_SELECT_SPACING_GU: float = _CONFIG["muxdemux"]["select_spacing_gu"]


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
    """Build the TikZ body: centre at (0,0), optional per-axis scale."""
    tikz, emission, pins = entry["tikz"], entry["emission"], entry["pins"]
    bbox = rf"\useasboundingbox ({-BBOX},{-BBOX}) rectangle ({BBOX},{BBOX});"

    if emission == "path":
        return bbox + "\n" + rf"\draw (0,0) to[{tikz}{suffix}] {_tex(pins[1]['offset'])};"
    if not library.is_multi_terminal_entry(entry):
        return bbox + "\n" + rf"\draw (0,0) node[{tikz}] {{}};"

    # Every multi-terminal node is centre-placed and aligned by scale alone
    # (§4): no anchor= placement, no lead stubs — pins sit at the scaled anchor.
    head = tikz + option + _scale_opt(entry)
    return bbox + "\n" + rf"\node[{head}] (X) at (0,0) {{}};"


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

def _snap(v: float) -> float:
    """Round *v* (GU) to the nearest pin-grid multiple."""
    return round(v / GRID_GU) * GRID_GU


def _axis_scale(coords: list[float]) -> float:
    """The best per-axis scale for one axis's measured anchor *coords* (§4).

    Candidate scales are ``1.0`` plus, for each coordinate, the factor that lands
    *that* coordinate exactly on the grid — clamped to ``[SCALE_MIN, SCALE_MAX]``
    so the search can't collapse toward the origin or distort past the bound.
    The winner maximises the count of coordinates that land on the grid, tie-broken
    toward ``1.0`` (least distortion) then toward the smaller scale (deterministic,
    so regeneration is bit-stable). Returned rounded to 4 dp.
    """
    cands = {1.0}
    for v in coords:
        if abs(v) > 1e-3:
            s = _snap(v) / v
            if SCALE_MIN <= s <= SCALE_MAX:
                cands.add(round(s, 6))

    def on_grid(s: float) -> int:
        return sum(1 for v in coords if abs(s * v - _snap(s * v)) < SNAP_TOL_GU)

    best = max(cands, key=lambda s: (on_grid(s), -abs(s - 1.0), -s))
    return round(best, 4)


def _scale_for(measured: dict[str, tuple], anchors: list[str]) -> tuple[float, float]:
    """The per-axis ``(sx, sy)`` for a set of *measured* anchor positions — the
    shared core of `best_alignment` (fixed nodes) and `render_muxdemux`.

    Per-axis while the two axes stay within ``SCALE_ANISOTROPY_MAX`` of each
    other; beyond that the symbol falls back to a **single uniform scale**
    (computed over both axes' coordinates). The cap protects thick-diagonal-stroke
    symbols (switch blades) from shearing in the LaTeX export, where the canvas —
    which buckets stroke widths — could not follow (§4)."""
    present = [a for a in anchors if a in measured]
    xs = [measured[a][0] for a in present]
    ys = [measured[a][1] for a in present]
    sx, sy = _axis_scale(xs), _axis_scale(ys)
    hi, lo = max(sx, sy), min(sx, sy)
    if lo > 0 and hi / lo > SCALE_ANISOTROPY_MAX:
        sx = sy = _axis_scale(xs + ys)   # one uniform scale (best-effort over both)
    return sx, sy


def _grid_offset(v: float) -> float:
    """One scaled pin coordinate, snapped to the grid when it lands within
    ``SNAP_TOL_GU`` of a grid line (so an on-grid pin gets the clean value, e.g.
    ``0.5`` not ``0.49``); a genuinely off-grid pin keeps its true scaled value
    (reached by the magnet). The ``+ 0.0`` normalises ``-0.0`` to ``0.0``."""
    s = _snap(v)
    out = s if abs(v - s) < SNAP_TOL_GU else v
    return round(out, 4) + 0.0


def _scaled_pins(pins: list[dict], measured: dict[str, tuple],
                 sx: float, sy: float) -> list[dict]:
    """*pins* with each anchored pin's ``offset`` set to its grid-snapped scaled
    anchor position; un-anchored pins pass through unchanged."""
    return [
        {**p, "offset": [_grid_offset(sx * measured[p["anchor"]][0]),
                         _grid_offset(sy * measured[p["anchor"]][1])]}
        if p.get("anchor") in measured else dict(p)
        for p in pins
    ]


def best_alignment(entry: dict) -> tuple[list[float] | None, list[dict]]:
    """Measure *entry*'s anchors and derive its uniform alignment (§4).

    Returns ``(scale, pins)``: ``scale`` is the per-axis ``[sx, sy]`` (or ``None``
    when ``[1.0, 1.0]``), and ``pins`` is *entry*'s pins with each anchored pin's
    ``offset`` set to its scaled measured position (relative to the node centre).
    Pins that don't land on the grid keep their true scaled offset and are reached
    by the canvas magnet. Non-multi-terminal entries pass through unchanged.
    Requires the toolchain (it renders to measure).
    """
    if not library.is_multi_terminal_entry(entry):
        return None, list(entry["pins"])
    pins = entry["pins"]
    anchors = [p["anchor"] for p in pins if p.get("anchor")]
    measured = render.measure_anchors(entry["tikz"], anchors, ctikzset=ctikzset(entry))
    # The grid-alignment scale is derived from the node's own terminal anchors only.
    # Sub-node anchors (``-L1.midtap`` taps) are interior connection points carried
    # along by the same scale — they must not pull on the alignment optimisation.
    grid_anchors = [a for a in anchors if not a.startswith("-")]
    sx, sy = _scale_for(measured, grid_anchors)
    out_pins = _scaled_pins(pins, measured, sx, sy)
    scale = None if (sx == 1.0 and sy == 1.0) else [sx, sy]
    return scale, out_pins


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
    """The definitions.json record for *kind*: authored fields + computed scale."""
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
        if entry.get("scale"):
            out["scale"] = [round(float(s), 4) for s in entry["scale"]]
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
    """Return *entry* with its `scale` and pin offsets re-derived from a fresh
    anchor measurement (the uniform `best_alignment`, §4); a no-op for
    non-multi-terminal kinds. Makes alignment a *computed* property of the
    current CircuiTikZ library, so re-generation reflows it automatically."""
    if not library.is_multi_terminal_entry(entry):
        return entry
    scale, pins = best_alignment(entry)
    out = {**entry, "pins": pins}
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
    """A concrete (non-parametric) entry for value *n* — param tikz + pin
    names/anchors (the offsets are placeholders, re-derived by best_alignment)."""
    p = entry["param"]
    base = {k: v for k, v in entry.items() if k != "param"}
    return {**base,
            "tikz": entry["tikz"] + ", " + p["option"].format(n=n),
            "pins": param_pins(entry, n)}


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
    """Render every value of a parametric component (logic gates).

    Each value runs the §4 pre-pass (set the gate body height so the inputs sit
    at ``GATE_INPUT_PITCH_GU``) then the uniform ``best_alignment`` — centre-placed,
    per-axis scale, **measured** pins (like mux/demux). Returns
    ``(geometry_by_key, data_entry)``: the data entry is an ordinary multi_terminal
    record at the default value plus a ``param`` block carrying the declaration and
    per-N ``scale``/``pins``/``bbox`` (and ``height`` for gates). Geometry is keyed
    ``kind:N``, with the default also aliased under the plain key."""
    p = entry["param"]
    height_key = p.get("height_key")            # gates: set body height (round bubble)
    geoms: dict[str, dict] = {}
    n_data: dict[str, dict] = {}
    for n in range(int(p["min"]), int(p["max"]) + 1):
        e_n = _param_entry_at(entry, n)
        height = None
        if height_key:
            # Size the body so inputs land at the grid pitch — no node yscale, so
            # the inversion bubble stays round and the code stays idiomatic.
            height = _gate_height(entry, n, height_key, GATE_INPUT_PITCH_GU)
            e_n = {**e_n, "ctikzset": [f"{height_key}={height}"]}
        scale, pins = best_alignment(e_n)
        e_n = {**e_n, "scale": scale, "pins": pins}
        g = geometry(e_n)
        geoms[param_geometry_key(kind, n)] = g
        nd = {"scale": scale,
              "pins": [{"name": q["name"], "offset": list(q["offset"]),
                        "anchor": q.get("anchor")} for q in pins],
              "bbox": compute_bbox(g, origin, pins)}
        if height is not None:
            nd["height"] = height
        n_data[str(n)] = nd
    default = int(p["default"])
    geoms[geometry_key(kind)] = geoms[param_geometry_key(kind, default)]  # static alias
    dflt = n_data[str(default)]
    # Store the *base* tikz keyword (not the concrete "…, number inputs=2"); codegen
    # re-appends the param option per instance, so storing the concrete form would
    # double it on the next regeneration.
    de = data_entry(kind, {**_param_entry_at(entry, default), "tikz": entry["tikz"],
                           "scale": dflt["scale"], "pins": dflt["pins"]})
    de["bbox"] = dflt["bbox"]
    de["param"] = {**{k: v for k, v in p.items() if k != "n_data"}, "n_data": n_data}
    return geoms, de


def render_store(authored: dict[str, dict]) -> tuple[dict, dict, tuple[float, float]]:
    """Render every component: returns (geometry, components_data, origin_svg).

    The ``scale`` and pin offsets are re-derived per multi-terminal component
    (``best_alignment``, §4), so the output reflects the current CircuiTikZ
    library, not the stored values."""
    origin = measure_origin(authored["R"]) if "R" in authored else measure_origin(
        next(iter(authored.values()))
    )
    geometry: dict[str, dict] = {}
    components: dict[str, dict] = {}
    for kind in sorted(authored):
        if "muxdemux" in authored[kind]:
            # Two-parameter mux/demux: every (data, select) combo is rendered
            # and measured. Regression guard: routed as a plain node, these
            # would silently lose params/n_data and every kind:data:select
            # geometry combo.
            geoms, de = render_muxdemux(kind, authored[kind], origin)
            geometry.update(geoms)
            components[kind] = de
            continue
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


def measure_circuitikz_version() -> str | None:
    """The installed CircuiTikZ version, from a minimal probe compile.

    ``None`` when the package reports no version. Used by the batch generator
    to stamp ``definitions.json`` with the version the library was rendered
    against, so symbol/anchor drift is diagnosable later."""
    _svg, log = render.render_svg(r"\draw (0,0) -- (0.25,0);", border_pt=BORDER_PT)
    return render.circuitikz_version(log)


def write_store(geometry: dict, components: dict, origin: tuple[float, float],
                circuitikz_version: str | None = None) -> None:
    """Write both data files. *circuitikz_version* stamps definitions.json with
    the version the library was generated against (omitted when unknown)."""
    GEOMETRY_PATH.write_text(json.dumps(geometry, indent=2) + "\n", encoding="utf-8")
    data: dict = {"origin_svg": list(origin)}
    if circuitikz_version:
        data["circuitikz_version"] = circuitikz_version
    data["components"] = components
    DEFINITIONS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def save_component(kind: str, entry: dict) -> None:
    """Add/replace one component: merge its geometry into geometry.json and its
    record into definitions.json (re-using the existing origin_svg and the
    batch-generation circuitikz_version stamp)."""
    data = json.loads(DEFINITIONS_PATH.read_text(encoding="utf-8"))
    components = data.get("components", data)
    origin = tuple(data["origin_svg"]) if "origin_svg" in data else measure_origin(entry)
    geometry = json.loads(GEOMETRY_PATH.read_text(encoding="utf-8"))

    mes = geometry_entries(kind, entry)
    geometry.update(mes)
    de = data_entry(kind, entry)
    de["bbox"] = compute_bbox(mes[geometry_key(kind)], origin, entry["pins"])
    components[kind] = de
    write_store(geometry, components, origin,
                circuitikz_version=data.get("circuitikz_version"))


# ---------------------------------------------------------------------------
# Multi-parameter mux/demux (CircuiTikZ ``muxdemux`` shape).
#
# A mux/demux carries TWO integer parameters — its data-line count and its
# select-line count — so the geometry is rendered for each value combination and
# the pins are *measured* (the configurable trapezoid's anchors don't sit on the
# grid) and baked into ``n_data`` per combo, along with the concrete ``muxdemux
# def`` option codegen re-emits. Like every node it is centre-placed and aligned
# by the uniform per-axis scale (§4); a wire snaps onto an off-grid pin via the
# magnet. The body size follows the [muxdemux] config knobs.
# ---------------------------------------------------------------------------

# Fixed nominal body size (GU) for every (data, select) combo — the box no longer
# grows with the pin count (the pins simply redistribute inside it); the user
# resizes the placed instance freely via the 2D drag handles (a per-instance scale,
# §6.4). Chosen to reproduce the original default (2 data lines, 1 select) so a
# default placement is unchanged.
MUX_FIXED_LH: float = 4.0   # left edge height (data side of a mux)
MUX_FIXED_RH: float = 2.0   # right edge height (output side of a mux)
MUX_FIXED_W: float = 2.0    # body width


def _muxdemux_combo(role: str, data: int, sel: int) -> tuple[str, dict, list[tuple[str, str]]]:
    """The concrete ``muxdemux def`` option, the measured (unscaled) anchors, and
    the (pin-name, anchor) pairs for one (data, select) combo of a *mux*/*demux*.
    The body is a **fixed** size (``MUX_FIXED_LH``/``RH``/``W``) independent of the
    pin counts — more pins simply pack closer; the user resizes the placed instance
    via the drag handles (§6.4). (A demux is a mirrored mux, so only the ``mux``
    role is generated; the ``demux`` branch is kept for completeness.)"""
    lh, rh, w = MUX_FIXED_LH, MUX_FIXED_RH, MUX_FIXED_W
    if role == "mux":      # data inputs on the left, one output right, selects below
        defstr = (f"muxdemux def={{Lh={lh}, Rh={rh}, NL={data}, "
                  f"NR=1, NB={sel}, NT=0, w={w}}}")
        pairs = ([(f"in{i}", f"lpin {i + 1}") for i in range(data)]
                 + [("out", "rpin 1")])
    else:                  # demux: one input left, data outputs right, selects below
        defstr = (f"muxdemux def={{Lh={rh}, Rh={lh}, NL=1, "
                  f"NR={data}, NB={sel}, NT=0, w={w}}}")
        pairs = ([("in", "lpin 1")]
                 + [(f"out{i}", f"rpin {i + 1}") for i in range(data)])
    pairs += [(f"sel{j}", f"bpin {j + 1}") for j in range(sel)]
    measured = render.measure_anchors(f"muxdemux, {defstr}", [a for _, a in pairs])
    return defstr, measured, [p for p in pairs if p[1] in measured]


def render_muxdemux(kind: str, entry: dict, origin, *, align: bool = True) -> tuple[dict, dict]:
    """Render every (data, select) combo of a mux/demux. Returns
    ``(geometry_by_key, data_entry)`` — geometry keyed ``kind:data:select`` (the
    default aliased under the plain key), and a multi-parameter data entry whose
    ``n_data[<data>,<select>]`` holds the baked option, per-axis scale, measured
    pins, and bbox. The uniform per-axis alignment (§4) lands the data pins on the
    grid; the slanted select pins stay off-grid (magnet)."""
    rec = entry["muxdemux"]
    role, dname, sname = rec["role"], rec["data_param"], rec["select_param"]
    specs = {s["name"]: s for s in entry["params"]}
    dspec, sspec = specs[dname], specs[sname]
    base = {k: v for k, v in entry.items() if k not in ("params", "muxdemux")}

    # The body is a *fixed* size (``_muxdemux_combo``). To keep the *display* size
    # constant too, bake the **default combo's** alignment scale for every combo —
    # a per-combo scale would re-grow the body as the pin count changes. A default
    # placement is unchanged; other counts pack their pins closer (connected via the
    # magnet) and the user resizes the instance with the 2D drag handles.
    # ``align=False`` (Option A) bakes no grid scale: the trapezoid renders at true
    # size with natural (off-grid) pins reached by the magnet.
    _dd, _sd = int(dspec["default"]), int(sspec["default"])
    _opt0, _m0, _pairs0 = _muxdemux_combo(role, _dd, _sd)
    sx, sy = _scale_for(_m0, [a for _, a in _pairs0]) if align else (1.0, 1.0)

    geoms: dict[str, dict] = {}
    n_data: dict[str, dict] = {}
    for d in range(int(dspec["min"]), int(dspec["max"]) + 1):
        for s in range(int(sspec["min"]), int(sspec["max"]) + 1):
            opt, measured, pairs = _muxdemux_combo(role, d, s)
            pins = _scaled_pins([{"name": nm, "anchor": a} for nm, a in pairs],
                                measured, sx, sy)
            scale = [sx, sy] if align else None
            ce = {**base, "tikz": "muxdemux", "emission": "node", "pins": pins}
            nd = {"option": opt, "pins": pins}
            if scale:
                ce["scale"] = scale
                nd["scale"] = scale
            g = geometry(ce, option=", " + opt)
            geoms[f"{geometry_key(kind)}:{d}:{s}"] = g
            nd["bbox"] = compute_bbox(g, origin, pins)
            n_data[f"{d},{s}"] = nd
    dd, sd = int(dspec["default"]), int(sspec["default"])
    geoms[geometry_key(kind)] = geoms[f"{geometry_key(kind)}:{dd}:{sd}"]   # static alias
    default = n_data[f"{dd},{sd}"]
    de = {
        "display_name": entry["display_name"], "category": entry["category"],
        "emission": "node", "tikz": "muxdemux", "labels": [],
        "pins": default["pins"], "bbox": default["bbox"],
        # The authoring rec is persisted so the batch generator (render_store)
        # can re-render the combos from definitions.json alone.
        "params": entry["params"], "muxdemux": rec, "n_data": n_data,
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
    write_store(geometry, components, origin,
                circuitikz_version=data.get("circuitikz_version"))


# ---------------------------------------------------------------------------
# Authored-entry validation (pre-flight for the batch generator)
# ---------------------------------------------------------------------------

EMISSIONS = ("path", "node")
PIN_GRID = 0.25


def _on_grid(v: float) -> bool:
    n = v / PIN_GRID
    return abs(n - round(n)) < 1e-6


def validate_entry(kind: str, entry: dict) -> list[str]:
    """Human-readable problems with an authored entry (empty == well-formed).

    Only a **path** bipole's two axial terminals are authored grid positions —
    they define the device span. Every node pin's offset is *derived* by
    `best_alignment` (measured, and legitimately off-grid for the magnet), so the
    grid check does not apply to it; a ``muxdemux`` entry carries no authored
    pins at all (every combo is measured), and a bipole's pins beyond the first
    two are off-axis taps (thyristor/triac gate).
    """
    errs: list[str] = []
    if not kind.strip():
        errs.append("Kind is required.")
    if not entry.get("tikz", "").strip():
        errs.append("CircuiTikZ keyword is required.")
    if entry.get("emission") not in EMISSIONS:
        errs.append(f"Emission must be one of {', '.join(EMISSIONS)}.")
    if "muxdemux" in entry:
        return errs

    pins = entry.get("pins", [])
    if not pins:
        errs.append("At least one pin is required.")
    names = [p["name"] for p in pins]
    if len(set(names)) != len(names):
        errs.append("Pin names must be unique.")
    is_path = entry.get("emission") == "path"
    for i, p in enumerate(pins):
        if not p["name"].strip():
            errs.append("Every pin needs a name.")
        if is_path and i < 2:        # the two axial terminals = the authored span
            ox, oy = p["offset"]
            if not (_on_grid(ox) and _on_grid(oy)):
                errs.append(f"Pin {p['name']!r} offset {(ox, oy)} is not on the 0.25 GU grid.")

    bbox = entry.get("bbox")
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        errs.append("bbox must be four numbers (x0, y0, x1, y1).")

    if library.is_multi_terminal_entry(entry):
        for p in pins:
            if not (p.get("anchor") or "").strip():
                errs.append(f"Multi-terminal pin {p['name']!r} needs a CircuiTikZ anchor.")
    return errs
