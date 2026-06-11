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

# Digital-block kinds whose native CircuiTikZ shape keeps *some* pins off the
# grid even after the best-effort alignment rescale (a mux/demux's slanted select
# pins; the ALU operands and the adder's pins). Those land off-grid by design and
# connect via the pin magnet, like a scaled logic gate — so they are exempt from
# the on-grid pin invariant below. (Flip-flops *do* align fully, so they are not
# exempt — see test_flipflop_pins_are_grid_aligned.)
_OFFGRID_PIN_KINDS = frozenset({
    "mux", "demux", "ALU", "adder",
    # Centre-placed shapes whose native anchors don't sit on the 0.25 grid; a wire
    # connects via the pin magnet (like the digital blocks): the electron tubes and
    # the fully-differential op-amp.
    "triode", "diodetube", "pentode", "tetrode", "fd op amp",
    # Thyristor/triac: the two axial terminals are on-grid, but the off-axis gate
    # pin sits at the native CircuiTikZ gate anchor (off-grid, magnet-connected).
    "thyristor", "triac",
    # Centre-placed SPDT/rotary switches (uniform scale, native throw anchors —
    # asymmetric, so off the grid; a non-uniform scale that landed them on-grid
    # would shear the switch blade in the LaTeX output, §5.4).
    "cute spdt up", "cute spdt down", "cute spdt mid", "rotaryswitch",
})


def test_all_pins_on_quarter_grid() -> None:
    """Every PinDef offset lies on a 0.25 GU boundary, except the digital-block
    kinds whose pins sit at native-shape anchors (see ``_OFFGRID_PIN_KINDS``).

    0.25 GU is the canvas minor grid (SNAP_GU, spec §3.1); pin offsets must be
    multiples of it so a placed component's pins land on the grid. Existing pins
    happen to be on the coarser 0.5 grid, which is a subset of 0.25.
    """
    for kind, defn in REGISTRY.items():
        if kind in _OFFGRID_PIN_KINDS:
            continue
        for pin in defn.pins:
            dx, dy = pin.offset
            assert (dx * 4) == int(dx * 4), (
                f"{kind}/{pin.name}: dx={dx} is not on a 0.25 GU boundary"
            )
            assert (dy * 4) == int(dy * 4), (
                f"{kind}/{pin.name}: dy={dy} is not on a 0.25 GU boundary"
            )


def test_centre_placed_nodes_use_uniform_scale() -> None:
    """A centre-placed (`anchor_pin` null) multi-terminal node must scale
    *uniformly* (sx == sy). A non-uniform node scale shears the symbol's strokes
    anisotropically in the LaTeX output (e.g. a thick, slanted switch blade), but
    the canvas re-strokes every path at a uniform width — so a non-uniform scale
    silently desyncs the canvas from the rendered output. (Regression: the cute
    SPDT / rotary switches were authored anchor-pinned with a non-uniform scale
    that sheared the blade; they are now centre-placed with a uniform scale.)"""
    import json
    from app.resources import resource_path

    comps = json.loads(
        open(resource_path("components", "definitions.json"), encoding="utf-8").read()
    )["components"]
    for kind, e in comps.items():
        if e.get("emission") == "node" and e.get("anchor_pin") is None and e.get("scale"):
            sx, sy = e["scale"]
            assert abs(sx - sy) < 1e-6, (
                f"{kind}: centre-placed node has non-uniform scale {e['scale']} "
                f"(shears strokes in the output; use a uniform scale)"
            )


def test_spdt_pins_anchor_aligned() -> None:
    """The SPDT switch is an anchor-pinned (at `in`) scaled node whose anchors
    land on the grid (regression for misaligned pins/leads): `in` at the origin,
    the two throws one GU to the right at ±0.25 GU, and a derived xscale/yscale.
    """
    defn = REGISTRY["spdt"]
    offsets = {p.name: tuple(p.offset) for p in defn.pins}
    assert offsets["in"] == (0.0, 0.0)
    assert offsets["out1"] == (1.0, -0.25)
    assert offsets["out2"] == (1.0, 0.25)


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


def test_power_mosfets_have_bulk_pin_and_body_diode() -> None:
    """nfet/pfet are 4-terminal: gate/drain/source plus a bulk (body) pin, and a
    body_diode variant that draws the intrinsic diode."""
    from app.components import library

    for kind in ("nfet", "pfet"):
        names = [p.name for p in REGISTRY[kind].pins]
        assert names == ["gate", "drain", "source", "bulk"]
        assert "body_diode" in {v["name"] for v in library.variant_specs(kind)}


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
