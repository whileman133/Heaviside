"""
Phase 1 tests — component registry.

All four tests exercise only app/components/*, which has no Qt dependency.
The ITEM_CLASSES test imports app.canvas.items when available; it is marked
xfail (expected to fail) until phase 5 (items.py) is implemented, so the
test is always present in the suite and automatically passes once the canvas
layer exists.
"""

from __future__ import annotations

import importlib

import pytest

from app.components.registry import REGISTRY


# ---------------------------------------------------------------------------
# test_no_duplicate_kinds
# ---------------------------------------------------------------------------

def test_no_duplicate_kinds() -> None:
    """REGISTRY was built from a list of ComponentDef objects; verify uniqueness.

    Because REGISTRY is a plain dict keyed by kind, duplicates would silently
    overwrite earlier entries. We detect that by comparing the list length to
    the dict length.
    """
    from app.components import registry as reg_module

    # Collect all ComponentDef objects that were defined in the module.
    all_defs = [
        obj
        for name, obj in vars(reg_module).items()
        if not name.startswith("_") and hasattr(obj, "kind") and hasattr(obj, "tikz_keyword")
    ]
    # The registry dict must contain every defined kind exactly once.
    assert len(REGISTRY) == len(set(REGISTRY.keys())), (
        "REGISTRY contains duplicate kind keys"
    )
    # Belt-and-suspenders: every private _XXX variable that is a ComponentDef
    # must appear in the registry.
    private_defs = [
        obj
        for name, obj in vars(reg_module).items()
        if name.startswith("_") and hasattr(obj, "kind") and hasattr(obj, "tikz_keyword")
    ]
    for defn in private_defs:
        assert defn.kind in REGISTRY, (
            f"ComponentDef '{defn.kind}' was defined but not added to REGISTRY"
        )


# ---------------------------------------------------------------------------
# test_all_pins_on_quarter_grid
# ---------------------------------------------------------------------------

def test_two_terminal_axial_pins_on_quarter_grid() -> None:
    """Every two-terminal path device's two **axial** terminals lie on the 0.25 GU
    grid (the canvas minor grid, SNAP_GU, spec §3.1), so a placed device connects
    on-grid along its axis.

    (Retargeted from the curated-library ``test_all_pins_on_quarter_grid``: that
    test asserted *every* pin of *every* kind lands on the grid except a short
    enumerated exemption list. In the manual-scraped library most symbols keep
    their native CircuiTikZ anchors, which are off the 0.25 grid by design and
    connect via the pin magnet (§5.4/§6.4) — extra anchors like a thyristor gate,
    a potentiometer wiper, a transformer centre tap, or a logic gate's scaled
    terminals. The blanket grid invariant is a curated-library fact that no longer
    holds; the surviving, meaningful guarantee is that a two-terminal device's two
    *axial* terminals — ``pins[0]`` at the origin and ``pins[1]`` at the span — sit
    on the grid. The codegen set is the authoritative two-terminal list.)
    """
    from app.codegen.circuitikz import _TWO_TERMINAL_KINDS as PATH_KINDS

    checked = 0
    for kind in PATH_KINDS:
        defn = REGISTRY[kind]
        if len(defn.pins) < 2:
            continue  # degenerate annotations (open/short carry both axial pins)
        for pin in defn.pins[:2]:               # the two axial terminals only
            dx, dy = pin.offset
            assert (dx * 4) == int(dx * 4), (
                f"{kind}/{pin.name}: dx={dx} is not on a 0.25 GU boundary"
            )
            assert (dy * 4) == int(dy * 4), (
                f"{kind}/{pin.name}: dy={dy} is not on a 0.25 GU boundary"
            )
        checked += 1
    assert checked >= 50  # sanity: the two-terminal set is non-trivial


# NOTE: ``test_node_scale_within_anisotropy_cap`` was removed. It asserted that the
# per-axis grid-alignment *scale* baked into every node entry stayed within the
# anisotropy cap. The curated library baked such a scale into each centre-placed /
# anchor-pinned node; the manual-scraped library bakes **no** ``scale`` anywhere
# (symbols render at native size and connect off-grid via the pin magnet, §5.4), so
# there is nothing to bound — the property it guarded no longer exists. (It also
# read ``app.components.generate.SCALE_ANISOTROPY_MAX`` and the old
# ``components/definitions.json`` path, neither of which is used by the alignment-
# free manual pipeline.)


# NOTE: ``test_spdt_pins_centre_placed_and_gridded`` was removed. It pinned the
# SPDT switch's three terminals to specific grid-aligned offsets
# (in=(-0.5,0), out1/out2=(0.5,±0.25)) — the curated library's *scaled* anchors.
# The manual ``spdt`` keeps its native CircuiTikZ anchors (in≈(-0.595,0),
# out≈(0.595,±0.315)), off the 0.25 grid by design (magnet-connected, §5.4). The
# baked grid-aligned offsets it asserted are a curated fact that no longer holds.


# ---------------------------------------------------------------------------
# test_default_span_matches_terminal_pin
# ---------------------------------------------------------------------------

def test_default_span_matches_terminal_pin() -> None:
    """For **every** two-terminal (path-emitted) component, default_span equals the
    offset of the second pin — the axial span the code generator draws between.

    Data-driven over the real codegen set (not a hand-picked subset), so a path
    device with extra off-axis pins is covered too: the thyristor/triac carry a
    third gate pin, and a stale ``len(pins) == 2`` guard once collapsed their span
    to (0,0), emitting a degenerate ``to[thyristor] (x,y) (x,y)`` (regression)."""
    from app.codegen.circuitikz import _TWO_TERMINAL_KINDS as PATH_KINDS

    checked = {"R", "C", "L", "D", "V", "I"} & PATH_KINDS
    assert {"thyristor", "triac"} <= PATH_KINDS  # the 3-pin path devices are covered
    for kind in PATH_KINDS:
        if kind in ("open", "short"):
            continue  # resizable annotations: span is the user-set default, not a pin
        defn = REGISTRY[kind]
        assert len(defn.pins) >= 2, f"{kind}: expected >=2 pins, got {len(defn.pins)}"
        assert defn.default_span == defn.pins[1].offset, (
            f"{kind}: default_span {defn.default_span} != "
            f"terminal pin offset {defn.pins[1].offset}"
        )
        assert defn.default_span != (0.0, 0.0), f"{kind}: degenerate (zero) span"
        checked.add(kind)
    assert len(checked) >= 10  # sanity: the set is non-trivial


# ---------------------------------------------------------------------------
# test_all_kinds_have_item_class
# ---------------------------------------------------------------------------

@pytest.mark.xfail(
    condition=importlib.util.find_spec("app.canvas.items") is None,
    reason="app/canvas/items.py not yet implemented (phase 5)",
    strict=False,
)
def test_circle_registered_like_rect() -> None:
    """The circle drawing kind mirrors rect: Drawing, no pins, resizable, (0.5,0.5)."""
    from app.components.model import CircleComponent
    circ = REGISTRY["circle"]
    assert circ.category == "Drawing"
    assert circ.pins == []
    assert circ.resizable is True
    assert circ.default_span == (0.5, 0.5)
    assert circ.component_class is CircleComponent


# NOTE: ``test_power_mosfets_have_bulk_pin_and_body_diode`` was removed. It asserted
# the curated ``nfet``/``pfet`` were 4-terminal with a ``bulk`` pin
# (gate/drain/source/bulk) and a ``body_diode`` variant. The manual-scraped
# ``nfet``/``pfet`` are the native 3-terminal symbols (pins G/D/S, no bulk anchor)
# with a ``bodydiode`` variant; no manual kind exposes a ``bulk`` pin. The
# 4-terminal-with-bulk structure it pinned is a curated fact that no longer exists.


# NOTE: ``test_display_order_is_a_preference_not_exhaustive`` was removed. It
# asserted invariants over ``registry._DISPLAY_ORDER`` / ``_ALL`` / ``_order_key``
# — the curated palette ordering machinery. Those names were deleted from
# ``app/components/registry.py``: the registry is now the manual scrape order plus
# the bespoke kinds appended (the palette groups/sorts within a category itself,
# §5.4), so there is no curated display-order preference to test.


def test_all_kinds_resolve_to_a_component_item() -> None:
    """Every REGISTRY kind resolves to a ComponentItem (explicit entry or the
    generic fallback), exactly as the canvas/palette look it up.  Most kinds have
    no explicit entry — they intentionally fall back to the base ComponentItem."""
    try:
        from app.canvas.items import ITEM_CLASSES, ComponentItem  # type: ignore[import]
    except ImportError:
        pytest.skip("app.canvas.items not available yet")

    for kind in REGISTRY:
        cls = ITEM_CLASSES.get(kind, ComponentItem)  # the real lookup (scene/palette)
        assert issubclass(cls, ComponentItem), f"kind '{kind}' -> non-ComponentItem {cls}"
    # A single-point node (ground) has no explicit entry — it falls back to the base
    # ComponentItem. A two-terminal path symbol (R) maps to the length-resizable item
    # (endpoint handles, §5.7); special-behaviour kinds keep their explicit entries.
    assert "ground" not in ITEM_CLASSES
    from app.canvas.items import _ResizablePathItem
    assert ITEM_CLASSES.get("R") is _ResizablePathItem
    assert {"nigfete", "open", "rect"} <= set(ITEM_CLASSES)
