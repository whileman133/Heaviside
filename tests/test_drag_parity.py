"""
Preview ↔ commit parity regression tests.

The wire-reshape rules live exactly once, in ``app.schematic.reshape``; the
commands apply the computed results and the drag previews render them as
ghosts. These tests drive real canvas gestures, capture the preview geometry
at the last frame, commit, and assert the committed model equals the preview
**exactly** — so they fail if anyone reintroduces a hand-rolled fork of the
reshape logic on either side.

Scenario matrix:

* straight component drag pulling a connected wire;
* component + wire co-drag where a junction tap follows the dragged wire;
* whole-wire drag with a junction tap;
* re-stretch lead grown when a pin is dragged off a multi-wire junction;
* contained-wire removal (a lead dragged collinearly onto its rail);
* box (rect) resize with attached wires;
* vertex drag with collinear merge and with collapse-to-a-point.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:  # pragma: no cover - environment-dependent
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QGraphicsSceneMouseEvent  # noqa: E402

from app.canvas.commands import ResizeCommand  # noqa: E402
from app.canvas.scene import SchematicScene  # noqa: E402
from app.schematic.model import Wire, wire_contained_by_others  # noqa: E402


@pytest.fixture()
def scene() -> SchematicScene:
    return SchematicScene()


# ---------------------------------------------------------------------------
# Gesture helpers (same conventions as test_scene.py)
# ---------------------------------------------------------------------------

def _begin_component_drag(scene: SchematicScene, comp_id: str):
    item = scene._comp_items[comp_id]
    item.setSelected(True)
    p = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    p.setButton(Qt.LeftButton)
    comp = next(c for c in scene.schematic.components if c.id == comp_id)
    cx, cy = comp.position[0] + 1.0, comp.position[1]   # resistor centre
    p.setScenePos(scene.gu_to_scene(cx, cy))
    scene.mousePressEvent(p)


def _drag_component_to(scene: SchematicScene, comp_id: str, dx_gu, dy_gu):
    from app.canvas.style import GRID_PX

    item = scene._comp_items[comp_id]
    item.setPos(item.pos().x() + dx_gu * GRID_PX, item.pos().y() + dy_gu * GRID_PX)
    mv = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseMove)
    mv.setScenePos(item.pos())
    mv.setButtons(Qt.LeftButton)
    scene.mouseMoveEvent(mv)


def _release_component(scene: SchematicScene, comp_id: str):
    item = scene._comp_items[comp_id]
    rel = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseRelease)
    rel.setButton(Qt.LeftButton)
    rel.setScenePos(item.pos())
    scene.mouseReleaseEvent(rel)


def _capture_previews(scene: SchematicScene) -> dict[str, list]:
    """Wire id → live preview polyline (only wires currently showing a ghost)."""
    return {
        wid: list(item.preview_points)
        for wid, item in scene._wire_items.items()
        if item.preview_points is not None
    }


def _capture_restretch_ghosts(scene: SchematicScene) -> list[list]:
    return [
        list(g.preview_points)
        for g in scene._drag._restretch_ghosts
        if g.preview_points is not None
    ]


def _assert_parity(scene: SchematicScene, previews: dict[str, list]) -> None:
    """Every previewed wire either matches the committed model exactly, or was
    removed at commit — in which case the ghost must have been degenerate
    (collapsed) or fully covered by the surviving wires (contained), i.e. the
    preview showed nothing the commit didn't."""
    model = {w.id: list(w.points) for w in scene.schematic.wires}
    for wid, ghost in previews.items():
        if wid in model:
            assert model[wid] == ghost, (
                f"preview/commit fork for wire {wid}: "
                f"ghost={ghost} committed={model[wid]}"
            )
        else:
            others = [SimpleNamespace(points=p) for p in model.values()]
            assert len(ghost) < 2 or wire_contained_by_others(ghost, others), (
                f"wire {wid} was removed at commit but its ghost {ghost} was "
                f"neither collapsed nor covered by the committed geometry"
            )


# ---------------------------------------------------------------------------
# 1. Straight component drag pulling a connected wire
# ---------------------------------------------------------------------------

def test_parity_straight_drag_pulls_wire(scene: SchematicScene):
    a = scene.place_component("R", (0.0, 0.0))   # pins (0,0),(2,0)
    scene.place_component("R", (6.0, 0.0))
    w = scene.add_wire([(2.0, 0.0), (6.0, 0.0)])

    _begin_component_drag(scene, a.id)
    _drag_component_to(scene, a.id, 0.0, 2.0)
    previews = _capture_previews(scene)
    assert w.id in previews                      # the connected wire is ghosted
    _release_component(scene, a.id)

    _assert_parity(scene, previews)
    # And the move really happened (guard against a vacuous pass).
    assert scene.schematic.wires[0].points[0] == (2.0, 2.0)


# ---------------------------------------------------------------------------
# 2. Component + wire co-drag: a junction tap follows the dragged wire
# ---------------------------------------------------------------------------

def test_parity_co_drag_junction_tap_follows(scene: SchematicScene):
    """A wire tapping the co-dragged rail mid-segment follows in the preview
    exactly as it does at commit (regression: the preview used to skip the
    explicit junction-tap rule that MoveCommand applied)."""
    a = scene.place_component("R", (0.0, 0.0))           # pins (0,0),(2,0)
    scene.place_component("R", (20.0, 20.0))             # bystander: not a select-all move
    rail = Wire(id="rail", points=[(2.0, 0.0), (6.0, 0.0)])
    tap = Wire(id="tap", points=[(4.0, 0.0), (4.0, 2.0)])
    scene.schematic.wires.extend([rail, tap])
    scene._rebuild_items()

    item = scene._comp_items[a.id]
    item.setSelected(True)
    scene._wire_items[rail.id].setSelected(True)
    p = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    p.setButton(Qt.LeftButton)
    p.setScenePos(scene.gu_to_scene(1.0, 0.0))           # resistor body centre
    scene.mousePressEvent(p)
    assert rail.id in scene._drag.drag_wire_ids

    _drag_component_to(scene, a.id, 0.0, 1.0)
    previews = _capture_previews(scene)
    # Both the rail (rigid) and the tap (follows at the shared point) ghost.
    assert rail.id in previews and tap.id in previews
    assert previews[rail.id] == [(2.0, 1.0), (6.0, 1.0)]
    assert previews[tap.id][0] == (4.0, 1.0)
    _release_component(scene, a.id)

    _assert_parity(scene, previews)


# ---------------------------------------------------------------------------
# 3. Whole-wire drag with a junction tap
# ---------------------------------------------------------------------------

def test_parity_wire_drag_with_tap(scene: SchematicScene):
    rail = Wire(id="rail", points=[(0.0, 0.0), (4.0, 0.0)])
    tap = Wire(id="tap", points=[(2.0, 0.0), (2.0, 2.0)])
    scene.schematic.wires.extend([rail, tap])
    scene._rebuild_items()

    scene._drag.wire_drag_ids = {rail.id}
    scene._drag.preview_wire_drag(0.0, 1.0)
    previews = _capture_previews(scene)
    assert rail.id in previews and tap.id in previews
    scene._drag.clear_component_drag_preview()
    scene._drag.wire_drag_ids = {rail.id}
    scene._drag.commit_wire_drag((0.0, 1.0))

    _assert_parity(scene, previews)
    by_id = {w.id: w.points for w in scene.schematic.wires}
    assert by_id[rail.id] == [(0.0, 1.0), (4.0, 1.0)]


# ---------------------------------------------------------------------------
# 4. Re-stretch lead: pin dragged off a multi-wire junction
# ---------------------------------------------------------------------------

def test_parity_restretch_lead(scene: SchematicScene):
    a = scene.place_component("R", (0.0, 0.0))           # pins (0,0),(2,0)
    scene.place_component("R", (20.0, 20.0))             # bystander: not a select-all move
    scene.schematic.wires.append(Wire(id="w_up", points=[(2.0, 0.0), (2.0, 2.0)]))
    scene.schematic.wires.append(Wire(id="w_right", points=[(2.0, 0.0), (4.0, 0.0)]))
    scene._rebuild_items()
    before_ids = {w.id for w in scene.schematic.wires}

    _begin_component_drag(scene, a.id)
    _drag_component_to(scene, a.id, 0.0, -1.0)
    previews = _capture_previews(scene)
    ghost_leads = _capture_restretch_ghosts(scene)
    # The junction wires do NOT follow; the connection re-stretches instead.
    assert "w_up" not in previews and "w_right" not in previews
    assert ghost_leads, "expected a re-stretch lead ghost during the drag"
    _release_component(scene, a.id)

    _assert_parity(scene, previews)
    # The committed move created exactly the leads the preview ghosted.
    new_wires = [w for w in scene.schematic.wires if w.id not in before_ids]
    assert sorted(tuple(p for p in w.points) for w in new_wires) == sorted(
        tuple(p for p in path) for path in ghost_leads
    )
    # The junction wires are untouched at commit too.
    by_id = {w.id: w.points for w in scene.schematic.wires}
    assert by_id["w_up"] == [(2.0, 0.0), (2.0, 2.0)]
    assert by_id["w_right"] == [(2.0, 0.0), (4.0, 0.0)]


# ---------------------------------------------------------------------------
# 5. Contained-wire removal: lead dragged collinearly onto its rail
# ---------------------------------------------------------------------------

def test_parity_contained_wire_removed(scene: SchematicScene):
    a = scene.place_component("R", (0.0, 0.0))           # pins (0,0),(2,0)
    scene.place_component("R", (20.0, 20.0))             # bystander: not a select-all move
    lead = scene.add_wire([(2.0, 0.0), (4.0, 0.0)])
    rail = scene.add_wire([(4.0, 0.0), (8.0, 0.0)])

    _begin_component_drag(scene, a.id)
    _drag_component_to(scene, a.id, 4.0, 0.0)            # pin (2,0) → (6,0): mid-rail
    previews = _capture_previews(scene)
    assert lead.id in previews                           # the lead ghosts onto the rail
    _release_component(scene, a.id)

    _assert_parity(scene, previews)
    # The lead is gone at commit — it lay entirely on the rail.
    assert lead.id not in {w.id for w in scene.schematic.wires}
    # No re-stretch ghosts were shown for a sole-lead pin.
    assert scene._drag._restretch_ghosts == []
    # And the model is degenerate-free.
    assert all(len(w.points) >= 2 for w in scene.schematic.wires)
    assert rail is not None


# ---------------------------------------------------------------------------
# 6. Box (rect) resize with attached wires
# ---------------------------------------------------------------------------

def test_parity_box_resize_with_attached_wires(scene: SchematicScene):
    comp = scene.place_component("rect", (0.0, 0.0))
    comp.span_override = (2.0, 2.0)
    scene._rebuild_items()
    # One wire on the moving (right) edge midpoint, one on the anchored corner.
    w_right = scene.add_wire([(2.0, 1.0), (5.0, 1.0)])
    w_anchor = scene.add_wire([(0.0, 0.0), (0.0, -2.0)])

    old_span, new_span = (2.0, 2.0), (3.0, 2.0)
    scene._drag._preview_box_resize_wires(comp, old_span, new_span)
    previews = _capture_previews(scene)
    assert w_right.id in previews
    assert w_anchor.id not in previews                   # anchored corner: no motion
    for item in scene._wire_items.values():
        item.clear_preview_points()

    scene._push(ResizeCommand(comp.id, new_span, old_span))
    _assert_parity(scene, previews)
    by_id = {w.id: w.points for w in scene.schematic.wires}
    assert by_id[w_right.id][0] == (3.0, 1.0)            # followed the scaled edge
    assert by_id[w_anchor.id] == [(0.0, 0.0), (0.0, -2.0)]


# ---------------------------------------------------------------------------
# 7. Vertex drag: collinear merge and collapse
# ---------------------------------------------------------------------------

def test_parity_vertex_drag_collinear_merge(scene: SchematicScene):
    w = scene.add_wire([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])
    # Drag the corner straight down onto the wire's own axis: the preview and
    # the commit both simplify the collinear run identically.
    scene._drag.vertex_drag = (w.id, 1, (2.0, 0.0))
    scene._drag.vertex_drag_group = [(w.id, 1)]
    scene._drag.preview_vertex_drag((2.0, 1.0))
    previews = _capture_previews(scene)
    assert w.id in previews
    scene._wire_items[w.id].clear_preview_points()
    scene._drag.vertex_drag = None
    scene._drag.vertex_drag_group = []

    scene.move_wire_vertex(w.id, 1, (2.0, 1.0))
    _assert_parity(scene, previews)


def test_parity_vertex_drag_collapse_removes_wire(scene: SchematicScene):
    w = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    scene._drag.vertex_drag = (w.id, 1, (2.0, 0.0))
    scene._drag.vertex_drag_group = [(w.id, 1)]
    scene._drag.preview_vertex_drag((0.0, 0.0))          # endpoint onto endpoint
    previews = _capture_previews(scene)
    assert w.id in previews and len(previews[w.id]) < 2  # ghost shows the collapse
    scene._wire_items[w.id].clear_preview_points()
    scene._drag.vertex_drag = None
    scene._drag.vertex_drag_group = []

    scene.move_wire_vertex(w.id, 1, (0.0, 0.0))
    _assert_parity(scene, previews)
    assert w.id not in {x.id for x in scene.schematic.wires}
