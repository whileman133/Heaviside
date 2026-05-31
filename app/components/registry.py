"""
Component registry — all v1 component definitions.

REGISTRY maps CircuiTikZ keyword strings to ComponentDef objects.
ITEM_CLASSES maps the same keys to their QGraphicsItem subclasses (populated
in app/canvas/items.py; forward-declared here as None until that module is
imported, so the registry module itself has no Qt dependency).

To add a new component type:
  1. Add a ComponentDef entry to REGISTRY below.
  2. Add a ComponentItem subclass in app/canvas/items.py.
  3. Add the mapping to ITEM_CLASSES in app/canvas/items.py.
"""

from __future__ import annotations

from app.components.model import ComponentDef, PinDef

# ---------------------------------------------------------------------------
# Passives
# ---------------------------------------------------------------------------

_RESISTOR = ComponentDef(
    kind="R",
    display_name="Resistor",
    category="Passives",
    bbox=(0.0, -0.5, 2.0, 0.5),
    pins=[
        PinDef(name="in",  offset=(0.0, 0.0)),
        PinDef(name="out", offset=(2.0, 0.0)),
    ],
    label_slots=["l", "l_", "v", "v^", "i", "i_"],
    tikz_keyword="R",
    default_span=(2.0, 0.0),
)

_CAPACITOR = ComponentDef(
    kind="C",
    display_name="Capacitor",
    category="Passives",
    bbox=(0.0, -0.5, 2.0, 0.5),
    pins=[
        PinDef(name="in",  offset=(0.0, 0.0)),
        PinDef(name="out", offset=(2.0, 0.0)),
    ],
    label_slots=["l", "l_", "v", "v^", "i", "i_"],
    tikz_keyword="C",
    default_span=(2.0, 0.0),
)

_INDUCTOR = ComponentDef(
    kind="L",
    display_name="Inductor",
    category="Passives",
    bbox=(0.0, -0.5, 2.0, 0.5),
    pins=[
        PinDef(name="in",  offset=(0.0, 0.0)),
        PinDef(name="out", offset=(2.0, 0.0)),
    ],
    label_slots=["l", "l_", "v", "v^", "i", "i_"],
    tikz_keyword="L",
    default_span=(2.0, 0.0),
)

_DIODE = ComponentDef(
    kind="D",
    display_name="Diode",
    category="Passives",
    bbox=(0.0, -0.5, 2.0, 0.5),
    pins=[
        PinDef(name="anode",   offset=(0.0, 0.0)),
        PinDef(name="cathode", offset=(2.0, 0.0)),
    ],
    label_slots=["l", "l_", "v", "v^", "i", "i_"],
    tikz_keyword="D",
    default_span=(2.0, 0.0),
)

# ---------------------------------------------------------------------------
# Amplifiers
# ---------------------------------------------------------------------------

_OPAMP = ComponentDef(
    kind="op amp",
    display_name="Op-Amp",
    category="Amplifiers",
    # Pin offsets match CircuiTikZ's actual anchor geometry (measured from the
    # compiled output): ±1.1944 GU horizontally, ±0.4918 GU vertically.
    # Power supply pins are omitted — they are conventionally not shown.
    bbox=(-1.5, -1.0, 1.5, 1.0),
    pins=[
        PinDef(name="+",   offset=(-1.5,  0.5)),
        PinDef(name="-",   offset=(-1.5, -0.5)),
        PinDef(name="out", offset=( 1.5,  0.0)),
    ],
    label_slots=["l"],
    tikz_keyword="op amp",
    default_span=(0.0, 0.0),
)

# ---------------------------------------------------------------------------
# Sources — fixed
# ---------------------------------------------------------------------------

_VSOURCE = ComponentDef(
    kind="V",
    display_name="Voltage Source",
    category="Sources",
    bbox=(-0.5, 0.0, 0.5, 2.0),
    pins=[
        PinDef(name="+", offset=(0.0, 0.0)),
        PinDef(name="-", offset=(0.0, 2.0)),
    ],
    label_slots=["l", "l_", "v", "v^"],
    tikz_keyword="V",
    default_span=(0.0, 2.0),
)

_ISOURCE = ComponentDef(
    kind="I",
    display_name="Current Source",
    category="Sources",
    bbox=(-0.5, 0.0, 0.5, 2.0),
    pins=[
        PinDef(name="+", offset=(0.0, 0.0)),
        PinDef(name="-", offset=(0.0, 2.0)),
    ],
    label_slots=["l", "l_", "i", "i_"],
    tikz_keyword="I",
    default_span=(0.0, 2.0),
)

_AC_VSOURCE = ComponentDef(
    kind="vsource",
    display_name="AC Voltage Source",
    category="Sources",
    bbox=(-0.5, 0.0, 0.5, 2.0),
    pins=[
        PinDef(name="+", offset=(0.0, 0.0)),
        PinDef(name="-", offset=(0.0, 2.0)),
    ],
    label_slots=["l", "l_", "v", "v^"],
    tikz_keyword="vsource",
    default_span=(0.0, 2.0),
)

_AC_ISOURCE = ComponentDef(
    kind="isource",
    display_name="AC Current Source",
    category="Sources",
    bbox=(-0.5, 0.0, 0.5, 2.0),
    pins=[
        PinDef(name="+", offset=(0.0, 0.0)),
        PinDef(name="-", offset=(0.0, 2.0)),
    ],
    label_slots=["l", "l_", "i", "i_"],
    tikz_keyword="isource",
    default_span=(0.0, 2.0),
)

# ---------------------------------------------------------------------------
# Sources — dependent
# ---------------------------------------------------------------------------

_VCVS = ComponentDef(
    kind="cV",
    display_name="VCVS",
    category="Sources",
    bbox=(-0.5, 0.0, 0.5, 2.0),
    pins=[
        PinDef(name="+", offset=(0.0, 0.0)),
        PinDef(name="-", offset=(0.0, 2.0)),
    ],
    label_slots=["l", "l_", "v", "v^"],
    tikz_keyword="cV",
    default_span=(0.0, 2.0),
)

_VCCS = ComponentDef(
    kind="cI",
    display_name="VCCS",
    category="Sources",
    bbox=(-0.5, 0.0, 0.5, 2.0),
    pins=[
        PinDef(name="+", offset=(0.0, 0.0)),
        PinDef(name="-", offset=(0.0, 2.0)),
    ],
    label_slots=["l", "l_", "i", "i_"],
    tikz_keyword="cI",
    default_span=(0.0, 2.0),
)

# ---------------------------------------------------------------------------
# MOSFETs
# ---------------------------------------------------------------------------

_NIGFETE = ComponentDef(
    kind="nigfete",
    display_name="NMOS",
    category="MOSFETs",
    # Geometry matches the grid-aligned circuitikz export (origin = gate pin).
    # Relative to the circuitikz node center the terminals are gate (-1.5,0),
    # drain (0,-1), source (0,+1); expressed from the gate origin that is
    # drain (1.5,-1), source (1.5,+1).
    bbox=(0.0, -1.0, 1.5, 1.0),
    pins=[
        PinDef(name="gate",   offset=(0.0,  0.0)),
        PinDef(name="drain",  offset=(1.5, -1.0)),   # Qt y-down: -1 = visual top
        PinDef(name="source", offset=(1.5,  1.0)),   # Qt y-down: +1 = visual bottom
    ],
    label_slots=["l"],
    tikz_keyword="nigfete",
    default_span=(0.0, 0.0),
)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, ComponentDef] = {
    defn.kind: defn
    for defn in [
        _RESISTOR,
        _CAPACITOR,
        _INDUCTOR,
        _DIODE,
        _OPAMP,
        _VSOURCE,
        _ISOURCE,
        _AC_VSOURCE,
        _AC_ISOURCE,
        _VCVS,
        _VCCS,
        _NIGFETE,
    ]
}

# ---------------------------------------------------------------------------
# ITEM_CLASSES — populated by app/canvas/items.py at import time.
# Declared here so test_registry.py can verify completeness without importing Qt.
# ---------------------------------------------------------------------------

# The canvas module sets this at import time:
#   from app.components.registry import ITEM_CLASSES
#   ITEM_CLASSES.update({ "R": ResistorItem, ... })
#
# Before canvas is imported it is empty; test_registry tests against the
# canvas module directly (importing items.py) to verify the mapping.
ITEM_CLASSES: dict[str, type] = {}
