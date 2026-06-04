"""
Unit tests for app/canvas/wiregeometry.py — wire snapping / hit-testing.

WireGeometry reads a Schematic through a getter and uses only pure geometry
helpers, so these tests need no QGraphicsScene and no QApplication.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF

from app.canvas.style import GRID_PX
from app.canvas.wiregeometry import WireGeometry
from app.schematic.model import Component, Schematic, Wire


def _scene_pt(x_gu: float, y_gu: float) -> QPointF:
    return QPointF(x_gu * GRID_PX, y_gu * GRID_PX)


def _wg(*components, wires=()) -> WireGeometry:
    sch = Schematic(
        version="0.1", name="t",
        components=list(components), wires=list(wires),
    )
    return WireGeometry(lambda: sch)


def _r(cid: str, pos) -> Component:
    # R at pos has pins at pos and pos + (2, 0).
    return Component(id=cid, kind="R", position=pos, rotation=0, options="")


def test_nearest_pin_within_and_outside_radius():
    wg = _wg(_r("r1", (0.0, 0.0)))
    assert wg.nearest_pin((0.1, 0.0)) == (0.0, 0.0)   # within PIN_SNAP_GU (0.125)
    assert wg.nearest_pin((0.2, 0.0)) is None          # outside 0.125


def test_all_pin_positions():
    wg = _wg(_r("r1", (0.0, 0.0)), _r("r2", (0.0, 3.0)))
    assert wg.all_pin_positions() == {(0.0, 0.0), (2.0, 0.0), (0.0, 3.0), (2.0, 3.0)}


def test_wire_snap_target_priority_pin_over_wire():
    # A pin and a wire vertex both near the cursor: pin wins, is_connectable True.
    w = Wire(id="w1", points=[(0.0, 0.0), (0.0, 4.0)])
    wg = _wg(_r("r1", (0.0, 0.0)), wires=[w])
    pt, connectable = wg.wire_snap_target((0.1, 0.0))
    assert pt == (0.0, 0.0)
    assert connectable is True


def test_wire_snap_target_falls_back_to_grid():
    wg = _wg()
    pt, connectable = wg.wire_snap_target((5.0, 5.0))
    assert pt == (5.0, 5.0)
    assert connectable is False


def test_wire_snap_target_onto_segment():
    w = Wire(id="w1", points=[(0.0, 0.0), (4.0, 0.0)])
    wg = _wg(wires=[w])
    pt, connectable = wg.wire_snap_target((2.0, 0.1))   # within PIN_SNAP_GU (0.125)
    assert pt == (2.0, 0.0)
    assert connectable is True


def test_wire_snap_target_excludes_own_wire():
    w = Wire(id="w1", points=[(0.0, 0.0), (4.0, 0.0)])
    wg = _wg(wires=[w])
    pt, connectable = wg.wire_snap_target((2.0, 0.2), exclude_wire_id="w1")
    assert connectable is False  # its own wire is ignored -> grid fallback


def test_vertex_is_draggable_endpoint_on_pin_still_draggable():
    w = Wire(id="w1", points=[(0.0, 0.0), (0.0, 4.0)])
    wg = _wg(_r("r1", (0.0, 0.0)), wires=[w])
    # Both endpoints are draggable now — dragging a connected endpoint (index 0,
    # on r1's pin) disconnects it. Only out-of-range indices are non-draggable.
    assert wg.vertex_is_draggable(w, 0) is True
    assert wg.vertex_is_draggable(w, 1) is True
    assert wg.vertex_is_draggable(w, 5) is False  # out of range


def test_vertex_is_draggable_intermediate_always():
    w = Wire(id="w1", points=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])
    wg = _wg(wires=[w])
    assert wg.vertex_is_draggable(w, 1) is True


def test_wire_vertex_at_finds_draggable_corner():
    w = Wire(id="w1", points=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])
    wg = _wg(wires=[w])
    assert wg.wire_vertex_at(_scene_pt(2.0, 0.0)) == ("w1", 1)


def test_wire_vertex_at_returns_connected_endpoint():
    w = Wire(id="w1", points=[(0.0, 0.0), (0.0, 4.0)])
    wg = _wg(_r("r1", (0.0, 0.0)), wires=[w])
    # The (0,0) endpoint sits on a pin but is now draggable (drag to disconnect),
    # so wire_vertex_at returns it.
    assert wg.wire_vertex_at(_scene_pt(0.0, 0.0)) == ("w1", 0)


def test_unconnected_pin_at_detects_and_skips_connected():
    w = Wire(id="w1", points=[(2.0, 0.0), (2.0, 4.0)])  # endpoint on r1's pin 2
    wg = _wg(_r("r1", (0.0, 0.0)), wires=[w])
    assert wg.unconnected_pin_at(_scene_pt(0.0, 0.0)) == (0.0, 0.0)  # free pin
    assert wg.unconnected_pin_at(_scene_pt(2.0, 0.0)) is None         # connected


def test_click_select_wire_id_prefers_passthrough():
    through = Wire(id="through", points=[(0.0, 0.0), (4.0, 0.0)])
    stub = Wire(id="stub", points=[(2.0, 0.0), (2.0, 2.0)])
    wg = _wg(wires=[through, stub])
    # A click on the shared point grabbing the stub still resolves to the wire
    # the cursor passes through.
    assert wg.click_select_wire_id(_scene_pt(1.0, 0.0), "stub") == "through"
