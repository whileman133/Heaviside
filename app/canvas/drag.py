"""
Drag and live-preview controller for the schematic canvas.

:class:`DragPreviewController` owns the transient state of the three canvas drag
gestures — component move, wire-vertex drag, and resizable-component endpoint
drag — and renders their *live previews* (ghosted wires, open-circle and
junction dots) without touching the model. The model is mutated only when a
gesture commits, via the undo stack.

It is a collaborator of :class:`~app.canvas.scene.SchematicScene`: it holds a
back-reference to the scene and reads/writes the scene's graphics-item maps
through it (the preview is inherently about Qt items, so it cannot be fully
decoupled). The scene's mouse-event handlers stay in the scene and drive this
controller: they set the ``*_drag`` state on press, call the ``preview_*``
methods on move, and call the ``commit_*`` methods on release.

State (read by the scene's event handlers and a few tests):

* ``drag_start``        – {component_id: position_at_drag_start} for a move
* ``drag_wire_ids``     – wire ids selected when a move began
* ``vertex_drag``       – (wire_id, index, original_point) or None
* ``vertex_press_gu``   – snapped cursor where a vertex grab began
* ``endpoint_drag``     – (comp_id, handle_index, old_span) or None
* ``endpoint_press_gu`` – snapped cursor where an endpoint grab began
* ``previewed_wire_ids``– wire ids currently showing a drag-preview path
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import QPointF

from app.canvas.commands import (
    MacroCommand,
    MoveCommand,
    ResizeCommand,
    reshape_wire_points,
)
from app.canvas.geometry import (
    gu_to_scene,
    local_span_to_world,
    scene_to_gu,
    snap_point_gu,
    world_delta_to_local,
)
from app.canvas.items import (
    JunctionItem,
    OpenCircleItem,
    _ResizableTwoTerminalItem,
)
from app.schematic.model import (
    component_pin_positions,
    route,
    simplify_points,
)

if TYPE_CHECKING:
    from app.canvas.scene import SchematicScene


def _round_pt(pt: tuple[float, float]) -> tuple[float, float]:
    """Round a coordinate to 6 dp for stable dict/set keys (float noise guard)."""
    return (round(pt[0], 6), round(pt[1], 6))


class DragPreviewController:
    """Owns drag state and renders live drag previews for a SchematicScene."""

    def __init__(self, scene: "SchematicScene") -> None:
        self._scene = scene

        # Drag-move bookkeeping: id -> position at drag start (GU).
        self.drag_start: dict[str, tuple[float, float]] = {}
        # Wire IDs selected at drag-start, captured before super() may deselect them.
        self.drag_wire_ids: set[str] = set()

        # Wire-vertex drag: (wire_id, index, original_point_gu) or None.
        self.vertex_drag: tuple[str, int, tuple[float, float]] | None = None
        # Snapped cursor position where the vertex grab began, to tell a click
        # (no grid movement → select) from a real drag (→ move the vertex).
        self.vertex_press_gu: tuple[float, float] | None = None

        # Endpoint drag for resizable components: (comp_id, handle_index, old_span).
        # handle_index: 0 = origin handle (moves component), 1 = terminal handle.
        self.endpoint_drag: tuple[str, int, tuple[float, float]] | None = None
        self.endpoint_press_gu: tuple[float, float] | None = None

        # Wire ids currently showing a drag-preview (during a component drag),
        # so they can be cleared precisely on release.
        self.previewed_wire_ids: set[str] = set()

    # ------------------------------------------------------------------
    # Gesture status
    # ------------------------------------------------------------------

    def any_active(self) -> bool:
        """True while a component / vertex / endpoint drag is in progress."""
        return (
            bool(self.drag_start)
            or self.vertex_drag is not None
            or self.endpoint_drag is not None
        )

    def reset_preview_tracking(self) -> None:
        """Forget which wires were ghosted (a rebuild supersedes any in-flight ghost)."""
        self.previewed_wire_ids = set()

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _static_pin_positions(self) -> set[tuple[float, float]]:
        """Rounded pin coordinates of components NOT currently being dragged.

        Dragged components are excluded because their model positions are stale
        mid-drag; callers supply the live (dragged) pins via ``extra_pin_positions``.
        """
        dragged = set(self.drag_start.keys())
        return {
            _round_pt(p)
            for comp in self._scene._schematic.components
            if comp.id not in dragged
            for p in component_pin_positions(comp)
        }

    def _endpoint_local_delta(
        self, comp_id: str, gu: tuple[float, float]
    ) -> tuple[_ResizableTwoTerminalItem, object, float, float] | None:
        """Resolve an endpoint-drag target to ``(item, comp, dx, dy)`` or None.

        ``dx, dy`` is the raw (un-snapped) drag delta mapped into the
        component's local span axes. Returns None when the component is not a
        resizable two-terminal item. Shared by the preview and commit paths.
        """
        item = self._scene._comp_items.get(comp_id)
        if not isinstance(item, _ResizableTwoTerminalItem):
            return None
        comp = item.component
        ox, oy = comp.position
        dx, dy = world_delta_to_local(gu[0] - ox, gu[1] - oy, comp.rotation)
        return item, comp, dx, dy

    # ------------------------------------------------------------------
    # Endpoint drag helpers (resizable components)
    # ------------------------------------------------------------------

    def endpoint_handle_at(self, scene_pos: QPointF) -> str | None:
        """Return comp_id if *scene_pos* is over the terminal resize handle of any
        resizable component, regardless of selection state.  Checks selected
        items first."""
        scene = self._scene
        selected = scene.selectedItems()
        candidates = list(selected) + [
            item for item in scene._comp_items.values()
            if isinstance(item, _ResizableTwoTerminalItem) and item not in selected
        ]
        for item in candidates:
            if not isinstance(item, _ResizableTwoTerminalItem):
                continue
            local = item.mapFromScene(scene_pos)
            if item.terminal_handle_hit(local):
                return item.component.id
        return None

    def preview_endpoint_drag(self, gu: tuple[float, float]) -> None:
        """Live visual update while dragging the terminal endpoint (model untouched)."""
        if self.endpoint_drag is None:
            return
        comp_id, _handle_idx, old_span = self.endpoint_drag
        resolved = self._endpoint_local_delta(comp_id, gu)
        if resolved is None:
            return
        item, comp, dx, dy = resolved
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return

        # Compute old terminal world position (before preview span is applied).
        ox, oy = comp.position
        old_rx, old_ry = local_span_to_world(old_span, comp.rotation, comp.mirror)
        old_pin = (ox + old_rx, oy + old_ry)

        item.set_preview_span((dx, dy))

        # Compute new terminal world position from the snapped drag target.
        new_pin = gu

        pin_dx = new_pin[0] - old_pin[0]
        pin_dy = new_pin[1] - old_pin[1]
        if pin_dx == 0.0 and pin_dy == 0.0:
            return
        for wire in self._scene._schematic.wires:
            pts = wire.points
            if len(pts) < 2:
                continue
            start_hit = pts[0] == old_pin
            end_hit = pts[-1] == old_pin
            if not start_hit and not end_hit:
                continue
            new_pts = reshape_wire_points(
                pts, start_hit=start_hit, end_hit=end_hit,
                dx=pin_dx, dy=pin_dy,
            )
            wire_item = self._scene._wire_items.get(wire.id)
            if wire_item is not None and len(new_pts) >= 2:
                wire_item.set_preview_points(new_pts)

    def commit_endpoint_drag(
        self,
        comp_id: str,
        old_span: tuple[float, float],
        gu: tuple[float, float],
    ) -> None:
        """Commit a ResizeCommand for the dragged terminal endpoint."""
        resolved = self._endpoint_local_delta(comp_id, gu)
        if resolved is None:
            return
        item, _comp, dx, dy = resolved
        dx = round(dx * 2) / 2
        dy = round(dy * 2) / 2
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            item.set_preview_span(old_span)
            return
        new_span = (dx, dy)
        if new_span == old_span:
            return
        cmd = ResizeCommand(comp_id, new_span, old_span)
        self._scene._stack.push(cmd)
        self._scene._rebuild_items()
        self._scene.schematic_changed.emit()

    # ------------------------------------------------------------------
    # Wire-vertex drag
    # ------------------------------------------------------------------

    def preview_vertex_drag(self, gu: tuple[float, float]) -> None:
        """Live visual feedback while dragging a wire vertex (model untouched).

        Repaints the affected wire item with the dragged vertex moved to *gu*,
        inserting elbows on adjacent segments so the preview stays Manhattan —
        matching the behaviour of MoveWireVertexCommand. The model is only
        updated on release.
        """
        wire_id, idx, _orig = self.vertex_drag
        item = self._scene._wire_items.get(wire_id)
        if item is None:
            return
        pts = list(item.wire.points)
        if not (0 <= idx < len(pts)):
            return
        pts[idx] = gu
        # Re-route the two segments that touch the moved vertex through the
        # shared horizontal-first primitive so the preview stays Manhattan
        # (mirrors MoveWireVertexCommand.do — identical corner convention).
        rebuilt: list[tuple[float, float]] = []
        for j, p in enumerate(pts):
            if j == 0:
                rebuilt.append(p)
                continue
            prev = pts[j - 1]
            if j == idx or j - 1 == idx:
                rebuilt.extend(route(prev, p, vfirst=False)[1:-1])
            rebuilt.append(p)
        simplified = simplify_points(rebuilt)
        item.set_preview_points(simplified)
        self.update_ocirc_preview({wire_id: simplified})

    # ------------------------------------------------------------------
    # Component drag
    # ------------------------------------------------------------------

    def preview_component_drag(self) -> None:
        """Ghost connected wires as the dragged components move (model untouched).

        Pins of the moving components are taken at their drag-start positions;
        the live delta comes from each item's current (snapped) position. Each
        connected wire is reshaped and simplified with the shared helper and
        pushed to its WireItem as a preview path. On release the real
        MoveCommand commits.
        """
        scene = self._scene
        if not self.drag_start:
            return

        # Live delta per component, and the union of their start-pos pins.
        deltas: dict[str, tuple[float, float]] = {}
        start_pins: set[tuple[float, float]] = set()
        for cid, start in self.drag_start.items():
            item = scene._comp_items.get(cid)
            if item is None:
                continue
            cur = scene_to_gu(item.pos())   # unsnapped, for smooth ghosting
            deltas[cid] = (cur[0] - start[0], cur[1] - start[1])
            # Pins at the start position (use a stand-in component at `start`).
            comp = next(
                (c for c in scene._schematic.components if c.id == cid), None
            )
            if comp is not None:
                at_start = replace(comp, position=start)
                for p in component_pin_positions(at_start):
                    start_pins.add(_round_pt(p))

        if not deltas:
            return
        # A single representative delta (all co-dragged items share it).
        dx, dy = next(iter(deltas.values()))

        # When every component is being dragged the whole circuit translates
        # rigidly, so free wire endpoints (open-circle nodes) move too.
        all_dragged = (
            set(self.drag_start.keys()) >= {c.id for c in scene._schematic.components}
        )

        previewed: set[str] = set()
        for wire in scene._schematic.wires:
            pts = wire.points
            if len(pts) < 2:
                continue
            # Mirror MoveCommand._reshape_wires exactly: selected wires and
            # all_dragged both force a rigid translate so free endpoints follow.
            if all_dragged or wire.id in self.drag_wire_ids:
                start_hit = end_hit = True
            else:
                start_hit = _round_pt(pts[0]) in start_pins
                end_hit = _round_pt(pts[-1]) in start_pins
                if not (start_hit or end_hit):
                    continue
            new_pts = reshape_wire_points(
                pts, start_hit=start_hit, end_hit=end_hit, dx=dx, dy=dy,
                simplify=True,
            )
            item = scene._wire_items.get(wire.id)
            if item is not None:
                item.set_preview_points(new_pts)
                previewed.add(wire.id)

        # Clear any wire that was previewed last frame but no longer is.
        for wid in self.previewed_wire_ids - previewed:
            it = scene._wire_items.get(wid)
            if it is not None:
                it.clear_preview_points()
        self.previewed_wire_ids = previewed

        # Keep open-circle items in sync with the previewed wire endpoints.
        # Also pass the current (dragged) pin positions so endpoints that have
        # followed a dragged pin are not incorrectly shown as unconnected.
        preview_pts: dict[str, list[tuple[float, float]]] = {}
        for wire in scene._schematic.wires:
            wi = scene._wire_items.get(wire.id)
            if wi is not None and wi.preview_points is not None:
                preview_pts[wire.id] = wi.preview_points

        dragged_pins: set[tuple[float, float]] = set()
        for cid, start in self.drag_start.items():
            comp = next((c for c in scene._schematic.components if c.id == cid), None)
            item = scene._comp_items.get(cid)
            if comp is not None and item is not None:
                cur = scene_to_gu(item.pos())
                ddx = cur[0] - start[0]
                ddy = cur[1] - start[1]
                at_cur = replace(comp, position=(comp.position[0] + ddx, comp.position[1] + ddy))
                for p in component_pin_positions(at_cur):
                    dragged_pins.add(_round_pt(p))

        self.update_ocirc_preview(preview_pts, extra_pin_positions=dragged_pins)
        self.update_junction_preview(preview_pts, dragged_pins)

    def commit_component_drag(self) -> None:
        """Push the MoveCommand(s) for a finished component drag, then reset state.

        Reads each item's final snapped position, groups components by identical
        snapped delta, and emits one move (plus any pin-landing wire splits) per
        delta as a single undoable action. Called by the scene after Qt has
        finished its own mouse-grab bookkeeping.
        """
        scene = self._scene
        per_delta: dict[tuple[float, float], list[str]] = {}
        for cid, start in self.drag_start.items():
            item = scene._comp_items.get(cid)
            if item is None:
                continue
            new_gu = snap_point_gu(item.pos())
            # Reset the item to its model position; the command moves it.
            item.setPos(gu_to_scene(*start))
            d = (new_gu[0] - start[0], new_gu[1] - start[1])
            if d != (0.0, 0.0):
                per_delta.setdefault(d, []).append(cid)
        drag_wire_ids = list(self.drag_wire_ids)
        self.drag_start = {}
        self.drag_wire_ids = set()

        all_cmds: list = []
        for d, ids in per_delta.items():
            move_cmd = MoveCommand(ids, d, wire_ids=drag_wire_ids)
            split_cmds = scene._pin_splits_after_delta(ids, d)
            all_cmds.append(move_cmd)
            all_cmds.extend(split_cmds)
        if len(all_cmds) == 1:
            scene._push(all_cmds[0])
        elif all_cmds:
            scene._push(MacroCommand(all_cmds, label="Move"))

    def clear_component_drag_preview(self) -> None:
        for wid in self.previewed_wire_ids:
            item = self._scene._wire_items.get(wid)
            if item is not None:
                item.clear_preview_points()
        self.previewed_wire_ids = set()

    # ------------------------------------------------------------------
    # Open-circle and junction preview (during a drag)
    # ------------------------------------------------------------------

    def update_ocirc_preview(
        self,
        preview_pts_by_wire: dict[str, list[tuple[float, float]]],
        extra_pin_positions: set[tuple[float, float]] | None = None,
    ) -> None:
        """Reposition open-circle items to match wire endpoint preview positions.

        *preview_pts_by_wire* maps wire_id → the preview point list for that
        wire (only wires whose endpoints are moving need to be included).

        *extra_pin_positions* is an optional set of additional pin coordinates
        to treat as connected — used during component drag to supply the current
        (dragged) pin positions, which differ from the model positions and would
        otherwise cause connected wire endpoints to falsely appear as open.
        """
        scene = self._scene
        # All pin positions, using live (dragged) positions via extra_pin_positions
        # for components currently being dragged (their model positions are stale).
        pin_positions = self._static_pin_positions()
        if extra_pin_positions:
            pin_positions |= extra_pin_positions

        # Build a count of how many times each coordinate appears across all
        # wire point lists (using preview positions where available).  A count
        # > 1 means the point is shared with another wire — not an open endpoint.
        all_wire_points: dict[tuple[float, float], int] = {}
        for wire in scene._schematic.wires:
            pts = preview_pts_by_wire.get(wire.id, wire.points)
            for pt in pts:
                pt_r = _round_pt(pt)
                all_wire_points[pt_r] = all_wire_points.get(pt_r, 0) + 1

        # Compute the desired ocirc positions from model wires, substituting
        # preview endpoints for any wire that is being previewed.
        desired: set[tuple[float, float]] = set()
        for wire in scene._schematic.wires:
            pts = preview_pts_by_wire.get(wire.id, wire.points)
            if len(pts) < 2:
                continue
            for pt in (pts[0], pts[-1]):
                pt_r = _round_pt(pt)
                if pt_r in pin_positions:
                    continue
                if all_wire_points.get(pt_r, 0) > 1:
                    continue
                desired.add(pt_r)

        # Remove items no longer needed.
        for coord in list(scene._open_circle_items):
            if coord not in desired:
                scene.removeItem(scene._open_circle_items.pop(coord))
        # Add or reposition items.
        for coord in desired:
            if coord not in scene._open_circle_items:
                oc = OpenCircleItem()
                oc.setPos(gu_to_scene(*coord))
                scene.addItem(oc)
                scene._open_circle_items[coord] = oc

    def update_junction_preview(
        self,
        preview_pts_by_wire: dict[str, list[tuple[float, float]]],
        extra_pin_positions: set[tuple[float, float]] | None = None,
    ) -> None:
        """Move junction dot items to match previewed wire positions during drag.

        Recomputes junction degree using preview wire points so dots follow
        the dragged topology rather than staying at the pre-drag model positions.
        """
        scene = self._scene
        # Recompute degree manually using the same logic as junction_points().
        degree: dict[tuple[float, float], int] = {}

        def add(pt: tuple[float, float], d: int) -> None:
            pt = _round_pt(pt)
            degree[pt] = degree.get(pt, 0) + d

        for wire in scene._schematic.wires:
            pts = preview_pts_by_wire.get(wire.id, wire.points)
            own: dict[tuple[float, float], int] = {}
            n = len(pts)
            for i, pt in enumerate(pts):
                pt_r = _round_pt(pt)
                own[pt_r] = own.get(pt_r, 0) + (1 if (i == 0 or i == n - 1) else 2)
            for pt, d in own.items():
                add(pt, d)

        # Add pin positions for non-dragged components (live dragged pins arrive
        # via extra_pin_positions, since model positions are stale mid-drag).
        for p in self._static_pin_positions():
            add(p, 1)
        for p in (extra_pin_positions or set()):
            add(p, 1)

        wanted = {pt for pt, d in degree.items() if d >= 3}

        # Remove dots no longer needed.
        for coord in list(scene._junction_items):
            if coord not in wanted:
                scene.removeItem(scene._junction_items.pop(coord))
        # Add or reposition.
        for coord in wanted:
            if coord not in scene._junction_items:
                dot = JunctionItem()
                dot.setPos(gu_to_scene(*coord))
                scene.addItem(dot)
                scene._junction_items[coord] = dot
            else:
                scene._junction_items[coord].setPos(gu_to_scene(*coord))
