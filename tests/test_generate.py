"""
Component generation pipeline tests (spec/component-pipeline.md).

The Qt-free core in ``app/components/generate.py`` (data entries, alignment,
validation, the store) is tested directly; the render paths are gated on
latex/dvisvgm.
"""

from __future__ import annotations

import json
import shutil

import pytest

from app.components import generate as renderer
from app.components import render as comp_render

_HAVE_TOOLCHAIN = bool(shutil.which("latex") and shutil.which("dvisvgm"))


def _opamp_entry() -> dict:
    return {
        "display_name": "Op-Amp", "category": "Amplifiers",
        "emission": "node", "tikz": "op amp", "labels": ["l"],
        "bbox": [-1.5, -1.0, 1.5, 1.0],
        "pins": [
            {"name": "+", "offset": [-1.5, 0.5], "anchor": "+"},
            {"name": "-", "offset": [-1.5, -0.5], "anchor": "-"},
            {"name": "out", "offset": [1.5, 0.0], "anchor": "out"},
        ],
    }


def _resistor_entry() -> dict:
    return {
        "display_name": "Resistor", "category": "Resistors",
        "emission": "path", "tikz": "R", "labels": ["l"],
        "bbox": [0.0, -0.25, 2.0, 0.25],
        "pins": [
            {"name": "in", "offset": [0.0, 0.0], "anchor": None},
            {"name": "out", "offset": [2.0, 0.0], "anchor": None},
        ],
    }


# ---------------------------------------------------------------------------
# renderer.data_entry — record shape (scale-only, no anchor_pin / leads)
# ---------------------------------------------------------------------------

def test_data_entry_has_no_anchor_pin_or_leads():
    """Every node is centre-placed and aligned by scale alone (§4) — the record
    carries no `anchor_pin` and no `leads`, and the pins pass through verbatim."""
    e = renderer.data_entry("op amp", {**_opamp_entry(), "scale": [1.0504, 1.0]})
    assert "anchor_pin" not in e
    assert "leads" not in e
    assert e["scale"] == [1.0504, 1.0]
    assert [(p["name"], p["anchor"]) for p in e["pins"]] == [
        ("+", "+"), ("-", "-"), ("out", "out")]


def test_data_entry_omits_unit_scale():
    """A `[1, 1]` scale is dropped (omitted when no stretch); a node with no
    authored scale gets no scale field."""
    e = renderer.data_entry("op amp", _opamp_entry())   # no scale on the fixture
    assert "scale" not in e


def test_compute_bbox_from_ink_extent_union_pins():
    # SVG coords for a symbol spanning x:[0,1.02] GU, y:[-0.21,0.21] GU about the
    # origin, with pins at (0,0) and (1,0).  The bbox = ink ∪ pins, rounded
    # outward to 0.05.
    ox = oy = 15.0312
    K = 28.34765
    geom = {
        "paths": [{"d": f"M{ox} {oy - 0.21 * K} L{ox + 1.02 * K} {oy + 0.21 * K}"}],
        "glyphs": [],
    }
    pins = [{"name": "in", "offset": [0, 0]}, {"name": "out", "offset": [1, 0]}]
    assert renderer.compute_bbox(geom, (ox, oy), pins) == [0.0, -0.25, 1.05, 0.25]


def test_compute_bbox_handles_hv_and_curves():
    # H/V (axis moves) and C (control points) must not desync coordinate pairing.
    ox = oy = 15.0312
    K = 28.34765
    geom = {
        "paths": [{
            "d": (f"M{ox} {oy}H{ox + 0.5 * K}V{oy - 0.3 * K}"
                  f"C{ox + 0.5 * K} {oy - 0.3 * K} {ox + 0.5 * K} {oy} {ox + K} {oy}"),
        }],
        "glyphs": [],
    }
    pins = [{"name": "p", "offset": [0, 0]}]
    # x spans 0..1.0, y spans -0.3..0 (Qt y-down) -> rounds to clean 0.05 grid.
    assert renderer.compute_bbox(geom, (ox, oy), pins) == [0.0, -0.3, 1.0, 0.0]


def test_param_pins_computes_symmetric_inputs():
    """A parametric component's pins: output + N inputs at the declared pitch,
    symmetric about the output's y, named/anchored by the templates."""
    entry = {"param": {
        "input": {"name": "in{i}", "anchor": "in {i}", "x": -1.5, "pitch": 0.5},
        "output": {"name": "out", "anchor": "out", "offset": [0, 0]}}}
    got = [(p["name"], tuple(p["offset"]), p["anchor"]) for p in renderer.param_pins(entry, 3)]
    assert got == [
        ("out", (0, 0), "out"),
        ("in1", (-1.5, -0.5), "in 1"),
        ("in2", (-1.5, 0.0), "in 2"),
        ("in3", (-1.5, 0.5), "in 3"),
    ]
    assert len(renderer.param_pins(entry, 7)) == 8   # output + 7 inputs


def test_and_gate_is_parametric_in_the_data():
    d = json.loads(renderer.DEFINITIONS_PATH.read_text())["components"]["and"]
    assert d["param"]["min"] == 2 and d["param"]["max"] == 16
    assert set(d["param"]["n_data"]) == {str(n) for n in range(2, 17)}
    # At its default value it is an ordinary 2-input multi-terminal (node) record.
    assert [p["name"] for p in d["pins"]] == ["out", "in1", "in2"]
    assert d["tikz"] == "and port"  # base keyword, not the concrete "…, number inputs=2"


def test_variant_key():
    assert renderer.variant_key("D", {"name": "filled", "token": "*", "mode": "suffix"}) == "D*"
    assert renderer.variant_key("nigfete", {"name": "body_diode", "token": "bodydiode",
                                         "mode": "option"}) == "nigfete_bodydiode"


# ---------------------------------------------------------------------------
# validate_entry — the batch generator's pre-flight
# ---------------------------------------------------------------------------

def test_validate_clean():
    assert renderer.validate_entry("R", _resistor_entry()) == []
    assert renderer.validate_entry("op amp", _opamp_entry()) == []


def test_validate_off_grid_pin():
    e = _resistor_entry()
    e["pins"][1]["offset"] = [2.1, 0.0]
    assert any("0.25 GU grid" in m for m in renderer.validate_entry("R", e))


def test_validate_multi_terminal_needs_anchor():
    e = _opamp_entry()
    e["pins"][0]["anchor"] = None
    assert any("CircuiTikZ anchor" in m for m in renderer.validate_entry("op amp", e))


def test_validate_requires_kind_and_keyword():
    e = _resistor_entry()
    e["tikz"] = ""
    msgs = renderer.validate_entry("", e)
    assert any("Kind is required" in m for m in msgs)
    assert any("keyword is required" in m for m in msgs)


def test_validate_allows_measured_off_grid_styles():
    """Measured pins are legitimately off-grid: a path bipole's off-axis tap
    (thyristor gate), a centre-placed entry with a uniform grid scale, and a
    muxdemux entry (no authored pins at all) must all validate clean."""
    tap = _resistor_entry()
    tap["pins"].append({"name": "gate", "offset": [1.7, -0.77], "anchor": None})
    assert renderer.validate_entry("thyristor", tap) == []

    scaled = {**_opamp_entry(), "scale": [0.8929, 0.8929]}
    scaled["pins"] = [{**p, "offset": [-1.12, -0.56]} for p in scaled["pins"]]
    assert renderer.validate_entry("flipflop D", scaled) == []

    muxd = {"display_name": "Multiplexer", "category": "Logic",
            "emission": "node", "tikz": "muxdemux",
            "muxdemux": {"role": "mux", "data_param": "inputs",
                         "select_param": "selects"}}
    assert renderer.validate_entry("mux", muxd) == []


def test_validate_every_shipped_entry():
    """The whole shipped library passes the generator's pre-flight."""
    authored = renderer.load_authored()
    bad = {k: errs for k, e in authored.items()
           if (errs := renderer.validate_entry(k, e))}
    assert bad == {}


def test_data_entry_derives_component_def():
    from app.components import library
    cdef = library.to_component_def("R", renderer.data_entry("R", _resistor_entry()))
    assert cdef.kind == "R"
    assert cdef.default_span == (2.0, 0.0)
    assert [p.name for p in cdef.pins] == ["in", "out"]


# ---------------------------------------------------------------------------
# Render paths (gated on toolchain)
# ---------------------------------------------------------------------------

@pytest.mark.slow  # re-renders every symbol through latex/dvisvgm (~3 min); --run-slow
@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_render_store_reproduces_committed_files():
    authored = renderer.load_authored()
    geometry, components, origin = renderer.render_store(authored)
    cur = json.loads(renderer.DEFINITIONS_PATH.read_text())
    assert list(origin) == cur["origin_svg"]
    assert components == cur["components"]
    assert geometry == json.loads(renderer.GEOMETRY_PATH.read_text())


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_best_alignment_uniform_scale_and_pins():
    """The uniform algorithm (§4) is recomputed from measurement: a per-axis
    scale (closest-to-1.0 that grids the pins) and scaled pin offsets — no
    anchor_pin, no leads. The op amp, formerly leads-only, now aligns by scale."""
    defs = json.loads(renderer.DEFINITIONS_PATH.read_text())["components"]

    scale, pins = renderer.best_alignment(defs["op amp"])
    assert scale == [1.0504, 1.0]
    off = {p["name"]: p["offset"] for p in pins}
    assert off == {"+": [-1.25, 0.5], "-": [-1.25, -0.5], "out": [1.25, 0.0]}

    scale, pins = renderer.best_alignment(defs["npn"])
    assert scale == [0.8929, 0.974]      # the grid-landing scale nearest 1.0

    # Every pin offset is a multiple of the grid except where the symbol is
    # intrinsically off-grid (none for the BJT).
    scale, pins = renderer.best_alignment(defs["nigfete"])
    assert scale == [1.0204, 0.974]


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_best_alignment_scale_within_bounds():
    """The derived scale never exceeds the configured distortion bounds."""
    defs = json.loads(renderer.DEFINITIONS_PATH.read_text())["components"]
    for kind in ("op amp", "npn", "nigfete", "spdt", "flipflop D"):
        scale, _ = renderer.best_alignment(defs[kind])
        if scale is not None:
            for s in scale:
                assert renderer.SCALE_MIN - 1e-9 <= s <= renderer.SCALE_MAX + 1e-9


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_save_component_round_trips(tmp_path, monkeypatch):
    # Redirect the store to temp files seeded with a minimal valid store.
    comp_path = tmp_path / "definitions.json"
    man_path = tmp_path / "geometry.json"
    comp_path.write_text(json.dumps({"origin_svg": [15.0312, 15.0312],
                                     "circuitikz_version": "9.9-test",
                                     "components": {}}))
    man_path.write_text("{}")
    monkeypatch.setattr(renderer, "DEFINITIONS_PATH", comp_path)
    monkeypatch.setattr(renderer, "GEOMETRY_PATH", man_path)

    renderer.save_component("R", _resistor_entry())
    data = json.loads(comp_path.read_text())
    assert "R" in data["components"]
    assert data["components"]["R"]["tikz"] == "R"
    # An incremental save keeps the batch-generation version stamp.
    assert data["circuitikz_version"] == "9.9-test"
    geometry = json.loads(man_path.read_text())
    assert "R" in geometry and geometry["R"]["paths"]


# ---------------------------------------------------------------------------
# Batch regeneration faithfulness (render_store vs the authored styles)
# ---------------------------------------------------------------------------

def test_best_alignment_grids_pins_and_picks_scale_nearest_one(monkeypatch):
    """The uniform algorithm scales each axis to land the measured anchors on the
    grid, choosing the grid-landing scale nearest 1.0, and bakes the scaled
    offsets into the pins (no anchor_pin / leads). Pure — measurement stubbed."""
    entry = {
        "display_name": "D Flip-flop", "category": "Logic", "emission": "node",
        "tikz": "flipflop D", "labels": [],
        "pins": [{"name": "D", "offset": [0, 0], "anchor": "pin 1"},
                 {"name": "Q", "offset": [0, 0], "anchor": "pin 3"}],
    }
    measured = {"pin 1": (-1.12, -0.56), "pin 3": (1.12, -0.56)}
    monkeypatch.setattr(renderer.render, "measure_anchors",
                        lambda kw, anchors, ctikzset=None: measured)

    scale, pins = renderer.best_alignment(entry)
    # 1.12 * 0.8929 == 1.0 and 0.56 * 0.8929 == 0.5: pins snap to the grid.
    assert scale == [0.8929, 0.8929]
    assert [p["offset"] for p in pins] == [[-1.0, -0.5], [1.0, -0.5]]


def test_best_alignment_keeps_offgrid_pin_when_no_in_bounds_scale(monkeypatch):
    """A coordinate no in-bounds scale can grid stays at its true scaled value
    (reached by the magnet) rather than forcing a distorting scale."""
    entry = {
        "display_name": "X", "category": "C", "emission": "node", "tikz": "x",
        "pins": [{"name": "a", "offset": [0, 0], "anchor": "a"},
                 {"name": "b", "offset": [0, 0], "anchor": "b"}],
    }
    # 'a' grids at sx=1.0; 'b' would need ~1.6 (out of [0.7,1.3]) → stays off-grid.
    measured = {"a": (1.0, 0.0), "b": (0.157, 0.0)}
    monkeypatch.setattr(renderer.render, "measure_anchors",
                        lambda kw, anchors, ctikzset=None: measured)

    scale, pins = renderer.best_alignment(entry)
    assert scale is None                       # sx = sy = 1.0
    off = {p["name"]: p["offset"] for p in pins}
    assert off["a"] == [1.0, 0.0]
    assert off["b"] == [0.157, 0.0]            # off-grid, kept verbatim


def test_render_store_routes_muxdemux(monkeypatch):
    """The batch generator re-renders two-parameter mux/demux entries through
    render_muxdemux (regression: routed as plain nodes they silently lost
    params/n_data and every kind:data:select geometry combo)."""
    rec = {"role": "mux", "data_param": "inputs", "select_param": "selects"}
    authored = {"mux": {
        "display_name": "Multiplexer", "category": "Logic", "emission": "node",
        "tikz": "muxdemux", "labels": [], "pins": [],
        "params": [{"name": "inputs", "min": 2, "max": 2, "default": 2},
                   {"name": "selects", "min": 1, "max": 1, "default": 1}],
        "muxdemux": rec,
    }}
    monkeypatch.setattr(renderer, "measure_origin", lambda e: (15.0, 15.0))
    called = {}

    def _fake_muxdemux(kind, entry, origin):
        called["kind"] = kind
        return ({"mux:2:1": {"paths": []}, "mux": {"paths": []}},
                {"params": entry["params"], "muxdemux": entry["muxdemux"],
                 "n_data": {"2,1": {}}})

    monkeypatch.setattr(renderer, "render_muxdemux", _fake_muxdemux)
    geometry, components, _origin = renderer.render_store(authored)
    assert called["kind"] == "mux"
    assert "mux:2:1" in geometry
    assert components["mux"]["n_data"] == {"2,1": {}}
    assert components["mux"]["muxdemux"] == rec


def test_render_muxdemux_persists_authoring_rec(monkeypatch):
    """The stored mux/demux record carries its 'muxdemux' authoring rec, so the
    batch generator can re-render the combos from definitions.json alone."""
    monkeypatch.setattr(renderer, "_muxdemux_combo", lambda role, d, s: (
        f"muxdemux def={{NL=1, NR={d}, NB={s}}}",
        {"lpin 1": (-1.0, 0.5), "rpin 1": (1.0, 0.5), "bpin 1": (0.0, -1.0)},
        [("in", "lpin 1"), ("out0", "rpin 1"), ("sel0", "bpin 1")]))
    monkeypatch.setattr(renderer, "geometry",
                        lambda e, option="": {"viewBox": [0, 0, 1, 1],
                                              "paths": [], "glyphs": []})
    monkeypatch.setattr(renderer, "compute_bbox", lambda g, o, p: [-1, -1, 1, 1])

    rec = {"role": "demux", "data_param": "outputs", "select_param": "selects"}
    entry = {
        "display_name": "Demultiplexer", "category": "Logic",
        "emission": "node", "tikz": "muxdemux", "labels": [],
        "params": [{"name": "outputs", "min": 2, "max": 2, "default": 2},
                   {"name": "selects", "min": 1, "max": 1, "default": 1}],
        "muxdemux": rec,
    }
    geoms, de = renderer.render_muxdemux("demux", entry, (15.0, 15.0))
    assert de["muxdemux"] == rec
    assert set(de["n_data"]) == {"2,1"}
    assert set(geoms) == {"demux:2:1", "demux"}


# ---------------------------------------------------------------------------
# CircuiTikZ generation-version stamp
# ---------------------------------------------------------------------------

def test_render_doc_probes_circuitikz_version():
    """Every measurement compile typesets the installed CircuiTikZ version
    (guarded by \\ifdefined so pre-macro versions still compile)."""
    assert r"\typeout{HVCTIKZVERSION \pgfcircversion}" in comp_render._DOC
    assert r"\ifdefined\pgfcircversion" in comp_render._DOC


def test_circuitikz_version_parses_probe_and_banner():
    # Primary: the HVCTIKZVERSION line from _DOC's probe.
    log = "blah\nHVCTIKZVERSION 1.6.4\nHVANCHOR out = 28.4pt , 0.0pt\n"
    assert comp_render.circuitikz_version(log) == "1.6.4"
    # Fallback: the package's log banner (pre-\pgfcircversion releases).
    banner = "Package: circuitikz 2021/01/01 The CircuiTikZ package version 1.2.7\n"
    assert comp_render.circuitikz_version(banner) == "1.2.7"
    # Neither present -> unknown.
    assert comp_render.circuitikz_version("no version info here") is None


def test_write_store_stamps_circuitikz_version(tmp_path, monkeypatch):
    """The batch generator records the CircuiTikZ version the library was
    rendered against; an unknown version omits the key rather than lying."""
    monkeypatch.setattr(renderer, "DEFINITIONS_PATH", tmp_path / "definitions.json")
    monkeypatch.setattr(renderer, "GEOMETRY_PATH", tmp_path / "geometry.json")

    renderer.write_store({}, {}, (15.0, 15.0), circuitikz_version="1.7.0")
    data = json.loads((tmp_path / "definitions.json").read_text())
    assert data["circuitikz_version"] == "1.7.0"
    assert data["origin_svg"] == [15.0, 15.0]

    renderer.write_store({}, {}, (15.0, 15.0))
    data = json.loads((tmp_path / "definitions.json").read_text())
    assert "circuitikz_version" not in data


