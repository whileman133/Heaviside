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
    HOP_RADIUS_GU,
    Schematic,
    Wire,
    WireHop,
    circle_connection_points,
    component_connection_points,
    component_pin_positions,
    junction_points,
    open_endpoints,
    rect_perimeter_points,
    unconnected_pins,
    route,
    simplify_points,
    wire_crossings,
    wire_fraction_at_point,
    wire_point_at_fraction,
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
    defaults = dict(id=_uid(), kind="R", position=(0.0, 0.0), rotation=0, options="")
    defaults.update(kwargs)
    return Component(**defaults)


def _wire(points, **kwargs) -> Wire:
    defaults = dict(id=_uid())
    defaults.update(kwargs)
    return Wire(points=points, **defaults)


# ---------------------------------------------------------------------------
# Component capability mixins (FontedComponent / StyledComponent)
# ---------------------------------------------------------------------------

def test_component_mixin_composition() -> None:
    """Bipole composes both mixins; rect only style; text_node only font.

    Also a regression guard: if the mixins are ever listed *after*
    DrawingComponent in the bases, dataclass field ordering raises at import,
    so merely importing/constructing here would fail.
    """
    from app.components.model import (
        BipoleComponent,
        DrawingComponent,
        FontedComponent,
        RectComponent,
        StyledComponent,
        TextNodeComponent,
    )

    bipole = BipoleComponent(
        id=_uid(), kind="bipole", position=(0.0, 0.0), rotation=0, options="",
        fill_color="red!20", border_width=1.5, line_style="dashed", font_bold=True,
    )
    assert isinstance(bipole, FontedComponent)
    assert isinstance(bipole, StyledComponent)
    assert isinstance(bipole, DrawingComponent)
    assert (bipole.fill_color, bipole.line_style, bipole.font_bold) == ("red!20", "dashed", True)
    assert bipole.font_size == 7.0  # bipole override of FontedComponent's 12.0

    rect = RectComponent(id=_uid(), kind="rect", position=(0.0, 0.0), rotation=0, options="")
    assert isinstance(rect, StyledComponent)
    # rect gained FontedComponent for its centred block-diagram text label.
    assert isinstance(rect, FontedComponent)
    assert rect.font_size == 12.0  # FontedComponent default (no override)

    text = TextNodeComponent(id=_uid(), kind="text_node", position=(0.0, 0.0), rotation=0, options="Hi")
    assert isinstance(text, FontedComponent)
    assert not isinstance(text, StyledComponent)


# ---------------------------------------------------------------------------
# Rect edge connection points (block-diagram wiring)
# ---------------------------------------------------------------------------

def _rect(**kwargs) -> Component:
    from app.components.model import RectComponent
    defaults = dict(id=_uid(), kind="rect", position=(0.0, 0.0), rotation=0, options="")
    defaults.update(kwargs)
    return RectComponent(**defaults)


def test_rect_perimeter_points_unit_box() -> None:
    """A 1x1 rect at the origin has 0.25-GU points all around its perimeter."""
    pts = rect_perimeter_points(_rect(span_override=(1.0, 1.0)))
    # Corners present.
    assert {(0.0, 0.0), (1.0, 0.0), (0.0, 1.0), (1.0, 1.0)} <= pts
    # Edge midpoints (on 0.25 grid) present.
    assert (0.5, 0.0) in pts and (1.0, 0.5) in pts and (0.5, 1.0) in pts and (0.0, 0.75) in pts
    # An interior point is NOT a perimeter point.
    assert (0.5, 0.5) not in pts
    # Perimeter of a 1x1 box at 0.25 spacing: 4 edges * 4 segments = 16 distinct points.
    assert len(pts) == 16


def test_rect_perimeter_points_offset_and_default_span() -> None:
    """Perimeter is computed from position + (span_override or default_span)."""
    pts = rect_perimeter_points(_rect(position=(2.0, 1.0), span_override=None))
    # default_span is (1,1): corners at (2,1) and (3,2).
    assert {(2.0, 1.0), (3.0, 1.0), (2.0, 2.0), (3.0, 2.0)} <= pts
    assert (2.5, 1.0) in pts  # top edge midpoint


def test_component_connection_points_rect_vs_named() -> None:
    """connection points = perimeter for rect, named pins for everything else."""
    rect = _rect(span_override=(1.0, 1.0))
    assert component_connection_points(rect) == rect_perimeter_points(rect)
    res = _resistor(position=(0.0, 0.0))
    assert component_connection_points(res) == set(component_pin_positions(res))


def test_open_endpoint_on_rect_edge_is_connected() -> None:
    """A wire ending on a rect edge is connected (no open-circle terminal)."""
    rect = _rect(position=(0.0, 0.0), span_override=(2.0, 2.0))
    # Wire from open space into the left edge midpoint (0, 1).
    w = _wire([(-2.0, 1.0), (0.0, 1.0)])
    result = open_endpoints(_make_schematic(rect, wires=(w,)))
    assert (0.0, 1.0) not in result   # touches the rect edge → connected
    assert (-2.0, 1.0) in result      # free end stays open


# ---------------------------------------------------------------------------
# Circle cardinal connection points (block-diagram node)
# ---------------------------------------------------------------------------

def _circle(**kwargs) -> Component:
    from app.components.model import CircleComponent
    defaults = dict(id=_uid(), kind="circle", position=(0.0, 0.0), rotation=0, options="")
    defaults.update(kwargs)
    return CircleComponent(**defaults)


def test_circle_connection_points_are_only_cardinal() -> None:
    """A circle exposes exactly the four N/S/E/W bounding-box edge midpoints."""
    pts = circle_connection_points(_circle(span_override=(2.0, 2.0)))
    assert pts == {(1.0, 0.0), (1.0, 2.0), (2.0, 1.0), (0.0, 1.0)}  # N, S, E, W


def test_circle_connection_points_ellipse() -> None:
    """For a non-square (ellipse) box the cardinal points are the axis ends."""
    pts = circle_connection_points(_circle(position=(1.0, 1.0), span_override=(4.0, 2.0)))
    # centre (3,2); N=(3,1) S=(3,3) E=(5,2) W=(1,2)
    assert pts == {(3.0, 1.0), (3.0, 3.0), (5.0, 2.0), (1.0, 2.0)}


def test_component_connection_points_circle() -> None:
    circ = _circle(span_override=(2.0, 2.0))
    assert component_connection_points(circ) == circle_connection_points(circ)


def test_open_endpoint_on_circle_cardinal_is_connected_but_not_corner() -> None:
    """Only the cardinal points connect; a wire ending elsewhere on the box stays open."""
    circ = _circle(position=(0.0, 0.0), span_override=(2.0, 2.0))
    cardinal = _wire([(-2.0, 1.0), (0.0, 1.0)])   # ends on W point (0,1)
    corner = _wire([(-2.0, 0.0), (0.0, 0.0)])     # ends on box corner (0,0) — NOT a cardinal point
    result = open_endpoints(_make_schematic(circ, wires=(cardinal, corner)))
    assert (0.0, 1.0) not in result   # cardinal → connected
    assert (0.0, 0.0) in result       # corner is not a circle connection point → open


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
    """A Wire with a vertex off the 0.25 GU grid produces a validation error."""
    wire = _wire([(0.3, 0.0), (2.0, 0.0)])   # 0.3 is not a multiple of 0.25
    errors = validate(_make_schematic(wires=[wire]))
    assert any("0.25 GU boundary" in e for e in errors), errors


def test_wire_on_quarter_grid_is_valid() -> None:
    """Vertices on the 0.25 GU grid (e.g. a fine-nudged pin at y=0.25) validate."""
    fet = Component(id=_uid(), kind="nigfete", position=(0.0, -0.25), rotation=0, options="")
    src = component_pin_positions(fet)[2]    # source pin at (1.0, 0.25) — on the 0.25 grid
    wire = _wire([src, (src[0], 2.0)])
    assert validate(_make_schematic(fet, wires=[wire])) == []


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
# route — the single Manhattan routing primitive (spec §6.4)
# ---------------------------------------------------------------------------

def test_route_axis_aligned_is_two_points() -> None:
    assert route((0.0, 0.0), (3.0, 0.0)) == [(0.0, 0.0), (3.0, 0.0)]
    assert route((1.0, 1.0), (1.0, 4.0)) == [(1.0, 1.0), (1.0, 4.0)]


def test_route_dominant_axis_horizontal_first() -> None:
    # |dx|=3 > |dy|=2 → horizontal-first, corner at (b.x, a.y).
    assert route((0.0, 0.0), (3.0, 2.0)) == [(0.0, 0.0), (3.0, 0.0), (3.0, 2.0)]


def test_route_dominant_axis_vertical_first() -> None:
    # |dy|=3 > |dx|=2 → vertical-first, corner at (a.x, b.y).
    assert route((0.0, 0.0), (2.0, 3.0)) == [(0.0, 0.0), (0.0, 3.0), (2.0, 3.0)]


def test_route_equal_legs_tie_to_horizontal() -> None:
    # |dx| == |dy| → horizontal-first (the `>=` tie-break).
    assert route((0.0, 0.0), (2.0, 2.0)) == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]


def test_route_explicit_orientation_overrides_dominant_axis() -> None:
    # Same diagonal, opposite forced corners.
    assert route((0.0, 0.0), (2.0, 3.0), vfirst=False) == [
        (0.0, 0.0), (2.0, 0.0), (2.0, 3.0),
    ]
    assert route((0.0, 0.0), (3.0, 2.0), vfirst=True) == [
        (0.0, 0.0), (0.0, 2.0), (3.0, 2.0),
    ]


def test_route_corner_slice_is_single_point_or_empty() -> None:
    assert route((0.0, 0.0), (2.0, 3.0))[1:-1] == [(0.0, 3.0)]
    assert route((0.0, 0.0), (3.0, 0.0))[1:-1] == []


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
    return Component(id=cid, kind="R", position=pos, rotation=0, options="")


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


def test_no_junction_dots_wire_excluded() -> None:
    """A wire flagged no_junction_dots does not contribute to junction degree."""
    main = _W("a", [(0.0, 2.0), (2.0, 2.0), (4.0, 2.0)])  # passes through (2,2)
    branch = Wire(id="b", points=[(2.0, 2.0), (2.0, 5.0)], no_junction_dots=True)
    s = _make_schematic(wires=(main, branch))
    assert junction_points(s) == set()  # branch's degree-1 is not counted


def test_no_junction_dots_does_not_remove_others() -> None:
    """A flagged wire doesn't suppress a dot that other wires independently
    justify at the same coordinate."""
    a = _W("a", [(0.0, 2.0), (2.0, 2.0), (4.0, 2.0)])   # interior vertex (deg 2)
    b = _W("b", [(2.0, 0.0), (2.0, 2.0)])               # endpoint (deg 1) -> 3
    flagged = Wire(id="c", points=[(2.0, 2.0), (2.0, 5.0)], no_junction_dots=True)
    s = _make_schematic(wires=(a, b, flagged))
    assert junction_points(s) == {(2.0, 2.0)}  # a + b still make the dot


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


def test_no_termination_dots_suppresses_open_endpoints() -> None:
    """A wire flagged no_termination_dots contributes no open endpoints."""
    w = Wire(id="a", points=[(0.0, 0.0), (4.0, 0.0)], no_termination_dots=True)
    assert open_endpoints(_make_schematic(wires=(w,))) == set()


def test_no_termination_dots_does_not_affect_other_wires() -> None:
    """A flagged wire still counts as a connection for another wire ending on
    it (the other wire's shared end is not flagged open)."""
    flagged = Wire(id="a", points=[(0.0, 0.0), (4.0, 0.0)], no_termination_dots=True)
    other = _W("b", [(4.0, 0.0), (4.0, 2.0)])  # ends on flagged wire's endpoint
    result = open_endpoints(_make_schematic(wires=(flagged, other)))
    assert (4.0, 0.0) not in result   # shared end is connected (degree>1)
    assert (4.0, 2.0) in result       # other wire's far end is still open


def test_custom_marker_suppresses_open_endpoint() -> None:
    """An endpoint bearing a custom marker gets no automatic open-circle terminal."""
    # end_marker is on points[-1] == (4,0); start (0,0) keeps its terminal.
    w = Wire(id="a", points=[(0.0, 0.0), (4.0, 0.0)], end_marker="arrow")
    assert open_endpoints(_make_schematic(wires=(w,))) == {(0.0, 0.0)}


def test_custom_marker_start_and_end_suppress_both_endpoints() -> None:
    """Markers on both ends suppress both automatic terminals."""
    w = Wire(
        id="a",
        points=[(0.0, 0.0), (4.0, 0.0)],
        start_marker="arrow",
        end_marker="arrow",
    )
    assert open_endpoints(_make_schematic(wires=(w,))) == set()


def test_custom_marker_does_not_affect_other_wires() -> None:
    """A marked end still counts as a connection for another wire ending there."""
    marked = Wire(id="a", points=[(0.0, 0.0), (4.0, 0.0)], end_marker="arrow")
    other = _W("b", [(4.0, 0.0), (4.0, 2.0)])  # shares the marked endpoint
    result = open_endpoints(_make_schematic(wires=(marked, other)))
    assert (4.0, 0.0) not in result   # shared end is connected (degree>1)
    assert (4.0, 2.0) in result       # other wire's far end is still open


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


def test_open_endpoints_voltage_annotation_does_not_connect_wire() -> None:
    """A wire ending on a voltage annotation pin stays an open endpoint.

    The `open` annotation is an open circuit, so it must not turn a dangling
    wire end into a connected one.
    """
    va = Component(id="va", kind="open", position=(2.0, 0.0),
                   rotation=0, options="v=$V$")          # pins (2,0),(4,0)
    s = _make_schematic(va, wires=(_W("a", [(0.0, 0.0), (2.0, 0.0)]),))
    result = open_endpoints(s)
    assert (2.0, 0.0) in result   # wire end on the annotation pin → still open
    assert (0.0, 0.0) in result


def test_open_endpoints_real_pin_still_connects_wire() -> None:
    """A wire ending on a real component pin is still excluded (sanity)."""
    r = _R("r1", (2.0, 0.0))   # pins (2,0),(4,0)
    s = _make_schematic(r, wires=(_W("a", [(0.0, 0.0), (2.0, 0.0)]),))
    assert (2.0, 0.0) not in open_endpoints(s)


def test_open_endpoints_degenerate_wire_does_not_connect() -> None:
    """A stray single-point wire must not suppress a real open endpoint.

    Regression: a degenerate one-point wire at the same coordinate made the
    real wire's endpoint look connected, hiding its ocirc.
    """
    s = _make_schematic(wires=(
        _W("a", [(0.0, 0.0), (4.0, 0.0)]),
        _W("b", [(4.0, 0.0)]),   # degenerate: no segments, connects nothing
    ))
    result = open_endpoints(s)
    assert (4.0, 0.0) in result   # still open despite the degenerate wire
    assert (0.0, 0.0) in result


# ---------------------------------------------------------------------------
# unconnected_pins
# ---------------------------------------------------------------------------

def test_unconnected_pins_lone_component() -> None:
    """A component with no wires has every pin reported as unconnected."""
    r = _R("r1", (0.0, 0.0))   # pins at (0,0) and (2,0)
    s = _make_schematic(r)
    assert unconnected_pins(s) == {(0.0, 0.0), (2.0, 0.0)}


def test_unconnected_pins_wired_pin_excluded() -> None:
    """A pin with a wire vertex on it is not reported; the bare pin is."""
    r = _R("r1", (0.0, 0.0))   # pins at (0,0) and (2,0)
    s = _make_schematic(r, wires=(_W("a", [(2.0, 0.0), (5.0, 0.0)]),))
    result = unconnected_pins(s)
    assert (2.0, 0.0) not in result   # has a wire on it
    assert (0.0, 0.0) in result       # still dangling


def test_unconnected_pins_interior_wire_vertex_connects() -> None:
    """A wire passing *through* a pin (interior vertex) counts as connected."""
    r = _R("r1", (0.0, 0.0))   # pins at (0,0) and (2,0)
    s = _make_schematic(r, wires=(_W("a", [(2.0, -2.0), (2.0, 0.0), (2.0, 2.0)]),))
    assert (2.0, 0.0) not in unconnected_pins(s)


def test_unconnected_pins_abutting_pins_excluded() -> None:
    """Two component pins sharing a coordinate are connected to each other."""
    r1 = _R("r1", (0.0, 0.0))   # pins at (0,0) and (2,0)
    r2 = _R("r2", (2.0, 0.0))   # pins at (2,0) and (4,0)
    result = unconnected_pins(s := _make_schematic(r1, r2))
    assert (2.0, 0.0) not in result          # shared pin
    assert {(0.0, 0.0), (4.0, 0.0)} <= result


def test_unconnected_pins_no_components() -> None:
    """No components → no unconnected pins (and a stray wire is irrelevant)."""
    s = _make_schematic(wires=(_W("a", [(0.0, 0.0), (4.0, 0.0)]),))
    assert unconnected_pins(s) == set()


def _open(cid, pos):
    """Voltage annotation (CircuiTikZ `open`) — draws no wire."""
    return Component(id=cid, kind="open", position=pos, rotation=0, options="v=$V$")


def test_unconnected_pins_voltage_annotation_does_not_connect() -> None:
    """A voltage annotation abutting a real pin must NOT suppress its ocirc.

    The `open` annotation is an open circuit (draws nothing), so the resistor
    pin it touches is still electrically dangling and stays in the set.
    """
    r = _R("r1", (0.0, 0.0))      # pins at (0,0) and (2,0)
    va = _open("va", (2.0, 0.0))  # voltage annotation pin coincides with (2,0)
    result = unconnected_pins(_make_schematic(r, va))
    assert (2.0, 0.0) in result   # real pin still flagged despite the annotation
    assert (0.0, 0.0) in result


def test_unconnected_pins_voltage_annotation_pins_not_flagged() -> None:
    """The voltage annotation's own pins are never flagged (not a terminal)."""
    va = _open("va", (5.0, 5.0))  # free-floating annotation, pins (5,5),(7,5)
    assert unconnected_pins(_make_schematic(va)) == set()


def test_unconnected_pins_degenerate_wire_does_not_connect() -> None:
    """A single-point wire on a pin must not mark it as connected."""
    r = _R("r1", (0.0, 0.0))   # pins (0,0),(2,0)
    s = _make_schematic(r, wires=(_W("b", [(2.0, 0.0)]),))  # degenerate on (2,0)
    assert (2.0, 0.0) in unconnected_pins(s)


def test_unconnected_pins_current_annotation_still_connects() -> None:
    """A current annotation (`short`) IS a real wire, so it connects the pin."""
    r = _R("r1", (0.0, 0.0))   # pins at (0,0) and (2,0)
    short = Component(id="ca", kind="short", position=(2.0, 0.0),
                      rotation=0, options="i=$i$")  # pins (2,0),(4,0)
    result = unconnected_pins(_make_schematic(r, short))
    assert (2.0, 0.0) not in result   # shared with the short → connected


# ---------------------------------------------------------------------------
# wire_point_at_fraction / wire_fraction_at_point
# ---------------------------------------------------------------------------

def test_point_at_fraction_straight() -> None:
    pts = [(0.0, 0.0), (4.0, 0.0)]
    assert wire_point_at_fraction(pts, 0.0) == (0.0, 0.0)
    assert wire_point_at_fraction(pts, 0.5) == (2.0, 0.0)
    assert wire_point_at_fraction(pts, 1.0) == (4.0, 0.0)


def test_point_at_fraction_l_wire_uses_arc_length() -> None:
    # L-wire, total length 4 + 2 = 6. Half = arc-length 3 → (3,0) on first leg.
    pts = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0)]
    assert wire_point_at_fraction(pts, 0.5) == (3.0, 0.0)
    assert wire_point_at_fraction(pts, 1.0) == (4.0, 2.0)


def test_point_at_fraction_clamps_and_degenerate() -> None:
    pts = [(0.0, 0.0), (4.0, 0.0)]
    assert wire_point_at_fraction(pts, -1.0) == (0.0, 0.0)
    assert wire_point_at_fraction(pts, 2.0) == (4.0, 0.0)
    assert wire_point_at_fraction([(1.0, 1.0)], 0.5) == (1.0, 1.0)   # single point
    assert wire_point_at_fraction([], 0.5) == (0.0, 0.0)            # empty


def test_fraction_at_point_projects_onto_polyline() -> None:
    pts = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0)]   # total 6
    assert abs(wire_fraction_at_point(pts, (2.0, 0.0)) - (2.0 / 6.0)) < 1e-9
    assert abs(wire_fraction_at_point(pts, (4.0, 1.0)) - (5.0 / 6.0)) < 1e-9
    # Off the line: projects to the nearest point on the polyline.
    assert abs(wire_fraction_at_point(pts, (2.0, 1.0)) - (2.0 / 6.0)) < 1e-9


def test_fraction_round_trips_with_point() -> None:
    pts = [(0.0, 0.0), (4.0, 0.0), (4.0, 2.0)]
    for frac in (0.1, 0.5, 0.83):
        pt = wire_point_at_fraction(pts, frac)
        assert abs(wire_fraction_at_point(pts, pt) - frac) < 1e-9


# ---------------------------------------------------------------------------
# Line-hop detection (wire_crossings)
# ---------------------------------------------------------------------------

def test_wire_default_z_order_is_zero() -> None:
    assert _wire([(0.0, 0.0), (2.0, 0.0)]).z_order == 0


def test_crossing_emits_single_hop_on_higher_z_order_wire() -> None:
    """An H×V crossing with no shared vertex yields one hop on the higher-z wire."""
    h = _wire([(0.0, 1.0), (4.0, 1.0)], id="h", z_order=1)   # horizontal at y=1
    v = _wire([(2.0, 0.0), (2.0, 3.0)], id="v", z_order=0)   # vertical at x=2
    hops = wire_crossings(_make_schematic(wires=(h, v)))
    assert len(hops) == 1
    hop = hops[0]
    assert hop.point == (2.0, 1.0)
    assert hop.wire_id == "h"          # higher z_order hops
    assert hop.orientation == "h"      # the hopper's crossed segment is horizontal
    assert hop.seg_index == 0


def test_crossing_tie_breaks_on_later_wire() -> None:
    """Equal z_order → the wire later in the list hops."""
    h = _wire([(0.0, 1.0), (4.0, 1.0)], id="h", z_order=0)
    v = _wire([(2.0, 0.0), (2.0, 3.0)], id="v", z_order=0)
    hops = wire_crossings(_make_schematic(wires=(h, v)))
    assert len(hops) == 1 and hops[0].wire_id == "v"   # v is later in the list


def test_shared_vertex_crossing_is_no_hop() -> None:
    """A 4-way cross at a shared vertex is a connection (degree 4 → dot), not a hop."""
    # (2,2) is an explicit shared vertex of both wires.
    a = _wire([(0.0, 2.0), (2.0, 2.0), (4.0, 2.0)], id="a")
    b = _wire([(2.0, 0.0), (2.0, 2.0), (2.0, 4.0)], id="b")
    assert wire_crossings(_make_schematic(wires=(a, b))) == []


def test_t_connection_is_no_hop() -> None:
    """A T (one wire's endpoint on another's segment interior) is a connection."""
    through = _wire([(0.0, 1.0), (4.0, 1.0)], id="t")
    stub = _wire([(2.0, 1.0), (2.0, 3.0)], id="s")    # endpoint (2,1) on the through-segment
    assert wire_crossings(_make_schematic(wires=(through, stub))) == []


def test_corner_touch_is_no_hop() -> None:
    """A wire whose corner vertex lies on another's segment is connected, not hopped."""
    through = _wire([(0.0, 1.0), (4.0, 1.0)], id="t")
    elbow = _wire([(2.0, 1.0), (2.0, 3.0), (5.0, 3.0)], id="e")  # corner at (2,1)
    assert wire_crossings(_make_schematic(wires=(through, elbow))) == []


def test_crossing_at_component_pin_is_no_hop() -> None:
    """A crossing exactly on a component pin is a real connection, no hop."""
    res = _resistor(position=(2.0, 1.0))               # pins at (2,1) and (4,1)
    h = _wire([(0.0, 1.0), (4.0, 1.0)], id="h")        # along the pins' row
    v = _wire([(2.0, 0.0), (2.0, 3.0)], id="v")        # crosses at (2,1) = a pin
    assert wire_crossings(_make_schematic(res, wires=(h, v))) == []


def test_annotation_wire_suppresses_hop() -> None:
    """A wire flagged no_junction_dots is an annotation lead → excluded from hops."""
    h = _wire([(0.0, 1.0), (4.0, 1.0)], id="h", no_junction_dots=True)
    v = _wire([(2.0, 0.0), (2.0, 3.0)], id="v")
    assert wire_crossings(_make_schematic(wires=(h, v))) == []


def test_hop_mode_default_empty() -> None:
    assert _wire([(0.0, 0.0), (2.0, 0.0)]).hop_mode == ""


def test_hop_mode_never_yields_to_crosser() -> None:
    """A 'never' wire doesn't hop, but a crossing wire still hops over it."""
    # h would normally hop (higher z), but it's 'never' → v hops over it instead.
    h = _wire([(0.0, 1.0), (4.0, 1.0)], id="h", z_order=5, hop_mode="never")
    v = _wire([(2.0, 0.0), (2.0, 3.0)], id="v")
    hops = wire_crossings(_make_schematic(wires=(h, v)))
    assert len(hops) == 1 and hops[0].wire_id == "v"


def test_hop_mode_both_never_no_hop() -> None:
    """Two 'never' wires crossing draw no bump at all."""
    h = _wire([(0.0, 1.0), (4.0, 1.0)], id="h", hop_mode="never")
    v = _wire([(2.0, 0.0), (2.0, 3.0)], id="v", hop_mode="never")
    assert wire_crossings(_make_schematic(wires=(h, v))) == []


def test_hop_mode_always_overrides_z_order() -> None:
    """An 'always' wire hops even with the lower z_order."""
    h = _wire([(0.0, 1.0), (4.0, 1.0)], id="h", z_order=0, hop_mode="always")
    v = _wire([(2.0, 0.0), (2.0, 3.0)], id="v", z_order=9)
    hops = wire_crossings(_make_schematic(wires=(h, v)))
    assert len(hops) == 1 and hops[0].wire_id == "h"


def test_hop_mode_always_hops_when_global_off() -> None:
    """'always' hops even when the global default is off; a default wire doesn't."""
    h = _wire([(0.0, 1.0), (4.0, 1.0)], id="h", hop_mode="always")
    v = _wire([(2.0, 0.0), (2.0, 3.0)], id="v")
    forced = wire_crossings(_make_schematic(wires=(h, v)), default_on=False)
    assert len(forced) == 1 and forced[0].wire_id == "h"
    d1 = _wire([(0.0, 1.0), (4.0, 1.0)], id="d1")
    d2 = _wire([(2.0, 0.0), (2.0, 3.0)], id="d2")
    assert wire_crossings(_make_schematic(wires=(d1, d2)), default_on=False) == []


def test_multiple_crossings_on_one_segment() -> None:
    """A horizontal wire crossing two verticals gets two hops (one per crossing)."""
    h = _wire([(0.0, 1.0), (6.0, 1.0)], id="h", z_order=1)
    v1 = _wire([(2.0, 0.0), (2.0, 3.0)], id="v1")
    v2 = _wire([(4.0, 0.0), (4.0, 3.0)], id="v2")
    hops = wire_crossings(_make_schematic(wires=(h, v1, v2)))
    pts = sorted(hop.point for hop in hops)
    assert pts == [(2.0, 1.0), (4.0, 1.0)]
    assert all(hop.wire_id == "h" for hop in hops)


def test_collinear_overlap_is_no_hop() -> None:
    """Two parallel (collinear) wires never cross → no hop."""
    a = _wire([(0.0, 1.0), (4.0, 1.0)], id="a")
    b = _wire([(1.0, 1.0), (5.0, 1.0)], id="b")
    assert wire_crossings(_make_schematic(wires=(a, b))) == []


def test_self_crossing_ignored() -> None:
    """A single wire that crosses itself produces no hop (pairs are distinct wires)."""
    # An L that doubles back over its own row is still one wire; no cross-wire hop.
    w = _wire([(0.0, 0.0), (4.0, 0.0), (4.0, 2.0), (2.0, 2.0), (2.0, -1.0)], id="w")
    assert wire_crossings(_make_schematic(wires=(w,))) == []


def test_hop_radius_constant_positive() -> None:
    assert HOP_RADIUS_GU > 0
