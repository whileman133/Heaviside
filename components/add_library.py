#!/usr/bin/env python3
r"""
*** ONE-SHOT BOOTSTRAP TOOL — DO NOT RE-RUN CASUALLY ***

This script carries its own authored component tables and OVERWRITES the
corresponding entries in ``components/definitions.json`` when run, silently
reverting any later edits made through the component-editor GUI or by hand.
``components/generate_components.py`` is the sanctioned re-render path: it
treats definitions.json as the source of truth and only regenerates geometry.

Author the "library build-out" components into ``components/definitions.json`` +
``components/geometry.json`` — the previously-unimplemented CircuiTikZ symbols
listed in ``COMPONENTS.md``.

Two waves:

* **Bipoles** — ordinary two-terminal ``to[…]`` devices (``emission: "path"``).
  Each is a CircuiTikZ keyword **verified to compile** in the installed library;
  the renderer measures its geometry/bbox.  (See ``components/_probe.py`` for how
  the keyword set was discovered — nothing here is invented.)
* **Nodes** — fixed multi-terminal shapes (transistors, tubes, amps, …) handled
  like the digital blocks: measure the shape's anchors, pick the wireable
  terminals, and bake a best-effort grid-alignment scale (``add_digital`` pattern).

Run after a fresh checkout or a CircuiTikZ change (idempotent — re-authors in
place):

    python components/add_library.py

Requires ``latex`` + ``dvisvgm`` (+ Ghostscript via ``LIBGS``), the same toolchain
as ``generate_components.py``.  It calls ``renderer.save_component`` per kind.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.componenteditor import renderer  # noqa: E402

# Label-slot presets (which CircuiTikZ annotation quick-keys a device accepts).
PASSIVE = ["l", "l_", "v", "v^", "i", "i_"]   # two-terminal passive / meter / switch
SRC_V = ["l", "l_", "v", "v^"]                # voltage source
SRC_I = ["l", "l_", "i", "i_"]                # current source
BLOCK = ["l"]                                  # labelled block (amp/filter/…)


def _bipole(kind: str, display: str, category: str, *,
            orient: str = "h", labels=PASSIVE) -> dict:
    """A two-terminal ``to[kind]`` path component. ``orient`` 'h' draws along +x
    (pins at 0 and 2 GU), 'v' draws along +y (a vertical source). The renderer
    measures the rendered ink for the bbox."""
    span = [0.0, 2.0] if orient == "v" else [2.0, 0.0]
    names = ("+", "-") if orient == "v" else ("in", "out")
    return {
        "display_name": display,
        "category": category,
        "emission": "path",
        "tikz": kind,
        "labels": list(labels),
        "pins": [
            {"name": names[0], "offset": [0.0, 0.0], "anchor": None},
            {"name": names[1], "offset": span, "anchor": None},
        ],
    }


# --- Wave 1: bipoles -------------------------------------------------------
# (kind == CircuiTikZ keyword, display, category, orient, labels)
BIPOLES: list[tuple] = [
    # Resistors & sensors
    ("varistor", "Varistor", "Resistors", "h", PASSIVE),
    ("photoresistor", "Photoresistor (LDR)", "Resistors", "h", PASSIVE),
    ("thermistor ntc", "Thermistor (NTC)", "Resistors", "h", PASSIVE),
    ("thermistor ptc", "Thermistor (PTC)", "Resistors", "h", PASSIVE),
    # Capacitors
    ("vC", "Variable Capacitor", "Capacitors", "h", PASSIVE),
    ("feC", "Ferroelectric Capacitor", "Capacitors", "h", PASSIVE),
    ("cC", "Curved Capacitor", "Capacitors", "h", PASSIVE),
    ("sC", "Capacitive Sensor", "Capacitors", "h", PASSIVE),
    ("piezoelectric", "Piezoelectric Crystal", "Capacitors", "h", PASSIVE),
    # Inductors
    ("vL", "Variable Inductor", "Inductors", "h", PASSIVE),
    ("sL", "Inductive Sensor", "Inductors", "h", PASSIVE),
    # Sources
    ("dcvsource", "DC Voltage Source", "Sources", "v", SRC_V),
    ("vsourcesquare", "Square Voltage Source", "Sources", "v", SRC_V),
    ("vsourcetri", "Triangle Voltage Source", "Sources", "v", SRC_V),
    ("vsourceN", "Noise Voltage Source", "Sources", "v", SRC_V),
    ("dcisource", "DC Current Source", "Sources", "v", SRC_I),
    # Switches
    ("spst", "SPST Switch", "Switches", "h", PASSIVE),
    ("cute open switch", "Switch (NO, Cute)", "Switches", "h", PASSIVE),
    ("cute closed switch", "Switch (NC, Cute)", "Switches", "h", PASSIVE),
    ("reed", "Reed Switch", "Switches", "h", PASSIVE),
    ("toggle switch", "Toggle Switch", "Switches", "h", PASSIVE),
    # Instruments
    ("oscope", "Oscilloscope", "Instruments", "h", PASSIVE),
    ("rmeter", "Meter", "Instruments", "h", PASSIVE),
    # Transducers
    ("loudspeaker", "Loudspeaker", "Transducers", "h", PASSIVE),
    ("mic", "Microphone", "Transducers", "h", PASSIVE),
    ("buzzer", "Buzzer", "Transducers", "h", PASSIVE),
    # Signal-processing / RF blocks
    ("amp", "Amplifier", "Blocks", "h", BLOCK),
    ("adc", "ADC", "Blocks", "h", BLOCK),
    ("dac", "DAC", "Blocks", "h", BLOCK),
    ("lowpass", "Lowpass Filter", "Blocks", "h", BLOCK),
    ("highpass", "Highpass Filter", "Blocks", "h", BLOCK),
    ("bandpass", "Bandpass Filter", "Blocks", "h", BLOCK),
    ("allpass", "Allpass Filter", "Blocks", "h", BLOCK),
    ("phaseshifter", "Phase Shifter", "Blocks", "h", BLOCK),
    ("detector", "Detector", "Blocks", "h", BLOCK),
    ("vco", "VCO", "Blocks", "h", BLOCK),
    # (Thyristor / triac are authored separately below — they carry a third,
    # off-axis *gate* pin in addition to the two anode/cathode terminals.)
    # Misc
    ("afuse", "Fuse (Asymmetric)", "Misc", "h", PASSIVE),
    ("squid", "SQUID", "Misc", "h", PASSIVE),
    ("bulb", "Light Bulb", "Misc", "h", PASSIVE),
]


# Thyristors are two-terminal ``to[…]`` path devices that also carry a *gate*.
# Unlike the two anode/cathode terminals (which lie on the device axis), the gate
# sticks out perpendicular to it. CircuiTikZ exposes it as the shape's ``gate``
# anchor at a fixed offset; a wire connects to it by coordinate (no anchor
# reference needed in the output), because ``component_pin_positions`` transforms
# this offset with the same rotate-then-mirror transform CircuiTikZ uses to place
# the drawn gate stub — verified to coincide at all 8 rotation×mirror cases. The
# offset is off the 0.25-GU grid (so the gate is magnet-connected, like the
# digital-block pins); keep it as the *measured* value for output to line up.
GATE_OFFSET = (1.7, -0.77)   # CircuiTikZ thyristor/triac ``gate`` anchor (measured)

THYRISTORS: list[tuple] = [
    ("thyristor", "Thyristor (SCR)"),
    ("triac", "TRIAC"),
]


def _thyristor(kind: str, display: str) -> dict:
    """A thyristor-family path device: the two axial terminals plus the off-axis
    ``gate`` pin (kept as pin index 2 so ``pins[1]`` stays the axial terminal)."""
    entry = _bipole(kind, display, "Diodes", orient="h", labels=PASSIVE)
    entry["pins"].append(
        {"name": "gate", "offset": [GATE_OFFSET[0], GATE_OFFSET[1]], "anchor": None}
    )
    return entry


# --- Wave 2: fixed multi-terminal node shapes ------------------------------

def _tripole(kind: str, display: str, category: str, anchor_pin: str,
             pinspecs: list[tuple]) -> dict:
    """An anchor-pinned tripole (transistor / SPDT-style), authored like the
    existing npn/spdt: place ``anchor_pin`` at the origin and give the other pins
    their target grid offsets, then ``realigned`` bakes the scale (and any residual
    leads) from a fresh anchor measurement."""
    entry = {
        "display_name": display, "category": category, "emission": "node",
        "tikz": kind, "labels": ["l"], "anchor_pin": anchor_pin,
        "pins": [{"name": n, "offset": [float(o[0]), float(o[1])], "anchor": a}
                 for n, o, a in pinspecs],
    }
    return renderer.realigned(entry)


def _block(kind: str, display: str, category: str,
           pin_anchors: list[tuple], labels=("l",)) -> dict:
    """A centre-placed multi-terminal node (anchor_pin null, like the op amp /
    flip-flops): measure the named anchors and bake a best-effort uniform
    grid-alignment scale; pins sit at the scaled anchors (off-grid ones connect via
    the magnet). Used for tubes, the differential amps, the Schmitt triggers, and
    the gyrator."""
    from app.components import render
    measured = render.measure_anchors(kind, [a for _, a in pin_anchors])
    pairs = [(n, a) for n, a in pin_anchors if a in measured]
    sc = renderer.best_alignment_scale(measured)
    return {
        "display_name": display, "category": category, "emission": "node",
        "tikz": kind, "labels": list(labels), "anchor_pin": None, "leads": [],
        "scale": [round(sc, 6), round(sc, 6)],
        "pins": [{"name": n, "anchor": a,
                  "offset": [round(measured[a][0] * sc, 4), round(measured[a][1] * sc, 4)]}
                 for n, a in pairs],
    }


def _mono(kind: str, display: str, category: str) -> dict:
    """A single-terminal node placed at its connection point (like ``ground`` /
    the supply rails): one pin at the origin, no anchor_pin."""
    return {
        "display_name": display, "category": category, "emission": "node",
        "tikz": kind, "labels": [],
        "pins": [{"name": "in", "offset": [0.0, 0.0], "anchor": None}],
    }


# Anchor-pinned tripoles: (kind, display, category, anchor_pin, [(pin, offset, anchor)])
TRIPOLES: list[tuple] = [
    ("nigbt", "N-IGBT", "Transistors", "gate",
     [("gate", [0, 0], "B"), ("collector", [1, -1], "C"), ("emitter", [1, 1], "E")]),
    ("pigbt", "P-IGBT", "Transistors", "gate",
     [("gate", [0, 0], "B"), ("emitter", [1, -1], "E"), ("collector", [1, 1], "C")]),
    ("nmos", "N-MOS (Simplified)", "Transistors", "gate",
     [("gate", [0, 0], "B"), ("drain", [1, -1], "C"), ("source", [1, 1], "E")]),
    ("pmos", "P-MOS (Simplified)", "Transistors", "gate",
     [("gate", [0, 0], "B"), ("source", [1, -1], "E"), ("drain", [1, 1], "C")]),
    ("nmosd", "N-MOS Depletion (Simplified)", "Transistors", "gate",
     [("gate", [0, 0], "B"), ("drain", [1, -1], "C"), ("source", [1, 1], "E")]),
    ("pmosd", "P-MOS Depletion (Simplified)", "Transistors", "gate",
     [("gate", [0, 0], "B"), ("source", [1, -1], "E"), ("drain", [1, 1], "C")]),
    ("isfet", "ISFET", "Transistors", "gate",
     [("gate", [0, 0], "base"), ("drain", [1, -1], "C"), ("source", [1, 1], "E")]),
    # NB: the SPDT/rotary switches are NOT here. Their throw anchors are asymmetric,
    # so forcing them onto symmetric grid targets produced a *non-uniform* node
    # scale that sheared the blade (anisotropic stroke width) in the LaTeX output
    # while the canvas re-stroked at uniform width — a canvas/output mismatch. They
    # are authored centre-placed (uniform scale, NODE_BLOCKS below) instead.
]

# Centre-placed multi-terminal nodes: (kind, display, category, [(pin, anchor)]).
# Uniform best-effort scale (no shape distortion); off-grid pins are magnet-connected.
NODE_BLOCKS: list[tuple] = [
    # Electron tubes. The control grid is ``grid``; multi-grid tubes also expose
    # the ``screen`` (and, for the pentode, ``suppressor``) grid taps. ``cathode``
    # is one filament leg (``cathode 2`` is the other, left implicit).
    ("triode", "Triode", "Tubes",
     [("anode", "anode"), ("grid", "grid"), ("cathode", "cathode")]),
    ("diodetube", "Vacuum Diode", "Tubes",
     [("anode", "anode"), ("cathode", "cathode")]),
    ("tetrode", "Tetrode", "Tubes",
     [("anode", "anode"), ("grid", "grid"), ("screen", "screen"),
      ("cathode", "cathode")]),
    ("pentode", "Pentode", "Tubes",
     [("anode", "anode"), ("grid", "grid"), ("screen", "screen"),
      ("suppressor", "suppressor"), ("cathode", "cathode")]),
    ("fd op amp", "Fully-Differential Op-Amp", "Amplifiers",
     [("in+", "+"), ("in-", "-"), ("out+", "out +"), ("out-", "out -")]),
    ("schmitt", "Schmitt Trigger", "Amplifiers", [("in", "in"), ("out", "out")]),
    ("invschmitt", "Schmitt Trigger (Inverting)", "Amplifiers",
     [("in", "in"), ("out", "out")]),
    ("gyrator", "Gyrator", "Blocks",
     [("in1", "left"), ("in2", "south"), ("out1", "right"), ("out2", "north")]),
    # SPDT/rotary switches: centre-placed with a uniform scale so the blade isn't
    # sheared in the output (see the note in TRIPOLES). Common pole + two throws.
    ("cute spdt up", "SPDT Switch (Cute, Up)", "Switches",
     [("in", "in"), ("out1", "out 1"), ("out2", "out 2")]),
    ("cute spdt down", "SPDT Switch (Cute, Down)", "Switches",
     [("in", "in"), ("out1", "out 1"), ("out2", "out 2")]),
    ("cute spdt mid", "SPDT Switch (Cute, Mid)", "Switches",
     [("in", "in"), ("out1", "out 1"), ("out2", "out 2")]),
    ("rotaryswitch", "Rotary Switch", "Switches",
     [("in", "in"), ("out1", "out 1"), ("out2", "out 2")]),
]

# Single-terminal nodes (placed at their feed/connection point).
MONOS: list[tuple] = [
    ("antenna", "Antenna", "Antennas"),
]


def main() -> int:
    n = 0
    for kind, display, category, orient, labels in BIPOLES:
        renderer.save_component(kind, _bipole(kind, display, category,
                                              orient=orient, labels=labels))
        n += 1
        print(f"  + {kind:28s} {display}  [{category}]")
    for kind, display in THYRISTORS:
        renderer.save_component(kind, _thyristor(kind, display))
        n += 1
        print(f"  + {kind:28s} {display}  [Diodes] (+gate)")
    for kind, display, category, ap, specs in TRIPOLES:
        renderer.save_component(kind, _tripole(kind, display, category, ap, specs))
        n += 1
        print(f"  + {kind:28s} {display}  [{category}]")
    for kind, display, category, pa in NODE_BLOCKS:
        renderer.save_component(kind, _block(kind, display, category, pa))
        n += 1
        print(f"  + {kind:28s} {display}  [{category}]")
    for kind, display, category in MONOS:
        renderer.save_component(kind, _mono(kind, display, category))
        n += 1
        print(f"  + {kind:28s} {display}  [{category}]")
    print(f"Authored {n} components.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
