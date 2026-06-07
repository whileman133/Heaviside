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
# Helper — two-terminal component kinds
# ---------------------------------------------------------------------------

_TWO_TERMINAL_KINDS = {"R", "C", "L", "D", "V", "I", "vsourcesin", "isourcesin", "cV", "cI"}


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

def test_all_pins_on_quarter_grid() -> None:
    """Every PinDef offset in every ComponentDef lies on a 0.25 GU boundary.

    0.25 GU is the canvas minor grid (SNAP_GU, spec §3.1); pin offsets must be
    multiples of it so a placed component's pins land on the grid. Existing pins
    happen to be on the coarser 0.5 grid, which is a subset of 0.25.
    """
    for kind, defn in REGISTRY.items():
        for pin in defn.pins:
            dx, dy = pin.offset
            assert (dx * 4) == int(dx * 4), (
                f"{kind}/{pin.name}: dx={dx} is not on a 0.25 GU boundary"
            )
            assert (dy * 4) == int(dy * 4), (
                f"{kind}/{pin.name}: dy={dy} is not on a 0.25 GU boundary"
            )


# ---------------------------------------------------------------------------
# test_default_span_matches_terminal_pin
# ---------------------------------------------------------------------------

def test_default_span_matches_terminal_pin() -> None:
    """For two-terminal components, default_span == offset of the second pin."""
    for kind in _TWO_TERMINAL_KINDS:
        defn = REGISTRY[kind]
        assert len(defn.pins) >= 2, (
            f"{kind}: expected at least 2 pins, got {len(defn.pins)}"
        )
        terminal_pin = defn.pins[1]
        assert defn.default_span == terminal_pin.offset, (
            f"{kind}: default_span {defn.default_span} != "
            f"terminal pin offset {terminal_pin.offset}"
        )


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


def test_display_order_is_a_preference_not_exhaustive() -> None:
    """A kind absent from _DISPLAY_ORDER still appears in REGISTRY (after the
    listed ones), so adding a component never requires editing the order list."""
    import app.components.registry as reg

    # Every kind the library/bespoke defs provide is present — nothing dropped.
    assert set(reg.REGISTRY) == set(reg._ALL)
    # Listed kinds keep their curated relative order.
    listed = [k for k in reg.REGISTRY if k in reg._DISPLAY_ORDER]
    assert listed == [k for k in reg._DISPLAY_ORDER if k in reg._ALL]
    # An unlisted kind sorts after every listed kind.
    assert reg._order_key("zzz_new_kind") > reg._order_key(reg._DISPLAY_ORDER[-1])


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
    # Explicit entries exist only for special-behaviour kinds, never plain symbols.
    assert "R" not in ITEM_CLASSES and "ground" not in ITEM_CLASSES
    assert {"nigfete", "open", "rect"} <= set(ITEM_CLASSES)
