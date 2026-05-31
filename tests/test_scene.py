"""
Phase 7 integration tests — SchematicScene / SchematicView (spec §13.3).

These drive the canvas at the scene level (no real window), exercising the
parts of §13.3 that do not depend on phase-9 UI panels:

  - test_place_component_updates_model
  - test_undo_place           (undo via the scene)
  - test_undo_redo_place
  - test_snap_to_grid
  - test_pin_snap
  - test_source_reflects_scene  (codegen of the scene's model contains the
                                 expected CircuiTikZ keyword — proxy for the
                                 phase-9 source panel)

A Qt application is required. In headless CI use:

    QT_QPA_PLATFORM=offscreen pytest

If PySide6 cannot initialise its platform plugin (e.g. missing system GL
libraries on a bare sandbox), the whole module is skipped rather than failing —
these are integration tests, and the unit-level command tests in
test_commands.py already cover the model logic without Qt.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Skip the entire module gracefully if Qt / its platform plugin is unavailable.
pytest.importorskip("PySide6.QtWidgets", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:  # creating the QApplication can fail if no platform plugin loads
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.canvas.scene import Mode, SchematicScene  # noqa: E402
from app.canvas.view import SchematicView  # noqa: E402
from app.codegen.circuitikz import generate  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def scene() -> SchematicScene:
    return SchematicScene()


# ---------------------------------------------------------------------------
# Placement → model
# ---------------------------------------------------------------------------

def test_place_component_updates_model(scene: SchematicScene):
    comp = scene.place_component("R", (2.0, 0.0))
    assert len(scene.schematic.components) == 1
    placed = scene.schematic.components[0]
    assert placed.id == comp.id
    assert placed.kind == "R"
    assert placed.position == (2.0, 0.0)
    # The view item was created for it.
    assert comp.id in scene._comp_items


def test_place_creates_graphics_item(scene: SchematicScene):
    scene.place_component("V", (0.0, 0.0))
    items = [it for it in scene.items() if hasattr(it, "component")]
    kinds = {it.component.kind for it in items}
    assert "V" in kinds


# ---------------------------------------------------------------------------
# Undo / redo through the scene
# ---------------------------------------------------------------------------

def test_undo_place(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    assert len(scene.schematic.components) == 1
    scene.undo()
    assert scene.schematic.components == []
    # The graphics item is gone too.
    assert scene._comp_items == {}


def test_undo_redo_place(scene: SchematicScene):
    comp = scene.place_component("C", (1.0, 1.0), rotation=90)
    scene.undo()
    assert scene.schematic.components == []
    scene.redo()
    assert len(scene.schematic.components) == 1
    restored = scene.schematic.components[0]
    assert restored.id == comp.id
    assert restored.rotation == 90
    assert restored.position == (1.0, 1.0)


def test_delete_selected_removes_component(scene: SchematicScene):
    comp = scene.place_component("R", (0.0, 0.0))
    scene._comp_items[comp.id].setSelected(True)
    scene.delete_selected()
    assert scene.schematic.components == []
    scene.undo()
    assert len(scene.schematic.components) == 1


# ---------------------------------------------------------------------------
# Snapping
# ---------------------------------------------------------------------------

def test_snap_to_grid(scene: SchematicScene):
    # Placement snaps an off-grid request to the nearest 0.5 GU point.
    comp = scene.place_component("R", (0.7, 1.24))
    assert comp.position == (0.5, 1.0)


@pytest.mark.parametrize(
    "raw,expected",
    [(0.0, 0.0), (0.24, 0.0), (0.26, 0.5), (0.7, 0.5), (0.74, 0.5), (0.76, 1.0)],
)
def test_snap_gu_rounding(scene: SchematicScene, raw, expected):
    assert scene.snap_gu(raw) == expected


def test_pin_snap(scene: SchematicScene):
    # Resistor at (1,1): pins at 'in' (1,1) and 'out' (3,1).
    scene.place_component("R", (1.0, 1.0))
    # A point just past the 'out' pin snaps to the exact pin coord.
    assert scene._nearest_pin_gu((3.1, 1.0)) == (3.0, 1.0)
    # A point far from any pin does not snap.
    assert scene._nearest_pin_gu((10.0, 10.0)) is None


def test_pin_snap_respects_radius(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))  # pins (0,0),(2,0)
    # 0.25 GU is the snap radius; just inside snaps, just outside does not.
    assert scene._nearest_pin_gu((0.2, 0.0)) == (0.0, 0.0)
    assert scene._nearest_pin_gu((0.4, 0.0)) is None


# ---------------------------------------------------------------------------
# Wiring through the scene
# ---------------------------------------------------------------------------

def test_add_wire_updates_model(scene: SchematicScene):
    w = scene.add_wire([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])
    assert w is not None
    assert len(scene.schematic.wires) == 1
    assert scene.schematic.wires[0].points == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]


def test_add_wire_rejects_single_point(scene: SchematicScene):
    assert scene.add_wire([(0.0, 0.0)]) is None
    assert scene.schematic.wires == []


def test_route_manhattan(scene: SchematicScene):
    # Dominant axis: travel the longer leg first (spec §6.4 routing primitive).
    # |dy|=3 > |dx|=2 → vertical-first, corner at (ax, by).
    assert scene._route((0.0, 0.0), (2.0, 3.0)) == [(0.0, 0.0), (0.0, 3.0), (2.0, 3.0)]
    # |dx|=3 > |dy|=2 → horizontal-first, corner at (bx, ay).
    assert scene._route((0.0, 0.0), (3.0, 2.0)) == [(0.0, 0.0), (3.0, 0.0), (3.0, 2.0)]
    # Equal legs tie to horizontal-first (|dx| >= |dy|).
    assert scene._route((0.0, 0.0), (2.0, 2.0)) == [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]
    # Straight segments stay two-point.
    assert scene._route((0.0, 0.0), (2.0, 0.0)) == [(0.0, 0.0), (2.0, 0.0)]


# ---------------------------------------------------------------------------
# Mode signals
# ---------------------------------------------------------------------------

def test_mode_change_emits_signal(scene: SchematicScene):
    seen = []
    scene.mode_changed.connect(seen.append)
    scene.enter_wire_mode()
    assert scene.mode == Mode.WIRE
    assert seen and seen[-1] == Mode.WIRE
    scene.enter_select_mode()
    assert scene.mode == Mode.SELECT


def test_start_placement_enters_place_mode(scene: SchematicScene):
    scene.start_placement("R")
    assert scene.mode == Mode.PLACE
    assert scene._ghost is not None
    scene.cancel_current()
    assert scene.mode == Mode.SELECT
    assert scene._ghost is None


def test_start_placement_unknown_kind_raises(scene: SchematicScene):
    with pytest.raises(KeyError):
        scene.start_placement("not_a_kind")


# ---------------------------------------------------------------------------
# Source reflects scene (proxy for the phase-9 source panel)
# ---------------------------------------------------------------------------

def test_source_reflects_scene(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    src = generate(scene.schematic)
    assert "to[R" in src or "R," in src or "[R]" in src


def test_schematic_changed_signal(scene: SchematicScene):
    fired = []
    scene.schematic_changed.connect(lambda: fired.append(1))
    scene.place_component("R", (0.0, 0.0))
    assert fired  # at least one emission on placement


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------

def test_view_zoom(scene: SchematicScene):
    view = SchematicView(scene)
    z0 = view.zoom
    view.zoom_in()
    assert view.zoom > z0
    view.zoom_out()
    assert abs(view.zoom - z0) < 1e-9


def test_view_fit_empty(scene: SchematicScene):
    view = SchematicView(scene)
    # Fit with no items should not raise and resets to a sane zoom.
    view.fit_to_schematic()
    assert view.zoom > 0


# ---------------------------------------------------------------------------
# Regression: "disappearing component" bug
#
# Components were left ItemIsMovable in every mode, so a press-drag in WIRE
# mode let Qt move the graphics item without any MoveCommand. The item then
# desynced from its model position until the next _rebuild_items() snapped it
# back — which looked on screen like the component vanishing/teleporting.
# ---------------------------------------------------------------------------

from PySide6.QtWidgets import QGraphicsItem  # noqa: E402


def _is_movable(item) -> bool:
    return bool(item.flags() & QGraphicsItem.ItemIsMovable)


def _is_selectable(item) -> bool:
    return bool(item.flags() & QGraphicsItem.ItemIsSelectable)


def test_items_movable_only_in_select_mode(scene: SchematicScene):
    comp = scene.place_component("R", (1.0, 1.0))
    item = scene._comp_items[comp.id]

    # SELECT (default): draggable + selectable.
    assert _is_movable(item)
    assert _is_selectable(item)

    # WIRE mode: item must NOT be movable/selectable (root cause of the bug).
    scene.enter_wire_mode()
    item = scene._comp_items[comp.id]
    assert not _is_movable(item)
    assert not _is_selectable(item)

    # PLACE mode: also locked.
    scene.start_placement("C")
    item = scene._comp_items[comp.id]
    assert not _is_movable(item)

    # Back to SELECT: interactivity restored.
    scene.enter_select_mode()
    item = scene._comp_items[comp.id]
    assert _is_movable(item)
    assert _is_selectable(item)


def test_flags_reapplied_after_rebuild_in_nonselect_mode(scene: SchematicScene):
    """A command that rebuilds items while in WIRE mode must keep them locked."""
    scene.place_component("R", (0.0, 0.0))
    scene.enter_wire_mode()
    # Adding a wire triggers _rebuild_items(); freshly created component items
    # are movable by default and must be re-locked by _apply_item_flags().
    scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    for item in scene._comp_items.values():
        assert not _is_movable(item)
        assert not _is_selectable(item)


def test_wire_mode_press_does_not_move_component(scene: SchematicScene):
    """Pressing on a component body in WIRE mode starts a wire, not a drag.

    The model position must be untouched and the item must stay in sync.
    """
    comp = scene.place_component("R", (5.0, 5.0))  # pins (5,5),(7,5)
    scene.enter_wire_mode()

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    from app.canvas.style import GRID_PX

    press = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    press.setButton(Qt.LeftButton)
    # press on the resistor body (between pins), at GU (6,5)
    press.setScenePos(scene.gu_to_scene(6.0, 5.0))
    scene.mousePressEvent(press)

    # Model position is unchanged; a wire has begun instead.
    assert scene.schematic.components[0].position == (5.0, 5.0)
    item = scene._comp_items[comp.id]
    # Item is locked, so Qt cannot have grabbed it for a drag.
    assert not _is_movable(item)
    # Item pixel pos still matches the model.
    assert item.pos() == scene.gu_to_scene(5.0, 5.0)


def test_multi_select_group_drag_moves_each_component(scene: SchematicScene):
    """Group drag must move every selected component, not just the last one."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    a = scene.place_component("R", (0.0, 0.0))
    b = scene.place_component("R", (4.0, 0.0))
    ia = scene._comp_items[a.id]
    ib = scene._comp_items[b.id]
    ia.setSelected(True)
    ib.setSelected(True)

    # Begin drag (records start positions for the selection). Press at the
    # body centre (1,0), not the origin pin, to avoid auto-wire.
    press = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    press.setButton(Qt.LeftButton)
    press.setScenePos(scene.gu_to_scene(1.0, 0.0))
    scene.mousePressEvent(press)
    assert set(scene._drag_start) == {a.id, b.id}

    # Simulate both items dragged by the same +2 GU in x.
    from app.canvas.style import GRID_PX
    ia.setPos(ia.pos().x() + 2 * GRID_PX, ia.pos().y())
    ib.setPos(ib.pos().x() + 2 * GRID_PX, ib.pos().y())

    rel = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseRelease)
    rel.setButton(Qt.LeftButton)
    rel.setScenePos(ia.pos())
    scene.mouseReleaseEvent(rel)

    by_id = {c.id: c.position for c in scene.schematic.components}
    assert by_id[a.id] == (2.0, 0.0)
    assert by_id[b.id] == (6.0, 0.0)  # would stay (4,0) under the old delta bug

    # And it is one undoable unit.
    scene.undo()
    by_id = {c.id: c.position for c in scene.schematic.components}
    assert by_id[a.id] == (0.0, 0.0)
    assert by_id[b.id] == (4.0, 0.0)


# ---------------------------------------------------------------------------
# Regression: component vanishes on the SECOND move.
#
# Root cause was that _push() did a full destroy-and-recreate of every item.
# Run inside a mouse-release that handler deleted the item Qt was still
# finalizing its grab on, corrupting interaction state so the next rebuilt
# item never painted. _rebuild_items now *reconciles* (reuses live items),
# so an item survives across repeated moves.
# ---------------------------------------------------------------------------

def _drag(scene: SchematicScene, comp_id: str, dx_gu: float, dy_gu: float):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    from app.canvas.style import GRID_PX

    item = scene._comp_items[comp_id]
    item.setSelected(True)
    comp = next(c for c in scene.schematic.components if c.id == comp_id)
    body_center = scene.gu_to_scene(comp.position[0] + 1.0, comp.position[1])
    press = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    press.setButton(Qt.LeftButton)
    press.setScenePos(body_center)  # body centre, not a pin (avoids auto-wire)
    scene.mousePressEvent(press)
    item.setPos(item.pos().x() + dx_gu * GRID_PX, item.pos().y() + dy_gu * GRID_PX)
    rel = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseRelease)
    rel.setButton(Qt.LeftButton)
    rel.setScenePos(item.pos())
    scene.mouseReleaseEvent(rel)


def test_component_survives_repeated_moves(scene: SchematicScene):
    comp = scene.place_component("R", (79.0, 77.0))

    for i in range(1, 6):
        _drag(scene, comp.id, 2.0, 0.0)
        item = scene._comp_items[comp.id]
        # Model advanced by 2 GU each move.
        assert scene.schematic.components[0].position == (79.0 + 2 * i, 77.0)
        # Item still present, in the scene, visible, and position-synced.
        assert item.scene() is scene
        assert item.isVisible()
        assert item.opacity() == 1.0
        assert item.pos() == scene.gu_to_scene(79.0 + 2 * i, 77.0)


def test_move_reuses_same_item_object(scene: SchematicScene):
    """Reconcile must not destroy the item: identity is preserved across moves."""
    comp = scene.place_component("R", (0.0, 0.0))
    first = scene._comp_items[comp.id]
    _drag(scene, comp.id, 1.0, 0.0)
    _drag(scene, comp.id, 1.0, 0.0)
    # Same Python object reused — proof there was no destructive rebuild.
    assert scene._comp_items[comp.id] is first


def test_reconcile_removes_deleted_and_keeps_others(scene: SchematicScene):
    a = scene.place_component("R", (0.0, 0.0))
    b = scene.place_component("R", (4.0, 0.0))
    item_b = scene._comp_items[b.id]

    scene._comp_items[a.id].setSelected(True)
    scene.delete_selected()

    # a's item is gone; b's item is the *same* object (untouched by the diff).
    assert a.id not in scene._comp_items
    assert scene._comp_items[b.id] is item_b

    # Undo restores a with a fresh item; b still the same object.
    scene.undo()
    assert a.id in scene._comp_items
    assert scene._comp_items[b.id] is item_b


def test_wire_item_refreshed_not_recreated(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    w = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    wire_item = scene._wire_items[w.id]
    # A later unrelated command must not recreate the existing wire item.
    scene.place_component("C", (5.0, 0.0))
    assert scene._wire_items[w.id] is wire_item


# ---------------------------------------------------------------------------
# Wire preview ghost + grid-node anchors (WIRE mode)
# ---------------------------------------------------------------------------

from PySide6.QtCore import Qt, QPointF  # noqa: E402
from PySide6.QtWidgets import QGraphicsSceneMouseEvent  # noqa: E402


def _wire_press(scene: SchematicScene, gu, shift=False):
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    e.setButton(Qt.LeftButton)
    e.setScenePos(scene.gu_to_scene(*gu))
    if shift:
        e.setModifiers(Qt.ShiftModifier)
    scene.mousePressEvent(e)


def _wire_move(scene: SchematicScene, gu, shift=False):
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseMove)
    e.setScenePos(scene.gu_to_scene(*gu))
    if shift:
        e.setModifiers(Qt.ShiftModifier)
    scene.mouseMoveEvent(e)


def test_snap_target_pin_vs_grid_node(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))  # pins (0,0),(2,0)
    pt, is_pin = scene.wire_snap_target((2.05, 0.0))
    assert pt == (2.0, 0.0) and is_pin is True
    pt, is_pin = scene.wire_snap_target((4.0, 0.0))
    assert pt == (4.0, 0.0) and is_pin is False


def test_wire_preview_spawns_and_tracks(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    scene.enter_wire_mode()
    assert scene._wire_preview is None  # nothing until a wire is begun

    _wire_press(scene, (2.0, 0.0))       # start at a pin
    assert scene._wire_preview is not None

    _wire_move(scene, (4.0, 2.0))
    p = scene._wire_preview
    assert p.cursor == (4.0, 2.0)
    # committed start vertex is carried in the preview points
    assert (2.0, 0.0) in p.points


def test_wire_preview_pin_marker(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))   # pins (0,0),(2,0)
    scene.place_component("R", (6.0, 0.0))   # pins (6,0),(8,0)
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))

    _wire_move(scene, (4.0, 1.0))
    assert scene._wire_preview.cursor_is_pin is False
    _wire_move(scene, (6.0, 0.0))            # over the other pin
    assert scene._wire_preview.cursor_is_pin is True


def test_wire_preview_dominant_axis_route(scene: SchematicScene):
    """The ghost corner follows the longer leg (spec §6.4); no key flips it."""
    scene.place_component("R", (0.0, 0.0))
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))

    # |dy|=3 > |dx|=2 → vertical-first: corner at (2,3).
    _wire_move(scene, (4.0, 3.0))
    p = scene._wire_preview
    assert list(p.points) + [p.cursor] == [(2.0, 0.0), (2.0, 3.0), (4.0, 3.0)]

    # |dx|=3 > |dy|=2 → horizontal-first: corner at (5,0).
    _wire_move(scene, (5.0, 2.0))
    p = scene._wire_preview
    assert list(p.points) + [p.cursor] == [(2.0, 0.0), (5.0, 0.0), (5.0, 2.0)]


def test_wire_preview_ignores_shift(scene: SchematicScene):
    """Regression: the Shift route-toggle was removed; modifier has no effect."""
    scene.place_component("R", (0.0, 0.0))
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))

    _wire_move(scene, (4.0, 3.0), shift=False)
    p = scene._wire_preview
    without = list(p.points) + [p.cursor]

    _wire_move(scene, (4.0, 3.0), shift=True)
    p = scene._wire_preview
    with_shift = list(p.points) + [p.cursor]

    assert without == with_shift


def test_grid_node_anchor_adds_vertex_without_terminating(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))   # pin at (2,0)
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))           # start at pin
    _wire_press(scene, (4.0, 0.0))           # empty grid node → anchor
    # Wire not committed yet; anchor recorded; preview still alive.
    assert scene.schematic.wires == []
    assert (4.0, 0.0) in scene._wire_pts
    assert scene._wire_preview is not None


def test_pin_click_terminates_and_clears_preview(scene: SchematicScene):
    # B is placed so its 'in' pin (6,2) is off the A-row, making the routed
    # anchor a genuine corner that survives simplification.
    scene.place_component("R", (0.0, 0.0))   # pins (0,0),(2,0)
    scene.place_component("R", (6.0, 2.0))   # pins (6,2),(8,2)
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))           # start at pin A
    _wire_press(scene, (6.0, 0.0))           # grid-node anchor (real corner)
    _wire_press(scene, (6.0, 2.0))           # pin B → terminate

    assert len(scene.schematic.wires) == 1
    assert scene.schematic.wires[0].points == [(2.0, 0.0), (6.0, 0.0), (6.0, 2.0)]
    assert scene._wire_pts == []
    assert scene._wire_preview is None


def test_collinear_anchor_is_simplified_away(scene: SchematicScene):
    """An anchor dropped on a straight run is removed (minimal node count)."""
    scene.place_component("R", (0.0, 0.0))   # pins (0,0),(2,0)
    scene.place_component("R", (6.0, 0.0))   # pins (6,0),(8,0)
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))           # start at pin A
    _wire_press(scene, (4.0, 0.0))           # collinear anchor on the straight run
    _wire_press(scene, (6.0, 0.0))           # pin B → terminate

    assert len(scene.schematic.wires) == 1
    # The redundant (4,0) midpoint is dropped — just a single segment.
    assert scene.schematic.wires[0].points == [(2.0, 0.0), (6.0, 0.0)]


def test_escape_clears_wire_preview(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))
    assert scene._wire_preview is not None
    scene.cancel_current()
    assert scene._wire_pts == []
    assert scene._wire_preview is None


def test_mode_change_clears_wire_preview(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))
    assert scene._wire_preview is not None
    scene.enter_select_mode()
    assert scene._wire_preview is None


def test_double_click_terminates_on_empty_space(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))   # pin (2,0)
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))           # start at pin

    dbl = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseDoubleClick)
    dbl.setButton(Qt.LeftButton)
    dbl.setScenePos(scene.gu_to_scene(5.0, 0.0))
    scene.mouseDoubleClickEvent(dbl)

    assert len(scene.schematic.wires) == 1
    assert scene._wire_preview is None


# ---------------------------------------------------------------------------
# Wire vertex dragging (SELECT mode)
# ---------------------------------------------------------------------------

def _sel_press(scene: SchematicScene, gu):
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    e.setButton(Qt.LeftButton)
    e.setScenePos(scene.gu_to_scene(*gu))
    scene.mousePressEvent(e)


def _sel_move(scene: SchematicScene, gu):
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseMove)
    e.setScenePos(scene.gu_to_scene(*gu))
    scene.mouseMoveEvent(e)


def _sel_release(scene: SchematicScene, gu):
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseRelease)
    e.setButton(Qt.LeftButton)
    e.setScenePos(scene.gu_to_scene(*gu))
    scene.mouseReleaseEvent(e)


def _wire_with_pin_endpoint(scene: SchematicScene):
    """Resistor at (0,0) (pin at 2,0); wire from that pin to a corner to a free
    end. Returns the wire."""
    scene.place_component("R", (0.0, 0.0))
    scene.add_wire([(2.0, 0.0), (2.0, 3.0), (5.0, 3.0)])
    return scene.schematic.wires[0]


def test_vertex_hit_test_finds_draggable_corner(scene: SchematicScene):
    w = _wire_with_pin_endpoint(scene)
    hit = scene.wire_vertex_at(scene.gu_to_scene(2.0, 3.0))   # the corner
    assert hit == (w.id, 1)


def test_vertex_hit_test_skips_pin_endpoint(scene: SchematicScene):
    _wire_with_pin_endpoint(scene)
    # (2,0) is the wire endpoint sitting on the resistor's pin → not draggable.
    assert scene.wire_vertex_at(scene.gu_to_scene(2.0, 0.0)) is None


def test_vertex_hit_test_free_endpoint_draggable(scene: SchematicScene):
    w = _wire_with_pin_endpoint(scene)
    hit = scene.wire_vertex_at(scene.gu_to_scene(5.0, 3.0))   # free end
    assert hit == (w.id, 2)


def test_locked_indices_marked_on_item(scene: SchematicScene):
    w = _wire_with_pin_endpoint(scene)
    item = scene._wire_items[w.id]
    # Only the pin endpoint (index 0) is locked.
    assert item.locked_indices == {0}


def test_drag_corner_reshapes_wire(scene: SchematicScene):
    w = _wire_with_pin_endpoint(scene)
    _sel_press(scene, (2.0, 3.0))            # grab the corner
    assert scene._vertex_drag is not None
    _sel_move(scene, (4.0, 1.0))             # live preview
    _sel_release(scene, (4.0, 1.0))          # commit

    assert scene._vertex_drag is None
    pts = scene.schematic.wires[0].points
    assert pts[0] == (2.0, 0.0)              # pin endpoint unchanged
    assert pts[-1] == (5.0, 3.0)             # far endpoint unchanged
    # path stays Manhattan
    for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
        assert x0 == x1 or y0 == y1


def test_vertex_drag_preview_is_manhattan(scene: SchematicScene):
    """Live preview during a vertex drag must stay Manhattan (no diagonal segments).

    Regression: before the fix, _preview_vertex_drag moved the vertex raw
    without inserting elbows, so adjacent segments went diagonal until release.
    """
    _wire_with_pin_endpoint(scene)
    wire_id = scene.schematic.wires[0].id
    _sel_press(scene, (2.0, 3.0))            # grab the corner
    assert scene._vertex_drag is not None
    _sel_move(scene, (4.0, 1.0))             # move to a position that would be diagonal
    item = scene._wire_items[wire_id]
    preview = item._preview_points
    assert preview is not None, "preview should be set during drag"
    for (x0, y0), (x1, y1) in zip(preview, preview[1:]):
        assert x0 == x1 or y0 == y1, f"diagonal segment in preview: ({x0},{y0})→({x1},{y1})"
    _sel_release(scene, (4.0, 1.0))


def test_vertex_drag_preview_is_simplified(scene: SchematicScene):
    """Preview during vertex drag must not contain redundant collinear vertices.

    Regression: before the fix, simplify_points was not called on the preview
    point list, so dragging a vertex to a collinear position left a redundant
    intermediate vertex visible in the ghost.

    Wire: (0,0)→(2,0)→(2,3). Drag the corner (2,0) to (2,1.5): the new
    point is collinear with its two neighbours on x=2, so the preview should
    simplify to two points — (0,0)→(2,1.5)→(2,3) collapses (2,1.5) away,
    giving (0,0)→(2,3). But (0,0)→(2,3) is diagonal, so the elbow logic
    inserts (2,0) back. Net result: the preview should never have three
    consecutive collinear points.
    """
    w = scene.add_wire([(0.0, 0.0), (2.0, 0.0), (2.0, 3.0)])
    wire_id = w.id

    _sel_press(scene, (2.0, 0.0))          # grab the corner
    assert scene._vertex_drag is not None
    _sel_move(scene, (2.0, 1.5))           # move along the vertical leg → collinear

    item = scene._wire_items[wire_id]
    preview = item._preview_points
    assert preview is not None
    for i in range(len(preview) - 2):
        x0, y0 = preview[i]
        x1, y1 = preview[i + 1]
        x2, y2 = preview[i + 2]
        assert not ((x0 == x1 == x2) or (y0 == y1 == y2)), \
            f"redundant collinear vertex at index {i+1} in preview: {preview}"

    _sel_release(scene, (2.0, 1.5))


def test_ocirc_follows_dragged_endpoint(scene: SchematicScene):
    """Open-circle item tracks a free endpoint as it is dragged.

    Regression: before the fix, the ocirc stayed at the original position
    during a vertex drag preview and only snapped to the new position on release.
    """
    # Free wire: both endpoints are open (no component).
    w = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    wire_id = w.id
    # Both endpoints should have ocirc items initially.
    assert (0.0, 0.0) in scene._open_circle_items
    assert (4.0, 0.0) in scene._open_circle_items

    # Drag the (4,0) endpoint to (4,2).
    _sel_press(scene, (4.0, 0.0))
    assert scene._vertex_drag is not None
    _sel_move(scene, (4.0, 2.0))

    # The ocirc should have moved to (4,2); the old position should be gone.
    assert (4.0, 0.0) not in scene._open_circle_items, \
        "stale ocirc at old endpoint position during drag"
    assert (4.0, 2.0) in scene._open_circle_items, \
        "ocirc missing at new endpoint position during drag"

    _sel_release(scene, (4.0, 2.0))


def test_drag_vertex_is_undoable(scene: SchematicScene):
    w = _wire_with_pin_endpoint(scene)
    original = [tuple(p) for p in w.points]
    _sel_press(scene, (2.0, 3.0))
    _sel_move(scene, (4.0, 1.0))
    _sel_release(scene, (4.0, 1.0))
    assert scene.schematic.wires[0].points != original
    scene.undo()
    assert scene.schematic.wires[0].points == original


def test_press_on_pin_endpoint_does_not_start_drag(scene: SchematicScene):
    _wire_with_pin_endpoint(scene)
    _sel_press(scene, (2.0, 0.0))            # the pin endpoint
    assert scene._vertex_drag is None


def test_drag_release_at_same_spot_is_noop(scene: SchematicScene):
    w = _wire_with_pin_endpoint(scene)
    original = [tuple(p) for p in w.points]
    count_before = scene.undo_stack.undo_count
    _sel_press(scene, (2.0, 3.0))
    _sel_release(scene, (2.0, 3.0))          # released without moving
    assert scene.schematic.wires[0].points == original
    # No extra command pushed for a zero-distance drag.
    assert scene.undo_stack.undo_count == count_before
    # A press+release on a vertex without moving is a plain click: it selects
    # the wire (so it can be deleted) rather than leaving nothing selected.
    assert scene.selected_wire_ids() == [w.id]


def test_click_near_endpoint_selects_short_wire(scene: SchematicScene):
    """A click near a short wire's open end selects it (regression).

    Bug: on a short stub whose vertex-grab zones (±VERTEX_HIT_GU) cover most of
    its length, clicking near either end started a zero-distance vertex "drag"
    that cleared the selection and selected nothing — so wires with open-circle
    ends could not be selected or deleted. A press+release without movement now
    selects the wire.
    """
    # 1-GU stub from a free open end to a T-junction on a vertical wire.
    through = scene.add_wire([(0.0, 0.0), (0.0, 2.0)])  # vertical
    stub = scene.add_wire([(-1.0, 1.0), (0.0, 1.0)])    # stub T-ing into it
    # With split-on-join the vertical wire is now two halves + the stub = 3 wires.
    assert len(scene.schematic.wires) == 3
    assert (-1.0, 1.0) in scene._open_circle_items       # free end is an ocirc

    # Click right on the open-circle end (well inside the vertex-grab zone).
    _sel_press(scene, (-1.0, 1.0))
    _sel_release(scene, (-1.0, 1.0))
    assert scene.selected_wire_ids() == [stub.id]

    # Deleting the stub dissolves the junction → the two halves merge back into
    # one wire whose ID is NOT the original stub id.
    scene.delete_selected()
    assert len(scene.schematic.wires) == 1
    assert stub.id not in {w.id for w in scene.schematic.wires}
    assert through.id not in {w.id for w in scene.schematic.wires}  # original split away
    assert scene.schematic.wires[0].points == [(0.0, 0.0), (0.0, 2.0)]


def test_click_on_segment_near_vertex_does_not_move_it(scene: SchematicScene):
    """Clicking a segment near a vertex must not relocate the vertex (regression).

    Bug: a vertex can be grabbed from up to VERTEX_HIT_GU away, but the
    click-vs-drag test compared the snapped cursor to the vertex's *old*
    position. So clicking the segment between a corner and a pin-locked endpoint
    grabbed the corner and, because the snapped cursor differed from it, "moved"
    the corner onto the pin — spuriously inserting a junction dot and pushing a
    MoveWireVertexCommand. A stationary click (no grid movement) must select
    only, leaving geometry and the undo stack untouched.
    """
    scene.place_component("R", (0.0, 0.0))             # pin at (0,0)
    scene.add_wire([(0.0, 0.0), (0.0, 2.0)])           # vertical through the pin
    stub = scene.add_wire([(-1.0, 0.5), (0.0, 0.5), (0.0, 0.0)])  # corner (0,0.5) → pin
    original = [tuple(p) for p in stub.points]
    junctions_before = sorted(scene._junction_items.keys())
    count_before = scene.undo_stack.undo_count

    # Click on the (0,0.5)→(0,0) segment at (0,0.22): grabs the corner (0,0.5)
    # (the endpoint (0,0) is pin-locked) but the cursor snaps to (0,0).
    _sel_press(scene, (0.0, 0.22))
    _sel_release(scene, (0.0, 0.22))

    assert scene.schematic.wires[-1].points == original   # geometry untouched
    assert scene.undo_stack.undo_count == count_before     # no spurious command
    assert sorted(scene._junction_items.keys()) == junctions_before  # no new dot
    assert scene.selected_wire_ids() == [stub.id]          # selected instead


def test_click_at_t_junction_selects_through_wire_half(scene: SchematicScene):
    """Each half of a split through wire is independently selectable.

    With split-on-join, a T-junction creates two separate wire objects for the
    through wire's halves.  Clicking on the body of each half selects that half
    and not the stub.
    """
    scene.add_wire([(0.0, 0.0), (0.0, 2.0)])          # vertical through wire
    stub = scene.add_wire([(1.0, 1.0), (0.0, 1.0)])   # stub T-ing in at (0,1)

    # Split-on-join: now 3 wires (lower half, upper half, stub).
    assert len(scene.schematic.wires) == 3
    half_ids = {w.id for w in scene.schematic.wires if w.id != stub.id}

    # Click on the lower half body (0,0)→(0,1) at (0,0.3).
    _sel_press(scene, (0.0, 0.3))
    _sel_release(scene, (0.0, 0.3))
    sel = scene.selected_wire_ids()
    assert len(sel) == 1 and sel[0] in half_ids and sel[0] != stub.id

    # Click on the upper half body (0,1)→(0,2) at (0,1.7).
    scene.clearSelection()
    _sel_press(scene, (0.0, 1.7))
    _sel_release(scene, (0.0, 1.7))
    sel = scene.selected_wire_ids()
    assert len(sel) == 1 and sel[0] in half_ids and sel[0] != stub.id


def test_vertex_drag_only_in_select_mode(scene: SchematicScene):
    _wire_with_pin_endpoint(scene)
    scene.enter_wire_mode()
    # In wire mode a press near a vertex starts a NEW wire, not a vertex drag.
    _sel_press(scene, (2.0, 3.0))
    assert scene._vertex_drag is None


# ---------------------------------------------------------------------------
# Deleting selected wires
# ---------------------------------------------------------------------------

def test_delete_selected_wire(scene: SchematicScene):
    w = scene.add_wire([(0.0, 0.0), (3.0, 0.0)])
    scene._wire_items[w.id].setSelected(True)
    scene.delete_selected()
    assert scene.schematic.wires == []
    scene.undo()
    assert [x.id for x in scene.schematic.wires] == [w.id]


def test_delete_selected_wire_and_component(scene: SchematicScene):
    c = scene.place_component("R", (0.0, 0.0))
    w = scene.add_wire([(10.0, 10.0), (12.0, 10.0)])  # unconnected
    scene._comp_items[c.id].setSelected(True)
    scene._wire_items[w.id].setSelected(True)
    scene.delete_selected()
    assert scene.schematic.components == []
    assert scene.schematic.wires == []


def test_selected_wire_ids(scene: SchematicScene):
    w = scene.add_wire([(0.0, 0.0), (3.0, 0.0)])
    assert scene.selected_wire_ids() == []
    scene._wire_items[w.id].setSelected(True)
    assert scene.selected_wire_ids() == [w.id]


def test_delete_nothing_selected_is_noop(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    before = scene.undo_stack.undo_count
    scene.delete_selected()  # nothing selected
    assert scene.undo_stack.undo_count == before


# ---------------------------------------------------------------------------
# Wire ghost while dragging a connected component
# ---------------------------------------------------------------------------

def _begin_component_drag(scene: SchematicScene, comp_id: str):
    item = scene._comp_items[comp_id]
    item.setSelected(True)
    p = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    p.setButton(Qt.LeftButton)
    # Press near the body centre (between the pins), not on a pin — a press
    # right on a free pin now auto-starts a wire.
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


def _setup_two_connected(scene: SchematicScene):
    a = scene.place_component("R", (0.0, 0.0))   # pin (2,0)
    b = scene.place_component("R", (6.0, 0.0))   # pin (6,0)
    w = scene.add_wire([(2.0, 0.0), (6.0, 0.0)])
    return a, b, w


def test_wire_ghost_appears_during_component_drag(scene: SchematicScene):
    a, b, w = _setup_two_connected(scene)
    _begin_component_drag(scene, a.id)
    _drag_component_to(scene, a.id, 0.0, 2.0)

    # The connected wire shows a preview; the model is untouched mid-drag.
    assert w.id in scene._previewed_wire_ids
    item = scene._wire_items[w.id]
    assert item._preview_points is not None
    assert item._preview_points[0] == (2.0, 2.0)     # endpoint followed
    assert item._preview_points[-1] == (6.0, 0.0)    # far end fixed
    assert scene.schematic.wires[0].points == [(2.0, 0.0), (6.0, 0.0)]  # model unchanged


def test_wire_ghost_clears_and_commits_on_release(scene: SchematicScene):
    a, b, w = _setup_two_connected(scene)
    _begin_component_drag(scene, a.id)
    _drag_component_to(scene, a.id, 0.0, 2.0)
    _release_component(scene, a.id)

    # Preview cleared; model committed to the reshaped wire.
    assert scene._previewed_wire_ids == set()
    assert scene._wire_items[w.id]._preview_points is None
    pts = scene.schematic.wires[0].points
    assert pts[0] == (2.0, 2.0)
    assert pts[-1] == (6.0, 0.0)


def test_unconnected_wire_not_ghosted(scene: SchematicScene):
    a, b, w = _setup_two_connected(scene)
    free = scene.add_wire([(10.0, 10.0), (12.0, 10.0)])
    _begin_component_drag(scene, a.id)
    _drag_component_to(scene, a.id, 0.0, 2.0)
    assert free.id not in scene._previewed_wire_ids
    assert scene._wire_items[free.id]._preview_points is None


def test_ghost_matches_committed_geometry(scene: SchematicScene):
    """The ghost path and committed path agree: both are simplified."""
    a, b, w = _setup_two_connected(scene)
    _begin_component_drag(scene, a.id)
    _drag_component_to(scene, a.id, 0.0, 2.0)
    ghost = list(scene._wire_items[w.id]._preview_points)
    _release_component(scene, a.id)
    committed = list(scene.schematic.wires[0].points)
    assert ghost == committed


def test_component_drag_snaps_to_grid_mid_drag(scene: SchematicScene):
    """Component item position snaps to 0.5 GU during drag, not only on release.

    Regression: before the fix, Qt moved items at sub-grid pixel positions until
    release, so the visual position was unsnapped mid-drag.
    """
    from app.canvas.style import GRID_PX

    a = scene.place_component("R", (0.0, 0.0))
    _begin_component_drag(scene, a.id)

    # Move the item to a fractional (off-grid) pixel position and fire mouseMoveEvent.
    item = scene._comp_items[a.id]
    off_grid_x = item.pos().x() + 0.3 * GRID_PX   # 0.3 GU off — not a 0.5 GU boundary
    off_grid_y = item.pos().y() + 0.7 * GRID_PX
    item.setPos(off_grid_x, off_grid_y)
    mv = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseMove)
    mv.setScenePos(QPointF(off_grid_x, off_grid_y))
    mv.setButtons(Qt.LeftButton)
    scene.mouseMoveEvent(mv)

    # After the event, item.pos() must sit exactly on a 0.5 GU grid point.
    snapped = scene.snap_point_gu(item.pos())
    actual = scene.scene_to_gu(item.pos())
    assert actual == snapped, (
        f"item position {actual} is not snapped to grid {snapped} mid-drag"
    )


# ---------------------------------------------------------------------------
# Auto-enter / auto-exit wire mode via pin clicks
# ---------------------------------------------------------------------------

def test_unconnected_pin_at_detects_free_pin(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))   # pins (0,0),(2,0)
    assert scene.unconnected_pin_at(scene.gu_to_scene(0.0, 0.0)) == (0.0, 0.0)
    assert scene.unconnected_pin_at(scene.gu_to_scene(2.0, 0.0)) == (2.0, 0.0)
    # Empty space → no pin.
    assert scene.unconnected_pin_at(scene.gu_to_scene(5.0, 5.0)) is None


def test_unconnected_pin_at_skips_connected_pin(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))         # pin (2,0)
    scene.add_wire([(2.0, 0.0), (4.0, 0.0)])       # attaches a wire to (2,0)
    assert scene.unconnected_pin_at(scene.gu_to_scene(2.0, 0.0)) is None


def test_click_free_pin_enters_wire_mode(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))   # free pin (2,0)
    assert scene.mode == Mode.SELECT
    _sel_press(scene, (2.0, 0.0))
    assert scene.mode == Mode.WIRE
    assert scene._wire_pts == [(2.0, 0.0)]
    assert scene._wire_preview is not None


def test_click_connected_pin_stays_select(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))   # pin (2,0)
    scene.add_wire([(2.0, 0.0), (4.0, 0.0)])
    _sel_press(scene, (2.0, 0.0))
    assert scene.mode == Mode.SELECT          # did not hijack the click


def test_terminate_on_pin_returns_to_select(scene: SchematicScene):
    a = scene.place_component("R", (0.0, 0.0))   # pin (2,0)
    b = scene.place_component("R", (6.0, 0.0))   # pin (6,0)
    _sel_press(scene, (2.0, 0.0))                # auto-enter wire at A.out
    assert scene.mode == Mode.WIRE
    _sel_press(scene, (6.0, 0.0))                # terminate on B.in
    assert scene.mode == Mode.SELECT
    assert len(scene.schematic.wires) == 1
    assert scene.schematic.wires[0].points == [(2.0, 0.0), (6.0, 0.0)]


def test_full_pin_to_pin_roundtrip_via_select(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    scene.place_component("R", (6.0, 2.0))   # pin (6,2): needs a corner
    _sel_press(scene, (2.0, 0.0))            # start at A.out
    _sel_press(scene, (6.0, 2.0))            # end on B.in
    assert scene.mode == Mode.SELECT
    w = scene.schematic.wires[0]
    assert w.points[0] == (2.0, 0.0)
    assert w.points[-1] == (6.0, 2.0)


def test_double_click_empty_space_stays_wire(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))   # free pin (2,0)
    _sel_press(scene, (2.0, 0.0))            # auto-enter wire
    assert scene.mode == Mode.WIRE
    dbl = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseDoubleClick)
    dbl.setButton(Qt.LeftButton)
    dbl.setScenePos(scene.gu_to_scene(4.0, 3.0))   # empty space
    scene.mouseDoubleClickEvent(dbl)
    assert scene.mode == Mode.WIRE           # keep routing
    assert len(scene.schematic.wires) == 1


def test_double_click_on_pin_returns_to_select(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))   # pin (2,0)
    scene.place_component("R", (6.0, 0.0))   # pin (6,0)
    _sel_press(scene, (2.0, 0.0))            # start at A.out
    dbl = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseDoubleClick)
    dbl.setButton(Qt.LeftButton)
    dbl.setScenePos(scene.gu_to_scene(6.0, 0.0))   # on B.in
    scene.mouseDoubleClickEvent(dbl)
    assert scene.mode == Mode.SELECT
    assert len(scene.schematic.wires) == 1


def _dbl_click(scene: SchematicScene, gu):
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseDoubleClick)
    e.setButton(Qt.LeftButton)
    e.setScenePos(scene.gu_to_scene(*gu))
    scene.mouseDoubleClickEvent(e)


def test_double_click_wire_body_enters_wire_mode(scene: SchematicScene):
    """Double-clicking on a wire segment in SELECT mode starts a new wire."""
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    assert scene.mode == Mode.SELECT

    # Double-click at a grid point on the wire body.
    _dbl_click(scene, (2.0, 0.0))

    assert scene.mode == Mode.WIRE
    assert scene._wire_pts == [(2.0, 0.0)]
    assert scene._wire_preview is not None


def test_double_click_wire_commits_splits_on_add(scene: SchematicScene):
    """A wire started via double-click on a wire body splits the target on commit.

    Double-clicking a wire body enters WIRE mode; routing away and finalizing
    (via a second double-click to a free endpoint) splits the original wire.
    """
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])

    _dbl_click(scene, (2.0, 0.0))   # enter WIRE mode from (2,0) on the wire
    assert scene.mode == Mode.WIRE

    # Double-click in WIRE mode commits to a free endpoint.
    _dbl_click(scene, (2.0, 3.0))   # finalize wire (2,0)→(2,3)
    # Mode stays WIRE (free-endpoint finalization).

    # The target wire is now split + the new stub = 3 wires; junction at (2,0).
    assert len(scene.schematic.wires) == 3
    assert (2.0, 0.0) in scene._junction_items


def test_double_click_wire_vertex_enters_wire_mode(scene: SchematicScene):
    """Double-clicking exactly on an existing wire vertex also enters WIRE mode."""
    scene.add_wire([(0.0, 0.0), (0.0, 2.0), (4.0, 2.0)])  # L-wire; corner at (0,2)

    _dbl_click(scene, (0.0, 2.0))

    assert scene.mode == Mode.WIRE
    assert scene._wire_pts == [(0.0, 2.0)]


def test_double_click_empty_space_enters_wire_mode(scene: SchematicScene):
    """Double-clicking blank canvas enters WIRE mode from the snapped grid point."""
    _dbl_click(scene, (2.0, 5.0))   # empty space

    assert scene.mode == Mode.WIRE
    assert scene._wire_pts == [(2.0, 5.0)]
    assert scene._wire_preview is not None


def test_double_click_wire_near_component_enters_wire_mode(scene: SchematicScene):
    """A wire inside a component's bounding box is reachable by double-click.

    Regression: the component double-click check ran first and swallowed the
    event, preventing wire mode from being entered on wires that overlap with
    a component's bounding rect (e.g. the side wires of a series R-L loop).
    """
    # Resistor with pins at (0,0) and (2,0); its bbox spans that area.
    scene.place_component("R", (0.0, 0.0))
    # Vertical wire inside the component's vertical extent, close to the left pin.
    scene.add_wire([(0.0, 0.0), (0.0, 2.0)])

    # Double-click on the wire body at (0, 1.0) — inside the component bbox.
    _dbl_click(scene, (0.0, 1.0))

    assert scene.mode == Mode.WIRE
    assert scene._wire_pts == [(0.0, 1.0)]


def test_vertex_drag_still_wins_over_pin_autostart(scene: SchematicScene):
    """A press on a draggable wire vertex must not auto-start a new wire."""
    scene.place_component("R", (0.0, 0.0))         # pin (2,0)
    scene.add_wire([(2.0, 0.0), (2.0, 3.0)])       # corner at (2,3) draggable
    _sel_press(scene, (2.0, 3.0))                  # grab the corner
    assert scene.mode == Mode.SELECT               # no auto wire-mode
    assert scene._vertex_drag is not None


# ---------------------------------------------------------------------------
# Wire selection hit-area = thin span, not bounding rect
# ---------------------------------------------------------------------------

def _in_wire_shape(scene: SchematicScene, wire_id: str, gu) -> bool:
    item = scene._wire_items[wire_id]
    local = item.mapFromScene(scene.gu_to_scene(*gu))
    return item.shape().contains(local)


def test_wire_shape_hits_on_segment(scene: SchematicScene):
    w = scene.add_wire([(0.0, 0.0), (0.0, 2.0), (2.0, 2.0)])
    assert _in_wire_shape(scene, w.id, (0.0, 1.0))    # vertical leg
    assert _in_wire_shape(scene, w.id, (1.0, 2.0))    # horizontal leg


def test_wire_shape_misses_bbox_interior(scene: SchematicScene):
    """A point inside the bounding rect but off every segment is not a hit."""
    w = scene.add_wire([(0.0, 0.0), (0.0, 2.0), (2.0, 2.0)])
    # (1.5, 0.8) sits inside the L's bounding box but far from both legs.
    assert not _in_wire_shape(scene, w.id, (1.5, 0.8))


def test_wire_does_not_cover_overlapping_component(scene: SchematicScene):
    """The wire's old bbox covered the resistor centre; its shape must not."""
    from app.canvas.items import ComponentItem, WireItem

    scene.place_component("R", (0.0, 0.0))                  # pins (0,0),(2,0)
    scene.add_wire([(0.0, 0.0), (0.0, 2.0), (2.0, 2.0)])    # L overlapping bbox

    pt = scene.gu_to_scene(1.0, 0.0)                        # resistor body centre
    types = {type(i).__name__ for i in scene.items(pt)}
    assert "ResistorItem" in types
    assert "WireItem" not in types


def test_wire_shape_includes_draggable_handles(scene: SchematicScene):
    w = scene.add_wire([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])  # all free vertices
    # The middle vertex handle (a corner) is grabbable → inside the shape.
    assert _in_wire_shape(scene, w.id, (2.0, 0.0))


def test_wire_shape_empty_for_degenerate(scene: SchematicScene):
    from app.canvas.items import WireItem
    from app.schematic.model import Wire

    item = WireItem(Wire(id="x", points=[(0.0, 0.0)]))
    assert item.shape().isEmpty()


# ---------------------------------------------------------------------------
# Wire-to-wire snapping + junction dots on the canvas
# ---------------------------------------------------------------------------

def test_snap_to_existing_wire_vertex(scene: SchematicScene):
    scene.add_wire([(2.0, 2.0), (4.0, 2.0)])
    target, connectable = scene.wire_snap_target((2.1, 2.0))
    assert target == (2.0, 2.0)
    assert connectable is True


def test_snap_onto_wire_segment(scene: SchematicScene):
    scene.add_wire([(0.0, 2.0), (4.0, 2.0)])      # horizontal segment
    # Cursor just above the segment at x=1 snaps onto it at (1,2).
    target, connectable = scene.wire_snap_target((1.0, 2.1))
    assert target == (1.0, 2.0)
    assert connectable is True


def test_pin_snap_takes_priority_over_wire(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))        # pin (2,0)
    scene.add_wire([(2.5, 0.0), (4.0, 0.0)])      # wire vertex near the pin
    target, _ = scene.wire_snap_target((2.1, 0.0))
    assert target == (2.0, 0.0)                   # the pin wins


def test_junction_dot_item_appears_for_three_wires(scene: SchematicScene):
    scene.add_wire([(0.0, 2.0), (2.0, 2.0)])
    scene.add_wire([(2.0, 0.0), (2.0, 2.0)])
    assert scene._junction_items == {}            # only two so far
    scene.add_wire([(2.0, 2.0), (4.0, 2.0)])      # third → junction
    assert (2.0, 2.0) in scene._junction_items


def test_junction_dot_item_removed_when_wire_deleted(scene: SchematicScene):
    a = scene.add_wire([(0.0, 2.0), (2.0, 2.0)])
    scene.add_wire([(2.0, 0.0), (2.0, 2.0)])
    c = scene.add_wire([(2.0, 2.0), (4.0, 2.0)])
    assert (2.0, 2.0) in scene._junction_items
    # Delete one wire → back to two → dot disappears.
    scene._wire_items[c.id].setSelected(True)
    scene.delete_selected()
    assert scene._junction_items == {}


def test_route_third_wire_into_junction_via_clicks(scene: SchematicScene):
    """End-to-end: click-route a 3rd wire onto an existing meeting point."""
    scene.add_wire([(0.0, 2.0), (2.0, 2.0)])
    scene.add_wire([(2.0, 2.0), (2.0, 4.0)])
    scene.enter_wire_mode()
    # Start a new wire to the right, terminate ON the existing vertex (2,2).
    _wire_press(scene, (4.0, 2.0))
    _wire_press(scene, (2.0, 2.0))                # snaps to wire vertex → junction
    assert len(scene.schematic.wires) == 3
    assert (2.0, 2.0) in scene._junction_items
    # Terminating on connectable geometry returns to SELECT.
    assert scene.mode == Mode.SELECT


# ---------------------------------------------------------------------------
# Mid-segment connection splits the target wire (+ junction dot)
# ---------------------------------------------------------------------------

def test_connect_to_mid_segment_splits_target(scene: SchematicScene):
    a = scene.add_wire([(0.0, 2.0), (4.0, 2.0)])         # single segment
    assert scene.schematic.wires[0].points == [(0.0, 2.0), (4.0, 2.0)]

    # New wire T's into the middle at (2,2).
    scene.add_wire([(2.0, 2.0), (2.0, 5.0)])

    # The existing wire is now two independent halves; original ID is gone.
    assert len(scene.schematic.wires) == 3
    assert all(w.id != a.id for w in scene.schematic.wires)
    pts = sorted([w.points for w in scene.schematic.wires])
    assert [(0.0, 2.0), (2.0, 2.0)] in pts
    assert [(2.0, 2.0), (4.0, 2.0)] in pts
    assert (2.0, 2.0) in scene._junction_items


def test_mid_segment_split_is_one_undo(scene: SchematicScene):
    a = scene.add_wire([(0.0, 2.0), (4.0, 2.0)])
    scene.add_wire([(2.0, 2.0), (2.0, 5.0)])
    assert len(scene.schematic.wires) == 3

    scene.undo()   # single undo reverses split + new wire
    assert len(scene.schematic.wires) == 1
    assert scene.schematic.wires[0].id == a.id
    assert scene.schematic.wires[0].points == [(0.0, 2.0), (4.0, 2.0)]
    assert scene._junction_items == {}


def test_connect_to_existing_endpoint_does_not_split(scene: SchematicScene):
    """Connecting at an existing wire endpoint does not split the wire."""
    a = scene.add_wire([(0.0, 2.0), (2.0, 2.0)])         # endpoint at (2,2)
    scene.add_wire([(2.0, 2.0), (2.0, 5.0)])             # meet at the endpoint
    wire_a = next(w for w in scene.schematic.wires if w.id == a.id)
    assert wire_a.points == [(0.0, 2.0), (2.0, 2.0)]     # unchanged, still 2 wires
    assert len(scene.schematic.wires) == 2


def test_connect_to_wire_corner_splits_l_wire(scene: SchematicScene):
    """Connecting a new wire to the elbow of an L-wire splits it into two straights.

    An L-wire (78,0)→(78,2)→(81,2) has a corner at (78,2).  A new wire
    connecting at that corner must split the L-wire so each leg becomes
    independently selectable and deletable.
    """
    elbow = scene.add_wire([(78.0, 0.0), (78.0, 2.0), (81.0, 2.0)])  # L-wire
    assert len(scene.schematic.wires) == 1

    scene.add_wire([(78.0, 2.0), (75.0, 2.0)])   # connects at the elbow

    # L-wire is now two independent straight wires.
    assert len(scene.schematic.wires) == 3
    assert all(w.id != elbow.id for w in scene.schematic.wires)
    endpoint_sets = [frozenset(w.points) for w in scene.schematic.wires]
    assert frozenset([(75.0, 2.0), (78.0, 2.0)]) in endpoint_sets
    assert frozenset([(78.0, 0.0), (78.0, 2.0)]) in endpoint_sets
    assert frozenset([(78.0, 2.0), (81.0, 2.0)]) in endpoint_sets
    assert (78.0, 2.0) in scene._junction_items


def test_connect_to_wire_corner_split_is_one_undo(scene: SchematicScene):
    """Corner-split + new wire is a single undoable action."""
    elbow = scene.add_wire([(78.0, 0.0), (78.0, 2.0), (81.0, 2.0)])
    scene.add_wire([(78.0, 2.0), (75.0, 2.0)])
    assert len(scene.schematic.wires) == 3

    scene.undo()
    assert len(scene.schematic.wires) == 1
    assert scene.schematic.wires[0].id == elbow.id
    assert scene.schematic.wires[0].points == [(78.0, 0.0), (78.0, 2.0), (81.0, 2.0)]
    assert scene._junction_items == {}


def test_mid_segment_split_codegen_has_circ(scene: SchematicScene):
    from app.codegen.circuitikz import generate

    scene.add_wire([(0.0, 2.0), (4.0, 2.0)])
    scene.add_wire([(2.0, 2.0), (2.0, 5.0)])
    src = generate(scene.schematic)
    assert r"\node[circ] at (2,2) {};" in src


# ---------------------------------------------------------------------------
# Dragging an existing wire vertex onto another wire's segment → split + dot
# ---------------------------------------------------------------------------

def test_drag_vertex_onto_segment_splits_target(scene: SchematicScene):
    target = scene.add_wire([(0.0, 2.0), (4.0, 2.0)])    # single segment
    mover = scene.add_wire([(2.0, 5.0), (2.0, 3.0)])     # free end at (2,3)

    # Grab mover's (2,3) endpoint and drop it just above the target line.
    _sel_press(scene, (2.0, 3.0))
    assert scene._vertex_drag is not None
    _sel_release(scene, (2.0, 2.1))                      # snaps onto (2,2)

    # Target is now two independent halves; original ID is gone.
    assert all(w.id != target.id for w in scene.schematic.wires)
    half_pts = sorted([
        w.points for w in scene.schematic.wires if w.id != mover.id
    ])
    assert [(0.0, 2.0), (2.0, 2.0)] in half_pts
    assert [(2.0, 2.0), (4.0, 2.0)] in half_pts
    mov = next(w for w in scene.schematic.wires if w.id == mover.id)
    assert mov.points[-1] == (2.0, 2.0)                          # moved onto it
    assert (2.0, 2.0) in scene._junction_items


def test_drag_vertex_onto_segment_is_one_undo(scene: SchematicScene):
    target = scene.add_wire([(0.0, 2.0), (4.0, 2.0)])
    scene.add_wire([(2.0, 5.0), (2.0, 3.0)])
    _sel_press(scene, (2.0, 3.0))
    _sel_release(scene, (2.0, 2.1))
    assert (2.0, 2.0) in scene._junction_items

    scene.undo()   # single undo reverses split + move
    tgt = next(w for w in scene.schematic.wires if w.id == target.id)
    assert tgt.points == [(0.0, 2.0), (4.0, 2.0)]
    assert scene._junction_items == {}


def test_drag_vertex_does_not_split_own_wire(scene: SchematicScene):
    """An L-wire's corner dragged along its own other leg must not self-split."""
    w = scene.add_wire([(0.0, 0.0), (0.0, 4.0), (4.0, 4.0)])
    before = len(w.points)
    # Drag the corner (0,4) somewhere that is NOT on this wire; just verify no
    # spurious self-split occurs and the wire stays a single object.
    _sel_press(scene, (0.0, 4.0))
    _sel_release(scene, (2.0, 6.0))
    same = next(x for x in scene.schematic.wires if x.id == w.id)
    # No extra wire created; still one wire, model valid.
    assert len([x for x in scene.schematic.wires if x.id == w.id]) == 1
    from app.schematic.validate import validate
    assert validate(scene.schematic) == []


def test_drag_vertex_onto_existing_vertex_no_duplicate(scene: SchematicScene):
    target = scene.add_wire([(0.0, 2.0), (2.0, 2.0)])    # endpoint at (2,2)
    scene.add_wire([(2.0, 5.0), (2.0, 3.0)])
    _sel_press(scene, (2.0, 3.0))
    _sel_release(scene, (2.0, 2.1))                      # snaps to the (2,2) endpoint
    tgt = next(w for w in scene.schematic.wires if w.id == target.id)
    assert tgt.points == [(0.0, 2.0), (2.0, 2.0)]        # unchanged, no split


# ---------------------------------------------------------------------------
# Options child item (LabelTextItem)
# ---------------------------------------------------------------------------

def test_options_item_created(scene: SchematicScene):
    """Each ComponentItem has a single LabelTextItem child for the options string."""
    from app.canvas.items import LabelTextItem

    comp = scene.place_component("R", (0.0, 0.0))
    item = scene._comp_items[comp.id]
    assert isinstance(item._options_item, LabelTextItem)


def test_empty_options_item_hidden(scene: SchematicScene):
    """The options child is hidden when options is empty."""
    comp = scene.place_component("R", (0.0, 0.0))
    item = scene._comp_items[comp.id]
    assert not item._options_item.isVisible()


def test_non_empty_options_item_visible(scene: SchematicScene):
    """Setting options makes the child visible with the raw string as text."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$, v=$5V$")
    item = scene._comp_items[comp.id]
    assert item._options_item.isVisible()
    assert item._options_item.toPlainText() == "l=$R_1$, v=$5V$"


def test_cleared_options_hides_child(scene: SchematicScene):
    """Setting options to empty hides the child item."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    scene.edit_component_options(comp.id, "")
    item = scene._comp_items[comp.id]
    assert not item._options_item.isVisible()


def test_options_item_above_bbox(scene: SchematicScene):
    """The options child is positioned above the component bbox."""
    from app.canvas.style import GRID_PX

    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    bbox_top_px = -0.5 * GRID_PX  # resistor bbox y0 = -0.5 GU
    assert item._options_item.pos().y() < bbox_top_px


def test_options_undo_hides_child(scene: SchematicScene):
    """Undoing an options edit hides the child item again."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    assert scene._comp_items[comp.id]._options_item.isVisible()
    scene.undo()
    assert not scene._comp_items[comp.id]._options_item.isVisible()


def test_options_item_begin_edit(scene: SchematicScene):
    """begin_options_edit activates the in-place editor."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    item.begin_options_edit()
    assert item._options_item.is_editing


def test_options_commit_updates_model(scene: SchematicScene):
    """Committing an in-place edit pushes an EditCommand and updates the model."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]

    item._options_item.begin_edit()
    item._options_item.setPlainText("l=$R_2$, v=$10V$")
    item._options_item.end_edit(commit=True)

    updated = next(c for c in scene.schematic.components if c.id == comp.id)
    assert updated.options == "l=$R_2$, v=$10V$"


def test_options_cancel_does_not_update_model(scene: SchematicScene):
    """Cancelling an in-place edit leaves the model unchanged."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]

    item._options_item.begin_edit()
    item._options_item.setPlainText("CHANGED")
    item._options_item.end_edit(commit=False)

    unchanged = next(c for c in scene.schematic.components if c.id == comp.id)
    assert unchanged.options == "l=$R_1$"


def test_ghost_hides_options_item(scene: SchematicScene):
    """The options child is hidden in ghost (placement preview) state."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    item.set_ghost(True)
    assert not item._options_item.isVisible()
    item.set_ghost(False)
    assert item._options_item.isVisible()
