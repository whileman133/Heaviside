"""
Component-editor core tests (spec/component-editor.md).

The Qt-free core (baker data/validation + draft) is tested directly; the bake
(render) paths are gated on latex/dvisvgm; the window is smoke-tested offscreen.
"""

from __future__ import annotations

import json
import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.componenteditor import baker, draft

_HAVE_TOOLCHAIN = bool(shutil.which("latex") and shutil.which("dvisvgm"))


def _opamp_entry() -> dict:
    return {
        "display_name": "Op-Amp", "category": "Tripoles",
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
        "display_name": "Resistor", "category": "Bipoles",
        "emission": "two_terminal", "tikz": "R", "labels": ["l"],
        "bbox": [0.0, -0.25, 2.0, 0.25],
        "pins": [
            {"name": "in", "offset": [0.0, 0.0], "anchor": None},
            {"name": "out", "offset": [2.0, 0.0], "anchor": None},
        ],
    }


# ---------------------------------------------------------------------------
# baker.data_entry — computed leads
# ---------------------------------------------------------------------------

def test_data_entry_computes_leads_for_centre_placed():
    e = baker.data_entry("op amp", _opamp_entry())
    assert e["anchor_pin"] is None
    # Centre-placed: every pin gets a lead to its grid offset.
    assert e["leads"] == [
        {"anchor": "+", "to": [-1.5, 0.5]},
        {"anchor": "-", "to": [-1.5, -0.5]},
        {"anchor": "out", "to": [1.5, 0.0]},
    ]


def test_data_entry_anchor_pin_excluded_from_leads():
    nigfete = {
        "display_name": "NMOS", "category": "Tripoles", "emission": "multi_terminal",
        "tikz": "nigfete", "labels": ["l"], "bbox": [-0.05, -1.1, 1.05, 0.55],
        "anchor_pin": "gate",
        "pins": [
            {"name": "gate", "offset": [0, 0], "anchor": "gate"},
            {"name": "drain", "offset": [1, -1], "anchor": "drain"},
            {"name": "source", "offset": [1, 0.5], "anchor": "source"},
        ],
    }
    e = baker.data_entry("nigfete", nigfete)
    assert {l["anchor"] for l in e["leads"]} == {"drain", "source"}  # gate is the origin


def test_variant_key():
    assert baker.variant_key("D", {"name": "filled", "token": "*", "mode": "suffix"}) == "D*"
    assert baker.variant_key("nigfete", {"name": "body_diode", "token": "bodydiode",
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
    authored = baker.load_authored()
    manifest, components, origin = baker.render_store(authored)
    cur = json.loads(baker.COMPONENTS_PATH.read_text())
    assert list(origin) == cur["origin_svg"]
    assert components == cur["components"]
    assert manifest == json.loads(baker.MANIFEST_PATH.read_text())


@pytest.mark.skipif(not _HAVE_TOOLCHAIN, reason="latex/dvisvgm not installed")
def test_save_component_round_trips(tmp_path, monkeypatch):
    # Redirect the store to temp files seeded with a minimal valid store.
    comp_path = tmp_path / "components.json"
    man_path = tmp_path / "manifest.json"
    comp_path.write_text(json.dumps({"origin_svg": [15.0312, 15.0312], "components": {}}))
    man_path.write_text("{}")
    monkeypatch.setattr(baker, "COMPONENTS_PATH", comp_path)
    monkeypatch.setattr(baker, "MANIFEST_PATH", man_path)

    baker.save_component("R", _resistor_entry())
    data = json.loads(comp_path.read_text())
    assert "R" in data["components"]
    assert data["components"]["R"]["tikz"] == "R"
    manifest = json.loads(man_path.read_text())
    assert "R" in manifest and manifest["R"]["paths"]


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
    assert draft.validate_entry(kind, entry) == []
