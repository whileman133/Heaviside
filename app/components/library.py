"""
Component library — load ``components/components.json`` and build the runtime
structures from it (spec: ``spec/component-editor.md`` §3).

This is the one flat data file that replaces the per-component magic numbers
previously hand-maintained in ``registry.py`` and the ``circuitikz`` codegen
tables.  It carries the registry/codegen layer (pins, bbox, alignment, metadata);
symbol geometry stays in the existing ``manifest.json``.

The 33 SVG-symbol kinds live in the file; the 6 bespoke kinds (the resizable
annotations ``open``/``short`` and the drawing primitives) are not
command-derived and keep their hand-coded ``ComponentDef`` in ``registry.py``.

``registry.py`` calls :func:`library_component_defs` to build the 33 SVG-symbol
``ComponentDef``s, and ``app/codegen/circuitikz.py`` calls
:func:`build_codegen_tables`; ``tests/test_components_library.py`` pins the
expected values.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.components.model import (
    Component,
    ComponentDef,
    DiodeComponent,
    MosfetComponent,
    PinDef,
)
from app.resources import resource_path

# Kinds NOT in the library (no CircuiTikZ-command symbol) — keep hand-coded defs.
NON_LIBRARY_KINDS: frozenset[str] = frozenset(
    {"open", "short", "bipole", "rect", "circle", "text_node"}
)


def library_path() -> Path:
    return Path(resource_path("components", "components.json"))


@lru_cache(maxsize=1)
def load_library() -> dict[str, dict]:
    """Load and cache the component data file."""
    return json.loads(library_path().read_text(encoding="utf-8"))


def _component_class(entry: dict) -> type:
    names = {v["name"] for v in entry.get("variants", [])}
    if "filled" in names:
        return DiodeComponent
    if "body_diode" in names:
        return MosfetComponent
    return Component


def to_component_def(kind: str, entry: dict) -> ComponentDef:
    """Build a registry :class:`ComponentDef` from one library entry."""
    pins = [PinDef(name=p["name"], offset=tuple(p["offset"])) for p in entry["pins"]]
    # default_span: origin -> terminal for a two-pin device, else (0, 0).
    if len(pins) == 2:
        (x0, y0), (x1, y1) = pins[0].offset, pins[1].offset
        default_span = (x1 - x0, y1 - y0)
    else:
        default_span = (0.0, 0.0)
    return ComponentDef(
        kind=kind,
        display_name=entry["display_name"],
        category=entry["category"],
        bbox=tuple(entry["bbox"]),  # type: ignore[arg-type]
        pins=pins,
        label_slots=list(entry.get("labels", [])),
        tikz_keyword=entry["tikz"],
        default_span=default_span,
        resizable=False,  # every library (SVG-symbol) kind is fixed-size
        component_class=_component_class(entry),
    )


def library_component_defs() -> dict[str, ComponentDef]:
    """Build the ``ComponentDef`` for every CircuiTikZ-symbol kind in the file.

    ``registry.py`` merges these with its hand-coded bespoke defs to form
    ``REGISTRY``.  Qt-free and self-contained (no import of ``registry``).
    """
    return {kind: to_component_def(kind, entry) for kind, entry in load_library().items()}


def _scale_to_opts(scale: list[float]) -> str:
    """Format ``[sx, sy]`` as the ``xscale=…, yscale=…`` node-option string."""
    parts = []
    if abs(scale[0] - 1.0) > 1e-9:
        parts.append(f"xscale={scale[0]:g}")
    if abs(scale[1] - 1.0) > 1e-9:
        parts.append(f"yscale={scale[1]:g}")
    return ", ".join(parts)


def build_codegen_tables() -> dict:
    """Project the ``circuitikz`` codegen tables from the library.

    Returns a dict with the same shapes as the hand-maintained tables, so the
    switchover can drop those literals (proven equal in the test module).
    """
    two: set[str] = set()
    multi: set[str] = set()
    node: set[str] = set()
    diode: set[str] = set()
    anchor_pin: dict[str, tuple[str, str]] = {}
    pin_to_ctikz: dict[str, dict[str, str]] = {}
    extra_opts: dict[str, str] = {}
    leads: dict[str, list[tuple[str, str]]] = {}

    for kind, e in load_library().items():
        if any(v["name"] == "filled" for v in e.get("variants", [])):
            diode.add(kind)
        if e["emission"] == "two_terminal":
            two.add(kind)
        elif e["emission"] == "node":
            node.add(kind)
        else:
            multi.add(kind)
            pin_to_ctikz[kind] = {p["name"]: p["anchor"] for p in e["pins"] if p.get("anchor")}
            if e.get("anchor_pin"):
                ap = e["anchor_pin"]
                ctikz = next((p["anchor"] for p in e["pins"] if p["name"] == ap), None)
                if ctikz:
                    anchor_pin[kind] = (ctikz, ap)
            if (opts := _scale_to_opts(e.get("scale", [1.0, 1.0]))):
                extra_opts[kind] = opts
            anchor_to_pin = {p["anchor"]: p["name"] for p in e["pins"] if p.get("anchor")}
            leads[kind] = [(ld["anchor"], anchor_to_pin[ld["anchor"]])
                           for ld in e.get("leads", []) if ld["anchor"] in anchor_to_pin]

    return {
        "two_terminal_kinds": two,
        "multi_terminal_kinds": multi,
        "node_kinds": node,
        "diode_kinds": diode,
        "anchor_pin": anchor_pin,
        "pin_to_ctikz": pin_to_ctikz,
        "extra_opts": extra_opts,
        "leads": leads,
    }
