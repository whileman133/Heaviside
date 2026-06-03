"""
Phase 6 tests — undo/redo command stack (spec §6.6, §13.3).

These are the integration undo/redo tests. They exercise the command layer
against the live Schematic model and require no display server and no LaTeX —
the command layer is deliberately Qt-free, so the canonical
``QT_QPA_PLATFORM=offscreen`` invocation works but is not required here.

Covered (spec §13.3):
  - test_place_command_updates_model
  - test_undo_place
  - test_undo_redo_place
  - test_undo_move
  - test_undo_delete            (restores component AND connected wires)
  - test_undo_edit_label
  - test_undo_stack_depth        (20 mixed ops, undo all → original empty state)
  - test_group_rotate_single_component
  - test_group_rotate_two_components_centroid
  - test_group_rotate_internal_wire
  - test_group_rotate_boundary_wire
  - test_group_rotate_undo
  - test_group_rotate_redo
"""

from __future__ import annotations

import copy
import uuid

import pytest

from app.canvas.commands import (
    Command,
    DeleteCommand,
    EditCommand,
    GroupRotateCommand,
    MacroCommand,
    MoveCommand,
    MoveOptionsLabelCommand,
    MoveWireVertexCommand,
    PlaceCommand,
    ResizeCommand,
    SetWireEndLabelCommand,
    SetWireEndMarkerCommand,
    SetWireLineStyleCommand,
    SetWireLineWidthCommand,
    SetWireMidLabelCommand,
    SetWireMidLabelPosCommand,
    SetWireNoJunctionDotsCommand,
    SetWireNoTerminationDotsCommand,
    SetWireStartLabelCommand,
    SetWireStartMarkerCommand,
    SplitWireCommand,
    UndoStack,
    WireCommand,
    reshape_wire_points,
)
from app.schematic.model import Component, Schematic, Wire


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _empty() -> Schematic:
    return Schematic(version="0.1", name="test")


def _resistor(comp_id: str | None = None, position=(0.0, 0.0), **kw) -> Component:
    return Component(
        id=comp_id or _uid(),
        kind="R",
        position=position,
        rotation=kw.get("rotation", 0),
        options=kw.get("options", ""),
        mirror=kw.get("mirror", False),
    )


def _stack() -> UndoStack:
    return UndoStack(_empty())


# ---------------------------------------------------------------------------
# PlaceCommand
# ---------------------------------------------------------------------------

def test_place_command_updates_model():
    stack = _stack()
    comp = _resistor()
    stack.push(PlaceCommand(comp))

    assert len(stack.schematic.components) == 1
    assert stack.schematic.components[0].id == comp.id
    assert stack.schematic.components[0].kind == "R"


def test_place_inserts_independent_copy():
    """Mutating the original after placement must not affect the model."""
    stack = _stack()
    comp = _resistor(options="l=$R_1$")
    stack.push(PlaceCommand(comp))

    comp.options = "MUTATED"
    comp.position = (99.0, 99.0)

    placed = stack.schematic.components[0]
    assert placed.options == "l=$R_1$"
    assert placed.position == (0.0, 0.0)


def test_undo_place():
    stack = _stack()
    stack.push(PlaceCommand(_resistor()))
    assert len(stack.schematic.components) == 1

    stack.undo()
    assert stack.schematic.components == []
    assert stack.can_redo()
    assert not stack.can_undo()


def test_undo_redo_place():
    stack = _stack()
    comp = _resistor(options="l=$R_1$", rotation=90, mirror=True)
    original = copy.deepcopy(comp)
    stack.push(PlaceCommand(comp))

    stack.undo()
    assert stack.schematic.components == []

    stack.redo()
    assert len(stack.schematic.components) == 1
    restored = stack.schematic.components[0]
    assert restored.id == original.id
    assert restored.kind == original.kind
    assert restored.position == original.position
    assert restored.rotation == original.rotation
    assert restored.mirror == original.mirror
    assert restored.options == original.options


# ---------------------------------------------------------------------------
# MoveCommand
# ---------------------------------------------------------------------------

def test_undo_move():
    stack = _stack()
    comp = _resistor(position=(1.0, 1.0))
    stack.push(PlaceCommand(comp))

    stack.push(MoveCommand([comp.id], delta=(0.5, -1.5)))
    assert stack.schematic.components[0].position == (1.5, -0.5)

    stack.undo()
    assert stack.schematic.components[0].position == (1.0, 1.0)


def test_move_multiple_components():
    stack = _stack()
    a = _resistor(position=(0.0, 0.0))
    b = _resistor(position=(3.0, 0.0))
    stack.push(PlaceCommand(a))
    stack.push(PlaceCommand(b))

    stack.push(MoveCommand([a.id, b.id], delta=(1.0, 1.0)))
    by_id = {c.id: c.position for c in stack.schematic.components}
    assert by_id[a.id] == (1.0, 1.0)
    assert by_id[b.id] == (4.0, 1.0)

    stack.undo()
    by_id = {c.id: c.position for c in stack.schematic.components}
    assert by_id[a.id] == (0.0, 0.0)
    assert by_id[b.id] == (3.0, 0.0)


# ---------------------------------------------------------------------------
# DeleteCommand — must restore connected wires
# ---------------------------------------------------------------------------

def test_undo_delete_restores_component_and_connected_wires():
    stack = _stack()
    # Resistor 'in' pin at (0,0), 'out' pin at (2,0).
    comp = _resistor(comp_id="r1", position=(0.0, 0.0))
    stack.push(PlaceCommand(comp))

    # Wire connected to the 'out' pin (2,0); should be deleted with the comp.
    connected = Wire(id="w_conn", points=[(2.0, 0.0), (2.0, 2.0)])
    # Wire NOT touching any pin of the resistor; should survive deletion.
    unrelated = Wire(id="w_free", points=[(5.0, 5.0), (6.0, 5.0)])
    stack.push(WireCommand(connected))
    stack.push(WireCommand(unrelated))

    stack.push(DeleteCommand([comp.id]))

    # Component and connected wire gone; unrelated wire remains.
    assert stack.schematic.components == []
    wire_ids = {w.id for w in stack.schematic.wires}
    assert wire_ids == {"w_free"}

    # Undo restores both the component and the connected wire.
    stack.undo()
    assert len(stack.schematic.components) == 1
    assert stack.schematic.components[0].id == "r1"
    wire_ids = {w.id for w in stack.schematic.wires}
    assert wire_ids == {"w_conn", "w_free"}


def test_delete_multiple_components():
    stack = _stack()
    a = _resistor(comp_id="a", position=(0.0, 0.0))
    b = _resistor(comp_id="b", position=(4.0, 0.0))
    stack.push(PlaceCommand(a))
    stack.push(PlaceCommand(b))

    stack.push(DeleteCommand(["a", "b"]))
    assert stack.schematic.components == []

    stack.undo()
    ids = {c.id for c in stack.schematic.components}
    assert ids == {"a", "b"}


def test_delete_restores_original_order():
    stack = _stack()
    a = _resistor(comp_id="a")
    b = _resistor(comp_id="b")
    c = _resistor(comp_id="c")
    for comp in (a, b, c):
        stack.push(PlaceCommand(comp))

    stack.push(DeleteCommand(["b"]))
    assert [x.id for x in stack.schematic.components] == ["a", "c"]

    stack.undo()
    assert [x.id for x in stack.schematic.components] == ["a", "b", "c"]


def test_delete_explicit_wire_only():
    """A directly-selected wire (no components) is deleted and restored."""
    stack = UndoStack(
        Schematic(
            version="0.1",
            name="t",
            wires=[
                Wire(id="w1", points=[(0.0, 0.0), (2.0, 0.0)]),
                Wire(id="w2", points=[(0.0, 3.0), (2.0, 3.0)]),
            ],
        )
    )
    stack.push(DeleteCommand([], wire_ids=["w1"]))
    assert [w.id for w in stack.schematic.wires] == ["w2"]

    stack.undo()
    assert [w.id for w in stack.schematic.wires] == ["w1", "w2"]


def test_delete_mixed_component_and_wire():
    """Delete a component and a separately-selected (unconnected) wire."""
    s = Schematic(
        version="0.1",
        name="t",
        components=[_resistor(comp_id="a", position=(0.0, 0.0))],
        wires=[Wire(id="free", points=[(10.0, 10.0), (12.0, 10.0)])],
    )
    stack = UndoStack(s)
    stack.push(DeleteCommand(["a"], wire_ids=["free"]))
    assert stack.schematic.components == []
    assert stack.schematic.wires == []

    stack.undo()
    assert [c.id for c in stack.schematic.components] == ["a"]
    assert [w.id for w in stack.schematic.wires] == ["free"]


def test_delete_wire_id_dedup_with_connected():
    """A wire that is BOTH selected and connected isn't double-removed."""
    s = Schematic(
        version="0.1",
        name="t",
        components=[_resistor(comp_id="a", position=(0.0, 0.0))],  # pin (2,0)
        wires=[Wire(id="w", points=[(2.0, 0.0), (2.0, 2.0)])],     # touches pin
    )
    stack = UndoStack(s)
    stack.push(DeleteCommand(["a"], wire_ids=["w"]))
    assert stack.schematic.wires == []
    stack.undo()
    assert [w.id for w in stack.schematic.wires] == ["w"]


# ---------------------------------------------------------------------------
# WireCommand
# ---------------------------------------------------------------------------

def test_undo_wire():
    stack = _stack()
    wire = Wire(id="w1", points=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])
    stack.push(WireCommand(wire))
    assert len(stack.schematic.wires) == 1

    stack.undo()
    assert stack.schematic.wires == []

    stack.redo()
    assert stack.schematic.wires[0].points == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]


# ---------------------------------------------------------------------------
# SplitWireCommand
# ---------------------------------------------------------------------------

def test_split_wire_creates_two_wires():
    """SplitWireCommand replaces the original wire with two independent halves."""
    s = Schematic(
        version="0.1", name="t",
        wires=[Wire(id="w", points=[(0.0, 2.0), (4.0, 2.0)])],
    )
    SplitWireCommand("w", 1, (2.0, 2.0)).do(s)
    assert len(s.wires) == 2
    pts = sorted([w.points for w in s.wires])
    assert pts == [[(0.0, 2.0), (2.0, 2.0)], [(2.0, 2.0), (4.0, 2.0)]]
    # Original wire ID is gone.
    assert all(w.id != "w" for w in s.wires)


def test_split_wire_undo_restores():
    s = Schematic(
        version="0.1", name="t",
        wires=[Wire(id="w", points=[(0.0, 2.0), (4.0, 2.0)])],
    )
    cmd = SplitWireCommand("w", 1, (2.0, 2.0))
    cmd.do(s)
    assert len(s.wires) == 2
    cmd.undo(s)
    assert len(s.wires) == 1
    assert s.wires[0].id == "w"
    assert s.wires[0].points == [(0.0, 2.0), (4.0, 2.0)]
    cmd.redo(s)
    assert len(s.wires) == 2


def test_split_wire_corner_and_endpoint_edge_cases():
    """Corner (intermediate vertex) is split; endpoint coincidence is a no-op."""
    s = Schematic(
        version="0.1", name="t",
        wires=[Wire(id="w", points=[(0.0, 2.0), (2.0, 2.0), (4.0, 2.0)])],
    )
    # Unknown wire → no-op.
    SplitWireCommand("nope", 1, (1.0, 2.0)).do(s)
    assert len(s.wires) == 1

    # Splitting at the intermediate vertex (2,2) IS a valid corner split.
    SplitWireCommand("w", 1, (2.0, 2.0)).do(s)
    assert len(s.wires) == 2
    pts = sorted([w.points for w in s.wires])
    assert pts == [[(0.0, 2.0), (2.0, 2.0)], [(2.0, 2.0), (4.0, 2.0)]]

    # Splitting at an endpoint of an existing wire → no-op (point is at pts[0] or pts[-1]).
    s2 = Schematic(
        version="0.1", name="t",
        wires=[Wire(id="x", points=[(0.0, 2.0), (4.0, 2.0)])],
    )
    SplitWireCommand("x", 0, (0.0, 2.0)).do(s2)
    assert len(s2.wires) == 1   # endpoint → no split


def test_split_plus_wire_macro_is_one_undo():
    """A MacroCommand of split + add behaves as a single undoable unit."""
    s = Schematic(
        version="0.1", name="t",
        wires=[Wire(id="a", points=[(0.0, 2.0), (4.0, 2.0)])],
    )
    stack = UndoStack(s)
    macro = MacroCommand(
        [
            SplitWireCommand("a", 1, (2.0, 2.0)),
            WireCommand(Wire(id="b", points=[(2.0, 2.0), (2.0, 5.0)])),
        ],
        label="Wire",
    )
    stack.push(macro)
    # Two halves + new wire = 3 wires total.
    assert len(stack.schematic.wires) == 3
    all_pts = sorted([w.points for w in stack.schematic.wires])
    assert [(0.0, 2.0), (2.0, 2.0)] in all_pts
    assert [(2.0, 2.0), (4.0, 2.0)] in all_pts
    assert stack.undo_count == 1

    stack.undo()
    assert len(stack.schematic.wires) == 1
    assert stack.schematic.wires[0].id == "a"
    assert stack.schematic.wires[0].points == [(0.0, 2.0), (4.0, 2.0)]


# ---------------------------------------------------------------------------
# EditCommand
# ---------------------------------------------------------------------------

def test_undo_edit_options():
    stack = _stack()
    comp = _resistor(comp_id="r1", options="l=$R_1$")
    stack.push(PlaceCommand(comp))

    stack.push(EditCommand("r1", new_options="l=$R_2$, v=$V$"))
    assert stack.schematic.components[0].options == "l=$R_2$, v=$V$"

    stack.undo()
    assert stack.schematic.components[0].options == "l=$R_1$"

    stack.redo()
    assert stack.schematic.components[0].options == "l=$R_2$, v=$V$"


def test_edit_with_explicit_old_options():
    stack = _stack()
    comp = _resistor(comp_id="r1", options="l=x")
    stack.push(PlaceCommand(comp))

    cmd = EditCommand("r1", new_options="l=y", old_options="l=x")
    stack.push(cmd)
    assert stack.schematic.components[0].options == "l=y"
    stack.undo()
    assert stack.schematic.components[0].options == "l=x"


# ---------------------------------------------------------------------------
# MacroCommand
# ---------------------------------------------------------------------------

def test_macro_command_groups_as_one_unit():
    stack = _stack()
    a = _resistor(comp_id="a")
    b = _resistor(comp_id="b")
    macro = MacroCommand([PlaceCommand(a), PlaceCommand(b)], label="Paste")
    stack.push(macro)

    assert {c.id for c in stack.schematic.components} == {"a", "b"}
    assert stack.undo_count == 1  # one undoable unit

    stack.undo()
    assert stack.schematic.components == []

    stack.redo()
    assert {c.id for c in stack.schematic.components} == {"a", "b"}


def test_macro_undoes_children_in_reverse():
    """A move that depends on a prior place must unwind in reverse order."""
    stack = _stack()
    comp = _resistor(comp_id="r1", position=(0.0, 0.0))
    macro = MacroCommand(
        [PlaceCommand(comp), MoveCommand(["r1"], delta=(2.0, 0.0))]
    )
    stack.push(macro)
    assert stack.schematic.components[0].position == (2.0, 0.0)

    stack.undo()
    assert stack.schematic.components == []


# ---------------------------------------------------------------------------
# Stack semantics
# ---------------------------------------------------------------------------

def test_push_clears_redo_history():
    stack = _stack()
    stack.push(PlaceCommand(_resistor(comp_id="a")))
    stack.undo()
    assert stack.can_redo()

    stack.push(PlaceCommand(_resistor(comp_id="b")))
    assert not stack.can_redo()
    assert {c.id for c in stack.schematic.components} == {"b"}


def test_undo_redo_on_empty_stack_returns_none():
    stack = _stack()
    assert stack.undo() is None
    assert stack.redo() is None


def test_clear_drops_history():
    stack = _stack()
    stack.push(PlaceCommand(_resistor()))
    stack.clear()
    assert not stack.can_undo()
    assert not stack.can_redo()


def test_labels_reported():
    stack = _stack()
    assert stack.undo_label() is None
    stack.push(PlaceCommand(_resistor()))
    assert stack.undo_label() == "Place"
    stack.undo()
    assert stack.redo_label() == "Place"


# ---------------------------------------------------------------------------
# Deep history — 20 mixed operations
# ---------------------------------------------------------------------------

def test_undo_stack_depth():
    """20 sequential ops, then undo all 20 → original empty state (spec §13.3)."""
    stack = _stack()
    assert stack.schematic.components == []
    assert stack.schematic.wires == []

    ops: list[Command] = []

    # Build a varied sequence of exactly 20 commands.
    comp_ids = [f"c{i}" for i in range(8)]
    # 8 places
    for cid in comp_ids:
        ops.append(PlaceCommand(_resistor(comp_id=cid, position=(0.0, 0.0))))
    # 4 moves
    for cid in comp_ids[:4]:
        ops.append(MoveCommand([cid], delta=(1.0, 0.5)))
    # 4 edits
    for cid in comp_ids[:4]:
        ops.append(EditCommand(cid, new_options=f"l=${cid}$"))
    # 2 wires
    ops.append(WireCommand(Wire(id="w0", points=[(0.0, 0.0), (3.0, 0.0)])))
    ops.append(WireCommand(Wire(id="w1", points=[(0.0, 0.0), (0.0, 3.0)])))
    # 2 deletes
    ops.append(DeleteCommand([comp_ids[6]]))
    ops.append(DeleteCommand([comp_ids[7]]))

    assert len(ops) == 20
    for op in ops:
        stack.push(op)

    assert stack.undo_count == 20

    # Undo everything.
    for _ in range(20):
        assert stack.undo() is not None

    assert stack.schematic.components == []
    assert stack.schematic.wires == []
    assert not stack.can_undo()
    assert stack.redo_count == 20


def test_full_undo_then_full_redo_roundtrip():
    """After undoing all and redoing all, the model state must match."""
    stack = _stack()
    a = _resistor(comp_id="a", position=(0.0, 0.0))
    stack.push(PlaceCommand(a))
    stack.push(MoveCommand(["a"], delta=(2.0, 0.0)))
    stack.push(EditCommand("a", new_options="l=$R_a$"))

    after = copy.deepcopy(stack.schematic)

    for _ in range(3):
        stack.undo()
    assert stack.schematic.components == []

    for _ in range(3):
        stack.redo()

    assert stack.schematic.components == after.components
    assert stack.schematic.wires == after.wires


def test_command_is_abstract():
    with pytest.raises(TypeError):
        Command()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# MoveCommand — connected wires follow the moving component
# ---------------------------------------------------------------------------

from app.schematic.validate import validate  # noqa: E402


def _two_resistors_with_wire() -> Schematic:
    """A at (0,0) [pins (0,0),(2,0)], B at (5,0) [pins (5,0),(7,0)],
    straight wire from A.out (2,0) to B.in (5,0)."""
    return Schematic(
        version="0.1",
        name="t",
        components=[
            _resistor(comp_id="a", position=(0.0, 0.0)),
            _resistor(comp_id="b", position=(5.0, 0.0)),
        ],
        wires=[Wire(id="w", points=[(2.0, 0.0), (5.0, 0.0)])],
    )


def test_move_drags_connected_endpoint_along_axis():
    s = _two_resistors_with_wire()
    # Move A so its pin (2,0) slides to (2,-1): endpoint follows, elbow added.
    MoveCommand(["a"], (0.0, -1.0)).do(s)
    assert s.wires[0].points[0] == (2.0, -1.0)
    assert s.wires[0].points[-1] == (5.0, 0.0)  # far end unchanged
    assert validate(s) == []


def test_move_inserts_elbow_to_stay_manhattan():
    s = _two_resistors_with_wire()
    # Move A perpendicular to the wire: endpoint (2,0) -> (2,2). An elbow keeps
    # the segment to the unmoved far end Manhattan.
    MoveCommand(["a"], (0.0, 2.0)).do(s)
    pts = s.wires[0].points
    assert pts[0] == (2.0, 2.0)
    assert pts[-1] == (5.0, 0.0)
    assert validate(s) == []  # no diagonal segments
    # consecutive segments are axis-aligned
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        assert x0 == x1 or y0 == y1


def test_move_both_ends_translates_rigidly():
    s = _two_resistors_with_wire()
    MoveCommand(["a", "b"], (1.0, 1.0)).do(s)
    # Whole wire shifts by the delta, keeping exactly two points.
    assert s.wires[0].points == [(3.0, 1.0), (6.0, 1.0)]
    assert validate(s) == []


def test_move_both_ends_preserves_elbow_shape():
    s = Schematic(
        version="0.1",
        name="t",
        components=[
            _resistor(comp_id="a", position=(0.0, 0.0)),   # pin (2,0)
            _resistor(comp_id="b", position=(5.0, 3.0)),   # pin (5,3)
        ],
        wires=[Wire(id="w", points=[(2.0, 0.0), (5.0, 0.0), (5.0, 3.0)])],
    )
    MoveCommand(["a", "b"], (1.0, 1.0)).do(s)
    assert s.wires[0].points == [(3.0, 1.0), (6.0, 1.0), (6.0, 4.0)]
    assert validate(s) == []


def test_move_leaves_unconnected_wires_untouched():
    s = _two_resistors_with_wire()
    s.wires.append(Wire(id="free", points=[(10.0, 10.0), (12.0, 10.0)]))
    MoveCommand(["a"], (0.0, 2.0)).do(s)
    free = next(w for w in s.wires if w.id == "free")
    assert free.points == [(10.0, 10.0), (12.0, 10.0)]


def test_move_undo_restores_exact_wire_geometry():
    s = _two_resistors_with_wire()
    original = [tuple(p) for p in s.wires[0].points]
    cmd = MoveCommand(["a"], (0.0, 2.0))
    cmd.do(s)
    assert s.wires[0].points != original  # changed
    cmd.undo(s)
    assert s.wires[0].points == original  # restored exactly
    assert s.components[0].position == (0.0, 0.0)


def test_move_redo_reapplies_wire_follow():
    s = _two_resistors_with_wire()
    cmd = MoveCommand(["a"], (0.0, 2.0))
    cmd.do(s)
    after = [tuple(p) for p in s.wires[0].points]
    cmd.undo(s)
    cmd.redo(s)
    assert s.wires[0].points == after


def test_move_endpoint_on_far_end_follows():
    """The wire's END endpoint (not just start) follows its component."""
    s = _two_resistors_with_wire()
    # Move B so its pin (5,0) -> (5,2): the wire END should follow.
    MoveCommand(["b"], (0.0, 2.0)).do(s)
    assert s.wires[0].points[-1] == (5.0, 2.0)
    assert s.wires[0].points[0] == (2.0, 0.0)
    assert validate(s) == []


def test_move_via_stack_follows_and_undoes():
    stack = UndoStack(_two_resistors_with_wire())
    stack.push(MoveCommand(["a"], (0.0, 2.0)))
    assert stack.schematic.wires[0].points[0] == (2.0, 2.0)
    stack.undo()
    assert stack.schematic.wires[0].points == [(2.0, 0.0), (5.0, 0.0)]


def test_reshape_helper_matches_move_command():
    """The shared reshape helper produces the same path as MoveCommand."""
    s = _two_resistors_with_wire()
    MoveCommand(["a"], (0.0, 2.0)).do(s)
    via_command = s.wires[0].points

    via_helper = reshape_wire_points(
        [(2.0, 0.0), (5.0, 0.0)], start_hit=True, end_hit=False, dx=0.0, dy=2.0
    )
    assert via_helper == via_command


def test_reshape_helper_both_ends_rigid():
    out = reshape_wire_points(
        [(2.0, 0.0), (5.0, 0.0)], start_hit=True, end_hit=True, dx=1.0, dy=1.0
    )
    assert out == [(3.0, 1.0), (6.0, 1.0)]


def test_reshape_helper_no_hit_returns_input():
    pts = [(0.0, 0.0), (2.0, 0.0)]
    assert reshape_wire_points(pts, start_hit=False, end_hit=False, dx=5, dy=5) == pts


def test_reshape_helper_unsimplified_keeps_collinear():
    """With simplify=False (preview mode) a collinear slide keeps the midpoint."""
    out = reshape_wire_points(
        [(2.0, 0.0), (5.0, 0.0)], start_hit=True, end_hit=False,
        dx=-1.0, dy=0.0, simplify=False,
    )
    assert out == [(1.0, 0.0), (5.0, 0.0)]  # 2-pt either way here (no elbow)


def test_move_extending_line_stays_minimal():
    """Dragging an endpoint straight along the wire must not add a vertex.

    A.out (2,0) sits on a straight wire to B.in (5,0). Moving A left along the
    same axis keeps the wire two-point — no redundant collinear node.
    """
    s = _two_resistors_with_wire()
    MoveCommand(["a"], (-1.0, 0.0)).do(s)   # pin (2,0) -> (1,0), still collinear
    assert s.wires[0].points == [(1.0, 0.0), (5.0, 0.0)]


def test_move_extending_line_codegen_minimal():
    from app.codegen.circuitikz import generate

    s = _two_resistors_with_wire()
    MoveCommand(["a"], (-1.0, 0.0)).do(s)
    src = generate(s)
    # The wire path must be a single segment, not three collinear nodes.
    assert "(1,0) -- (5,0)" in src
    assert "(2,0) -- (5,0)" not in src


# ---------------------------------------------------------------------------
# MoveWireVertexCommand — drag a single wire vertex
# ---------------------------------------------------------------------------

def _wire_only(points):
    return Schematic(
        version="0.1", name="t", wires=[Wire(id="w", points=list(points))]
    )


def test_vertex_move_middle_off_axis_stays_manhattan():
    s = _wire_only([(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)])
    MoveWireVertexCommand("w", 1, (4.0, 1.0)).do(s)
    pts = s.wires[0].points
    assert pts[0] == (0.0, 0.0)
    assert pts[-1] == (2.0, 3.0)
    assert validate(s) == []
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        assert x0 == x1 or y0 == y1


def test_vertex_move_along_axis_simplifies():
    s = _wire_only([(0.0, 0.0), (2.0, 0.0), (5.0, 0.0)])
    MoveWireVertexCommand("w", 1, (3.0, 0.0)).do(s)
    # The middle vertex stayed collinear → collapses to a single segment.
    assert s.wires[0].points == [(0.0, 0.0), (5.0, 0.0)]


def test_vertex_move_free_endpoint():
    s = _wire_only([(0.0, 0.0), (3.0, 0.0)])
    MoveWireVertexCommand("w", 1, (3.0, 2.0)).do(s)
    assert s.wires[0].points[0] == (0.0, 0.0)
    assert s.wires[0].points[-1] == (3.0, 2.0)
    assert validate(s) == []


def test_vertex_move_undo_restores_exactly():
    s = _wire_only([(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)])
    orig = [tuple(p) for p in s.wires[0].points]
    cmd = MoveWireVertexCommand("w", 1, (4.0, 1.0))
    cmd.do(s)
    assert s.wires[0].points != orig
    cmd.undo(s)
    assert s.wires[0].points == orig


def test_vertex_move_redo():
    s = _wire_only([(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)])
    cmd = MoveWireVertexCommand("w", 1, (4.0, 1.0))
    cmd.do(s)
    after = [tuple(p) for p in s.wires[0].points]
    cmd.undo(s)
    cmd.redo(s)
    assert s.wires[0].points == after


def test_vertex_move_unknown_wire_is_noop():
    s = _wire_only([(0.0, 0.0), (2.0, 0.0)])
    MoveWireVertexCommand("nope", 0, (9.0, 9.0)).do(s)  # should not raise
    assert s.wires[0].points == [(0.0, 0.0), (2.0, 0.0)]


def test_vertex_move_collapse_removes_wire():
    """Dragging a vertex onto the wire's only other point removes the wire.

    Regression: this used to leave a degenerate single-point wire (which then
    suppressed nearby ocirc markers and emitted a stray draw coordinate).
    """
    s = _wire_only([(0.0, 0.0), (2.0, 0.0)])
    MoveWireVertexCommand("w", 1, (0.0, 0.0)).do(s)   # drag end onto start
    assert s.wires == []   # collapsed wire is removed, not left degenerate


def test_vertex_move_collapse_undo_restores_wire():
    """Undo re-adds a wire that a collapse removed, with its original points."""
    s = _wire_only([(0.0, 0.0), (2.0, 0.0)])
    cmd = MoveWireVertexCommand("w", 1, (0.0, 0.0))
    cmd.do(s)
    assert s.wires == []
    cmd.undo(s)
    assert len(s.wires) == 1
    assert s.wires[0].id == "w"
    assert s.wires[0].points == [(0.0, 0.0), (2.0, 0.0)]
    cmd.redo(s)
    assert s.wires == []


def test_vertex_move_via_stack():
    stack = UndoStack(_wire_only([(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)]))
    stack.push(MoveWireVertexCommand("w", 1, (4.0, 1.0)))
    assert validate(stack.schematic) == []
    stack.undo()
    assert stack.schematic.wires[0].points == [(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)]


# ---------------------------------------------------------------------------
# MoveOptionsLabelCommand
# ---------------------------------------------------------------------------

def test_move_options_label_sets_offset():
    stack = _stack()
    comp = _resistor(comp_id="r1")
    stack.push(PlaceCommand(comp))
    assert stack.schematic.components[0].label_offset is None

    stack.push(MoveOptionsLabelCommand("r1", (10.0, -20.0)))
    assert stack.schematic.components[0].label_offset == (10.0, -20.0)


def test_move_options_label_undo():
    stack = _stack()
    comp = _resistor(comp_id="r1")
    stack.push(PlaceCommand(comp))
    stack.push(MoveOptionsLabelCommand("r1", (5.0, 3.0)))
    assert stack.schematic.components[0].label_offset == (5.0, 3.0)

    stack.undo()
    assert stack.schematic.components[0].label_offset is None


def test_move_options_label_undo_preserves_prior_offset():
    stack = _stack()
    comp = _resistor(comp_id="r1")
    stack.push(PlaceCommand(comp))
    stack.push(MoveOptionsLabelCommand("r1", (5.0, 3.0)))
    stack.push(MoveOptionsLabelCommand("r1", (9.0, 1.0)))

    stack.undo()
    assert stack.schematic.components[0].label_offset == (5.0, 3.0)

    stack.undo()
    assert stack.schematic.components[0].label_offset is None


def test_move_options_label_redo():
    stack = _stack()
    comp = _resistor(comp_id="r1")
    stack.push(PlaceCommand(comp))
    stack.push(MoveOptionsLabelCommand("r1", (7.0, -4.0)))
    stack.undo()
    stack.redo()
    assert stack.schematic.components[0].label_offset == (7.0, -4.0)


def test_move_options_label_clears_offset():
    stack = _stack()
    comp = _resistor(comp_id="r1")
    stack.push(PlaceCommand(comp))
    stack.push(MoveOptionsLabelCommand("r1", (5.0, 5.0)))
    stack.push(MoveOptionsLabelCommand("r1", None))
    assert stack.schematic.components[0].label_offset is None

    stack.undo()
    assert stack.schematic.components[0].label_offset == (5.0, 5.0)


# ---------------------------------------------------------------------------
# MoveCommand — free-endpoint (open-circle) wires follow select-all drags
# ---------------------------------------------------------------------------

def test_move_all_translates_free_endpoint():
    """Dragging all components rigidly translates free wire endpoints too.

    Two-component schematic: moving both (select-all) translates the free end;
    moving only one (partial) leaves the free end anchored.
    """
    stack = _stack()
    a = _resistor(comp_id="a", position=(0.0, 0.0))   # pins at (0,0) and (2,0)
    b = _resistor(comp_id="b", position=(4.0, 0.0))   # pins at (4,0) and (6,0)
    stack.push(PlaceCommand(a))
    stack.push(PlaceCommand(b))
    # Wire from b's 'out' pin (6,0) to a free end at (8,0).
    stack.push(WireCommand(Wire(id="w1", points=[(6.0, 0.0), (8.0, 0.0)])))

    # Moving only 'b' — partial drag — free end stays anchored.
    stack.push(MoveCommand(["b"], delta=(1.0, 0.0)))
    assert stack.schematic.wires[0].points[-1] == (8.0, 0.0)
    stack.undo()

    # Moving both (select-all) — free end moves with the circuit.
    stack.push(MoveCommand(["a", "b"], delta=(1.0, 0.0)))
    assert stack.schematic.wires[0].points[-1] == (9.0, 0.0)


def test_move_all_free_endpoint_undo():
    """Undo of a select-all move restores the free endpoint to its original position."""
    stack = _stack()
    comp = _resistor(comp_id="r1", position=(0.0, 0.0))
    stack.push(PlaceCommand(comp))
    stack.push(WireCommand(Wire(id="w1", points=[(2.0, 0.0), (4.0, 0.0)])))

    stack.push(MoveCommand(["r1"], delta=(2.0, 0.0)))
    assert stack.schematic.wires[0].points[-1] == (6.0, 0.0)

    stack.undo()
    assert stack.schematic.wires[0].points[-1] == (4.0, 0.0)


def test_partial_move_leaves_free_endpoint():
    """Dragging a subset of components does NOT move unconnected free endpoints."""
    stack = _stack()
    a = _resistor(comp_id="a", position=(0.0, 0.0))
    b = _resistor(comp_id="b", position=(4.0, 0.0))
    stack.push(PlaceCommand(a))
    stack.push(PlaceCommand(b))
    # Wire from b's 'out' pin (6,0) to a free end at (8,0).
    stack.push(WireCommand(Wire(id="w1", points=[(6.0, 0.0), (8.0, 0.0)])))

    # Move only 'a' — free end of w1 (not touching a) should not move.
    stack.push(MoveCommand(["a"], delta=(1.0, 0.0)))
    wire = stack.schematic.wires[0]
    assert wire.points[-1] == (8.0, 0.0)   # free end stays


def test_explicit_wire_ids_translate_rigidly():
    """Explicitly passing wire_ids causes those wires to translate rigidly."""
    # Two components so dragging only 'a' is a genuine partial drag.
    stack = _stack()
    a = _resistor(comp_id="a", position=(0.0, 0.0))
    b = _resistor(comp_id="b", position=(4.0, 0.0))
    stack.push(PlaceCommand(a))
    stack.push(PlaceCommand(b))
    # Free wire not connected to any component pin.
    stack.push(WireCommand(Wire(id="w_free", points=[(8.0, 0.0), (10.0, 0.0)])))

    # Partial drag of 'a' without wire_ids: free wire stays.
    stack.push(MoveCommand(["a"], delta=(1.0, 0.0)))
    assert stack.schematic.wires[0].points == [(8.0, 0.0), (10.0, 0.0)]
    stack.undo()

    # Same drag but with w_free explicitly included: free wire translates.
    stack.push(MoveCommand(["a"], delta=(1.0, 0.0), wire_ids=["w_free"]))
    assert stack.schematic.wires[0].points == [(9.0, 0.0), (11.0, 0.0)]

    stack.undo()
    assert stack.schematic.wires[0].points == [(8.0, 0.0), (10.0, 0.0)]


# ---------------------------------------------------------------------------
# GroupRotateCommand
# ---------------------------------------------------------------------------

def test_group_rotate_single_component():
    """Single component at its own centroid: position unchanged, rotation +90."""
    stack = _stack()
    comp = _resistor(comp_id="r1", position=(2.0, 3.0), rotation=0)
    stack.push(PlaceCommand(comp))
    stack.push(GroupRotateCommand(["r1"], [], centroid=(2.0, 3.0)))
    c = stack.schematic.components[0]
    assert c.position == (2.0, 3.0)
    assert c.rotation == 90


def test_group_rotate_two_components_centroid():
    """Two resistors rotate around their shared centroid."""
    stack = _stack()
    # Place at (0,0) and (2,0); centroid = (1,0).
    stack.push(PlaceCommand(_resistor(comp_id="a", position=(0.0, 0.0))))
    stack.push(PlaceCommand(_resistor(comp_id="b", position=(2.0, 0.0))))
    stack.push(GroupRotateCommand(["a", "b"], [], centroid=(1.0, 0.0)))
    positions = {c.id: c.position for c in stack.schematic.components}
    # 90° CW on screen (Qt Y-down): (dx,dy) → (-dy, dx)
    # a: dx=-1, dy=0  → new = (1-0, 0+(-1)) = (1, -1)
    # b: dx=1,  dy=0  → new = (1-0, 0+1)    = (1,  1)
    assert positions["a"] == (1.0, -1.0)
    assert positions["b"] == (1.0, 1.0)
    for c in stack.schematic.components:
        assert c.rotation == 90


def test_group_rotate_internal_wire():
    """A wire between two selected components rotates with the group."""
    stack = _stack()
    stack.push(PlaceCommand(_resistor(comp_id="a", position=(0.0, 0.0))))
    stack.push(PlaceCommand(_resistor(comp_id="b", position=(2.0, 0.0))))
    stack.push(WireCommand(Wire(id="w1", points=[(0.0, 0.0), (2.0, 0.0)])))
    stack.push(GroupRotateCommand(["a", "b"], ["w1"], centroid=(1.0, 0.0)))
    wire = stack.schematic.wires[0]
    # (0,0): dx=-1,dy=0 → (1-0, 0+(-1)) = (1,-1)
    # (2,0): dx=1, dy=0 → (1-0, 0+1)   = (1, 1)
    assert wire.points[0] == (1.0, -1.0)
    assert wire.points[-1] == (1.0, 1.0)


def test_group_rotate_boundary_wire():
    """A wire connecting a selected component to an unselected one is reshaped."""
    stack = _stack()
    # Resistor at (0,0): its 'in' pin is at (0,0) and 'out' pin at (2,0)
    # (R has span 2 GU, pins at each end).
    stack.push(PlaceCommand(_resistor(comp_id="r1", position=(0.0, 0.0))))
    # Free wire from (2,0) — the 'out' pin — going right to (4,0).
    stack.push(WireCommand(Wire(id="w_boundary", points=[(2.0, 0.0), (4.0, 0.0)])))
    # Rotate only r1 around its own centroid (0,0).
    stack.push(GroupRotateCommand(["r1"], [], centroid=(0.0, 0.0)))
    wire = stack.schematic.wires[0]
    # After rotating r1 90° CW on screen around (0,0):
    #   r1's 'out' pin was at (2,0): dx=2,dy=0 → (0-0, 0+2) = (0,2)
    # The boundary wire's start was at (2,0); it must now start at (0,2).
    assert wire.points[0] == (0.0, 2.0)
    # The far end (4,0) is unconnected and should not have moved.
    assert wire.points[-1] == (4.0, 0.0)


def test_group_rotate_boundary_wire_collapse_removes_it():
    """A boundary wire that folds onto itself under rotation is removed.

    Regression: GroupRotateCommand reshaped boundary wires with the same
    reshape_wire_points() that can collapse to a single point, but (unlike
    MoveCommand) didn't guard the result — leaving a stray degenerate wire.
    Here r1's out pin (2,0) rotates around (0,0) onto (0,2), which is the wire's
    free end, so the wire collapses.
    """
    stack = _stack()
    stack.push(PlaceCommand(_resistor(comp_id="r1", position=(0.0, 0.0))))
    stack.push(WireCommand(Wire(id="wb", points=[(2.0, 0.0), (0.0, 2.0)])))
    stack.push(GroupRotateCommand(["r1"], [], centroid=(0.0, 0.0)))
    assert stack.schematic.wires == []   # collapsed wire removed, not degenerate


def test_group_rotate_boundary_wire_collapse_undo_restores():
    """Undo re-adds a boundary wire that a rotation collapsed; redo removes it."""
    stack = _stack()
    stack.push(PlaceCommand(_resistor(comp_id="r1", position=(0.0, 0.0))))
    stack.push(WireCommand(Wire(id="wb", points=[(2.0, 0.0), (0.0, 2.0)])))
    stack.push(GroupRotateCommand(["r1"], [], centroid=(0.0, 0.0)))
    assert stack.schematic.wires == []
    stack.undo()
    assert len(stack.schematic.wires) == 1
    assert stack.schematic.wires[0].id == "wb"
    assert stack.schematic.wires[0].points == [(2.0, 0.0), (0.0, 2.0)]
    stack.redo()
    assert stack.schematic.wires == []


def test_group_rotate_undo():
    """Undo restores all component positions, rotations, and wire points."""
    stack = _stack()
    stack.push(PlaceCommand(_resistor(comp_id="a", position=(0.0, 0.0))))
    stack.push(PlaceCommand(_resistor(comp_id="b", position=(2.0, 0.0))))
    stack.push(WireCommand(Wire(id="w1", points=[(0.0, 0.0), (2.0, 0.0)])))
    stack.push(GroupRotateCommand(["a", "b"], ["w1"], centroid=(1.0, 0.0)))
    stack.undo()
    positions = {c.id: c.position for c in stack.schematic.components}
    assert positions["a"] == (0.0, 0.0)
    assert positions["b"] == (2.0, 0.0)
    for c in stack.schematic.components:
        assert c.rotation == 0
    assert stack.schematic.wires[0].points == [(0.0, 0.0), (2.0, 0.0)]


def test_group_rotate_redo():
    """Redo re-applies the rotation after an undo."""
    stack = _stack()
    stack.push(PlaceCommand(_resistor(comp_id="a", position=(0.0, 0.0))))
    stack.push(PlaceCommand(_resistor(comp_id="b", position=(2.0, 0.0))))
    stack.push(GroupRotateCommand(["a", "b"], [], centroid=(1.0, 0.0)))
    stack.undo()
    stack.redo()
    positions = {c.id: c.position for c in stack.schematic.components}
    assert positions["a"] == (1.0, -1.0)
    assert positions["b"] == (1.0, 1.0)
    for c in stack.schematic.components:
        assert c.rotation == 90


# ---------------------------------------------------------------------------
# ResizeCommand
# ---------------------------------------------------------------------------

def _open(comp_id: str | None = None, position=(0.0, 0.0)) -> Component:
    return Component(
        id=comp_id or _uid(),
        kind="open",
        position=position,
        rotation=0,
        options="",
    )


def test_resize_sets_span_override():
    stack = _stack()
    stack.push(PlaceCommand(_open(comp_id="a", position=(0.0, 0.0))))
    stack.push(ResizeCommand("a", new_span=(4.0, 0.0), old_span=(2.0, 0.0)))
    comp = stack.schematic.components[0]
    assert comp.span_override == (4.0, 0.0)


def test_resize_undo_restores_span():
    stack = _stack()
    stack.push(PlaceCommand(_open(comp_id="a", position=(0.0, 0.0))))
    stack.push(ResizeCommand("a", new_span=(4.0, 0.0), old_span=(2.0, 0.0)))
    stack.undo()
    comp = stack.schematic.components[0]
    assert comp.span_override == (2.0, 0.0)


def test_resize_redo():
    stack = _stack()
    stack.push(PlaceCommand(_open(comp_id="a", position=(0.0, 0.0))))
    stack.push(ResizeCommand("a", new_span=(4.0, 0.0), old_span=(2.0, 0.0)))
    stack.undo()
    stack.redo()
    comp = stack.schematic.components[0]
    assert comp.span_override == (4.0, 0.0)


def test_resize_reshapes_connected_wire():
    """A wire connected to the terminal pin follows the resize."""
    stack = _stack()
    stack.push(PlaceCommand(_open(comp_id="a", position=(0.0, 0.0))))
    # Wire from terminal pin (2,0) to (2,2).
    stack.push(WireCommand(Wire(id="w1", points=[(2.0, 0.0), (2.0, 2.0)])))
    stack.push(ResizeCommand("a", new_span=(4.0, 0.0), old_span=(2.0, 0.0)))
    wire = stack.schematic.wires[0]
    assert wire.points[0] == (4.0, 0.0)


def test_resize_undo_restores_wire():
    stack = _stack()
    stack.push(PlaceCommand(_open(comp_id="a", position=(0.0, 0.0))))
    stack.push(WireCommand(Wire(id="w1", points=[(2.0, 0.0), (2.0, 2.0)])))
    stack.push(ResizeCommand("a", new_span=(4.0, 0.0), old_span=(2.0, 0.0)))
    stack.undo()
    wire = stack.schematic.wires[0]
    assert wire.points[0] == (2.0, 0.0)


def test_codegen_open_uses_span_override():
    from app.codegen.circuitikz import generate
    s = Schematic(version="0.1", name="t")
    comp = _open(comp_id="a", position=(0.0, 0.0))
    comp.span_override = (3.0, 0.0)
    s.components.append(comp)
    src = generate(s)
    assert "(0,0) to[open]" in src
    assert "3" in src


def test_codegen_ground():
    from app.codegen.circuitikz import generate
    s = Schematic(version="0.1", name="t")
    s.components.append(Component(
        id="g1", kind="ground", position=(1.0, 0.0),
        rotation=0, options="",
    ))
    src = generate(s)
    assert "node[ground]" in src


# ---------------------------------------------------------------------------
# Wire style commands (SetWireLineStyleCommand / SetWireLineWidthCommand)
# ---------------------------------------------------------------------------

def _wire_stack() -> tuple[UndoStack, str]:
    wid = _uid()
    sch = Schematic(version="0.1", name="t",
                    wires=[Wire(id=wid, points=[(0.0, 0.0), (2.0, 0.0)])])
    return UndoStack(sch), wid


def test_set_wire_line_style_do_undo_redo():
    stack, wid = _wire_stack()
    stack.push(SetWireLineStyleCommand(wid, "dashed", ""))
    assert stack.schematic.wires[0].line_style == "dashed"
    stack.undo()
    assert stack.schematic.wires[0].line_style == ""
    stack.redo()
    assert stack.schematic.wires[0].line_style == "dashed"


def test_set_wire_line_width_do_undo():
    stack, wid = _wire_stack()
    stack.push(SetWireLineWidthCommand(wid, 1.2, 0.4))
    assert stack.schematic.wires[0].line_width == 1.2
    stack.undo()
    assert stack.schematic.wires[0].line_width == 0.4


def test_set_wire_no_junction_dots_do_undo():
    stack, wid = _wire_stack()
    stack.push(SetWireNoJunctionDotsCommand(wid, True, False))
    assert stack.schematic.wires[0].no_junction_dots is True
    stack.undo()
    assert stack.schematic.wires[0].no_junction_dots is False


def test_set_wire_no_termination_dots_do_undo():
    stack, wid = _wire_stack()
    stack.push(SetWireNoTerminationDotsCommand(wid, True, False))
    assert stack.schematic.wires[0].no_termination_dots is True
    stack.undo()
    assert stack.schematic.wires[0].no_termination_dots is False


def test_set_wire_start_marker_do_undo_redo():
    stack, wid = _wire_stack()
    stack.push(SetWireStartMarkerCommand(wid, "arrow", ""))
    assert stack.schematic.wires[0].start_marker == "arrow"
    stack.undo()
    assert stack.schematic.wires[0].start_marker == ""
    stack.redo()
    assert stack.schematic.wires[0].start_marker == "arrow"


def test_set_wire_end_marker_do_undo():
    stack, wid = _wire_stack()
    stack.push(SetWireEndMarkerCommand(wid, "arrow", ""))
    assert stack.schematic.wires[0].end_marker == "arrow"
    stack.undo()
    assert stack.schematic.wires[0].end_marker == ""


def test_set_wire_start_label_do_undo_redo():
    stack, wid = _wire_stack()
    stack.push(SetWireStartLabelCommand(wid, "$x(t)$", ""))
    assert stack.schematic.wires[0].start_label == "$x(t)$"
    stack.undo()
    assert stack.schematic.wires[0].start_label == ""
    stack.redo()
    assert stack.schematic.wires[0].start_label == "$x(t)$"


def test_set_wire_end_label_do_undo():
    stack, wid = _wire_stack()
    stack.push(SetWireEndLabelCommand(wid, "$y(t)$", ""))
    assert stack.schematic.wires[0].end_label == "$y(t)$"
    stack.undo()
    assert stack.schematic.wires[0].end_label == ""


def test_set_wire_mid_label_do_undo_redo():
    stack, wid = _wire_stack()
    stack.push(SetWireMidLabelCommand(wid, "$V_{bus}$", ""))
    assert stack.schematic.wires[0].mid_label == "$V_{bus}$"
    stack.undo()
    assert stack.schematic.wires[0].mid_label == ""
    stack.redo()
    assert stack.schematic.wires[0].mid_label == "$V_{bus}$"


def test_set_wire_mid_label_pos_do_undo():
    stack, wid = _wire_stack()
    stack.push(SetWireMidLabelPosCommand(wid, 0.25, 0.5))
    assert stack.schematic.wires[0].mid_label_pos == 0.25
    stack.undo()
    assert stack.schematic.wires[0].mid_label_pos == 0.5
