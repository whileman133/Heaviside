#!/usr/bin/env python3
r"""
*** ONE-SHOT BOOTSTRAP TOOL — DO NOT RE-RUN CASUALLY ***

This script carries its own authored component tables and OVERWRITES the
corresponding entries in ``components/definitions.json`` when run, silently
reverting any later edits made through the component-editor GUI or by hand.
``components/generate_components.py`` is the sanctioned re-render path: it
treats definitions.json as the source of truth and only regenerates geometry.

Author the digital-logic block components (flip-flops, mux/demux, ALU, adder)
into ``components/definitions.json`` + ``components/geometry.json``.

These are native CircuiTikZ shapes — ``flipflop`` (D/SR/JK/T) and ``muxdemux``
(which also provides the named ``ALU``, ``demux`` and ``one bit adder`` styles).
Each is a centre-placed multi-terminal node (``anchor_pin`` null, like the op
amp): the shape is drawn undistorted and every pin is bridged to a tidy grid
coordinate with a clean axis-aligned lead.  The pin's perpendicular coordinate
keeps the shape's measured anchor value (generally off the 0.25-GU grid) so the
lead stays straight; a wire snaps onto the off-grid pin via the magnet, exactly
as for scaled logic gates.

The pin ``offset`` perpendicular-to-the-lead coordinate must equal the **measured
CircuiTikZ anchor** (see ``app/components/render.measure_anchors``) or the lead
will run diagonally.  The values below were measured against CircuiTikZ 1.4.6.

Run after a fresh checkout or a CircuiTikZ change:

    python components/add_digital.py

Requires ``latex`` + ``dvisvgm`` (the same toolchain as generate_components.py).
It calls ``renderer.save_component`` per kind, which renders the symbol's
geometry and merges it into both data files (re-using the existing ``origin_svg``).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.componenteditor import renderer  # noqa: E402

CATEGORY = "Logic"


def _block(display: str, tikz: str, pin_anchors: list[tuple[str, str]]) -> dict:
    """A centre-placed multi-terminal entry (anchor_pin null) for a fixed digital
    block.  Pins are given as ``(name, ctikz_anchor)`` only — the anchors are
    **measured** and a best-effort uniform grid-alignment scale (`best_alignment
    _scale`) is baked in, so the pins land on the 0.25-GU grid where a rescale can
    manage it (the rest stay off-grid and connect via the magnet, like a gate)."""
    from app.components import render
    measured = render.measure_anchors(tikz, [a for _, a in pin_anchors])
    pairs = [(n, a) for n, a in pin_anchors if a in measured]
    sc = renderer.best_alignment_scale(measured)
    return {
        "display_name": display,
        "category": CATEGORY,
        "emission": "node",
        "tikz": tikz,
        "labels": [],          # raw pgf shapes: self-labelled by their pin glyphs
        "anchor_pin": None,    # centre-placed; scaled to land pins on the grid
        "leads": [],           # no bridge stubs — pins sit at the scaled anchors
        "scale": [round(sc, 6), round(sc, 6)],
        "pins": [{"name": n, "anchor": a,
                  "offset": [round(measured[a][0] * sc, 4), round(measured[a][1] * sc, 4)]}
                 for n, a in pairs],
    }


# Flip-flops (flipflop shape): pins 1=top-left, 2=mid-left, 3=bottom-left,
# 4=bottom-right, 5=mid-right, 6=top-right.
def _flipflops() -> dict[str, dict]:
    corner = [("Q", "pin 6"), ("Qbar", "pin 4")]
    return {
        "flipflop D": _block("D Flip-Flop", "flipflop D",
                             [("D", "pin 1"), ("clk", "pin 3")] + corner),
        "flipflop SR": _block("SR Flip-Flop", "flipflop SR",
                              [("S", "pin 1"), ("R", "pin 3")] + corner),
        "flipflop JK": _block("JK Flip-Flop", "flipflop JK",
                              [("J", "pin 1"), ("clk", "pin 2"), ("K", "pin 3")] + corner),
        "flipflop T": _block("T Flip-Flop", "flipflop T",
                             [("T", "pin 1"), ("clk", "pin 3")] + corner),
    }


def _entries() -> dict[str, dict]:
    e = _flipflops()
    # ALU — notched trapezoid: operands A/B left, result right, op-select pins
    # bottom, zero flag top.
    e["ALU"] = _block("ALU", "ALU", [
        ("A", "lpin 1"), ("B", "lpin 2"), ("result", "rpin 1"),
        ("op0", "bpin 1"), ("op1", "bpin 2"), ("zero", "tpin 1"),
    ])
    # Adder — `one bit adder` notched trapezoid.
    e["adder"] = _block("Adder", "one bit adder", [
        ("A", "lpin 1"), ("B", "lpin 2"), ("sum", "rpin 1"), ("cin", "bpin 1"),
    ])
    return e


# Parametric multiplexer/demultiplexer (CircuiTikZ ``muxdemux``).  Two parameters
# — the data-line count and the select-line count — so the geometry is rendered
# per value-combo and the (off-grid) pins are measured (renderer.render_muxdemux).
# A single 'Multiplexer'/'Demultiplexer' spans the whole range, replacing the old
# fixed mux2/mux4/demux2/demux4 entries.
def _muxdemux_entry(display: str, role: str, data_name: str) -> dict:
    return {
        "display_name": display, "category": CATEGORY, "emission": "node",
        "tikz": "muxdemux", "labels": [], "anchor_pin": None,
        "params": [
            {"name": data_name, "min": 2, "max": 16, "default": 2},
            {"name": "selects", "min": 1, "max": 4, "default": 1},
        ],
        "muxdemux": {"role": role, "data_param": data_name, "select_param": "selects"},
    }


MUXDEMUX: dict[str, dict] = {
    "mux": _muxdemux_entry("Multiplexer", "mux", "inputs"),
    "demux": _muxdemux_entry("Demultiplexer", "demux", "outputs"),
}

# Fixed mux/demux kinds superseded by the parametric ``mux``/``demux`` above.
_SUPERSEDED = ("mux2", "mux4", "demux2", "demux4")


def main() -> int:
    import json
    # Drop the superseded fixed kinds from both data files before re-authoring.
    defs = json.loads(renderer.DEFINITIONS_PATH.read_text(encoding="utf-8"))
    geom = json.loads(renderer.GEOMETRY_PATH.read_text(encoding="utf-8"))
    removed = False
    for old in _SUPERSEDED:
        removed |= defs["components"].pop(old, None) is not None
        geom.pop(old, None)
    if removed:
        renderer.write_store(geom, defs["components"], tuple(defs["origin_svg"]))
        print(f"  - removed superseded fixed kinds: {', '.join(_SUPERSEDED)}")

    entries = _entries()
    for kind, entry in entries.items():
        renderer.save_component(kind, entry)
        print(f"  + {kind:14s} {entry['display_name']}  (scale {entry['scale'][0]})")
    for kind, entry in MUXDEMUX.items():
        renderer.save_muxdemux(kind, entry)
        n = len(json.loads(renderer.DEFINITIONS_PATH.read_text())["components"][kind]["n_data"])
        print(f"  + {kind:14s} {entry['display_name']} ({n} variants)")
    print(f"Authored {len(entries) + len(MUXDEMUX)} digital-logic components.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
