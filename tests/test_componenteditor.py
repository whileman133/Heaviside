"""
Component-editor core tests (spec/component-editor.md).

The Qt-free core (renderer data/validation + draft) is tested directly; the
render paths are gated on latex/dvisvgm; the window is smoke-tested offscreen.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.componenteditor import draft, renderer

_HAVE_TOOLCHAIN = bool(shutil.which("latex") and shutil.which("dvisvgm"))


def _opamp_entry() -> dict:
    return {
        "display_name": "Op-Amp", "category": "Amplifiers",
        "emission": "multi_terminal", "tikz": "op amp", "labels": ["l"],
        "bbox": [-1.5, -1.0, 1.5, 1.0],
        "pins": [
            {"name": "+", "offset": [-1.5, 0.5], "anchor": "+"},
            {"name": "-", "offset": [-1.5, -0.5], "anchor": "-"},
            {"name": "out", "offset": [1.5, 0.0], "anchor": "out"},
        ],
        "anchor_pin": None,
    }


def _resistor_entry() -> dict:
    return {
        "display_name": "Resistor", "category": "Resistors",
        "emission": "two_terminal", "tikz": "R", "labels": ["l"],
        "bbox": [0.0, -0.25, 2.0, 0.25],
        "pins": [
            {"name": "in", "offset": [0.0, 0.0], "anchor": None},
            {"name": "out", "offset": [2.0, 0.0], "anchor": None},
        ],
    }


# ---------------------------------------------------------------------------
# renderer.data_entry — computed leads
# ---------------------------------------------------------------------------

def test_data_entry_computes_leads_for_centre_placed():
    e = renderer.data_entry("op amp", _opamp_entry())
    assert e["anchor_pin"] is None
    # Centre-placed: every pin gets a lead to its grid offset.
    assert e["leads"] == [
        {"anchor": "+", "to": [-1.5, 0.5]},
        {"anchor": "-", "to": [-1.5, -0.5]},
        {"anchor": "out", "to": [1.5, 0.0]},
    ]


def test_data_entry_anchor_pin_excluded_from_leads():
    nigfete = {
        "display_name": "NMOS", "category": "Transistors", "emission": "multi_terminal",
        "tikz": "nigfete", "labels": ["l"], "bbox": [-0.05, -1.1, 1.05, 0.55],
        "anchor_pin": "gate",
        "pins": [
            {"name": "gate", "offset": [0, 0], "anchor": "gate"},
            {"name": "drain", "offset": [1, -1], "anchor": "drain"},
            {"name": "source", "offset": [1, 0.5], "anchor": "source"},
        ],
    }
    e = renderer.data_entry("nigfete", nigfete)
    assert {l["anchor"] for l in e["leads"]} == {"drain", "source"}  # gate is the origin


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


def test_variant_key():
    assert renderer.variant_key("D", {"name": "filled", "token": "*", "mode": "suffix"}) == "D*"
    assert renderer.variant_key("nigfete", {"name": "body_diode", "token": "bodydiode",
                                         "mode": "option"}) == "nigfete_bodydiode"


# ---------------------------------------------------------------------------
# draft.validate_entry
# ---------------------------------------------------------------------------

def test_validate_clean():
    assert draft.validate_entry("R", _resistor_entry()) == []
    assert draft.validate_entry("op amp", _opamp_entry()) == []


def test_validate_off_grid_pin():
    e = _resistor_entry()
    e["pins"][1]["offset"] = [2.1, 0.0]
    assert any("0.25 GU grid" in m for m in draft.validate_entry("R", e))


def test_validate_multi_terminal_needs_anchor():
    e = _opamp_entry()
    e["pins"][0]["anchor"] = None
    assert any("CircuiTikZ anchor" in m for m in draft.validate_entry("op amp", e))


def test_validate_requires_kind_and_keyword():
    e = _resistor_entry()
    e["tikz"] = ""
    msgs = draft.validate_entry("", e)
    assert any("Kind is required" in m for m in msgs)
    assert any("keyword is required" in m for m in msgs)


def test_derived_component_def():
    cdef = draft.derived_component_def("R", _resistor_entry())
    assert cdef.kind == "R"
    assert cdef.default_span == (2.0, 0.0)
    assert [p.name for p in cdef.pins] == ["in", "out"]


# ---------------------------------------------------------------------------
# Render paths (gated on toolchain)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_render_store_reproduces_committed_files():
    authored = renderer.load_authored()
    geometry, components, origin = renderer.render_store(authored)
    cur = json.loads(renderer.DEFINITIONS_PATH.read_text())
    assert list(origin) == cur["origin_svg"]
    assert components == cur["components"]
    assert geometry == json.loads(renderer.GEOMETRY_PATH.read_text())


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_fit_alignment_derives_scale_and_leads():
    """Alignment is recomputed from measurement (not read from the stored values),
    with the scale-vs-leads strategy driven by anchor_pin."""
    defs = json.loads(renderer.DEFINITIONS_PATH.read_text())["components"]

    # Centre-placed (anchor_pin=None): leads-only, no scale (don't distort).
    scale, leads = renderer.fit_alignment(defs["op amp"])
    assert scale is None
    assert {ld["anchor"] for ld in leads} == {"+", "-", "out"}

    # Anchor-pinned BJT: a single scale lands collector/emitter exactly, no leads.
    scale, leads = renderer.fit_alignment(defs["npn"])
    assert scale == [1.1905, 1.2987] and leads == []

    # Anchor-pinned MOSFET: scale + one residual source lead.
    scale, leads = renderer.fit_alignment(defs["nigfete"])
    assert scale == [1.0204, 0.962]
    assert [ld["anchor"] for ld in leads] == ["source"]


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_lead_paths_isolated_by_leadsfree_diff():
    # The editor colours pin extensions (leads) by diffing the full render against
    # a leads-free render: the extra paths are exactly the extensions.
    opamp = _opamp_entry()
    opamp["leads"] = [{"anchor": "+", "to": [-1.5, 0.5]},
                      {"anchor": "-", "to": [-1.5, -0.5]},
                      {"anchor": "out", "to": [1.5, 0.0]}]
    full = renderer.geometry(opamp)
    body = renderer.geometry({**opamp, "leads": []})
    body_ds = {p["d"] for p in body["paths"]}
    leads = [p["d"] for p in full["paths"] if p["d"] not in body_ds]
    assert len(leads) == 3                      # one extension per pin
    # A symbol with no extensions yields no extra paths.
    r = _resistor_entry()
    rfull = renderer.geometry(r)
    rbody = renderer.geometry({**r, "leads": []})
    assert {p["d"] for p in rfull["paths"]} == {p["d"] for p in rbody["paths"]}


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_save_component_round_trips(tmp_path, monkeypatch):
    # Redirect the store to temp files seeded with a minimal valid store.
    comp_path = tmp_path / "definitions.json"
    man_path = tmp_path / "geometry.json"
    comp_path.write_text(json.dumps({"origin_svg": [15.0312, 15.0312], "components": {}}))
    man_path.write_text("{}")
    monkeypatch.setattr(renderer, "DEFINITIONS_PATH", comp_path)
    monkeypatch.setattr(renderer, "GEOMETRY_PATH", man_path)

    renderer.save_component("R", _resistor_entry())
    data = json.loads(comp_path.read_text())
    assert "R" in data["components"]
    assert data["components"]["R"]["tikz"] == "R"
    geometry = json.loads(man_path.read_text())
    assert "R" in geometry and geometry["R"]["paths"]


# ---------------------------------------------------------------------------
# Window smoke (offscreen)
# ---------------------------------------------------------------------------

def test_window_constructs_and_round_trips_form():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    try:
        QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Qt unavailable: {exc}")
    from app.componenteditor.window import ComponentEditorWindow

    win = ComponentEditorWindow()
    win._entry_to_form("op amp", _opamp_entry())
    kind, entry = win._form_to_entry()
    assert kind == "op amp"
    assert entry["emission"] == "multi_terminal"
    assert [p["name"] for p in entry["pins"]] == ["+", "-", "out"]
    assert "scale" not in entry  # op amp has no scale (it uses leads)
    assert draft.validate_entry(kind, entry) == []


def test_window_scale_is_editable_and_round_trips():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    try:
        QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Qt unavailable: {exc}")
    from app.componenteditor.window import ComponentEditorWindow

    win = ComponentEditorWindow()
    scaled = {**_opamp_entry(), "scale": [1.1905, 1.2987]}
    win._entry_to_form("npn", scaled)
    # Loaded into the editable spin boxes...
    assert win._scale_x.value() == 1.1905
    assert win._scale_y.value() == 1.2987
    # ...and written back out.
    _, out = win._form_to_entry()
    assert out["scale"] == [1.1905, 1.2987]
    # Editing the spin boxes by hand flows into the entry.
    win._set_scale(2.0, 0.5)
    _, out2 = win._form_to_entry()
    assert out2["scale"] == [2.0, 0.5]


def test_window_bbox_is_read_only_and_derived():
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication
    try:
        QApplication.instance() or QApplication([])
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"Qt unavailable: {exc}")
    from app.componenteditor.window import ComponentEditorWindow

    win = ComponentEditorWindow()
    # The bbox is computed on Render, not hand-typed: the spin boxes are read-only.
    assert all(sb.isReadOnly() for sb in win._bbox)
    win._set_bbox([0.0, -0.25, 2.05, 0.25])
    _, entry = win._form_to_entry()
    assert entry["bbox"] == [0.0, -0.25, 2.05, 0.25]
