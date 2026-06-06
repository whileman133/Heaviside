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

from app.components.model import (
    BipoleComponent,
    CircleComponent,
    ComponentDef,
    DiodeComponent,
    MosfetComponent,
    PinDef,
    RectComponent,
    TextNodeComponent,
)

# ---------------------------------------------------------------------------
# Bipoles (passives, diodes, general bipole)
# ---------------------------------------------------------------------------

_RESISTOR = ComponentDef(
    kind="R",
    display_name="Resistor",
    category="Bipoles",
    # ±0.25 GU perpendicular to the leads — snug around the zigzag (which reaches
    # ±0.21 GU) so side labels sit close to the body.
    bbox=(0.0, -0.25, 2.0, 0.25),
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
    category="Bipoles",
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
    category="Bipoles",
    # ±0.25 GU perpendicular to the leads — snug around the humps (which reach
    # ≈0.20 GU above the lead) so side labels sit close to the body.
    bbox=(0.0, -0.25, 2.0, 0.25),
    pins=[
        PinDef(name="in",  offset=(0.0, 0.0)),
        PinDef(name="out", offset=(2.0, 0.0)),
    ],
    label_slots=["l", "l_", "v", "v^", "i", "i_"],
    tikz_keyword="L",
    default_span=(2.0, 0.0),
)

_DIODE_PINS = [
    PinDef(name="anode",   offset=(0.0, 0.0)),
    PinDef(name="cathode", offset=(2.0, 0.0)),
]
_DIODE_LABELS = ["l", "l_", "v", "v^", "i", "i_"]
_DIODE_BBOX   = (0.0, -0.5, 2.0, 0.5)

_DIODE = ComponentDef(
    kind="D", display_name="Diode", category="Bipoles",
    bbox=_DIODE_BBOX, pins=_DIODE_PINS, label_slots=_DIODE_LABELS,
    tikz_keyword="D", default_span=(2.0, 0.0), component_class=DiodeComponent,
)
_ZENER = ComponentDef(
    kind="zD", display_name="Zener Diode", category="Bipoles",
    bbox=_DIODE_BBOX, pins=_DIODE_PINS, label_slots=_DIODE_LABELS,
    tikz_keyword="zD", default_span=(2.0, 0.0), component_class=DiodeComponent,
)
_SCHOTTKY = ComponentDef(
    kind="sD", display_name="Schottky Diode", category="Bipoles",
    bbox=_DIODE_BBOX, pins=_DIODE_PINS, label_slots=_DIODE_LABELS,
    tikz_keyword="sD", default_span=(2.0, 0.0), component_class=DiodeComponent,
)
_TUNNEL = ComponentDef(
    kind="tD", display_name="Tunnel Diode", category="Bipoles",
    bbox=_DIODE_BBOX, pins=_DIODE_PINS, label_slots=_DIODE_LABELS,
    tikz_keyword="tD", default_span=(2.0, 0.0), component_class=DiodeComponent,
)
_TVS = ComponentDef(
    kind="zzD", display_name="TVS Diode", category="Bipoles",
    bbox=_DIODE_BBOX, pins=_DIODE_PINS, label_slots=_DIODE_LABELS,
    tikz_keyword="zzD", default_span=(2.0, 0.0), component_class=DiodeComponent,
)
_LED = ComponentDef(
    kind="leD", display_name="LED", category="Bipoles",
    bbox=(0.0, -0.75, 2.0, 0.75), pins=_DIODE_PINS, label_slots=_DIODE_LABELS,
    tikz_keyword="leD", default_span=(2.0, 0.0), component_class=DiodeComponent,
)

# ---------------------------------------------------------------------------
# Amplifiers
# ---------------------------------------------------------------------------

_OPAMP = ComponentDef(
    kind="op amp",
    display_name="Op-Amp",
    category="Tripoles",
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
    category="Bipoles",
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
    category="Bipoles",
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
    kind="vsourcesin",
    display_name="AC Voltage Source",
    category="Bipoles",
    bbox=(-0.5, 0.0, 0.5, 2.0),
    pins=[
        PinDef(name="+", offset=(0.0, 0.0)),
        PinDef(name="-", offset=(0.0, 2.0)),
    ],
    label_slots=["l", "l_", "v", "v^"],
    tikz_keyword="vsourcesin",
    default_span=(0.0, 2.0),
)

_AC_ISOURCE = ComponentDef(
    kind="isourcesin",
    display_name="AC Current Source",
    category="Bipoles",
    bbox=(-0.5, 0.0, 0.5, 2.0),
    pins=[
        PinDef(name="+", offset=(0.0, 0.0)),
        PinDef(name="-", offset=(0.0, 2.0)),
    ],
    label_slots=["l", "l_", "i", "i_"],
    tikz_keyword="isourcesin",
    default_span=(0.0, 2.0),
)

# ---------------------------------------------------------------------------
# Sources — dependent
# ---------------------------------------------------------------------------

_VCVS = ComponentDef(
    kind="cV",
    display_name="VCVS",
    category="Bipoles",
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
    category="Bipoles",
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
    category="Tripoles",
    # Pin offsets match CircuiTikZ's actual anchor geometry when placed with
    # anchor=gate, snapped to the nearest 0.25 GU boundary.
    # Measured from compiled output (pt/28.348):
    #   gate (0,0), drain (0.984,-1.043)→(1.0,-1.0), source (0.984,0.502)→(1.0,0.5)
    bbox=(-0.05, -1.1, 1.05, 0.55),
    pins=[
        PinDef(name="gate",   offset=(0.0,  0.0)),
        PinDef(name="drain",  offset=(1.0, -1.0)),   # Qt y-down: -1 = visual top
        PinDef(name="source", offset=(1.0,  0.5)),   # Qt y-down: +0.5 = visual bottom
    ],
    label_slots=["l"],
    tikz_keyword="nigfete",
    default_span=(0.0, 0.0),
    component_class=MosfetComponent,
)

_NIGFETD = ComponentDef(
    kind="nigfetd",
    display_name="NMOS (depletion)",
    category="Tripoles",
    # Identical pin geometry to nigfete; only the channel drawing differs
    # (solid line instead of three dashes = depletion mode).
    bbox=(-0.05, -1.1, 1.05, 0.55),
    pins=[
        PinDef(name="gate",   offset=(0.0,  0.0)),
        PinDef(name="drain",  offset=(1.0, -1.0)),
        PinDef(name="source", offset=(1.0,  0.5)),
    ],
    label_slots=["l"],
    tikz_keyword="nigfetd",
    default_span=(0.0, 0.0),
    component_class=MosfetComponent,
)

_PIGFETE = ComponentDef(
    kind="pigfete",
    display_name="PMOS",
    category="Tripoles",
    # PMOS enhancement: gate on left, source at top, drain at bottom.
    # Measured CTikZ anchor positions (pt/28.34765, anchor=gate):
    #   gate (0,0), source (0.984,-0.500)→(1.0,-0.5), drain (0.984,1.043)→(1.0,1.0)
    bbox=(-0.05, -0.55, 1.05, 1.1),
    pins=[
        PinDef(name="gate",   offset=(0.0,  0.0)),
        PinDef(name="source", offset=(1.0, -0.5)),   # Qt y-down: -0.5 = visual top
        PinDef(name="drain",  offset=(1.0,  1.0)),   # Qt y-down: +1.0 = visual bottom
    ],
    label_slots=["l"],
    tikz_keyword="pigfete",
    default_span=(0.0, 0.0),
    component_class=MosfetComponent,
)

_PIGFETD = ComponentDef(
    kind="pigfetd",
    display_name="PMOS (depletion)",
    category="Tripoles",
    # PMOS depletion: same pin geometry as pigfete, solid channel line.
    bbox=(-0.05, -0.55, 1.05, 1.1),
    pins=[
        PinDef(name="gate",   offset=(0.0,  0.0)),
        PinDef(name="source", offset=(1.0, -0.5)),
        PinDef(name="drain",  offset=(1.0,  1.0)),
    ],
    label_slots=["l"],
    tikz_keyword="pigfetd",
    default_span=(0.0, 0.0),
    component_class=MosfetComponent,
)

# ---------------------------------------------------------------------------
# BJTs
# ---------------------------------------------------------------------------

_NPN = ComponentDef(
    kind="npn",
    display_name="NPN BJT",
    category="Tripoles",
    # Anchor = base pin.  Measured from re-exported SVG with grid-aligned leads
    # (the npn lead routing in tools/export_circuitikz_svgs.py):
    #   base (0,0), collector (1.013,-1.0)→(1.0,-1.0), emitter (1.013,1.0)→(1.0,1.0)
    # x-error 0.013 GU ≈ 0.8 px — sub-pixel, no scale correction applied.
    bbox=(0.0, -1.1, 1.1, 1.1),
    pins=[
        PinDef(name="base",      offset=(0.0,  0.0)),
        PinDef(name="collector", offset=(1.0, -1.0)),   # Qt y-down: -1 = visual top
        PinDef(name="emitter",   offset=(1.0,  1.0)),   # Qt y-down: +1 = visual bottom
    ],
    label_slots=["l"],
    tikz_keyword="npn",
    default_span=(0.0, 0.0),
)

_PNP = ComponentDef(
    kind="pnp",
    display_name="PNP BJT",
    category="Tripoles",
    # Same SVG geometry as NPN; emitter is at top (visual top = Qt y -1),
    # collector at bottom (Qt y +1).
    bbox=(0.0, -1.1, 1.1, 1.1),
    pins=[
        PinDef(name="base",      offset=(0.0,  0.0)),
        PinDef(name="emitter",   offset=(1.0, -1.0)),   # Qt y-down: -1 = visual top
        PinDef(name="collector", offset=(1.0,  1.0)),   # Qt y-down: +1 = visual bottom
    ],
    label_slots=["l"],
    tikz_keyword="pnp",
    default_span=(0.0, 0.0),
)

# ---------------------------------------------------------------------------
# Annotations
# ---------------------------------------------------------------------------

_OPEN = ComponentDef(
    kind="open",
    display_name="Voltage Annotation",
    category="Annotations",
    bbox=(0.0, -0.4, 2.0, 0.4),
    pins=[
        PinDef(name="in",  offset=(0.0, 0.0)),
        PinDef(name="out", offset=(2.0, 0.0)),
    ],
    label_slots=["v", "v^", "v_", "i", "i_"],
    tikz_keyword="open",
    default_span=(2.0, 0.0),
    resizable=True,
)

_SHORT = ComponentDef(
    kind="short",
    display_name="Current Annotation",
    category="Annotations",
    bbox=(0.0, -0.4, 2.0, 0.4),
    pins=[
        PinDef(name="in",  offset=(0.0, 0.0)),
        PinDef(name="out", offset=(2.0, 0.0)),
    ],
    label_slots=["i", "i_", "i^", "v", "v^", "v_"],
    tikz_keyword="short",
    default_span=(2.0, 0.0),
    resizable=True,
)

# ---------------------------------------------------------------------------
# Nodes (single-terminal)
# ---------------------------------------------------------------------------

_GROUND = ComponentDef(
    kind="ground",
    display_name="Ground",
    category="Nodes",
    bbox=(-0.5, 0.0, 0.5, 0.75),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=[],
    tikz_keyword="ground",
    default_span=(0.0, 0.0),
)

_RGROUND = ComponentDef(
    kind="rground",
    display_name="Reference Ground",
    category="Nodes",
    bbox=(-0.5, 0.0, 0.5, 0.5),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=[],
    tikz_keyword="rground",
    default_span=(0.0, 0.0),
)

_SGROUND = ComponentDef(
    kind="sground",
    display_name="Signal Ground",
    category="Nodes",
    bbox=(-0.5, 0.0, 0.5, 0.75),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=[],
    tikz_keyword="sground",
    default_span=(0.0, 0.0),
)

_NGROUND = ComponentDef(
    kind="nground",
    display_name="Noiseless Ground",
    category="Nodes",
    bbox=(-0.75, 0.0, 0.75, 0.75),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=[],
    tikz_keyword="nground",
    default_span=(0.0, 0.0),
)

_PGROUND = ComponentDef(
    kind="pground",
    display_name="Protective Earth",
    category="Nodes",
    bbox=(-0.75, 0.0, 0.75, 0.75),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=[],
    tikz_keyword="pground",
    default_span=(0.0, 0.0),
)

_CGROUND = ComponentDef(
    kind="cground",
    display_name="Chassis Ground",
    category="Nodes",
    bbox=(-0.5, 0.0, 0.5, 0.75),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=[],
    tikz_keyword="cground",
    default_span=(0.0, 0.0),
)

_EGROUND = ComponentDef(
    kind="eground",
    display_name="Earth Ground",
    category="Nodes",
    bbox=(-0.75, 0.0, 0.75, 0.75),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=[],
    tikz_keyword="eground",
    default_span=(0.0, 0.0),
)

# ---------------------------------------------------------------------------
# Power rails (single-terminal, positive supplies point up, negative down)
# ---------------------------------------------------------------------------

_VCC = ComponentDef(
    kind="vcc",
    display_name="VCC",
    category="Nodes",
    bbox=(-0.5, -0.75, 0.5, 0.0),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=["l"],
    tikz_keyword="vcc",
    default_span=(0.0, 0.0),
)

_VDD = ComponentDef(
    kind="vdd",
    display_name="VDD",
    category="Nodes",
    bbox=(-0.5, -0.75, 0.5, 0.0),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=["l"],
    tikz_keyword="vdd",
    default_span=(0.0, 0.0),
)

_VEE = ComponentDef(
    kind="vee",
    display_name="VEE",
    category="Nodes",
    bbox=(-0.5, 0.0, 0.5, 0.75),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=["l"],
    tikz_keyword="vee",
    default_span=(0.0, 0.0),
)

_VSS = ComponentDef(
    kind="vss",
    display_name="VSS",
    category="Nodes",
    bbox=(-0.5, 0.0, 0.5, 0.75),
    pins=[PinDef(name="in", offset=(0.0, 0.0))],
    label_slots=["l"],
    tikz_keyword="vss",
    default_span=(0.0, 0.0),
)

# ---------------------------------------------------------------------------
# Drawing annotations (non-circuit visual elements)
# ---------------------------------------------------------------------------

_TEXT_NODE = ComponentDef(
    kind="text_node",
    display_name="Text",
    category="Drawing",
    # Placeholder bbox; TextNodeItem overrides boundingRect() dynamically.
    bbox=(-1.5, -0.4, 1.5, 0.4),
    pins=[],
    label_slots=[],
    tikz_keyword="text_node",
    default_span=(0.0, 0.0),
    component_class=TextNodeComponent,
)

_RECT = ComponentDef(
    kind="rect",
    display_name="Rectangle",
    category="Drawing",
    # Placeholder bbox matching default span; RectItem overrides boundingRect().
    bbox=(0.0, 0.0, 1.0, 1.0),
    pins=[],
    label_slots=[],
    tikz_keyword="rectangle",
    default_span=(1.0, 1.0),
    resizable=True,
    component_class=RectComponent,
)

_CIRCLE = ComponentDef(
    kind="circle",
    display_name="Circle",
    category="Drawing",
    # Placeholder bbox matching default span; CircleItem overrides boundingRect().
    bbox=(0.0, 0.0, 0.5, 0.5),
    pins=[],
    label_slots=[],
    tikz_keyword="circle",
    default_span=(0.5, 0.5),
    resizable=True,
    component_class=CircleComponent,
)

_BIPOLE = ComponentDef(
    kind="bipole",
    display_name="Bipole",
    category="Bipoles",
    # bbox matches standard bipole half-height (±0.25 GU).
    # BipoleItem overrides boundingRect() dynamically from span_override.
    bbox=(0.0, -0.25, 1.0, 0.25),
    pins=[
        PinDef(name="in",  offset=(0.0, 0.0)),
        PinDef(name="out", offset=(1.0, 0.0)),
    ],
    label_slots=["t", "l", "l_", "v", "v^", "i", "i_"],
    tikz_keyword="twoport",
    default_span=(1.0, 0.0),
    resizable=True,
    component_class=BipoleComponent,
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
        _ZENER,
        _SCHOTTKY,
        _TUNNEL,
        _TVS,
        _LED,
        _VSOURCE,
        _ISOURCE,
        _AC_VSOURCE,
        _AC_ISOURCE,
        _VCVS,
        _VCCS,
        _BIPOLE,
        _OPAMP,
        _NIGFETE,
        _NIGFETD,
        _PIGFETE,
        _PIGFETD,
        _NPN,
        _PNP,
        _OPEN,
        _SHORT,
        _GROUND,
        _RGROUND,
        _SGROUND,
        _NGROUND,
        _PGROUND,
        _CGROUND,
        _EGROUND,
        _VCC,
        _VDD,
        _VEE,
        _VSS,
        _TEXT_NODE,
        _RECT,
        _CIRCLE,
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
