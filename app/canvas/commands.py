"""
Undo/redo command stack (spec §6.6).

This module implements the Command pattern over the :class:`Schematic` model.
It is intentionally **Qt-free**: every command mutates a plain ``Schematic``
dataclass and nothing else. This keeps the command layer fully unit-testable
in a headless environment (no display server, no PySide6 import required) and
lets the canvas scene wrap an :class:`UndoStack` without leaking Qt types into
the model.

Command set and inverses (spec §6.6):

    | Command          | Inverse                                          |
    | ---------------- | ------------------------------------------------ |
    | PlaceCommand     | Remove component                                 |
    | DeleteCommand    | Restore component(s) and connected wires         |
    | MoveCommand      | Move back to original position                   |
    | WireCommand      | Remove wire                                      |
    | SplitWireCommand | Restore original wire (remove two halves)        |
    | MergeWireCommand | Split merged wire back into two originals        |
    | EditCommand            | Restore previous options string                  |
    | MoveOptionsLabelCommand| Restore previous label_offset                    |
    | MacroCommand           | Composite of the above (undone in reverse order) |

Each command exposes ``do(schematic)`` and ``undo(schematic)``. A command must
be idempotent with respect to repeated do/undo cycles: ``do`` then ``undo``
must return the schematic to a state equal to the one before ``do`` ran, and a
subsequent ``do`` must reproduce the post-``do`` state exactly. Commands store
deep copies of any mutable state they capture so that later edits to the live
model cannot corrupt the undo history.
"""

from __future__ import annotations

import copy
import uuid
from abc import ABC, abstractmethod

from app.schematic.model import (
    Component,
    Schematic,
    Wire,
    component_pin_positions as _component_pin_positions,
    route,
    simplify_points,
)

__all__ = [
    "Command",
    "PlaceCommand",
    "DeleteCommand",
    "MoveCommand",
    "MoveWireVertexCommand",
    "SplitWireCommand",
    "MergeWireCommand",
    "WireCommand",
    "EditCommand",
    "MoveOptionsLabelCommand",
    "RotateCommand",
    "MirrorCommand",
    "MacroCommand",
    "UndoStack",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_component(schematic: Schematic, comp_id: str) -> Component:
    """Return the live component with *comp_id* or raise KeyError."""
    for comp in schematic.components:
        if comp.id == comp_id:
            return comp
    raise KeyError(f"no component with id {comp_id!r} in schematic")


def _wire_touches_position(wire: Wire, pos: tuple[float, float]) -> bool:
    """Return True if either endpoint of *wire* lies exactly on *pos*.

    Connectivity in the v1 model is purely geometric: a wire is "connected" to
    a component if one of the wire's two endpoints coincides with a pin
    coordinate of that component. Deleting the component therefore also deletes
    any wire whose start or end touches one of its pins.
    """
    if not wire.points:
        return False
    return wire.points[0] == pos or wire.points[-1] == pos



def _seg_elbow(
    moved: tuple[float, float], neighbour: tuple[float, float]
) -> tuple[float, float] | None:
    """Elbow vertex between *moved* and *neighbour*, or None if already
    axis-aligned. Keeps the path Manhattan after an endpoint shift.

    Thin wrapper over the shared :func:`route` primitive (spec §6.4): the elbow
    is the vertical-first corner ``(moved.x, neighbour.y)`` — i.e. vertical from
    the moved endpoint, then horizontal into the neighbour. ``route`` returns
    ``[moved, neighbour]`` (no corner) when the two are already axis-aligned, so
    slicing the middle yields ``None`` here.
    """
    mid = route(moved, neighbour, vfirst=True)[1:-1]
    return mid[0] if mid else None


def reshape_wire_points(
    points: list[tuple[float, float]],
    *,
    start_hit: bool,
    end_hit: bool,
    dx: float,
    dy: float,
    simplify: bool = True,
) -> list[tuple[float, float]]:
    """Return the points of a wire after a connected component moves by (dx,dy).

    *start_hit* / *end_hit* say whether the wire's first / last vertex is
    attached to a moving component's pin. Behaviour mirrors :class:`MoveCommand`
    exactly (it is the shared implementation):

    * both ends attached → rigid translation of the whole polyline;
    * one end attached   → that endpoint shifts, with an auto-elbow inserted on
      the adjacent segment if it would otherwise go diagonal.

    When *simplify* is True the result is run through :func:`simplify_points` to
    drop redundant collinear vertices. The live drag preview passes
    ``simplify=False`` for smoother intermediate frames; the committed command
    simplifies.
    """
    pts = list(points)
    if len(pts) < 2 or not (start_hit or end_hit):
        return pts

    if start_hit and end_hit:
        pts = [(x + dx, y + dy) for (x, y) in pts]
    else:
        if start_hit:
            p, nb = pts[0], pts[1]
            new_p = (p[0] + dx, p[1] + dy)
            elbow = _seg_elbow(new_p, nb)
            head = [new_p] + ([elbow] if elbow is not None else [])
            pts = head + pts[1:]
        else:  # end_hit
            p, nb = pts[-1], pts[-2]
            new_p = (p[0] + dx, p[1] + dy)
            elbow = _seg_elbow(new_p, nb)
            tail = ([elbow] if elbow is not None else []) + [new_p]
            pts = pts[:-1] + tail

    return simplify_points(pts) if simplify else pts


# ---------------------------------------------------------------------------
# Command base class
# ---------------------------------------------------------------------------

class Command(ABC):
    """Abstract base for all undoable operations.

    Subclasses implement :meth:`do` and :meth:`undo`, each taking the live
    :class:`Schematic` and mutating it in place. A command instance is bound to
    the specific objects it was constructed with; it is not reusable across
    different schematics.
    """

    #: Short human-readable label, e.g. for an Edit menu ("Undo Place").
    label: str = "Command"

    @abstractmethod
    def do(self, schematic: Schematic) -> None:
        """Apply this command's effect to *schematic*."""

    @abstractmethod
    def undo(self, schematic: Schematic) -> None:
        """Reverse this command's effect on *schematic*."""

    def redo(self, schematic: Schematic) -> None:
        """Re-apply this command. Defaults to :meth:`do`."""
        self.do(schematic)


# ---------------------------------------------------------------------------
# Concrete commands
# ---------------------------------------------------------------------------

class PlaceCommand(Command):
    """Place a single component onto the schematic.

    Inverse: remove the placed component. A deep copy of the component is
    stored so the inserted instance is independent of any caller-held
    reference.
    """

    label = "Place"

    def __init__(self, component: Component) -> None:
        self._component = copy.deepcopy(component)

    @property
    def component_id(self) -> str:
        return self._component.id

    def do(self, schematic: Schematic) -> None:
        # Insert a fresh copy so the live model and our stored template never
        # alias the same object.
        schematic.components.append(copy.deepcopy(self._component))

    def undo(self, schematic: Schematic) -> None:
        schematic.components[:] = [
            c for c in schematic.components if c.id != self._component.id
        ]


class DeleteCommand(Command):
    """Delete components and wires.

    Removes:
      * every component in *component_ids*,
      * every wire in *wire_ids* (directly selected wires), and
      * every wire connected to a deleted component's pins.

    Inverse: restore the deleted components and wires (spec §6.6, §6.3). The
    full removed set is captured at :meth:`do` time so that ``undo`` restores
    exactly what was removed, at its original index.
    """

    label = "Delete"

    def __init__(
        self,
        component_ids: list[str],
        wire_ids: list[str] | None = None,
    ) -> None:
        self._component_ids = list(component_ids)
        self._explicit_wire_ids = list(wire_ids or [])
        # Filled in on do(); deep copies of everything removed.
        self._removed_components: list[Component] = []
        self._removed_wires: list[Wire] = []
        # Remember original ordering positions so undo restores order.
        self._component_index: dict[str, int] = {}
        self._wire_index: dict[str, int] = {}

    def do(self, schematic: Schematic) -> None:
        target_ids = set(self._component_ids)
        explicit_wire_ids = set(self._explicit_wire_ids)

        # Collect the pin positions of every targeted component so we can find
        # the wires that touch them.
        connected_positions: set[tuple[float, float]] = set()
        for comp in schematic.components:
            if comp.id in target_ids:
                for pos in _component_pin_positions(comp):
                    connected_positions.add(pos)

        # Snapshot removed components (preserving original index for undo).
        self._removed_components = []
        self._component_index = {}
        for idx, comp in enumerate(schematic.components):
            if comp.id in target_ids:
                self._component_index[comp.id] = idx
                self._removed_components.append(copy.deepcopy(comp))

        # Snapshot removed wires: those explicitly selected, plus any wire with
        # an endpoint on a connected pin.
        self._removed_wires = []
        self._wire_index = {}
        for idx, wire in enumerate(schematic.wires):
            connected = any(
                _wire_touches_position(wire, p) for p in connected_positions
            )
            if wire.id in explicit_wire_ids or connected:
                self._wire_index[wire.id] = idx
                self._removed_wires.append(copy.deepcopy(wire))

        # Apply removals.
        removed_wire_ids = {w.id for w in self._removed_wires}
        schematic.components[:] = [
            c for c in schematic.components if c.id not in target_ids
        ]
        schematic.wires[:] = [
            w for w in schematic.wires if w.id not in removed_wire_ids
        ]

    def undo(self, schematic: Schematic) -> None:
        # Restore components at their original indices (ascending order so that
        # earlier insertions don't shift later target indices).
        for comp in sorted(
            self._removed_components,
            key=lambda c: self._component_index[c.id],
        ):
            idx = self._component_index[comp.id]
            idx = min(idx, len(schematic.components))
            schematic.components.insert(idx, copy.deepcopy(comp))

        for wire in sorted(
            self._removed_wires,
            key=lambda w: self._wire_index[w.id],
        ):
            idx = self._wire_index[wire.id]
            idx = min(idx, len(schematic.wires))
            schematic.wires.insert(idx, copy.deepcopy(wire))


class MoveCommand(Command):
    """Move one or more components by a fixed delta in grid units.

    Connected wire endpoints follow the components they touch: any wire vertex
    that coincides with a moving component's pin (geometric connectivity, the
    same rule :class:`DeleteCommand` uses) is shifted by the same delta. If a
    shifted endpoint would make its adjacent segment diagonal, an auto-elbow
    vertex is inserted so the wire stays Manhattan-legal (spec invariant 4).

    Inverse: restore each affected wire's exact original point list and move the
    components back. Operates by component id so it stays valid even if the
    component list is rebuilt between do/undo cycles.
    """

    label = "Move"

    def __init__(
        self,
        component_ids: list[str],
        delta: tuple[float, float],
        wire_ids: list[str] | None = None,
    ) -> None:
        self._component_ids = list(component_ids)
        self._dx, self._dy = delta
        # Wires that are explicitly selected for rigid translation (in addition
        # to the wires that follow via pin connectivity).
        self._explicit_wire_ids: frozenset[str] = frozenset(wire_ids or [])
        # wire id -> original points, captured at first do() for exact undo.
        self._orig_wire_points: dict[str, list[tuple[float, float]]] = {}
        # wire ids that were removed because they collapsed; restored on undo.
        self._removed_wire_ids: set[str] = set()

    # -- component motion -------------------------------------------------

    def _shift_components(self, schematic: Schematic, sign: float) -> None:
        ids = set(self._component_ids)
        for comp in schematic.components:
            if comp.id in ids:
                x, y = comp.position
                comp.position = (x + sign * self._dx, y + sign * self._dy)

    # -- connectivity -----------------------------------------------------

    def _connected_pin_set(self, schematic: Schematic) -> set[tuple[float, float]]:
        """Absolute pin coordinates of the moving components, BEFORE the move."""
        ids = set(self._component_ids)
        pins: set[tuple[float, float]] = set()
        for comp in schematic.components:
            if comp.id in ids:
                for p in _component_pin_positions(comp):
                    pins.add(p)
        return pins

    # -- wire reshaping ---------------------------------------------------

    def _reshape_wires(self, schematic: Schematic) -> None:
        """Drag connected endpoints by the delta, inserting elbows as needed.

        When every component in the schematic is being moved (select-all drag)
        every wire translates rigidly so free endpoints (open-circle nodes)
        move with the rest of the circuit.  Otherwise only endpoints that sit on
        a moving pin are shifted; free endpoints stay anchored.

        Wires that collapse to a single point (both endpoints moved to the same
        coordinate) are removed from the schematic. Their original points are
        still captured so undo can restore them.
        """
        all_dragged = (
            set(self._component_ids) >= {c.id for c in schematic.components}
        )
        pins = self._connected_pin_set(schematic)
        if not pins and not all_dragged and not self._explicit_wire_ids:
            return
        to_remove: list[str] = []
        for wire in schematic.wires:
            pts = wire.points
            if len(pts) < 2:
                continue

            if all_dragged or wire.id in self._explicit_wire_ids:
                start_hit = end_hit = True
            else:
                start_hit = pts[0] in pins
                end_hit = pts[-1] in pins
                if not start_hit and not end_hit:
                    continue

            # Capture the pristine path once, for undo.
            if wire.id not in self._orig_wire_points:
                self._orig_wire_points[wire.id] = list(wire.points)

            new_pts = reshape_wire_points(
                pts,
                start_hit=start_hit,
                end_hit=end_hit,
                dx=self._dx,
                dy=self._dy,
            )
            if len(new_pts) < 2:
                # Wire collapsed to a point — remove it.
                to_remove.append(wire.id)
            else:
                wire.points = new_pts

        if to_remove:
            self._removed_wire_ids.update(to_remove)
            schematic.wires[:] = [
                w for w in schematic.wires if w.id not in to_remove
            ]

    # -- Command API ------------------------------------------------------

    def do(self, schematic: Schematic) -> None:
        # Reshape wires using pin positions BEFORE moving the components.
        self._reshape_wires(schematic)
        self._shift_components(schematic, +1.0)

    def undo(self, schematic: Schematic) -> None:
        self._shift_components(schematic, -1.0)
        # Restore each affected wire's exact original geometry.
        for wire in schematic.wires:
            orig = self._orig_wire_points.get(wire.id)
            if orig is not None:
                wire.points = list(orig)
        # Re-add any wires that were removed because they collapsed.
        existing_ids = {w.id for w in schematic.wires}
        for wid in self._removed_wire_ids:
            orig = self._orig_wire_points.get(wid)
            if orig is not None and wid not in existing_ids:
                schematic.wires.append(Wire(id=wid, points=list(orig)))

    def redo(self, schematic: Schematic) -> None:
        # On redo the original points are already captured; reapply directly so
        # we don't recapture an already-reshaped path.
        self._reshape_wires(schematic)
        self._shift_components(schematic, +1.0)


class WireCommand(Command):
    """Add a single wire to the schematic.

    Inverse: remove the wire by id.
    """

    label = "Wire"

    def __init__(self, wire: Wire) -> None:
        self._wire = copy.deepcopy(wire)

    @property
    def wire_id(self) -> str:
        return self._wire.id

    def do(self, schematic: Schematic) -> None:
        schematic.wires.append(copy.deepcopy(self._wire))

    def undo(self, schematic: Schematic) -> None:
        schematic.wires[:] = [
            w for w in schematic.wires if w.id != self._wire.id
        ]


class SplitWireCommand(Command):
    """Split an existing wire into two at a mid-segment point.

    Used when a new wire connects to the middle of an existing wire's segment:
    the existing wire is replaced by two new wires that meet at the connection
    point, so each half is independently selectable and deletable.  Pairs with
    a :class:`WireCommand` inside a :class:`MacroCommand` so the split + add is
    one undoable action.

    Inverse: remove the two halves and restore the original wire.
    """

    label = "Split wire"

    def __init__(
        self,
        wire_id: str,
        index: int,
        point: tuple[float, float],
        new_id1: str | None = None,
        new_id2: str | None = None,
    ) -> None:
        self._wire_id = wire_id
        self._index = index
        self._point = point
        self._new_id1 = new_id1 or str(uuid.uuid4())
        self._new_id2 = new_id2 or str(uuid.uuid4())
        self._orig_points: list[tuple[float, float]] | None = None
        self._orig_index: int | None = None   # position in schematic.wires

    def _find(self, schematic: Schematic, wire_id: str) -> tuple[int, Wire] | None:
        for i, w in enumerate(schematic.wires):
            if w.id == wire_id:
                return i, w
        return None

    def do(self, schematic: Schematic) -> None:
        result = self._find(schematic, self._wire_id)
        if result is None:
            return
        pos, wire = result
        if self._orig_points is None:
            self._orig_points = list(wire.points)
            self._orig_index = pos
        pts = list(self._orig_points)
        idx = max(0, min(self._index, len(pts)))
        if 0 < idx < len(pts) and pts[idx] == self._point:
            # Point is already the intermediate vertex at idx (corner split):
            # split without inserting a duplicate.
            split_pts = pts
        elif self._point in pts:
            # Point coincides with an endpoint — nothing to split.
            return
        else:
            # Normal mid-segment case: insert the new vertex.
            split_pts = pts[:idx] + [self._point] + pts[idx:]
        half1 = Wire(id=self._new_id1, points=split_pts[:idx + 1])
        half2 = Wire(id=self._new_id2, points=split_pts[idx:])
        schematic.wires[pos:pos + 1] = [half1, half2]

    def undo(self, schematic: Schematic) -> None:
        if self._orig_points is None:
            return
        # Remove both halves (they may be anywhere in the list now).
        new_ids = {self._new_id1, self._new_id2}
        pos = next(
            (i for i, w in enumerate(schematic.wires) if w.id in new_ids),
            None,
        )
        schematic.wires[:] = [w for w in schematic.wires if w.id not in new_ids]
        orig = Wire(id=self._wire_id, points=list(self._orig_points))
        insert_at = pos if pos is not None else self._orig_index or 0
        insert_at = min(insert_at, len(schematic.wires))
        schematic.wires.insert(insert_at, orig)


class MergeWireCommand(Command):
    """Merge two wires that share a free endpoint into one wire.

    Used when deleting a wire dissolves a T-junction, leaving two wire stubs
    whose shared endpoint has degree 2 (no component pin, no third wire).
    Bundled after a :class:`DeleteCommand` inside a :class:`MacroCommand` so
    the delete + merge is one undoable action.

    Inverse: split the merged wire back into the two originals.
    """

    label = "Merge wires"

    def __init__(
        self,
        wire_id1: str,
        wire_id2: str,
        merge_point: tuple[float, float],
        new_id: str | None = None,
    ) -> None:
        self._wire_id1 = wire_id1
        self._wire_id2 = wire_id2
        self._merge_point = merge_point
        self._new_id = new_id or str(uuid.uuid4())
        self._orig_pts1: list[tuple[float, float]] | None = None
        self._orig_pts2: list[tuple[float, float]] | None = None
        self._orig_index: int | None = None

    def _find(self, schematic: Schematic, wire_id: str) -> tuple[int, Wire] | None:
        for i, w in enumerate(schematic.wires):
            if w.id == wire_id:
                return i, w
        return None

    def do(self, schematic: Schematic) -> None:
        r1 = self._find(schematic, self._wire_id1)
        r2 = self._find(schematic, self._wire_id2)
        if r1 is None or r2 is None:
            return
        pos1, w1 = r1
        _,   w2 = r2
        if self._orig_pts1 is None:
            self._orig_pts1 = list(w1.points)
            self._orig_pts2 = list(w2.points)
            self._orig_index = pos1
        p = self._merge_point
        # Orient w1 so that p is its last point.
        pts1 = list(w1.points) if w1.points[-1] == p else list(reversed(w1.points))
        # Orient w2 so that p is its first point.
        pts2 = list(w2.points) if w2.points[0] == p else list(reversed(w2.points))
        merged_pts = simplify_points(pts1 + pts2[1:])
        merged = Wire(id=self._new_id, points=merged_pts)
        # Remove both originals and insert the merged wire where w1 was.
        old_ids = {self._wire_id1, self._wire_id2}
        schematic.wires[:] = [w for w in schematic.wires if w.id not in old_ids]
        insert_at = min(pos1, len(schematic.wires))
        schematic.wires.insert(insert_at, merged)

    def undo(self, schematic: Schematic) -> None:
        if self._orig_pts1 is None:
            return
        result = self._find(schematic, self._new_id)
        pos = result[0] if result is not None else (self._orig_index or 0)
        schematic.wires[:] = [w for w in schematic.wires if w.id != self._new_id]
        w1 = Wire(id=self._wire_id1, points=list(self._orig_pts1))
        w2 = Wire(id=self._wire_id2, points=list(self._orig_pts2))
        insert_at = min(pos, len(schematic.wires))
        schematic.wires.insert(insert_at, w1)
        schematic.wires.insert(insert_at + 1, w2)

    def redo(self, schematic: Schematic) -> None:
        self.do(schematic)


class MoveWireVertexCommand(Command):
    """Move a single vertex of a wire to a new position.

    The dragged vertex goes exactly where it is dropped; each adjacent segment
    that would become diagonal gets an auto-elbow inserted so the path stays
    Manhattan (spec invariant 4). The result is then run through
    :func:`simplify_points` to drop any vertices that became redundant.

    Endpoints that sit on a component pin are *not* moved by this command — the
    scene refuses to start a vertex drag there (those endpoints are owned by
    wire-following). This command assumes the target vertex is draggable.

    Inverse: restore the wire's exact original point list.
    """

    label = "Move node"

    def __init__(
        self,
        wire_id: str,
        index: int,
        new_point: tuple[float, float],
    ) -> None:
        self._wire_id = wire_id
        self._index = index
        self._new_point = new_point
        self._orig_points: list[tuple[float, float]] | None = None

    def _find_wire(self, schematic: Schematic) -> Wire | None:
        for w in schematic.wires:
            if w.id == self._wire_id:
                return w
        return None

    @staticmethod
    def _seg_elbow(
        a: tuple[float, float], b: tuple[float, float]
    ) -> tuple[float, float] | None:
        """Elbow vertex making a–b Manhattan, or None if already axis-aligned.

        Thin wrapper over the shared :func:`route` primitive (spec §6.4): the
        horizontal-first corner ``(b.x, a.y)`` — horizontal from *a*, then
        vertical into *b*. ``route`` yields no corner when a–b are already
        axis-aligned, so the sliced middle is empty → None.
        """
        mid = route(a, b, vfirst=False)[1:-1]
        return mid[0] if mid else None

    def do(self, schematic: Schematic) -> None:
        wire = self._find_wire(schematic)
        if wire is None:
            return
        if self._orig_points is None:
            self._orig_points = list(wire.points)

        pts = list(self._orig_points)
        i = self._index
        if not (0 <= i < len(pts)):
            return
        pts[i] = self._new_point

        # Rebuild around the moved vertex, inserting elbows where a neighbouring
        # segment turned diagonal. Build left→right so indices stay coherent.
        rebuilt: list[tuple[float, float]] = []
        for j, p in enumerate(pts):
            if j == 0:
                rebuilt.append(p)
                continue
            prev = pts[j - 1]
            # If either endpoint of this segment is the moved vertex, the
            # segment may need an elbow.
            if j == i or j - 1 == i:
                elbow = self._seg_elbow(prev, p)
                if elbow is not None:
                    rebuilt.append(elbow)
            rebuilt.append(p)

        wire.points = simplify_points(rebuilt)

    def undo(self, schematic: Schematic) -> None:
        wire = self._find_wire(schematic)
        if wire is not None and self._orig_points is not None:
            wire.points = list(self._orig_points)

    def redo(self, schematic: Schematic) -> None:
        self.do(schematic)


class EditCommand(Command):
    """Replace the options string of a single component.

    Inverse: restore the previous options string.
    """

    label = "Edit"

    def __init__(
        self,
        component_id: str,
        new_options: str,
        old_options: str | None = None,
    ) -> None:
        self._component_id = component_id
        self._new_options = new_options
        # If old_options is not supplied, it is captured on first do().
        self._old_options: str | None = old_options

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_options is None:
            self._old_options = comp.options
        comp.options = self._new_options

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.options = self._old_options or ""


class MoveOptionsLabelCommand(Command):
    """Set (or clear) the label_offset of a single component's options label.

    Inverse: restore the previous label_offset value.
    """

    label = "Move Label"

    # Sentinel distinguishing "old value not yet captured" from explicit None.
    _UNSET: tuple[()] = ()

    def __init__(
        self,
        component_id: str,
        new_offset: tuple[float, float] | None,
    ) -> None:
        self._component_id = component_id
        self._new_offset = new_offset
        self._old_offset: object = self._UNSET

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_offset is self._UNSET:
            self._old_offset = comp.label_offset
        comp.label_offset = self._new_offset

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.label_offset = self._old_offset  # type: ignore[assignment]


class RotateCommand(Command):
    """Change the rotation of a single component by a multiple of 90°.

    Inverse: restore the previous rotation value.
    """

    label = "Rotate"

    def __init__(self, component_id: str, new_rotation: int, old_rotation: int | None = None) -> None:
        if new_rotation not in (0, 90, 180, 270):
            raise ValueError(f"Invalid rotation {new_rotation!r}")
        self._component_id = component_id
        self._new_rotation = new_rotation
        self._old_rotation: int | None = old_rotation

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_rotation is None:
            self._old_rotation = comp.rotation
        comp.rotation = self._new_rotation

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.rotation = self._old_rotation if self._old_rotation is not None else 0


class MirrorCommand(Command):
    """Toggle (or explicitly set) the horizontal mirror state of a component.

    Inverse: restore the previous mirror value.
    """

    label = "Mirror"

    def __init__(self, component_id: str, new_mirror: bool, old_mirror: bool | None = None) -> None:
        self._component_id = component_id
        self._new_mirror = new_mirror
        self._old_mirror: bool | None = old_mirror

    def do(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        if self._old_mirror is None:
            self._old_mirror = comp.mirror
        comp.mirror = self._new_mirror

    def undo(self, schematic: Schematic) -> None:
        comp = _find_component(schematic, self._component_id)
        comp.mirror = self._old_mirror if self._old_mirror is not None else False


class MacroCommand(Command):
    """A composite command applied as a single undoable unit.

    ``do`` runs children in order; ``undo`` runs them in reverse order so that
    inverse effects unwind correctly (spec §6.6).
    """

    label = "Macro"

    def __init__(self, commands: list[Command], label: str | None = None) -> None:
        self._commands = list(commands)
        if label is not None:
            self.label = label

    @property
    def commands(self) -> list[Command]:
        return list(self._commands)

    def do(self, schematic: Schematic) -> None:
        for cmd in self._commands:
            cmd.do(schematic)

    def undo(self, schematic: Schematic) -> None:
        for cmd in reversed(self._commands):
            cmd.undo(schematic)


# ---------------------------------------------------------------------------
# Undo stack
# ---------------------------------------------------------------------------

class UndoStack:
    """A bounded-history undo/redo stack bound to one :class:`Schematic`.

    The stack owns the document it mutates. :meth:`push` applies a command and
    records it; :meth:`undo` / :meth:`redo` walk the history. Pushing a new
    command after one or more undos discards the redo tail, matching standard
    editor semantics. The stack is per-session and is never serialized (spec
    §6.6).
    """

    def __init__(self, schematic: Schematic) -> None:
        self._schematic = schematic
        self._undo: list[Command] = []
        self._redo: list[Command] = []

    # -- properties --------------------------------------------------------

    @property
    def schematic(self) -> Schematic:
        return self._schematic

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_count(self) -> int:
        return len(self._undo)

    @property
    def redo_count(self) -> int:
        return len(self._redo)

    def undo_label(self) -> str | None:
        return self._undo[-1].label if self._undo else None

    def redo_label(self) -> str | None:
        return self._redo[-1].label if self._redo else None

    # -- operations --------------------------------------------------------

    def push(self, command: Command) -> None:
        """Apply *command* to the document and record it for undo.

        Clears the redo history (a new action invalidates redone-away future).
        """
        command.do(self._schematic)
        self._undo.append(command)
        self._redo.clear()

    def undo(self) -> Command | None:
        """Undo the most recent command. Returns it, or None if empty."""
        if not self._undo:
            return None
        command = self._undo.pop()
        command.undo(self._schematic)
        self._redo.append(command)
        return command

    def redo(self) -> Command | None:
        """Redo the most recently undone command. Returns it, or None."""
        if not self._redo:
            return None
        command = self._redo.pop()
        command.redo(self._schematic)
        self._undo.append(command)
        return command

    def clear(self) -> None:
        """Drop all undo and redo history (e.g. after New / Open)."""
        self._undo.clear()
        self._redo.clear()
