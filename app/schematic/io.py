"""
Schematic file I/O.

save(schematic, path) — serializes a Schematic to a UTF-8 JSON .hv file.
load(path)            — deserializes and validates a .hv file, returning a
                        Schematic or raising SchematicLoadError on any problem.

No Qt dependency. No side effects beyond filesystem access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.components.model import (
    Component,
    DiodeComponent,
    DrawingComponent,
    FontedComponent,
    MosfetComponent,
    RectComponent,
    StyledComponent,
    TextNodeComponent,
)
from app.components.registry import REGISTRY
from app.components.style import parse_style_options
from app.schematic.model import Schematic, Wire
from app.schematic.validate import validate

# Spec versions this loader accepts. Extend when new versions are defined.
_KNOWN_VERSIONS: set[str] = {"0.1"}


class SchematicLoadError(Exception):
    """Raised when a .hv file cannot be loaded for any reason."""


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
    # Write to a sibling temp file, then atomically replace the target so a
    # failed/interrupted write never corrupts an existing file. Write without
    # BOM; explicitly UTF-8.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def load(path: str | Path) -> Schematic:
    """Load and validate a .hv file.

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
    d: dict[str, Any] = {
        "id": c.id,
        "kind": c.kind,
        "position": list(c.position),
        "rotation": c.rotation,
        "mirror": c.mirror,
        "options": c.options,
    }
    if c.label_offset is not None:
        d["label_offset"] = list(c.label_offset)
    if c.span_override is not None:
        d["span_override"] = list(c.span_override)
    if isinstance(c, DrawingComponent) and c.z_order != 0:
        d["z_order"] = c.z_order
    if isinstance(c, DiodeComponent) and c.filled:
        d["filled"] = True
    if isinstance(c, MosfetComponent) and c.body_diode:
        d["body_diode"] = True
    if isinstance(c, StyledComponent):
        if c.fill_color:
            d["fill_color"] = c.fill_color
        if abs(c.border_width - 0.4) > 1e-6:
            d["border_width"] = c.border_width
        if c.line_style:
            d["line_style"] = c.line_style
    if isinstance(c, FontedComponent):
        # Omit fields equal to the class-level default to keep files compact.
        cls_default_size = type(c).__dataclass_fields__["font_size"].default
        if c.font_size != cls_default_size:
            d["font_size"] = c.font_size
        if c.font_bold:
            d["font_bold"] = True
        if c.font_italic:
            d["font_italic"] = True
        if c.font_family:
            d["font_family"] = c.font_family
    return d


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

    if "options" in data:
        options = data["options"]
        if not isinstance(options, str):
            raise SchematicLoadError(f"{ctx}.options must be a string")
    elif "labels" in data:
        # Migrate v0.1 files that stored a labels dict instead of an options string.
        old_labels = data["labels"]
        if not isinstance(old_labels, dict):
            raise SchematicLoadError(f"{ctx}.labels must be an object")
        options = ", ".join(
            f"{k}={v}" for k, v in old_labels.items() if isinstance(v, str) and v
        )
    else:
        options = ""

    label_offset: tuple[float, float] | None = None
    raw_lo = data.get("label_offset")
    if raw_lo is not None:
        if not (isinstance(raw_lo, list) and len(raw_lo) == 2):
            raise SchematicLoadError(f"{ctx}.label_offset must be a two-element array")
        try:
            label_offset = (float(raw_lo[0]), float(raw_lo[1]))
        except (TypeError, ValueError) as exc:
            raise SchematicLoadError(
                f"{ctx}.label_offset values must be numbers"
            ) from exc

    span_override: tuple[float, float] | None = None
    raw_so = data.get("span_override")
    if raw_so is not None:
        if not (isinstance(raw_so, list) and len(raw_so) == 2):
            raise SchematicLoadError(f"{ctx}.span_override must be a two-element array")
        try:
            span_override = (float(raw_so[0]), float(raw_so[1]))
        except (TypeError, ValueError) as exc:
            raise SchematicLoadError(
                f"{ctx}.span_override values must be numbers"
            ) from exc

    defn = REGISTRY.get(kind)
    cls = defn.component_class if defn is not None else Component

    kwargs: dict = {
        "id": comp_id,
        "kind": kind,
        "position": position,
        "rotation": rot_raw,
        "mirror": mirror,
        "options": options,
        "label_offset": label_offset,
        "span_override": span_override,
    }

    if issubclass(cls, DrawingComponent):
        raw_z = data.get("z_order", 0)
        if not isinstance(raw_z, int):
            raise SchematicLoadError(f"{ctx}.z_order must be an integer")
        kwargs["z_order"] = raw_z

    if issubclass(cls, DiodeComponent):
        kwargs["filled"] = bool(data.get("filled", False))

    if issubclass(cls, MosfetComponent):
        kwargs["body_diode"] = bool(data.get("body_diode", False))

    if issubclass(cls, StyledComponent):
        has_style_fields = any(
            k in data for k in ("fill_color", "border_width", "line_style")
        )
        if issubclass(cls, RectComponent) and not has_style_fields and options:
            # Migrate legacy rect files that stored the style in the options
            # string; promote it into the StyledComponent fields and clear
            # options (which is unused for rects in the field-based format).
            fill, bw, ls = parse_style_options(options)
            kwargs["fill_color"] = fill
            kwargs["border_width"] = bw
            kwargs["line_style"] = ls
            kwargs["options"] = ""
        else:
            kwargs["fill_color"] = str(data.get("fill_color", ""))
            raw_bw = data.get("border_width", 0.4)
            try:
                kwargs["border_width"] = float(raw_bw)
            except (TypeError, ValueError) as exc:
                raise SchematicLoadError(f"{ctx}.border_width must be a number") from exc
            kwargs["line_style"] = str(data.get("line_style", ""))

    if issubclass(cls, FontedComponent):
        raw_ff = data.get("font_family", "")
        if not isinstance(raw_ff, str):
            raise SchematicLoadError(f"{ctx}.font_family must be a string")
        kwargs["font_bold"] = bool(data.get("font_bold", False))
        kwargs["font_italic"] = bool(data.get("font_italic", False))
        kwargs["font_family"] = raw_ff
        if "font_size" in data:
            kwargs["font_size"] = float(data["font_size"])
        elif issubclass(cls, TextNodeComponent) and span_override is not None:
            # Migrate: very old text_node files stored font_size in span_override[0].
            kwargs["font_size"] = span_override[0]
            kwargs["span_override"] = None

    return cls(**kwargs)


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
