#!/usr/bin/env python3
"""
Generate ``components/components.json`` — the one flat data file that holds the
per-component registry + code-generation data (pins, bbox, alignment, metadata)
that is currently hand-maintained as magic numbers across ``registry.py`` and the
``circuitikz`` codegen tables.

Run this to (re)build the file from the current sources.  For an *existing*
component it consolidates today's values; for a *new* component the author first
runs ``app/components/bake.py`` to MEASURE the pin anchors (so no number is typed
by hand) and adds an entry.  Symbol geometry stays in the existing
``manifest.json`` (built by ``tools/export_circuitikz_svgs.py``) — this file is
only the registry/codegen layer.

    python tools/generate_components.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.codegen import circuitikz as cg  # noqa: E402
from app.components.registry import REGISTRY  # noqa: E402

OUT = ROOT / "components" / "components.json"

_MOSFET_KINDS = frozenset({"nigfete", "nigfetd", "pigfete", "pigfetd"})


def _emission(kind: str) -> str:
    if kind in cg._MULTI_TERMINAL_KINDS:
        return "multi_terminal"
    if kind in cg._NODE_KINDS:
        return "node"
    return "two_terminal"


def _parse_scale(opts: str) -> list[float]:
    sx = sy = 1.0
    if (m := re.search(r"xscale=([-\d.]+)", opts)):
        sx = float(m.group(1))
    if (m := re.search(r"yscale=([-\d.]+)", opts)):
        sy = float(m.group(1))
    return [sx, sy]


def _variants(kind: str) -> list[dict]:
    if kind in cg._DIODE_KINDS:
        return [{"name": "filled", "token": "*", "mode": "suffix"}]
    if kind in _MOSFET_KINDS:
        return [{"name": "body_diode", "token": "bodydiode", "mode": "option"}]
    return []


def _entry(kind: str, defn) -> dict:
    emission = _emission(kind)
    anchor_map = cg._PIN_TO_CTIKZ_ANCHOR.get(kind, {})
    offsets = {p.name: list(p.offset) for p in defn.pins}

    entry: dict = {
        "display_name": defn.display_name,
        "category": defn.category,
        "emission": emission,
        "tikz": defn.tikz_keyword,
        "labels": list(defn.label_slots),
        "bbox": list(defn.bbox),
        "pins": [
            {"name": p.name, "offset": list(p.offset), "anchor": anchor_map.get(p.name)}
            for p in defn.pins
        ],
    }
    if emission == "multi_terminal":
        ap = cg._MULTI_TERMINAL_ANCHOR_PIN.get(kind)
        entry["anchor_pin"] = ap[1] if ap else None
        scale = _parse_scale(cg._MULTI_TERMINAL_EXTRA_OPTS.get(kind, ""))
        if scale != [1.0, 1.0]:
            entry["scale"] = scale
        leads = [
            {"anchor": anchor, "to": offsets[pin]}
            for anchor, pin in cg._MULTI_TERMINAL_LEADS.get(kind, [])
            if pin in offsets
        ]
        if leads:
            entry["leads"] = leads
    if (vs := _variants(kind)):
        entry["variants"] = vs
    return entry


def main() -> int:
    store_kinds = (
        (set(cg._TWO_TERMINAL_KINDS) - {"open", "short"})
        | set(cg._MULTI_TERMINAL_KINDS)
        | set(cg._NODE_KINDS)
    )
    data = {kind: _entry(kind, REGISTRY[kind]) for kind in sorted(store_kinds)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {len(data)} components -> {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
