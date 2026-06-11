"""
Transform-consistency tripwire (spec §7 Mirror / rotation conventions).

The clockwise-rotate-then-mirror transform is implemented independently in
several places:

* ``app.schematic.model.component_pin_positions`` — model connectivity,
* ``app.canvas.geometry.local_span_to_world`` / ``world_delta_to_local`` —
  endpoint-drag math,
* ``app.canvas.commands.GroupRotateCommand._rot90cw`` — group rotation,
* the canvas ``QTransform`` built by ``ComponentItem`` and refreshed by
  ``SchematicScene._rebuild_items`` — painting.

These copies must agree exactly: a sign error in any one of them silently
detaches wires or paints components away from their pins. This module asserts
pairwise agreement for every rotation × mirror combination so such a drift
fails CI immediately.

The Qt-dependent checks need an offscreen platform:

    QT_QPA_PLATFORM=offscreen pytest tests/test_transforms.py
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:  # creating the QApplication can fail if no platform plugin loads
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtGui import QTransform  # noqa: E402

from app.canvas.commands import GroupRotateCommand  # noqa: E402
from app.canvas.geometry import (  # noqa: E402
    local_span_to_world,
    scene_to_gu,
    world_delta_to_local,
)
from app.canvas.scene import SchematicScene  # noqa: E402
from app.canvas.style import GRID_PX  # noqa: E402
from app.components.registry import REGISTRY  # noqa: E402
from app.schematic.model import (  # noqa: E402
    Component,
    Schematic,
    component_pin_positions,
)

ROTATIONS = (0, 90, 180, 270)
MIRRORS = (False, True)

#: A local offset that is asymmetric in x and y, so every sign error shows.
SPAN = (3.0, 1.0)


def _close(a: tuple[float, float], b: tuple[float, float], tol: float = 1e-9) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


# ---------------------------------------------------------------------------
# model.component_pin_positions ↔ geometry.local_span_to_world
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mirror", MIRRORS)
@pytest.mark.parametrize("rotation", ROTATIONS)
def test_pin_positions_match_local_span_to_world(rotation: int, mirror: bool):
    """The resizable terminal pin (offset = span_override) must land exactly
    where the endpoint-drag math (local_span_to_world) says it does."""
    comp = Component(
        id="c", kind="open", position=(5.0, 7.0), rotation=rotation,
        options="", mirror=mirror, span_override=SPAN,
    )
    pins = component_pin_positions(comp)
    wx, wy = local_span_to_world(SPAN, rotation, mirror)
    assert _close(pins[1], (5.0 + wx, 7.0 + wy))


# ---------------------------------------------------------------------------
# geometry.world_delta_to_local inverts local_span_to_world (drag inversion)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mirror", MIRRORS)
@pytest.mark.parametrize("rotation", ROTATIONS)
def test_world_delta_to_local_inverts_span_transform(rotation: int, mirror: bool):
    """Round-trip: map a local span to world, then invert it exactly the way
    the endpoint-drag code does (un-flip x for mirror, then un-rotate)."""
    wx, wy = local_span_to_world(SPAN, rotation, mirror)
    if mirror:
        wx = -wx                 # drag.py undoes the global Flip-X first
    assert _close(world_delta_to_local(wx, wy, rotation), SPAN)


# ---------------------------------------------------------------------------
# canvas QTransform (mirror-then-rotate call order = rotate-then-flip on points)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mirror", MIRRORS)
@pytest.mark.parametrize("rotation", ROTATIONS)
def test_qtransform_construction_matches_geometry(rotation: int, mirror: bool):
    """The QTransform built by ComponentItem/scene._rebuild_items (scale(-1,1)
    then rotate(r)) must map a local point exactly like local_span_to_world."""
    t = QTransform()
    if mirror:
        t.scale(-1.0, 1.0)
    t.rotate(rotation)
    mapped = t.map(QPointF(SPAN[0], SPAN[1]))
    assert _close((mapped.x(), mapped.y()), local_span_to_world(SPAN, rotation, mirror))


@pytest.mark.parametrize("mirror", MIRRORS)
@pytest.mark.parametrize("rotation", ROTATIONS)
def test_canvas_item_transform_matches_model_pins(rotation: int, mirror: bool):
    """End-to-end: the live item's scene transform (set by _rebuild_items)
    places each registry pin exactly at component_pin_positions' coordinate."""
    scene = SchematicScene()
    comp = scene.place_component("R", (3.0, 2.0))
    # Mutate via the scene so the _rebuild_items REFRESH path (scene.py's own
    # QTransform construction) is the code under test, not just the item ctor.
    scene.rotate_component(comp.id, rotation)
    scene.mirror_component(comp.id, mirror)
    item = scene._comp_items[comp.id]
    live = scene._component_by_id(comp.id)
    for pin, world in zip(REGISTRY["R"].pins, component_pin_positions(live)):
        local = QPointF(pin.offset[0] * GRID_PX, pin.offset[1] * GRID_PX)
        mapped = scene_to_gu(item.mapToScene(local))
        assert _close(mapped, world, tol=1e-6)


# ---------------------------------------------------------------------------
# GroupRotateCommand._rot90cw shares the rotation=90 convention
# ---------------------------------------------------------------------------

def test_rot90cw_matches_rotation_convention():
    """_rot90cw about the origin is exactly the model's rotation=90 transform
    (Y-down clockwise), and four applications are the identity."""
    p = SPAN
    assert _close(
        GroupRotateCommand._rot90cw(p[0], p[1], 0.0, 0.0),
        local_span_to_world(p, 90, False),
    )
    q = p
    for _ in range(4):
        q = GroupRotateCommand._rot90cw(q[0], q[1], 0.0, 0.0)
    assert _close(q, p)


@pytest.mark.parametrize("mirror", MIRRORS)
@pytest.mark.parametrize("rotation", ROTATIONS)
def test_group_rotate_pins_follow_rot90cw(rotation: int, mirror: bool):
    """A group rotation must move every pin to _rot90cw(old pin) — for every
    stored rotation AND mirror state — or connected wires detach (regression:
    mirrored components rotated their state the wrong way)."""
    comp = Component(
        id="c", kind="R", position=(3.0, 2.0), rotation=rotation,
        options="", mirror=mirror,
    )
    s = Schematic(version="0.1", name="t", components=[comp])
    before = component_pin_positions(comp)
    cmd = GroupRotateCommand(["c"], [], (3.0, 2.0))
    cmd.do(s)
    after = component_pin_positions(comp)
    expected = [cmd._rot90cw(x, y, 3.0, 2.0) for x, y in before]
    for got, exp in zip(after, expected):
        assert _close(got, exp)
    # Undo restores the original stored state exactly.
    cmd.undo(s)
    assert comp.rotation == rotation and comp.mirror is mirror
    assert component_pin_positions(comp) == before
