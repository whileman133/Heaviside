"""
Component registry.

``REGISTRY`` maps each CircuiTikZ keyword to a :class:`ComponentDef`.  It is
assembled from two sources:

* the **CircuiTikZ-symbol kinds** (resistor, diodes, sources, op-amp, MOSFETs,
  BJTs, grounds, rails) are built from ``components/definitions.json`` via
  :func:`app.components.library.library_component_defs` — their pins, bbox, and
  alignment data are measured/generated, not hand-typed (see
  ``spec/component-pipeline.md``); and
* the **bespoke kinds** (the resizable annotations ``open``/``short`` and the
  drawing primitives ``bipole``/``rect``/``circle``/``text_node``) are defined as
  literals below — they are not derived from a CircuiTikZ command.

The library kinds are kept in the data file's scrape order (the manual's own
component sequence); the bespoke annotation/drawing kinds follow them. The
palette groups by category and sorts within a category itself (§5.4).

To add a plain CircuiTikZ component: measure it (``app/components/render.py``) and
add an entry to ``components/generated/definitions.json``
(``components/generate_library.py``). That's it — the registry, codegen, and canvas
all derive from the data, and the canvas item falls back to the generic
``ComponentItem``.  Only a component that needs special item behaviour (a custom
``boundingRect``, hit-testing, or resize) also needs a ``ComponentItem`` subclass +
an ``ITEM_CLASSES`` row in ``app/canvas/items.py``.
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
    # The CircuiTikZ manual documents `open`/`short` under "Resistive bipoles", so
    # they live in the **Resistors** category rather than an invented "Annotations"
    # group with no manual counterpart.
    category="Resistors",
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
    category="Resistors",   # manual section "Resistive bipoles" — see _OPEN above
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
    display_name="Generic Bipole",
    category="Misc",
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

_LIB: dict[str, ComponentDef] = library_component_defs()

# A bespoke kind with no library entry but that the manual documents **next to** a
# library kind is placed right after that sibling, so it keeps the manual's order
# within its category (``open`` follows ``short`` in "Resistive bipoles").
_BESPOKE_AFTER: dict[str, str] = {"open": "short"}

# The library's ``definitions.json`` is written in manual order (`generate_library`
# sorts by source position) and is the authoritative sequence, so we preserve it. A
# bespoke kind (resizable annotations + drawing primitives) **wins over** any same-named
# library kind; crucially it takes that library kind's **position** (an in-place
# override) instead of being appended — so ``short`` stays where the manual lists it
# (front of Resistors) rather than drifting to the end. ``open`` (no library entry) is
# anchored right after ``short`` via ``_BESPOKE_AFTER``; the remaining purely-bespoke
# kinds (the Drawing primitives, the Misc bipole) append at the end, forming their own
# trailing categories.
REGISTRY: dict[str, ComponentDef] = {}
for _k, _v in _LIB.items():
    REGISTRY[_k] = _BESPOKE.get(_k, _v)          # in-place override (e.g. short)
    for _bk, _anchor in _BESPOKE_AFTER.items():
        if _anchor == _k and _bk in _BESPOKE:
            REGISTRY[_bk] = _BESPOKE[_bk]         # open, right after short
for _k, _v in _BESPOKE.items():                   # rect/circle/text_node/bipole, …
    REGISTRY.setdefault(_k, _v)

# ---------------------------------------------------------------------------
# Runtime (document-scoped) custom components
# ---------------------------------------------------------------------------
#
# Custom components (app.components.custom) live on the open document, not in the
# bundled library, so they are injected into REGISTRY at runtime and scrubbed when
# another document loads. Built-in lookups (REGISTRY[kind], the palette, validation)
# then see them transparently. Codegen and the canvas resolve a custom kind's
# classification, pin→anchor map and geometry via its base kind (no rebuild of the
# import-time codegen frozensets is needed — see app.codegen.circuitikz and
# app.canvas.svgsym). The Qt geometry store is touched through a lazy import so this
# module stays Qt-free for headless codegen/io tests.

_RUNTIME_KINDS: set[str] = set()


def _inject_geometry(kind: str, geometry: dict | None) -> None:
    """Push a custom kind's captured geometry into the canvas geometry store."""
    if geometry is None:
        return
    try:
        from app.canvas import svgsym
    except Exception:  # pragma: no cover - canvas/Qt unavailable (headless tools)
        return
    from app.components.library import geometry_key
    svgsym.set_runtime_geometry(geometry_key(kind), geometry)
    svgsym.symbol_paths.cache_clear()


def _clear_geometry() -> None:
    try:
        from app.canvas import svgsym
    except Exception:  # pragma: no cover - canvas/Qt unavailable
        return
    svgsym.clear_runtime_geometry()
    svgsym.symbol_paths.cache_clear()


def register_runtime_component(defn: ComponentDef) -> None:
    """Add a custom :class:`ComponentDef` to the live registry (and its geometry to
    the canvas store). Idempotent for a given kind — re-registering replaces it."""
    REGISTRY[defn.kind] = defn
    _RUNTIME_KINDS.add(defn.kind)
    _inject_geometry(defn.kind, defn.geometry)


def reset_runtime_components() -> None:
    """Remove every runtime-registered custom component, restoring the import-time
    registry. Called before switching the active document."""
    for kind in _RUNTIME_KINDS:
        REGISTRY.pop(kind, None)
    _RUNTIME_KINDS.clear()
    _clear_geometry()


def runtime_component_kinds() -> frozenset[str]:
    """The kinds currently registered as runtime custom components."""
    return frozenset(_RUNTIME_KINDS)


def sync_runtime_components(schematic) -> None:
    """Make the live registry reflect exactly *schematic*'s custom components:
    scrub the previous document's customs, then register this document's. The single
    entry point the UI calls whenever the active document changes."""
    from app.components.custom import spec_to_component_def

    reset_runtime_components()
    for spec in getattr(schematic, "custom_components", {}).values():
        register_runtime_component(spec_to_component_def(spec))


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
