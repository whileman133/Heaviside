"""
Component registry.

``REGISTRY`` maps each CircuiTikZ keyword to a :class:`ComponentDef`.  It is
assembled from two sources:

* the **CircuiTikZ-symbol kinds** (resistor, diodes, sources, op-amp, MOSFETs,
  BJTs, grounds, rails) are built from ``components/definitions.json`` via
  :func:`app.components.library.library_component_defs` — their pins, bbox, and
  alignment data are measured/generated, not hand-typed (see
  ``spec/component-editor.md``); and
* the **bespoke kinds** (the resizable annotations ``open``/``short`` and the
  drawing primitives ``bipole``/``rect``/``circle``/``text_node``) are defined as
  literals below — they are not derived from a CircuiTikZ command.

``_DISPLAY_ORDER`` is a *preference* for the within-category palette order
(§5.4) — kinds not named in it still appear (after the named ones), so it never
needs editing to add a component.

To add a plain CircuiTikZ component: measure it (``app/components/render.py``) and
add an entry to ``components/definitions.json`` (``components/generate_components.py``).
That's it — the registry, codegen, and canvas all derive from the data, and the
canvas item falls back to the generic ``ComponentItem``.  Only a component that
needs special item behaviour (a custom ``boundingRect``, hit-testing, or resize)
also needs a ``ComponentItem`` subclass + an ``ITEM_CLASSES`` row in
``app/canvas/items.py``; an unusual palette position can optionally be set in
``_DISPLAY_ORDER``.
"""

from __future__ import annotations

from app.components.library import library_component_defs
from app.components.model import (
    BipoleComponent,
    CircleComponent,
    ComponentDef,
    PinDef,
    RectComponent,
    TextNodeComponent,
)

# ---------------------------------------------------------------------------
# Bespoke (non-CircuiTikZ-command) kinds — hand-defined literals.
# ---------------------------------------------------------------------------

_OPEN = ComponentDef(
    kind="open",
    display_name="Voltage Annotation",
    category="Annotations",
    bbox=(0.0, -0.4, 2.0, 0.4),
    pins=[
        PinDef(name="in", offset=(0.0, 0.0)),
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
        PinDef(name="in", offset=(0.0, 0.0)),
        PinDef(name="out", offset=(2.0, 0.0)),
    ],
    label_slots=["i", "i_", "i^", "v", "v^", "v_"],
    tikz_keyword="short",
    default_span=(2.0, 0.0),
    resizable=True,
)

_BIPOLE = ComponentDef(
    kind="bipole",
    display_name="Bipole",
    category="Bipoles",
    # bbox matches standard bipole half-height (±0.25 GU).
    # BipoleItem overrides boundingRect() dynamically from span_override.
    bbox=(0.0, -0.25, 1.0, 0.25),
    pins=[
        PinDef(name="in", offset=(0.0, 0.0)),
        PinDef(name="out", offset=(1.0, 0.0)),
    ],
    label_slots=["t", "l", "l_", "v", "v^", "i", "i_"],
    tikz_keyword="twoport",
    default_span=(1.0, 0.0),
    resizable=True,
    component_class=BipoleComponent,
)

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

_BESPOKE: dict[str, ComponentDef] = {
    defn.kind: defn for defn in (_OPEN, _SHORT, _BIPOLE, _RECT, _CIRCLE, _TEXT_NODE)
}

# ---------------------------------------------------------------------------
# Registry assembly
# ---------------------------------------------------------------------------

# Within-category palette display order (§5.4).  Bipoles → Tripoles → Nodes →
# Annotations → Drawing; the per-category order is the sequence here.
_DISPLAY_ORDER: list[str] = [
    # Bipoles
    "R", "C", "L",
    "D", "zD", "sD", "tD", "zzD", "leD",
    "V", "I", "vsourcesin", "isourcesin", "cV", "cI",
    "bipole",
    # Tripoles
    "op amp",
    "nigfete", "nigfetd", "pigfete", "pigfetd",
    "npn", "pnp",
    # Annotations
    "open", "short",
    # Nodes
    "ground", "rground", "sground", "nground", "pground", "cground", "eground",
    "vcc", "vdd", "vee", "vss",
    # Drawing
    "text_node", "rect", "circle",
]

_ALL: dict[str, ComponentDef] = {**_BESPOKE, **library_component_defs()}

# ``_DISPLAY_ORDER`` is a *preference*, not an exhaustive list.  Kinds named in
# it are ordered as listed; any kind present in the data but not named falls in
# after them (then alphabetically).  The palette groups by category, so a new
# definitions.json entry simply appears at the end of its category — adding a
# component needs no edit here, while the curated order of known kinds is kept.
def _order_key(kind: str) -> tuple[int, str]:
    return (_DISPLAY_ORDER.index(kind) if kind in _DISPLAY_ORDER else len(_DISPLAY_ORDER),
            kind)


REGISTRY: dict[str, ComponentDef] = {
    kind: _ALL[kind] for kind in sorted(_ALL, key=_order_key)
}

# ---------------------------------------------------------------------------
# ITEM_CLASSES — populated by app/canvas/items.py at import time.
# Declared here so test_registry.py can verify completeness without importing Qt.
# ---------------------------------------------------------------------------

# The canvas module sets this at import time:
#   from app.components.registry import ITEM_CLASSES
#   ITEM_CLASSES.update({ "nigfete": _MosfetItem, ... })  # special-behaviour kinds only
#
# Before canvas is imported it is empty; test_registry tests against the
# canvas module directly (importing items.py) to verify the mapping.
ITEM_CLASSES: dict[str, type] = {}
