"""
Schematic file I/O.

save(schematic, path) — serializes a Schematic to a UTF-8 JSON .ctikz file.
load(path)            — deserializes and validates a .ctikz file, returning a
                        Schematic or raising SchematicLoadError on any problem.

No Qt dependency. No side effects beyond filesystem access.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.schematic.model import Component, Schematic, Wire
from app.schematic.validate import validate

# Spec versions this loader accepts. Extend when new versions are defined.
_KNOWN_VERSIONS: set[str] = {"0.1"}


class SchematicLoadError(Exception):
    """Raised when a .ctikz file cannot be loaded for any reason."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save(schematic: Schematic, path: str | Path) -> None:
    """Serialise *schematic* to a UTF-8 JSON file at *path*.

    The file is written atomically (written to a temp name then renamed) so a
    failed write never corrupts an existing file.
    """
    path = Path(path)
    data = _schematic_to_dict(schematic)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    # Write without BOM; explicitly UTF-8.
    path.write_text(text, encoding="utf-8")


def load(path: str | Path) -> Schematic:
    """Load and validate a .ctikz file.

    Raises SchematicLoadError with a descriptive message on any problem:
    - malformed JSON
    - missing required fields
    - unknown version
    - invariant violations
    """
    path = Path(path)

    # 1. Read raw bytes and decode as UTF-8.
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchematicLoadError(f"Cannot read file: {exc}") from exc

    # 2. Parse JSON.
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SchematicLoadError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise SchematicLoadError("Top-level JSON value must be an object")

    # 3. Validate schema structure and build the model objects.
    schematic = _dict_to_schematic(data)

    # 4. Validate schematic invariants.
    errors = validate(schematic)
    if errors:
        raise SchematicLoadError(
            f"Schematic invariant violated: {errors[0]}"
            + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else "")
        )

    return schematic


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _schematic_to_dict(s: Schematic) -> dict[str, Any]:
    return {
        "version": s.version,
        "name": s.name,
        "components": [_component_to_dict(c) for c in s.components],
        "wires": [_wire_to_dict(w) for w in s.wires],
        "metadata": s.metadata,
    }


def _component_to_dict(c: Component) -> dict[str, Any]:
    return {
        "id": c.id,
        "kind": c.kind,
        "position": list(c.position),
        "rotation": c.rotation,
        "mirror": c.mirror,
        "labels": c.labels,
    }


def _wire_to_dict(w: Wire) -> dict[str, Any]:
    return {
        "id": w.id,
        "points": [list(pt) for pt in w.points],
    }


# ---------------------------------------------------------------------------
# Deserialisation helpers
# ---------------------------------------------------------------------------

def _require(data: dict, key: str, context: str) -> Any:
    """Return data[key] or raise SchematicLoadError if missing."""
    if key not in data:
        raise SchematicLoadError(f"Missing required field '{key}' in {context}")
    return data[key]


def _dict_to_schematic(data: dict) -> Schematic:
    version = _require(data, "version", "schematic")
    if not isinstance(version, str):
        raise SchematicLoadError("Field 'version' must be a string")
    if version not in _KNOWN_VERSIONS:
        raise SchematicLoadError(
            f"Unknown schematic version '{version}'. "
            f"Supported versions: {sorted(_KNOWN_VERSIONS)}"
        )

    name = _require(data, "name", "schematic")
    if not isinstance(name, str):
        raise SchematicLoadError("Field 'name' must be a string")

    raw_components = _require(data, "components", "schematic")
    if not isinstance(raw_components, list):
        raise SchematicLoadError("Field 'components' must be an array")
    components = [_dict_to_component(c, i) for i, c in enumerate(raw_components)]

    raw_wires = _require(data, "wires", "schematic")
    if not isinstance(raw_wires, list):
        raise SchematicLoadError("Field 'wires' must be an array")
    wires = [_dict_to_wire(w, i) for i, w in enumerate(raw_wires)]

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise SchematicLoadError("Field 'metadata' must be an object")

    return Schematic(
        version=version,
        name=name,
        components=components,
        wires=wires,
        metadata=metadata,
    )


def _dict_to_component(data: Any, index: int) -> Component:
    ctx = f"components[{index}]"
    if not isinstance(data, dict):
        raise SchematicLoadError(f"{ctx} must be an object")

    comp_id = _require(data, "id", ctx)
    kind    = _require(data, "kind", ctx)
    pos_raw = _require(data, "position", ctx)
    rot_raw = _require(data, "rotation", ctx)

    if not isinstance(comp_id, str):
        raise SchematicLoadError(f"{ctx}.id must be a string")
    if not isinstance(kind, str):
        raise SchematicLoadError(f"{ctx}.kind must be a string")
    if not (isinstance(pos_raw, list) and len(pos_raw) == 2):
        raise SchematicLoadError(f"{ctx}.position must be a two-element array")
    if not isinstance(rot_raw, int):
        raise SchematicLoadError(f"{ctx}.rotation must be an integer")

    try:
        position = (float(pos_raw[0]), float(pos_raw[1]))
    except (TypeError, ValueError) as exc:
        raise SchematicLoadError(f"{ctx}.position values must be numbers") from exc

    mirror = bool(data.get("mirror", False))
    labels = data.get("labels", {})
    if not isinstance(labels, dict):
        raise SchematicLoadError(f"{ctx}.labels must be an object")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in labels.items()):
        raise SchematicLoadError(f"{ctx}.labels keys and values must all be strings")

    return Component(
        id=comp_id,
        kind=kind,
        position=position,
        rotation=rot_raw,
        mirror=mirror,
        labels=labels,
    )


def _dict_to_wire(data: Any, index: int) -> Wire:
    ctx = f"wires[{index}]"
    if not isinstance(data, dict):
        raise SchematicLoadError(f"{ctx} must be an object")

    wire_id  = _require(data, "id", ctx)
    pts_raw  = _require(data, "points", ctx)

    if not isinstance(wire_id, str):
        raise SchematicLoadError(f"{ctx}.id must be a string")
    if not isinstance(pts_raw, list):
        raise SchematicLoadError(f"{ctx}.points must be an array")

    points: list[tuple[float, float]] = []
    for j, pt in enumerate(pts_raw):
        if not (isinstance(pt, list) and len(pt) == 2):
            raise SchematicLoadError(f"{ctx}.points[{j}] must be a two-element array")
        try:
            points.append((float(pt[0]), float(pt[1])))
        except (TypeError, ValueError) as exc:
            raise SchematicLoadError(
                f"{ctx}.points[{j}] values must be numbers"
            ) from exc

    return Wire(id=wire_id, points=points)
