"""
Invariants for the manual-scraped component library (``components/generated/``).

The manual library is opt-in (``HEAVISIDE_COMPONENT_LIB=manual``) and regenerated
from the CircuiTikZ manual, so these read the *generated* data files directly
(no Qt, no env switch) and guard the policy choices that aren't otherwise covered
by the curated-library suite:

* documented body anchors are exposed (gate ``bin``/``bout`` for inversion bubbles,
  flip-flop border anchors) — but chips' probed border anchors stay stripped;
* a demultiplexer is a mirrored multiplexer, so only the ``muxdemux`` element
  exists (no separate ``demux``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DEFS = Path(__file__).resolve().parent.parent / "components" / "generated" / "definitions.json"

if not _DEFS.is_file():  # pragma: no cover - manual library not generated
    pytest.skip("manual library not generated", allow_module_level=True)

_COMPONENTS = json.loads(_DEFS.read_text(encoding="utf-8"))["components"]
_GEOM = _DEFS.parent / "geometry.json"


def _pin_names(kind: str) -> list[str]:
    return [p["name"] for p in _COMPONENTS[kind]["pins"]]


def test_geometry_keys_are_sorted_for_stable_regen():
    """``geometry.json`` keys are emitted sorted, so re-running the generator (which
    builds geometry in arbitrary parallel-completion order) produces a byte-stable
    file — a regeneration only diffs when content actually changes."""
    geom = json.loads(_GEOM.read_text(encoding="utf-8"))
    assert list(geom) == sorted(geom)


def test_gates_expose_body_anchors_for_inversion_bubbles():
    """Logic gates expose both the lead-tip wiring anchors (``in``/``out``) and the
    body anchors (``bin``/``bout``) the manual documents, so a user can drop an
    inversion bubble on the gate body."""
    for kind in ("buffer", "ieeestd not port"):
        pins = _pin_names(kind)
        assert {"in", "bin", "out", "bout"} <= set(pins), f"{kind}: {pins}"


def test_parametric_gate_exposes_both_anchor_families():
    """A parametric gate's per-N data carries both families per input."""
    nd = _COMPONENTS["american and port"]["param"]["n_data"]["2"]
    names = {p["name"] for p in nd["pins"]}
    assert {"in 1", "in 2", "out", "bin 1", "bin 2", "bout"} <= names


def test_manual_library_bakes_no_grid_alignment_scale():
    """The manual library renders every component at its true CircuiTikZ size — it
    bakes **no** grid-alignment scale (pins sit at their natural, mostly off-grid
    positions, reached by the magnet). So no entry — top-level or per-``n_data``
    combo — carries a ``scale`` key."""
    for kind, e in _COMPONENTS.items():
        assert "scale" not in e, f"{kind}: unexpected baked top-level scale"
        nd = (e.get("param") or {}).get("n_data") or e.get("n_data") or {}
        for combo, v in nd.items():
            assert "scale" not in v, f"{kind}[{combo}]: unexpected baked scale"


def test_input_parametric_gate_only_pin_count_varies():
    """Changing a gate's input count only adds/removes pins; with no baked scale the
    body is never rescaled (the emitted node carries no xscale/yscale at any count)."""
    gates = [(k, c["param"]) for k, c in _COMPONENTS.items()
             if c.get("param", {}).get("name") == "inputs"]
    assert gates, "expected input-parametric gates in the manual library"
    for kind, param in gates:
        n_data = param["n_data"]
        counts = {n: len(v["pins"]) for n, v in n_data.items()}
        assert counts[str(param["max"])] > counts[str(param["min"])], counts


def test_multiterminal_bjts_expose_primary_collector_emitter():
    """The parametric BJTs expose the **primary** base/collector/emitter terminals
    (``B``/``C``/``E``, like the curated npn/pnp), and add the numbered branch
    terminals only when more than one collector/emitter is configured."""
    for kw in ("bjtnpn", "bjtpnp"):
        bjt = _COMPONENTS[kw]
        default = {p["name"] for p in bjt["pins"]}
        assert default == {"B", "C", "E"}, f"{kw}: {default}"
        multi = {p["name"] for p in bjt["n_data"]["2,3"]["pins"]}
        assert {"B", "C", "E", "C1", "C2", "E1", "E2", "E3"} <= multi, f"{kw}: {multi}"


def test_inversion_dot_is_a_centered_bubble_kind():
    """``notcirc`` is a single-point marker with a **centre** pin and an inversion-bubble
    kind. The bubble sits on the gate body anchor; the canvas draws it centred there (a
    preview), and codegen exports it tangent via the ``[ocirc, left]`` idiom — tangency
    is never baked into the pin."""
    from app.schematic.model import is_terminal_marker, INVERSION_BUBBLE_KINDS

    assert "notcirc" in INVERSION_BUBBLE_KINDS and is_terminal_marker("notcirc")
    pins = _COMPONENTS["notcirc"]["pins"]
    assert len(pins) == 1 and pins[0]["offset"] == [0.0, 0.0]


def test_logic_gates_carry_ctikzset_size_keys():
    """american/european and-family gates bake the CircuiTikZ body ``height``/``width``
    ``\\ctikzset`` keys (the recommended sizing) with their defaults; not/buffer and the
    ieeestd family don't (the keys don't size them — they fall back to xscale/yscale)."""
    for kw in ("american and port", "american or port", "american nand port",
               "european and port"):
        sk = _COMPONENTS[kw].get("size_keys")
        assert sk and sk["path"] == f"tripoles/{kw}"
        assert sk["height"] > 0 and sk["width"] > 0
    assert _COMPONENTS["american and port"]["size_keys"]["height"] == 0.8
    assert _COMPONENTS["american and port"]["size_keys"]["width"] == 1.1
    for kw in ("american not port", "american buffer port", "ieeestd and port"):
        assert "size_keys" not in _COMPONENTS[kw], kw


def test_flipflop_keeps_documented_border_anchors():
    """The flip-flop's documented border/edge anchors survive (they were being
    stripped as if they were chips' redundant probe anchors)."""
    pins = set(_pin_names("flipflop"))
    assert {"bpin 1", "bpin 6"} <= pins


def test_flipflop_jk_exposes_documented_body_anchors():
    """``flipflop JK`` is the manual's fully-documented flip-flop; its body/border
    anchors (``bpin N``/``bup``/``bdown``) are now exposed verbatim from the manual's
    anchor list (merged in via ``extract_doc_anchors.extract``)."""
    pins = set(_pin_names("flipflop JK"))
    assert {"bpin 1", "bpin 6", "bup", "bdown"} <= pins


def test_seven_segment_exposes_segment_terminals():
    """The seven-segment display exposes its documented segment terminals (``a``–``g``
    and the decimal ``dot``) rather than a single placeholder origin pin — these come
    from the manual's anchor list, which the bare scrape missed."""
    pins = set(_pin_names("bare7seg"))
    assert {"a", "b", "c", "d", "f", "g", "dot"} <= pins
    assert "in" not in pins        # the placeholder origin pin is gone


def test_documented_geometric_anchors_exposed():
    """Geometric anchors the manual documents become wireable pins — on a shape that
    also has real terminals. A BNC documents left/right/center beside hot/zero/shield,
    so its geometric anchors join the connector terminals (the policy is to expose
    every documented anchor, geometric included)."""
    pins = set(_pin_names("bnc"))
    assert {"hot", "zero", "shield"} <= pins         # the connector terminals
    assert pins & {"left", "right", "center"}        # ≥1 documented geometric anchor


def test_single_point_symbols_are_not_promoted_by_geometric_anchors():
    """A node whose documented anchors are ALL geographic (ground, supply rail) stays a
    single-point symbol — its compass is not exposed, so its standalone ``\\node at``
    emission (node-side placement, node text) is preserved instead of being turned into
    a multi-terminal node with a compass rose of pins."""
    for kind in ("ground", "vcc"):
        assert _pin_names(kind) == ["in"]


def test_chip_border_anchors_still_stripped():
    """A chip documents no anchors (all probed), so its redundant ``bpin N`` border
    anchors stay stripped — only the external ``pin N`` pads are exposed."""
    dip = _COMPONENTS["dipchip"]
    nd = dip["param"]["n_data"][str(dip["param"]["default"])]
    names = [p["name"] for p in nd["pins"]]
    assert all(n.startswith("pin ") for n in names), names
    assert not any(n.startswith("bpin") for n in names), names


def test_single_muxdemux_element_no_separate_demux():
    """A demultiplexer is a mirrored multiplexer, so the library carries only the
    parametric ``muxdemux`` element — no redundant ``demux`` kind."""
    assert "demux" not in _COMPONENTS
    mux = _COMPONENTS["muxdemux"]
    assert {s["name"] for s in mux["params"]} == {"inputs", "selects"}
    names = {p["name"] for p in mux["n_data"]["3,2"]["pins"]}
    assert {"in0", "in1", "in2", "out", "sel0", "sel1"} <= names


def test_muxdemux_body_size_is_fixed_across_pin_counts():
    """The muxdemux body no longer grows with the pin count — its bbox is the same
    for a 2-input and an 8-input mux (the pins just pack closer; the user resizes)."""
    mux = _COMPONENTS["muxdemux"]
    bbox2 = mux["n_data"]["2,1"]["bbox"]
    bbox8 = mux["n_data"]["8,1"]["bbox"]
    # Heights (y-extent) match within a small tolerance (alignment-scale rounding).
    h2 = bbox2[3] - bbox2[1]
    h8 = bbox8[3] - bbox8[1]
    assert abs(h2 - h8) < 0.3, (bbox2, bbox8)
