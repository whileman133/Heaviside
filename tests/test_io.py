"""
Phase 3 tests — schematic file I/O.

All tests use a tmp_path fixture (no fixed filesystem paths).
No Qt, no LaTeX required.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from app.components.registry import REGISTRY
from app.schematic.io import SchematicLoadError, load, save
from app.schematic.model import Component, Schematic, Wire


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _empty_schematic() -> Schematic:
    return Schematic(version="0.1", name="empty")


def _one_of_each() -> Schematic:
    """Schematic with one instance of every v1 component kind."""
    from app.components.registry import REGISTRY

    components = []
    from app.components.model import RectComponent
    for i, (kind, defn) in enumerate(REGISTRY.items()):
        # Rects carry their style in fields, not the options string, so they
        # round-trip with empty options; everything else carries a label.
        opts = "" if issubclass(defn.component_class, RectComponent) else f"l=$X_{{{i}}}$"
        components.append(
            defn.component_class(
                id=_uid(),
                kind=kind,
                position=(float(i * 3), 0.0),
                rotation=0,
                options=opts,
                mirror=False,
            )
        )
    return Schematic(version="0.1", name="all_kinds", components=components)


def _schematic_with_wires() -> Schematic:
    return Schematic(
        version="0.1",
        name="wires",
        wires=[
            Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)]),
            Wire(id=_uid(), points=[(0.0, 0.0), (0.0, 1.5), (2.0, 1.5)]),
        ],
    )


def _schematic_with_options() -> Schematic:
    """Options string containing LaTeX special characters."""
    return Schematic(
        version="0.1",
        name="options",
        components=[
            Component(
                id=_uid(),
                kind="R",
                position=(0.0, 0.0),
                rotation=0,
                options=r"l=$\frac{R_2}{R_1}$, v=$V_{\mathrm{out}}$, i=$i_{\alpha} + i_{\beta}$",
            )
        ],
    )


# ---------------------------------------------------------------------------
# test_roundtrip_empty
# ---------------------------------------------------------------------------

def test_roundtrip_empty(tmp_path: Path) -> None:
    """Save and reload an empty schematic — loaded schematic equals original.

    ``save`` always writes the current file-format version, so the version is
    expected to be normalised, not preserved verbatim.
    """
    from app.schematic.io import _FORMAT_VERSION

    original = _empty_schematic()
    p = tmp_path / "empty.hv"
    save(original, p)
    loaded = load(p)

    assert loaded.version == _FORMAT_VERSION
    assert loaded.name == original.name
    assert loaded.components == original.components
    assert loaded.wires == original.wires
    assert loaded.metadata == original.metadata
    # Document config defaults to american and round-trips.
    assert loaded.voltage_style == "american"
    assert loaded.current_style == "american"


# ---------------------------------------------------------------------------
# Document config (voltage/current label styles, format 0.2)
# ---------------------------------------------------------------------------

def test_config_roundtrip(tmp_path: Path) -> None:
    """Non-default voltage/current styles round-trip through save/load."""
    s = Schematic(version="0.1", name="cfg", voltage_style="european",
                  current_style="american")
    p = tmp_path / "cfg.hv"
    save(s, p)
    loaded = load(p)
    assert loaded.voltage_style == "european"
    assert loaded.current_style == "american"
    # save() writes a config object at the current format version.
    import json
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == "0.3"
    assert data["config"] == {"voltage_style": "european", "current_style": "american"}


def test_load_v01_defaults_config_to_american(tmp_path: Path) -> None:
    """A 0.1 file (no config object) loads with american defaults."""
    p = tmp_path / "old.hv"
    p.write_text(
        '{"version": "0.1", "name": "old", "components": [], "wires": []}',
        encoding="utf-8",
    )
    loaded = load(p)
    assert (loaded.voltage_style, loaded.current_style) == ("american", "american")


def test_load_unknown_style_falls_back_to_american(tmp_path: Path) -> None:
    """An unrecognised style value is coerced to american rather than failing."""
    p = tmp_path / "weird.hv"
    p.write_text(
        '{"version": "0.2", "name": "w", "components": [], "wires": [], '
        '"config": {"voltage_style": "martian", "current_style": "european"}}',
        encoding="utf-8",
    )
    loaded = load(p)
    assert loaded.voltage_style == "american"   # bogus → default
    assert loaded.current_style == "european"   # valid → kept


# ---------------------------------------------------------------------------
# test_roundtrip_components
# ---------------------------------------------------------------------------

def test_roundtrip_components(tmp_path: Path) -> None:
    """Save and reload a schematic with one of each v1 component — all fields preserved."""
    original = _one_of_each()
    p = tmp_path / "all_kinds.hv"
    save(original, p)
    loaded = load(p)

    assert len(loaded.components) == len(original.components)
    for orig_c, load_c in zip(original.components, loaded.components):
        assert load_c.id       == orig_c.id
        assert load_c.kind     == orig_c.kind
        assert load_c.position == orig_c.position
        assert load_c.rotation == orig_c.rotation
        assert load_c.mirror   == orig_c.mirror
        assert load_c.options  == orig_c.options


def test_parametric_params_round_trip(tmp_path: Path) -> None:
    """A parametric component's integer params (logic-gate input count) survive
    save/load; the default is omitted from the file."""
    s = Schematic(version="0.1", name="gates", components=[
        Component(id=_uid(), kind="and", position=(0.0, 0.0), rotation=0,
                  options="l=$U_1$", params={"inputs": 5}),
        Component(id=_uid(), kind="and", position=(4.0, 0.0), rotation=0,
                  options="l=$U_2$"),  # default inputs -> no params
    ])
    p = tmp_path / "gates.hv"
    save(s, p)
    assert '"params"' in p.read_text(encoding="utf-8")          # the n=5 one is written
    loaded = load(p)
    assert loaded.components[0].params == {"inputs": 5}
    assert loaded.components[1].params == {}                    # default omitted


def test_kind_alias_migrates_renamed_kind_on_load(tmp_path: Path, monkeypatch) -> None:
    """A renamed kind keeps loading via _KIND_ALIASES (old -> current), so a
    CircuiTikZ re-generation that renames a symbol doesn't break old .hv files."""
    import app.schematic.io as io

    # Pretend "R" was once written as the (now-defunct) kind "resistor".
    monkeypatch.setitem(io._KIND_ALIASES, "resistor", "R")
    original = Schematic(version="0.1", name="aliased", components=[
        Component(id=_uid(), kind="R", position=(0.0, 0.0), rotation=0, options="l=$R_1$"),
    ])
    p = tmp_path / "aliased.hv"
    save(original, p)
    # Rewrite the on-disk kind to the old name, as an old file would have it.
    text = p.read_text(encoding="utf-8").replace('"kind": "R"', '"kind": "resistor"')
    p.write_text(text, encoding="utf-8")

    loaded = load(p)                          # must not raise "unknown kind"
    assert [c.kind for c in loaded.components] == ["R"]


# ---------------------------------------------------------------------------
# test_roundtrip_wires
# ---------------------------------------------------------------------------

def test_roundtrip_wires(tmp_path: Path) -> None:
    """Save and reload a schematic containing wires — all wire points preserved."""
    original = _schematic_with_wires()
    p = tmp_path / "wires.hv"
    save(original, p)
    loaded = load(p)

    assert len(loaded.wires) == len(original.wires)
    for orig_w, load_w in zip(original.wires, loaded.wires):
        assert load_w.id     == orig_w.id
        assert load_w.points == orig_w.points


# ---------------------------------------------------------------------------
# test_roundtrip_labels
# ---------------------------------------------------------------------------

def test_roundtrip_options(tmp_path: Path) -> None:
    """Options string with LaTeX special characters survives a save/load cycle unchanged."""
    original = _schematic_with_options()
    p = tmp_path / "options.hv"
    save(original, p)
    loaded = load(p)

    assert loaded.components[0].options == original.components[0].options


# ---------------------------------------------------------------------------
# test_load_unknown_version
# ---------------------------------------------------------------------------

def test_load_unknown_version(tmp_path: Path) -> None:
    """Loading a .hv file with an unrecognised version raises SchematicLoadError."""
    data = {"version": "99.0", "name": "x", "components": [], "wires": [], "metadata": {}}
    p = tmp_path / "bad_version.hv"
    p.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SchematicLoadError, match="version"):
        load(p)


# ---------------------------------------------------------------------------
# test_load_invalid_json
# ---------------------------------------------------------------------------

def test_load_invalid_json(tmp_path: Path) -> None:
    """Loading a malformed JSON file raises SchematicLoadError."""
    p = tmp_path / "corrupt.hv"
    p.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(SchematicLoadError, match="[Ii]nvalid JSON|JSON"):
        load(p)


# ---------------------------------------------------------------------------
# test_load_missing_field
# ---------------------------------------------------------------------------

def test_load_missing_field(tmp_path: Path) -> None:
    """Loading a JSON file missing a required field raises SchematicLoadError."""
    # Missing 'name'
    data = {"version": "0.1", "components": [], "wires": []}
    p = tmp_path / "missing_field.hv"
    p.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SchematicLoadError, match="[Mm]issing"):
        load(p)


# ---------------------------------------------------------------------------
# test_load_invalid_invariant
# ---------------------------------------------------------------------------

def test_load_invalid_invariant(tmp_path: Path) -> None:
    """Loading a file that violates an invariant (diagonal wire) raises SchematicLoadError."""
    data = {
        "version": "0.1",
        "name": "bad",
        "components": [],
        "wires": [
            {"id": _uid(), "points": [[0.0, 0.0], [1.0, 1.0]]}  # diagonal
        ],
        "metadata": {},
    }
    p = tmp_path / "diagonal.hv"
    p.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SchematicLoadError, match="invariant|diagonal|Manhattan"):
        load(p)


# ---------------------------------------------------------------------------
# test_save_creates_file
# ---------------------------------------------------------------------------

def test_save_creates_file(tmp_path: Path) -> None:
    """save() creates a file at the specified path."""
    p = tmp_path / "new.hv"
    assert not p.exists()
    save(_empty_schematic(), p)
    assert p.exists()
    assert p.stat().st_size > 0


def test_save_is_atomic_overwrite(tmp_path: Path) -> None:
    """save() replaces an existing file atomically and leaves no temp file behind."""
    p = tmp_path / "atomic.hv"

    first = _empty_schematic()
    first.name = "first"
    save(first, p)

    second = _empty_schematic()
    second.name = "second"
    save(second, p)

    # Target reflects the latest write...
    assert load(p).name == "second"
    # ...the sibling temp file used during the atomic replace is gone, and the
    # only extra artefact is the backup of the replaced file.
    assert not list(tmp_path.glob("*.tmp*"))
    assert {f.name for f in tmp_path.iterdir()} == {"atomic.hv", "atomic.hv.bak"}


def test_save_overwrite_keeps_backup_of_previous_file(tmp_path: Path) -> None:
    """Replacing an existing .hv leaves the prior contents in <name>.hv.bak."""
    p = tmp_path / "doc.hv"

    first = _empty_schematic()
    first.name = "first"
    save(first, p)
    assert not (tmp_path / "doc.hv.bak").exists()   # no backup on first save

    second = _empty_schematic()
    second.name = "second"
    save(second, p)

    backup = tmp_path / "doc.hv.bak"
    assert backup.exists()
    assert load(backup).name == "first"             # the replaced contents
    assert load(p).name == "second"


def test_save_invalid_schematic_raises_and_writes_nothing(tmp_path: Path) -> None:
    """save() of an invalid schematic raises SchematicSaveError and leaves the
    filesystem untouched (no destination, no temp file)."""
    from app.schematic.io import SchematicSaveError

    bad = Schematic(version="0.1", name="bad", wires=[
        Wire(id=_uid(), points=[(0.0, 0.0), (1.0, 1.0)]),   # diagonal
    ])
    p = tmp_path / "bad.hv"
    with pytest.raises(SchematicSaveError, match="invariant"):
        save(bad, p)
    assert list(tmp_path.iterdir()) == []           # nothing written at all


def test_save_does_not_clobber_existing_file_with_invalid_schematic(tmp_path: Path) -> None:
    """An invalid in-memory document can never overwrite a good file on disk."""
    from app.schematic.io import SchematicSaveError

    p = tmp_path / "doc.hv"
    good = _empty_schematic()
    good.name = "good"
    save(good, p)

    bad = Schematic(version="0.1", name="bad", wires=[
        Wire(id=_uid(), points=[]),                 # empty wire → invalid
    ])
    with pytest.raises(SchematicSaveError):
        save(bad, p)
    assert load(p).name == "good"                   # untouched


def test_save_cleans_up_tmp_file_on_failure(tmp_path: Path, monkeypatch) -> None:
    """If the final atomic replace fails, the temp file is removed."""
    import os as _os

    def _boom(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(_os, "replace", _boom)
    p = tmp_path / "doc.hv"
    with pytest.raises(OSError, match="simulated"):
        save(_empty_schematic(), p)
    assert list(tmp_path.iterdir()) == []           # tmp file cleaned up


# ---------------------------------------------------------------------------
# test_save_is_utf8
# ---------------------------------------------------------------------------

def test_save_is_utf8(tmp_path: Path) -> None:
    """Saved .hv files are valid UTF-8 and contain no byte-order mark."""
    schematic = _schematic_with_options()  # contains non-ASCII-safe LaTeX
    p = tmp_path / "utf8.hv"
    save(schematic, p)

    raw = p.read_bytes()
    # No UTF-8 BOM
    assert not raw.startswith(b"\xef\xbb\xbf"), "File must not start with a UTF-8 BOM"
    # Decodable as UTF-8
    text = raw.decode("utf-8")
    # Must be valid JSON
    parsed = json.loads(text)
    assert parsed["name"] == schematic.name


# ---------------------------------------------------------------------------
# label_offset round-trip
# ---------------------------------------------------------------------------

def test_roundtrip_label_offset(tmp_path: Path) -> None:
    """label_offset is serialised and restored correctly."""
    comp = Component(
        id=_uid(),
        kind="R",
        position=(0.0, 0.0),
        rotation=0,
        options="l=$R_1$",
        label_offset=(12.5, -30.0),
    )
    original = Schematic(version="0.1", name="lo_test", components=[comp])
    p = tmp_path / "lo.hv"
    save(original, p)
    loaded = load(p)

    assert loaded.components[0].label_offset == (12.5, -30.0)


def test_label_offset_none_not_serialised(tmp_path: Path) -> None:
    """When label_offset is None the key is omitted from the JSON."""
    comp = Component(
        id=_uid(),
        kind="R",
        position=(0.0, 0.0),
        rotation=0,
        options="",
        label_offset=None,
    )
    original = Schematic(version="0.1", name="lo_none", components=[comp])
    p = tmp_path / "lo_none.hv"
    save(original, p)
    raw = json.loads(p.read_text())
    assert "label_offset" not in raw["components"][0]


def test_label_offset_missing_loads_as_none(tmp_path: Path) -> None:
    """Old files without label_offset deserialise with label_offset=None."""
    data = {
        "version": "0.1",
        "name": "old",
        "components": [
            {
                "id": _uid(),
                "kind": "R",
                "position": [0.0, 0.0],
                "rotation": 0,
                "mirror": False,
                "options": "",
            }
        ],
        "wires": [],
        "metadata": {},
    }
    p = tmp_path / "old.hv"
    p.write_text(json.dumps(data), encoding="utf-8")
    loaded = load(p)
    assert loaded.components[0].label_offset is None


def test_label_offset_bad_type_raises(tmp_path: Path) -> None:
    """label_offset with wrong type raises SchematicLoadError."""
    data = {
        "version": "0.1",
        "name": "bad",
        "components": [
            {
                "id": _uid(),
                "kind": "R",
                "position": [0.0, 0.0],
                "rotation": 0,
                "mirror": False,
                "options": "",
                "label_offset": "not_a_list",
            }
        ],
        "wires": [],
        "metadata": {},
    }
    p = tmp_path / "bad.hv"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="label_offset"):
        load(p)


# ---------------------------------------------------------------------------
# BipoleComponent fill_color / line_width round-trip (+ legacy border_width load)
# ---------------------------------------------------------------------------

def test_bipole_fill_color_roundtrip(tmp_path: Path) -> None:
    """BipoleComponent.fill_color survives a save/load cycle."""
    from app.components.model import BipoleComponent
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, mirror=False, options="t=Test",
        span_override=(2.0, 0.0), fill_color="cyan!15",
    )
    s = Schematic(version="0.1", name="bipole-fill", components=[comp])
    p = tmp_path / "bipole_fill.hv"
    save(s, p)
    s2 = load(p)
    loaded = s2.components[0]
    assert isinstance(loaded, BipoleComponent)
    assert loaded.fill_color == "cyan!15"


def test_bipole_line_width_roundtrip(tmp_path: Path) -> None:
    """BipoleComponent.line_width (the unified outline width) survives save/load."""
    from app.components.model import BipoleComponent
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, mirror=False, options="",
        span_override=(2.0, 0.0), line_width=1.5,
    )
    s = Schematic(version="0.1", name="bipole-bw", components=[comp])
    p = tmp_path / "bipole_bw.hv"
    save(s, p)
    s2 = load(p)
    loaded = s2.components[0]
    assert isinstance(loaded, BipoleComponent)
    assert abs(loaded.line_width - 1.5) < 1e-6


def test_legacy_border_width_loads_as_line_width(tmp_path: Path) -> None:
    """A legacy file that stored a block's outline width under "border_width"
    (pre-unification) loads it into the unified `line_width` field."""
    from app.components.model import BipoleComponent
    data = {
        "version": "0.1",
        "name": "legacy-bw",
        "components": [
            {
                "id": _uid(), "kind": "bipole", "position": [0.0, 0.0],
                "rotation": 0, "mirror": False, "options": "t=Z",
                "span_override": [2.0, 0.0], "border_width": 1.5,
            }
        ],
        "wires": [], "metadata": {},
    }
    p = tmp_path / "legacy_bw.hv"
    p.write_text(json.dumps(data), encoding="utf-8")
    loaded = load(p).components[0]
    assert isinstance(loaded, BipoleComponent)
    assert abs(loaded.line_width - 1.5) < 1e-6


def test_bipole_defaults_not_saved(tmp_path: Path) -> None:
    """fill_color='' and line_width=0.4 are omitted from the saved JSON."""
    from app.components.model import BipoleComponent
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, mirror=False, options="",
        span_override=(1.0, 0.0),
    )
    s = Schematic(version="0.1", name="bipole-defaults", components=[comp])
    p = tmp_path / "bipole_defaults.hv"
    save(s, p)
    raw = json.loads(p.read_text())
    comp_dict = raw["components"][0]
    assert "fill_color" not in comp_dict
    assert "border_width" not in comp_dict
    assert "line_width" not in comp_dict


# ---------------------------------------------------------------------------
# StyledComponent fill / border / line_style round-trip + rect text handling
# ---------------------------------------------------------------------------

def test_rect_style_fields_roundtrip(tmp_path: Path) -> None:
    """RectComponent fill_color/line_width/line_style survive a save/load cycle."""
    from app.components.model import RectComponent
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, mirror=False, options="",
        span_override=(2.0, 2.0),
        fill_color="yellow!20", line_width=1.5, line_style="dashed",
    )
    s = Schematic(version="0.1", name="rect-style", components=[comp])
    p = tmp_path / "rect_style.hv"
    save(s, p)
    loaded = load(p).components[0]
    assert isinstance(loaded, RectComponent)
    assert loaded.fill_color == "yellow!20"
    assert abs(loaded.line_width - 1.5) < 1e-6
    assert loaded.line_style == "dashed"
    assert loaded.options == ""


def test_rect_text_roundtrip(tmp_path: Path) -> None:
    """A rect's centred text (options) and font fields survive save/load."""
    from app.components.model import RectComponent
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, mirror=False, options="$H(s)$",
        span_override=(4.0, 2.0),
        font_size=10.0, font_bold=True, font_family="sans",
    )
    s = Schematic(version="0.1", name="rect-text", components=[comp])
    p = tmp_path / "rect_text.hv"
    save(s, p)
    loaded = load(p).components[0]
    assert isinstance(loaded, RectComponent)
    # Text preserved verbatim (NOT parsed/stripped as a style string).
    assert loaded.options == "$H(s)$"
    assert abs(loaded.font_size - 10.0) < 1e-6
    assert loaded.font_bold is True
    assert loaded.font_family == "sans"


def test_rect_text_kept_verbatim_not_parsed_as_style(tmp_path: Path) -> None:
    """A rect's options text is loaded verbatim, never parsed as a draw-style string."""
    data = {
        "version": "0.1",
        "name": "rect-text",
        "components": [
            {
                "id": _uid(),
                "kind": "rect",
                "position": [0.0, 0.0],
                "rotation": 0,
                "mirror": False,
                "options": "Processor",
                "span_override": [3.0, 1.0],
            }
        ],
        "wires": [],
        "metadata": {},
    }
    p = tmp_path / "rect_text_02.hv"
    p.write_text(json.dumps(data), encoding="utf-8")

    from app.components.model import RectComponent
    loaded = load(p).components[0]
    assert isinstance(loaded, RectComponent)
    assert loaded.options == "Processor"   # kept, not parsed as style
    assert loaded.fill_color == ""
    assert loaded.line_style == ""


def test_bipole_line_style_roundtrip(tmp_path: Path) -> None:
    """BipoleComponent.line_style survives a save/load cycle (dashed border support)."""
    from app.components.model import BipoleComponent
    comp = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0),
        rotation=0, mirror=False, options="t=Test",
        span_override=(2.0, 0.0), line_style="dashed",
    )
    s = Schematic(version="0.1", name="bipole-ls", components=[comp])
    p = tmp_path / "bipole_ls.hv"
    save(s, p)
    loaded = load(p).components[0]
    assert isinstance(loaded, BipoleComponent)
    assert loaded.line_style == "dashed"


def test_component_scale_roundtrip(tmp_path: Path) -> None:
    """A logic gate's non-default scale survives save/load; the default (1.0) is
    omitted from the JSON, and a legacy gate file without `scale` loads at 1.0."""
    from app.components.model import Component
    gate = Component(id=_uid(), kind="and", position=(0.0, 0.0), rotation=0,
                     options="", params={"inputs": 3}, scale=0.5)
    plain = Component(id=_uid(), kind="or", position=(4.0, 0.0), rotation=0,
                      options="", params={"inputs": 2})  # scale defaults to 1.0
    s = Schematic(version="0.1", name="scale", components=[gate, plain])
    p = tmp_path / "scale.hv"
    save(s, p)
    raw = json.loads(p.read_text())["components"]
    assert raw[0]["scale"] == 0.5
    assert "scale" not in raw[1]                    # default omitted
    loaded = load(p).components
    assert abs(loaded[0].scale - 0.5) < 1e-9
    assert abs(loaded[1].scale - 1.0) < 1e-9        # absent → 1.0 (back-compat)


def test_styled_defaults_not_saved(tmp_path: Path) -> None:
    """Default fill_color/line_width/line_style are omitted from saved JSON."""
    from app.components.model import RectComponent
    comp = RectComponent(
        id=_uid(), kind="rect", position=(0.0, 0.0),
        rotation=0, mirror=False, options="", span_override=(2.0, 2.0),
    )
    s = Schematic(version="0.1", name="rect-defaults", components=[comp])
    p = tmp_path / "rect_defaults.hv"
    save(s, p)
    comp_dict = json.loads(p.read_text())["components"][0]
    assert "fill_color" not in comp_dict
    assert "border_width" not in comp_dict
    assert "line_width" not in comp_dict
    assert "line_style" not in comp_dict


def test_variant_roundtrip(tmp_path: Path) -> None:
    """An active variant (body_diode) survives a save/load cycle via the map."""
    comp = Component(
        id=_uid(), kind="nigfete", position=(0.0, 0.0),
        rotation=0, mirror=False, options="", variants={"body_diode": True},
    )
    s = Schematic(version="0.1", name="nmos-bd", components=[comp])
    p = tmp_path / "nmos_bd.hv"
    save(s, p)
    raw = json.loads(p.read_text())
    assert raw["components"][0]["variants"] == {"body_diode": True}
    loaded = load(p).components[0]
    assert loaded.variants.get("body_diode") is True


def test_variant_inactive_not_saved(tmp_path: Path) -> None:
    """A false/absent variant is omitted from the saved JSON."""
    comp = Component(
        id=_uid(), kind="nigfete", position=(0.0, 0.0),
        rotation=0, mirror=False, options="", variants={"body_diode": False},
    )
    s = Schematic(version="0.1", name="nmos-no-bd", components=[comp])
    p = tmp_path / "nmos_no_bd.hv"
    save(s, p)
    raw = json.loads(p.read_text())
    assert "variants" not in raw["components"][0]


def test_legacy_variant_keys_back_compat(tmp_path: Path) -> None:
    """Pre-variants .hv files (legacy `filled`/`body_diode` keys) still load."""
    p = tmp_path / "legacy.hv"
    p.write_text(json.dumps({
        "version": "0.1", "name": "legacy", "metadata": {},
        "components": [
            {"id": _uid(), "kind": "D", "position": [0, 0], "rotation": 0,
             "mirror": False, "options": "", "filled": True},
            {"id": _uid(), "kind": "nigfete", "position": [2, 0], "rotation": 0,
             "mirror": False, "options": "", "body_diode": True},
        ],
        "wires": [],
    }))
    comps = load(p).components
    assert comps[0].variants.get("filled") is True
    assert comps[1].variants.get("body_diode") is True


# ---------------------------------------------------------------------------
# Wire line_style / line_width round-trip
# ---------------------------------------------------------------------------

def test_roundtrip_wire_style(tmp_path: Path) -> None:
    """A wire's line_style and line_width are serialised and restored."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)],
             line_style="dashed", line_width=0.8)
    original = Schematic(version="0.1", name="ws", wires=[w])
    p = tmp_path / "ws.hv"
    save(original, p)
    loaded = load(p)
    assert loaded.wires[0].line_style == "dashed"
    assert loaded.wires[0].line_width == 0.8


def test_wire_default_style_not_serialised(tmp_path: Path) -> None:
    """Default style fields are omitted from the JSON (backward-compatible)."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)])
    save(Schematic(version="0.1", name="ws", wires=[w]), tmp_path / "ws.hv")
    raw = json.loads((tmp_path / "ws.hv").read_text())
    assert "line_style" not in raw["wires"][0]
    assert "line_width" not in raw["wires"][0]


def test_wire_missing_style_loads_defaults(tmp_path: Path) -> None:
    """Old files without wire style fields load with the defaults."""
    doc = {
        "version": "0.1", "name": "old", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]]}],
    }
    p = tmp_path / "old.hv"
    p.write_text(json.dumps(doc))
    loaded = load(p)
    assert loaded.wires[0].line_style == ""
    assert loaded.wires[0].line_width == 0.4


def test_wire_bad_style_type_raises(tmp_path: Path) -> None:
    doc = {
        "version": "0.1", "name": "bad", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "line_width": "wide"}],
    }
    p = tmp_path / "bad.hv"
    p.write_text(json.dumps(doc))
    with pytest.raises(SchematicLoadError, match="line_width"):
        load(p)


def test_roundtrip_wire_no_junction_dots(tmp_path: Path) -> None:
    """A wire's no_junction_dots flag round-trips through save+load."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)], no_junction_dots=True)
    save(Schematic(version="0.1", name="nj", wires=[w]), tmp_path / "nj.hv")
    loaded = load(tmp_path / "nj.hv")
    assert loaded.wires[0].no_junction_dots is True


def test_wire_no_junction_dots_default_omitted(tmp_path: Path) -> None:
    """The default (False) is omitted from the JSON; old files load as False."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)])
    save(Schematic(version="0.1", name="nj", wires=[w]), tmp_path / "nj.hv")
    raw = json.loads((tmp_path / "nj.hv").read_text())
    assert "no_junction_dots" not in raw["wires"][0]


def test_wire_no_junction_dots_bad_type_raises(tmp_path: Path) -> None:
    doc = {
        "version": "0.1", "name": "bad", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "no_junction_dots": "yes"}],
    }
    (tmp_path / "bad.hv").write_text(json.dumps(doc))
    with pytest.raises(SchematicLoadError, match="no_junction_dots"):
        load(tmp_path / "bad.hv")


def test_roundtrip_wire_no_termination_dots(tmp_path: Path) -> None:
    """A wire's no_termination_dots flag round-trips through save+load."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)], no_termination_dots=True)
    save(Schematic(version="0.1", name="nt", wires=[w]), tmp_path / "nt.hv")
    loaded = load(tmp_path / "nt.hv")
    assert loaded.wires[0].no_termination_dots is True


def test_wire_no_termination_dots_default_omitted(tmp_path: Path) -> None:
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)])
    save(Schematic(version="0.1", name="nt", wires=[w]), tmp_path / "nt.hv")
    raw = json.loads((tmp_path / "nt.hv").read_text())
    assert "no_termination_dots" not in raw["wires"][0]


def test_wire_no_termination_dots_bad_type_raises(tmp_path: Path) -> None:
    doc = {
        "version": "0.1", "name": "bad", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "no_termination_dots": 1}],
    }
    (tmp_path / "bad.hv").write_text(json.dumps(doc))
    with pytest.raises(SchematicLoadError, match="no_termination_dots"):
        load(tmp_path / "bad.hv")


def test_roundtrip_wire_markers(tmp_path: Path) -> None:
    """A wire's start_marker/end_marker survive a save+load cycle."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)],
             start_marker="arrow", end_marker="arrow")
    save(Schematic(version="0.1", name="mk", wires=[w]), tmp_path / "mk.hv")
    loaded = load(tmp_path / "mk.hv")
    assert loaded.wires[0].start_marker == "arrow"
    assert loaded.wires[0].end_marker == "arrow"


def test_wire_markers_default_omitted(tmp_path: Path) -> None:
    """Empty markers are omitted from the JSON; old files load with defaults."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)])
    save(Schematic(version="0.1", name="mk", wires=[w]), tmp_path / "mk.hv")
    raw = json.loads((tmp_path / "mk.hv").read_text())
    assert "start_marker" not in raw["wires"][0]
    assert "end_marker" not in raw["wires"][0]


def test_wire_marker_bad_type_raises(tmp_path: Path) -> None:
    doc = {
        "version": "0.1", "name": "bad", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "end_marker": 1}],
    }
    (tmp_path / "bad.hv").write_text(json.dumps(doc))
    with pytest.raises(SchematicLoadError, match="end_marker"):
        load(tmp_path / "bad.hv")


def test_roundtrip_wire_labels(tmp_path: Path) -> None:
    """A wire's start_label/end_label survive a save+load cycle."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)],
             start_label="in", end_label="$y(t)$")
    save(Schematic(version="0.1", name="lb", wires=[w]), tmp_path / "lb.hv")
    loaded = load(tmp_path / "lb.hv")
    assert loaded.wires[0].start_label == "in"
    assert loaded.wires[0].end_label == "$y(t)$"


def test_wire_labels_default_omitted(tmp_path: Path) -> None:
    """Empty labels are omitted from the JSON (back-compat)."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)])
    save(Schematic(version="0.1", name="lb", wires=[w]), tmp_path / "lb.hv")
    raw = json.loads((tmp_path / "lb.hv").read_text())
    assert "start_label" not in raw["wires"][0]
    assert "end_label" not in raw["wires"][0]


def test_roundtrip_wire_label_placement(tmp_path: Path) -> None:
    """A wire's start/end label placement survives a save+load cycle."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)],
             start_label="in", start_label_placement="above",
             end_label="$y$", end_label_placement="below")
    save(Schematic(version="0.1", name="lp", wires=[w]), tmp_path / "lp.hv")
    loaded = load(tmp_path / "lp.hv").wires[0]
    assert loaded.start_label_placement == "above"
    assert loaded.end_label_placement == "below"


def test_wire_label_placement_default_omitted(tmp_path: Path) -> None:
    """Default ('' = off-end) placement is omitted from the JSON (back-compat)."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)], end_label="$y$")
    save(Schematic(version="0.1", name="lp", wires=[w]), tmp_path / "lp.hv")
    raw = json.loads((tmp_path / "lp.hv").read_text())
    assert "start_label_placement" not in raw["wires"][0]
    assert "end_label_placement" not in raw["wires"][0]


def test_wire_label_placement_bad_type_raises(tmp_path: Path) -> None:
    doc = {
        "version": "0.1", "name": "bad", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "end_label_placement": 3}],
    }
    (tmp_path / "bad.hv").write_text(json.dumps(doc))
    with pytest.raises(SchematicLoadError, match="end_label_placement"):
        load(tmp_path / "bad.hv")


def test_wire_label_bad_type_raises(tmp_path: Path) -> None:
    doc = {
        "version": "0.1", "name": "bad", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "start_label": 5}],
    }
    (tmp_path / "bad.hv").write_text(json.dumps(doc))
    with pytest.raises(SchematicLoadError, match="start_label"):
        load(tmp_path / "bad.hv")


def test_roundtrip_wire_mid_label(tmp_path: Path) -> None:
    """A wire's mid_label and mid_label_pos survive a save+load cycle."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)],
             mid_label="$V_{bus}$", mid_label_pos=0.25)
    save(Schematic(version="0.1", name="m", wires=[w]), tmp_path / "m.hv")
    loaded = load(tmp_path / "m.hv")
    assert loaded.wires[0].mid_label == "$V_{bus}$"
    assert loaded.wires[0].mid_label_pos == 0.25


def test_wire_mid_label_defaults_omitted(tmp_path: Path) -> None:
    """Empty mid_label and the default 0.5 position are omitted from JSON."""
    w = Wire(id=_uid(), points=[(0.0, 0.0), (4.0, 0.0)])
    save(Schematic(version="0.1", name="m", wires=[w]), tmp_path / "m.hv")
    raw = json.loads((tmp_path / "m.hv").read_text())
    assert "mid_label" not in raw["wires"][0]
    assert "mid_label_pos" not in raw["wires"][0]


def test_wire_mid_label_pos_clamped_on_load(tmp_path: Path) -> None:
    doc = {
        "version": "0.1", "name": "m", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "mid_label": "x", "mid_label_pos": 1.5}],
    }
    (tmp_path / "m.hv").write_text(json.dumps(doc))
    assert load(tmp_path / "m.hv").wires[0].mid_label_pos == 1.0


def test_wire_mid_label_pos_bad_type_raises(tmp_path: Path) -> None:
    doc = {
        "version": "0.1", "name": "bad", "components": [],
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "mid_label_pos": "halfway"}],
    }
    (tmp_path / "bad.hv").write_text(json.dumps(doc))
    with pytest.raises(SchematicLoadError, match="mid_label_pos"):
        load(tmp_path / "bad.hv")


# ---------------------------------------------------------------------------
# Wire z_order round-trip (line-hop layering)
# ---------------------------------------------------------------------------

def test_roundtrip_wire_z_order(tmp_path: Path) -> None:
    """A wire's non-zero z_order survives a save/load cycle; default stays absent."""
    plain = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)])
    layered = Wire(id=_uid(), points=[(0.0, 1.0), (2.0, 1.0)], z_order=-3)
    original = Schematic(version="0.1", name="z", wires=[plain, layered])
    p = tmp_path / "z.hv"
    save(original, p)

    # Default z_order is not persisted (keeps plain wires' JSON minimal).
    raw = json.loads(p.read_text())
    assert "z_order" not in raw["wires"][0]
    assert raw["wires"][1]["z_order"] == -3

    loaded = load(p)
    assert loaded.wires[0].z_order == 0
    assert loaded.wires[1].z_order == -3


def test_wire_z_order_wrong_type_raises(tmp_path: Path) -> None:
    """A non-integer wire z_order raises SchematicLoadError."""
    data = {
        "version": "0.1",
        "name": "bad",
        "components": [],
        "wires": [{"id": _uid(), "points": [[0, 0], [2, 0]], "z_order": "high"}],
        "metadata": {},
    }
    p = tmp_path / "bad.hv"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="z_order"):
        load(p)


def test_roundtrip_wire_hop_mode(tmp_path: Path) -> None:
    """A wire's hop_mode round-trips; the default "" is omitted from JSON."""
    plain = Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)])
    never = Wire(id=_uid(), points=[(0.0, 1.0), (2.0, 1.0)], hop_mode="never")
    always = Wire(id=_uid(), points=[(0.0, 2.0), (2.0, 2.0)], hop_mode="always")
    p = tmp_path / "hm.hv"
    save(Schematic(version="0.1", name="hm", wires=[plain, never, always]), p)
    raw = json.loads(p.read_text())
    assert "hop_mode" not in raw["wires"][0]
    assert raw["wires"][1]["hop_mode"] == "never"
    assert raw["wires"][2]["hop_mode"] == "always"
    loaded = load(p)
    assert [w.hop_mode for w in loaded.wires] == ["", "never", "always"]


def test_wire_hop_mode_invalid_raises(tmp_path: Path) -> None:
    data = {
        "version": "0.1", "name": "bad", "components": [],
        "wires": [{"id": _uid(), "points": [[0, 0], [2, 0]], "hop_mode": "sometimes"}],
        "metadata": {},
    }
    p = tmp_path / "bad.hv"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="hop_mode"):
        load(p)


# ---------------------------------------------------------------------------
# Component stroke width (line_width) round-trip
# ---------------------------------------------------------------------------

def test_component_line_width_roundtrip(tmp_path: Path) -> None:
    """A component's stroke width is serialised and restored; the default is omitted."""
    from app.components.model import Component
    wide = Component(id=_uid(), kind="R", position=(0.0, 0.0), rotation=0,
                     options="", line_width=0.9)
    plain = Component(id=_uid(), kind="R", position=(2.0, 0.0), rotation=0, options="")
    p = tmp_path / "lw.hv"
    save(Schematic(version="0.1", name="lw", components=[wide, plain]), p)
    raw = json.loads(p.read_text())
    assert raw["components"][0]["line_width"] == 0.9
    assert "line_width" not in raw["components"][1]   # default omitted
    loaded = load(p)
    assert loaded.components[0].line_width == 0.9
    assert loaded.components[1].line_width == 0.4


# ---------------------------------------------------------------------------
# Robustness — non-finite numbers, raw-exception wrapping, size bound,
# degenerate wires, type strictness, format version (audit fixes)
# ---------------------------------------------------------------------------

def test_load_nan_literal_in_position_raises_load_error(tmp_path: Path) -> None:
    """A NaN literal in a coordinate raises SchematicLoadError, not a crash."""
    p = tmp_path / "nan.hv"
    p.write_text(
        '{"version": "0.1", "name": "n", "wires": [], "metadata": {}, '
        '"components": [{"id": "c1", "kind": "R", "position": [NaN, 0], '
        '"rotation": 0, "mirror": false, "options": ""}]}',
        encoding="utf-8",
    )
    with pytest.raises(SchematicLoadError, match="non-finite|NaN"):
        load(p)


def test_load_infinity_literal_in_wire_points_raises_load_error(tmp_path: Path) -> None:
    """Infinity/-Infinity literals in wire points raise SchematicLoadError."""
    p = tmp_path / "inf.hv"
    p.write_text(
        '{"version": "0.1", "name": "i", "components": [], "metadata": {}, '
        '"wires": [{"id": "w1", "points": [[0, 0], [Infinity, 0]]}]}',
        encoding="utf-8",
    )
    with pytest.raises(SchematicLoadError, match="non-finite|Infinity"):
        load(p)


def test_load_nan_via_string_coercion_raises_load_error(tmp_path: Path) -> None:
    """A numeric field given as the string "nan" (which float() accepts) is
    still rejected with SchematicLoadError by the finiteness check."""
    doc = {
        "version": "0.1", "name": "n", "components": [], "metadata": {},
        "wires": [{"id": _uid(), "points": [[0.0, 0.0], [2.0, 0.0]],
                   "line_width": "nan"}],
    }
    p = tmp_path / "nanstr.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="finite"):
        load(p)


def test_load_bad_font_size_raises_load_error_not_value_error(tmp_path: Path) -> None:
    """font_size "12pt" raises SchematicLoadError, not a raw ValueError."""
    doc = {
        "version": "0.1", "name": "f", "wires": [], "metadata": {},
        "components": [{
            "id": _uid(), "kind": "rect", "position": [0.0, 0.0],
            "rotation": 0, "mirror": False, "options": "x",
            "span_override": [2.0, 1.0], "font_size": "12pt",
        }],
    }
    p = tmp_path / "fontsize.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="font_size"):
        load(p)


def test_load_never_leaks_a_raw_exception(tmp_path: Path) -> None:
    """Any malformed value the field checks miss is re-raised as
    SchematicLoadError (catch-all wrapper around parse/convert/validate)."""
    # metadata is accepted as any dict; a params dict with a list value raises
    # TypeError inside int() — the wrapper must convert it.
    doc = {
        "version": "0.1", "name": "x", "wires": [], "metadata": {},
        "components": [{
            "id": _uid(), "kind": "and", "position": [0.0, 0.0],
            "rotation": 0, "mirror": False, "options": "",
            "params": {"inputs": [1, 2]},
        }],
    }
    p = tmp_path / "weird.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchematicLoadError):
        load(p)


def test_load_rejects_oversized_file(tmp_path: Path, monkeypatch) -> None:
    """A .hv file above the size bound is rejected before parsing."""
    import app.schematic.io as io

    monkeypatch.setattr(io, "_MAX_FILE_BYTES", 64)
    p = tmp_path / "big.hv"
    p.write_text('{"version": "0.1", "name": "' + "x" * 200 + '", '
                 '"components": [], "wires": []}', encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="too large"):
        load(p)


def test_load_empty_points_wire_raises_load_error(tmp_path: Path) -> None:
    """A wire with an empty points list fails the >=2-points invariant."""
    doc = {
        "version": "0.1", "name": "w", "components": [], "metadata": {},
        "wires": [{"id": _uid(), "points": []}],
    }
    p = tmp_path / "empty_wire.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="at least two points"):
        load(p)


def test_load_single_point_wire_raises_load_error(tmp_path: Path) -> None:
    """A wire with a single point fails the >=2-points invariant."""
    doc = {
        "version": "0.1", "name": "w", "components": [], "metadata": {},
        "wires": [{"id": _uid(), "points": [[1.0, 1.0]]}],
    }
    p = tmp_path / "one_point_wire.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="at least two points"):
        load(p)


def test_rotation_integral_float_accepted(tmp_path: Path) -> None:
    """rotation 90.0 (a JSON float that is integral) is coerced to int 90."""
    doc = {
        "version": "0.1", "name": "r", "wires": [], "metadata": {},
        "components": [{"id": _uid(), "kind": "R", "position": [0.0, 0.0],
                        "rotation": 90.0, "mirror": False, "options": ""}],
    }
    p = tmp_path / "rot.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    loaded = load(p).components[0]
    assert loaded.rotation == 90
    assert isinstance(loaded.rotation, int)


def test_rotation_non_integral_float_rejected(tmp_path: Path) -> None:
    """rotation 45.5 raises SchematicLoadError."""
    doc = {
        "version": "0.1", "name": "r", "wires": [], "metadata": {},
        "components": [{"id": _uid(), "kind": "R", "position": [0.0, 0.0],
                        "rotation": 45.5, "mirror": False, "options": ""}],
    }
    p = tmp_path / "rot_bad.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="rotation"):
        load(p)


def test_rotation_bool_rejected(tmp_path: Path) -> None:
    """rotation true (a JSON boolean) raises SchematicLoadError."""
    doc = {
        "version": "0.1", "name": "r", "wires": [], "metadata": {},
        "components": [{"id": _uid(), "kind": "R", "position": [0.0, 0.0],
                        "rotation": True, "mirror": False, "options": ""}],
    }
    p = tmp_path / "rot_bool.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="rotation"):
        load(p)


def test_component_z_order_bool_rejected(tmp_path: Path) -> None:
    """A boolean component z_order is rejected (bool is not an integer here),
    matching the wire path's strictness."""
    doc = {
        "version": "0.1", "name": "z", "wires": [], "metadata": {},
        "components": [{"id": _uid(), "kind": "rect", "position": [0.0, 0.0],
                        "rotation": 0, "mirror": False, "options": "",
                        "span_override": [2.0, 1.0], "z_order": True}],
    }
    p = tmp_path / "z_bool.hv"
    p.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="z_order"):
        load(p)


def test_format_version_03_roundtrips_and_old_versions_load(tmp_path: Path) -> None:
    """save() writes version 0.3; files declaring 0.1, 0.2 and 0.3 all load."""
    p = tmp_path / "v.hv"
    save(_empty_schematic(), p)
    assert json.loads(p.read_text(encoding="utf-8"))["version"] == "0.3"
    assert load(p).version == "0.3"

    for old in ("0.1", "0.2"):
        q = tmp_path / f"v{old}.hv"
        q.write_text(
            json.dumps({"version": old, "name": "old",
                        "components": [], "wires": []}),
            encoding="utf-8",
        )
        assert load(q).version == old
