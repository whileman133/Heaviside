"""
Schematic file I/O.

save(schematic, path) — validates then serializes a Schematic to a UTF-8 JSON
                        .hv file, raising SchematicSaveError if the in-memory
                        document is invalid (nothing is written).
load(path)            — deserializes and validates a .hv file, returning a
                        Schematic or raising SchematicLoadError on any problem.

No Qt dependency. No side effects beyond filesystem access.
"""

from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path
from typing import Any

from app.components.model import (
    Component,
    FontedComponent,
    StyledComponent,
)
from app.components.registry import REGISTRY
from app.components.style import split_top_level

# Power-rail kinds whose pre-0.6 ``l=`` label is migrated into ``node_text`` on
# load (mirrors ``app.codegen.circuitikz._POWER_RAIL_KINDS``; kept local so I/O has
# no dependency on the code generator).
_LEGACY_POWER_RAIL_KINDS: frozenset[str] = frozenset({"vcc", "vdd", "vee", "vss"})


def _split_off_l_slot(options: str) -> tuple[str, str]:
    """Return ``(l_value, remaining_options)`` for a legacy migration: pull the
    first ``l=`` slot's value out of *options* (comma-aware so a value containing
    commas inside ``$…$``/``{…}`` is not truncated) and return the rest joined back.
    ``("", options)`` when there is no ``l=`` slot."""
    l_value = ""
    kept: list[str] = []
    for seg in split_top_level(options):
        key, eq, val = seg.partition("=")
        if not l_value and eq and key.strip() == "l" and val.strip():
            l_value = val.strip()
        elif seg.strip():
            kept.append(seg.strip())
    return l_value, ", ".join(kept)
from app.schematic.model import LABEL_STYLES, WIRE_HOP_MODES, Schematic, Wire
from app.schematic.validate import validate

# File-format version written by the current code.  Distinct from the spec
# version.  "0.1" is the initial (pre-1.0) format: the on-disk shape is not yet
# stable and may change between early releases without migration support. There
# are no earlier formats to migrate from.
#: 0.2 added the top-level ``config`` object (document voltage/current label
#: styles). 0.1 files load unchanged with american defaults.
#: 0.3 covers the optional wire/component fields added since 0.2 (start_marker,
#: end_marker, start/end/mid labels and placements, hop_mode, z_order,
#: line_width, scale, params, variants, span_override) so an older build that
#: would silently strip them refuses the file instead. 0.1/0.2 files load
#: unchanged.
#: 0.4 extends ``z_order`` from drawing annotations to *every* component, so any
#: component can be sent to front/back. A 0.3 build would silently strip a plain
#: component's z_order on save, so the bump refuses the newer file; 0.1–0.3 files
#: load unchanged (absent z_order defaults to 0).
#: 0.5 adds the document preamble settings to ``config`` (``siunitx`` flag and a
#: free-form ``preamble`` string). A 0.4 build would silently strip them on save,
#: so the bump refuses the newer file; 0.1–0.4 files load unchanged (both default
#: to off / empty).
#: 0.6 adds a per-component ``node_text`` (the ``{…}`` slot of a node-style
#: component). A 0.5 build would silently strip it on save, so the bump refuses the
#: newer file; 0.1–0.5 files load unchanged (absent node_text defaults to empty,
#: and a legacy power-rail ``l=`` slot is migrated into it on load).
#: 0.7 adds the document ``symbol_style`` map to ``config`` (per-family symbol style:
#: american/european resistors, cute/american/european inductors — manual library). A
#: 0.6 build would silently strip it on save, so the bump refuses the newer file;
#: 0.1–0.6 files load unchanged (absent symbol_style defaults to all-american).
#: 0.8 adds a per-component ``node_side`` (a single-terminal node's placement keyword:
#: left/right/above/below — the user-set inversion-bubble side, replacing the former
#: gate-context inference). A 0.7 build would silently strip it on save, so the bump
#: refuses the newer file; 0.1–0.7 files load unchanged (absent node_side defaults to
#: empty = centred).
#: 0.9 adds three document fields to ``config``: ``mark_unconnected_pins`` and
#: ``line_hops`` (the display options moved out of app Preferences) and ``diode_scale``
#: (the CircuiTikZ ``diodes/scale`` body size). A 0.8 build would silently strip them on
#: save, so the bump refuses the newer file; 0.1–0.8 files load unchanged (defaults:
#: mark_unconnected_pins off, line_hops on, diode_scale 0.8).
#: 0.10 adds two document fields to ``config``: ``mark_open_ends`` (draw open-circle
#: ``ocirc`` terminals at dangling wire ends) and ``mark_junctions`` (draw solid
#: ``circ`` dots at wire junctions), both defaulting **on**. A 0.9 build would silently
#: strip them on save, so the bump refuses the newer file; 0.1–0.9 files load unchanged
#: (both default on, preserving the prior always-draw behaviour).
_FORMAT_VERSION: str = "0.10"

# File-format versions this loader accepts. Extend when new versions are defined.
_KNOWN_VERSIONS: set[str] = {
    "0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9", "0.10",
}

# Refuse to parse implausibly large files (a real schematic is a few hundred KB
# at most). Checked via stat() before the file is read into memory.
_MAX_FILE_BYTES: int = 32 * 1024 * 1024  # 32 MB

# Component-kind migration map: ``{old_kind: current_kind}``.  A ``.hv`` file
# stores only a component's ``kind`` string (never its geometry), so the kind is
# the stable identifier across CircuiTikZ-library re-generations.  If a future
# re-generation renames a kind, add the old→new mapping here and old files keep
# loading.  Applied in :func:`_dict_to_component` before the registry lookup.
_KIND_ALIASES: dict[str, str] = {}


class SchematicLoadError(Exception):
    """Raised when a .hv file cannot be loaded for any reason."""


class SchematicSaveError(Exception):
    """Raised when a schematic cannot be saved (e.g. it fails validation).

    save() raises this *before* touching the destination file, so an invalid
    in-memory document can never overwrite a good file on disk.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save(schematic: Schematic, path: str | Path) -> None:
    """Validate then serialise *schematic* to a UTF-8 JSON file at *path*.

    The schematic is validated first; an invalid document raises
    SchematicSaveError and nothing is written (writing it would produce a file
    that refuses to load — permanent data loss). The file is then written
    atomically: the JSON is written to a sibling per-process temp file, flushed
    and fsync'd, an existing destination is copied to ``<name>.bak``
    (best-effort), and the temp file replaces the destination via os.replace.
    On any failure after the temp file is created it is removed.
    """
    path = Path(path)

    # 1. Refuse to persist an invalid document.
    try:
        errors = validate(schematic)
    except Exception as exc:  # validate() crashed on corrupt in-memory state
        raise SchematicSaveError(
            f"Cannot save: schematic could not be validated: {exc}"
        ) from exc
    if errors:
        raise SchematicSaveError(
            f"Cannot save: schematic invariant violated: {errors[0]}"
            + (f" (and {len(errors) - 1} more)" if len(errors) > 1 else "")
        )

    # 2. Serialise. allow_nan=False so a stray NaN/Infinity in a numeric field
    # can never produce a file the loader rejects.
    data = _schematic_to_dict(schematic)
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SchematicSaveError(f"Cannot save: schematic is not serialisable: {exc}") from exc

    # 3. Write to a sibling temp file (unique per process, so two instances
    # saving the same path cannot collide), then atomically replace the target
    # so a failed/interrupted write never corrupts an existing file. Write
    # without BOM; explicitly UTF-8.
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        # Keep a best-effort backup of the file being replaced; failure to
        # write the .bak must not block the save itself.
        if path.exists():
            try:
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def load(path: str | Path) -> Schematic:
    """Load and validate a .hv file.

    Raises SchematicLoadError with a descriptive message on any problem:
    - malformed JSON
    - missing required fields
    - unknown version
    - invariant violations
    """
    path = Path(path)

    # 1. Bound the input size (checked via stat, before reading into memory),
    # then read raw bytes and decode as UTF-8.
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise SchematicLoadError(f"Cannot read file: {exc}") from exc
    if size > _MAX_FILE_BYTES:
        raise SchematicLoadError(
            f"File is too large to be a schematic "
            f"({size} bytes; the limit is {_MAX_FILE_BYTES} bytes)"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise SchematicLoadError(f"Cannot read file: {exc}") from exc

    # 2. Parse JSON. NaN/Infinity/-Infinity literals (which json.loads would
    # otherwise accept) are rejected here — they are not valid JSON and the
    # model cannot represent them.
    def _reject_constant(name: str) -> float:
        raise SchematicLoadError(
            f"Invalid JSON: non-finite number literal {name!r} is not allowed"
        )

    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise SchematicLoadError(f"Invalid JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise SchematicLoadError("Top-level JSON value must be an object")

    # 3+4. Build the model objects and validate schematic invariants. Any
    # unexpected exception (a malformed value the field-level checks missed)
    # is re-raised as SchematicLoadError so no raw exception ever escapes.
    try:
        schematic = _dict_to_schematic(data)
        errors = validate(schematic)
    except SchematicLoadError:
        raise
    except Exception as exc:
        raise SchematicLoadError(f"Malformed .hv file: {exc}") from exc
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
        # Always written as the current format version.
        "version": _FORMAT_VERSION,
        "name": s.name,
        "config": {
            "voltage_style": s.voltage_style,
            "current_style": s.current_style,
            "symbol_style": dict(s.symbol_style),
            "siunitx": s.siunitx,
            "preamble": s.preamble,
            "mark_unconnected_pins": s.mark_unconnected_pins,
            "line_hops": s.line_hops,
            "mark_open_ends": s.mark_open_ends,
            "mark_junctions": s.mark_junctions,
            "diode_scale": s.diode_scale,
        },
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
    # Node-style {…} slot text; omitted when empty (the common case).
    if c.node_text:
        d["node_text"] = c.node_text
    # Single-terminal node placement keyword (left/right/above/below); omitted when
    # empty (centred, the common case).
    if getattr(c, "node_side", ""):
        d["node_side"] = c.node_side
    if c.label_offset is not None:
        d["label_offset"] = list(c.label_offset)
    if c.span_override is not None:
        d["span_override"] = list(c.span_override)
    # Layer (front/back), carried by every component; omitted at the 0 baseline.
    if c.z_order != 0:
        d["z_order"] = c.z_order
    # Active variants only (e.g. {"filled": true}); omitted when none are on.
    active = {name: True for name, on in c.variants.items() if on}
    if active:
        d["variants"] = active
    # Integer parameters for a parametric kind (e.g. {"inputs": 4}); omitted when
    # none set (the kind's default applies).
    if c.params:
        d["params"] = {name: int(v) for name, v in c.params.items()}
    # Unified stroke/outline width (symbols and block kinds), omitted at the
    # CircuiTikZ default (0.4 pt).
    if abs(c.line_width - 0.4) > 1e-6:
        d["line_width"] = c.line_width
    # Per-instance logic-gate size multiplier, omitted at the default (1.0).
    if abs(c.scale - 1.0) > 1e-6:
        d["scale"] = c.scale
    if isinstance(c, StyledComponent):
        if c.fill_color:
            d["fill_color"] = c.fill_color
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
    d: dict[str, Any] = {
        "id": w.id,
        "points": [list(pt) for pt in w.points],
    }
    # Only persist style fields when they differ from the defaults, keeping
    # plain wires' JSON unchanged and backward-compatible.
    if w.line_style:
        d["line_style"] = w.line_style
    if w.line_width != 0.4:
        d["line_width"] = w.line_width
    if w.no_junction_dots:
        d["no_junction_dots"] = True
    if w.no_termination_dots:
        d["no_termination_dots"] = True
    if w.hop_mode:
        d["hop_mode"] = w.hop_mode
    if w.start_marker:
        d["start_marker"] = w.start_marker
    if w.end_marker:
        d["end_marker"] = w.end_marker
    if w.start_label:
        d["start_label"] = w.start_label
    if w.end_label:
        d["end_label"] = w.end_label
    if w.mid_label:
        d["mid_label"] = w.mid_label
    if w.mid_label_pos != 0.5:
        d["mid_label_pos"] = w.mid_label_pos
    if w.start_label_placement:
        d["start_label_placement"] = w.start_label_placement
    if w.end_label_placement:
        d["end_label_placement"] = w.end_label_placement
    if w.z_order:
        d["z_order"] = w.z_order
    return d


# ---------------------------------------------------------------------------
# Deserialisation helpers
# ---------------------------------------------------------------------------

def _require(data: dict, key: str, context: str) -> Any:
    """Return data[key] or raise SchematicLoadError if missing."""
    if key not in data:
        raise SchematicLoadError(f"Missing required field '{key}' in {context}")
    return data[key]


def _finite(value: float, what: str) -> float:
    """Return *value* or raise SchematicLoadError if it is NaN/±Infinity.

    Belt-and-braces alongside the parse_constant rejection in load(): every
    numeric field that reaches the model must be a finite float (a non-finite
    coordinate would crash grid validation and the canvas).
    """
    if not math.isfinite(value):
        raise SchematicLoadError(f"{what} must be a finite number")
    return value


def _dict_to_schematic(data: dict) -> Schematic:
    version = _require(data, "version", "schematic")
    if not isinstance(version, str):
        raise SchematicLoadError("Field 'version' must be a string")
    if version not in _KNOWN_VERSIONS:
        raise SchematicLoadError(
            f"This file uses .hv format version '{version}', which this version "
            f"of Heaviside does not support (it reads "
            f"{sorted(_KNOWN_VERSIONS)}). It was likely saved by a newer "
            f"release — please update Heaviside to open it."
        )

    name = _require(data, "name", "schematic")
    if not isinstance(name, str):
        raise SchematicLoadError("Field 'name' must be a string")

    raw_components = _require(data, "components", "schematic")
    if not isinstance(raw_components, list):
        raise SchematicLoadError("Field 'components' must be an array")
    components = [
        _dict_to_component(c, i) for i, c in enumerate(raw_components)
    ]

    raw_wires = _require(data, "wires", "schematic")
    if not isinstance(raw_wires, list):
        raise SchematicLoadError("Field 'wires' must be an array")
    wires = [_dict_to_wire(w, i) for i, w in enumerate(raw_wires)]

    metadata = data.get("metadata", {})
    if not isinstance(metadata, dict):
        raise SchematicLoadError("Field 'metadata' must be an object")

    # Document config (added in 0.2). Absent in 0.1 files → american defaults.
    # Unknown style values fall back to "american" rather than failing the load.
    config = data.get("config", {})
    if not isinstance(config, dict):
        raise SchematicLoadError("Field 'config' must be an object")

    def _style(key: str) -> str:
        value = config.get(key, "american")
        return value if value in LABEL_STYLES else "american"

    # Preamble settings (added in 0.5). siunitx defaults **on** (matching a new
    # document, §7.2) so a pre-0.5 file — which predates the option — opens with
    # unit-macro support; preamble defaults empty. A non-bool/non-string value is
    # coerced rather than failing the load.
    siunitx = bool(config.get("siunitx", True))
    raw_preamble = config.get("preamble", "")
    preamble = raw_preamble if isinstance(raw_preamble, str) else ""

    # Symbol style (added in 0.7). A string→string map (family → style value); unknown
    # axes/values are clamped to the default at render time (library.style_value), so we
    # keep it permissive here. Absent → empty (all american), so pre-0.7 files are
    # unchanged.
    raw_symbol = config.get("symbol_style", {})
    symbol_style = ({str(k): str(v) for k, v in raw_symbol.items()}
                    if isinstance(raw_symbol, dict) else {})

    # Display options + diode scale (added in 0.9; moved out of app Preferences).
    # Defaults match the prior preference defaults / the diode constant, so a pre-0.9
    # file opens unchanged: marks off, line-hops on, diode scale 0.8. (0.8 is the baked
    # baseline a pre-field file was effectively drawn at — preserving its look — distinct
    # from the **new-document** default of 0.6, the manual's recommendation, set on the
    # Schematic dataclass.) Values are coerced rather than failing the load.
    mark_unconnected_pins = bool(config.get("mark_unconnected_pins", False))
    line_hops = bool(config.get("line_hops", True))
    mark_open_ends = bool(config.get("mark_open_ends", True))
    mark_junctions = bool(config.get("mark_junctions", True))
    try:
        diode_scale = float(config.get("diode_scale", 0.8))
    except (TypeError, ValueError):
        diode_scale = 0.8

    return Schematic(
        version=version,
        name=name,
        components=components,
        wires=wires,
        metadata=metadata,
        voltage_style=_style("voltage_style"),
        current_style=_style("current_style"),
        symbol_style=symbol_style,
        siunitx=siunitx,
        preamble=preamble,
        mark_unconnected_pins=mark_unconnected_pins,
        line_hops=line_hops,
        mark_open_ends=mark_open_ends,
        mark_junctions=mark_junctions,
        diode_scale=diode_scale,
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
    # Migrate a renamed kind to its current name so old files keep loading after
    # a CircuiTikZ-library re-generation renames a symbol (see _KIND_ALIASES).
    kind = _KIND_ALIASES.get(kind, kind)
    if not (isinstance(pos_raw, list) and len(pos_raw) == 2):
        raise SchematicLoadError(f"{ctx}.position must be a two-element array")
    # Rotation must be integral but, like every other numeric field, an
    # integral float (e.g. 90.0) is coerced rather than rejected. Bools and
    # non-integral values are rejected.
    if isinstance(rot_raw, bool):
        raise SchematicLoadError(f"{ctx}.rotation must be an integer")
    if isinstance(rot_raw, int):
        rotation = rot_raw
    elif isinstance(rot_raw, float) and math.isfinite(rot_raw) and rot_raw.is_integer():
        rotation = int(rot_raw)
    else:
        raise SchematicLoadError(f"{ctx}.rotation must be an integer")

    try:
        position = (float(pos_raw[0]), float(pos_raw[1]))
    except (TypeError, ValueError) as exc:
        raise SchematicLoadError(f"{ctx}.position values must be numbers") from exc
    _finite(position[0], f"{ctx}.position")
    _finite(position[1], f"{ctx}.position")

    mirror = bool(data.get("mirror", False))

    if "options" in data:
        options = data["options"]
        if not isinstance(options, str):
            raise SchematicLoadError(f"{ctx}.options must be a string")
    else:
        options = ""

    raw_nt = data.get("node_text", "")
    if not isinstance(raw_nt, str):
        raise SchematicLoadError(f"{ctx}.node_text must be a string")
    node_text = raw_nt
    # Legacy migration: pre-0.6 power rails carried their voltage name in an ``l=``
    # slot of ``options`` (rendered via ``label=right:…``). That slot now lives in
    # ``node_text`` (the {…} slot). Move it over when no node_text is set, so old
    # files keep their rail labels. Other slots in ``options`` are preserved.
    if not node_text and kind in _LEGACY_POWER_RAIL_KINDS:
        node_text, options = _split_off_l_slot(options)

    raw_side = data.get("node_side", "")
    if not isinstance(raw_side, str):
        raise SchematicLoadError(f"{ctx}.node_side must be a string")
    node_side = raw_side

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
        _finite(label_offset[0], f"{ctx}.label_offset")
        _finite(label_offset[1], f"{ctx}.label_offset")

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
        _finite(span_override[0], f"{ctx}.span_override")
        _finite(span_override[1], f"{ctx}.span_override")

    defn = REGISTRY.get(kind)
    cls = defn.component_class if defn is not None else Component

    # Active variants (generic).  Read the new `variants` map, plus the legacy
    # `filled` / `body_diode` keys for back-compat with pre-variants `.hv` files.
    raw_variants = data.get("variants", {})
    if not isinstance(raw_variants, dict):
        raise SchematicLoadError(f"{ctx}.variants must be an object")
    variants = {str(name): bool(on) for name, on in raw_variants.items() if on}
    if data.get("filled"):
        variants["filled"] = True
    if data.get("body_diode"):
        variants["body_diode"] = True

    # Integer parameters for a parametric kind (e.g. logic-gate input count).
    raw_params = data.get("params", {})
    if not isinstance(raw_params, dict):
        raise SchematicLoadError(f"{ctx}.params must be an object")
    try:
        params = {str(name): int(v) for name, v in raw_params.items()}
    except (TypeError, ValueError, OverflowError) as exc:
        raise SchematicLoadError(f"{ctx}.params values must be integers") from exc

    # Unified stroke/outline width. Legacy files stored a block's outline width
    # under "border_width" (now merged into line_width), so fall back to it.
    raw_lw = data.get("line_width", data.get("border_width", 0.4))
    try:
        line_width = _finite(float(raw_lw), f"{ctx}.line_width")
    except (TypeError, ValueError) as exc:
        raise SchematicLoadError(f"{ctx}.line_width must be a number") from exc

    raw_scale = data.get("scale", 1.0)
    try:
        scale = _finite(float(raw_scale), f"{ctx}.scale")
    except (TypeError, ValueError) as exc:
        raise SchematicLoadError(f"{ctx}.scale must be a number") from exc

    kwargs: dict = {
        "id": comp_id,
        "kind": kind,
        "position": position,
        "rotation": rotation,
        "mirror": mirror,
        "options": options,
        "node_text": node_text,
        "node_side": node_side,
        "label_offset": label_offset,
        "span_override": span_override,
        "variants": variants,
        "params": params,
        "line_width": line_width,
        "scale": scale,
    }

    # z_order is carried by every component (0.4+); older files omit it for plain
    # components and it defaults to 0.
    raw_z = data.get("z_order", 0)
    if not isinstance(raw_z, int) or isinstance(raw_z, bool):
        raise SchematicLoadError(f"{ctx}.z_order must be an integer")
    kwargs["z_order"] = raw_z

    if issubclass(cls, StyledComponent):
        kwargs["fill_color"] = str(data.get("fill_color", ""))
        kwargs["line_style"] = str(data.get("line_style", ""))

    if issubclass(cls, FontedComponent):
        raw_ff = data.get("font_family", "")
        if not isinstance(raw_ff, str):
            raise SchematicLoadError(f"{ctx}.font_family must be a string")
        kwargs["font_bold"] = bool(data.get("font_bold", False))
        kwargs["font_italic"] = bool(data.get("font_italic", False))
        kwargs["font_family"] = raw_ff
        if "font_size" in data:
            try:
                kwargs["font_size"] = _finite(
                    float(data["font_size"]), f"{ctx}.font_size"
                )
            except (TypeError, ValueError) as exc:
                raise SchematicLoadError(
                    f"{ctx}.font_size must be a number"
                ) from exc

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
        _finite(points[-1][0], f"{ctx}.points[{j}]")
        _finite(points[-1][1], f"{ctx}.points[{j}]")

    line_style = data.get("line_style", "")
    if not isinstance(line_style, str):
        raise SchematicLoadError(f"{ctx}.line_style must be a string")

    raw_lw = data.get("line_width", 0.4)
    try:
        line_width = _finite(float(raw_lw), f"{ctx}.line_width")
    except (TypeError, ValueError) as exc:
        raise SchematicLoadError(f"{ctx}.line_width must be a number") from exc

    no_junction_dots = data.get("no_junction_dots", False)
    if not isinstance(no_junction_dots, bool):
        raise SchematicLoadError(f"{ctx}.no_junction_dots must be a boolean")

    no_termination_dots = data.get("no_termination_dots", False)
    if not isinstance(no_termination_dots, bool):
        raise SchematicLoadError(f"{ctx}.no_termination_dots must be a boolean")

    hop_mode = data.get("hop_mode", "")
    if not isinstance(hop_mode, str) or hop_mode not in WIRE_HOP_MODES:
        raise SchematicLoadError(
            f"{ctx}.hop_mode must be one of {WIRE_HOP_MODES!r}"
        )

    start_marker = data.get("start_marker", "")
    if not isinstance(start_marker, str):
        raise SchematicLoadError(f"{ctx}.start_marker must be a string")

    end_marker = data.get("end_marker", "")
    if not isinstance(end_marker, str):
        raise SchematicLoadError(f"{ctx}.end_marker must be a string")

    start_label = data.get("start_label", "")
    if not isinstance(start_label, str):
        raise SchematicLoadError(f"{ctx}.start_label must be a string")

    end_label = data.get("end_label", "")
    if not isinstance(end_label, str):
        raise SchematicLoadError(f"{ctx}.end_label must be a string")

    mid_label = data.get("mid_label", "")
    if not isinstance(mid_label, str):
        raise SchematicLoadError(f"{ctx}.mid_label must be a string")

    raw_mid_pos = data.get("mid_label_pos", 0.5)
    try:
        mid_label_pos = _finite(float(raw_mid_pos), f"{ctx}.mid_label_pos")
    except (TypeError, ValueError) as exc:
        raise SchematicLoadError(f"{ctx}.mid_label_pos must be a number") from exc
    mid_label_pos = max(0.0, min(1.0, mid_label_pos))

    start_label_placement = data.get("start_label_placement", "")
    if not isinstance(start_label_placement, str):
        raise SchematicLoadError(f"{ctx}.start_label_placement must be a string")

    end_label_placement = data.get("end_label_placement", "")
    if not isinstance(end_label_placement, str):
        raise SchematicLoadError(f"{ctx}.end_label_placement must be a string")

    z_order = data.get("z_order", 0)
    if not isinstance(z_order, int) or isinstance(z_order, bool):
        raise SchematicLoadError(f"{ctx}.z_order must be an integer")

    return Wire(
        id=wire_id, points=points, line_style=line_style, line_width=line_width,
        no_junction_dots=no_junction_dots, no_termination_dots=no_termination_dots,
        hop_mode=hop_mode,
        start_marker=start_marker, end_marker=end_marker,
        start_label=start_label, end_label=end_label,
        mid_label=mid_label, mid_label_pos=mid_label_pos,
        start_label_placement=start_label_placement,
        end_label_placement=end_label_placement,
        z_order=z_order,
    )
