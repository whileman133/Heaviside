#!/usr/bin/env python3
r"""Generate the Heaviside component library from the CircuiTikZ manual.

This is the sole, fully *regenerable* component library: it covers the components
the CircuiTikZ manual documents by combining the manual scrape (:mod:`scrape_manual`
— categories, descriptions) with **render probes** of the actual shapes for the
geometry that matters (pins/anchors), via the shared render/measure pipeline in
``app.components.generate``.

Pins use CircuiTikZ anchor names verbatim. Terminals are discovered by *probing the
engine*, not by the manual's anchor lists — the manual under-documents anchors (e.g.
only the representative logic gate lists its inputs), so a manual-only pass would
miss most pins. For each component we:

* path bipole → axial ``in``/``out`` at (0,0)/(span,0) plus any off-axis terminals
  (``wiper``/``gate``/…) measured from the shape, connected by coordinate;
* node with ≥1 terminal → a centre-placed multi-terminal node: probe its real
  anchors, dedupe by position, scale onto the grid (``_scale_for``/``_grid_offset``),
  pins carry their anchor name (named-anchor connection);
* node with no terminals → a single-point node (ground / supply rail).

Output is written to ``components/generated/{definitions,geometry}.json`` — the
files the running app loads at startup (see ``app.resources``).

    python components/generate_library.py             # generate (slow: renders each)
    python components/generate_library.py --summary    # scope counts only
    python components/generate_library.py --only npn,pR # generate a subset (debug)

Requires the LaTeX toolchain (it renders every component).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))   # sibling scrapers

import scrape_manual as sm                               # noqa: E402
import extract_doc_anchors as eda                        # noqa: E402
from app.components import generate as gen               # noqa: E402
from app.components import library as lib                # noqa: E402
from app.components import render                        # noqa: E402

OUT_DIR = ROOT / "components" / "generated"

# Design note — the manual library bakes **no** grid-alignment scale. Every component
# is rendered at its true CircuiTikZ size and keeps its natural pin positions (mostly
# off the 0.25-GU grid); wires reach them via the canvas magnet / off-grid snap lines
# rather than the symbol being stretched to land pins on the grid. ``_grid_offset``
# still cleans a pin that *happens* to fall on the grid, but nothing is forced. (The
# curated library still grid-aligns, via the shared ``generate.best_alignment``.)

# Quick-key label slots a path bipole accepts. (A later phase can probe acceptance;
# some shapes reject ``l=``.)
_BIPOLE_LABELS = ["l", "l_", "v", "v^", "i", "i_"]

# A path bipole's extra terminal counts as off-axis (a real extra pin like a gate or
# wiper) when its anchor sits more than this far off the device axis (GU); on-axis
# anchors (anode/cathode) coincide with the in/out endpoints and are dropped.
_AXIS_TOL = 0.05

# Manual entries to skip: internal shape duplicates (``…shape``), the manual's
# example-only custom flip-flops (not real components), and ``demux`` — a
# demultiplexer is just a **mirrored** multiplexer, so the single parametric
# ``muxdemux`` element covers it (avoids a redundant palette entry).
_EXCLUDE = {"iecconnshape", "flipflop AB", "flipflop myJK", "demux"}

# Short, palette-friendly names for the manual's (long) subsection categories.
_CATEGORY_MAP = {
    "Grounds and supply voltages": "Grounds",
    "Resistive bipoles": "Resistors",
    "Capacitors and inductors: dynamical bipoles": "Cap/Ind",
    "Diodes and such": "Diodes",
    "Sources and generators": "Sources",
    "Instruments": "Instruments",
    "Mechanical Analogy": "Mechanical",
    "Miscellaneous bipoles": "Misc",
    "Multiple wires (buses)": "Buses",
    "Crossings": "Crossings",
    "Arrows": "Arrows",
    "Terminal shapes": "Terminals",
    "Connectors": "Connectors",
    "Block diagram components": "Blocks",
    "Transistors": "Transistors",
    "Electronic Tubes": "Tubes",
    "Amplifiers": "Amplifiers",
    "Logic gates": "Logic",
    "Flip-flops": "Flip-flops",
    "Multiplexer and de-multiplexer": "Mux/Demux",
    "RF components": "RF",
    "Switches, buttons and jumpers": "Switches",
    "Double bipoles (transformers)": "Transformers",
    "Chips (integrated circuits)": "Chips",
    "Electro-Mechanical Devices": "Electromech",
    "Seven segment displays": "Displays",
    "Path-style components": "Misc",
    "Node-style components": "Misc",
}


def _category(c: dict) -> str:
    """Short palette category name for a scraped component (the manual subsection
    titles are too long for the palette's category buttons)."""
    return _CATEGORY_MAP.get(c["category"], c["category"])

# Anchor names that are not *connection points* but where a label/decoration is
# drawn: the node-text positions (``label``/``text``, already measured separately into
# ``text_anchor``) and the decoration positions (``tip``/``arrows``). Never exposed.
_NON_TERMINAL_EXACT = {"label", "text", "tip", "arrows"}


def _is_doc_anchor(name: str) -> bool:
    """Whether a **manual-documented** anchor should be exposed as a wireable pin.
    Permissive — keeps everything the manual lists (electrical terminals, body-border
    anchors, polarity-dot marks, the geometric anchors ``north``/``center``/``left``/…,
    internal centres and body-diode/circle taps, option-state ``no…`` anchors) except
    the label/decoration draw-positions above and the non-referenceable
    ``<anchor>.<direction>`` compass sub-anchors (``out.n``; genuine internal sub-nodes
    like ``L1.midtap`` come through :func:`_extra_subnode_anchors`). Coincident anchors
    collapse later via :func:`_dedupe_by_position`."""
    return name not in _NON_TERMINAL_EXACT and "." not in name


def _is_terminal(name: str) -> bool:
    """Whether a **render-probed** anchor is a terminal. Stricter than
    :func:`_is_doc_anchor`: the engine reports *every* anchor a shape defines —
    including the generic geographical set every node carries — so for the probe
    fallback (shapes the manual under-documents) we additionally drop the geographical
    anchors (`eda._is_geo`). Geometric anchors reach the library only when the manual
    **explicitly** documents them (the :func:`_is_doc_anchor` path), never as probe
    noise on every shape."""
    return _is_doc_anchor(name) and not eda._is_geo(name)


def _strip_border_anchors(names: list[str], present: set[str]) -> list[str]:
    """Drop a chip/flip-flop shape's *border* anchors — the inner ``b``-prefixed
    points where a numbered pin meets the body (``bpin 1``/``blpin 1``/``bbpin 1``/…,
    and the clock-edge markers ``bup``/``bdown``) — when the real pin is also present.

    For muxdemux-style shapes ``bpin N`` is the *bottom* pin (a genuine terminal with
    no ``pin N`` counterpart) and is kept; the de-``b`` lookup distinguishes the two."""
    out = []
    for n in names:
        if n in ("bup", "bdown"):
            continue
        if n.startswith("b") and n[1:] in present:   # border of a present pin
            continue
        out.append(n)
    return out


def _measure_subnode(keyword: str, sub: str, anchor: str) -> tuple[float, float] | None:
    """Measure a composite shape's sub-node anchor (e.g. a double-bipole's
    ``L.south``) in GU, y-down."""
    body = rf"\node[{keyword}] (X) at (0,0) {{}};"
    _svg, log = render.render_svg(body, border_pt=10, node_id=f"X-{sub}", anchors=[anchor])
    for n, xs, ys in render._ANCHOR_RE.findall(log):
        if n == anchor:
            return (round(float(xs) / render.TEXPT_PER_GU, 4),
                    round(-float(ys) / render.TEXPT_PER_GU, 4))
    return None


def _measure_bipole(keyword: str, names: list[str]) -> dict[str, tuple[float, float]]:
    """Measure named anchors of a path bipole drawn ``(0,0)→(2,0)`` (GU, y-down)."""
    body = rf"\draw (0,0) to[{keyword}, name=B] (2,0);"
    _svg, log = render.render_svg(body, border_pt=10, node_id="B", anchors=names)
    out: dict[str, tuple[float, float]] = {}
    for n, xs, ys in render._ANCHOR_RE.findall(log):
        out[n] = (round(float(xs) / render.TEXPT_PER_GU, 4),
                  round(-float(ys) / render.TEXPT_PER_GU, 4))
    return out


def _dedupe_by_position(names: list[str],
                        measured: dict[str, tuple]) -> list[str]:
    """Keep one name per distinct measured position (first in *names* order) — so a
    shape's aliases (``B``/``base``, ``G``/``gate``) collapse to a single pin."""
    seen: set[tuple[float, float]] = set()
    out: list[str] = []
    for n in names:
        p = measured.get(n)
        if p is None:
            continue
        key = (round(p[0], 3), round(p[1], 3))
        if key not in seen:
            seen.add(key)
            out.append(n)
    return out


def _bipole_extras(c: dict, pool: list[str], conditional: set[str]) -> list[tuple[str, tuple]]:
    """Extra terminals of a path bipole beyond its two axial endpoints, as
    ``[(name, offset)]``. Candidates are the *base* (option-independent) documented
    anchors, or — when it documents none (e.g. the ``empty``/``full`` fill-variants
    inherit the base's gate) — a render probe with the option-*conditional* anchors
    filtered out. Every documented anchor that is **not** at one of the two endpoints
    (``in`` = (0,0), ``out`` = (2,0)) is an extra terminal: an off-axis ``gate``/
    ``wiper``, an on-axis ``midtap``, or a geometric anchor (``left``/``right``/
    ``center`` = the body edges/centre). Anchors coincident with an endpoint
    (``anode``/``cathode``) fold into in/out; further coincidences collapse by
    position (so a fallback-to-centre name doesn't add a duplicate)."""
    candidates = [a for a in c["base_anchors"] if _is_doc_anchor(a)]
    if not candidates:
        candidates = [a for a in (eda.probe_anchors(c["keyword"], "path", pool) or [])
                      if _is_terminal(a) and a not in conditional]
    if not candidates:
        return []
    measured = _measure_bipole(c["keyword"], candidates)
    seen: set[tuple[float, float]] = {(0.0, 0.0), (2.0, 0.0)}   # the two endpoints
    extras: list[tuple[str, tuple]] = []
    for n in candidates:
        p = measured.get(n)
        if p is None:
            continue
        key = (round(p[0], 3), round(p[1], 3))
        if key in seen:
            continue
        seen.add(key)
        extras.append((n, p))
    return extras


def _bipole_entry(c: dict, pool: list[str], conditional: set[str]) -> dict:
    """Path bipole: axial in/out + any off-axis extra terminals (see
    :func:`_bipole_extras`), coordinate-connected."""
    pins = [{"name": "in", "offset": [0.0, 0.0], "anchor": None},
            {"name": "out", "offset": [2.0, 0.0], "anchor": None}]
    for n, (x, y) in _bipole_extras(c, pool, conditional):
        pins.append({"name": n, "offset": [x, y], "anchor": None})
    return {
        "display_name": sm._demacro(c["description"]) or c["keyword"],
        "category": _category(c), "emission": "path", "tikz": c["keyword"],
        "labels": list(_BIPOLE_LABELS), "pins": pins,
    }


def _node_terminals(c: dict, pool: list[str], conditional: set[str]) -> list[str]:
    """Terminal anchor names for a node. Exposes **every documented anchor** the
    manual lists for the shape (electrical terminals, body-border anchors, the
    geometric ``center``/``left``/… — see :func:`_is_doc_anchor`), and unions in the
    engine's real terminals when the manual's list lacks them (the manual under-
    documents some nodes — gates inherit their inputs, ``pnp`` documents anchors only
    on its body-diode *variant*, so its base ``B``/``C``/``E`` come from the probe).
    The probe is geographic-/border-filtered (:func:`_is_terminal`,
    :func:`_strip_border_anchors`) so it adds only real terminals, never the engine's
    generic compass set. Coincident anchors collapse via :func:`_dedupe_by_position`."""
    documented = [a for a in c["base_anchors"] if _is_doc_anchor(a)]
    # A node whose documented anchors are **all geographic** has no real terminal — it
    # is a single-point symbol (ground/supply/terminal-marker). Keep it single-point
    # rather than promote it to a multi-terminal node: that would break its standalone
    # ``\node[…] at`` emission (node-side placement, node text — §7) and scatter a
    # compass rose of pins. (Geometric anchors are additive only on a node that ALSO
    # carries a real terminal, e.g. a BNC's left/right/center beside hot/zero/shield.)
    if documented and all(eda._is_geo(a) for a in documented):
        documented = []
    # When two anchors coincide they collapse to the first by :func:`_dedupe_by_position`,
    # so order **named terminals before geometric** ones: a 7-seg's middle segment ``g``
    # (at the body centre) then wins over ``center``, a BNC's ``hot`` over a coincident
    # geo anchor, etc. (stable — relative order within each group is preserved).
    def _terminals_first(anchors: list[str]) -> list[str]:
        return sorted(anchors, key=eda._is_geo)

    # Trust the manual's list as-is when it names at least one *base* (option-
    # independent) terminal. Otherwise — no documented anchors, or they are all option-
    # conditional (``pnp`` lists only its body-diode anchors) — probe the engine and
    # union, so the base terminals (B/C/E) are not lost behind a variant-only doc list.
    if documented and not all(a in conditional for a in documented):
        return _terminals_first(documented)
    probed = [a for a in (eda.probe_anchors(c["keyword"], "node", pool) or [])
              if _is_terminal(a) and a not in conditional and a not in ("a", "b")]
    probed = _strip_border_anchors(probed, set(probed))
    return _terminals_first(documented + [a for a in probed if a not in documented])


def _text_anchor(measured: dict, scale: tuple[float, float]) -> list[float] | None:
    """Where a node's inline ``{…}`` text sits, as ``(text − center)`` in GU, scaled
    by the node's baked scale — matching what the canvas needs to place node text
    (measured by the generator)."""
    if "text" not in measured or "center" not in measured:
        return None
    tx, ty = measured["text"]
    cx, cy = measured["center"]
    return [round((tx - cx) * scale[0], 4) + 0.0, round((ty - cy) * scale[1], 4) + 0.0]


def _variant_candidates(c: dict, pin_names: set[str]) -> list[str]:
    """Scraped options worth offering as boolean variants: drop the ``no…``
    negations (off-states) and any that double as a terminal name (``bulk``…)."""
    out: list[str] = []
    for o in c["options"]:
        if o.startswith("no") or o in pin_names or o in out:
            continue
        out.append(o)
    return out


def _extra_subnode_anchors(c: dict) -> list[str]:
    """Documented sub-node anchors the manual lists on a *constituent* shape but not
    on the composite's own anchor list. A transformer is two coupled inductors —
    sub-nodes ``L1`` (primary) and ``L2`` (secondary) — each of which inherits the
    inductor ``midtap`` centre tap (the manual documents ``midtap`` on ``L``). They
    are reached via the ``-L1.midtap`` / ``-L2.midtap`` sub-node refs. Self-validating:
    :func:`_measure_subnode` returns ``None`` for a kind without the sub-node, so a
    non-coil ``transformer``-named shape silently gains nothing."""
    if c["type"] == "node" and "transformer" in c["keyword"]:
        return ["L1.midtap", "L2.midtap"]
    return []


def _node_entry(c: dict, pool: list[str], conditional: set[str]) -> dict:
    """Node component: single-point (one origin pin) when it has no terminals, else a
    centre-placed multi-terminal node — pins at grid-scaled anchors (named-anchor
    connection), plus any documented sub-node terminals (``-L.south``). Carries the
    measured ``text_anchor`` and, for multi-terminal nodes, candidate ``option``
    variants (resolved/filtered at render time)."""
    base = {"display_name": sm._demacro(c["description"]) or c["keyword"],
            "category": _category(c), "emission": "node", "tikz": c["keyword"],
            "labels": []}
    terminals = _node_terminals(c, pool, conditional)
    if not terminals:                                   # single-point (ground / rail / marker)
        m = render.measure_anchors(c["keyword"], ["text", "center"])
        entry = {**base, "pins": [{"name": "in", "offset": [0.0, 0.0], "anchor": None}]}
        ta = _text_anchor(m, (1.0, 1.0))
        if ta:
            entry["text_anchor"] = ta
        return entry

    measured = render.measure_anchors(c["keyword"], terminals + ["text", "center"])
    canon = _dedupe_by_position(terminals, measured)
    pins = [{"name": a, "anchor": a,
             "offset": [gen._grid_offset(measured[a][0]),
                        gen._grid_offset(measured[a][1])]}
            for a in canon]
    # documented sub-node terminals (e.g. double-bipole L.south, transformer coil
    # centre taps L1.midtap/L2.midtap) → ``-L.south`` ref
    for sa in c["base_subnode_anchors"] + _extra_subnode_anchors(c):
        sub, _, anch = sa.partition(".")
        if not anch or not _is_doc_anchor(anch):
            continue
        m = _measure_subnode(c["keyword"], sub, anch)
        if m is not None:
            pins.append({"name": sa, "anchor": f"-{sub}.{anch}",
                         "offset": [gen._grid_offset(m[0]), gen._grid_offset(m[1])]})
    entry = {**base, "anchor_pin": None, "leads": [], "pins": pins}
    ta = _text_anchor(measured, (1.0, 1.0))
    if ta:
        entry["text_anchor"] = ta
    entry["_variant_candidates"] = _variant_candidates(c, {p["name"] for p in pins})
    return entry


# ---------------------------------------------------------------------------
# Parametric logic gates (``number inputs=N``). The input count isn't documented
# per-N, so we render the gate at each N and measure its pins from the engine. We
# skip the curated height pre-pass (inputs land off-grid, magnet-connected) — the
# runtime needs only ``{name, min, max, default, option, n_data}``.
# ---------------------------------------------------------------------------

_GATE_PARAM = "number inputs"
_GATE_MIN, _GATE_MAX, _GATE_DEFAULT = 2, 8, 2


def _measure_node_with_option(keyword: str, option: str,
                              candidates: list[str]) -> dict[str, tuple]:
    """Measure every *candidate* anchor that the shape actually defines under
    ``node[keyword, option]`` — one render, ``\\ifcsname``-guarded so undefined names
    are skipped (no centre-fallback). Returns ``{name: (gu_x, gu_y)}`` (y-down)."""
    safe = [a for a in candidates if eda._SAFE_NAME.match(a)]
    lines = [rf"\node[{keyword}, {option}] (X) at (0,0) {{}};", r"\makeatletter",
             r"\edef\hv@shape{\csname pgf@sh@ns@X\endcsname}"]
    lines += [rf"\ifcsname pgf@anchor@\hv@shape @{a}\endcsname"
              rf"\pgfpointanchor{{X}}{{{a}}}"
              rf"\typeout{{HVPT {a} = \the\pgf@x , \the\pgf@y}}\fi" for a in safe]
    lines.append(r"\makeatother")
    try:
        log = render.compile_log("\n".join(lines), border_pt=6, node_id="X", anchors=[])
    except render.RenderError:
        return {}
    out: dict[str, tuple] = {}
    for m in re.finditer(r"HVPT (.+?) = (-?[\d.]+)pt\s*,\s*(-?[\d.]+)pt", log):
        out[m.group(1).strip()] = (round(float(m.group(2)) / render.TEXPT_PER_GU, 4),
                                   round(-float(m.group(3)) / render.TEXPT_PER_GU, 4))
    return out


def _gate_size_keys(kw: str) -> dict | None:
    """If a logic-gate shape supports the CircuiTikZ body ``height``/``width`` ctikzset
    keys, return ``{"path": "tripoles/<kw>", "height": <default>, "width": <default>}``,
    else ``None``. The american and/or/nand/nor (and xor/xnor) families do; not/buffer
    do not (fixed size — they fall back to the node xscale/yscale transform). Probed
    from the live shape so the defaults track the installed CircuiTikZ."""
    path = f"tripoles/{kw}"
    out: dict[str, float] = {}
    for key in ("height", "width"):
        body = (r"\makeatletter"
                rf"\pgfkeysgetvalue{{/tikz/circuitikz/{path}/{key}}}{{\hv@v}}"
                rf"\typeout{{HVKEY {key} = \hv@v}}\makeatother"
                rf"\node[{kw}] (X) at (0,0){{}};")
        try:
            log = render.compile_log(body, border_pt=6, node_id="X", anchors=[])
        except render.RenderError:
            return None
        m = re.search(rf"HVKEY {key} = (.*)", log)
        if not m:
            return None
        try:
            out[key] = float(m.group(1).strip())   # ".8" → 0.8
        except ValueError:
            return None                            # \hv@v unexpanded → key undefined
    return {"path": path, **out}


def _gate_param_entry(c: dict, pool: list[str], conditional: set[str],
                      origin: tuple) -> tuple[dict, dict] | None:
    """A parametric logic gate: render N = min..max, measure pins per N, build the
    ``param`` block + per-N geometry. ``None`` if the default value fails."""
    kw = c["keyword"]
    # The gate shape defines two input families (``in N`` and ``bin N``); the
    # documented base anchors say which is canonical (``and port`` → ``in``,
    # ``or port`` → ``bin``). Under-documented gates (nand/nor/xnor) fall back to the
    # keyword (an or-family gate uses ``bin``).
    base = c["base_anchors"]
    if any(a.startswith("bin") for a in base):
        in_prefix = "bin"
    elif any(a.startswith("in") for a in base):
        in_prefix = "in"
    else:
        toks = kw.lower().split()
        gate_type = toks[-2] if len(toks) >= 2 and toks[-1] == "port" else ""
        in_prefix = "bin" if gate_type in {"or", "nor", "xor", "xnor"} else "in"
    out_name = "out" if in_prefix == "in" else "bout"
    # The complementary anchor family — the *other* of the lead-tip (``in``/``out``)
    # and body (``bin``/``bout``) sets — is exposed too, so the user can place
    # inversion bubbles at the gate body / lead tips; any that coincide with a primary
    # pin collapse out. Pins sit at their natural (mostly off-grid) positions.
    sec_in_prefix = "bin" if in_prefix == "in" else "in"
    sec_out = "bout" if out_name == "out" else "out"
    n_data: dict[str, dict] = {}
    geometry: dict[str, dict] = {}
    text_anchor = None
    for n in range(_GATE_MIN, _GATE_MAX + 1):
        want = [f"{in_prefix} {i}" for i in range(1, n + 1)] + [out_name, "text", "center"]
        sec_want = [f"{sec_in_prefix} {i}" for i in range(1, n + 1)] + [sec_out]
        measured = _measure_node_with_option(kw, f"{_GATE_PARAM}={n}", want + sec_want)
        canon = [a for a in want[:-2] if a in measured]   # inputs + output, in order
        if not canon:
            continue
        extra = [a for a in sec_want if a in measured]    # complementary (bubble) anchors
        exposed = _dedupe_by_position(canon + extra, measured)
        pins = [{"name": a, "anchor": a,
                 "offset": [gen._grid_offset(measured[a][0]),
                            gen._grid_offset(measured[a][1])]} for a in exposed]
        g = gen.geometry({"emission": "node", "tikz": kw, "pins": pins},
                         option=f", {_GATE_PARAM}={n}")
        geometry[gen.param_geometry_key(kw, n)] = g
        n_data[str(n)] = {"pins": [{"name": p["name"], "offset": p["offset"],
                                    "anchor": p["anchor"]} for p in pins],
                          "bbox": gen.compute_bbox(g, origin, pins)}
        if n == _GATE_DEFAULT:
            text_anchor = _text_anchor(measured, (1.0, 1.0))
    if str(_GATE_DEFAULT) not in n_data:
        return None
    nd = n_data[str(_GATE_DEFAULT)]
    geometry[lib.geometry_key(kw)] = geometry[gen.param_geometry_key(kw, _GATE_DEFAULT)]
    de = {"display_name": sm._demacro(c["description"]) or kw, "category": _category(c),
          "emission": "node", "tikz": kw, "labels": [],
          "pins": nd["pins"], "bbox": nd["bbox"],
          "param": {"name": "inputs", "min": _GATE_MIN, "max": _GATE_MAX,
                    "default": _GATE_DEFAULT, "option": _GATE_PARAM + "={n}",
                    "n_data": n_data}}
    if text_anchor:
        de["text_anchor"] = text_anchor
    size_keys = _gate_size_keys(kw)
    if size_keys:
        de["size_keys"] = size_keys                # sized via \ctikzset height/width
    return de, geometry


# --- multi-terminal BJTs (collectors / emitters) ---------------------------
# Two integer parameters → a multi-parameter kind: n_data keyed "c,e", each baking
# its own option string (like mux/demux). Pins are B + per-collector C1..Cc +
# per-emitter E1..Ee (the manual's documented per-terminal anchors).
_BJT_KINDS = frozenset({"bjtnpn", "bjtpnp"})
_BJT_MIN, _BJT_MAX, _BJT_DEFAULT = 1, 4, 1


def _bjt_param_entry(c: dict, origin: tuple) -> tuple[dict, dict] | None:
    kw = c["keyword"]
    n_data: dict[str, dict] = {}
    geometry: dict[str, dict] = {}
    text_anchor = None
    for nc in range(_BJT_MIN, _BJT_MAX + 1):
        for ne in range(_BJT_MIN, _BJT_MAX + 1):
            opt = f"collectors={nc}, emitters={ne}"
            # Always expose the **primary** collector/emitter terminals — the lead
            # tips ``C``/``E`` (CircuiTikZ's canonical single-BJT anchors, matching
            # the curated npn/pnp) — plus the numbered branch terminals ``C1…``/``E1…``
            # only when more than one collector/emitter is present.
            want = ["B", "C", "E"]
            if nc > 1:
                want += [f"C{i}" for i in range(1, nc + 1)]
            if ne > 1:
                want += [f"E{i}" for i in range(1, ne + 1)]
            measured = _measure_node_with_option(kw, opt, want + ["text", "center"])
            canon = [a for a in want if a in measured]
            if not canon:
                continue
            exposed = _dedupe_by_position(canon, measured)
            pins = [{"name": a, "anchor": a,
                     "offset": [gen._grid_offset(measured[a][0]),
                                gen._grid_offset(measured[a][1])]} for a in exposed]
            g = gen.geometry({"emission": "node", "tikz": kw, "pins": pins},
                             option=f", {opt}")
            geometry[f"{lib.geometry_key(kw)}:{nc}:{ne}"] = g
            n_data[f"{nc},{ne}"] = {
                "option": opt,
                "pins": [{"name": p["name"], "offset": p["offset"],
                          "anchor": p["anchor"]} for p in pins],
                "bbox": gen.compute_bbox(g, origin, pins)}
            if nc == _BJT_DEFAULT and ne == _BJT_DEFAULT:
                text_anchor = _text_anchor(measured, (1.0, 1.0))
    key = f"{_BJT_DEFAULT},{_BJT_DEFAULT}"
    if key not in n_data:
        return None
    nd = n_data[key]
    geometry[lib.geometry_key(kw)] = geometry[f"{lib.geometry_key(kw)}:{_BJT_DEFAULT}:{_BJT_DEFAULT}"]
    de = {"display_name": sm._demacro(c["description"]) or kw, "category": _category(c),
          "emission": "node", "tikz": kw, "labels": [],
          "pins": nd["pins"], "bbox": nd["bbox"],
          "params": [{"name": "collectors", "min": _BJT_MIN, "max": _BJT_MAX,
                      "default": _BJT_DEFAULT},
                     {"name": "emitters", "min": _BJT_MIN, "max": _BJT_MAX,
                      "default": _BJT_DEFAULT}],
          "n_data": n_data}
    if text_anchor:
        de["text_anchor"] = text_anchor
    return de, geometry


def _muxdemux_entry(c: dict, origin: tuple) -> tuple[dict, dict] | None:
    """The manual's generic ``muxdemux`` as a parametric **mux** (data inputs left,
    one output right, select lines below) via the shared :func:`gen.render_muxdemux`.
    (CircuiTikZ's muxdemux is one generic shape configured by a ``muxdemux def``
    string; we pick the canonical mux role — rotate/mirror for demux usage.)"""
    entry = {
        "display_name": sm._demacro(c["description"]) or "Multiplexer",
        "category": _category(c), "emission": "node", "tikz": "muxdemux", "labels": [],
        "params": [{"name": "inputs", "min": 2, "max": 8, "default": 2},
                   {"name": "selects", "min": 1, "max": 4, "default": 1}],
        "muxdemux": {"role": "mux", "data_param": "inputs", "select_param": "selects"},
    }
    try:
        geoms, de = gen.render_muxdemux("muxdemux", entry, origin, align=False)
    except render.RenderError:
        return None
    return de, geoms   # our worker expects (data_entry, geometry)


# --- IC chips (num pins) ---------------------------------------------------
# A single integer parameter (pin count): pins are pin 1..pin N (the external pads;
# the inner ``bpin N`` body anchors are not exposed). A DIP only has *even* counts (two
# rows) and a QFP only *multiples of four* (four sides) — CircuiTikZ ignores other
# values — so we step the parameter and generate only valid counts. (default, min,
# max, step).
_CHIP_PARAMS = {"dipchip": (8, 4, 28, 2), "qfpchip": (16, 4, 28, 4)}


def _chip_param_entry(c: dict, origin: tuple) -> tuple[dict, dict] | None:
    kw = c["keyword"]
    default, mn, mx, step = _CHIP_PARAMS[kw]
    n_data: dict[str, dict] = {}
    geometry: dict[str, dict] = {}
    text_anchor = None
    for n in range(mn, mx + 1, step):
        want = [f"pin {i}" for i in range(1, n + 1)]
        measured = _measure_node_with_option(kw, f"num pins={n}", want + ["text", "center"])
        canon = [a for a in want if a in measured]
        if not canon:
            continue
        pins = [{"name": a, "anchor": a,
                 "offset": [gen._grid_offset(measured[a][0]),
                            gen._grid_offset(measured[a][1])]} for a in canon]
        g = gen.geometry({"emission": "node", "tikz": kw, "pins": pins},
                         option=f", num pins={n}")
        geometry[gen.param_geometry_key(kw, n)] = g
        n_data[str(n)] = {"pins": [{"name": p["name"], "offset": p["offset"],
                                    "anchor": p["anchor"]} for p in pins],
                          "bbox": gen.compute_bbox(g, origin, pins)}
        if n == default:
            text_anchor = _text_anchor(measured, (1.0, 1.0))
    if str(default) not in n_data:
        return None
    nd = n_data[str(default)]
    geometry[lib.geometry_key(kw)] = geometry[gen.param_geometry_key(kw, default)]
    de = {"display_name": sm._demacro(c["description"]) or kw, "category": _category(c),
          "emission": "node", "tikz": kw, "labels": [],
          "pins": nd["pins"], "bbox": nd["bbox"],
          "param": {"name": "pins", "min": mn, "max": mx, "step": step,
                    "default": default, "option": "num pins={n}", "n_data": n_data}}
    if text_anchor:
        de["text_anchor"] = text_anchor
    return de, geometry


# --- document symbol style (american/european/cute) ------------------------
# CircuiTikZ styles whole families document-wide, so rather than separate components
# per style we render each style-eligible component under its axis's ``\ctikzset`` and,
# where the geometry changes, store it under ``geometry_key(kind):<value>`` and tag the
# component with its axis. The canvas/codegen then switch all of a family at once from a
# document setting (``library.STYLE_AXES``). Only these categories are probed (cheap),
# and a component that doesn't actually respond stays untagged.
_STYLE_AXIS_BY_CATEGORY = {
    "Resistors": "resistors", "Cap/Ind": "inductors", "Transformers": "inductors",
}


def _style_geometry(kind: str, entry: dict,
                    base_geom: dict) -> tuple[str | None, dict[str, dict]]:
    """Detect whether *entry* responds to its category's style axis. Returns
    ``(axis | None, {styled_key: geom})`` — geometry for each non-default style value
    whose render differs from the unstyled (american) base."""
    axis = _STYLE_AXIS_BY_CATEGORY.get(entry.get("category"))
    if not axis:
        return None, {}
    gkey = lib.geometry_key(kind)
    base_cs = list(entry.get("ctikzset", []))
    styled: dict[str, dict] = {}
    for value, opts in lib.STYLE_AXES[axis].items():
        if not opts:                       # the default (american) — that's base_geom
            continue
        try:
            g = gen.geometry({**entry, "ctikzset": base_cs + opts})
        except render.RenderError:
            continue
        if g != base_geom:
            styled[f"{gkey}:{value}"] = g
    return (axis if styled else None), styled


def _render_entry(kind: str, entry: dict, origin: tuple) -> tuple[dict, dict] | None:
    """Render *entry*'s base + variant geometry and build its data record, or
    ``None`` if the base fails to compile.

    Variant candidates (``entry['_variant_candidates']``) are each rendered with the
    option applied; one that fails to compile or doesn't change the ink is dropped,
    so only genuine option variants survive. Variant geometry is stored under
    ``geometry_key(kind) + "_<token>"`` — the key the runtime
    (``library.variant_geometry_suffix``) looks up — not ``generate.variant_key``,
    which mis-handles space-containing kinds."""
    try:
        base_geom = gen.geometry(entry)
    except render.RenderError:
        return None
    gkey = lib.geometry_key(kind)
    geometry = {gkey: base_geom}
    variants: list[dict] = []
    variant_geoms: list[dict] = []
    for opt in entry.pop("_variant_candidates", []):
        name = opt.replace(" ", "_")
        if any(v["name"] == name for v in variants):
            continue
        try:
            vgeom = gen.geometry(entry, option=f", {opt}")
        except render.RenderError:
            continue
        if vgeom == base_geom:                          # option had no visible effect
            continue
        variants.append({"name": name, "token": opt, "mode": "option"})
        # Store under the *sanitized* key the runtime looks up (it maps the
        # variant suffix through ``geometry_key``, spaces → underscores), so a
        # space-token variant (``tr circle``) resolves on the canvas.
        geometry[lib.geometry_key(f"{gkey}_{opt}")] = vgeom
        variant_geoms.append(vgeom)
    if variants:
        entry["variants"] = variants
    # Document-style geometry (american base + european/cute deltas for the affected
    # families). The terminals don't move with style, so the base pins/scale carry over.
    style_axis, style_geoms = _style_geometry(kind, entry, base_geom)
    geometry.update(style_geoms)
    de = gen.data_entry(kind, entry)
    if style_axis:
        de["style_axis"] = style_axis
    # The bbox must cover the base *and* every variant/style — a variant (a body diode)
    # or a style (cute coils) often extends past the base, and the item's boundingRect
    # is this static bbox, so a too-small one clips/hides it on the canvas.
    extra = (*variant_geoms, *style_geoms.values())
    merged = {"paths": [p for gm in (base_geom, *extra) for p in gm.get("paths", [])],
              "glyphs": [g for gm in (base_geom, *extra) for g in gm.get("glyphs", [])]}
    de["bbox"] = gen.compute_bbox(merged, origin, entry["pins"])
    if entry.get("text_anchor"):
        de["text_anchor"] = entry["text_anchor"]
    return de, geometry


def build(components: list[dict], pool: list[str], conditional: set[str], *,
          workers: int = 12, log=lambda m: None) -> tuple[dict, dict, tuple]:
    """Author + render every in-scope component (parallel). *conditional* is the set
    of option-dependent anchor names to keep out of probe fallbacks. Returns
    ``(geometry, components_data, origin)``."""
    origin = gen.measure_origin(
        {"emission": "path", "tikz": "R", "pins":
            [{"name": "in", "offset": [0.0, 0.0]}, {"name": "out", "offset": [2.0, 0.0]}]})

    def work(c: dict):
        kw = c["keyword"]
        # Parametric multi-terminal kinds take their own (multi-render) paths.
        if c["type"] == "node" and kw in _BJT_KINDS:
            try:
                return kw, _bjt_param_entry(c, origin)
            except render.RenderError:
                return kw, None
        if c["type"] == "node" and kw == "muxdemux":
            return kw, _muxdemux_entry(c, origin)
        if c["type"] == "node" and kw in _CHIP_PARAMS:
            try:
                return kw, _chip_param_entry(c, origin)
            except render.RenderError:
                return kw, None
        if c["type"] == "node" and _GATE_PARAM in c["parameters"]:
            try:
                return c["keyword"], _gate_param_entry(c, pool, conditional, origin)
            except render.RenderError:
                return c["keyword"], None
        try:
            entry = (_bipole_entry(c, pool, conditional) if c["type"] == "path"
                     else _node_entry(c, pool, conditional))
        except render.RenderError:
            return c["keyword"], None
        return c["keyword"], _render_entry(c["keyword"], entry, origin)

    geometry: dict[str, dict] = {}
    data: dict[str, dict] = {}
    skipped: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, c): c for c in components}
        for fut in as_completed(futures):
            kw, result = fut.result()
            done += 1
            if result is None:
                skipped.append(kw)
            else:
                de, mes = result
                data[kw] = de
                geometry.update(mes)
            if done % 25 == 0 or done == len(components):
                log(f"  {done}/{len(components)} ({len(skipped)} skipped)")
    if skipped:
        log(f"  skipped (failed to compile): {', '.join(sorted(skipped))}")
    # Emit in scrape (manual) order — parallel completion order is arbitrary, and the
    # palette groups categories by first appearance, so this keeps both the category
    # order and the within-category order matching the manual.
    data = {c["keyword"]: data[c["keyword"]] for c in components if c["keyword"] in data}
    # Geometry keys are likewise inserted in arbitrary thread-completion order; sort
    # them so a regeneration is **byte-stable** (a component's base/param/variant keys
    # share a prefix, so sorting also groups them). Order is irrelevant at runtime
    # (lookup by key); determinism keeps regen diffs clean.
    geometry = dict(sorted(geometry.items()))
    return geometry, data, origin


def _circuitikz_version() -> str | None:
    try:
        _svg, logtext = render.render_svg(r"\draw (0,0) to[R] (2,0);", border_pt=2)
        return render.circuitikz_version(logtext)
    except render.RenderError:
        return None


def write_generated(geometry: dict, components: dict, origin: tuple,
                    version: str | None) -> None:
    """Write the generated library in the same on-disk format as the curated files."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "geometry.json").write_text(
        json.dumps(geometry, indent=2) + "\n", encoding="utf-8")
    data: dict = {"origin_svg": list(origin)}
    if version:
        data["circuitikz_version"] = version
    data["components"] = components
    (OUT_DIR / "definitions.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate the manual component library.")
    ap.add_argument("--summary", action="store_true",
                    help="report scope counts and exit (do not render or write)")
    ap.add_argument("--only", help="comma-separated keywords to generate (debug)")
    ap.add_argument("--manual", type=Path, default=None,
                    help="path to circuitikzmanual.tex (default: locate via kpsewhich)")
    args = ap.parse_args()

    manual_path = args.manual or eda._find_manual()
    if not manual_path or not manual_path.is_file():
        print("could not locate circuitikzmanual.tex (pass --manual PATH)", file=sys.stderr)
        return 1
    manual_text = manual_path.read_text(encoding="utf-8", errors="replace")
    db = sm.scrape(manual_text)
    # The probe pool and the option-conditional set drive only the *probe fallback*
    # (for shapes the manual under-documents). They are computed from the **original
    # scrape**, BEFORE the doc-anchor merge below — otherwise an anchor documented for
    # one shape (the 7-seg ``dot``) would enter the shared pool and probe-resolve on an
    # unrelated shape (a flip-flop's bubble ``dot``), leaking a spurious pin.
    pool = sm._anchor_pool(db)
    # The manual documents only the first few numbered IC pins; extend the pool so
    # multi-pin shapes (flip-flops, chips, mux-family) surface ALL their pins.
    pool = list(dict.fromkeys(
        pool + [f"{p} {i}" for p in ("pin", "lpin", "rpin", "tpin", "bpin")
                for i in range(1, 17)]))
    # Option-conditional anchors: documented only on option-decorated entries, never
    # on any base shape — kept out of probe fallbacks so they don't become floating
    # pins (e.g. a transistor's body-diode anchors, a tube's filament).
    base_all = {a for c in db["components"].values() for a in c["base_anchors"]}
    conditional = {a for c in db["components"].values() for a in c["anchors"]
                   if a not in base_all}
    # The manual scrape under-reports anchors for some shapes (flip-flops, the
    # seven-segment display, …). ``extract_doc_anchors.extract`` parses the manual's
    # ``\anchor`` macros more completely, so merge its per-keyword list into each
    # shape's documented ``base_anchors`` — the source of truth for "every anchor the
    # manual mentions". ``keep_geo=True`` keeps the **geometric** anchors too
    # (``north``/``center``/``left``/…): the policy is to surface every documented
    # anchor (§5.4), since CircuiTikZ documents them because they're useful to wire to
    # or align with. A shape with documented anchors takes the verbatim documented
    # path (no probe); the terminal filter (:func:`_is_terminal`) drops only the
    # label/decoration draw-positions and the non-referenceable ``.``-compound names.
    doc_anchors = eda.extract(manual_text, keep_geo=True)
    for kw, c in db["components"].items():
        seen = set(c["base_anchors"])
        c["base_anchors"] += [a for a in doc_anchors.get(kw, []) if a not in seen]

    # Order by the manual's source position so each category lists its components in
    # the manual's own sequence (the scrape's node-pass-then-path-pass otherwise
    # interleaves them); the palette groups by category, preserving this order.
    comps = [c for c in db["components"].values() if c["keyword"] not in _EXCLUDE]
    comps.sort(key=lambda c: c.get("pos", 0))
    if args.only:
        want = {k.strip() for k in args.only.split(",")}
        comps = [c for c in comps if c["keyword"] in want]
    paths = sum(1 for c in comps if c["type"] == "path")
    print(f"In scope: {len(comps)} components ({paths} path, {len(comps) - paths} node).",
          file=sys.stderr)
    if args.summary:
        return 0

    print("Rendering (slow)…", file=sys.stderr)
    geometry, components, origin = build(comps, pool, conditional,
                                         log=lambda m: print(m, file=sys.stderr))
    write_generated(geometry, components, origin, _circuitikz_version())
    print(f"Wrote {len(components)} components to {OUT_DIR}/.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
