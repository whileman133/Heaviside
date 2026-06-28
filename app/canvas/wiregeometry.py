"""
Wire snapping and hit-testing queries.

:class:`WireGeometry` answers read-only spatial questions about the current
schematic — "what pin/vertex/segment is near this point?", "is this vertex
draggable?", "which wire did this click land on?". It holds **no graphics
state**; it reads the live :class:`Schematic` through a getter and relies on the
pure helpers in :mod:`app.canvas.geometry`. This makes the snapping logic
unit-testable without a ``QGraphicsScene`` or a running ``QApplication``.

The owning :class:`~app.canvas.scene.SchematicScene` keeps thin methods that
delegate here, so its event handlers and the test suite call the same names as
before.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QPointF

from app.canvas.geometry import (
    PIN_GRAB_GU,
    PIN_SNAP_GU,
    VERTEX_HIT_GU,
    scene_to_gu,
    snap_gu,
    snap_point_gu,
    wire_proximity_key,
)
from app.schematic.model import (
    Schematic,
    Wire,
    component_connection_points,
    component_pin_positions,
)


class WireGeometry:
    """Spatial queries over a schematic's pins and wires (no Qt state)."""

    def __init__(self, schematic_getter: Callable[[], Schematic]) -> None:
        self._get_schematic = schematic_getter

    @property
    def _schematic(self) -> Schematic:
        return self._get_schematic()

    # -- pin / vertex / segment proximity --------------------------------

    def nearest_pin(self, gu: tuple[float, float]) -> tuple[float, float] | None:
        """Return the nearest named pin within PIN_SNAP_GU of *gu*, else None.

        Named-pins-only (no rect-perimeter connection points), so SELECT-mode
        wire auto-start (:meth:`unconnected_pin_at`) is not triggered by clicking
        a rect's edge — that should select/drag the rect, not start a wire.
        """
        gx, gy = gu
        best: tuple[float, float] | None = None
        best_d2 = PIN_SNAP_GU * PIN_SNAP_GU
        for comp in self._schematic.components:
            for px, py in component_pin_positions(comp):
                d2 = (px - gx) ** 2 + (py - gy) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = (px, py)
        return best

    def nearest_connection_point(
        self, gu: tuple[float, float],
        exclude_component_ids: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> tuple[float, float] | None:
        """Nearest wire-connection point within PIN_SNAP_GU of *gu*, else None.

        Includes named pins *and* rect-perimeter connection points, so a wire
        being drawn snaps to (and reports connectable at) any grid point on a
        block-diagram rectangle's edge.

        *exclude_component_ids* omits those components' pins — used while dragging a
        terminal marker so it does not magnet onto its **own** pin (the model still
        holds it at its pre-drag position during the gesture, which would otherwise
        pin the marker to where it started and make small moves snap back).
        """
        gx, gy = gu
        best: tuple[float, float] | None = None
        best_d2 = PIN_SNAP_GU * PIN_SNAP_GU
        for comp in self._schematic.components:
            if comp.id in exclude_component_ids:
                continue
            for px, py in component_connection_points(comp):
                d2 = (px - gx) ** 2 + (py - gy) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = (px, py)
        return best

    def nearest_wire_vertex(
        self, gu: tuple[float, float], exclude_wire_id: str | None = None
    ) -> tuple[float, float] | None:
        """Nearest existing wire vertex within PIN_SNAP_GU of *gu*, else None."""
        gx, gy = gu
        best: tuple[float, float] | None = None
        best_d2 = PIN_SNAP_GU * PIN_SNAP_GU
        for wire in self._schematic.wires:
            if wire.id == exclude_wire_id:
                continue
            for px, py in wire.points:
                d2 = (px - gx) ** 2 + (py - gy) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = (px, py)
        return best

    def nearest_wire_segment_point(
        self, gu: tuple[float, float], exclude_wire_id: str | None = None
    ) -> tuple[float, float] | None:
        """Nearest point on an existing wire segment, snapped to 0.25 GU.

        Returns the snapped foot of the perpendicular from *gu* onto the closest
        Manhattan segment, but only if it is within PIN_SNAP_GU and lands on the
        segment interior. Lets a wire T into the middle of another wire.
        """
        gx, gy = gu
        best: tuple[float, float] | None = None
        best_d2 = PIN_SNAP_GU * PIN_SNAP_GU
        for wire in self._schematic.wires:
            if wire.id == exclude_wire_id:
                continue
            pts = wire.points
            for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
                # Project onto the (axis-aligned) segment, then snap.
                if x0 == x1:                       # vertical segment
                    fx = x0
                    fy = min(max(gy, min(y0, y1)), max(y0, y1))
                elif y0 == y1:                     # horizontal segment
                    fy = y0
                    fx = min(max(gx, min(x0, x1)), max(x0, x1))
                else:                              # (shouldn't happen: Manhattan)
                    continue
                sx, sy = snap_gu(fx), snap_gu(fy)
                d2 = (sx - gx) ** 2 + (sy - gy) ** 2
                if d2 <= best_d2:
                    best_d2 = d2
                    best = (sx, sy)
        return best

    def wire_snap_target(
        self,
        gu: tuple[float, float],
        exclude_wire_id: str | None = None,
        raw_gu: tuple[float, float] | None = None,
        exclude_component_ids: frozenset[str] | set[str] | tuple[str, ...] = (),
    ) -> tuple[tuple[float, float], bool]:
        """Resolve a wire endpoint for cursor position *gu* (already snapped).

        Snap priority: component pin → existing wire vertex → nearest point on
        an existing wire segment → bare 0.25 GU grid node. Snapping to a pin or
        to existing wire geometry forms a junction (and is treated as a
        "connectable" target). Returns ``(point, is_connectable)`` where the
        flag drives the preview marker (ring vs. plain dot) and termination.

        *exclude_wire_id* omits one wire from the wire-vertex / wire-segment
        snap — used while dragging a vertex so it does not snap to its own wire.

        *raw_gu* is the **unsnapped** cursor position (in GU). When given, the
        component-pin pass measures distance from it, so a pin that sits *off*
        the 0.25-GU grid (a scaled logic gate's terminal) is grabbable — its
        nearest grid node can be more than ``PIN_SNAP_GU`` away. Defaults to
        *gu* (the grid-snapped cursor) for callers without the raw position.
        """
        pin = self.nearest_connection_point(
            raw_gu if raw_gu is not None else gu, exclude_component_ids)
        if pin is not None:
            return pin, True
        vtx = self.nearest_wire_vertex(gu, exclude_wire_id)
        if vtx is not None:
            return vtx, True
        seg = self.nearest_wire_segment_point(gu, exclude_wire_id)
        if seg is not None:
            return seg, True
        return gu, False

    def wire_snap_point(self, scene_pt: QPointF) -> tuple[float, float] | None:
        """Return the nearest wire vertex or segment point if within snap range.

        Like :meth:`wire_snap_target` but ignores component pins and the bare
        grid fallback — returns non-None only when the cursor is genuinely on
        or very close to an existing wire.  Used by the double-click-on-wire
        gesture to locate the start point for a new wire.
        """
        gu = snap_point_gu(scene_pt)
        vtx = self.nearest_wire_vertex(gu)
        if vtx is not None:
            return vtx
        return self.nearest_wire_segment_point(gu)

    # -- pin connectivity -------------------------------------------------

    def wire_endpoint_positions(self) -> set[tuple[float, float]]:
        """All wire endpoint coordinates currently in the schematic."""
        out: set[tuple[float, float]] = set()
        for wire in self._schematic.wires:
            if wire.points:
                out.add(wire.points[0])
                out.add(wire.points[-1])
        return out

    def all_pin_positions(self) -> set[tuple[float, float]]:
        """All wire-connection coordinates (named pins + rect-edge points).

        Used to lock wire endpoints that are owned by component-follow (so a
        rect-edge endpoint is not independently draggable) and to flag
        connectable targets.
        """
        pins: set[tuple[float, float]] = set()
        for comp in self._schematic.components:
            for p in component_connection_points(comp):
                pins.add(p)
        return pins

    def unconnected_pin_at(self, scene_pt: QPointF) -> tuple[float, float] | None:
        """Return the connection point under *scene_pt* only if a wire may be auto-started.

        Used to auto-start a wire when the user clicks a free pin **or a free
        rect-edge / circle-cardinal connection point** in SELECT mode. Returns
        None (so the click falls through to normal selection / component drag)
        when:

        * the cursor is not tightly on a connection point (within ``PIN_GRAB_GU``);
        * the nearest connection point already has a wire endpoint on it.

        The tight grab radius (smaller than the body half-extent) is what keeps
        component / shape dragging intact: a press near the *centre* of a part is
        not on a connection point and falls through to selection/drag, while a
        press right on a free pin/lead-end or perimeter dot starts a wire.
        """
        # Use the raw (unsnapped) cursor so the grab is tight *and* off-grid pins
        # (scaled logic gates) are reachable — their nearest grid node can fall
        # outside PIN_SNAP_GU.
        rx, ry = scene_to_gu(scene_pt)
        pin = self.nearest_connection_point((rx, ry))
        if pin is None:
            return None
        if (pin[0] - rx) ** 2 + (pin[1] - ry) ** 2 > PIN_GRAB_GU * PIN_GRAB_GU:
            return None
        if pin in self.wire_endpoint_positions():
            return None
        return pin

    # -- wire vertex hit-testing -----------------------------------------

    def vertex_is_draggable(
        self, wire: Wire, index: int, pins: set[tuple[float, float]] | None = None
    ) -> bool:
        """Every in-range vertex is draggable — including connected endpoints.

        An endpoint that coincides with a component pin or a drawing-element
        connection point (rect edge / circle cardinal point) is draggable too:
        dragging it **disconnects** it from the pin/edge. Component-follow still
        moves a connected endpoint when the component itself moves (that is
        handled by ``MoveCommand``); draggability only governs *direct*
        manipulation of the vertex. *pins* is accepted for backward
        compatibility but no longer consulted.
        """
        pts = wire.points
        return 0 <= index < len(pts)

    def wire_vertex_at(self, scene_pt: QPointF) -> tuple[str, int] | None:
        """Return the (wire_id, index) of a draggable vertex under *scene_pt*.

        Picks the nearest draggable vertex within VERTEX_HIT_GU; returns None if
        none qualifies. Endpoints on pins are skipped (not draggable).
        """
        gx, gy = scene_to_gu(scene_pt)
        pins = self.all_pin_positions()
        best: tuple[str, int] | None = None
        best_d2 = VERTEX_HIT_GU * VERTEX_HIT_GU
        for wire in self._schematic.wires:
            for i, (px, py) in enumerate(wire.points):
                d2 = (px - gx) ** 2 + (py - gy) ** 2
                if d2 <= best_d2 and self.vertex_is_draggable(wire, i, pins):
                    best_d2 = d2
                    best = (wire.id, i)
        return best

    def click_select_wire_id(self, scene_pt: QPointF, grabbed_id: str) -> str:
        """Wire to select for a click that grabbed a vertex of *grabbed_id*.

        Returns the wire the cursor is actually on — the closest segment within
        VERTEX_HIT_GU, preferring a pass-through over an endpoint-touch. On a
        true tie the grabbed wire wins, so a click where two wires overlap stays
        on the grabbed one. Falls back to *grabbed_id* if nothing is in range.
        """
        gx, gy = scene_to_gu(scene_pt)
        bound2 = VERTEX_HIT_GU * VERTEX_HIT_GU
        best_id: str | None = None
        best_key: tuple[float, int] | None = None
        for wire in self._schematic.wires:
            key = wire_proximity_key(gx, gy, wire.points)
            if key is None or key[0] > bound2:
                continue
            if best_key is None or key < best_key:
                best_key, best_id = key, wire.id
        if best_id is None:
            return grabbed_id
        grabbed = next(
            (w for w in self._schematic.wires if w.id == grabbed_id), None
        )
        if grabbed is not None:
            gk = wire_proximity_key(gx, gy, grabbed.points)
            if gk is not None and gk == best_key:
                return grabbed_id
        return best_id
