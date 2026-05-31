"""
Phase 2 tests — schematic model and validation.

All tests exercise app/schematic/model.py and app/schematic/validate.py.
No Qt, no filesystem, no LaTeX required.
"""

from __future__ import annotations

import uuid

import pytest

from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    junction_points,
    open_endpoints,
    simplify_points,
)
from app.schematic.validate import validate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _make_schematic(*components, wires=()) -> Schematic:
    return Schematic(
        version="0.1",
        name="test",
        components=list(components),
        wires=list(wires),
    )


def _resistor(**kwargs) -> Component:
    defaults = dict(id=_uid(), kind="R", position=(0.0, 0.0), rotation=0, labels={})
    defaults.update(kwargs)
    return Component(**defaults)


def _wire(points, **kwargs) -> Wire:
    defaults = dict(id=_uid())
    defaults.update(kwargs)
    return Wire(points=points, **defaults)


# ---------------------------------------------------------------------------
# test_component_valid
# ---------------------------------------------------------------------------

def test_component_valid() -> None:
    """A Component with a known kind, valid rotation, and valid position passes validate()."""
    comp = _resistor(position=(1.0, 2.5), rotation=90)
    errors = validate(_make_schematic(comp))
    assert errors == []


# ---------------------------------------------------------------------------
# test_component_invalid_kind
# ---------------------------------------------------------------------------

def test_component_invalid_kind() -> None:
    """A Component with a kind not in REGISTRY produces a validation error."""
    comp = _resistor(kind="NOTACOMPONENT")
    errors = validate(_make_schematic(comp))
    assert any("unknown kind" in e for e in errors), errors


# ---------------------------------------------------------------------------
# test_component_invalid_rotation
# ---------------------------------------------------------------------------

def test_component_invalid_rotation() -> None:
    """A Component with rotation=45 produces a validation error."""
    comp = _resistor(rotation=45)
    errors = validate(_make_schematic(comp))
    assert any("rotation" in e for e in errors), errors


# ---------------------------------------------------------------------------
# test_wire_valid
# ---------------------------------------------------------------------------

def test_wire_valid() -> None:
    """A Wire with a valid Manhattan path on 0.5 GU boundaries passes validation."""
    wire = _wire([(0.0, 0.0), (2.0, 0.0), (2.0, 1.5)])
    errors = validate(_make_schematic(wires=[wire]))
    assert errors == []


# ---------------------------------------------------------------------------
# test_wire_off_grid
# ---------------------------------------------------------------------------

def test_wire_off_grid() -> None:
    """A Wire with a vertex at (0.3, 0.0) produces a validation error."""
    wire = _wire([(0.3, 0.0), (2.0, 0.0)])
    errors = validate(_make_schematic(wires=[wire]))
    assert any("0.5 GU boundary" in e for e in errors), errors


# ---------------------------------------------------------------------------
# test_wire_diagonal
# ---------------------------------------------------------------------------

def test_wire_diagonal() -> None:
    """A Wire with a diagonal segment produces a validation error."""
    wire = _wire([(0.0, 0.0), (1.0, 1.0)])
    errors = validate(_make_schematic(wires=[wire]))
    assert any("diagonal" in e or "Manhattan" in e for e in errors), errors


# ---------------------------------------------------------------------------
# test_schematic_duplicate_ids
# ---------------------------------------------------------------------------

def test_schematic_duplicate_ids() -> None:
    """A Schematic with two components sharing the same id produces a validation error."""
    shared_id = _uid()
    comp_a = _resistor(id=shared_id)
    comp_b = _resistor(id=shared_id)
    errors = validate(_make_schematic(comp_a, comp_b))
    assert any("Duplicate component id" in e for e in errors), errors


# ---------------------------------------------------------------------------
# test_schematic_empty_valid
# ---------------------------------------------------------------------------

def test_schematic_empty_valid() -> None:
    """An empty Schematic (no components, no wires) passes validation."""
    errors = validate(_make_schematic())
    assert errors == []


# ---------------------------------------------------------------------------
# simplify_points — collapse redundant collinear / duplicate vertices
# ---------------------------------------------------------------------------

def test_simplify_collapses_collinear_horizontal() -> None:
    assert simplify_points([(0, 0), (2, 0), (5, 0)]) == [(0, 0), (5, 0)]


def test_simplify_collapses_collinear_vertical() -> None:
    assert simplify_points([(0, 0), (0, 2), (0, 5)]) == [(0, 0), (0, 5)]


def test_simplify_collapses_long_run() -> None:
    assert simplify_points([(0, 0), (1, 0), (2, 0), (3, 0)]) == [(0, 0), (3, 0)]


def test_simplify_keeps_genuine_elbow() -> None:
    pts = [(0, 0), (2, 0), (2, 5)]
    assert simplify_points(pts) == pts


def test_simplify_run_then_elbow() -> None:
    assert simplify_points([(0, 0), (1, 0), (2, 0), (2, 5)]) == [
        (0, 0),
        (2, 0),
        (2, 5),
    ]


def test_simplify_mixed_runs_and_elbows() -> None:
    pts = [(0, 0), (2, 0), (4, 0), (4, 2), (4, 4), (7, 4)]
    assert simplify_points(pts) == [(0, 0), (4, 0), (4, 4), (7, 4)]


def test_simplify_removes_consecutive_duplicates() -> None:
    assert simplify_points([(0, 0), (0, 0), (2, 0)]) == [(0, 0), (2, 0)]


def test_simplify_two_points_untouched() -> None:
    assert simplify_points([(0, 0), (2, 0)]) == [(0, 0), (2, 0)]


def test_simplify_already_minimal_unchanged() -> None:
    pts = [(0, 0), (2, 0), (2, 5), (5, 5)]
    assert simplify_points(pts) == pts


def test_simplify_all_duplicates_collapse_to_one() -> None:
    assert simplify_points([(3, 1), (3, 1)]) == [(3, 1)]


def test_simplify_does_not_mutate_input() -> None:
    pts = [(0, 0), (1, 0), (2, 0)]
    original = list(pts)
    simplify_points(pts)
    assert pts == original


def test_simplify_u_turn_collapses_to_straight() -> None:
    """A–B–A pattern: collinear collapse drops B (same y), leaving A–A duplicate.

    Regression: before the fix, simplify_points ran dedup only before the
    collinear pass. Collapsing B from (A, B, A) produces a consecutive duplicate
    (A, A) that the earlier dedup couldn't see. The second dedup pass now catches
    it, so the result is just the two distinct endpoints.

    Concrete case from a vertex drag: dragging the free end of
    (80.5,75.5)→(81,75.5)→(81,74.5) to (80.5,74.5) inserts an elbow at
    (80.5,75.5), producing (80.5,75.5)→(81,75.5)→(80.5,75.5)→(80.5,74.5).
    The (81,75.5) is same-y collinear between the two (80.5,75.5) entries and
    gets dropped, leaving a duplicate first point that caused a spurious
    junction dot at the component pin.
    """
    pts = [(80.5, 75.5), (81.0, 75.5), (80.5, 75.5), (80.5, 74.5)]
    assert simplify_points(pts) == [(80.5, 75.5), (80.5, 74.5)]


def test_simplify_result_stays_valid() -> None:
    """A simplified wire still passes schematic validation (Manhattan)."""
    w = Wire(id="w", points=simplify_points([(0, 0), (1, 0), (2, 0), (2, 3)]))
    errors = validate(_make_schematic(wires=(w,)))
    assert errors == []


# ---------------------------------------------------------------------------
# junction_points — connection dots
# ---------------------------------------------------------------------------

def _W(wid, pts):
    return Wire(id=wid, points=list(pts))


def _R(cid, pos):
    return Component(id=cid, kind="R", position=pos, rotation=0, labels={})


def test_junction_three_wires_meeting() -> None:
    s = _make_schematic(
        wires=(
            _W("a", [(0.0, 2.0), (2.0, 2.0)]),
            _W("b", [(2.0, 0.0), (2.0, 2.0)]),
            _W("c", [(2.0, 2.0), (4.0, 2.0)]),
        )
    )
    assert junction_points(s) == {(2.0, 2.0)}


def test_junction_two_wire_corner_has_no_dot() -> None:
    s = _make_schematic(
        wires=(
            _W("a", [(0.0, 0.0), (2.0, 0.0)]),
            _W("b", [(2.0, 0.0), (2.0, 2.0)]),
        )
    )
    assert junction_points(s) == set()


def test_junction_two_wire_crossing_is_connected() -> None:
    """Two wires sharing a mid-vertex are electrically joined (degree 4) → dot.

    This model has no non-connecting "hop" crossing: coincident wire points are
    connected, so a 4-way overlap gets a junction dot.
    """
    s = _make_schematic(
        wires=(
            _W("a", [(0.0, 2.0), (2.0, 2.0), (4.0, 2.0)]),
            _W("b", [(2.0, 0.0), (2.0, 2.0), (2.0, 4.0)]),
        )
    )
    assert junction_points(s) == {(2.0, 2.0)}


def test_junction_T_split_has_dot() -> None:
    """A wire ending on another wire's interior vertex (a T) → dot.

    Wire 'a' passes through (2,2) (degree 2); wire 'b' ends there (degree 1);
    total degree 3 → junction.
    """
    s = _make_schematic(
        wires=(
            _W("a", [(0.0, 2.0), (2.0, 2.0), (4.0, 2.0)]),   # passes through
            _W("b", [(2.0, 2.0), (2.0, 5.0)]),               # T into it
        )
    )
    assert junction_points(s) == {(2.0, 2.0)}


def test_junction_pin_pass_through_has_dot() -> None:
    """A pin meeting a wire that passes straight through it → dot (1 + 2 = 3)."""
    s = _make_schematic(
        _R("r", (0.0, 0.0)),                                  # pin (2,0)
        wires=(_W("a", [(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)]),),  # through (2,0)
    )
    assert junction_points(s) == {(2.0, 0.0)}


def test_junction_pin_plus_two_wires_has_dot() -> None:
    s = _make_schematic(
        _R("r", (0.0, 0.0)),                       # pins (0,0),(2,0)
        wires=(
            _W("a", [(2.0, 0.0), (4.0, 0.0)]),
            _W("b", [(2.0, 0.0), (2.0, 2.0)]),
        ),
    )
    assert junction_points(s) == {(2.0, 0.0)}


def test_junction_pin_plus_one_wire_has_no_dot() -> None:
    s = _make_schematic(
        _R("r", (0.0, 0.0)),
        wires=(_W("a", [(2.0, 0.0), (4.0, 0.0)]),),
    )
    assert junction_points(s) == set()


def test_junction_no_spurious_dot_after_u_turn_drag() -> None:
    """Dragging a wire endpoint so the elbow lands on the adjacent pin must not
    create a junction dot at that pin.

    Regression: MoveWireVertexCommand inserted an elbow equal to pts[0] (the
    pin coordinate), producing a U-turn path A–B–A–C. simplify_points dropped B
    (same-y collinear) but left a consecutive duplicate A–A at the start; the
    second A was counted as an interior vertex (degree 2) and combined with the
    pin's degree 1 to give degree 3 → spurious circ node.
    """
    from app.canvas.commands import MoveWireVertexCommand

    # Resistor at (0,0): pins at (0,0) and (2,0).
    # Wire: (2,0)→(3,0)→(3,1) — L-shape off the right pin.
    # Drag the free endpoint (3,1) to (2,1): elbow inserted at (2,0) = pin.
    r = _R("r", (0.0, 0.0))
    w = _W("w1", [(2.0, 0.0), (3.0, 0.0), (3.0, 1.0)])
    s = _make_schematic(r, wires=(w,))
    cmd = MoveWireVertexCommand("w1", 2, (2.0, 1.0))
    cmd.do(s)
    assert junction_points(s) == set(), (
        f"spurious junction after drag; wire points: {w.points}"
    )


def test_junction_single_wire_revisiting_point() -> None:
    """A lone wire that returns to a point isn't a multi-wire junction."""
    s = _make_schematic(
        wires=(_W("a", [(2.0, 2.0), (0.0, 2.0), (0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]),)
    )
    assert junction_points(s) == set()


def test_junction_four_way() -> None:
    s = _make_schematic(
        wires=(
            _W("a", [(0.0, 2.0), (2.0, 2.0)]),
            _W("b", [(4.0, 2.0), (2.0, 2.0)]),
            _W("c", [(2.0, 0.0), (2.0, 2.0)]),
            _W("d", [(2.0, 4.0), (2.0, 2.0)]),
        )
    )
    assert junction_points(s) == {(2.0, 2.0)}


# ---------------------------------------------------------------------------
# wire_splits_at — mid-segment connection detection
# ---------------------------------------------------------------------------

def test_wire_splits_at_interior_point() -> None:
    from app.schematic.model import wire_splits_at

    s = _make_schematic(wires=(_W("a", [(0.0, 2.0), (4.0, 2.0)]),))
    assert wire_splits_at(s, (2.0, 2.0)) == [("a", 1)]


def test_wire_splits_at_vertex_is_not_a_split() -> None:
    from app.schematic.model import wire_splits_at

    s = _make_schematic(wires=(_W("a", [(0.0, 2.0), (4.0, 2.0)]),))
    # An existing endpoint is not a split site.
    assert wire_splits_at(s, (0.0, 2.0)) == []
    assert wire_splits_at(s, (4.0, 2.0)) == []


def test_wire_splits_at_off_segment_point() -> None:
    from app.schematic.model import wire_splits_at

    s = _make_schematic(wires=(_W("a", [(0.0, 2.0), (4.0, 2.0)]),))
    assert wire_splits_at(s, (2.0, 3.0)) == []   # not on the line


def test_wire_splits_at_multi_segment_index() -> None:
    from app.schematic.model import wire_splits_at

    # L-shaped wire: (0,0)->(0,4)->(4,4). Split the second (horizontal) leg.
    s = _make_schematic(wires=(_W("a", [(0.0, 0.0), (0.0, 4.0), (4.0, 4.0)]),))
    assert wire_splits_at(s, (2.0, 4.0)) == [("a", 2)]


# ---------------------------------------------------------------------------
# open_endpoints
# ---------------------------------------------------------------------------

def test_open_endpoints_free_wire_both_ends() -> None:
    """A wire with no component pins at either end has both endpoints open."""
    s = _make_schematic(wires=(_W("a", [(0.0, 0.0), (4.0, 0.0)]),))
    assert open_endpoints(s) == {(0.0, 0.0), (4.0, 0.0)}


def test_open_endpoints_pin_connected_end_excluded() -> None:
    """An endpoint coinciding with a component pin is not open.

    Resistor at (0,0) has pins at (0,0) and (2,0). Wire from (2,0) to (5,0):
    the (2,0) end is on a pin → closed; (5,0) is free → open.
    """
    r = _R("r1", (0.0, 0.0))   # pins at (0,0) and (2,0) per registry
    s = _make_schematic(r, wires=(_W("a", [(2.0, 0.0), (5.0, 0.0)]),))
    result = open_endpoints(s)
    assert (2.0, 0.0) not in result
    assert (5.0, 0.0) in result


def test_open_endpoints_both_ends_connected_to_pins() -> None:
    """A wire spanning two component pins has no open endpoints."""
    r1 = _R("r1", (0.0, 0.0))   # pins at (0,0) and (2,0)
    r2 = _R("r2", (6.0, 0.0))   # pins at (6,0) and (8,0)
    s = _make_schematic(r1, r2, wires=(_W("a", [(2.0, 0.0), (6.0, 0.0)]),))
    assert open_endpoints(s) == set()


def test_open_endpoints_no_wires() -> None:
    """A schematic with no wires has no open endpoints."""
    r = _R("r1", (0.0, 0.0))
    s = _make_schematic(r)
    assert open_endpoints(s) == set()


def test_open_endpoints_interior_vertices_excluded() -> None:
    """Interior vertices of a wire are never reported as open endpoints."""
    # L-shaped wire: corner at (2,0) is interior, not an endpoint.
    s = _make_schematic(wires=(_W("a", [(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)]),))
    result = open_endpoints(s)
    assert (2.0, 0.0) not in result   # interior vertex
    assert (0.0, 0.0) in result
    assert (2.0, 3.0) in result
