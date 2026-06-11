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
    MergeWireCommand,
    MoveCommand,
    MoveOptionsLabelCommand,
    MoveWireVertexCommand,
    PlaceCommand,
    ResizeCommand,
    SetVariantCommand,
    SetWireEndLabelCommand,
    SetWireEndLabelPlacementCommand,
    SetWireEndMarkerCommand,
    SetWireLineStyleCommand,
    SetWireLineWidthCommand,
    SetWireMidLabelCommand,
    SetWireMidLabelPosCommand,
    SetWireNoJunctionDotsCommand,
    SetWireNoTerminationDotsCommand,
    SetWireStartLabelCommand,
    SetWireStartLabelPlacementCommand,
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


def test_split_wire_preserves_labels_and_style():
    """Regression: splitting a labelled/styled wire (e.g. a connection landing
    mid-segment of a labelled bus) keeps the decorations — start label/marker go
    to the half that keeps the original start, end label/marker to the other, and
    whole-wire style (line_style, z_order, …) goes to both. Undo restores the
    original verbatim."""
    orig = Wire(
        id="bus", points=[(2.0, 0.0), (2.0, 4.0)],
        start_label="$a$", start_marker="arrow", start_label_placement="above",
        end_label="$z$", end_marker="bar",
        line_style="dashed", line_width=0.8, z_order=3, no_junction_dots=True,
    )
    s = Schematic(version="0.1", name="t", wires=[orig])
    cmd = SplitWireCommand("bus", 1, (2.0, 2.0), "h1", "h2")
    cmd.do(s)
    h1 = next(w for w in s.wires if w.id == "h1")
    h2 = next(w for w in s.wires if w.id == "h2")
    # h1 keeps the original first point → its start decorations.
    assert (h1.start_label, h1.start_marker, h1.start_label_placement) == ("$a$", "arrow", "above")
    assert h1.end_label == "" and h1.end_marker == ""
    # h2 keeps the original last point → its end decorations.
    assert (h2.end_label, h2.end_marker) == ("$z$", "bar")
    assert h2.start_label == "" and h2.start_marker == ""
    # Whole-wire style carries to both halves.
    for h in (h1, h2):
        assert h.line_style == "dashed" and abs(h.line_width - 0.8) < 1e-9
        assert h.z_order == 3 and h.no_junction_dots is True
    # Undo restores the original wire verbatim (including all decorations).
    cmd.undo(s)
    assert len(s.wires) == 1
    restored = s.wires[0]
    assert restored.id == "bus" and restored.start_label == "$a$"
    assert restored.end_marker == "bar" and restored.z_order == 3


def test_merge_wire_preserves_labels_and_style():
    """Merging two wires (T-junction dissolve on delete) keeps each surviving
    end's label/marker and the line style; undo restores both originals."""
    w1 = Wire(id="w1", points=[(0.0, 2.0), (2.0, 2.0)], start_label="$in$",
              line_style="dotted", z_order=2)
    w2 = Wire(id="w2", points=[(2.0, 2.0), (4.0, 2.0)], end_label="$out$",
              end_marker="stealth")
    s = Schematic(version="0.1", name="t", wires=[w1, w2])
    cmd = MergeWireCommand("w1", "w2", (2.0, 2.0), "m")
    cmd.do(s)
    m = next(w for w in s.wires if w.id == "m")
    assert m.start_label == "$in$"          # from w1's far end
    assert m.end_label == "$out$" and m.end_marker == "stealth"  # from w2's far end
    assert m.line_style == "dotted" and m.z_order == 2           # body from w1
    cmd.undo(s)
    ids = {w.id for w in s.wires}
    assert ids == {"w1", "w2"}
    assert next(w for w in s.wires if w.id == "w1").start_label == "$in$"


def test_move_wire_only_translates_and_taps_follow():
    """A whole-wire move (MoveCommand with only wire_ids) rigidly translates the
    selected wire while a junction tap follows at the shared vertex and its far
    (pinned) end stays put; undo restores both."""
    bus = Wire(id="bus", points=[(2.0, 0.0), (2.0, 4.0)], start_label="$a$")
    tap = Wire(id="tap", points=[(2.0, 2.0), (5.0, 2.0)])
    s = Schematic(version="0.1", name="t", wires=[bus, tap])
    cmd = MoveCommand([], (1.0, 0.0), wire_ids=["bus"])
    cmd.do(s)
    assert next(w for w in s.wires if w.id == "bus").points == [(3.0, 0.0), (3.0, 4.0)]
    # Tap's bus end follows to (3,2); the far end stays at (5,2).
    assert next(w for w in s.wires if w.id == "tap").points == [(3.0, 2.0), (5.0, 2.0)]
    # The moved bus keeps its label.
    assert next(w for w in s.wires if w.id == "bus").start_label == "$a$"
    cmd.undo(s)
    assert next(w for w in s.wires if w.id == "bus").points == [(2.0, 0.0), (2.0, 4.0)]
    assert next(w for w in s.wires if w.id == "tap").points == [(2.0, 2.0), (5.0, 2.0)]


def test_move_collapsed_wire_restored_with_labels_on_undo():
    """If a move collapses a labelled wire to a point and it is removed, undo
    restores it verbatim (labels intact), not as a bare points-only wire."""
    from app.schematic.model import component_pin_positions
    comp = Component(id="c", kind="R", position=(0.0, 0.0), rotation=0, options="")
    # A second, far-away component so the move is NOT a select-all (which would
    # rigidly translate the whole circuit instead of dragging the pin's lead).
    other = Component(id="o", kind="R", position=(20.0, 20.0), rotation=0, options="")
    p0 = component_pin_positions(comp)[0]          # a pin of the resistor
    free = (p0[0] + 1.0, p0[1])                     # free endpoint 1 GU to the right
    # A lead from the pin to the free point; moving the component +1 GU in x drags
    # the pin onto the free end, collapsing the wire to a point (it is removed).
    lead = Wire(id="lead", points=[p0, free], start_label="$x$", line_style="dashed")
    s = Schematic(version="0.1", name="t", components=[comp, other], wires=[lead])
    cmd = MoveCommand(["c"], (1.0, 0.0))
    cmd.do(s)
    assert all(w.id != "lead" for w in s.wires)     # collapsed and removed
    cmd.undo(s)
    restored = next(w for w in s.wires if w.id == "lead")
    assert restored.points == [p0, free]            # geometry restored
    assert restored.start_label == "$x$" and restored.line_style == "dashed"


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


def test_move_off_multiwire_junction_restretches_lead():
    """A moving pin on a junction shared by 2+ wires keeps the existing net intact
    and stays connected via a fresh **re-stretch lead** from the node to the new
    pin (regression for the erroneous junction dot after dragging a component onto
    a rail node and back, plus the re-stretch follow-up).
    """
    s = Schematic(
        version="0.1",
        name="t",
        components=[
            _resistor(comp_id="a", position=(0.0, 0.0)),    # pins (0,0),(2,0)
            _resistor(comp_id="b", position=(10.0, 10.0)),  # elsewhere, not moved
        ],
        wires=[
            Wire(id="w1", points=[(2.0, 0.0), (2.0, 2.0)]),   # both end on the
            Wire(id="w2", points=[(2.0, 0.0), (4.0, 0.0)]),   # right pin (2,0)
        ],
    )
    cmd = MoveCommand(["a"], (0.0, -1.0))
    cmd.do(s)
    # Neither original wire is torn off the junction — both keep their geometry.
    assert next(w for w in s.wires if w.id == "w1").points == [(2.0, 0.0), (2.0, 2.0)]
    assert next(w for w in s.wires if w.id == "w2").points == [(2.0, 0.0), (4.0, 0.0)]
    # A new lead now connects the node (2,0) to the pin's new position (2,-1).
    new_leads = [w for w in s.wires if w.id not in ("w1", "w2")]
    assert len(new_leads) == 1
    assert new_leads[0].points == [(2.0, 0.0), (2.0, -1.0)]
    assert validate(s) == []
    # Undo removes the lead and restores everything; redo re-adds it.
    cmd.undo(s)
    assert [w.id for w in s.wires] == ["w1", "w2"]
    cmd.redo(s)
    assert any(w.points == [(2.0, 0.0), (2.0, -1.0)] for w in s.wires)


def test_move_removes_fully_contained_lead():
    """When a move slides a connected lead so it lies entirely on top of another
    wire, the now-redundant lead is removed (and restored on undo)."""
    s = Schematic(
        version="0.1",
        name="t",
        components=[
            _resistor(comp_id="a", position=(0.0, 0.0)),    # pins (0,0),(2,0)
            _resistor(comp_id="b", position=(10.0, 10.0)),  # elsewhere, not moved
        ],
        wires=[
            # Vertical lead off a's right pin (2,0), lying on a longer vertical rail.
            Wire(id="lead", points=[(2.0, 0.0), (2.0, 2.0)]),
            Wire(id="rail", points=[(2.0, -2.0), (2.0, 5.0)]),
        ],
    )
    cmd = MoveCommand(["a"], (0.0, 1.0))   # pin (2,0)->(2,1); lead → [(2,1),(2,2)]
    cmd.do(s)
    ids = [w.id for w in s.wires]
    assert "lead" not in ids       # fully on the rail now → removed
    assert "rail" in ids
    cmd.undo(s)
    assert "lead" in [w.id for w in s.wires]   # undo restores it
    assert next(w for w in s.wires if w.id == "lead").points == [(2.0, 0.0), (2.0, 2.0)]


def test_move_single_lead_still_follows():
    """The fix above must not change the common case: a pin with a *single* lead
    still drags that lead (it is the pin's sole wire endpoint)."""
    s = _two_resistors_with_wire()                 # one wire on A's pin (2,0)
    MoveCommand(["a"], (0.0, -1.0)).do(s)
    assert s.wires[0].points[0] == (2.0, -1.0)     # the lead followed
    assert validate(s) == []


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
    # (codegen normalises coordinates toward the origin, so after the move the
    # min corner shifts back to 0 — the wire is one segment from (2,0) to (6,0).)
    assert "(2,0) -- (6,0)" in src
    assert "(2,0) -- (4,0) -- (6,0)" not in src


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


def test_set_variant_do_undo_redo():
    stack = _stack()
    comp = Component(id=_uid(), kind="nigfete", position=(0.0, 0.0), rotation=0, options="")
    stack.push(PlaceCommand(comp))
    stack.push(SetVariantCommand(comp.id, "body_diode", True))
    assert stack.schematic.components[0].variants.get("body_diode") is True
    stack.undo()
    assert stack.schematic.components[0].variants.get("body_diode") in (False, None)
    stack.redo()
    assert stack.schematic.components[0].variants.get("body_diode") is True


def test_set_wire_start_label_placement_do_undo_redo():
    stack, wid = _wire_stack()
    stack.push(SetWireStartLabelPlacementCommand(wid, "above", ""))
    assert stack.schematic.wires[0].start_label_placement == "above"
    stack.undo()
    assert stack.schematic.wires[0].start_label_placement == ""
    stack.redo()
    assert stack.schematic.wires[0].start_label_placement == "above"


def test_set_wire_end_label_placement_do_undo():
    stack, wid = _wire_stack()
    stack.push(SetWireEndLabelPlacementCommand(wid, "below", ""))
    assert stack.schematic.wires[0].end_label_placement == "below"
    stack.undo()
    assert stack.schematic.wires[0].end_label_placement == ""


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


# ---------------------------------------------------------------------------
# Rect block-diagram edge connections — wires follow on move and resize
# ---------------------------------------------------------------------------

def _rect(comp_id="r", position=(0.0, 0.0), span=(2.0, 2.0)) -> Component:
    from app.components.model import RectComponent
    return RectComponent(
        id=comp_id, kind="rect", position=position, rotation=0,
        options="", span_override=span,
    )


def test_move_drags_wire_connected_to_rect_edge():
    """A wire starting on a rect's edge follows when the rect is moved.

    A second (unmoved) component keeps ``all_dragged`` false so only the
    edge-connected endpoint follows, not the whole wire.
    """
    s = Schematic(
        version="0.1", name="t",
        components=[
            _rect(comp_id="r", position=(0.0, 0.0), span=(2.0, 2.0)),
            _resistor(comp_id="x", position=(10.0, 10.0)),
        ],
        # Wire from the right-edge midpoint (2,1) down to a free end (2,4).
        wires=[Wire(id="w", points=[(2.0, 1.0), (2.0, 4.0)])],
    )
    MoveCommand(["r"], (3.0, 0.0)).do(s)
    # The edge endpoint follows the rect by the same delta; free end unchanged.
    assert s.wires[0].points[0] == (5.0, 1.0)
    assert s.wires[0].points[-1] == (2.0, 4.0)
    assert validate(s) == []


def test_move_rect_edge_wire_undo_restores():
    s = Schematic(
        version="0.1", name="t",
        components=[
            _rect(comp_id="r", position=(0.0, 0.0), span=(2.0, 2.0)),
            _resistor(comp_id="x", position=(10.0, 10.0)),
        ],
        wires=[Wire(id="w", points=[(2.0, 1.0), (2.0, 4.0)])],
    )
    original = [tuple(p) for p in s.wires[0].points]
    cmd = MoveCommand(["r"], (3.0, 0.0))
    cmd.do(s)
    assert s.wires[0].points != original
    cmd.undo(s)
    assert s.wires[0].points == original


def test_resize_rect_far_edge_wire_follows_scaled():
    """Growing a rect scales connected edge points about the fixed corner."""
    s = Schematic(
        version="0.1", name="t",
        components=[_rect(comp_id="r", position=(0.0, 0.0), span=(2.0, 2.0))],
        wires=[
            # Right edge midpoint (2,1) — a "far" edge that moves on resize.
            Wire(id="far", points=[(2.0, 1.0), (5.0, 1.0)]),
            # Left edge midpoint (0,1) — through the fixed corner; stays put.
            Wire(id="near", points=[(-3.0, 1.0), (0.0, 1.0)]),
        ],
    )
    ResizeCommand("r", new_span=(4.0, 2.0), old_span=(2.0, 2.0)).do(s)
    far = next(w for w in s.wires if w.id == "far")
    near = next(w for w in s.wires if w.id == "near")
    # (2,1) maps to (0 + 1*4, 0 + 0.5*2) = (4,1).
    assert far.points[0] == (4.0, 1.0)
    # Left-edge point is on the anchored corner's edge — unchanged.
    assert near.points[-1] == (0.0, 1.0)
    assert validate(s) == []


def test_resize_rect_edge_wire_undo_restores():
    s = Schematic(
        version="0.1", name="t",
        components=[_rect(comp_id="r", position=(0.0, 0.0), span=(2.0, 2.0))],
        wires=[Wire(id="far", points=[(2.0, 1.0), (5.0, 1.0)])],
    )
    original = [tuple(p) for p in s.wires[0].points]
    cmd = ResizeCommand("r", new_span=(4.0, 2.0), old_span=(2.0, 2.0))
    cmd.do(s)
    assert s.wires[0].points != original
    cmd.undo(s)
    assert s.wires[0].points == original


# ---------------------------------------------------------------------------
# Circle block-diagram cardinal connections — wires follow on move and resize
# ---------------------------------------------------------------------------

def _circle(comp_id="o", position=(0.0, 0.0), span=(2.0, 2.0)) -> Component:
    from app.components.model import CircleComponent
    return CircleComponent(
        id=comp_id, kind="circle", position=position, rotation=0,
        options="", span_override=span,
    )


def test_move_drags_wire_connected_to_circle_cardinal():
    """A wire on a circle's east cardinal point follows when the circle moves."""
    s = Schematic(
        version="0.1", name="t",
        components=[
            _circle(comp_id="o", position=(0.0, 0.0), span=(2.0, 2.0)),
            _resistor(comp_id="x", position=(10.0, 10.0)),
        ],
        # East cardinal point of the circle is (2,1); free end at (2,4).
        wires=[Wire(id="w", points=[(2.0, 1.0), (2.0, 4.0)])],
    )
    MoveCommand(["o"], (3.0, 0.0)).do(s)
    assert s.wires[0].points[0] == (5.0, 1.0)
    assert s.wires[0].points[-1] == (2.0, 4.0)
    assert validate(s) == []


def test_resize_circle_cardinal_wire_follows_scaled():
    """Growing a circle scales its cardinal connections about the fixed corner."""
    s = Schematic(
        version="0.1", name="t",
        components=[_circle(comp_id="o", position=(0.0, 0.0), span=(2.0, 2.0))],
        wires=[
            Wire(id="east", points=[(2.0, 1.0), (5.0, 1.0)]),   # E cardinal (moves)
            Wire(id="west", points=[(-3.0, 1.0), (0.0, 1.0)]),  # W cardinal (fixed edge)
        ],
    )
    ResizeCommand("o", new_span=(4.0, 2.0), old_span=(2.0, 2.0)).do(s)
    east = next(w for w in s.wires if w.id == "east")
    west = next(w for w in s.wires if w.id == "west")
    # E (2,1) maps about (0,0) to (0 + 1*4, 0 + 0.5*2) = (4,1).
    assert east.points[0] == (4.0, 1.0)
    # W (0,1) is on the anchored corner's edge — unchanged.
    assert west.points[-1] == (0.0, 1.0)
    assert validate(s) == []


def test_resize_circle_cardinal_wire_undo_restores():
    s = Schematic(
        version="0.1", name="t",
        components=[_circle(comp_id="o", position=(0.0, 0.0), span=(2.0, 2.0))],
        wires=[Wire(id="east", points=[(2.0, 1.0), (5.0, 1.0)])],
    )
    original = [tuple(p) for p in s.wires[0].points]
    cmd = ResizeCommand("o", new_span=(4.0, 2.0), old_span=(2.0, 2.0))
    cmd.do(s)
    assert s.wires[0].points != original
    cmd.undo(s)
    assert s.wires[0].points == original


# ---------------------------------------------------------------------------
# Junction move — orientation preserved into the junction
# ---------------------------------------------------------------------------

def test_reshape_junction_wire_preserves_vertical_via_corner():
    """A wire that enters the junction vertically (with an interior corner)
    keeps its vertical approach — the corner relocates with the junction."""
    from app.canvas.commands import reshape_junction_wire
    pts = [(2.0, 2.0), (2.0, 0.0), (5.0, 0.0)]      # junction (idx0) ↑ then →
    out = reshape_junction_wire(pts, 0, (4.0, 2.0))  # drag junction right
    assert out == [(4.0, 2.0), (4.0, 0.0), (5.0, 0.0)]   # vertical preserved


def test_reshape_junction_wire_preserves_horizontal_far_endpoint():
    """A 2-point wire entering horizontally keeps a horizontal segment at the
    junction (an elbow is inserted toward the fixed far endpoint)."""
    from app.canvas.commands import reshape_junction_wire
    pts = [(0.0, 0.0), (2.0, 0.0)]                   # junction at idx1, horizontal
    out = reshape_junction_wire(pts, 1, (2.0, 2.0))  # drag junction down
    assert out[-1] == (2.0, 2.0)
    assert out[-2][1] == 2.0                          # segment into junction is horizontal


def test_move_junction_command_orientation_and_undo():
    from app.canvas.commands import MoveJunctionCommand
    s = Schematic(
        version="0.1", name="t",
        wires=[
            Wire(id="stub", points=[(2.0, 2.0), (2.0, 0.0), (5.0, 0.0)]),
            Wire(id="left", points=[(0.0, 2.0), (2.0, 2.0)]),
        ],
    )
    cmd = MoveJunctionCommand([("stub", 0), ("left", 1)], (4.0, 2.0))
    cmd.do(s)
    stub = next(w for w in s.wires if w.id == "stub")
    left = next(w for w in s.wires if w.id == "left")
    assert stub.points == [(4.0, 2.0), (4.0, 0.0), (5.0, 0.0)]   # vertical kept
    assert left.points == [(0.0, 2.0), (4.0, 2.0)]               # horizontal kept
    cmd.undo(s)
    assert next(w for w in s.wires if w.id == "stub").points == [(2.0, 2.0), (2.0, 0.0), (5.0, 0.0)]
    assert next(w for w in s.wires if w.id == "left").points == [(0.0, 2.0), (2.0, 2.0)]


# ---------------------------------------------------------------------------
# Single connectivity convention (point_key) — float noise must not detach wires
# ---------------------------------------------------------------------------

# A coordinate with classic binary float noise: 0.1 + 0.2 != 0.3 exactly.
_NOISY_03 = 0.1 + 0.2


def test_move_follows_wire_with_float_noise_endpoint():
    """A wire endpoint that differs from a pin only by float noise (sub-1e-6)
    still follows the moving component — connectivity compares through
    point_key, not raw tuple equality."""
    comp = _resistor(comp_id="c", position=(_NOISY_03 - 2.0, 0.0))  # pin 2 at noisy 0.3
    other = _resistor(comp_id="o", position=(20.0, 20.0))           # not a select-all
    wire = Wire(id="w", points=[(0.3, 0.0), (5.0, 0.0)])            # clean 0.3 endpoint
    s = Schematic(version="0.1", name="t", components=[comp, other], wires=[wire])
    assert comp.position[0] + 2.0 != 0.3                            # genuinely noisy
    MoveCommand(["c"], (0.0, 1.0)).do(s)
    assert s.wires[0].points[0][1] == 1.0                           # endpoint followed


def test_delete_removes_wire_with_float_noise_endpoint():
    comp = _resistor(comp_id="c", position=(_NOISY_03 - 2.0, 0.0))
    wire = Wire(id="w", points=[(0.3, 0.0), (5.0, 0.0)])
    s = Schematic(version="0.1", name="t", components=[comp], wires=[wire])
    cmd = DeleteCommand(["c"])
    cmd.do(s)
    assert s.wires == []                       # noisy-connected wire deleted too
    cmd.undo(s)
    assert [w.id for w in s.wires] == ["w"]


# ---------------------------------------------------------------------------
# SplitWireCommand — split site resolved at do() time (stale-index safety)
# ---------------------------------------------------------------------------

def test_split_wire_resolves_index_after_move_reshapes_wire():
    """Inside a move+split macro the move reshapes the wire BEFORE the split
    runs, so the index captured against pre-move geometry is stale. The split
    must re-resolve from the point against current geometry."""
    s = Schematic(
        version="0.1", name="t",
        wires=[Wire(id="w", points=[(0.0, 2.0), (2.0, 2.0), (2.0, 6.0), (8.0, 6.0)])],
    )
    stack = UndoStack(s)
    # Rigid wire translate by (0,-2): the path becomes
    # (0,0)-(2,0)-(2,4)-(8,4); split point (5,4) lies on the LAST segment
    # (insert index 3), but against the pre-move geometry a caller computing
    # indices would have found nothing there.
    macro = MacroCommand([
        MoveCommand([], (0.0, -2.0), wire_ids=["w"]),
        SplitWireCommand("w", 1, (5.0, 4.0)),       # index 1 is a stale hint
    ])
    stack.push(macro)
    pts = sorted(w.points for w in s.wires)
    assert pts == [
        [(0.0, 0.0), (2.0, 0.0), (2.0, 4.0), (5.0, 4.0)],
        [(5.0, 4.0), (8.0, 4.0)],
    ]
    stack.undo()
    assert [w.id for w in s.wires] == ["w"]
    assert s.wires[0].points == [(0.0, 2.0), (2.0, 2.0), (2.0, 6.0), (8.0, 6.0)]


def test_split_wire_noop_when_point_not_on_wire_and_undo_aware():
    """A split whose point is no longer anywhere on the wire is a clean no-op,
    and its undo is also a no-op (it must not 'restore' stale geometry)."""
    s = Schematic(
        version="0.1", name="t",
        wires=[Wire(id="w", points=[(0.0, 0.0), (4.0, 0.0)])],
    )
    cmd = SplitWireCommand("w", 1, (2.0, 5.0))      # nowhere near the wire
    cmd.do(s)
    assert len(s.wires) == 1 and s.wires[0].id == "w"
    s.wires[0].points = [(0.0, 0.0), (9.0, 0.0)]    # mutate after the no-op
    cmd.undo(s)                                      # must not clobber anything
    assert s.wires[0].points == [(0.0, 0.0), (9.0, 0.0)]


def test_split_wire_noop_when_move_reshaped_endpoint_onto_point():
    """If an earlier command moved a wire endpoint onto the split site, the
    point is an endpoint at do() time → nothing to split (no degenerate half)."""
    s = Schematic(
        version="0.1", name="t",
        wires=[Wire(id="w", points=[(0.0, 0.0), (4.0, 0.0)])],
    )
    s.wires[0].points = [(2.0, 0.0), (4.0, 0.0)]     # simulate pre-split reshape
    cmd = SplitWireCommand("w", 1, (2.0, 0.0))
    cmd.do(s)
    assert len(s.wires) == 1 and len(s.wires[0].points) == 2


# ---------------------------------------------------------------------------
# Exception safety — macro unwind, push-not-recorded, missing-wire no-ops
# ---------------------------------------------------------------------------

class _BoomCommand(Command):
    """Test double: raises on do()."""
    label = "Boom"

    def do(self, schematic):
        raise RuntimeError("boom")

    def undo(self, schematic):  # pragma: no cover - never reached
        raise AssertionError("undo of a never-applied command")


def test_macro_unwinds_executed_children_on_failure():
    """If a child of a macro raises, the already-executed children are undone
    (in reverse) and the exception propagates — no half-applied document."""
    s = _empty()
    macro = MacroCommand([
        PlaceCommand(_resistor(comp_id="a")),
        WireCommand(Wire(id="w", points=[(0.0, 0.0), (2.0, 0.0)])),
        _BoomCommand(),
    ])
    with pytest.raises(RuntimeError):
        macro.do(s)
    assert s.components == [] and s.wires == []


def test_undo_stack_push_failure_records_nothing():
    stack = _stack()
    with pytest.raises(RuntimeError):
        stack.push(_BoomCommand())
    assert stack.undo_count == 0 and not stack.can_undo()
    # A failing macro likewise leaves the stack (and document) untouched.
    with pytest.raises(RuntimeError):
        stack.push(MacroCommand([PlaceCommand(_resistor()), _BoomCommand()]))
    assert stack.undo_count == 0
    assert stack.schematic.components == []


def test_wire_attr_command_on_missing_wire_is_noop():
    """A per-attribute wire command whose wire vanished is a clean no-op (the
    shared _find_wire returns None instead of raising)."""
    s = _empty()
    cmd = SetWireLineStyleCommand("ghost", "dashed", "")
    cmd.do(s)       # must not raise
    cmd.undo(s)     # must not raise
    assert s.wires == []


def test_typed_component_wrong_class_raises_typeerror():
    """_typed_component raises an explicit TypeError (not an assert, which
    vanishes under -O) when the component is the wrong class."""
    from app.canvas.commands import SetZOrderCommand
    s = _empty()
    s.components.append(_resistor(comp_id="r"))      # plain Component, no z_order
    with pytest.raises(TypeError):
        SetZOrderCommand("r", 1, 0).do(s)


# ---------------------------------------------------------------------------
# Verbatim wire restore — styles/labels and list position survive undo
# ---------------------------------------------------------------------------

def test_move_wire_vertex_collapse_restores_verbatim_at_index():
    """A vertex drag that collapses a labelled wire restores it verbatim
    (labels/style intact) at its original position in schematic.wires."""
    w0 = Wire(id="first", points=[(10.0, 10.0), (12.0, 10.0)])
    w1 = Wire(id="w", points=[(0.0, 0.0), (2.0, 0.0)],
              start_label="$a$", line_style="dashed", z_order=4)
    w2 = Wire(id="last", points=[(20.0, 20.0), (22.0, 20.0)])
    s = Schematic(version="0.1", name="t", wires=[w0, w1, w2])
    cmd = MoveWireVertexCommand("w", 1, (0.0, 0.0))   # collapse onto the start
    cmd.do(s)
    assert all(w.id != "w" for w in s.wires)
    cmd.undo(s)
    assert [w.id for w in s.wires] == ["first", "w", "last"]   # original index
    restored = s.wires[1]
    assert restored.start_label == "$a$" and restored.line_style == "dashed"
    assert restored.z_order == 4


def test_resize_collapse_restores_wire_verbatim_at_index():
    """A resize that collapses a connected, styled wire restores it verbatim at
    its original index on undo (was: bare Wire(id, points) appended at the end)."""
    comp = Component(id="a", kind="open", position=(0.0, 0.0), rotation=0,
                     options="", span_override=(2.0, 0.0))
    lead = Wire(id="lead", points=[(2.0, 0.0), (3.0, 0.0)],
                end_label="$v$", line_width=1.2)
    other = Wire(id="other", points=[(10.0, 10.0), (12.0, 10.0)])
    s = Schematic(version="0.1", name="t", components=[comp], wires=[lead, other])
    # Stretch the terminal onto the lead's far end: the lead collapses.
    cmd = ResizeCommand("a", new_span=(3.0, 0.0), old_span=(2.0, 0.0))
    cmd.do(s)
    assert all(w.id != "lead" for w in s.wires)
    cmd.undo(s)
    assert [w.id for w in s.wires] == ["lead", "other"]        # original index
    restored = s.wires[0]
    assert restored.end_label == "$v$" and abs(restored.line_width - 1.2) < 1e-9
    assert restored.points == [(2.0, 0.0), (3.0, 0.0)]


def test_move_junction_collapse_restores_verbatim_at_index():
    stub = Wire(id="stub", points=[(2.0, 2.0), (4.0, 2.0)],
                mid_label="$i$", line_style="dotted")
    other = Wire(id="other", points=[(2.0, 2.0), (2.0, 6.0)])
    s = Schematic(version="0.1", name="t", wires=[stub, other])
    from app.canvas.commands import MoveJunctionCommand
    # Drag the junction onto the stub's far end → the stub collapses.
    cmd = MoveJunctionCommand([("stub", 0), ("other", 0)], (4.0, 2.0))
    cmd.do(s)
    assert all(w.id != "stub" for w in s.wires)
    cmd.undo(s)
    assert [w.id for w in s.wires] == ["stub", "other"]
    restored = s.wires[0]
    assert restored.mid_label == "$i$" and restored.line_style == "dotted"
    assert restored.points == [(2.0, 2.0), (4.0, 2.0)]


def test_group_rotate_collapsed_wire_restored_verbatim_at_index():
    """A boundary wire that collapses under a group rotation is restored
    verbatim (style intact) at its original index on undo."""
    comp = _resistor(comp_id="c", position=(0.0, 0.0))      # pins (0,0),(2,0)
    # Boundary lead whose far end is where the pin lands after rotating 90° CW
    # about (0,0): pin (2,0) → (0,2), so the wire collapses to a point.
    lead = Wire(id="lead", points=[(2.0, 0.0), (0.0, 2.0)],
                start_label="$x$", line_style="dashed")
    other = Wire(id="other", points=[(10.0, 10.0), (12.0, 10.0)])
    s = Schematic(version="0.1", name="t", components=[comp], wires=[lead, other])
    cmd = GroupRotateCommand(["c"], [], (0.0, 0.0))
    cmd.do(s)
    assert all(w.id != "lead" for w in s.wires)             # collapsed + removed
    cmd.undo(s)
    assert [w.id for w in s.wires] == ["lead", "other"]      # original index
    restored = s.wires[0]
    assert restored.start_label == "$x$" and restored.line_style == "dashed"


# ---------------------------------------------------------------------------
# MoveJunctionCommand — a wire with BOTH endpoints at the junction
# ---------------------------------------------------------------------------

def test_move_junction_same_wire_both_endpoints_moves_both():
    """A loop wire with both endpoints at the junction must have BOTH ends
    moved on one evolving copy — reshaping each from the pristine points would
    let the second reshape overwrite the first."""
    from app.canvas.commands import MoveJunctionCommand
    loop = Wire(id="loop", points=[
        (2.0, 2.0), (2.0, 0.0), (6.0, 0.0), (6.0, 2.0), (2.0, 2.0)
    ])
    stub = Wire(id="stub", points=[(2.0, 2.0), (2.0, 5.0)])
    s = Schematic(version="0.1", name="t", wires=[loop, stub])
    cmd = MoveJunctionCommand([("loop", 0), ("loop", 4), ("stub", 0)], (3.0, 2.0))
    cmd.do(s)
    moved = next(w for w in s.wires if w.id == "loop")
    # No vertex may remain at the old junction coordinate.
    assert all((round(x, 6), round(y, 6)) != (2.0, 2.0) for x, y in moved.points)
    # Both wire ends arrive at the new junction point.
    assert moved.points[0] == (3.0, 2.0) and moved.points[-1] == (3.0, 2.0)
    cmd.undo(s)
    assert next(w for w in s.wires if w.id == "loop").points == [
        (2.0, 2.0), (2.0, 0.0), (6.0, 0.0), (6.0, 2.0), (2.0, 2.0)
    ]


# ---------------------------------------------------------------------------
# MergeWireCommand — sequential merges sharing a wire compose
# ---------------------------------------------------------------------------

def test_sequential_merges_sharing_a_wire_compose():
    """Two MergeWireCommands referencing the same wire (one wire bridging two
    dissolved junctions): the second re-resolves the consumed id to the first
    merge's result, so all three wires fuse into one. Undo restores all three."""
    a = Wire(id="A", points=[(2.0, 0.0), (6.0, 0.0)])
    b = Wire(id="B", points=[(0.0, 0.0), (2.0, 0.0)], start_label="$in$")
    c = Wire(id="C", points=[(6.0, 0.0), (8.0, 0.0)], end_label="$out$")
    s = Schematic(version="0.1", name="t", wires=[a, b, c])
    stack = UndoStack(s)
    macro = MacroCommand([
        MergeWireCommand("A", "B", (2.0, 0.0), "m1"),
        MergeWireCommand("A", "C", (6.0, 0.0), "m2"),   # A already consumed by m1
    ])
    stack.push(macro)
    assert len(s.wires) == 1
    merged = s.wires[0]
    assert merged.points in (
        [(0.0, 0.0), (8.0, 0.0)], [(8.0, 0.0), (0.0, 0.0)]
    )
    stack.undo()
    assert sorted(w.id for w in s.wires) == ["A", "B", "C"]
    assert next(w for w in s.wires if w.id == "B").start_label == "$in$"
    # Redo composes again identically.
    stack.redo()
    assert len(s.wires) == 1


# ---------------------------------------------------------------------------
# Group rotate — mirrored components keep their pins attached
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_group_rotate_mirrored_component_pins_follow(rotation):
    """Regression: mirror is applied OUTERMOST (after rotation), so a visual
    90° CW group turn must use rotation−90 for mirrored components; +90 sent
    the pins to the mirror-image position, detaching connected wires."""
    from app.schematic.model import component_pin_positions
    comp = _resistor(comp_id="c", position=(3.0, 2.0), rotation=rotation)
    comp.mirror = True
    s = Schematic(version="0.1", name="t", components=[comp])
    before = component_pin_positions(comp)
    cmd = GroupRotateCommand(["c"], [], (3.0, 2.0))
    cmd.do(s)
    after = component_pin_positions(comp)
    expected = [cmd._rot90cw(x, y, 3.0, 2.0) for x, y in before]
    for got, exp in zip(after, expected):
        assert abs(got[0] - exp[0]) < 1e-9 and abs(got[1] - exp[1]) < 1e-9
    cmd.undo(s)
    assert comp.rotation == rotation and comp.mirror is True
    assert component_pin_positions(comp) == before


def test_group_rotate_mirrored_component_boundary_wire_stays_attached():
    from app.schematic.model import component_pin_positions
    comp = _resistor(comp_id="c", position=(3.0, 2.0))
    comp.mirror = True
    pin = component_pin_positions(comp)[1]
    lead = Wire(id="lead", points=[pin, (pin[0], pin[1] + 4.0)])
    s = Schematic(version="0.1", name="t", components=[comp], wires=[lead])
    GroupRotateCommand(["c"], [], (3.0, 2.0)).do(s)
    new_pin = component_pin_positions(comp)[1]
    moved = next(w for w in s.wires if w.id == "lead")
    assert moved.points[0] == new_pin                   # still attached


# ---------------------------------------------------------------------------
# UndoStack — record() and save-point tracking (consumed by the UI layer)
# ---------------------------------------------------------------------------

def test_undo_stack_record_does_not_execute():
    """record() registers an ALREADY-applied command for undo without running
    do() again; undo/redo of the recorded command work normally."""
    stack = _stack()
    comp = _resistor(comp_id="a")
    cmd = PlaceCommand(comp)
    cmd.do(stack.schematic)                  # applied out-of-band (batch flush)
    stack.record(cmd)
    assert len(stack.schematic.components) == 1     # NOT applied twice
    assert stack.undo_count == 1
    stack.undo()
    assert stack.schematic.components == []
    stack.redo()
    assert len(stack.schematic.components) == 1


def test_save_point_fresh_stack_unmodified():
    stack = _stack()
    assert stack.is_modified() is False


def test_save_point_round_trip():
    stack = _stack()
    stack.push(PlaceCommand(_resistor()))
    assert stack.is_modified() is True
    stack.mark_save_point()
    assert stack.is_modified() is False
    stack.push(PlaceCommand(_resistor()))
    assert stack.is_modified() is True
    stack.undo()                              # back to the saved position
    assert stack.is_modified() is False
    stack.redo()
    assert stack.is_modified() is True
    stack.undo()
    stack.undo()                              # below the save point
    assert stack.is_modified() is True


def test_save_point_unreachable_after_divergent_edit():
    """Undo past the save point, then push a new command: the saved state lived
    in the now-discarded redo tail, so the document can never read unmodified
    again until the next save."""
    stack = _stack()
    stack.push(PlaceCommand(_resistor()))
    stack.push(PlaceCommand(_resistor()))
    stack.mark_save_point()                   # save point at depth 2
    stack.undo()                              # depth 1
    stack.push(PlaceCommand(_resistor()))     # diverge: redo tail discarded
    assert stack.is_modified() is True
    stack.undo()
    assert stack.is_modified() is True        # depth 1 is NOT the saved state
    stack.undo()
    assert stack.is_modified() is True
    # Re-marking re-baselines.
    stack.mark_save_point()
    assert stack.is_modified() is False


def test_save_point_cleared_history_is_baseline():
    stack = _stack()
    stack.push(PlaceCommand(_resistor()))
    stack.clear()                             # e.g. after File ▸ Open
    assert stack.is_modified() is False
    assert stack.can_undo() is False and stack.can_redo() is False
