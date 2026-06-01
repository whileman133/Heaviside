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
    for i, (kind, defn) in enumerate(REGISTRY.items()):
        components.append(
            defn.component_class(
                id=_uid(),
                kind=kind,
                position=(float(i * 3), 0.0),
                rotation=0,
                options=f"l=$X_{{{i}}}$",
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
    """Save and reload an empty schematic — loaded schematic equals original."""
    original = _empty_schematic()
    p = tmp_path / "empty.ctikz"
    save(original, p)
    loaded = load(p)

    assert loaded.version == original.version
    assert loaded.name == original.name
    assert loaded.components == original.components
    assert loaded.wires == original.wires
    assert loaded.metadata == original.metadata


# ---------------------------------------------------------------------------
# test_roundtrip_components
# ---------------------------------------------------------------------------

def test_roundtrip_components(tmp_path: Path) -> None:
    """Save and reload a schematic with one of each v1 component — all fields preserved."""
    original = _one_of_each()
    p = tmp_path / "all_kinds.ctikz"
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


# ---------------------------------------------------------------------------
# test_roundtrip_wires
# ---------------------------------------------------------------------------

def test_roundtrip_wires(tmp_path: Path) -> None:
    """Save and reload a schematic containing wires — all wire points preserved."""
    original = _schematic_with_wires()
    p = tmp_path / "wires.ctikz"
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
    p = tmp_path / "options.ctikz"
    save(original, p)
    loaded = load(p)

    assert loaded.components[0].options == original.components[0].options


def test_roundtrip_legacy_labels_migration(tmp_path: Path) -> None:
    """Old files with a 'labels' dict are loaded and migrated to an options string."""
    data = {
        "version": "0.1",
        "name": "legacy",
        "components": [
            {
                "id": "abc",
                "kind": "R",
                "position": [0.0, 0.0],
                "rotation": 0,
                "mirror": False,
                "labels": {"l": "$R_1$", "v": "$V$"},
            }
        ],
        "wires": [],
        "metadata": {},
    }
    p = tmp_path / "legacy.ctikz"
    p.write_text(json.dumps(data), encoding="utf-8")
    loaded = load(p)
    # Both slot=value pairs must appear in the migrated string.
    assert "l=$R_1$" in loaded.components[0].options
    assert "v=$V$" in loaded.components[0].options


# ---------------------------------------------------------------------------
# test_load_unknown_version
# ---------------------------------------------------------------------------

def test_load_unknown_version(tmp_path: Path) -> None:
    """Loading a .ctikz file with an unrecognised version raises SchematicLoadError."""
    data = {"version": "99.0", "name": "x", "components": [], "wires": [], "metadata": {}}
    p = tmp_path / "bad_version.ctikz"
    p.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SchematicLoadError, match="version"):
        load(p)


# ---------------------------------------------------------------------------
# test_load_invalid_json
# ---------------------------------------------------------------------------

def test_load_invalid_json(tmp_path: Path) -> None:
    """Loading a malformed JSON file raises SchematicLoadError."""
    p = tmp_path / "corrupt.ctikz"
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
    p = tmp_path / "missing_field.ctikz"
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
    p = tmp_path / "diagonal.ctikz"
    p.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SchematicLoadError, match="invariant|diagonal|Manhattan"):
        load(p)


# ---------------------------------------------------------------------------
# test_save_creates_file
# ---------------------------------------------------------------------------

def test_save_creates_file(tmp_path: Path) -> None:
    """save() creates a file at the specified path."""
    p = tmp_path / "new.ctikz"
    assert not p.exists()
    save(_empty_schematic(), p)
    assert p.exists()
    assert p.stat().st_size > 0


# ---------------------------------------------------------------------------
# test_save_is_utf8
# ---------------------------------------------------------------------------

def test_save_is_utf8(tmp_path: Path) -> None:
    """Saved .ctikz files are valid UTF-8 and contain no byte-order mark."""
    schematic = _schematic_with_options()  # contains non-ASCII-safe LaTeX
    p = tmp_path / "utf8.ctikz"
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
    p = tmp_path / "lo.ctikz"
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
    p = tmp_path / "lo_none.ctikz"
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
    p = tmp_path / "old.ctikz"
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
    p = tmp_path / "bad.ctikz"
    p.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(SchematicLoadError, match="label_offset"):
        load(p)
