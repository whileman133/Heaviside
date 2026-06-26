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
    MoveEndpointCommand,
    ResizeCommand,
)
from app.schematic.reshape import (
    compute_box_resize_reshape,
    compute_move_reshape,
    compute_pin_drag_reshape,
    move_vertex_points,
    reshape_junction_wire,
)
from app.canvas.geometry import (
    gu_to_scene,
    local_span_to_world,
    scene_to_gu,
    snap_point_gu,
    world_delta_to_local,
    world_span_to_local,
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
    is_box_kind,
    is_terminal_marker,
    junction_points,
    node_resize_factors,
    open_endpoints,
    point_key,
    unconnected_pins,
)

if TYPE_CHECKING:
    from app.canvas.scene import SchematicScene


class DragPreviewController:
    """Owns drag state and renders live drag previews for a SchematicScene."""

    def __init__(self, scene: "SchematicScene") -> None:
        self._scene = scene

        # Drag-move bookkeeping: id -> position at drag start (GU).
        self.drag_start: dict[str, tuple[float, float]] = {}
        # Wire IDs selected at drag-start, captured before super() may deselect them.
        self.drag_wire_ids: set[str] = set()

        # Whole-wire drag (move selected wires directly, not via a component):
        # the wire ids being translated, and the snapped grid point where the drag
        # began. Empty/None when no wire drag is active.
        self.wire_drag_ids: set[str] = set()
        self.wire_drag_start_gu: tuple[float, float] | None = None

        # Wire-vertex drag: (wire_id, index, original_point_gu) or None.
        self.vertex_drag: tuple[str, int, tuple[float, float]] | None = None
        # All wire vertices coincident with the grabbed one (a junction), as
        # [(wire_id, index), ...] — every one moves together so a junction drags
        # with all its connected wires. A lone vertex is a 1-element group.
        self.vertex_drag_group: list[tuple[str, int]] = []
        # Snapped cursor position where the vertex grab began, to tell a click
        # (no grid movement → select) from a real drag (→ move the vertex).
        self.vertex_press_gu: tuple[float, float] | None = None
        # Raw (unsnapped) press position, so a drag onto an *off-grid* pin — whose
        # nearest grid node can equal the press node — still reads as a drag.
        self.vertex_press_raw: tuple[float, float] | None = None
        # While dragging a junction (group > 1): a highlighted, enlarged dot that
        # follows the cursor, plus the static junction dot we hid at its origin.
        self.junction_preview: JunctionDragItem | None = None
        self._hidden_junction: JunctionItem | None = None

        # Endpoint drag for resizable components: (comp_id, handle_index, old_span).
        # handle_index: 0 = origin handle (moves component), 1 = terminal handle.
        self.endpoint_drag: tuple[str, int, tuple[float, float]] | None = None
        self.endpoint_press_gu: tuple[float, float] | None = None

        # Drag-resize of a resizable item: (comp_id, old_value). ``old_value`` is
        # the item's resize value before the drag (muxdemux: span_override (wf,hf) or
        # None; scalable gate/block: the float Component.scale).
        self.resize_drag: tuple[str, object] | None = None

        # Wire ids currently showing a drag-preview (during a component drag),
        # so they can be cleared precisely on release.
        self.previewed_wire_ids: set[str] = set()

        # Ghost wire items for the "re-stretch" leads grown while a pin is dragged
        # off a multi-wire junction (mirrors MoveCommand so the preview matches the
        # committed result). Transient; cleared when the drag preview is cleared.
        self._restretch_ghosts: list[WireItem] = []

        # Terminal-marker components following the current component drag (their
        # items are moved live; restored to their model position on preview clear).
        self._followed_marker_ids: list[str] = []

    # ------------------------------------------------------------------
    # Gesture status
    # ------------------------------------------------------------------

    def any_active(self) -> bool:
        """True while a component / vertex / endpoint drag is in progress."""
        return (
            bool(self.drag_start)
            or self.vertex_drag is not None
            or self.endpoint_drag is not None
            or self.resize_drag is not None
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
            point_key(p)
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

    def endpoint_handle_at(self, scene_pos: QPointF) -> tuple[str, int] | None:
        """Return ``(comp_id, handle_idx)`` if *scene_pos* is over a resize handle
        of any resizable component, regardless of selection state — ``handle_idx``
        is 1 for the terminal handle, 0 for the origin handle (line annotations
        only). Checks selected items first."""
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
            idx = item.endpoint_handle_index_at(local)
            if idx is not None:
                return item.component.id, idx
        return None

    def preview_endpoint_drag(self, gu: tuple[float, float]) -> None:
        """Live visual update while dragging an endpoint (model untouched)."""
        if self.endpoint_drag is None:
            return
        comp_id, handle_idx, old_span = self.endpoint_drag
        resolved = self._endpoint_local_delta(comp_id, gu)
        if resolved is None:
            return
        item, comp, dx, dy = resolved
        if handle_idx == 0:
            self._preview_origin_drag(item, comp, old_span, gu)
            return
        # The cursor is already 0.25-grid-snapped, so the terminal follows it
        # freely (no dead-zone); only an exact zero-length span is skipped so the
        # annotation doesn't momentarily collapse onto its origin.
        if dx == 0.0 and dy == 0.0:
            return

        # A rect/circle resizes as an anchored scale about its fixed corner, so
        # its connected wires follow via the same mapping ResizeCommand uses.
        if is_box_kind(comp):
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
        # The single source of the pin-follow rule (also applied by
        # ResizeCommand); render its result as ghosts, applying nothing.
        result = compute_pin_drag_reshape(
            self._scene._schematic.wires, old_pin=old_pin, dx=pin_dx, dy=pin_dy
        )
        for wid, new_pts in result.new_points.items():
            wire_item = self._scene._wire_items.get(wid)
            if wire_item is not None and len(new_pts) >= 2:
                wire_item.set_preview_points(new_pts)

    def _origin_terminal_world(
        self, comp, old_span: tuple[float, float]
    ) -> tuple[tuple[float, float], tuple[float, float]]:
        """``(old_origin, terminal)`` world positions for an origin drag — the
        terminal is held fixed while the origin follows the cursor."""
        ox, oy = comp.position
        tx, ty = local_span_to_world(old_span, comp.rotation, comp.mirror)
        return (ox, oy), (ox + tx, oy + ty)

    def _preview_origin_drag(
        self, item, comp, old_span: tuple[float, float], gu: tuple[float, float]
    ) -> None:
        """Live preview of an origin-endpoint drag: the origin follows the cursor,
        the terminal stays fixed, and wires at the old origin follow (model
        untouched)."""
        old_origin, terminal = self._origin_terminal_world(comp, old_span)
        new_origin = gu
        new_span = world_span_to_local(
            (terminal[0] - new_origin[0], terminal[1] - new_origin[1]),
            comp.rotation, comp.mirror,
        )
        # The origin follows the (0.25-snapped) cursor freely — no dead-zone — so
        # the handle never feels stuck on pickup; skip only an exact zero-length
        # span (origin landed on the terminal).
        if new_span == (0.0, 0.0):
            return
        item.set_preview_span(new_span)
        item.setPos(gu_to_scene(*new_origin))

        pin_dx = new_origin[0] - old_origin[0]
        pin_dy = new_origin[1] - old_origin[1]
        result = compute_pin_drag_reshape(
            self._scene._schematic.wires, old_pin=old_origin, dx=pin_dx, dy=pin_dy
        )
        for wid, new_pts in result.new_points.items():
            wire_item = self._scene._wire_items.get(wid)
            if wire_item is not None and len(new_pts) >= 2:
                wire_item.set_preview_points(new_pts)

    def _preview_box_resize_wires(
        self,
        comp,  # noqa: ANN001
        old_span: tuple[float, float],
        new_span: tuple[float, float],
    ) -> None:
        """Live preview of connected wires while a rect/circle is resized.

        Renders the result of the shared ``compute_box_resize_reshape`` (the
        same function ``ResizeCommand._reshape_wires_scaled`` applies at
        commit) as preview points on the wire items; the model is untouched
        until commit.
        """
        result = compute_box_resize_reshape(
            comp, old_span=old_span, new_span=new_span,
            wires=self._scene._schematic.wires,
        )
        for wid, new_pts in result.new_points.items():
            wire_item = self._scene._wire_items.get(wid)
            if wire_item is not None and len(new_pts) >= 2:
                wire_item.set_preview_points(new_pts)

    def restore_endpoint_preview(self, comp_id: str) -> None:
        """Reset an endpoint-dragged item to its live model component.

        The preview swapped the item's component for a ``dataclasses.replace``
        copy (``set_preview_span``); any path that ends the gesture **without**
        pushing a ResizeCommand must restore the real model object, or the item
        keeps rendering the preview span and no longer aliases the model.
        """
        comp = next(
            (c for c in self._scene._schematic.components if c.id == comp_id), None
        )
        item = self._scene._comp_items.get(comp_id)
        if comp is not None and isinstance(item, _ResizableTwoTerminalItem):
            item.component = comp
            # An origin drag also moved the item visually (set_preview_span keeps
            # position, but _preview_origin_drag calls setPos); restore it.
            item.setPos(gu_to_scene(*comp.position))

    def commit_endpoint_drag(
        self,
        comp_id: str,
        old_span: tuple[float, float],
        gu: tuple[float, float],
        handle_idx: int = 1,
    ) -> None:
        """Commit the dragged endpoint: a ResizeCommand for the terminal handle, or
        a MoveEndpointCommand for the origin handle (which moves the component and
        holds the terminal fixed)."""
        resolved = self._endpoint_local_delta(comp_id, gu)
        if resolved is None:
            return
        item, comp, dx, dy = resolved
        if handle_idx == 0:
            self._commit_origin_drag(comp_id, comp, old_span, gu)
            return
        # Snap the span to the 0.25 GU grid (matching placement, wire vertices and
        # the live preview), so what the user dragged is what commits.
        dx = round(dx * 4) / 4
        dy = round(dy * 4) / 4
        if (dx == 0.0 and dy == 0.0) or (dx, dy) == old_span:
            # Degenerate or unchanged — undo the preview swap, push nothing.
            self.restore_endpoint_preview(comp_id)
            return
        # Route through the scene's normal push path (one rebuild + one
        # schematic_changed, batch-aware) instead of poking the stack directly.
        self._scene._push(ResizeCommand(comp_id, (dx, dy), old_span))

    def _commit_origin_drag(
        self,
        comp_id: str,
        comp,  # noqa: ANN001
        old_span: tuple[float, float],
        gu: tuple[float, float],
    ) -> None:
        """Commit an origin-endpoint drag. The span is snapped to the 0.25 grid and
        the origin recomputed so the terminal stays exactly fixed; a no-op (zero or
        unchanged span) restores the preview and pushes nothing."""
        old_origin, terminal = self._origin_terminal_world(comp, old_span)
        raw_span = world_span_to_local(
            (terminal[0] - gu[0], terminal[1] - gu[1]), comp.rotation, comp.mirror
        )
        new_span = (round(raw_span[0] * 4) / 4, round(raw_span[1] * 4) / 4)
        if new_span == (0.0, 0.0) or new_span == old_span:
            self.restore_endpoint_preview(comp_id)
            return
        # The origin that keeps the terminal fixed for the snapped span.
        tx, ty = local_span_to_world(new_span, comp.rotation, comp.mirror)
        new_origin = (terminal[0] - tx, terminal[1] - ty)
        self._scene._push(
            MoveEndpointCommand(comp_id, new_span, old_span, new_origin, old_origin)
        )

    # ------------------------------------------------------------------
    # Drag-resize (item-driven): muxdemux (anisotropic) + scalable gates/blocks
    # (uniform). The resizable item supplies the value type, preview, command and
    # handle hit-test via a small protocol, so this controller is kind-agnostic.
    # ------------------------------------------------------------------

    @staticmethod
    def _is_resizable_item(item) -> bool:
        return hasattr(item, "resize_handle_at") and hasattr(item, "resize_from_local")

    def resize_handle_at(self, scene_pos: QPointF) -> str | None:
        """Return the component id whose drag-resize handle is under *scene_pos*
        (selected items first), or None."""
        scene = self._scene
        selected = scene.selectedItems()
        candidates = list(selected) + [
            it for it in scene._comp_items.values()
            if self._is_resizable_item(it) and it not in selected
        ]
        for item in candidates:
            if self._is_resizable_item(item) and item.resize_handle_at(
                item.mapFromScene(scene_pos)
            ):
                return item.component.id
        return None

    def _resize_value(self, comp_id: str, gu: tuple[float, float]):
        """Ask the item for the resize value from the drag target *gu*, mapped into
        the component's local frame (undoing rotation/mirror, like an endpoint drag)."""
        item = self._scene._comp_items.get(comp_id)
        if item is None or not self._is_resizable_item(item):
            return None
        comp = item.component
        # Measure the cursor against the *original* origin captured at grab time: an
        # anchored resize shifts the (previewed) origin to hold the opposite corner,
        # so reading the live position here would feed the shift back in and drift.
        ox, oy = getattr(item, "_resize_start_pos", None) or comp.position
        wdx, wdy = gu[0] - ox, gu[1] - oy
        if comp.mirror:
            wdx = -wdx
        ldx, ldy = world_delta_to_local(wdx, wdy, comp.rotation)
        return item.resize_from_local(ldx, ldy)

    def preview_resize(self, gu: tuple[float, float]) -> None:
        """Live visual update while dragging a resize handle (model untouched);
        connected wires follow each relocated pin as ghosts."""
        if self.resize_drag is None:
            return
        comp_id, _old = self.resize_drag
        item = self._scene._comp_items.get(comp_id)
        value = self._resize_value(comp_id, gu)
        if value is None or item is None:
            return
        model = self._scene._component_by_id(comp_id)
        old_pins = component_pin_positions(model) if model is not None else []
        item.apply_resize_preview(value)
        new_pins = component_pin_positions(item.component)
        for (oxp, oyp), (nxp, nyp) in zip(old_pins, new_pins):
            if (oxp, oyp) == (nxp, nyp):
                continue
            result = compute_pin_drag_reshape(
                self._scene._schematic.wires, old_pin=(oxp, oyp),
                dx=nxp - oxp, dy=nyp - oyp,
            )
            for wid, pts in result.new_points.items():
                wi = self._scene._wire_items.get(wid)
                if wi is not None and len(pts) >= 2:
                    wi.set_preview_points(pts)

    def restore_resize_preview(self, comp_id: str) -> None:
        """Drop a resize preview, re-aliasing the item to the live model (and undoing
        any preview origin shift the anchored resize applied)."""
        comp = self._scene._component_by_id(comp_id)
        item = self._scene._comp_items.get(comp_id)
        if comp is not None and item is not None and self._is_resizable_item(item):
            item.component = comp
            item.setPos(self._scene.gu_to_scene(*comp.position))
            item.prepareGeometryChange()
            item.update()

    def commit_resize(self, gu: tuple[float, float]) -> None:
        """Commit a resize: push the item's resize command, or restore the preview
        when the size is unchanged."""
        if self.resize_drag is None:
            return
        comp_id, old_value = self.resize_drag
        self.resize_drag = None
        item = self._scene._comp_items.get(comp_id)
        value = self._resize_value(comp_id, gu)
        if value is None or item is None or value == old_value:
            self.restore_resize_preview(comp_id)
            return
        self._scene._push(item.resize_command(value, old_value))

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
        # Group targets by wire so a wire with BOTH endpoints at the junction is
        # reshaped on one evolving copy (mirrors MoveJunctionCommand) instead of
        # the second reshape overwriting the first.
        by_wire: dict[str, list[int]] = {}
        for wire_id, idx in group:
            by_wire.setdefault(wire_id, []).append(idx)
        previews: dict[str, list[tuple[float, float]]] = {}
        for wire_id, idxs in by_wire.items():
            item = self._scene._wire_items.get(wire_id)
            if item is None:
                continue
            pts = list(item.wire.points)
            if not (0 <= idxs[0] < len(pts)):
                continue
            if is_junction:
                # Junction drag: keep each wire's orientation into the junction
                # (mirrors MoveJunctionCommand / reshape_junction_wire). Locate
                # each coincident vertex by coordinate in the evolving copy —
                # reshaping may renumber vertices.
                old_key = point_key(pts[idxs[0]])
                simplified = pts
                for _ in idxs:
                    j = next(
                        (i for i, p in enumerate(simplified)
                         if point_key(p) == old_key),
                        None,
                    )
                    if j is None:
                        break
                    simplified = reshape_junction_wire(simplified, j, gu)
                    if point_key(gu) == old_key:
                        break       # zero-distance move; avoid re-finding forever
            else:
                # Single vertex: the shared move_vertex_points (the same
                # function MoveWireVertexCommand applies at commit).
                simplified = move_vertex_points(pts, idxs[0], gu)
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
            # _junction_items is keyed by point_key'd coordinates (the
            # junction_points convention); the grabbed vertex is a raw model
            # coordinate, so round it the same way.
            dot = scene._junction_items.get(point_key(orig))
            if dot is not None:
                dot.setVisible(False)
                self._hidden_junction = dot

    def clear_junction_preview(self) -> None:
        if self.junction_preview is not None:
            self._scene._remove_item(self.junction_preview)
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
                    start_pins.add(point_key(p))

        if not deltas:
            return
        # A single representative delta (all co-dragged items share it).
        dx, dy = next(iter(deltas.values()))

        # When every component is being dragged the whole circuit translates
        # rigidly, so free wire endpoints (open-circle nodes) move too.
        all_dragged = (
            set(self.drag_start.keys()) >= {c.id for c in scene._schematic.components}
        )

        # The single source of the move rule set (sole-lead endpoint test,
        # junction-tap follow, re-stretch leads, contained-wire removal) — the
        # same function MoveCommand applies at commit. Render its result as
        # ghosts; the model is untouched until release.
        result = compute_move_reshape(
            scene._schematic.wires,
            moving_pins=start_pins,
            delta=(dx, dy),
            explicit_wire_ids=self.drag_wire_ids,
            all_dragged=all_dragged,
        )

        previewed: set[str] = set()
        for wid, new_pts in result.new_points.items():
            item = scene._wire_items.get(wid)
            if item is not None:
                item.set_preview_points(new_pts)
                previewed.add(wid)

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
                    dragged_pins.add(point_key(p))

        self.update_ocirc_preview(preview_pts, extra_pin_positions=dragged_pins)
        self.update_junction_preview(preview_pts)
        self.update_pin_circle_preview(preview_pts)

        # Ghost the re-stretch leads (pins dragged off a multi-wire junction), so
        # the preview shows the connection growing — matching what MoveCommand
        # will commit, not a momentarily-disconnected component. The paths come
        # from the same shared compute as the committed leads.
        self._update_restretch_preview(
            [list(path) for path in result.lead_paths]
        )

        # Terminal markers sitting on a moving pin follow the drag live (their items
        # are moved here; the committed MoveCommand moves the model on release).
        self._preview_followed_markers(start_pins, dx, dy)

    def _preview_followed_markers(
        self, start_pins: set[tuple[float, float]], dx: float, dy: float
    ) -> None:
        """Move terminal-marker items sitting on a moving pin by the live drag delta;
        restore any that stopped following. The committed MoveCommand moves the model
        (and the rebuild repositions the items) on release."""
        scene = self._scene
        following: list[str] = []
        for comp in scene._schematic.components:
            if comp.id in self.drag_start or not is_terminal_marker(comp):
                continue
            if any(point_key(p) in start_pins
                   for p in component_connection_points(comp)):
                item = scene._comp_items.get(comp.id)
                if item is not None:
                    item.setPos(gu_to_scene(comp.position[0] + dx,
                                            comp.position[1] + dy))
                    following.append(comp.id)
        # Restore any marker that followed last frame but no longer does.
        for cid in set(self._followed_marker_ids) - set(following):
            comp = self._scene._component_by_id(cid)
            item = scene._comp_items.get(cid)
            if comp is not None and item is not None:
                item.setPos(gu_to_scene(*comp.position))
        self._followed_marker_ids = following

    def _update_restretch_preview(
        self, paths: list[list[tuple[float, float]]]
    ) -> None:
        """Reconcile the ghost lead items to the computed re-stretch lead paths
        (node → live dragged position), creating/removing throwaway
        non-interactive WireItems."""
        scene = self._scene
        while len(self._restretch_ghosts) > len(paths):
            scene._remove_item(self._restretch_ghosts.pop())
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
            self._scene._remove_item(ghost)
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
            if point_key(raw) == point_key(start):
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
        # The selected wires translate by the drag delta exactly ONCE: attach
        # them to a single MoveCommand. With one delta group (the normal case)
        # that group carries them; should snapping ever produce several delta
        # groups, attaching the same wire set to each would translate the wires
        # once per group, by different deltas.
        wires_pending = drag_wire_ids
        for d, ids in per_delta.items():
            move_cmd = MoveCommand(ids, d, wire_ids=wires_pending)
            wires_pending = []
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
        # Reset any followed terminal markers to their model position; the committed
        # MoveCommand (if any) then moves them via the model rebuild.
        for cid in self._followed_marker_ids:
            comp = self._scene._component_by_id(cid)
            item = self._scene._comp_items.get(cid)
            if comp is not None and item is not None:
                item.setPos(gu_to_scene(*comp.position))
        self._followed_marker_ids = []

    # ------------------------------------------------------------------
    # Whole-wire drag (move selected wires; junction taps follow)
    # ------------------------------------------------------------------

    def preview_wire_drag(self, dx: float, dy: float) -> None:
        """Ghost the dragged wires (rigid translate by the live delta) and any
        junction tap that follows, without touching the model. Renders the
        result of the shared ``compute_move_reshape`` (the same function the
        committed wire-only MoveCommand applies) so the preview matches the
        committed move."""
        scene = self._scene
        if not self.wire_drag_ids:
            return
        result = compute_move_reshape(
            scene._schematic.wires,
            moving_pins=set(),
            delta=(dx, dy),
            explicit_wire_ids=self.wire_drag_ids,
            all_dragged=False,
        )

        previewed: set[str] = set()
        for wid, new_pts in result.new_points.items():
            item = scene._wire_items.get(wid)
            if item is not None:
                item.set_preview_points(new_pts)
                previewed.add(wid)

        for wid in self.previewed_wire_ids - previewed:
            it = scene._wire_items.get(wid)
            if it is not None:
                it.clear_preview_points()
        self.previewed_wire_ids = previewed

        preview_pts: dict[str, list[tuple[float, float]]] = {}
        for wire in scene._schematic.wires:
            wi = scene._wire_items.get(wire.id)
            if wi is not None and wi.preview_points is not None:
                preview_pts[wire.id] = wi.preview_points
        self.update_ocirc_preview(preview_pts)
        self.update_junction_preview(preview_pts)
        self.update_pin_circle_preview(preview_pts)

    def commit_wire_drag(self, delta: tuple[float, float]) -> None:
        """Push the MoveCommand for a finished whole-wire drag, then reset state.
        A zero delta (a plain click on a selected wire) pushes nothing."""
        scene = self._scene
        ids = list(self.wire_drag_ids)
        self.wire_drag_ids = set()
        self.wire_drag_start_gu = None
        if not ids or delta == (0.0, 0.0):
            return
        scene._push(MoveCommand([], delta, wire_ids=ids))

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
        # Connecting pin positions only (voltage annotations excluded), using live
        # (dragged) positions via extra_pin_positions for components currently being
        # dragged (their model positions are stale).
        pin_positions = self._static_pin_positions(connecting_only=True)
        if extra_pin_positions:
            pin_positions |= extra_pin_positions
        pin_positions = {point_key(p) for p in pin_positions}

        # Delegate to the single source of the open-terminal rule (model.py),
        # substituting the live preview geometry and pin positions, so the drag
        # preview can never drift from the committed canvas decorations — including
        # the no_termination_dots / custom-marker opt-outs.
        desired = open_endpoints(
            scene._schematic,
            points_override=preview_pts_by_wire,
            pin_positions=pin_positions,
        )

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

    def _live_pin_positions(
        self, *, connecting_only: bool = False
    ) -> list[tuple[float, float]]:
        """Pin coordinates of every component, with components currently being
        dragged taken at their **live** item positions (their model positions
        are stale mid-drag). Returned with multiplicity, matching how the model
        functions count pins. *connecting_only* skips ``NON_CONNECTING_KINDS``
        (the ``open`` voltage annotation), mirroring ``unconnected_pins``."""
        scene = self._scene
        out: list[tuple[float, float]] = []
        for comp in scene._schematic.components:
            if connecting_only and comp.kind in NON_CONNECTING_KINDS:
                continue
            live = comp
            if comp.id in self.drag_start:
                item = scene._comp_items.get(comp.id)
                if item is not None:
                    cur = scene_to_gu(item.pos())
                    start = self.drag_start[comp.id]
                    live = replace(
                        comp,
                        position=(
                            comp.position[0] + cur[0] - start[0],
                            comp.position[1] + cur[1] - start[1],
                        ),
                    )
            out.extend(component_pin_positions(live))
        return out

    def update_pin_circle_preview(
        self,
        preview_pts_by_wire: dict[str, list[tuple[float, float]]],
    ) -> None:
        """Keep unconnected-pin circles in sync during a drag (§10.8).

        Delegates to the single source of the rule (``unconnected_pins``)
        with the live preview wire geometry and live (dragged) pin positions
        substituted, so the drag preview can never drift from the committed
        markers. No-op unless the display preference is enabled.
        """
        scene = self._scene
        if not scene._mark_unconnected_pins:
            return

        desired = unconnected_pins(
            scene._schematic,
            points_override=preview_pts_by_wire,
            pin_positions=self._live_pin_positions(connecting_only=True),
        )

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
    ) -> None:
        """Move junction dot items to match previewed wire positions during drag.

        Delegates to the single source of the junction-dot rule
        (``junction_points``) with the live preview wire geometry and live
        (dragged) pin positions substituted, so the preview honours the same
        topology rules as the committed dots — including the per-wire
        ``no_junction_dots`` opt-out, which a hand-rolled degree count here
        previously ignored (dots flickered into existence during drags).
        """
        scene = self._scene
        wanted = junction_points(
            scene._schematic,
            points_override=preview_pts_by_wire,
            pin_positions=self._live_pin_positions(),
        )

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
