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
    reshape_junction_wire,
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
    JunctionDragItem,
    JunctionItem,
    OpenCircleItem,
    WireItem,
    _ResizableTwoTerminalItem,
)
from app.schematic.model import (
    NON_CONNECTING_KINDS,
    Wire,
    component_connection_points,
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
        # All wire vertices coincident with the grabbed one (a junction), as
        # [(wire_id, index), ...] — every one moves together so a junction drags
        # with all its connected wires. A lone vertex is a 1-element group.
        self.vertex_drag_group: list[tuple[str, int]] = []
        # Snapped cursor position where the vertex grab began, to tell a click
        # (no grid movement → select) from a real drag (→ move the vertex).
        self.vertex_press_gu: tuple[float, float] | None = None
        # While dragging a junction (group > 1): a highlighted, enlarged dot that
        # follows the cursor, plus the static junction dot we hid at its origin.
        self.junction_preview: JunctionDragItem | None = None
        self._hidden_junction: JunctionItem | None = None

        # Endpoint drag for resizable components: (comp_id, handle_index, old_span).
        # handle_index: 0 = origin handle (moves component), 1 = terminal handle.
        self.endpoint_drag: tuple[str, int, tuple[float, float]] | None = None
        self.endpoint_press_gu: tuple[float, float] | None = None

        # Wire ids currently showing a drag-preview (during a component drag),
        # so they can be cleared precisely on release.
        self.previewed_wire_ids: set[str] = set()

        # Ghost wire items for the "re-stretch" leads grown while a pin is dragged
        # off a multi-wire junction (mirrors MoveCommand so the preview matches the
        # committed result). Transient; cleared when the drag preview is cleared.
        self._restretch_ghosts: list[WireItem] = []

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

    def _static_pin_positions(self, connecting_only: bool = False) -> set[tuple[float, float]]:
        """Rounded pin coordinates of components NOT currently being dragged.

        Dragged components are excluded because their model positions are stale
        mid-drag; callers supply the live (dragged) pins via ``extra_pin_positions``.

        When *connecting_only* is True, pins of ``NON_CONNECTING_KINDS`` (the
        ``open`` voltage annotation) are omitted, so the open-endpoint preview
        matches ``open_endpoints`` — a voltage annotation does not connect a wire.
        """
        dragged = set(self.drag_start.keys())
        return {
            _round_pt(p)
            for comp in self._scene._schematic.components
            if comp.id not in dragged
            and not (connecting_only and comp.kind in NON_CONNECTING_KINDS)
            for p in component_connection_points(comp)
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
        wdx, wdy = gu[0] - ox, gu[1] - oy
        # Invert the same rotate-then-mirror transform local_span_to_world
        # applies (§7 Mirror): undo the global Flip-X (negate world x) first, then
        # the rotation, so the dragged terminal of a mirrored component lands
        # under the cursor.
        if comp.mirror:
            wdx = -wdx
        dx, dy = world_delta_to_local(wdx, wdy, comp.rotation)
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

        # A rect/circle resizes as an anchored scale about its fixed corner, so
        # its connected wires follow via the same mapping ResizeCommand uses.
        if comp.kind in ("rect", "circle"):
            item.set_preview_span((dx, dy))
            self._preview_box_resize_wires(comp, old_span, (dx, dy))
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

    def _preview_box_resize_wires(
        self,
        comp,  # noqa: ANN001
        old_span: tuple[float, float],
        new_span: tuple[float, float],
    ) -> None:
        """Live preview of connected wires while a rect/circle is resized.

        Mirrors ``ResizeCommand._reshape_wires_scaled`` but only sets preview
        points on the wire items; the model is untouched until commit.  Uses the
        kind's own connection points (rect perimeter / circle cardinal points).
        """
        x0, y0 = comp.position
        odx, ody = old_span
        ndx, ndy = new_span
        old_perim = component_connection_points(replace(comp, span_override=old_span))

        def _snap(v: float) -> float:
            return round(v / 0.25) * 0.25

        def _map(p: tuple[float, float]) -> tuple[float, float]:
            px, py = p
            fx = (px - x0) / odx if odx else 0.0
            fy = (py - y0) / ody if ody else 0.0
            return (_snap(x0 + fx * ndx), _snap(y0 + fy * ndy))

        for wire in self._scene._schematic.wires:
            pts = wire.points
            if len(pts) < 2:
                continue
            start_hit = pts[0] in old_perim
            end_hit = pts[-1] in old_perim
            if not start_hit and not end_hit:
                continue
            new_pts = list(pts)
            if start_hit:
                t = _map(new_pts[0])
                new_pts = reshape_wire_points(
                    new_pts, start_hit=True, end_hit=False,
                    dx=t[0] - new_pts[0][0], dy=t[1] - new_pts[0][1],
                )
            if end_hit:
                t = _map(new_pts[-1])
                new_pts = reshape_wire_points(
                    new_pts, start_hit=False, end_hit=True,
                    dx=t[0] - new_pts[-1][0], dy=t[1] - new_pts[-1][1],
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

        Repaints every wire in the junction group (a lone vertex is a group of
        one) with its coincident vertex moved to *gu*, inserting elbows on
        adjacent segments so each preview stays Manhattan — matching
        MoveWireVertexCommand. The model is only updated on release.
        """
        group = self.vertex_drag_group or (
            [(self.vertex_drag[0], self.vertex_drag[1])] if self.vertex_drag else []
        )
        is_junction = len(group) > 1
        previews: dict[str, list[tuple[float, float]]] = {}
        for wire_id, idx in group:
            item = self._scene._wire_items.get(wire_id)
            if item is None:
                continue
            pts = list(item.wire.points)
            if not (0 <= idx < len(pts)):
                continue
            if is_junction:
                # Junction drag: keep each wire's orientation into the junction
                # (mirrors MoveJunctionCommand / reshape_junction_wire).
                simplified = reshape_junction_wire(pts, idx, gu)
            else:
                # Single vertex: plain horizontal-first elbow (mirrors
                # MoveWireVertexCommand.do).
                pts[idx] = gu
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
            previews[wire_id] = simplified
        self.update_ocirc_preview(previews)
        self.update_pin_circle_preview(previews)
        # Junction feedback: a highlighted, enlarged dot follows the cursor (and
        # the resting dot at the junction's origin is hidden) so it's clear the
        # whole junction — not just one wire end — is being dragged.
        if is_junction:
            self._show_junction_preview(gu)

    def _show_junction_preview(self, gu: tuple[float, float]) -> None:
        scene = self._scene
        if self.junction_preview is None:
            self.junction_preview = JunctionDragItem()
            scene.addItem(self.junction_preview)
        self.junction_preview.setPos(scene.gu_to_scene(*gu))
        # Hide the resting junction dot at the drag's origin (if any) so we don't
        # show a stale dot left behind at the old position.
        if self._hidden_junction is None and self.vertex_drag is not None:
            orig = self.vertex_drag[2]
            dot = scene._junction_items.get(orig)
            if dot is not None:
                dot.setVisible(False)
                self._hidden_junction = dot

    def clear_junction_preview(self) -> None:
        if self.junction_preview is not None:
            self._scene.removeItem(self.junction_preview)
            self.junction_preview = None
        if self._hidden_junction is not None:
            self._hidden_junction.setVisible(True)
            self._hidden_junction = None

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
                for p in component_connection_points(at_start):
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

        # Endpoint multiplicity per coordinate — mirrors MoveCommand so a pin on a
        # multi-wire junction disconnects (rather than dragging the whole net) in
        # the live preview too, keeping it consistent with the committed move.
        endpoint_count: dict[tuple[float, float], int] = {}
        for w in scene._schematic.wires:
            if len(w.points) < 2:
                continue
            endpoint_count[_round_pt(w.points[0])] = endpoint_count.get(_round_pt(w.points[0]), 0) + 1
            endpoint_count[_round_pt(w.points[-1])] = endpoint_count.get(_round_pt(w.points[-1]), 0) + 1

        previewed: set[str] = set()
        for wire in scene._schematic.wires:
            pts = wire.points
            if len(pts) < 2:
                continue
            # Mirror MoveCommand._reshape_wires exactly: selected wires and
            # all_dragged both force a rigid translate so free endpoints follow;
            # otherwise an endpoint follows only when it is its pin's sole lead.
            if all_dragged or wire.id in self.drag_wire_ids:
                start_hit = end_hit = True
            else:
                start_hit = (_round_pt(pts[0]) in start_pins
                             and endpoint_count.get(_round_pt(pts[0]), 0) == 1)
                end_hit = (_round_pt(pts[-1]) in start_pins
                           and endpoint_count.get(_round_pt(pts[-1]), 0) == 1)
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
            if comp is not None and comp.kind in NON_CONNECTING_KINDS:
                continue  # voltage annotation forms no connection
            if comp is not None and item is not None:
                cur = scene_to_gu(item.pos())
                ddx = cur[0] - start[0]
                ddy = cur[1] - start[1]
                at_cur = replace(comp, position=(comp.position[0] + ddx, comp.position[1] + ddy))
                for p in component_connection_points(at_cur):
                    dragged_pins.add(_round_pt(p))

        self.update_ocirc_preview(preview_pts, extra_pin_positions=dragged_pins)
        self.update_junction_preview(preview_pts, dragged_pins)
        self.update_pin_circle_preview(preview_pts)

        # Ghost the re-stretch leads (pins dragged off a multi-wire junction), so
        # the preview shows the connection growing — matching what MoveCommand
        # will commit, not a momentarily-disconnected component.
        restretch_pins = set() if all_dragged else {
            p for p in start_pins if endpoint_count.get(p, 0) >= 2
        }
        self._update_restretch_preview(restretch_pins, dx, dy)

    def _update_restretch_preview(
        self, pins: set[tuple[float, float]], dx: float, dy: float
    ) -> None:
        """Reconcile the ghost lead items to one per re-stretch pin (node → live
        dragged position), creating/removing throwaway non-interactive WireItems."""
        scene = self._scene
        paths: list[list[tuple[float, float]]] = []
        for p in sorted(pins):
            path = route(p, (p[0] + dx, p[1] + dy))
            if len(path) >= 2 and len(set(path)) >= 2:
                paths.append(path)
        while len(self._restretch_ghosts) > len(paths):
            scene.removeItem(self._restretch_ghosts.pop())
        while len(self._restretch_ghosts) < len(paths):
            ghost = WireItem(Wire(id="__restretch_ghost__", points=[(0.0, 0.0), (0.0, 0.0)]))
            ghost.setFlag(ghost.GraphicsItemFlag.ItemIsSelectable, False)
            ghost.setAcceptHoverEvents(False)
            scene.addItem(ghost)
            self._restretch_ghosts.append(ghost)
        for ghost, path in zip(self._restretch_ghosts, paths):
            ghost.set_preview_points(path)

    def _clear_restretch_preview(self) -> None:
        for ghost in self._restretch_ghosts:
            self._scene.removeItem(ghost)
        self._restretch_ghosts = []

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
            # A plain click leaves the item exactly at its start position (no
            # mouseMove fired, so it was never live-snapped). Skip it so a click
            # pushes no spurious zero-distance move. Only a real drag snaps to
            # the 0.25 grid (§3.1).
            raw = scene_to_gu(item.pos())
            if _round_pt(raw) == _round_pt(start):
                item.setPos(gu_to_scene(*start))  # guard against float drift
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
        self._clear_restretch_preview()

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
        # Connecting pin positions only (voltage annotations excluded, matching
        # open_endpoints), using live (dragged) positions via extra_pin_positions
        # for components currently being dragged (their model positions are stale).
        pin_positions = self._static_pin_positions(connecting_only=True)
        if extra_pin_positions:
            pin_positions |= extra_pin_positions

        # Build a count of how many times each coordinate appears across all
        # wire point lists (using preview positions where available).  A count
        # > 1 means the point is shared with another wire — not an open endpoint.
        all_wire_points: dict[tuple[float, float], int] = {}
        for wire in scene._schematic.wires:
            pts = preview_pts_by_wire.get(wire.id, wire.points)
            if len(pts) < 2:
                continue  # degenerate wire connects nothing (mirrors open_endpoints)
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
                scene._remove_item(scene._open_circle_items.pop(coord))
        # Add or reposition items.
        for coord in desired:
            if coord not in scene._open_circle_items:
                oc = OpenCircleItem()
                oc.setPos(gu_to_scene(*coord))
                scene.addItem(oc)
                scene._open_circle_items[coord] = oc

    def update_pin_circle_preview(
        self,
        preview_pts_by_wire: dict[str, list[tuple[float, float]]],
    ) -> None:
        """Keep unconnected-pin circles in sync during a drag (§10.8).

        Recomputes which component pins are unconnected using the *live*
        positions of any components currently being dragged (their model
        positions are stale until commit) and preview wire points, then
        reconciles ``scene._pin_circle_items``.  No-op unless the display
        preference is enabled.
        """
        scene = self._scene
        if not scene._mark_unconnected_pins:
            return

        # Live pin multiplicity: dragged components at their current item pos,
        # everything else at its model position.
        pin_count: dict[tuple[float, float], int] = {}
        for comp in scene._schematic.components:
            if comp.kind in NON_CONNECTING_KINDS:
                continue  # voltage annotation forms no connection (mirrors unconnected_pins)
            live = comp
            if comp.id in self.drag_start:
                item = scene._comp_items.get(comp.id)
                if item is not None:
                    cur = scene_to_gu(item.pos())
                    start = self.drag_start[comp.id]
                    ddx, ddy = cur[0] - start[0], cur[1] - start[1]
                    live = replace(
                        comp,
                        position=(comp.position[0] + ddx, comp.position[1] + ddy),
                    )
            for p in component_pin_positions(live):
                pr = _round_pt(p)
                pin_count[pr] = pin_count.get(pr, 0) + 1

        # Live wire vertices (preview-substituted where a wire is being dragged).
        wire_points: set[tuple[float, float]] = set()
        for wire in scene._schematic.wires:
            pts = preview_pts_by_wire.get(wire.id, wire.points)
            if len(pts) < 2:
                continue  # degenerate wire connects nothing (mirrors unconnected_pins)
            for pt in pts:
                wire_points.add(_round_pt(pt))

        desired = {
            coord
            for coord, count in pin_count.items()
            if count == 1 and coord not in wire_points
        }

        for coord in list(scene._pin_circle_items):
            if coord not in desired:
                scene._remove_item(scene._pin_circle_items.pop(coord))
        for coord in desired:
            if coord not in scene._pin_circle_items:
                pc = OpenCircleItem()
                pc.setPos(gu_to_scene(*coord))
                scene.addItem(pc)
                scene._pin_circle_items[coord] = pc

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
            if len(pts) < 2:
                continue  # a degenerate single-point wire connects nothing
                          # (mirrors junction_points(); otherwise it would add a
                          # phantom dot at its point during a drag)
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
                scene._remove_item(scene._junction_items.pop(coord))
        # Add or reposition.
        for coord in wanted:
            if coord not in scene._junction_items:
                dot = JunctionItem()
                dot.setPos(gu_to_scene(*coord))
                scene.addItem(dot)
                scene._junction_items[coord] = dot
            else:
                scene._junction_items[coord].setPos(gu_to_scene(*coord))
