"""
Editing model for the component editor (Qt-free).

A *draft* is just the authored ``entry`` dict (the same shape stored in
``components/definitions.json`` under a kind).  This module validates a draft and
builds preview artifacts, so the GUI window stays a thin shell over it.
"""

from __future__ import annotations

from app.componenteditor import renderer
from app.components import library
from app.components.model import ComponentDef

EMISSIONS = ("path", "node")
CATEGORIES = ("Bipoles", "Tripoles", "Nodes", "Annotations", "Drawing")
PIN_GRID = 0.25


def _on_grid(v: float) -> bool:
    n = v / PIN_GRID
    return abs(n - round(n)) < 1e-6


def validate_entry(kind: str, entry: dict) -> list[str]:
    """Return human-readable problems with a draft (empty == ready to save)."""
    errs: list[str] = []
    if not kind.strip():
        errs.append("Kind is required.")
    if not entry.get("tikz", "").strip():
        errs.append("CircuiTikZ keyword is required.")
    if entry.get("emission") not in EMISSIONS:
        errs.append(f"Emission must be one of {', '.join(EMISSIONS)}.")

    pins = entry.get("pins", [])
    if not pins:
        errs.append("At least one pin is required.")
    names = [p["name"] for p in pins]
    if len(set(names)) != len(names):
        errs.append("Pin names must be unique.")
    for p in pins:
        if not p["name"].strip():
            errs.append("Every pin needs a name.")
        ox, oy = p["offset"]
        if not (_on_grid(ox) and _on_grid(oy)):
            errs.append(f"Pin {p['name']!r} offset {(ox, oy)} is not on the 0.25 GU grid.")

    bbox = entry.get("bbox")
    if not (isinstance(bbox, (list, tuple)) and len(bbox) == 4):
        errs.append("bbox must be four numbers (x0, y0, x1, y1).")

    if library.is_multi_terminal_entry(entry):
        for p in pins:
            if not (p.get("anchor") or "").strip():
                errs.append(f"Multi-terminal pin {p['name']!r} needs a CircuiTikZ anchor.")
        ap = entry.get("anchor_pin")
        if ap and ap not in names:
            errs.append(f"anchor_pin {ap!r} is not one of the pins.")
    return errs


def derived_component_def(kind: str, entry: dict) -> ComponentDef:
    """The registry ComponentDef this draft would produce (for the preview)."""
    return library.to_component_def(kind, renderer.data_entry(kind, entry))


def measured_anchors(entry: dict) -> dict[str, tuple[float, float]]:
    """Measure the CircuiTikZ anchors named by the draft's pins (GU, Qt y-down)."""
    from app.components import render
    if not entry.get("tikz", "").strip():
        return {}
    anchors = [p["anchor"] for p in entry.get("pins", []) if p.get("anchor")]
    if not anchors:
        return {}
    return render.measure_anchors(entry["tikz"], anchors)
