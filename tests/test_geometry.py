"""
Unit tests for app/canvas/geometry.py — the pure canvas geometry helpers.

No Qt scene and no QApplication required (QPointF is value-only).
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPointF

from app.canvas.geometry import (
    dist2_to_segment,
    gu_to_scene,
    local_span_to_world,
    scene_to_gu,
    snap_gu,
    snap_point_gu,
    wire_proximity_key,
    world_delta_to_local,
)
from app.canvas.style import GRID_PX


@pytest.mark.parametrize("raw, expected", [
    # Grid is 0.25 GU: round to the nearest quarter.
    (0.0, 0.0), (0.12, 0.0), (0.13, 0.25), (0.24, 0.25), (0.26, 0.25),
    (0.6, 0.5), (0.75, 0.75), (-0.13, -0.25), (1.5, 1.5),
])
def test_snap_gu(raw, expected):
    assert snap_gu(raw) == expected


def test_scene_gu_roundtrip():
    pt = QPointF(3 * GRID_PX, -2 * GRID_PX)
    assert scene_to_gu(pt) == (3.0, -2.0)
    assert gu_to_scene(3.0, -2.0) == pt


def test_snap_point_gu():
    # On the 0.25 grid: 0.26 GU rounds to 0.25, -0.1 GU rounds to 0.0.
    pt = QPointF(0.26 * GRID_PX, -0.1 * GRID_PX)
    assert snap_point_gu(pt) == (0.25, 0.0)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("mirror", [False, True])
def test_world_local_round_trip(rotation, mirror):
    """world_delta_to_local inverts local_span_to_world's rotation step.

    local_span_to_world applies mirror then rotation; world_delta_to_local only
    undoes the rotation, so feeding it an unmirrored span recovers that span.
    """
    span = (2.0, 0.0)
    world = local_span_to_world(span, rotation, mirror=False)
    back = world_delta_to_local(world[0], world[1], rotation)
    assert back == pytest.approx(span)


def test_local_span_to_world_known_values():
    # CW rotation in Y-down space: 90° maps (2,0) -> (0,2).
    assert local_span_to_world((2.0, 0.0), 90, False) == pytest.approx((0.0, 2.0))
    assert local_span_to_world((2.0, 0.0), 180, False) == pytest.approx((-2.0, 0.0))
    # Mirror flips the local x first.
    assert local_span_to_world((2.0, 0.0), 0, True) == pytest.approx((-2.0, 0.0))


def test_dist2_to_segment_interior_vs_endpoint():
    # Perpendicular foot lands inside the segment -> not an endpoint touch.
    d2, at_end = dist2_to_segment(1.0, 1.0, 0.0, 0.0, 2.0, 0.0)
    assert d2 == pytest.approx(1.0)
    assert at_end is False
    # Foot projects beyond the segment end -> endpoint touch.
    d2, at_end = dist2_to_segment(5.0, 0.0, 0.0, 0.0, 2.0, 0.0)
    assert d2 == pytest.approx(9.0)
    assert at_end is True


def test_dist2_to_segment_degenerate():
    d2, at_end = dist2_to_segment(3.0, 4.0, 1.0, 1.0, 1.0, 1.0)
    assert d2 == pytest.approx(4.0 + 9.0)
    assert at_end is True


def test_wire_proximity_key_empty():
    assert wire_proximity_key(0.0, 0.0, [(0.0, 0.0)]) is None


def test_wire_proximity_key_interior_beats_endpoint():
    pts = [(0.0, 0.0), (4.0, 0.0)]
    interior = wire_proximity_key(2.0, 0.5, pts)
    endpoint = wire_proximity_key(-0.5, 0.0, pts)
    assert interior[1] == 0       # passes through
    assert endpoint[1] == 1       # only touches the tip
    assert interior < endpoint    # interior sorts as "more on the wire"


def test_wire_proximity_key_intermediate_vertex_is_rank_zero():
    # An L-shaped wire; a hit exactly on the corner vertex must be rank 0.
    pts = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]
    key = wire_proximity_key(2.0, 0.0, pts)
    assert key == (0.0, 0)
