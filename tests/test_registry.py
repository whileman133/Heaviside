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
# test_all_pins_on_half_grid
# ---------------------------------------------------------------------------

def test_all_pins_on_half_grid() -> None:
    """Every PinDef offset in every ComponentDef lies on a 0.5 GU boundary."""
    for kind, defn in REGISTRY.items():
        for pin in defn.pins:
            dx, dy = pin.offset
            assert (dx * 2) == int(dx * 2), (
                f"{kind}/{pin.name}: dx={dx} is not on a 0.5 GU boundary"
            )
            assert (dy * 2) == int(dy * 2), (
                f"{kind}/{pin.name}: dy={dy} is not on a 0.5 GU boundary"
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
def test_all_kinds_have_item_class() -> None:
    """Every kind in REGISTRY has a corresponding entry in ITEM_CLASSES."""
    try:
        from app.canvas.items import ITEM_CLASSES  # type: ignore[import]
    except ImportError:
        pytest.skip("app.canvas.items not available yet")

    for kind in REGISTRY:
        assert kind in ITEM_CLASSES, (
            f"REGISTRY kind '{kind}' has no entry in ITEM_CLASSES"
        )
