"""
Drag-resize for multi-terminal nodes (spec §6.4).

Driven by a corner handle, an **anisotropic** node stores
``span_override = (wf, hf)`` — the manual-library gates, the digital blocks
(flip-flops, ALU, adder) and the ``muxdemux``: every scalable kind *without* a
CircuiTikZ body-height key. (The manual library carries no height-keyed gate, so
the legacy **uniform** ``Component.scale`` flavour — ``_ResizableGateItem`` — is
never exercised by a real kind; its dead protocol test was removed.)

The ``muxdemux`` is the anisotropic vehicle here; the pin-grid magnet assertions
use ``ieeestd buffer port`` (its ±0.9/±0.6 corner pins give a clean magnet at the
identity scale, unlike the muxdemux's denser data lines).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - host-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.schematic.model import (  # noqa: E402
    Component,
    Schematic,
    component_pin_positions,
    is_resizable_node,
    node_resize_factors,
)


def _comp(kind, **kw):
    return Component(id="a", kind=kind, position=(2.0, 2.0), rotation=0,
                     options="", **kw)


def _sch(*comps):
    return Schematic(version="0.1", name="t", components=list(comps))


def test_node_resize_factors_pure():
    """``node_resize_factors`` returns span_override for an anisotropic resizable
    node, and None for a non-scalable kind or an unresized instance."""
    assert is_resizable_node("muxdemux")     # digital block: anisotropic
    assert not is_resizable_node("R")        # two-terminal, not scalable
    c = _comp("muxdemux")
    assert node_resize_factors(c) is None    # unresized
    c.span_override = (2.0, 1.5)
    assert node_resize_factors(c) == (2.0, 1.5)
    assert node_resize_factors(_comp("R", span_override=(2.0, 1.5))) is None


def test_pins_scale_anisotropically_about_origin():
    """An anisotropic node's pin offsets scale independently in x/y about the origin."""
    base = _comp("muxdemux")
    scaled = _comp("muxdemux", span_override=(2.0, 0.5))
    for (bx, by), (sx, sy) in zip(component_pin_positions(base),
                                  component_pin_positions(scaled)):
        assert sx - 2.0 == pytest.approx((bx - 2.0) * 2.0)
        assert sy - 2.0 == pytest.approx((by - 2.0) * 0.5)


def test_snap_resize_factor_continuous_and_magnetic():
    """The pure snap helper is continuous between pin-aligning sizes and magnetic
    near them, and floors at the minimum (spec §6.4)."""
    from app.schematic.model import snap_resize_factor

    offs = [0.75, -0.75]                          # pin aligns when 0.75*f ∈ 0.25ℤ
    assert snap_resize_factor(1.0, offs) == pytest.approx(1.0)    # exact
    assert snap_resize_factor(1.01, offs) == pytest.approx(1.0)   # inside the magnet
    assert snap_resize_factor(1.12, offs) == pytest.approx(1.12)  # continuous band
    assert snap_resize_factor(0.01, offs, minimum=0.25) == pytest.approx(0.25)  # floor


def _corner_world(comp, corner, factors):
    """World position of an unscaled body *corner* given *factors* (the model's
    rotate-then-mirror transform about the component origin)."""
    from app.canvas.geometry import local_span_to_world
    w = local_span_to_world((corner[0] * factors[0], corner[1] * factors[1]),
                            comp.rotation, comp.mirror)
    return (round(comp.position[0] + w[0], 6), round(comp.position[1] + w[1], 6))


def test_resize_is_continuous_with_pin_grid_snap():
    """A corner drag resizes continuously, snapping only near sizes that land a pin
    on the grid (the old behaviour snapped the corner to the 0.25 GU grid)."""
    from app.canvas.scene import SchematicScene

    sc = SchematicScene()
    m = sc.place_component("ieeestd buffer port", (4.0, 4.0))
    item = sc._comp_items[m.id]
    gx, gy = item._corner_gu()
    ax, ay = item._opposite_corner((gx, gy))
    # Cursor placing the raw factor at 1.20 (between two pin-aligning sizes) →
    # returned verbatim; at 1.005 (near an aligning size) → pulled onto 1.0.
    (wf, hf), _ = item.resize_from_local(1.20 * (gx - ax) + ax, 1.20 * (gy - ay) + ay)
    assert wf == pytest.approx(1.20) and hf == pytest.approx(1.20)
    (wf2, _), _ = item.resize_from_local(1.005 * (gx - ax) + ax, 1.005 * (gy - ay) + ay)
    assert wf2 == pytest.approx(1.0)


def test_resize_holds_opposite_corner_for_every_grab():
    """All four corners are handles, and grabbing any one resizes with the
    diagonally-opposite corner held fixed — so the grabbed corner tracks the cursor
    at the same rate from whichever corner (the user-visible 'consistent drag')."""
    from app.canvas.scene import SchematicScene
    from app.schematic.model import node_resize_factors
    from PySide6.QtCore import QPointF
    import dataclasses

    sc = SchematicScene()
    m = sc.place_component("muxdemux", (4.0, 4.0))
    item = sc._comp_items[m.id]
    item.setSelected(True)
    base = item.component
    f0 = node_resize_factors(base) or (1.0, 1.0)
    corners = item._corners_gu()
    handles = item._handle_positions()
    assert len(set(corners)) == 4 and len(handles) == 4
    for (gx, gy), hp in zip(corners, handles):
        assert item.resize_handle_at(QPointF(hp.x(), hp.y()))
        assert item._active_corner == (gx, gy)
        opp = item._opposite_corner((gx, gy))
        opp_world0 = _corner_world(base, opp, f0)
        # Push the grabbed corner outward (away from the centre) by 0.5 GU/axis.
        glx, gly = gx * f0[0], gy * f0[1]
        (wf, hf), pos = item.resize_from_local(glx + (0.5 if glx >= 0 else -0.5),
                                               gly + (0.5 if gly >= 0 else -0.5))
        moved = dataclasses.replace(base, span_override=(wf, hf), position=pos)
        # The opposite corner is held exactly fixed; the body grew.
        assert _corner_world(moved, opp, (wf, hf)) == pytest.approx(opp_world0, abs=1e-6)
        assert wf > 1.0 and hf > 1.0


def test_resize_commit_does_not_move_component():
    """Committing a corner resize leaves the body exactly where the live preview
    showed it — the anchored origin shift is stored exactly (not grid-snapped), so the
    component does not jump on release. (Regression: a grid-snap at commit shifted it
    by up to 0.125 GU.)"""
    from app.canvas.scene import SchematicScene
    from PySide6.QtCore import QPointF

    sc = SchematicScene()
    m = sc.place_component("muxdemux", (4.0, 4.0))
    item = sc._comp_items[m.id]
    item.setSelected(True)
    corners = item._corners_gu()
    (gx, gy), hp = corners[0], item._handle_positions()[0]
    assert item.resize_handle_at(QPointF(hp.x(), hp.y()))
    # A drag landing in the continuous band → an off-grid anchored position.
    value = item.resize_from_local(gx - 0.37 if gx < 0 else gx + 0.37,
                                   gy - 0.37 if gy < 0 else gy + 0.37)
    (_, _), pos = value
    cmd = item.resize_command(value, item.resize_value())
    cmd.do(sc._schematic)
    assert sc._schematic.components[0].position == pytest.approx(pos)


def test_resize_preview_moves_item_to_anchored_origin():
    """The live preview translates the item to the anchored origin (not just the
    model), so the body sits where it will land on commit — no jump on release.
    (Regression: the preview mutated only the model position, so the origin shift
    appeared only on the commit rebuild.)"""
    from app.canvas.scene import SchematicScene
    from app.canvas.style import GRID_PX
    from PySide6.QtCore import QPointF

    sc = SchematicScene()
    m = sc.place_component("muxdemux", (4.0, 4.0))
    item = sc._comp_items[m.id]
    item.setSelected(True)
    (gx, gy), hp = item._corners_gu()[0], item._handle_positions()[0]
    assert item.resize_handle_at(QPointF(hp.x(), hp.y()))
    value = item.resize_from_local(gx - 0.5 if gx < 0 else gx + 0.5,
                                   gy - 0.5 if gy < 0 else gy + 0.5)
    (_, _), pos = value
    item.apply_resize_preview(value)
    assert item.pos().x() == pytest.approx(pos[0] * GRID_PX)
    assert item.pos().y() == pytest.approx(pos[1] * GRID_PX)


def test_resize_keeps_each_wire_on_its_own_pin():
    """Resizing a densely-pinned node re-routes every connected wire to **its own**
    pin's new position — never another pin's. (Regression: a sequential per-pin
    reshape mis-assigned wires when one pin's new position coincided with another's
    old position; the command applies one simultaneous old→new map.)"""
    from app.canvas.commands import ResizeNodeCommand, UndoStack
    from app.schematic.model import Wire, component_pin_positions, point_key
    from app.components import library

    m = _comp("muxdemux", params={"inputs": 6})
    pins = dict(zip([p.name for p in library.resolved_pins(m)],
                    component_pin_positions(m)))
    targets = ["in0", "in1", "in2"]
    wires = [Wire(id=f"w{i}", points=[(-2.0, pins[n][1]), pins[n]])
             for i, n in enumerate(targets)]
    sch = _sch(m)
    sch.wires.extend(wires)
    UndoStack(sch).push(ResizeNodeCommand("a", (1.3, 1.6), None))

    m2 = sch.components[0]
    new_pins = dict(zip([p.name for p in library.resolved_pins(m2)],
                        component_pin_positions(m2)))
    by_key = {point_key(v): k for k, v in new_pins.items()}
    for w, name in zip(sch.wires, targets):
        assert by_key.get(point_key(w.points[-1])) == name, w.id


def test_resize_node_command_round_trip():
    """ResizeNodeCommand sets the factors and undo restores them."""
    from app.canvas.commands import ResizeNodeCommand, UndoStack

    stack = UndoStack(_sch(_comp("muxdemux")))
    stack.push(ResizeNodeCommand("a", (2.0, 1.5), None))
    assert stack.schematic.components[0].span_override == (2.0, 1.5)
    stack.undo()
    assert stack.schematic.components[0].span_override is None
    stack.redo()
    assert stack.schematic.components[0].span_override == (2.0, 1.5)


def test_codegen_folds_resize_factors_independently():
    """Codegen multiplies the baked alignment scale by the per-instance factors, so
    width and height scale independently (xscale ≠ yscale when wf ≠ hf)."""
    from app.codegen.circuitikz import generate
    import re

    sch = _sch(_comp("muxdemux", span_override=(2.0, 1.0)))
    line = next(ln for ln in generate(sch).splitlines() if "mux" in ln)
    assert "xscale=" in line
    xs = float(re.search(r"xscale=(-?[\d.]+)", line).group(1))
    ys_m = re.search(r"yscale=([\d.]+)", line)
    ys = float(ys_m.group(1)) if ys_m else 1.0
    assert xs == pytest.approx(ys * 2.0, rel=1e-3)


def test_item_class_split_anisotropic_vs_uniform():
    """Scalable kinds map to the right item: height-keyed gates → uniform; every
    other scalable kind (blocks, mux/demux) → anisotropic."""
    from app.canvas.items import ITEM_CLASSES, _ResizableGateItem, _ResizableNodeItem
    from app.components.registry import REGISTRY
    from app.components import library

    scalable = [k for k in REGISTRY if library.is_scalable(k)]
    assert scalable
    for k in scalable:
        cls = ITEM_CLASSES.get(k)
        if library.gate_uses_height(k):
            assert cls is _ResizableGateItem, f"{k}: {cls}"
        else:
            assert cls is _ResizableNodeItem, f"{k}: {cls}"


def test_anisotropic_node_independent_factors():
    """A corner drag on an anisotropic node yields independent (wf, hf); its command
    is the ResizeNodeCommand (now carrying the anchored position shift)."""
    from app.canvas.scene import SchematicScene
    from app.canvas.commands import ResizeNodeCommand

    sc = SchematicScene()
    m = sc.place_component("muxdemux", (4.0, 4.0))
    item = sc._comp_items[m.id]
    gx, gy = item._corner_gu()
    ax, ay = item._opposite_corner((gx, gy))
    # Stretch the grabbed corner farther in x than y → wf > hf (independent axes).
    (wf, hf), _ = item.resize_from_local(2.0 * (gx - ax) + ax, 1.5 * (gy - ay) + ay)
    assert wf > hf > 1.0
    assert wf == pytest.approx(2.0, abs=0.2) and hf == pytest.approx(1.5, abs=0.2)
    cmd = item.resize_command(((2.0, 1.5), (4.0, 4.0)), (None, (4.0, 4.0)))
    assert isinstance(cmd, ResizeNodeCommand)
# NOTE: ``test_height_gate_uniform_resize_protocol`` was removed. It exercised the
# uniform (``Component.scale`` / ``_ResizableGateItem``) corner-drag protocol via a
# curated height-keyed gate (``and``). The manual library carries no height-keyed
# gate (``library.gate_uses_height`` is False for every kind), so no real component
# selects that item class — the protocol it tested is unreachable from the active
# library. ``test_item_class_split_anisotropic_vs_uniform`` still pins the mapping
# rule itself (height-keyed → uniform), so the rule stays covered.
