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
# Document label preamble (siunitx on canvas)
# ---------------------------------------------------------------------------

def test_set_schematic_mirrors_siunitx_into_label_renderer(scene: SchematicScene):
    """Loading a document forwards its siunitx flag to the canvas label renderer
    so \\qty/\\unit macros typeset on canvas (issue #29)."""
    from app.preview import mathrender
    from app.schematic.model import Schematic
    try:
        scene.set_schematic(Schematic(version="0.5", name="on", siunitx=True))
        assert "siunitx" in mathrender._label_preamble

        scene.set_schematic(Schematic(version="0.5", name="off", siunitx=False))
        assert mathrender._label_preamble == ""
    finally:
        mathrender.set_label_preamble("")


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
    # Placement snaps an off-grid request to the nearest 0.25 GU point.
    comp = scene.place_component("R", (0.7, 1.24))
    assert comp.position == (0.75, 1.25)


@pytest.mark.parametrize(
    "raw,expected",
    [(0.0, 0.0), (0.12, 0.0), (0.13, 0.25), (0.26, 0.25), (0.7, 0.75), (0.76, 0.75)],
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
    # PIN_SNAP_GU is 0.125 GU; just inside snaps, just outside does not.
    assert scene._nearest_pin_gu((0.1, 0.0)) == (0.0, 0.0)
    assert scene._nearest_pin_gu((0.2, 0.0)) is None


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


def test_move_onto_connected_lead_creates_no_degenerate_wire(scene: SchematicScene):
    """Moving a component so its pin lands on a wire it is already connected to
    must not carve off a degenerate single-point wire (regression).

    The split point is computed against the wire's *pre-move* geometry (where the
    pin lands mid-segment), but the move first reshapes that wire's endpoint onto
    the same point — so splitting there would leave a 1-point stub. `SplitWireCommand`
    must no-op when the split would land on an (reshaped) endpoint.
    """
    comp = scene.place_component("R", (0.0, 0.0))   # pins (0,0),(2,0)
    scene.add_wire([(2.0, 0.0), (2.0, 1.0)])         # lead off the right pin
    item = scene._comp_items[comp.id]
    item.setSelected(True)
    scene.nudge_selected(0.0, 0.25)                  # right pin (2,0) → (2,0.25)
    assert all(len(w.points) >= 2 for w in scene.schematic.wires), (
        "a move must never leave a degenerate single-point wire"
    )
    # And it stays clean across undo/redo.
    scene.undo()
    scene.redo()
    assert all(len(w.points) >= 2 for w in scene.schematic.wires)


def test_drag_onto_junction_and_back_restores_topology(scene: SchematicScene):
    """Dragging a component pin onto a wire junction and back must not leave an
    erroneous junction dot or tear the net, and the connection re-stretches so the
    original topology is restored (regression — the reported boost bug + the
    re-stretch follow-up).

    A resistor's right pin connects (via a short lead) to a node where a rail is
    split in two. Nudging the resistor onto the node collapses the lead; nudging it
    back leaves the rail intact, creates no dot at the pin, and re-grows the lead.
    """
    from app.schematic.model import Wire, junction_points

    comp = scene.place_component("R", (0.0, 0.0))     # pins (0,0),(2,0)
    # Lead from the right pin (2,0) down to the rail node (2,1), and the rail
    # split into two halves meeting at (2,1) — a degree-3 junction.
    scene.add_wire([(2.0, 0.0), (2.0, 1.0)])
    scene.schematic.wires.append(Wire(id="railL", points=[(0.0, 1.0), (2.0, 1.0)]))
    scene.schematic.wires.append(Wire(id="railR", points=[(2.0, 1.0), (4.0, 1.0)]))
    scene._rebuild_items()

    item = scene._comp_items[comp.id]
    item.setSelected(True)
    scene.nudge_selected(0.0, 1.0)    # pin (2,0) → (2,1): lands on the node
    item.setSelected(True)
    scene.nudge_selected(0.0, -1.0)   # back: pin (2,1) → (2,0)

    js = junction_points(scene.schematic)
    assert (2.0, 0.0) not in js, "no phantom junction dot at the returned pin"
    # The rail halves are untouched (not dragged off the node).
    rails = {w.id: w.points for w in scene.schematic.wires if w.id in ("railL", "railR")}
    assert rails["railL"] == [(0.0, 1.0), (2.0, 1.0)]
    assert rails["railR"] == [(2.0, 1.0), (4.0, 1.0)]
    # The connection re-grew: a lead now runs from the node (2,1) back to the pin
    # (2,0), so the component is still wired (and the node is a junction again).
    others = [w.points for w in scene.schematic.wires if w.id not in ("railL", "railR")]
    assert any(set(p) == {(2.0, 0.0), (2.0, 1.0)} for p in others), \
        "expected a re-stretched lead from the node back to the pin"
    assert (2.0, 1.0) in js
    # No degenerate wires either.
    assert all(len(w.points) >= 2 for w in scene.schematic.wires)


def test_degenerate_wire_renders_selectable_x(scene: SchematicScene):
    """A degenerate single-point wire (which an old file might still contain) is
    shown as a selectable, deletable red ✕ rather than silently invisible."""
    from app.schematic.model import Wire

    scene.place_component("R", (0.0, 0.0))
    scene.schematic.wires.append(Wire(id="deg", points=[(4.0, 0.0)]))
    scene._rebuild_items()
    item = scene._wire_items["deg"]
    # Selectable (non-empty hit shape) and has a finite bounding rect to paint into.
    assert not item.shape().isEmpty()
    assert item.boundingRect().isValid() and not item.boundingRect().isEmpty()
    # Select + delete removes it; undo brings it back.
    item.setSelected(True)
    scene.delete_selected()
    assert not any(w.id == "deg" for w in scene.schematic.wires)
    scene.undo()
    assert any(w.id == "deg" for w in scene.schematic.wires)


def test_degenerate_wire_no_phantom_junction_during_drag(scene: SchematicScene):
    """A degenerate single-point wire must not create a junction dot during a
    component drag (regression). ``junction_points`` skips ``len < 2`` wires, and
    the drag preview must do the same — otherwise a tiny drag (whose rounded pin
    lands back on the point) pushes the degree to 3 and a phantom dot flickers in.
    """
    from app.canvas.style import GRID_PX
    from app.schematic.model import Wire

    comp = scene.place_component("R", (0.0, 0.0))  # pins (0,0),(2,0)
    scene.add_wire([(2.0, 0.0), (2.0, 2.0)])       # real wire off the (2,0) pin
    # Inject a degenerate single-point wire at the (2,0) pin (the kind that can
    # linger in a saved file). Statically it forms no junction (degree 2).
    scene.schematic.wires.append(Wire(id="deg", points=[(2.0, 0.0)]))
    scene._rebuild_items()
    assert (2.0, 0.0) not in scene._junction_items

    # Drag the resistor a sub-cell amount so the rounded pin stays at (2,0).
    item = scene._comp_items[comp.id]
    scene._drag.drag_start = {comp.id: comp.position}
    scene._drag.drag_wire_ids = set()
    item.setPos(0.1 * GRID_PX, 0.0)
    scene._drag.preview_component_drag()
    assert (2.0, 0.0) not in scene._junction_items


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


def test_view_fit_ignores_origin_helper_items(scene: SchematicScene):
    """Regression: fit-to-schematic bounds only the *visible* schematic, not the
    hidden helper items (label editors / empty wire-label items) pinned at the
    scene origin. Those previously inflated the rect from (0,0) to a schematic
    placed far from the origin, making the fit zoom way out (~9%)."""
    view = SchematicView(scene)
    view.resize(800, 500)
    view.show()
    # A small cluster placed far from the origin, plus a wire (whose hidden
    # label editor sits at scene (0,0)).
    scene.place_component("R", (60.0, 60.0))
    scene.place_component("C", (64.0, 60.0))
    scene.add_wire([(62.0, 60.0), (64.0, 60.0)])
    view.fit_to_schematic()
    # The tight cluster fills the viewport at a healthy zoom; the origin-inflated
    # bug collapsed it to ~0.1.
    assert view.zoom > 0.5


def test_view_placement_shortcuts(scene: SchematicScene, monkeypatch):
    """From the Select tool with nothing selected, a mapped plain key starts
    placing its component — including v/i → the voltage/current annotations (§10.2)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view = SchematicView(scene)
    view.set_placement_shortcuts({"r": "R", "c": "C", "v": "open", "i": "short"})
    started: list[str] = []
    monkeypatch.setattr(scene, "start_placement", lambda k: started.append(k))

    def press(key, text):
        view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier, text))

    press(Qt.Key_R, "r")            # nothing selected → r places a resistor
    press(Qt.Key_C, "c")
    press(Qt.Key_V, "v")
    press(Qt.Key_I, "i")
    assert started == ["R", "C", "open", "short"]


def test_view_r_places_not_rotates(scene: SchematicScene, monkeypatch):
    """Plain `r` always places a resistor now (rotate moved to Ctrl+R), even with a
    component selected — it never rotates (§10.2)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view = SchematicView(scene)
    view.set_placement_shortcuts({"r": "R"})
    comp = scene.place_component("R", (5.0, 5.0))
    scene._comp_items[comp.id].setSelected(True)
    placed: list[str] = []
    rotated: list[bool] = []
    monkeypatch.setattr(scene, "start_placement", lambda k: placed.append(k))
    monkeypatch.setattr(scene, "rotate_selected_cw", lambda: rotated.append(True))

    view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_R, Qt.NoModifier, "r"))
    assert placed == ["R"] and rotated == []


def test_view_placement_key_swaps_active_ghost(scene: SchematicScene):
    """Pressing a mapped key while a ghost is up (PLACE mode) swaps it to the new
    kind, so the user can change their mind without returning to the palette (§10.2)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view = SchematicView(scene)
    view.set_placement_shortcuts({"r": "R", "c": "C"})
    scene.start_placement("R")  # resistor ghost (real placement, not mocked)
    assert scene.mode == Mode.PLACE and scene._place_kind == "R"

    view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_C, Qt.NoModifier, "c"))
    assert scene.mode == Mode.PLACE and scene._place_kind == "C"


def test_view_placement_keys_inactive_while_wiring(scene: SchematicScene, monkeypatch):
    """A mapped key does nothing in Wire mode, so it never interrupts wire routing."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view = SchematicView(scene)
    view.set_placement_shortcuts({"c": "C"})
    scene.enter_wire_mode()
    placed: list[str] = []
    monkeypatch.setattr(scene, "start_placement", lambda k: placed.append(k))

    view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_C, Qt.NoModifier, "c"))
    assert placed == []


def test_view_cursor_reflects_mode(scene: SchematicScene):
    """The canvas cursor signals the active tool: a crosshair while wiring or
    placing, an open hand for pan, the default arrow for select (§6.1)."""
    from PySide6.QtCore import Qt

    view = SchematicView(scene)
    scene.enter_wire_mode()
    assert view.cursor().shape() == Qt.CrossCursor
    scene.start_placement("R")
    assert view.cursor().shape() == Qt.CrossCursor
    scene.enter_pan_mode()
    assert view.cursor().shape() == Qt.OpenHandCursor
    scene.enter_select_mode()
    assert view.cursor().shape() == Qt.ArrowCursor


def test_view_space_pan_restores_wire_cursor(scene: SchematicScene):
    """A transient Space-pan in Wire mode restores the crosshair on release, not the
    default arrow (regression — the cursor must keep signalling Wire mode)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view = SchematicView(scene)
    scene.enter_wire_mode()
    view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    assert view.cursor().shape() == Qt.OpenHandCursor
    view.keyReleaseEvent(QKeyEvent(QKeyEvent.KeyRelease, Qt.Key_Space, Qt.NoModifier))
    assert view.cursor().shape() == Qt.CrossCursor


def test_view_arrow_nudge_step(scene: SchematicScene):
    """Arrow keys nudge by one 0.25 GU minor-grid cell (§3.1)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    view = SchematicView(scene)
    comp = scene.place_component("R", (5.0, 5.0))
    scene._comp_items[comp.id].setSelected(True)

    def press(key):
        view.keyPressEvent(QKeyEvent(QKeyEvent.KeyPress, key, Qt.NoModifier))

    press(Qt.Key_Down)
    assert scene._component_by_id(comp.id).position == (5.0, 5.25)
    press(Qt.Key_Right)
    assert scene._component_by_id(comp.id).position == (5.25, 5.25)


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


# ---------------------------------------------------------------------------
# Two-click span placement of voltage/current annotations (open / short)
# ---------------------------------------------------------------------------

def _press(scene: SchematicScene, gu, button=Qt.LeftButton):
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    e.setButton(button)
    e.setScenePos(scene.gu_to_scene(*gu))
    scene.mousePressEvent(e)


@pytest.mark.parametrize("kind", ["open", "short"])
def test_span_placement_two_clicks(scene: SchematicScene, kind: str):
    """open/short are placed by clicking the start, then the end: no ghost until
    the first click, a span ghost in between, and the annotation committed (origin
    + span) on the second click — PLACE mode stays active for the next one."""
    scene.start_placement(kind)
    assert scene._ghost is None                      # no ghost before the first click
    _wire_move(scene, (2.0, 0.0))
    assert scene._ghost is None                      # still none while awaiting start

    _press(scene, (0.0, 0.0))                         # first click → origin
    assert scene._ghost is not None
    _wire_move(scene, (3.0, 0.0))                     # ghost spans origin→cursor
    assert scene._ghost.component.span_override == (3.0, 0.0)

    _press(scene, (3.0, 0.0))                         # second click → place
    assert len(scene.schematic.components) == 1
    comp = scene.schematic.components[0]
    assert comp.kind == kind
    assert comp.position == (0.0, 0.0)
    assert comp.span_override == (3.0, 0.0)
    # Re-armed for another span placement, still in PLACE mode.
    assert scene._mode == Mode.PLACE
    assert scene._ghost is None and scene._place_start_gu is None


def test_span_placement_snaps_endpoints_to_pins(scene: SchematicScene):
    """A span-placement click near a component pin magnet-snaps to it (the same
    magnet wire drawing uses), so an annotation can be drawn exactly across a
    component's two pins."""
    scene.place_component("R", (2.0, 0.0))            # pins at (2,0) and (4,0)
    scene.start_placement("open")
    _press(scene, (2.1, 0.05))                         # near the left pin
    assert scene._place_start_gu == (2.0, 0.0)
    _press(scene, (3.95, -0.05))                       # near the right pin
    ann = next(c for c in scene.schematic.components if c.kind == "open")
    assert ann.position == (2.0, 0.0)
    assert ann.span_override == (2.0, 0.0)             # spans exactly pin-to-pin


def test_span_placement_zero_length_click_ignored(scene: SchematicScene):
    """A second click coinciding with the origin drops no annotation (so a
    double-click can't create a degenerate zero-span annotation)."""
    scene.start_placement("open")
    _press(scene, (1.0, 1.0))
    _press(scene, (1.0, 1.0))
    assert scene.schematic.components == []
    assert scene._place_start_gu == (1.0, 1.0)        # still armed at the origin


def test_span_placement_escape_cancels(scene: SchematicScene):
    """Escape mid-span abandons placement and returns to SELECT, leaving no ghost."""
    scene.start_placement("short")
    _press(scene, (0.0, 0.0))
    assert scene._ghost is not None
    scene.cancel_current()
    assert scene._mode == Mode.SELECT
    assert scene._ghost is None and scene._place_start_gu is None
    assert scene.schematic.components == []


def test_span_placement_right_click_abandons_then_exits(scene: SchematicScene):
    """A right-click mid-span re-arms (stays in PLACE); a second right-click exits."""
    scene.start_placement("open")
    _press(scene, (0.0, 0.0))
    _press(scene, (2.0, 0.0), button=Qt.RightButton)   # abandon the in-progress span
    assert scene._mode == Mode.PLACE
    assert scene._place_start_gu is None and scene._ghost is None
    _press(scene, (2.0, 0.0), button=Qt.RightButton)   # leave PLACE
    assert scene._mode == Mode.SELECT


def test_normal_component_still_single_click_place(scene: SchematicScene):
    """A non-span kind keeps the ghost-follow single-click placement."""
    scene.start_placement("R")
    assert scene._ghost is not None                   # ghost appears immediately
    _press(scene, (2.0, 0.0))
    assert len(scene.schematic.components) == 1
    assert scene._mode == Mode.PLACE                  # stays for rapid placement


# ---------------------------------------------------------------------------
# Cursor-follow paste (§6.7): begin_paste previews the clipboard as ghosts that
# track the cursor; a left-click commits at the cursor, Escape / right-click
# cancels — so the user positions pasted pins instead of pasting blind.
# ---------------------------------------------------------------------------

def _copy_one(scene: SchematicScene, kind: str, gu) -> "object":
    """Place *kind* at *gu*, select it, and copy it to the clipboard. Returns the
    placed component (the original; the paste makes new-UUID copies)."""
    comp = scene.place_component(kind, gu)
    scene.enter_select_mode()
    scene._comp_items[comp.id].setSelected(True)
    scene.copy_selection()
    return comp


def test_begin_paste_spawns_ghosts_and_defers_commit(scene: SchematicScene):
    """begin_paste enters PLACE mode with one ghost per clipboard component and
    commits nothing until the user clicks."""
    _copy_one(scene, "R", (3.0, 3.0))
    n_before = len(scene.schematic.components)

    scene.begin_paste()
    assert scene._mode == Mode.PLACE
    assert len(scene._paste_ghosts) == 1
    assert scene._paste_anchor_gu == (3.0, 3.0)       # single comp → its own position
    assert len(scene.schematic.components) == n_before  # nothing committed yet


def test_begin_paste_left_click_commits_at_cursor(scene: SchematicScene):
    """The pasted group's min-corner anchors at the click point — matching the
    ghost preview — and the scene returns to SELECT with the paste selected."""
    orig = _copy_one(scene, "R", (3.0, 3.0))
    n_before = len(scene.schematic.components)

    scene.begin_paste()
    _wire_move(scene, (10.0, 8.0))                     # ghost tracks the cursor
    assert len(scene.schematic.components) == n_before  # still no commit on move

    _press(scene, (10.0, 8.0))                         # click commits at the cursor
    assert scene._mode == Mode.SELECT
    assert not scene._paste_ghosts
    assert scene._paste_anchor_gu is None
    assert len(scene.schematic.components) == n_before + 1
    pasted = next(c for c in scene.schematic.components if c.id != orig.id)
    assert pasted.position == (10.0, 8.0)             # min corner landed at the click


def test_begin_paste_right_click_cancels(scene: SchematicScene):
    """A right-click during the paste preview cancels it: no components added."""
    _copy_one(scene, "R", (3.0, 3.0))
    n_before = len(scene.schematic.components)

    scene.begin_paste()
    _press(scene, (8.0, 8.0), button=Qt.RightButton)
    assert scene._mode == Mode.SELECT
    assert not scene._paste_ghosts
    assert scene._paste_anchor_gu is None
    assert len(scene.schematic.components) == n_before


def test_begin_paste_escape_cancels(scene: SchematicScene):
    """Escape (cancel_current) abandons the paste preview, leaving no ghosts."""
    _copy_one(scene, "R", (3.0, 3.0))
    n_before = len(scene.schematic.components)

    scene.begin_paste()
    scene.cancel_current()
    assert scene._mode == Mode.SELECT
    assert not scene._paste_ghosts
    assert len(scene.schematic.components) == n_before


def test_begin_paste_empty_clipboard_is_noop(scene: SchematicScene):
    """With an empty clipboard begin_paste does nothing — no mode change, no ghosts."""
    scene.begin_paste()
    assert scene._mode == Mode.SELECT
    assert not scene._paste_ghosts
    assert scene.schematic.components == []


def test_node_text_renders_on_canvas_for_node_style(scene: SchematicScene):
    """Setting node_text on a node-style component shows its on-canvas {…} label;
    clearing it hides the label again (the edit is visible without compiling)."""
    comp = scene.place_component("npn", (3.0, 3.0))
    item = scene._comp_items[comp.id]
    assert not item._node_text_item.isVisible()       # empty by default

    scene.edit_component_node_text(comp.id, "$Q_1$")
    assert scene._component_by_id(comp.id).node_text == "$Q_1$"
    assert item._node_text_item.isVisible()           # label now shown

    scene.edit_component_node_text(comp.id, "")
    assert not item._node_text_item.isVisible()        # hidden again


def test_node_text_label_has_transparent_background(scene: SchematicScene):
    """The on-canvas node-text label paints no opaque backdrop (transparent, to
    match CircuiTikZ), unlike axis-centred annotation labels."""
    comp = scene.place_component("npn", (3.0, 3.0))
    item = scene._comp_items[comp.id]
    assert item._node_text_item._opaque_bg is False


def test_node_text_inline_editor_commits_separately_from_options(scene: SchematicScene):
    """A node element has two in-place editors: the node-text editor commits to
    node_text (verbatim), independent of the options editor."""
    comp = scene.place_component("npn", (3.0, 3.0))
    scene.edit_component_node_text(comp.id, "$Q_1$")
    item = scene._comp_items[comp.id]

    item.begin_node_text_edit()
    assert item._node_text_editor.is_editing
    assert not item._node_text_item.isVisible()        # display hidden while editing
    assert item._node_text_editor.toPlainText() == "$Q_1$"   # pre-filled
    assert not item._options_item.is_editing           # the options editor is separate

    item._node_text_editor.setPlainText("$Q_2$")
    item._node_text_editor.end_edit(commit=True)
    assert scene._component_by_id(comp.id).node_text == "$Q_2$"
    assert scene._component_by_id(comp.id).options == ""   # options untouched
    assert not item._node_text_editor.is_editing


def test_node_text_editor_cancel_keeps_value(scene: SchematicScene):
    """Escaping the node-text editor (commit=False) leaves node_text unchanged."""
    comp = scene.place_component("npn", (3.0, 3.0))
    scene.edit_component_node_text(comp.id, "$Q_1$")
    item = scene._comp_items[comp.id]
    item.begin_node_text_edit()
    item._node_text_editor.setPlainText("$Q_9$")
    item._node_text_editor.end_edit(commit=False)
    assert scene._component_by_id(comp.id).node_text == "$Q_1$"


def _dbl(scene: SchematicScene, gu):
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseDoubleClick)
    e.setButton(Qt.LeftButton)
    e.setScenePos(scene.gu_to_scene(*gu))
    scene.mouseDoubleClickEvent(e)


def test_double_click_node_edits_node_text_not_options(scene: SchematicScene):
    """Double-clicking a node-style component on the canvas always edits its node
    text — its node[…] options are inspector-only, even when already set."""
    comp = scene.place_component("npn", (3.0, 3.0))   # no options, no node text
    item = scene._comp_items[comp.id]
    _dbl(scene, (3.0, 3.0))                            # on the node body (origin pin)
    assert item._node_text_editor.is_editing
    assert not item._options_item.is_editing
    item._node_text_editor.end_edit(commit=False)

    # Even with options set, the canvas edits node text (not options).
    scene.edit_component_options(comp.id, "color=blue")
    _dbl(scene, (3.0, 3.0))
    assert item._node_text_editor.is_editing
    assert not item._options_item.is_editing


def test_node_style_options_show_no_slot_labels(scene: SchematicScene):
    """A node-style component's options are not rendered as on-canvas slot labels
    (they are inspector-only); a path-style component still shows them."""
    npn = scene.place_component("npn", (3.0, 3.0))
    scene.edit_component_options(npn.id, "l=$Q_1$")
    nitem = scene._comp_items[npn.id]
    assert not any(s.isVisible() for s in nitem._slot_items)

    res = scene.place_component("R", (8.0, 0.0))
    scene.edit_component_options(res.id, "l=$R_1$")
    ritem = scene._comp_items[res.id]
    assert any(s.isVisible() for s in ritem._slot_items)


def test_snap_target_pin_vs_grid_node(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))  # pins (0,0),(2,0)
    pt, is_pin = scene.wire_snap_target((2.05, 0.0))
    assert pt == (2.0, 0.0) and is_pin is True
    pt, is_pin = scene.wire_snap_target((4.0, 0.0))
    assert pt == (4.0, 0.0) and is_pin is False


def test_route_offgrid_pin_extends_along_pin_lead_then_elbows(scene: SchematicScene):
    """The leg adjacent to an off-grid pin keeps the pin's off-grid coordinate (so
    the wire extends from the pin along its own lead line) and only then elbows onto
    the grid — instead of snapping to the grid immediately at the pin."""
    pin = (81.0, 79.125)                                   # x on-grid, y off-grid
    # Drawing FROM the pin: first leg keeps the off-grid y, corner carries it.
    out = scene._route(pin, (78.0, 78.0))
    assert out[0] == pin and out[1] == (78.0, 79.125)      # horizontal first leg
    assert out[2] == (78.0, 78.0)
    # Drawing INTO the pin: last leg keeps the off-grid y (natural approach).
    inn = scene._route((78.0, 78.0), pin)
    assert inn[-2] == (78.0, 79.125) and inn[-1] == pin
    # With neither endpoint off-grid the corner is kept on the grid as before.
    on = scene._route((0.0, 0.0), (2.0, 1.0))
    assert scene._on_grid(on[1])


def test_route_both_axes_offgrid_pin_honours_orientation(scene: SchematicScene):
    """A pin off-grid in BOTH axes (the thyristor/triac gate) can be routed either
    way: horizontal-first OR vertical-first, following the caller's orientation —
    both keep a valid corner (it inherits one of the pin's own off-grid coords).
    Regression: the router forced horizontal-first, so vertical routing silently
    fell back to a horizontal elbow and the user couldn't route up/down."""
    from app.schematic.model import component_pin_positions
    from app.schematic.validate import validate

    off = lambda v: abs(v * 4 - round(v * 4)) > 1e-6
    scene.place_component("thyristor", (40.0, 40.0))
    gate = component_pin_positions(scene.schematic.components[0])[2]
    assert off(gate[0]) and off(gate[1])       # the gate is off-grid in both axes
    target = (round((gate[0] - 1.0) * 4) / 4, round((gate[1] - 2.0) * 4) / 4)

    horiz = scene._route(gate, target, vfirst=False)
    vert = scene._route(gate, target, vfirst=True)
    assert horiz[1] == (target[0], gate[1])    # horizontal first leg (carries pin y)
    assert vert[1] == (gate[0], target[1])     # vertical first leg (carries pin x)
    assert horiz != vert                       # the two orientations are distinct
    # Both produce a valid schematic when wired from the gate.
    for path in (horiz, vert):
        scene.add_wire(list(path))
    assert validate(scene.schematic) == []


def test_move_wire_vertex_onto_offgrid_pin_keeps_it_offgrid(scene: SchematicScene):
    """Dropping a wire vertex onto a scaled gate's off-grid pin keeps it exactly on
    the pin (the magnet resolved it there); dropping elsewhere snaps to the grid.
    Regression: move_wire_vertex used to snap every target to the grid, knocking a
    wire off an off-grid pin so it could never stay connected."""
    from app.schematic.model import component_pin_positions
    scene.place_component("or", (20.0, 20.0))
    g = scene.schematic.components[0]
    scene.set_component_scale(g.id, 0.5)                  # 0.5 → off-grid inputs
    pin = component_pin_positions(g)[1]
    assert abs(round(pin[1] / 0.25) * 0.25 - pin[1]) > 1e-9   # truly off-grid
    w = scene.add_wire([(pin[0] - 2.0, 20.0), (pin[0], 20.0)])
    idx = len(scene._wire_by_id(w.id).points) - 1
    scene.move_wire_vertex(w.id, idx, pin)               # magnet target = the pin
    assert scene._wire_by_id(w.id).points[-1] == pin     # kept off-grid, on the pin
    # A separate vertex dropped on a NON-pin off-grid target still snaps to the grid.
    w2 = scene.add_wire([(30.0, 30.0), (32.0, 30.0)])
    scene.move_wire_vertex(w2.id, 1, (34.13, 30.07))
    assert scene._wire_by_id(w2.id).points[-1] == (34.25, 30.0)


def test_drag_does_not_resurrect_suppressed_termination_dot(scene: SchematicScene):
    """Regression: dragging any component/wire re-ran the open-circle preview, which
    ignored no_termination_dots (and custom markers) and so added open circles at a
    suppressed wire end. The preview must mirror open_endpoints exactly."""
    r = scene.place_component("R", (10.0, 10.0))          # unrelated component to drag
    w = scene.add_wire([(0.0, 0.0), (0.0, 2.0)])
    scene.set_wire_no_termination_dots(w.id, True)
    assert scene._open_circle_items == {}                 # suppressed on both ends
    # Simulate dragging the unrelated resistor (preview only).
    scene._drag.drag_start = {r.id: r.position}
    scene._drag.preview_component_drag()
    assert scene._open_circle_items == {}                 # still suppressed mid-drag
    # A custom end marker likewise suppresses the auto open circle during a drag.
    scene._drag.drag_start = {}
    w2 = scene.add_wire([(5.0, 0.0), (5.0, 2.0)])
    scene.set_wire_start_marker(w2.id, "arrow")
    scene._drag.drag_start = {r.id: r.position}
    scene._drag.preview_component_drag()
    assert (5.0, 0.0) not in scene._open_circle_items      # marker end stays bare


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


def test_wire_preview_heading_memory(scene: SchematicScene):
    """The elbow follows the cursor's locked out-direction (memory, §6.4): the
    first leg keeps the axis the cursor first went out along, even when the
    perpendicular leg later grows longer (which the old dominant-axis router would
    have flipped). The out-direction is set by the first clear departure and held."""
    scene.place_component("R", (0.0, 0.0))
    scene.enter_wire_mode()

    # Out HORIZONTALLY first, then far down → stays horizontal-first (right→down).
    _wire_press(scene, (2.0, 0.0))
    _wire_move(scene, (5.0, 0.0))            # out right → locks 'h'
    _wire_move(scene, (5.0, 6.0))            # then far down (|dy| > |dx|)
    p = scene._wire_preview
    assert list(p.points) + [p.cursor] == [(2.0, 0.0), (5.0, 0.0), (5.0, 6.0)]

    # A fresh wire out VERTICALLY first, then far right → stays vertical-first.
    scene._cancel_wire()
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))
    _wire_move(scene, (2.0, 3.0))            # out down → locks 'v'
    _wire_move(scene, (8.0, 3.0))            # then far right (|dx| > |dy|)
    p = scene._wire_preview
    assert list(p.points) + [p.cursor] == [(2.0, 0.0), (2.0, 3.0), (8.0, 3.0)]


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


@pytest.mark.parametrize("teardown", ["cancel_current", "enter_select_mode"])
def test_wire_preview_cleared_on_teardown(scene: SchematicScene, teardown: str):
    """Both ways out of an in-progress wire — Escape (cancel_current) and a mode
    change — drop the preview."""
    scene.place_component("R", (0.0, 0.0))
    scene.enter_wire_mode()
    _wire_press(scene, (2.0, 0.0))
    assert scene._wire_preview is not None
    getattr(scene, teardown)()
    assert scene._wire_preview is None
    assert scene._wire_pts == []


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


def test_logic_gate_placed_full_size(scene: SchematicScene):
    """A logic gate is placed at the full default scale (1.0), matching the digital
    blocks (no scaled layout). Its inputs land on the 0.25-GU grid; the centre-
    placed output sits at the scaled output anchor (off-grid, magnet-connected,
    §4). A non-gate also keeps scale 1.0."""
    from app.schematic.model import component_pin_positions
    from app.components import library
    g = scene.place_component("and", (10.0, 10.0))   # 2 inputs by default
    r = scene.place_component("R", (2.0, 2.0))
    assert abs(g.scale - 1.0) < 1e-9
    assert abs(r.scale - 1.0) < 1e-9
    # At scale 1.0 there is no scaled layout — base pins are used directly.
    assert library.gate_layout(g) is None
    # pins are [out, in1, in2]; the inputs are on grid, the output is not.
    pos = component_pin_positions(g)

    def _on_grid(p):
        return all(abs(round(v / 0.25) * 0.25 - v) < 1e-9 for v in p)

    assert _on_grid(pos[1]) and _on_grid(pos[2])    # inputs gridded
    assert not _on_grid(pos[0])                      # output at scaled anchor


def test_set_component_scale_is_undoable(scene: SchematicScene):
    g = scene.place_component("and", (10.0, 10.0))   # default 1.0
    scene.set_component_scale(g.id, 0.5)
    assert abs(scene.schematic.components[0].scale - 0.5) < 1e-9
    scene.undo()
    assert abs(scene.schematic.components[0].scale - 1.0) < 1e-9


def test_scaling_a_gate_makes_connected_wires_follow_its_pins(scene: SchematicScene):
    """Scaling a gate relocates its (off-grid) pins; a wire connected to a pin must
    follow it so the schematic stays valid. Regression: before this, the wire kept
    its old endpoint — now off-grid and on no pin — producing an invalid schematic
    (a CircuiTikZ generation error). Undo restores the original wiring exactly."""
    from app.schematic.model import component_pin_positions, Wire
    from app.components.model import Component
    from app.schematic.validate import validate
    import uuid
    g = Component(id="or00aaaa", kind="or", position=(20.0, 20.0), rotation=0,
                  options="", scale=0.5, params={"inputs": 4})
    scene._schematic.components.append(g)
    scene._rebuild_items()
    in1 = component_pin_positions(g)[1]                   # off-grid input pin
    w = Wire(id=str(uuid.uuid4()), points=[(16.0, 18.0), (in1[0], 18.0), in1])
    scene._schematic.wires.append(w)
    scene._rebuild_items()
    orig_pts = list(w.points)
    assert validate(scene.schematic) == []

    scene.set_component_scale(g.id, 0.75)                 # relocates the pins
    new_in1 = component_pin_positions(scene.schematic.components[0])[1]
    assert new_in1 != in1                                 # the pin actually moved
    moved = scene._wire_by_id(w.id)
    assert moved.points[-1] == new_in1                    # the wire followed it
    assert validate(scene.schematic) == []                # still valid → compiles

    scene.undo()
    assert abs(scene.schematic.components[0].scale - 0.5) < 1e-9
    assert scene._wire_by_id(w.id).points == orig_pts     # wiring restored exactly


def test_changing_input_count_makes_connected_wires_follow(scene: SchematicScene):
    """Changing a gate's input count relocates its pins (and adds/removes some).
    Connected wires follow the surviving pins to their new positions; a wire on a
    removed pin is snapped to the grid (valid, disconnected). The schematic stays
    valid (no CircuiTikZ error) and undo restores the wiring exactly."""
    from app.schematic.model import component_pin_positions, Wire
    from app.components.model import Component
    from app.schematic.validate import validate
    import uuid
    g = Component(id="or00aaaa", kind="or", position=(20.0, 20.0), rotation=0,
                  options="", scale=0.5, params={"inputs": 4})
    scene._schematic.components.append(g)
    scene._rebuild_items()
    pins = component_pin_positions(g)                     # out, in1..in4
    # one wire on in2 (survives a shrink) and one on in4 (removed by a shrink)
    w2 = Wire(id=str(uuid.uuid4()), points=[(16.0, 18.0), (pins[2][0], 18.0), pins[2]])
    w4 = Wire(id=str(uuid.uuid4()), points=[(16.0, 22.0), (pins[4][0], 22.0), pins[4]])
    scene._schematic.wires += [w2, w4]
    scene._rebuild_items()
    w2_orig, w4_orig = list(w2.points), list(w4.points)

    scene.set_component_param(g.id, "inputs", 3)          # in4 removed; in1..3 move
    new_in2 = component_pin_positions(scene.schematic.components[0])[2]
    assert scene._wire_by_id(w2.id).points[-1] == new_in2  # survives → follows
    assert scene._on_grid(scene._wire_by_id(w4.id).points[-1])  # removed → grid-snapped
    assert validate(scene.schematic) == []                 # valid → compiles

    scene.undo()
    assert scene.schematic.components[0].params["inputs"] == 4
    assert scene._wire_by_id(w2.id).points == w2_orig
    assert scene._wire_by_id(w4.id).points == w4_orig


def test_placement_ghost_uses_default_scale(scene: SchematicScene):
    """The placement ghost previews a logic gate at its full default scale (1.0),
    so what you see before clicking matches what gets placed."""
    scene.start_placement("and")
    assert scene._ghost is not None
    assert abs(scene._ghost.component.scale - 1.0) < 1e-9
    scene.start_placement("R")
    assert abs(scene._ghost.component.scale - 1.0) < 1e-9


def test_whole_wire_drag_moves_wire_and_taps_follow(scene: SchematicScene):
    """Pressing a selected wire's body and dragging translates it; a junction tap
    follows at the shared vertex while its far end stays. Labels survive and the
    move is one undoable step (spec §6.3)."""
    from app.schematic.model import Schematic, Wire
    sch = Schematic(version="0.1", name="t", wires=[
        Wire(id="bus", points=[(2.0, 0.0), (2.0, 4.0)], start_label="$a$"),
        Wire(id="tap", points=[(2.0, 2.0), (5.0, 2.0)]),
    ])
    scene.set_schematic(sch)
    scene._wire_items["bus"].setSelected(True)

    _sel_press(scene, (2.0, 1.0))            # press the bus body (not a vertex/junction)
    assert scene._drag.wire_drag_ids == {"bus"}
    _sel_move(scene, (3.0, 1.0))             # live preview
    _sel_release(scene, (3.0, 1.0))          # commit (delta = +1 GU in x)

    assert scene._drag.wire_drag_ids == set()
    bus = next(w for w in scene.schematic.wires if w.id == "bus")
    tap = next(w for w in scene.schematic.wires if w.id == "tap")
    assert bus.points == [(3.0, 0.0), (3.0, 4.0)]
    assert tap.points == [(3.0, 2.0), (5.0, 2.0)]   # bus end followed, far end stayed
    assert bus.start_label == "$a$"

    scene.undo()                              # single undo restores both wires
    assert next(w for w in scene.schematic.wires if w.id == "bus").points == [(2.0, 0.0), (2.0, 4.0)]
    assert next(w for w in scene.schematic.wires if w.id == "tap").points == [(2.0, 2.0), (5.0, 2.0)]


def test_vertex_hit_test_resolves_every_vertex(scene: SchematicScene):
    """wire_vertex_at returns each vertex by index — the pin-connected endpoint
    (0, draggable to disconnect), the corner (1), and the free end (2)."""
    w = _wire_with_pin_endpoint(scene)
    assert scene.wire_vertex_at(scene.gu_to_scene(2.0, 0.0)) == (w.id, 0)  # pin end
    assert scene.wire_vertex_at(scene.gu_to_scene(2.0, 3.0)) == (w.id, 1)  # corner
    assert scene.wire_vertex_at(scene.gu_to_scene(5.0, 3.0)) == (w.id, 2)  # free end


def test_no_locked_indices_all_vertices_draggable(scene: SchematicScene):
    w = _wire_with_pin_endpoint(scene)
    item = scene._wire_items[w.id]
    # Every vertex is draggable now (connected endpoints can be dragged to
    # disconnect), so no vertex is locked / handle-less.
    assert item.locked_indices == set()


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


def test_vertex_drag_preview_and_commit_land_on_offgrid_pin(scene: SchematicScene):
    """Dragging a wire endpoint near a scaled gate's off-grid pin snaps the *live
    preview* onto the pin (via the magnet), and the commit lands there too — so the
    wire can actually be connected to (and kept on) an off-grid pin by dragging."""
    from app.schematic.model import component_pin_positions
    scene.place_component("or", (20.0, 20.0))
    g = scene.schematic.components[0]
    scene.set_component_scale(g.id, 0.5)                 # 0.5 → off-grid pin
    pin = component_pin_positions(g)[1]
    assert abs(round(pin[1] / 0.25) * 0.25 - pin[1]) > 1e-9   # off-grid pin
    w = scene.add_wire([(pin[0] - 3.0, 20.0), (pin[0], 20.0)])
    wid = w.id
    end = len(scene._wire_by_id(wid).points) - 1
    _sel_press(scene, scene._wire_by_id(wid).points[end])   # grab the free end
    assert scene._vertex_drag is not None
    _sel_move(scene, pin)                                # cursor on the off-grid pin
    preview = scene._wire_items[wid]._preview_points
    assert preview is not None and preview[-1] == pin    # preview snapped to the pin
    _sel_release(scene, pin)
    assert scene._wire_by_id(wid).points[-1] == pin      # committed onto the pin


def test_vertex_drag_slides_along_offgrid_pin_axis(scene: SchematicScene):
    """A vertex collinear with an off-grid pin can be dragged *along that pin's
    axis* — its off-grid coordinate is preserved (so the segment into the pin stays
    straight) while the other coordinate snaps to the grid. Dragging away from the
    axis snaps both coordinates to the grid as usual."""
    from app.schematic.model import component_pin_positions, Wire
    import uuid
    scene.place_component("or", (20.0, 20.0))
    g = scene.schematic.components[0]
    pin = component_pin_positions(g)[1]                  # off-grid in y
    w = Wire(id=str(uuid.uuid4()),
             points=[(16.0, 18.0), (18.0, 18.0), (18.0, pin[1]), pin])
    scene._schematic.wires.append(w)
    scene._rebuild_items()
    ids = {w.id}
    # Near the pin's axis line → y stays on the off-grid pin coordinate.
    on_axis = scene._vertex_drag_target(ids, (17.3, pin[1] + 0.03), exclude_wire_id=w.id)
    assert on_axis == (17.25, pin[1])
    # Far from the axis line → snaps fully to the grid.
    off_axis = scene._vertex_drag_target(ids, (17.3, 18.4), exclude_wire_id=w.id)
    assert scene._on_grid(off_axis)


def test_vertex_drag_can_land_between_two_offgrid_pins(scene: SchematicScene):
    """The on-grid line *between* two adjacent off-grid pins (each PIN_SNAP_GU away)
    must be reachable: the magnet only wins when the cursor is actually closer to a
    pin than to that grid line. Snapping onto a pin still works when nearer it."""
    from app.schematic.model import component_pin_positions, Wire
    from app.components.model import Component
    import uuid
    g = Component(id="or00aaaa", kind="or", position=(20.0, 20.0), rotation=0,
                  options="", scale=0.5, params={"inputs": 4})
    scene._schematic.components.append(g)
    scene._rebuild_items()
    pins = component_pin_positions(g)
    in2, in3 = pins[2], pins[3]                          # adjacent off-grid inputs
    mid = (in2[0], (in2[1] + in3[1]) / 2.0)             # on-grid line between them
    assert scene._on_grid(mid)
    w = Wire(id=str(uuid.uuid4()), points=[(16.0, 18.0), (in2[0], 18.0), in2])
    scene._schematic.wires.append(w)
    scene._rebuild_items()
    ids = {w.id}
    # On the mid grid line: not captured by either pin.
    assert scene._vertex_drag_target(ids, mid, exclude_wire_id=w.id) == mid
    # Closer to pin 3 than to a grid line: snaps onto pin 3.
    assert scene._vertex_drag_target(ids, (in3[0], in3[1] + 0.02),
                                     exclude_wire_id=w.id) == in3
    # Exactly on the wire's own pin: connects.
    assert scene._vertex_drag_target(ids, in2, exclude_wire_id=w.id) == in2


def test_open_endpoints_overrides_match_committed(scene: SchematicScene):
    """The shared open_endpoints() drives both the committed canvas decorations and
    the drag preview: with no overrides it reflects the model; with points_override
    it reflects the previewed geometry — one rule, no duplicate implementation."""
    from app.schematic.model import open_endpoints
    w = scene.add_wire([(0.0, 0.0), (0.0, 2.0)])
    assert open_endpoints(scene.schematic) == {(0.0, 0.0), (0.0, 2.0)}
    # Substitute a moved geometry for this wire (as the drag preview does).
    moved = {w.id: [(1.0, 0.0), (1.0, 2.0)]}
    assert open_endpoints(scene.schematic, points_override=moved) == {(1.0, 0.0), (1.0, 2.0)}
    # no_termination_dots is honoured through the same path.
    scene.set_wire_no_termination_dots(w.id, True)
    assert open_endpoints(scene.schematic, points_override=moved) == set()


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


def test_press_on_pin_endpoint_starts_drag_to_disconnect(scene: SchematicScene):
    w = _wire_with_pin_endpoint(scene)
    _sel_press(scene, (2.0, 0.0))            # the pin endpoint
    # Connected endpoints are draggable now (drag to disconnect), so a press
    # there starts a vertex drag of that endpoint.
    assert scene._vertex_drag == (w.id, 0, (2.0, 0.0))


def test_drag_connected_endpoint_disconnects_it(scene: SchematicScene):
    """Dragging a wire endpoint off a component pin disconnects it: the endpoint
    moves to the new spot and is no longer coincident with the pin."""
    scene.place_component("R", (0.0, 0.0))          # pin at (2,0)
    w = scene.add_wire([(2.0, 0.0), (5.0, 0.0)])    # start on the pin; end free
    _sel_press(scene, (2.0, 0.0))                   # grab the connected endpoint
    assert scene._vertex_drag is not None
    _sel_move(scene, (2.0, 3.0))
    _sel_release(scene, (2.0, 3.0))                 # commit the disconnect

    pts = scene.schematic.wires[0].points
    assert pts[0] == (2.0, 3.0)                      # endpoint moved off the pin
    assert (2.0, 0.0) not in (pts[0], pts[-1])       # no longer on the pin


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
    """Component item position snaps to 0.25 GU during drag, not only on release.

    Regression: before the fix, Qt moved items at sub-grid pixel positions until
    release, so the visual position was unsnapped mid-drag.
    """
    from app.canvas.style import GRID_PX

    a = scene.place_component("R", (0.0, 0.0))
    _begin_component_drag(scene, a.id)

    # Move the item to a fractional (off-grid) pixel position and fire mouseMoveEvent.
    item = scene._comp_items[a.id]
    off_grid_x = item.pos().x() + 0.3 * GRID_PX   # 0.3 GU off — not a 0.25 GU boundary
    off_grid_y = item.pos().y() + 0.7 * GRID_PX
    item.setPos(off_grid_x, off_grid_y)
    mv = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseMove)
    mv.setScenePos(QPointF(off_grid_x, off_grid_y))
    mv.setButtons(Qt.LeftButton)
    scene.mouseMoveEvent(mv)

    # After the event, item.pos() must sit exactly on a 0.25 GU grid point.
    snapped = scene.snap_point_gu(item.pos())
    actual = scene.scene_to_gu(item.pos())
    assert actual == snapped, (
        f"item position {actual} is not snapped to grid {snapped} mid-drag"
    )


def test_click_on_component_does_not_push_spurious_move(scene: SchematicScene):
    """A plain click (press+release, no drag) on a component pushes no move and
    preserves its exact position (regression, §5.9)."""
    a = scene.place_component("R", (5.0, 5.0))
    scene._comp_items[a.id].setSelected(True)
    scene.nudge_selected(0.0, -0.25)                 # → (5.0, 4.75), one minor cell
    assert scene._component_by_id(a.id).position == (5.0, 4.75)

    count_before = scene.undo_stack.undo_count
    _begin_component_drag(scene, a.id)               # press at body centre
    _release_component(scene, a.id)                  # release without moving
    # Position preserved, no spurious MoveCommand pushed.
    assert scene._component_by_id(a.id).position == (5.0, 4.75)
    assert scene.undo_stack.undo_count == count_before


def test_nudge_025_any_direction_keeps_wires_valid(scene: SchematicScene):
    """On the 0.25 grid a 0.25 nudge in ANY direction keeps connected wires
    valid — a perpendicular nudge elbows on-grid instead of jogging off it (§3.1)."""
    from app.schematic.validate import validate

    r = scene.place_component("R", (0.0, 0.0))       # pins (0,0),(2,0)
    scene.place_component("R", (10.0, 10.0))         # 2nd component → not a select-all move
    scene.add_wire([(2.0, 0.0), (2.0, 2.0)])         # vertical lead off the right pin
    scene.clearSelection()
    scene._comp_items[r.id].setSelected(True)

    # Perpendicular (horizontal) 0.25 nudge: the lead's auto-elbow lands on a
    # 0.25 node, so the schematic stays valid (no rejection, the move happens).
    scene.nudge_selected(0.25, 0.0)
    assert scene._component_by_id(r.id).position == (0.25, 0.0)
    assert validate(scene.schematic) == []

    # Parallel (vertical) nudge along the lead is also fine.
    scene.nudge_selected(0.0, 0.25)
    assert scene._component_by_id(r.id).position == (0.25, 0.25)
    assert validate(scene.schematic) == []


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


# ---------------------------------------------------------------------------
# Unconnected-pin open circles on the canvas (display preference, §10.8)
# ---------------------------------------------------------------------------

def test_pin_circles_absent_by_default(scene: SchematicScene):
    """No pin circles are drawn unless the preference is enabled."""
    scene.place_component("R", (0.0, 0.0))
    assert scene._pin_circle_items == {}


def test_pin_circles_appear_when_enabled(scene: SchematicScene):
    """Enabling the preference draws a circle at each unconnected pin."""
    scene.place_component("R", (0.0, 0.0))   # free pins (0,0),(2,0)
    scene.set_mark_unconnected_pins(True)
    assert set(scene._pin_circle_items) == {(0.0, 0.0), (2.0, 0.0)}


def test_pin_circles_toggle_off_removes_items(scene: SchematicScene):
    """Disabling the preference removes the markers again."""
    scene.place_component("R", (0.0, 0.0))
    scene.set_mark_unconnected_pins(True)
    scene.set_mark_unconnected_pins(False)
    assert scene._pin_circle_items == {}


def test_pin_circle_removed_when_pin_gets_wired(scene: SchematicScene):
    """A pin that becomes wired loses its circle on the next rebuild."""
    scene.place_component("R", (0.0, 0.0))   # free pins (0,0),(2,0)
    scene.set_mark_unconnected_pins(True)
    scene.add_wire([(2.0, 0.0), (4.0, 0.0)])  # wire now attaches (2,0)
    assert (2.0, 0.0) not in scene._pin_circle_items
    assert (0.0, 0.0) in scene._pin_circle_items


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


def test_double_click_empty_space_exits_wire(scene: SchematicScene):
    """Double-clicking to end a wire in empty space commits it and exits WIRE mode."""
    scene.place_component("R", (0.0, 0.0))   # free pin (2,0)
    _sel_press(scene, (2.0, 0.0))            # auto-enter wire
    assert scene.mode == Mode.WIRE
    dbl = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseDoubleClick)
    dbl.setButton(Qt.LeftButton)
    dbl.setScenePos(scene.gu_to_scene(4.0, 3.0))   # empty space
    scene.mouseDoubleClickEvent(dbl)
    assert scene.mode == Mode.SELECT         # double-click ends the wire and exits
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


def _dbl_click(scene: SchematicScene, gu, modifiers=Qt.NoModifier):
    e = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseDoubleClick)
    e.setButton(Qt.LeftButton)
    e.setModifiers(modifiers)
    e.setScenePos(scene.gu_to_scene(*gu))
    scene.mouseDoubleClickEvent(e)


def test_double_click_wire_body_enters_wire_mode(scene: SchematicScene):
    """Plain double-click on a wire body starts a new wire from that point
    (enters WIRE mode); the target wire splits when the new wire commits."""
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    assert scene.mode == Mode.SELECT

    _dbl_click(scene, (2.0, 0.0))   # on the wire body

    assert scene.mode == Mode.WIRE
    assert scene._wire_pts == [(2.0, 0.0)]
    assert scene._wire_preview is not None


def test_double_click_wire_commits_splits_on_add(scene: SchematicScene):
    """A wire started by double-clicking a wire body splits the target on commit."""
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    _dbl_click(scene, (2.0, 0.0))           # enter WIRE mode at (2,0) on the wire
    assert scene.mode == Mode.WIRE
    _dbl_click(scene, (2.0, 3.0))           # finalize the new wire (2,0)→(2,3)
    # Target wire split into two halves + the new stub = 3 wires; junction at (2,0).
    assert len(scene.schematic.wires) == 3
    assert (2.0, 0.0) in scene._junction_items


def test_alt_double_click_wire_body_opens_mid_label_editor(scene: SchematicScene):
    """Alt+double-click on a wire body opens its mid-label inline editor."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    item = scene._wire_items[wire.id]

    _dbl_click(scene, (2.0, 0.0), modifiers=Qt.AltModifier)

    assert scene.mode == Mode.SELECT          # NOT wire mode
    assert item._label_editor.is_editing
    assert item._editing_end == "mid"
    item._label_editor.end_edit(commit=False)


def test_double_click_wire_vertex_enters_wire_mode(scene: SchematicScene):
    """Double-clicking an interior wire vertex starts a new wire from it."""
    scene.add_wire([(0.0, 0.0), (0.0, 2.0), (4.0, 2.0)])  # corner at (0,2)

    _dbl_click(scene, (0.0, 2.0))

    assert scene.mode == Mode.WIRE
    assert scene._wire_pts == [(0.0, 2.0)]


def test_double_click_free_endpoint_opens_label_editor(scene: SchematicScene):
    """Double-clicking a free wire endpoint opens its endpoint-label editor
    (even with no label set), instead of entering WIRE mode."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    item = scene._wire_items[wire.id]

    _dbl_click(scene, (4.0, 0.0))   # free END endpoint
    assert scene.mode == Mode.SELECT
    assert item._label_editor.is_editing
    assert item._editing_end == "end"
    item._label_editor.end_edit(commit=False)  # clean up the editor

    _dbl_click(scene, (0.0, 0.0))   # free START endpoint
    assert item._editing_end == "start"


def test_double_click_connected_endpoint_opens_endpoint_label_editor(scene: SchematicScene):
    """A wire endpoint on a component pin is now a per-end label target: a
    double-click there opens *that endpoint's* label editor (not the mid-label)."""
    scene.place_component("R", (0.0, 0.0))      # pins at (0,0) and (2,0)
    wire = scene.add_wire([(2.0, 0.0), (5.0, 0.0)])  # (2,0) on the pin; (5,0) free
    item = scene._wire_items[wire.id]

    _dbl_click(scene, (2.0, 0.0))               # connected START endpoint
    assert scene.mode == Mode.SELECT
    assert item._label_editor.is_editing
    assert item._editing_end == "start"
    item._label_editor.end_edit(commit=False)


def test_double_click_empty_space_enters_wire_mode(scene: SchematicScene):
    """Double-clicking blank canvas enters WIRE mode from the snapped grid point."""
    _dbl_click(scene, (2.0, 5.0))   # empty space

    assert scene.mode == Mode.WIRE
    assert scene._wire_pts == [(2.0, 5.0)]
    assert scene._wire_preview is not None


def test_double_click_wire_near_component_reaches_wire(scene: SchematicScene):
    """A wire inside a component's bounding box is reachable by double-click.

    Regression: the component double-click check ran first and swallowed the
    event, preventing the wire gesture (now entering WIRE mode) on wires that
    overlap a component's bounding rect (e.g. side wires of a series R-L loop).
    """
    # Resistor with pins at (0,0) and (2,0); its bbox spans that area.
    scene.place_component("R", (0.0, 0.0))
    # Vertical wire inside the component's vertical extent, close to the left pin.
    scene.add_wire([(0.0, 0.0), (0.0, 2.0)])

    # Double-click on the wire body at (0, 1.0) — inside the component bbox.
    _dbl_click(scene, (0.0, 1.0))

    assert scene.mode == Mode.WIRE                 # reached the wire, not the part
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
    # The resistor uses the generic ComponentItem (no special subclass).
    assert any(isinstance(i, ComponentItem) for i in scene.items(pt))
    types = {type(i).__name__ for i in scene.items(pt)}
    assert "WireItem" not in types


def test_wire_shape_includes_draggable_handles(scene: SchematicScene):
    w = scene.add_wire([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])  # all free vertices
    # The middle vertex handle (a corner) is grabbable → inside the shape.
    assert _in_wire_shape(scene, w.id, (2.0, 0.0))


def test_wire_shape_selectable_for_degenerate(scene: SchematicScene):
    """A degenerate single-point wire is drawn as a selectable red ✕ (so a stray
    one from an old file can be found and deleted) — its hit shape and bounding
    rect are non-empty. A truly empty (0-point) wire still has no shape."""
    from app.canvas.items import WireItem
    from app.schematic.model import Wire

    item = WireItem(Wire(id="x", points=[(0.0, 0.0)]))
    assert not item.shape().isEmpty()
    assert not item.boundingRect().isEmpty()

    empty = WireItem(Wire(id="y", points=[]))
    assert empty.shape().isEmpty()


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

    # The split + new wire is a single undoable action.
    scene.undo()
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

    # Corner-split + new wire is a single undoable action.
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
    # codegen normalises coordinates toward the origin (min y = 2 → 0), so the
    # junction dot lands at (2,0).
    assert r"\node[circ] at (2,0) {};" in src


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

    # The split + vertex move is a single undoable action.
    scene.undo()
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


def test_non_empty_options_shows_slot_labels(scene: SchematicScene):
    """Setting options shows one per-side slot label per annotation; the in-place
    editor item stays hidden (it activates only on double-click)."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$, v=$5V$")
    item = scene._comp_items[comp.id]
    assert not item._options_item.isVisible()  # editor hidden until edit
    visible = [s for s in item._slot_items if s.isVisible()]
    assert len(visible) == 2  # l (above) + v (below)


def test_cleared_options_hides_slots(scene: SchematicScene):
    """Setting options to empty hides all slot labels (and the editor)."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    scene.edit_component_options(comp.id, "")
    item = scene._comp_items[comp.id]
    assert not item._options_item.isVisible()
    assert not any(s.isVisible() for s in item._slot_items)


def test_label_slot_side(scene: SchematicScene):
    """A plain `l=` label on a horizontal component is placed above (offset up)."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    visible = [s for s in item._slot_items if s.isVisible()]
    assert len(visible) == 1
    # Horizontal lead axis -> left-of-traversal is screen-up (negative y).
    assert visible[0]._dir.y() < 0


def test_voltage_and_label_slots_opposite_sides_when_rotated(scene: SchematicScene):
    """On a rotated component, `l_` and `v^` land on opposite sides matching the
    LaTeX output (regression): for a 90°-rotated capacitor the rendered PDF puts
    `l_` on the screen-left and `v^` on the screen-right. The voltage slot must
    use the same traversal-relative basis as the label, not a separate heuristic
    that collapses both onto the same side."""
    comp = scene.place_component("C", (0.0, 0.0), rotation=90)
    scene.edit_component_options(comp.id, r"l_=$C$, v^=$V$")
    item = scene._comp_items[comp.id]
    visible = [s for s in item._slot_items if s.isVisible()]
    assert len(visible) == 2
    by_dir = {round(s._dir.x()): s for s in visible}
    # Opposite horizontal sides (one -x, one +x), not collapsed together.
    assert set(by_dir) == {-1, 1}
    # l_ to screen-left (C), v^ to screen-right (V) — see /tmp render comparison.
    l_slot = next(s for s in visible if s._fragment == "$C$")
    v_slot = next(s for s in visible if s._fragment == "$V$")
    assert l_slot._dir.x() < 0
    assert v_slot._dir.x() > 0


def test_voltage_source_default_v_label_flips_side(scene: SchematicScene):
    """A voltage source's default `v=` label sits on the opposite side from a
    passive's — CircuiTikZ's source voltage convention (regression).

    All three are vertical (pins (0,0)-(0,2), traversal down): a voltage source
    (cV) puts plain `v` on the screen-right, while a current source (I) and a
    passive default to the screen-left. The explicit `v^`/`v_` forms are not
    flipped (component-independent)."""
    cv = scene.place_component("cV", (0.0, 0.0))
    scene.edit_component_options(cv.id, "v=$V$")
    i = scene.place_component("I", (4.0, 0.0))
    scene.edit_component_options(i.id, "v=$V$")

    def vdir(comp):
        item = scene._comp_items[comp.id]
        return next(s for s in item._slot_items if s.isVisible())._dir

    assert vdir(cv).x() > 0.5, "voltage source default v must be screen-right"
    assert vdir(i).x() < -0.5, "current source default v must be screen-left"

    # The explicit v_ on the voltage source is NOT flipped (stays screen-left).
    scene.edit_component_options(cv.id, "v_=$V$")
    assert vdir(cv).x() < -0.5


def test_label_and_current_on_same_side_offset_along_axis(scene: SchematicScene):
    """A label and a current that default to the same side don't overlap: the
    label is centred over the body while the current is placed off-centre over the
    *exit lead* (offset along the lead axis), matching CircuiTikZ's `i=` placement.

    An inductor with `l=$L$, i=$i_L$` puts both above. Rather than stacking the
    current perpendicularly beyond the label, the current hugs the lead (clearing
    the arrowhead, not the body) but is shifted along the axis toward the second
    pin, so they clear each other along the wire instead of in height."""
    comp = scene.place_component("L", (0.0, 0.0))   # horizontal lead axis
    scene.edit_component_options(comp.id, r"l=$L$, i=$i_L$")
    item = scene._comp_items[comp.id]
    visible = [s for s in item._slot_items if s.isVisible()]
    assert len(visible) == 2
    l_slot = next(s for s in visible if s._fragment == "$L$")
    i_slot = next(s for s in visible if s._fragment == "$i_L$")
    # Same side (same offset direction).
    assert (round(l_slot._dir.x(), 3), round(l_slot._dir.y(), 3)) == \
           (round(i_slot._dir.x(), 3), round(i_slot._dir.y(), 3))
    # The current is offset along the lead axis (its label centre is shifted from
    # the label's, toward the second pin), so the two don't collide.
    dx = abs(i_slot._center_rel.x() - l_slot._center_rel.x())
    dy = abs(i_slot._center_rel.y() - l_slot._center_rel.y())
    assert dx + dy > 1.0


def test_open_annotation_labels_centered_on_axis(scene: SchematicScene):
    """The `open` voltage annotation centres its slot labels over the line
    (no perpendicular clearance) so they mirror the LaTeX arrow placement."""
    comp = scene.place_component("open", (0.0, 0.0))
    scene.edit_component_options(comp.id, "v=$V_s$")
    item = scene._comp_items[comp.id]
    visible = [s for s in item._slot_items if s.isVisible()]
    assert len(visible) == 1
    assert visible[0]._centered is True
    assert visible[0]._base_dist == 0.0


def test_dotted_grid_renders_without_error(scene: SchematicScene):
    """The dotted-grid background paints onto a device-space painter without
    error (the dots are stroked in device coords via QPolygonF)."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    img = QImage(120, 120, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    scene.drawBackground(painter, QRectF(0.0, 0.0, 120.0, 120.0))
    painter.end()
    # Something was drawn (paper fill at minimum), so the buffer is not empty.
    assert img.constBits() is not None


def _visible_decorations(item):
    return [d for d in item._decoration_items if d.isVisible()]


def test_voltage_slot_draws_american_signs(scene: SchematicScene):
    """A `v=` slot draws the american ± sign decoration by default (§5.8)."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "v=$v_R$")
    item = scene._comp_items[comp.id]
    decs = _visible_decorations(item)
    assert len(decs) == 1
    assert decs[0]._mode == "v_american"


def test_voltage_slot_draws_european_arrow_when_document_style_european(
    scene: SchematicScene,
):
    """With the document voltage style set to european, the `v=` decoration is the
    arrow form instead of ± signs; relayout updates existing components (§7.2)."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "v=$v_R$")
    scene.schematic.voltage_style = "european"
    scene.relayout_annotations()
    item = scene._comp_items[comp.id]
    decs = _visible_decorations(item)
    assert len(decs) == 1
    assert decs[0]._mode == "v_european"


def test_current_slot_draws_arrow(scene: SchematicScene):
    """An `i=` slot draws a direction-arrow decoration."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "i=$i$")
    item = scene._comp_items[comp.id]
    decs = _visible_decorations(item)
    assert len(decs) == 1
    assert decs[0]._mode == "current"


def test_current_label_clearance_is_lead_relative(scene: SchematicScene):
    """An `i=` label clears the arrowhead on the thin lead, not the component
    body, so its perpendicular clearance is the same regardless of body height.
    Regression: using the body's perpendicular thickness floated the current
    label far above the wire on `short`/tall-bodied parts (§5.8)."""
    from app.canvas.items import _CUR_ARROW_HEAD_W, _LABEL_GAP
    expected = _CUR_ARROW_HEAD_W / 2.0 + _LABEL_GAP

    def current_base(kind, pos):
        comp = scene.place_component(kind, pos)
        scene.edit_component_options(comp.id, r"i=$i$")
        slot = next(s for s in scene._comp_items[comp.id]._slot_items
                    if s.isVisible() and s._fragment == "$i$")
        return slot._base_dist

    assert current_base("L", (0.0, 0.0)) == expected   # tall inductor body
    assert current_base("R", (0.0, 4.0)) == expected   # flatter resistor body


def test_current_reversed_modifier_flips_direction_and_lead(scene: SchematicScene):
    """`i<=` reverses the current arrow and moves it to the *entry* lead, while
    `i=` rides the exit lead — matching CircuiTikZ (§5.8)."""
    fwd = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(fwd.id, r"i=$i$")
    rev = scene.place_component("R", (0.0, 4.0))
    scene.edit_component_options(rev.id, r"i<=$i$")
    fwd_item, rev_item = scene._comp_items[fwd.id], scene._comp_items[rev.id]

    fwd_dec = next(d for d in fwd_item._decoration_items if d.isVisible())
    rev_dec = next(d for d in rev_item._decoration_items if d.isVisible())
    assert fwd_dec._mode == "current" and rev_dec._mode == "current"
    assert fwd_dec._reversed is False
    assert rev_dec._reversed is True

    # Forward label rides toward the second pin; reversed toward the first.
    fwd_slot = next(s for s in fwd_item._slot_items if s.isVisible())
    rev_slot = next(s for s in rev_item._slot_items if s.isVisible())
    assert fwd_slot._center_rel.x() > rev_slot._center_rel.x()


def test_voltage_reversed_modifier_sets_polarity(scene: SchematicScene):
    """`v<=` marks the voltage decoration reversed (swapped ± / arrow)."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, r"v<=$v$")
    dec = next(d for d in scene._comp_items[comp.id]._decoration_items if d.isVisible())
    assert dec._mode == "v_american"
    assert dec._reversed is True


def test_open_current_annotation_arrow_centered_above_line(scene: SchematicScene):
    """The `open` current annotation draws a *centred* current arrow with its
    label above it — not the axis-centred label used for an open voltage (§5.8)."""
    comp = scene.place_component("open", (0.0, 0.0))
    scene.edit_component_options(comp.id, r"i=$i$")
    item = scene._comp_items[comp.id]
    dec = next(d for d in item._decoration_items if d.isVisible())
    assert dec._mode == "current"
    assert dec._centered is True
    slot = next(s for s in item._slot_items if s.isVisible())
    assert slot._centered is False   # the label sits above the arrow, not on the line


def test_current_centered_on_bodyless_short(scene: SchematicScene):
    """A current on a `short` (no body) is centred on the wire's midpoint like
    CircuiTikZ — and `i<=` only flips it in place — whereas a bodied component
    rides the exit lead and shifts when reversed (§5.8)."""
    sh = scene.place_component("short", (0.0, 0.0))
    scene.edit_component_options(sh.id, r"i=$i$")
    sh_item = scene._comp_items[sh.id]
    assert next(d for d in sh_item._decoration_items if d.isVisible())._centered is True
    fwd_x = next(s for s in sh_item._slot_items if s.isVisible())._center_rel.x()

    scene.edit_component_options(sh.id, r"i<=$i$")
    sh_dec = next(d for d in sh_item._decoration_items if d.isVisible())
    rev_x = next(s for s in sh_item._slot_items if s.isVisible())._center_rel.x()
    assert sh_dec._centered is True and sh_dec._reversed is True
    assert abs(rev_x - fwd_x) < 1e-6        # short: midpoint unchanged, arrow only flips

    # A bodied resistor instead rides the lead and shifts when reversed.
    r = scene.place_component("R", (0.0, 4.0))
    scene.edit_component_options(r.id, r"i=$i$")
    r_item = scene._comp_items[r.id]
    assert next(d for d in r_item._decoration_items if d.isVisible())._centered is False
    r_fwd = next(s for s in r_item._slot_items if s.isVisible())._center_rel.x()
    scene.edit_component_options(r.id, r"i<=$i$")
    r_rev = next(s for s in r_item._slot_items if s.isVisible())._center_rel.x()
    assert abs(r_rev - r_fwd) > 1.0


def test_label_slot_has_no_decoration(scene: SchematicScene):
    """A plain `l=` label draws no voltage/current decoration."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    assert _visible_decorations(item) == []


def test_open_voltage_annotation_draws_signs_and_centers_label(scene: SchematicScene):
    """The `open` voltage annotation draws ± signs at its terminals (like
    CircuiTikZ's `to[open, v=…]`) while the value label stays centred on the line;
    `v<=` swaps the polarity."""
    comp = scene.place_component("open", (0.0, 0.0))
    scene.edit_component_options(comp.id, "v=$V_s$")
    item = scene._comp_items[comp.id]
    decs = _visible_decorations(item)
    assert len(decs) == 1
    assert decs[0]._mode == "v_american"
    assert decs[0]._reversed is False
    # The value label stays centred on the line (not pushed to a side).
    label = next(s for s in item._slot_items if s.isVisible())
    assert label._centered is True

    scene.edit_component_options(comp.id, "v<=$V_s$")
    assert _visible_decorations(item)[0]._reversed is True


def test_voltage_decoration_axis_follows_traversal(scene: SchematicScene):
    """The voltage arrow's axis points first-pin → second-pin in screen space, so
    on a 90°-rotated component it is vertical, not horizontal."""
    comp = scene.place_component("R", (0.0, 0.0), rotation=90)
    scene.edit_component_options(comp.id, "v=$v_R$")
    item = scene._comp_items[comp.id]
    dec = _visible_decorations(item)[0]
    # Vertical traversal → axis runs along screen-y, not screen-x.
    assert abs(dec._axis.y()) > abs(dec._axis.x())


def test_options_undo_hides_slots(scene: SchematicScene):
    """Undoing an options edit hides the slot labels again."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    assert any(s.isVisible() for s in item._slot_items)
    scene.undo()
    item = scene._comp_items[comp.id]  # may be rebuilt by the undo
    assert not any(s.isVisible() for s in item._slot_items)


def test_options_item_begin_edit(scene: SchematicScene):
    """begin_options_edit activates the in-place editor."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    item.begin_options_edit()
    assert item._options_item.is_editing


def test_set_wire_line_style_and_width(scene: SchematicScene):
    """Wire style setters push undoable commands and update the model."""
    wire = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    assert wire is not None
    scene.set_wire_line_style(wire.id, "dashed")
    scene.set_wire_line_width(wire.id, 0.8)
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.line_style == "dashed" and w.line_width == 0.8
    scene.undo()  # undo the width change
    assert next(x for x in scene.schematic.wires if x.id == wire.id).line_width == 0.4
    assert next(x for x in scene.schematic.wires if x.id == wire.id).line_style == "dashed"


def test_set_wire_no_junction_dots(scene: SchematicScene):
    """The no_junction_dots setter is undoable and removes the junction dot."""
    main = scene.add_wire([(0.0, 0.0), (2.0, 0.0), (4.0, 0.0)])
    branch = scene.add_wire([(2.0, 0.0), (2.0, 2.0)])
    assert (2.0, 0.0) in scene._junction_items  # T-junction dot present
    scene.set_wire_no_junction_dots(branch.id, True)
    w = next(x for x in scene.schematic.wires if x.id == branch.id)
    assert w.no_junction_dots is True
    assert (2.0, 0.0) not in scene._junction_items  # dot removed
    scene.undo()
    assert (2.0, 0.0) in scene._junction_items  # restored


def test_set_wire_no_termination_dots(scene: SchematicScene):
    """The no_termination_dots setter is undoable and removes the open circles."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])  # free wire -> two ocircs
    assert (0.0, 0.0) in scene._open_circle_items
    scene.set_wire_no_termination_dots(wire.id, True)
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.no_termination_dots is True
    assert (0.0, 0.0) not in scene._open_circle_items
    assert (4.0, 0.0) not in scene._open_circle_items
    scene.undo()
    assert (0.0, 0.0) in scene._open_circle_items


def test_set_wire_marker_suppresses_open_circle(scene: SchematicScene):
    """Setting an endpoint marker is undoable and removes that end's open circle."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])  # free wire -> two ocircs
    assert (0.0, 0.0) in scene._open_circle_items
    assert (4.0, 0.0) in scene._open_circle_items
    scene.set_wire_end_marker(wire.id, "arrow")
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.end_marker == "arrow"
    assert (4.0, 0.0) not in scene._open_circle_items  # marked end: terminal gone
    assert (0.0, 0.0) in scene._open_circle_items       # unmarked end: terminal kept
    scene.undo()
    assert next(x for x in scene.schematic.wires if x.id == wire.id).end_marker == ""
    assert (4.0, 0.0) in scene._open_circle_items       # restored


def test_wire_setters_noop_when_unchanged(scene: SchematicScene):
    """Calling any wire setter with the value it already has pushes no command
    (the shared no-op guard on every wire-attribute setter)."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    before = scene._stack.can_undo()
    scene.set_wire_start_marker(wire.id, "")        # already none
    scene.set_wire_end_marker(wire.id, "")
    scene.set_wire_start_label(wire.id, "")         # already empty
    scene.set_wire_end_label(wire.id, "")
    scene.set_wire_mid_label(wire.id, "")
    scene.set_wire_mid_label_pos(wire.id, 0.5)      # already default
    scene.set_wire_line_style(wire.id, "")          # already solid
    scene.set_wire_line_width(wire.id, 0.4)         # already default
    assert scene._stack.can_undo() == before


def test_set_wire_labels_undoable(scene: SchematicScene):
    """Wire endpoint label setters push undoable commands and update the model."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.set_wire_start_label(wire.id, "in")
    scene.set_wire_end_label(wire.id, "$y(t)$")
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.start_label == "in" and w.end_label == "$y(t)$"
    scene.undo()  # undo end label
    assert next(x for x in scene.schematic.wires if x.id == wire.id).end_label == ""
    assert next(x for x in scene.schematic.wires if x.id == wire.id).start_label == "in"


def test_tab_cycle_endpoint_marker(scene: SchematicScene):
    """cycle_at on a free endpoint steps its marker through the cycle (and wraps)."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])

    def end_marker():
        return next(w for w in scene.schematic.wires if w.id == wire.id).end_marker

    expected = ["arrow", "stealth", "open", "bar", "", "arrow"]
    for want in expected:
        scene.cycle_at(scene.gu_to_scene(4.0, 0.0))
        assert end_marker() == want
    # Shift+Tab steps backward.
    scene.cycle_at(scene.gu_to_scene(4.0, 0.0), backward=True)
    assert end_marker() == ""
    # Marker cycling does not touch line_style.
    assert next(w for w in scene.schematic.wires if w.id == wire.id).line_style == ""
    # Each step is undoable.
    scene.undo()
    assert end_marker() == "arrow"


def test_tab_cycle_start_vs_end_endpoint(scene: SchematicScene):
    """The cursor's endpoint determines which marker cycles."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.cycle_at(scene.gu_to_scene(0.0, 0.0))   # START endpoint
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.start_marker == "arrow" and w.end_marker == ""


def test_tab_cycle_line_style_on_body(scene: SchematicScene):
    """cycle_at on a wire body steps the line style through the cycle (and wraps)."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])

    def line_style():
        return next(w for w in scene.schematic.wires if w.id == wire.id).line_style

    for want in ["dashed", "dotted", "dash dot", "", "dashed"]:
        scene.cycle_at(scene.gu_to_scene(2.0, 0.0))   # body midpoint
        assert line_style() == want
    # Body cycling leaves endpoint markers alone.
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.start_marker == "" and w.end_marker == ""


def test_tab_cycle_interior_vertex_cycles_line_style(scene: SchematicScene):
    """An interior vertex is not an endpoint, so it cycles the line style."""
    wire = scene.add_wire([(0.0, 0.0), (0.0, 2.0), (4.0, 2.0)])  # corner at (0,2)
    scene.cycle_at(scene.gu_to_scene(0.0, 2.0))   # interior corner
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.line_style == "dashed"
    assert w.start_marker == "" and w.end_marker == ""


def test_tab_cycle_empty_space_is_noop(scene: SchematicScene):
    """cycle_at off any wire changes nothing and reports no change."""
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    changed = scene.cycle_at(scene.gu_to_scene(2.0, 5.0))   # blank space
    assert changed is False


@pytest.mark.parametrize("kind", ["rect", "circle"])
def test_tab_cycle_marker_on_block_connected_endpoint(scene: SchematicScene, kind: str):
    """A wire endpoint on a block-diagram connection point (rect edge / circle
    cardinal) is pin-locked, but Tab still cycles its arrow marker, not the line
    style."""
    scene.place_component(kind, (0.0, 0.0))       # 2x2 box; east connection at (2,1)
    wire = scene.add_wire([(5.0, 1.0), (2.0, 1.0)])
    changed = scene.cycle_at(scene.gu_to_scene(2.0, 1.0))   # endpoint on the boundary
    assert changed is True
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.end_marker == "arrow"          # marker cycled
    assert w.line_style == ""               # line style untouched


def test_cycle_wire_label_placement_steps_and_is_independent(scene: SchematicScene):
    """`_cycle_wire_label_placement` steps a label's position through
    WIRE_LABEL_PLACEMENTS (wraps; `backward` reverses) for the chosen end only."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.set_wire_end_label(wire.id, "y")

    def end_place():
        return next(w for w in scene.schematic.wires if w.id == wire.id).end_label_placement

    for want in ["above", "below", ""]:        # "" → above → below → "" (wraps)
        scene._cycle_wire_label_placement(wire.id, "end", False)
        assert end_place() == want
    scene._cycle_wire_label_placement(wire.id, "end", True)   # backward from ""
    assert end_place() == "below"

    # The start end is independent of the end.
    scene._cycle_wire_label_placement(wire.id, "start", False)
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.start_label_placement == "above"
    assert w.end_label_placement == "below"


def test_tab_over_endpoint_label_cycles_placement(scene: SchematicScene):
    """`cycle_at` over a rendered endpoint label cycles its placement (priority
    over the endpoint-marker cycle)."""
    from PySide6.QtGui import QPainterPath
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.set_wire_end_label(wire.id, "y")
    item = scene._wire_items[wire.id]
    lbl = item._end_label_item
    # Force a rendered path so the label is hit-testable in a headless test.
    path = QPainterPath()
    path.addRect(0.0, 0.0, 10.0, 5.0)
    lbl._path = path
    lbl._reposition()

    pt = lbl.mapToScene(lbl.boundingRect().center())
    changed = scene.cycle_at(pt)
    assert changed is True
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.end_label_placement == "above"   # cycled from "" (not the marker)
    assert w.end_marker == ""                 # marker untouched


def test_set_wire_mid_label_and_pos(scene: SchematicScene):
    """Mid-label text and position setters are undoable; position clamps to [0,1]."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.set_wire_mid_label(wire.id, "$V_{bus}$")
    scene.set_wire_mid_label_pos(wire.id, 0.25)
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.mid_label == "$V_{bus}$" and w.mid_label_pos == 0.25
    scene.set_wire_mid_label_pos(wire.id, 5.0)   # clamps
    assert next(x for x in scene.schematic.wires if x.id == wire.id).mid_label_pos == 1.0
    scene.undo()  # undo the clamp
    assert next(x for x in scene.schematic.wires if x.id == wire.id).mid_label_pos == 0.25


def test_mid_label_inline_edit_commits(scene: SchematicScene):
    """Double-click editing of the mid-label commits via the scene."""
    from app.canvas.items import _WireMidLabel, WireItem

    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.set_wire_mid_label(wire.id, "$V_{bus}$")
    item = scene._wire_items[wire.id]
    assert isinstance(item._mid_label_item, _WireMidLabel)
    assert isinstance(item._mid_label_item.parentItem(), WireItem)

    ed = item._label_editor
    item.begin_label_edit("mid")
    assert ed.is_editing
    assert ed.toPlainText() == "$V_{bus}$"
    assert not item._mid_label_item.isVisible()   # display hidden while editing

    ed.setPlainText("$I_o$")
    ed.end_edit(commit=True)
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.mid_label == "$I_o$"
    assert item._mid_label_item.isVisible()        # display restored


def test_wire_label_inline_edit_commits(scene: SchematicScene):
    """Double-click editing of an end label commits the new text via the scene."""
    from app.canvas.items import _WireEndLabel, WireItem

    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.set_wire_end_label(wire.id, "$y(t)$")
    item = scene._wire_items[wire.id]
    assert isinstance(item._end_label_item, _WireEndLabel)
    assert item._end_label_item.end == "end"
    assert isinstance(item._end_label_item.parentItem(), WireItem)

    ed = item._label_editor
    item.begin_label_edit("end")
    assert ed.is_editing
    assert ed.toPlainText() == "$y(t)$"          # editor pre-filled with the fragment
    assert not item._end_label_item.isVisible()  # display hidden while editing

    ed.setPlainText("$z(t)$")
    ed.end_edit(commit=True)
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.end_label == "$z(t)$"
    assert item._end_label_item.isVisible()       # display restored
    assert not ed.isVisible()                     # editor hidden after commit


def test_wire_label_inline_edit_cancel_leaves_model(scene: SchematicScene):
    """Escape (cancel) restores the display label without changing the model."""
    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.set_wire_start_label(wire.id, "in")
    item = scene._wire_items[wire.id]
    ed = item._label_editor

    item.begin_label_edit("start")
    ed.setPlainText("CHANGED")
    ed.end_edit(commit=False)
    w = next(x for x in scene.schematic.wires if x.id == wire.id)
    assert w.start_label == "in"                  # unchanged on cancel
    assert item._start_label_item.isVisible()


def test_wire_label_field_commits_on_editing_finished_not_typing(scene: SchematicScene):
    """Typing in a label field must not commit mid-edit (cursor-jump regression).

    A keystroke (textChanged) used to fire the debounced commit, which re-bound
    the panel and reset the field's cursor to the end. Labels now commit only on
    editingFinished (Enter / focus-out).
    """
    from app.ui.properties import PropertiesPanel

    wire = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_wire(wire.id)
    sec = panel._wire_section

    # Simulate typing: setText emits textChanged, which must NOT commit.
    can_undo_before = scene._stack.can_undo()
    sec._end_label.setText("$y(t)$")
    assert scene._stack.can_undo() == can_undo_before
    assert next(w for w in scene.schematic.wires if w.id == wire.id).end_label == ""

    # editingFinished (Enter / focus-out) commits.
    sec._end_label.editingFinished.emit()
    assert next(w for w in scene.schematic.wires if w.id == wire.id).end_label == "$y(t)$"


def test_properties_panel_shows_wire(scene: SchematicScene):
    """Selecting a single wire binds the wire-style inspector; a component
    selection unbinds it."""
    from app.ui.properties import PropertiesPanel

    wire = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    scene.set_wire_line_style(wire.id, "dashed")
    scene.set_wire_end_marker(wire.id, "arrow")
    scene.set_wire_end_label(wire.id, "$y(t)$")
    panel = PropertiesPanel()
    panel.set_scene(scene)
    panel.show_wire(wire.id)
    assert panel._wire_section.isVisibleTo(panel)
    assert panel._wire_section._line_style.currentText() == "Dashed"
    assert panel._wire_section._end_marker.currentText() == "Arrow"
    assert panel._wire_section._start_marker.currentText() == "None"
    assert panel._wire_section._end_label.text() == "$y(t)$"
    assert panel._wire_section._start_label.text() == ""
    comp = scene.place_component("R", (5.0, 5.0))
    panel.show_component(comp.id)
    assert not panel._wire_section.isVisibleTo(panel)


def test_double_click_slot_label_opens_editor(scene: SchematicScene):
    """Double-clicking a rendered per-side slot label opens the in-place editor.

    Regression: slot labels are display-only `_SlotLabel`s, not `LabelTextItem`s,
    so the double-click handler must map them back to their parent component
    instead of falling through to wire-drawing.
    """
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QPainterPath
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    comp = scene.place_component("R", (3.0, 3.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]

    # Give the slot a path + position directly (the async render won't run in a
    # headless unit test) so it has a non-empty, hit-testable bounding rect.
    slot = item._slot_items[0]
    p = QPainterPath()
    p.addRect(0.0, -8.0, 14.0, 10.0)
    slot._path = p
    slot.setVisible(True)
    slot.setPos(0.0, -40.0)  # above the body, clear of the wire

    ev = QGraphicsSceneMouseEvent(QEvent.GraphicsSceneMouseDoubleClick)
    ev.setScenePos(slot.mapToScene(slot.boundingRect().center()))
    ev.setButton(Qt.LeftButton)
    scene.mouseDoubleClickEvent(ev)

    assert item._options_item.is_editing


def test_hover_highlights_label_group(scene: SchematicScene):
    """Hovering the component highlights its slot labels (same hover colour)."""
    from PySide6.QtGui import QColor

    from app.canvas.style import COLOR_HOVER, COLOR_NORMAL

    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    assert item._label_color() == QColor(COLOR_NORMAL)
    item._set_hovered(True)
    assert item._label_color() == QColor(COLOR_HOVER)
    item._set_hovered(False)
    assert item._label_color() == QColor(COLOR_NORMAL)


def test_label_side_is_traversal_relative(scene: SchematicScene):
    """`l`/`l_` sides follow the lead-axis traversal direction, not screen up/down.

    Regression: a controlled source whose lead axis points down had `l_` placed
    on the wrong (right) side; it must go left (right-of-traversal).
    """
    comp = scene.place_component("cV", (3.0, 3.0))  # pins (0,0)->(0,2): axis down
    scene.edit_component_options(comp.id, "l_=$O$")
    item = scene._comp_items[comp.id]
    geom = item._slot_geometry()
    # Down axis: left-of-traversal is screen-right, right-of-traversal is left.
    assert item._slot_direction("l_", geom).x() < 0   # l_ -> left
    assert item._slot_direction("l", geom).x() > 0     # l  -> right (opposite)


def test_resizable_slot_centres_on_actual_span(scene: SchematicScene):
    """A resizable component's slot label centres on its actual span, not the
    default registry bbox.

    Regression: `I_f+dl` sat right-of-centre because a 1-GU `short` used the
    2-GU default bbox centre.
    """
    from app.canvas.items import ITEM_CLASSES
    from app.canvas.style import GRID_PX
    from app.components.model import Component

    comp = Component(
        id="s1", kind="short", position=(0.0, 0.0), rotation=0,
        options="i=$I$", mirror=False, span_override=(1.0, 0.0),
    )
    item = ITEM_CLASSES["short"](comp)
    geom = item._slot_geometry()
    # 1-GU span -> centre at 0.5 GU, not the 2-GU default's 1.0 GU.
    assert geom["center_rel"].x() == 0.5 * GRID_PX


def test_slot_label_hover_highlights_component(scene: SchematicScene):
    """Hovering a slot label sets the parent component's hover state, so the
    body and all sibling labels highlight together."""
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QGraphicsSceneHoverEvent

    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    slot = item._slot_items[0]

    slot.hoverEnterEvent(QGraphicsSceneHoverEvent(QEvent.GraphicsSceneHoverEnter))
    assert item._hovered
    slot.hoverLeaveEvent(QGraphicsSceneHoverEvent(QEvent.GraphicsSceneHoverLeave))
    assert not item._hovered


def test_editor_shows_options_one_per_line(scene: SchematicScene):
    """begin_options_edit displays each option slot on its own line."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$, v=$V_s$")
    item = scene._comp_items[comp.id]
    item.begin_options_edit()
    assert item._options_item.toPlainText() == "l=$R_1$\nv=$V_s$"


def test_editor_commit_joins_lines_with_commas(scene: SchematicScene):
    """Newline-separated edits are stored back as a comma-separated string."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    item.begin_options_edit()
    item._options_item.setPlainText("l=$R_2$\nv=$10V$")
    item._options_item.end_edit(commit=True)
    updated = next(c for c in scene.schematic.components if c.id == comp.id)
    assert updated.options == "l=$R_2$, v=$10V$"


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


def test_ghost_hides_slot_labels(scene: SchematicScene):
    """Slot labels are hidden in ghost (placement preview) state, shown after."""
    comp = scene.place_component("R", (0.0, 0.0))
    scene.edit_component_options(comp.id, "l=$R_1$")
    item = scene._comp_items[comp.id]
    item.set_ghost(True)
    assert not any(s.isVisible() for s in item._slot_items)
    item.set_ghost(False)
    assert any(s.isVisible() for s in item._slot_items)


# ---------------------------------------------------------------------------
# Regression: group-rotate + delete with a junction dot must not crash paint
# ---------------------------------------------------------------------------

def test_no_index_method():
    """The scene uses NoIndex so removeItem updates the item list synchronously.

    The default BSP tree index only defers item removal; because _rebuild_items
    drops the last reference to coordinate-keyed junction/open-circle dots
    immediately, PySide frees the C++ item before the index is purged, and the
    next paint dereferences freed memory.
    """
    from PySide6.QtWidgets import QGraphicsScene
    s = SchematicScene()
    assert s.itemIndexMethod() == QGraphicsScene.ItemIndexMethod.NoIndex


def test_group_rotate_then_delete_then_paint_does_not_crash(scene: SchematicScene):
    """Regression: rotating a selected group containing a junction dot and then
    deleting it, followed by a repaint, previously segfaulted (dangling pointer
    in the scene's BSP index). It must now complete cleanly.
    """
    from PySide6.QtGui import QImage, QPainter

    view = SchematicView(scene)
    view.resize(800, 600)

    # A 3-way junction: two stubs meeting a through-wire at (80, 79.5).
    scene.place_component("R", (78.0, 79.0))
    scene.place_component("C", (78.0, 80.0))
    scene.place_component("L", (80.5, 79.5))
    scene.add_wire([(80.0, 79.0), (80.0, 79.5)])
    scene.add_wire([(80.0, 80.0), (80.0, 79.5)])
    scene.add_wire([(80.0, 79.5), (80.5, 79.5)])
    assert len(scene._junction_items) == 1

    for it in list(scene._comp_items.values()) + list(scene._wire_items.values()):
        it.setSelected(True)
    scene.rotate_selected_cw()

    for it in list(scene._comp_items.values()) + list(scene._wire_items.values()):
        it.setSelected(True)
    scene.delete_selected()

    # Force the paint path that walked the dangling pointer pre-fix.
    img = QImage(800, 600, QImage.Format_ARGB32)
    img.fill(0)
    painter = QPainter(img)
    scene.render(painter)
    painter.end()

    assert not scene.schematic.components
    assert not scene.schematic.wires
    assert not scene._junction_items


# ---------------------------------------------------------------------------
# Property/fuzz guard: paint after every mutation must never use freed memory
# ---------------------------------------------------------------------------

def test_random_mutation_sequences_never_crash_paint():
    """Drive randomized op sequences, painting the scene through a real view
    after each step.

    This is a *probabilistic* safety net, not a deterministic check: a
    use-after-free only faults when the freed memory happens to be reused, so no
    paint-based test can guarantee a catch (which is exactly why the original
    junction-dot crash went unnoticed). What this does buy is broad, repeated
    exercise of the real paint paths — a shown view's ``viewport().repaint()``
    (which queries the scene's item index for the exposed region) plus a direct
    ``scene.render`` — across place/move/rotate/wire/delete/undo/redo, including
    rubber-band selection (``setSelectionArea``, which builds the index). Any
    regression that frees an item the scene still references is likely to abort
    the run here rather than reach the field. The deterministic guard that the
    fix itself stays in place is ``test_no_index_method``.
    """
    import random

    from PySide6.QtGui import QImage, QPainter, QPainterPath

    KINDS = ["R", "C", "L", "D", "open", "ground"]

    def select_all_via_rubber_band(scene: SchematicScene) -> None:
        rect = scene.itemsBoundingRect()
        if not rect.isValid():
            return
        path = QPainterPath()
        path.addRect(rect.adjusted(-50, -50, 50, 50))
        scene.setSelectionArea(path)   # builds/queries the item index

    def rand_pt(rng: random.Random) -> tuple[float, float]:
        return (rng.randint(0, 12) * 0.5, rng.randint(0, 12) * 0.5)

    for seed in range(6):
        rng = random.Random(seed)
        scene = SchematicScene()
        view = SchematicView(scene)
        view.resize(500, 400)
        view.show()

        def paint() -> None:
            rect = scene.itemsBoundingRect()
            if rect.isValid():
                view.fitInView(rect.adjusted(-30, -30, 30, 30))
            view.viewport().repaint()      # real exposed-region paint (index query)
            _APP.processEvents()
            img = QImage(400, 320, QImage.Format_ARGB32)
            img.fill(0)
            p = QPainter(img)
            scene.render(p)                # full walk of all items
            p.end()

        for _ in range(40):
            op = rng.choice(
                ["place", "wire", "select_rotate", "delete_some",
                 "delete_all", "nudge", "undo", "redo"]
            )
            try:
                if op == "place":
                    scene.place_component(rng.choice(KINDS), rand_pt(rng))
                elif op == "wire":
                    a = rand_pt(rng)
                    b = (a[0] + rng.choice([-2, -1, 1, 2]), a[1])
                    c = (b[0], b[1] + rng.choice([-2, -1, 1, 2]))
                    scene.add_wire([a, b, c])
                elif op == "select_rotate":
                    select_all_via_rubber_band(scene)
                    scene.rotate_selected_cw()
                elif op == "delete_some":
                    items = list(scene._comp_items.values()) + list(scene._wire_items.values())
                    scene.clearSelection()
                    for it in items:
                        if rng.random() < 0.5:
                            it.setSelected(True)
                    scene.delete_selected()
                elif op == "delete_all":
                    select_all_via_rubber_band(scene)
                    scene.delete_selected()
                elif op == "nudge":
                    for it in scene._comp_items.values():
                        it.setSelected(True)
                    scene.nudge_selected(rng.choice([-0.5, 0.5]), rng.choice([-0.5, 0.5]))
                elif op == "undo":
                    scene.undo()
                elif op == "redo":
                    scene.redo()
            except Exception:
                # Model-level guard rejections (e.g. invalid wire) are fine; the
                # point of this test is that the *paint* below never crashes.
                pass

            paint()   # <- would segfault on a dangling item pointer


# ---------------------------------------------------------------------------
# Line-style rendering (regression: bipole ignored line_style on canvas)
# ---------------------------------------------------------------------------

def test_resolve_pen_style_mapping():
    """The shared line_style → Qt pen-style mapping used by rect and bipole items."""
    from PySide6.QtCore import Qt
    from app.canvas.items import _resolve_pen_style
    assert _resolve_pen_style("") == Qt.SolidLine
    assert _resolve_pen_style("dashed") == Qt.DashLine
    assert _resolve_pen_style("DOTTED") == Qt.DotLine        # case-insensitive
    assert _resolve_pen_style("dash dot") == Qt.DashDotLine
    assert _resolve_pen_style("bogus") == Qt.SolidLine        # unknown → solid


def _render_scene(scene: SchematicScene) -> bytes:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    img = QImage(400, 240, QImage.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    # Map the items' bounding rect to fill the image so a thick dashed border
    # spans many pixels and resolves distinctly from a solid one.
    scene.render(p, QRectF(img.rect()), scene.itemsBoundingRect())
    p.end()
    return bytes(img.constBits())


def test_bipole_line_style_changes_canvas_rendering(scene: SchematicScene):
    """Changing a bipole's line_style repaints its border with the new style.

    Regression: BipoleItem._draw_body previously built its pen without applying
    line_style, so dashed/dotted borders looked solid on the canvas.
    """
    comp = scene.place_component("bipole", (1.0, 0.0))
    # Drive edits through the scene (as the inspector does) so the canvas item
    # is refreshed; thick outline makes the dash pattern visible at render scale.
    scene.set_component_line_width(comp.id, 3.0)

    scene.set_line_style(comp.id, "")
    solid = _render_scene(scene)

    scene.set_line_style(comp.id, "dashed")
    dashed = _render_scene(scene)

    assert solid != dashed, "dashed bipole border should render differently from solid"


# ---------------------------------------------------------------------------
# Junction drag, sticky wire style, perimeter connection points
# ---------------------------------------------------------------------------

def test_coincident_vertices_finds_junction(scene: SchematicScene):
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])      # through wire
    scene.add_wire([(2.0, 0.0), (2.0, 2.0)])      # stub T-ing in → splits through
    assert len(scene.schematic.wires) == 3        # two halves + stub
    targets = scene._coincident_vertices((2.0, 0.0))
    assert len(targets) == 3                       # all three meet at the junction


def test_move_junction_drags_all_connected_wires(scene: SchematicScene):
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.add_wire([(2.0, 0.0), (2.0, 2.0)])
    targets = scene._coincident_vertices((2.0, 0.0))
    scene.move_junction(targets, (2.0, 1.0))
    # All three wires now meet at the moved junction; none remain at (2,0) as an
    # endpoint/junction (only a pass-through corner may remain).
    from app.schematic.validate import validate
    moved = scene._coincident_vertices((2.0, 1.0))
    assert len(moved) == 3
    assert validate(scene.schematic) == []
    # Undo restores the original geometry.
    scene.undo_stack.undo()
    assert len(scene._coincident_vertices((2.0, 0.0))) == 3
    assert len(scene._coincident_vertices((2.0, 1.0))) == 0


def test_new_wire_inherits_selected_wire_style(scene: SchematicScene):
    """Selecting a wire makes its style the template for newly drawn wires."""
    w1 = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    scene.set_wire_line_style(w1.id, "dashed")
    scene.set_wire_end_marker(w1.id, "arrow")
    scene.clearSelection()
    scene._wire_items[w1.id].setSelected(True)     # capture style for new wires
    w2 = scene.add_wire([(0.0, 2.0), (3.0, 2.0)])
    got = scene._wire_by_id(w2.id)
    assert got.line_style == "dashed"
    assert got.end_marker == "arrow"


def test_unconnected_perimeter_point_autostarts_wire(scene: SchematicScene):
    """Clicking a free rect-edge / circle-cardinal point starts a wire there."""
    scene.place_component("rect", (0.0, 0.0))        # 1x1; top-edge midpoint (0.5,0)
    assert scene.unconnected_pin_at(scene.gu_to_scene(0.5, 0.0)) == (0.5, 0.0)
    scene.place_component("circle", (10.0, 0.0))     # 0.5x0.5; east cardinal (10.5,0.25)
    assert scene.unconnected_pin_at(scene.gu_to_scene(10.5, 0.25)) == (10.5, 0.25)


def test_perimeter_connection_dots(scene: SchematicScene):
    """RectItem marks every 0.25-GU perimeter point; CircleItem only the four
    cardinal points."""
    r = scene.place_component("rect", (0.0, 0.0))    # 1x1 → 16 perimeter points
    assert len(scene._comp_items[r.id]._connection_dots_local()) == 16
    c = scene.place_component("circle", (10.0, 0.0))
    assert len(scene._comp_items[c.id]._connection_dots_local()) == 4


def test_new_wire_inherits_style_from_tab_cycle(scene: SchematicScene):
    """Tab-cycling a wire's endpoint marker makes it sticky for new wires."""
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.cycle_at(scene.gu_to_scene(4.0, 0.0))    # cycle END marker → arrow
    assert scene._new_wire_style["end_marker"] == "arrow"
    w2 = scene.add_wire([(0.0, 2.0), (3.0, 2.0)])
    assert scene._wire_by_id(w2.id).end_marker == "arrow"


def test_new_wire_inherits_style_from_inspector_setter(scene: SchematicScene):
    """Changing a wire's style via the inspector setter makes it sticky too."""
    w = scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.set_wire_line_style(w.id, "dotted")      # what the inspector calls
    assert scene._new_wire_style["line_style"] == "dotted"
    w2 = scene.add_wire([(0.0, 2.0), (3.0, 2.0)])
    assert scene._wire_by_id(w2.id).line_style == "dotted"


def test_junction_drag_shows_highlight_and_clears(scene: SchematicScene):
    """Dragging a junction shows a highlighted dot, hides the resting dot at the
    origin, and clears the highlight on release."""
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.add_wire([(2.0, 0.0), (2.0, 2.0)])       # T-junction at (2,0)
    assert (2.0, 0.0) in scene._junction_items

    _sel_press(scene, (2.0, 0.0))
    _sel_move(scene, (2.0, 1.0))
    assert scene._drag.junction_preview is not None          # highlighted dot
    assert not scene._junction_items[(2.0, 0.0)].isVisible()  # resting dot hidden
    _sel_release(scene, (2.0, 1.0))
    assert scene._drag.junction_preview is None              # cleared on release


def test_junction_item_hover_grows_and_highlights(scene: SchematicScene):
    """Hovering a junction dot grows it (and switches to the highlight colour)
    to signal it's draggable."""
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    scene.add_wire([(2.0, 0.0), (2.0, 2.0)])       # junction at (2,0)
    ji = scene._junction_items[(2.0, 0.0)]
    base = ji._radius()
    ji.hoverEnterEvent(None)
    assert ji._radius() > base
    ji.hoverLeaveEvent(None)
    assert ji._radius() == base


def test_move_junction_preserves_orientation_via_scene(scene: SchematicScene):
    """End-to-end: dragging a junction keeps each wire's orientation into it —
    a wire arriving vertically still arrives vertically."""
    scene.add_wire([(0.0, 2.0), (4.0, 2.0)])           # horizontal through wire
    stub = scene.add_wire([(2.0, 2.0), (2.0, 0.0), (5.0, 0.0)])  # vertical into junction
    targets = scene._coincident_vertices((2.0, 2.0))
    assert len(targets) == 3                            # two halves + stub
    scene.move_junction(targets, (3.0, 2.0))           # drag the junction right
    s = next(w for w in scene.schematic.wires if w.id == stub.id)
    assert s.points[0] == (3.0, 2.0)
    assert s.points[1] == (3.0, 0.0)                    # vertical segment followed


# ---------------------------------------------------------------------------
# Line-hops (decoration at non-connecting wire crossings)
# ---------------------------------------------------------------------------

def test_line_hops_populate_hopping_wire_item(scene: SchematicScene):
    """A non-connecting crossing puts a hop on the (later, tie-broken) wire."""
    scene.add_wire([(0.0, 1.0), (4.0, 1.0)])   # horizontal, index 0
    scene.add_wire([(2.0, 0.0), (2.0, 3.0)])   # vertical, index 1 → hops on tie
    h_id = scene.schematic.wires[0].id
    v_id = scene.schematic.wires[1].id
    assert scene._wire_items[v_id].hops
    assert scene._wire_items[v_id].hops[0].point == (2.0, 1.0)
    assert scene._wire_items[h_id].hops == []


def test_set_line_hops_toggles_bumps(scene: SchematicScene):
    scene.add_wire([(0.0, 1.0), (4.0, 1.0)])
    scene.add_wire([(2.0, 0.0), (2.0, 3.0)])
    v_id = scene.schematic.wires[1].id
    assert scene._wire_items[v_id].hops
    scene.set_line_hops(False)
    assert scene._wire_items[v_id].hops == []
    scene.set_line_hops(True)
    assert scene._wire_items[v_id].hops


def test_set_wire_z_order_flips_hopper_and_is_undoable(scene: SchematicScene):
    scene.add_wire([(0.0, 1.0), (4.0, 1.0)])   # h, index 0
    scene.add_wire([(2.0, 0.0), (2.0, 3.0)])   # v, index 1
    h_id = scene.schematic.wires[0].id
    v_id = scene.schematic.wires[1].id
    assert scene._wire_items[v_id].hops and not scene._wire_items[h_id].hops
    scene.set_wire_z_order(h_id, 1)            # raise h above v → h now hops
    assert scene._wire_items[h_id].hops and not scene._wire_items[v_id].hops
    assert scene._wire_items[h_id].zValue() == 1
    scene.undo()
    assert scene._wire_by_id(h_id).z_order == 0
    assert scene._wire_items[v_id].hops and not scene._wire_items[h_id].hops


def test_set_wire_hop_mode_never_yields_to_crosser_undoable(scene: SchematicScene):
    """Setting a wire to 'never' moves the bump onto the crossing wire; undoable."""
    scene.add_wire([(0.0, 1.0), (4.0, 1.0)])     # h, index 0
    scene.add_wire([(2.0, 0.0), (2.0, 3.0)])     # v, index 1 → v hops by tie
    h_id = scene.schematic.wires[0].id
    v_id = scene.schematic.wires[1].id
    assert scene._wire_items[v_id].hops
    scene.set_wire_hop_mode(v_id, "never")       # v no longer hops...
    assert scene._wire_items[v_id].hops == []
    assert scene._wire_items[h_id].hops and scene._wire_items[h_id].hops[0].point == (2.0, 1.0)
    scene.undo()
    assert scene._wire_by_id(v_id).hop_mode == ""
    assert scene._wire_items[v_id].hops


def test_always_hop_mode_shows_even_when_global_off(scene: SchematicScene):
    """An 'always' wire shows its bump even with the global line-hops preference off."""
    scene.set_line_hops(False)                   # global default off
    scene.add_wire([(0.0, 1.0), (4.0, 1.0)])     # h
    scene.add_wire([(2.0, 0.0), (2.0, 3.0)])     # v
    h_id = scene.schematic.wires[0].id
    assert scene._wire_items[h_id].hops == []    # nothing hops while off + default
    scene.set_wire_hop_mode(h_id, "always")
    assert scene._wire_items[h_id].hops and scene._wire_items[h_id].hops[0].point == (2.0, 1.0)


def test_hop_bump_bulges_off_the_line(scene: SchematicScene):
    """The bump arcs perpendicular to (above) a horizontal hopping wire."""
    scene.add_wire([(0.0, 1.0), (4.0, 1.0)])   # horizontal at y=1
    scene.add_wire([(2.0, 0.0), (2.0, 3.0)])   # vertical
    h_id = scene.schematic.wires[0].id
    scene.set_wire_z_order(h_id, 1)            # make the horizontal wire hop
    item = scene._wire_items[h_id]
    pts = [scene.gu_to_scene(*p) for p in item.wire.points]
    path = item._build_wire_path(pts)
    line_y = scene.gu_to_scene(0.0, 1.0).y()
    assert path.boundingRect().top() < line_y - 1.0   # bump extends above the line


# ---------------------------------------------------------------------------
# Live line-hops during gestures (drawing / dragging)
# ---------------------------------------------------------------------------

def test_wire_draw_preview_shows_live_hops(scene: SchematicScene):
    """While drawing a wire across an existing one, the ghost shows a live bump."""
    scene.add_wire([(2.0, 0.0), (2.0, 3.0)])     # existing vertical at x=2
    scene.enter_wire_mode()
    _wire_press(scene, (0.0, 1.0))               # anchor the new wire at (0,1)
    _wire_move(scene, (4.0, 1.0))                # drag across the vertical wire
    pv = scene._wire_preview
    assert pv is not None
    assert pv.hops and pv.hops[0].point == (2.0, 1.0)   # the new wire hops (later → tie)


def test_wire_draw_preview_updates_existing_wire_then_clears(scene: SchematicScene):
    """A higher-z existing wire shows the bump as the new wire is drawn across it,
    and the bump clears when the in-progress wire is discarded."""
    a = scene.add_wire([(2.0, 0.0), (2.0, 3.0)])
    scene.set_wire_z_order(a.id, 1)              # A outranks the new wire → A hops
    scene.enter_wire_mode()
    _wire_press(scene, (0.0, 1.0))
    _wire_move(scene, (4.0, 1.0))                # preview crosses A at (2,1)
    assert scene._wire_items[a.id].hops and scene._wire_items[a.id].hops[0].point == (2.0, 1.0)
    assert scene._wire_preview.hops == []        # the lower-z preview wire does not hop
    scene.cancel_current()                       # discard the in-progress wire
    assert scene._wire_items[a.id].hops == []    # the live bump clears


def test_vertex_drag_preview_shows_live_hops(scene: SchematicScene):
    """Dragging a wire endpoint across another wire shows a live bump on the dragged wire."""
    scene.add_wire([(2.0, 0.0), (2.0, 4.0)])     # A: vertical at x=2 (index 0)
    b = scene.add_wire([(0.0, 1.0), (1.0, 1.0)]) # B: short horizontal (index 1)
    _sel_press(scene, (1.0, 1.0))                # grab B's free endpoint
    _sel_move(scene, (4.0, 1.0))                 # extend B across A at (2,1)
    item_b = scene._wire_items[b.id]
    assert item_b.preview_points is not None
    assert item_b.hops and item_b.hops[0].point == (2.0, 1.0)


# ---------------------------------------------------------------------------
# Bring-to-front / send-to-back (shared wire + drawing-component z-stack)
# ---------------------------------------------------------------------------

def test_bring_wire_to_front_above_components_and_wires(scene: SchematicScene):
    """A wire brought to front lands above every other z-ordered object."""
    a = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    b = scene.add_wire([(0.0, 1.0), (2.0, 1.0)])
    r = scene.place_component("rect", (5.0, 5.0))   # a DrawingComponent
    scene.set_wire_z_order(b.id, 3)
    scene.set_component_z_order(r.id, 7)
    new_z = scene.bring_to_front(a.id)
    assert new_z == 8                                # max(3, 7, 0) + 1
    assert scene._wire_by_id(a.id).z_order == 8
    assert scene._wire_items[a.id].zValue() == 8


def test_send_wire_to_back_below_all(scene: SchematicScene):
    a = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    b = scene.add_wire([(0.0, 1.0), (2.0, 1.0)])
    scene.set_wire_z_order(b.id, -2)
    new_z = scene.send_to_back(a.id)
    assert new_z == -3                               # min(-2, 0) - 1
    assert scene._wire_by_id(a.id).z_order == -3


def test_bring_component_to_front_above_wire(scene: SchematicScene):
    """Drawing components and wires share one stack: a rect can front over a wire."""
    r = scene.place_component("rect", (0.0, 0.0))
    w = scene.add_wire([(5.0, 5.0), (7.0, 5.0)])
    scene.set_wire_z_order(w.id, 4)
    new_z = scene.bring_to_front(r.id)
    assert new_z == 5                                # max(4, 0) + 1
    assert scene._component_by_id(r.id).z_order == 5


def test_front_back_baseline_and_undoable(scene: SchematicScene):
    """With no other z-ordered objects, front = 1 / back = -1 (around the z=0
    circuit baseline); both are undoable."""
    a = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    assert scene.bring_to_front(a.id) == 1
    assert scene._wire_by_id(a.id).z_order == 1
    scene.undo()
    assert scene._wire_by_id(a.id).z_order == 0
    assert scene.send_to_back(a.id) == -1
    scene.undo()
    assert scene._wire_by_id(a.id).z_order == 0


def test_plain_component_front_back_and_canvas_zvalue(scene: SchematicScene):
    """A plain circuit component (not a drawing annotation) can be layered now,
    and its canvas item's z-value tracks the model."""
    r = scene.place_component("R", (0.0, 0.0))
    w = scene.add_wire([(5.0, 5.0), (7.0, 5.0)])
    scene.set_wire_z_order(w.id, 4)
    new_z = scene.bring_to_front(r.id)
    assert new_z == 5                                  # max(4, 0) + 1
    assert scene._component_by_id(r.id).z_order == 5
    assert scene._comp_items[r.id].zValue() == 5
    scene.undo()
    assert scene._component_by_id(r.id).z_order == 0
    assert scene._comp_items[r.id].zValue() == 0


# ---------------------------------------------------------------------------
# Right-click context menu — front/back for components and wires
# ---------------------------------------------------------------------------

def _context_event(scene: SchematicScene, gu):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QGraphicsSceneContextMenuEvent
    ev = QGraphicsSceneContextMenuEvent(QGraphicsSceneContextMenuEvent.ContextMenu)
    ev.setScenePos(scene.gu_to_scene(*gu))
    ev.setScreenPos(QPoint(0, 0))
    return ev


def _capture_menu(scene: SchematicScene) -> dict:
    """Replace the (blocking) menu popup with a capture of the scene state it
    would build from: the selection it targets, and whether it was shown."""
    captured: dict = {}

    def fake_show(event):
        captured["shown"] = True
        captured["ids"] = scene.selected_component_ids() + scene.selected_wire_ids()
        event.accept()

    scene._show_item_context_menu = fake_show  # type: ignore[assignment]
    return captured


def test_context_menu_selects_clicked_item(scene: SchematicScene):
    """Right-clicking an unselected item makes it the sole selection target."""
    r1 = scene.place_component("R", (0.0, 0.0))
    scene.place_component("R", (0.0, 2.0))
    captured = _capture_menu(scene)
    scene.contextMenuEvent(_context_event(scene, (0.0, 0.0)))
    assert captured["ids"] == [r1.id]
    assert scene.selected_component_ids() == [r1.id]


def test_context_menu_keeps_existing_multiselection(scene: SchematicScene):
    """Right-clicking inside a multi-selection applies to the whole group."""
    r1 = scene.place_component("R", (0.0, 0.0))
    r2 = scene.place_component("R", (0.0, 2.0))
    scene._comp_items[r1.id].setSelected(True)
    scene._comp_items[r2.id].setSelected(True)
    captured = _capture_menu(scene)
    scene.contextMenuEvent(_context_event(scene, (0.0, 2.0)))   # r2 is in the selection
    assert set(captured["ids"]) == {r1.id, r2.id}


def test_context_menu_empty_space_still_shows(scene: SchematicScene):
    """Right-clicking empty space still raises the menu (for Paste) and leaves the
    existing selection intact."""
    r = scene.place_component("R", (0.0, 0.0))
    scene._comp_items[r.id].setSelected(True)
    captured = _capture_menu(scene)
    scene.contextMenuEvent(_context_event(scene, (50.0, 50.0)))
    assert captured.get("shown") is True
    assert scene.selected_component_ids() == [r.id]   # selection untouched


def test_context_menu_layer_selection_is_one_undo_step(scene: SchematicScene):
    """_layer_selection (what the menu actions call) sends a mixed component/wire
    group to back as a single undoable step and restores on undo."""
    r = scene.place_component("R", (0.0, 0.0))
    w = scene.add_wire([(4.0, 0.0), (4.0, 2.0)])
    before = scene._stack.undo_count
    scene._layer_selection([r.id, w.id], to_front=False)
    assert scene._component_by_id(r.id).z_order < 0
    assert scene._wire_by_id(w.id).z_order < 0
    assert scene._stack.undo_count == before + 1        # one combined step
    scene.undo()
    assert scene._component_by_id(r.id).z_order == 0
    assert scene._wire_by_id(w.id).z_order == 0


def test_cut_selection_copies_then_deletes_in_one_step(scene: SchematicScene):
    """cut_selection puts the item on the clipboard and removes it as one
    undoable delete; undo brings it back."""
    r = scene.place_component("R", (0.0, 0.0))
    scene._comp_items[r.id].setSelected(True)
    before = scene._stack.undo_count
    scene.cut_selection()
    assert scene._component_by_id(r.id) is None             # deleted
    assert len(scene._clipboard_components) == 1            # on the clipboard
    assert scene._stack.undo_count == before + 1           # single step
    scene.undo()
    assert scene._component_by_id(r.id) is not None         # restored


def test_cut_with_no_selection_is_noop(scene: SchematicScene):
    scene.place_component("R", (0.0, 0.0))
    before = scene._stack.undo_count
    scene.cut_selection()
    assert scene._stack.undo_count == before
    assert scene._clipboard_components == []


def test_paste_at_cursor_anchors_group_top_left(scene: SchematicScene):
    """paste(at=…) anchors the clipboard group's top-left corner at the click
    point; the plain paste keeps the fixed 1 GU offset."""
    r = scene.place_component("R", (2.0, 2.0))
    scene._comp_items[r.id].setSelected(True)
    scene.copy_selection()
    scene.paste(at=(10.0, 5.0))
    pasted = [c for c in scene.schematic.components if c.id != r.id]
    assert len(pasted) == 1
    assert pasted[0].position == (10.0, 5.0)               # top-left at cursor


def test_paste_no_position_uses_fixed_offset(scene: SchematicScene):
    r = scene.place_component("R", (2.0, 2.0))
    scene._comp_items[r.id].setSelected(True)
    scene.copy_selection()
    scene.paste()
    pasted = [c for c in scene.schematic.components if c.id != r.id]
    assert pasted[0].position == (3.0, 3.0)                # original + 1 GU


def test_paste_empty_clipboard_is_noop(scene: SchematicScene):
    before = scene._stack.undo_count
    scene.paste(at=(1.0, 1.0))
    assert scene._stack.undo_count == before


def test_right_click_press_does_not_clear_selection(scene: SchematicScene):
    """A SELECT-mode right-button press is swallowed so it cannot disturb the
    selection before the context menu runs."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent
    r = scene.place_component("R", (0.0, 0.0))
    scene._comp_items[r.id].setSelected(True)
    ev = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    ev.setButton(Qt.RightButton)
    ev.setScenePos(scene.gu_to_scene(0.0, 0.0))
    scene.mousePressEvent(ev)
    assert scene._mode == Mode.SELECT
    assert scene.selected_component_ids() == [r.id]     # still selected


def test_wire_inspector_move_buttons_call_through(scene: SchematicScene):
    """WireStyleSection's Move-to-front/back drive the scene front/back methods."""
    from app.ui.properties import WireStyleSection
    a = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    b = scene.add_wire([(0.0, 1.0), (2.0, 1.0)])
    scene.set_wire_z_order(b.id, 5)
    section = WireStyleSection()
    section.bind_wire(scene._wire_by_id(a.id), scene)
    section._move(to_front=True)
    assert scene._wire_by_id(a.id).z_order == 6      # max(5, 0) + 1
    assert section._z_spin.value() == 6
    section._move(to_front=False)
    assert scene._wire_by_id(a.id).z_order == -1     # min(5, 0) - 1


def test_set_component_variant_toggles_and_undoes(scene: SchematicScene):
    """scene.set_component_variant toggles a generic boolean variant, undoably."""
    a = scene.place_component("nigfete", (0.0, 0.0))
    scene.set_component_variant(a.id, "body_diode", True)
    comp = next(c for c in scene.schematic.components if c.id == a.id)
    assert comp.variants.get("body_diode") is True
    scene._stack.undo()
    comp = next(c for c in scene.schematic.components if c.id == a.id)
    assert not comp.variants.get("body_diode")


def test_batch_groups_edits_into_one_undo_step(scene: SchematicScene):
    """scene.batch() collapses multiple mutations into a single MacroCommand:
    one undo reverts them all (used for multi-component inspector edits)."""
    a = scene.place_component("R", (2.0, 0.0))
    b = scene.place_component("R", (6.0, 0.0))
    with scene.batch("Edit options"):
        scene.edit_component_options(a.id, "l=$R_1$")
        scene.edit_component_options(b.id, "l=$R_2$")
    opts = {c.id: c.options for c in scene.schematic.components}
    assert opts[a.id] == "l=$R_1$" and opts[b.id] == "l=$R_2$"

    scene.undo()  # single step
    opts = {c.id: c.options for c in scene.schematic.components}
    assert opts[a.id] == "" and opts[b.id] == ""


def test_batch_with_single_edit_is_not_wrapped(scene: SchematicScene):
    """A batch containing one command pushes it directly (still one undo step)."""
    a = scene.place_component("R", (2.0, 0.0))
    with scene.batch("Edit"):
        scene.edit_component_options(a.id, "l=$R$")
    assert scene.schematic.components[0].options == "l=$R$"
    scene.undo()
    assert scene.schematic.components[0].options == ""


def test_shift_click_adds_to_selection(scene: SchematicScene):
    """Shift-clicking a second component adds it to the selection (multi-select)
    rather than replacing the first; clicking it again toggles it off."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    a = scene.place_component("R", (1.0, 0.0))   # body centre ~ (2,0)
    b = scene.place_component("R", (1.0, 4.0))   # body centre ~ (2,4)
    scene._comp_items[a.id].setSelected(True)

    def shift_click(gx, gy):
        ev = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
        ev.setButton(Qt.LeftButton)
        ev.setModifiers(Qt.ShiftModifier)
        ev.setScenePos(scene.gu_to_scene(gx, gy))
        scene.mousePressEvent(ev)

    shift_click(2.0, 4.0)                          # add the second resistor
    sel = set(scene.selected_component_ids())
    assert a.id in sel and b.id in sel

    shift_click(2.0, 4.0)                          # toggle it back off
    assert b.id not in set(scene.selected_component_ids())
    assert a.id in set(scene.selected_component_ids())


def test_shift_click_over_annotation_decoration_selects_element_beneath():
    """Regression: an `open`/`short` annotation's label and decoration are
    non-selectable children that float *over* the elements it measures. A
    Shift-click on such an element (whose body sits under the annotation's
    decoration) must add **that element** to the selection — matching a plain
    click — not the annotation. (Porous-electrode example: the cell-voltage
    `open` arrow spans across the dependent source and the Zs bipole.)"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGraphicsSceneMouseEvent

    from app.canvas.items import _AnnotationDecoration
    from app.schematic import io

    sch = io.load("examples/Battery Models/Porous Electrode Interface.hv")
    sc = SchematicScene(sch)

    cv = next(c for c in sch.components if c.kind == "cV")
    zs = next(c for c in sch.components if c.kind == "bipole")
    open_item = sc._comp_items[
        next(c for c in sch.components if c.kind == "open").id
    ]

    for comp in (cv, zs):
        item = sc._comp_items[comp.id]
        center = item.sceneBoundingRect().center()
        # Precondition: the annotation's decoration really does cover this point
        # (otherwise the test wouldn't exercise the climb-to-parent bug).
        assert any(
            isinstance(it, _AnnotationDecoration) for it in sc.items(center)
        ), f"{comp.kind} body is not under the annotation decoration"

        sc.clearSelection()
        ev = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
        ev.setButton(Qt.LeftButton)
        ev.setModifiers(Qt.ShiftModifier)
        ev.setScenePos(center)
        sc.mousePressEvent(ev)

        sel = set(sc.selected_component_ids())
        assert comp.id in sel, f"shift-click did not select the {comp.kind}"
        assert open_item.component.id not in sel, "selected the annotation instead"


def test_set_component_line_width_is_undoable(scene: SchematicScene):
    """Setting a component's stroke width is an undoable command."""
    comp = scene.place_component("R", (0.0, 0.0))
    assert comp.line_width == 0.4
    scene.set_component_line_width(comp.id, 1.2)
    assert scene.schematic.components[0].line_width == 1.2
    scene.undo()
    assert scene.schematic.components[0].line_width == 0.4
    scene.redo()
    assert scene.schematic.components[0].line_width == 1.2


def test_stroke_width_section_applies_to_all_but_text():
    """The unified Stroke section targets every drawable kind — circuit symbols
    **and** blocks (rect/circle/bipole) — but not pure text (text_node)."""
    from app.ui.properties import StrokeWidthSection
    from app.components.model import (
        Component, RectComponent, CircleComponent, BipoleComponent,
        TextNodeComponent,
    )
    sec = StrokeWidthSection()
    r = Component(id="x", kind="R", position=(0.0, 0.0), rotation=0, options="")
    rect = RectComponent(id="y", kind="rect", position=(0.0, 0.0), rotation=0, options="")
    circle = CircleComponent(id="z", kind="circle", position=(0.0, 0.0), rotation=0, options="")
    bip = BipoleComponent(id="b", kind="bipole", position=(0.0, 0.0), rotation=0, options="")
    text = TextNodeComponent(id="t", kind="text_node", position=(0.0, 0.0), rotation=0, options="")
    assert sec.applies_to(r) is True
    assert sec.applies_to(rect) is True
    assert sec.applies_to(circle) is True
    assert sec.applies_to(bip) is True
    assert sec.applies_to(text) is False


def test_open_european_voltage_label_sits_beside_arrow(scene: SchematicScene):
    """With european voltage the open annotation's label sits beside its curved
    arrow (not centred on the line); american keeps it centred (§5.8)."""
    comp = scene.place_component("open", (0.0, 0.0))
    scene.schematic.voltage_style = "european"
    scene.edit_component_options(comp.id, "v=$v$")
    scene.relayout_annotations()
    item = scene._comp_items[comp.id]
    assert next(s for s in item._slot_items if s.isVisible())._centered is False

    scene.schematic.voltage_style = "american"
    scene.relayout_annotations()
    assert next(s for s in item._slot_items if s.isVisible())._centered is True


# ---------------------------------------------------------------------------
# Batch — commands inside a batch run immediately (fresh old-value capture)
# ---------------------------------------------------------------------------

def test_batch_commands_see_fresh_state(scene: SchematicScene):
    """Regression: batch() used to defer do() to the flush, so a command built
    inside the batch captured pre-batch state. bring_to_front twice in one
    batch must stack the second object ABOVE the first, not give both z=1."""
    a = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    b = scene.add_wire([(0.0, 2.0), (2.0, 2.0)])
    with scene.batch("Arrange"):
        scene.bring_to_front(a.id)
        scene.bring_to_front(b.id)
    assert scene._wire_by_id(a.id).z_order == 1
    assert scene._wire_by_id(b.id).z_order == 2          # saw a's fresh z
    scene.undo()                                          # one step undoes both
    assert scene._wire_by_id(a.id).z_order == 0
    assert scene._wire_by_id(b.id).z_order == 0


def test_batch_records_without_reexecuting(scene: SchematicScene):
    """The flush records the already-applied commands; nothing runs twice."""
    a = scene.place_component("R", (2.0, 0.0))
    before = scene._stack.undo_count
    with scene.batch("Edit"):
        scene.edit_component_options(a.id, "l=$R_1$")
        scene.edit_component_options(a.id, "l=$R_2$")    # sees the first edit
    assert scene._component_by_id(a.id).options == "l=$R_2$"
    assert scene._stack.undo_count == before + 1          # one macro recorded
    scene.undo()
    assert scene._component_by_id(a.id).options == ""
    scene.redo()
    assert scene._component_by_id(a.id).options == "l=$R_2$"


# ---------------------------------------------------------------------------
# Delete dissolving two junctions that share a wire — merges must compose
# ---------------------------------------------------------------------------

def test_delete_two_junctions_sharing_wire_merges_chain(scene: SchematicScene):
    """Deleting both taps of a bus dissolves two T-junctions that share the
    middle wire. The two merges must compose into ONE through wire (the second
    merge re-resolves the wire consumed by the first); undo restores all."""
    mid = scene.add_wire([(2.0, 0.0), (6.0, 0.0)])
    left = scene.add_wire([(0.0, 0.0), (2.0, 0.0)])
    right = scene.add_wire([(6.0, 0.0), (8.0, 0.0)])
    tap1 = scene.add_wire([(2.0, 0.0), (2.0, 3.0)])
    tap2 = scene.add_wire([(6.0, 0.0), (6.0, 3.0)])
    assert len(scene.schematic.wires) == 5
    scene._wire_items[tap1.id].setSelected(True)
    scene._wire_items[tap2.id].setSelected(True)
    scene.delete_selected()
    assert len(scene.schematic.wires) == 1               # fully fused chain
    merged = scene.schematic.wires[0]
    assert sorted([merged.points[0], merged.points[-1]]) == [(0.0, 0.0), (8.0, 0.0)]
    scene.undo()                                          # single undo step
    ids = {w.id for w in scene.schematic.wires}
    assert ids == {mid.id, left.id, right.id, tap1.id, tap2.id}


# ---------------------------------------------------------------------------
# Endpoint drag — no-move release restores the model component
# ---------------------------------------------------------------------------

def test_endpoint_drag_and_return_restores_component(scene: SchematicScene):
    """Regression: the preview swaps the item's component for a replace() copy;
    a release without movement cleared wire ghosts but never swapped the model
    component back, leaving the item desynced (stuck at the preview span)."""
    from app.components.registry import REGISTRY
    comp = scene.place_component("open", (0.0, 0.0))
    old_span = REGISTRY["open"].default_span
    item = scene._comp_items[comp.id]
    scene._drag.endpoint_drag = (comp.id, 1, old_span)
    scene._drag.endpoint_press_gu = (1.0, 0.0)
    # Preview a stretch: the item now renders a temporary span copy.
    scene._drag.preview_endpoint_drag((6.0, 0.0))
    assert item.component.span_override == (6.0, 0.0)
    assert item.component is not scene._component_by_id(comp.id)
    # Release back at the press point (no movement).
    ev = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseRelease)
    ev.setButton(Qt.LeftButton)
    ev.setScenePos(scene.gu_to_scene(1.0, 0.0))
    scene.mouseReleaseEvent(ev)
    # The item aliases the live model component again, at the original span.
    assert item.component is scene._component_by_id(comp.id)
    assert item._effective_span() == old_span
    assert scene._stack.undo_count == 1                  # only the placement


def test_commit_endpoint_drag_routes_through_push(scene: SchematicScene):
    """commit_endpoint_drag goes through scene._push (undoable via the scene,
    schematic_changed emitted) instead of poking the stack directly."""
    from app.components.registry import REGISTRY
    comp = scene.place_component("open", (0.0, 0.0))
    old_span = REGISTRY["open"].default_span
    fired: list[int] = []
    scene.schematic_changed.connect(lambda: fired.append(1))
    scene._drag.commit_endpoint_drag(comp.id, old_span, (5.0, 0.0))
    assert scene._component_by_id(comp.id).span_override == (5.0, 0.0)
    assert len(fired) == 1
    scene.undo()
    assert scene._component_by_id(comp.id).span_override is None or \
        scene._component_by_id(comp.id).span_override == old_span


def test_endpoint_drag_has_no_dead_zone_near_origin(scene: SchematicScene):
    """Regression: a 0.5 GU dead-zone used to freeze the preview near the other
    endpoint (the 'sticks near a pin' bug). The terminal now follows the cursor
    at 0.25 granularity right down to a 0.25 span, and that span commits."""
    comp = scene.place_component("short", (5.0, 0.0))   # endpoints (5,0)-(7,0)
    old_span = comp.span_override or (2.0, 0.0)
    item = scene._comp_items[comp.id]
    scene._drag.endpoint_drag = (comp.id, 1, old_span)
    scene._drag.endpoint_press_gu = (7.0, 0.0)
    # Drag the terminal to 0.25 from the origin — previously frozen at 0.5.
    scene._drag.preview_endpoint_drag((5.25, 0.0))
    assert item.component.span_override == (0.25, 0.0)   # followed, not stuck
    scene._drag.commit_endpoint_drag(comp.id, old_span, (5.25, 0.0))
    assert scene._component_by_id(comp.id).span_override == (0.25, 0.0)


def test_origin_handle_moves_on_small_drag(scene: SchematicScene):
    """Regression: the origin handle used to resist a sub-0.5 GU drag (dead-zone),
    feeling stuck on pickup. A 0.25 GU origin drag now moves and commits."""
    comp = scene.place_component("short", (5.0, 0.0))
    old_span = comp.span_override or (2.0, 0.0)
    item = scene._comp_items[comp.id]
    scene._drag.endpoint_drag = (comp.id, 0, old_span)   # origin handle
    scene._drag.endpoint_press_gu = (5.0, 0.0)
    scene._drag.preview_endpoint_drag((4.75, 0.0))       # tiny 0.25 move
    assert item.component.span_override == (2.25, 0.0)    # terminal held, origin moved
    scene._drag.commit_endpoint_drag(comp.id, old_span, (4.75, 0.0), handle_idx=0)
    c = scene._component_by_id(comp.id)
    assert c.position == (4.75, 0.0) and c.span_override == (2.25, 0.0)


# ---------------------------------------------------------------------------
# Origin-endpoint drag — annotations are draggable from EITHER end
# ---------------------------------------------------------------------------

def _endpoint_release(scene: SchematicScene, gu):
    rel = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseRelease)
    rel.setButton(Qt.LeftButton)
    rel.setScenePos(scene.gu_to_scene(*gu))
    scene.mouseReleaseEvent(rel)


def test_line_annotation_endpoints_are_both_draggable(scene: SchematicScene):
    """Open/short/bipole line annotations expose an origin handle (index 0) and a
    terminal handle (index 1); boxes (rect/circle) expose only the terminal."""
    from PySide6.QtCore import QPointF
    for kind in ("open", "short", "bipole"):
        comp = scene.place_component(kind, (0.0, 0.0))
        item = scene._comp_items[comp.id]
        ep = item._endpoint_px()
        assert item._origin_draggable() is True
        assert item.endpoint_handle_index_at(QPointF(0.0, 0.0)) == 0
        assert item.endpoint_handle_index_at(QPointF(ep.x(), ep.y())) == 1
    for kind in ("rect", "circle"):
        comp = scene.place_component(kind, (0.0, 0.0))
        item = scene._comp_items[comp.id]
        ep = item._endpoint_px()
        assert item._origin_draggable() is False
        assert item.endpoint_handle_index_at(QPointF(0.0, 0.0)) is None
        assert item.endpoint_handle_index_at(QPointF(ep.x(), ep.y())) == 1


def test_origin_drag_holds_terminal_fixed(scene: SchematicScene):
    """Dragging the origin endpoint moves the component while the terminal stays
    put; undo restores both position and span."""
    from app.schematic.model import component_pin_positions
    comp = scene.place_component("short", (2.0, 2.0))   # origin (2,2), term (4,2)
    terminal = component_pin_positions(comp)[1]
    _wire_press(scene, (2.0, 2.0))
    assert scene._mode == Mode.SELECT
    assert scene._drag.endpoint_drag is not None
    assert scene._drag.endpoint_drag[1] == 0            # origin handle
    _wire_move(scene, (0.0, 3.0))
    _endpoint_release(scene, (0.0, 3.0))
    pins = component_pin_positions(scene._component_by_id(comp.id))
    assert pins[0] == (0.0, 3.0)                          # origin followed cursor
    assert pins[1] == terminal                            # terminal unchanged
    scene.undo()
    assert component_pin_positions(scene._component_by_id(comp.id)) == [(2.0, 2.0), (4.0, 2.0)]


def test_origin_press_does_not_enter_wire_mode(scene: SchematicScene):
    """Regression: a current (short) annotation's origin coincides with a
    connectable pin, so pressing-and-holding it used to auto-enter WIRE mode.
    The endpoint handle now wins, so the press starts an origin drag instead."""
    scene.place_component("short", (1.0, 1.0))
    _wire_press(scene, (1.0, 1.0))
    assert scene._mode == Mode.SELECT                     # NOT Mode.WIRE
    assert scene._drag.endpoint_drag is not None
    assert scene._drag.endpoint_drag[1] == 0


def test_origin_drag_reshapes_connected_wire(scene: SchematicScene):
    """A wire connected at the dragged origin follows; the terminal's wires don't."""
    comp = scene.place_component("short", (2.0, 2.0))
    w = scene.add_wire([(2.0, 2.0), (2.0, 5.0)])         # connected at the origin
    _wire_press(scene, (2.0, 2.0))
    _wire_move(scene, (0.0, 2.0))
    _endpoint_release(scene, (0.0, 2.0))
    pts = scene._wire_by_id(w.id).points
    assert pts[0] == (0.0, 2.0)                           # endpoint followed origin
    assert pts[-1] == (2.0, 5.0)                          # far end stayed
    scene.undo()
    assert scene._wire_by_id(w.id).points == [(2.0, 2.0), (2.0, 5.0)]


def test_origin_drag_and_return_pushes_nothing(scene: SchematicScene):
    """A press-and-release on the origin handle with no movement selects the item
    and pushes no command, restoring it to its live model component."""
    comp = scene.place_component("open", (0.0, 0.0))
    item = scene._comp_items[comp.id]
    before = scene._stack.undo_count
    _wire_press(scene, (0.0, 0.0))
    _endpoint_release(scene, (0.0, 0.0))
    assert scene._stack.undo_count == before
    assert item.component is scene._component_by_id(comp.id)


# ---------------------------------------------------------------------------
# Drag previews — junction dots must honour no_junction_dots mid-drag
# ---------------------------------------------------------------------------

def test_junction_preview_respects_no_junction_dots(scene: SchematicScene):
    """Regression: the drag preview recomputed junction degree by hand and
    ignored the per-wire no_junction_dots opt-out, so a suppressed dot
    flickered into existence during any drag."""
    scene.add_wire([(0.0, 0.0), (4.0, 0.0)])
    tap = scene.add_wire([(2.0, 0.0), (2.0, 3.0)])       # T-junction (bus splits)
    assert scene._junction_items != {}                    # dot while enabled
    scene.set_wire_no_junction_dots(tap.id, True)
    assert scene._junction_items == {}                    # committed: no dot
    scene._drag.update_junction_preview({})               # any drag repaint
    assert scene._junction_items == {}                    # preview: still none


# ---------------------------------------------------------------------------
# Component-drag commit — selected wires translate exactly once
# ---------------------------------------------------------------------------

def test_commit_component_drag_multiple_deltas_moves_wires_once(scene: SchematicScene):
    """Latent bug: with several per-delta groups, attaching the same selected
    wire set to each group's MoveCommand translated the wires once per group
    (by different deltas). The wires must move exactly once."""
    a = scene.place_component("R", (0.0, 0.0))
    b = scene.place_component("R", (0.0, 4.0))
    w = scene.add_wire([(10.0, 10.0), (12.0, 10.0)])
    # Simulate a finished drag whose two items snapped to DIFFERENT deltas.
    scene._drag.drag_start = {a.id: a.position, b.id: b.position}
    scene._drag.drag_wire_ids = {w.id}
    scene._comp_items[a.id].setPos(scene.gu_to_scene(1.0, 0.0))   # delta (1,0)
    scene._comp_items[b.id].setPos(scene.gu_to_scene(2.0, 4.0))   # delta (2,0)
    scene._drag.commit_component_drag()
    moved = scene._wire_by_id(w.id)
    assert moved.points == [(11.0, 10.0), (13.0, 10.0)]   # one delta, not the sum
    assert scene._component_by_id(a.id).position == (1.0, 0.0)
    assert scene._component_by_id(b.id).position == (2.0, 4.0)
    scene.undo()                                          # one undoable action
    assert scene._wire_by_id(w.id).points == [(10.0, 10.0), (12.0, 10.0)]
    assert scene._component_by_id(a.id).position == (0.0, 0.0)
    assert scene._component_by_id(b.id).position == (0.0, 4.0)


# ---------------------------------------------------------------------------
# Structural re-entrancy safety (grab release on removal; coalesced rebuilds)
# ---------------------------------------------------------------------------

def test_push_during_grab_ungrabs_before_removal(scene: SchematicScene):
    """Pushing a command that removes the mouse-grabbing item mid-gesture must
    release the grab before the item is freed (the dangerous sequence that
    previously relied on handler ordering alone)."""
    from app.canvas.commands import DeleteCommand

    a = scene.place_component("R", (0.0, 0.0))
    item = scene._comp_items[a.id]
    item.setSelected(True)
    press = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMousePress)
    press.setButton(Qt.LeftButton)
    press.setScenePos(scene.gu_to_scene(1.0, 0.0))       # body centre
    scene.mousePressEvent(press)
    assert scene.mouseGrabberItem() is item              # Qt grabbed the item

    # The dangerous moment: a command removes the grabbing item while the
    # press is still in flight. _remove_item must ungrab first.
    scene._push(DeleteCommand([a.id]))
    assert scene.mouseGrabberItem() is None
    assert a.id not in scene._comp_items

    # The release is then delivered safely (no grabber, no crash).
    rel = QGraphicsSceneMouseEvent(QGraphicsSceneMouseEvent.GraphicsSceneMouseRelease)
    rel.setButton(Qt.LeftButton)
    rel.setScenePos(scene.gu_to_scene(1.0, 0.0))
    scene.mouseReleaseEvent(rel)


def test_remove_item_ungrabs_grabbing_child(scene: SchematicScene):
    """Removing an item whose CHILD holds the mouse grab releases the grab too
    (the replace/teardown path: destroying an ancestor destroys the grabbing
    child with it)."""
    a = scene.place_component("R", (0.0, 0.0))
    item = scene._comp_items[a.id]
    child = next(iter(item.childItems()), None)
    assert child is not None
    child.setVisible(True)                   # an invisible item cannot grab
    child.grabMouse()
    assert scene.mouseGrabberItem() is child

    scene._remove_item(scene._comp_items.pop(a.id))
    assert scene.mouseGrabberItem() is None


def test_remove_item_keeps_unrelated_grab(scene: SchematicScene):
    """Removing an item must not disturb a grab held by an unrelated item."""
    a = scene.place_component("R", (0.0, 0.0))
    b = scene.place_component("R", (6.0, 0.0))
    grab_item = scene._comp_items[b.id]
    grab_item.grabMouse()
    assert scene.mouseGrabberItem() is grab_item

    scene._remove_item(scene._comp_items.pop(a.id))
    assert scene.mouseGrabberItem() is grab_item
    grab_item.ungrabMouse()


def test_reentrant_rebuild_coalesces(scene: SchematicScene, monkeypatch):
    """A _rebuild_items call arriving while a rebuild is already running must
    not recurse into a half-reconciled item map: it is deferred and the outer
    invocation loops once more until clean."""
    scene.place_component("R", (0.0, 0.0))

    calls = {"n": 0, "reentered": False}
    orig_body = scene._rebuild_items_now

    def body():
        calls["n"] += 1
        if not calls["reentered"]:
            calls["reentered"] = True
            depth_before = calls["n"]
            scene._rebuild_items()           # re-entrant request mid-rebuild
            # The nested call must NOT have run the body inline.
            assert calls["n"] == depth_before
        orig_body()

    monkeypatch.setattr(scene, "_rebuild_items_now", body)
    scene._rebuild_items()
    # Outer pass + exactly one coalesced re-run; no unbounded recursion.
    assert calls["n"] == 2
    assert scene._rebuilding is False
    assert scene._rebuild_pending is False


def test_push_during_rebuild_signal_is_safe(scene: SchematicScene):
    """A handler reacting to selectionChanged (fired synchronously from inside
    a rebuild, e.g. when a selected item is removed) may push another command;
    the nested rebuild request coalesces and both commands land in the model."""
    from app.canvas.commands import DeleteCommand

    a = scene.place_component("R", (0.0, 0.0))
    b = scene.place_component("R", (6.0, 0.0))
    scene._comp_items[a.id].setSelected(True)

    fired = {"done": False}

    def on_selection_changed():
        # Triggered when the selected item is removed mid-rebuild.
        if not fired["done"] and a.id not in {c.id for c in scene.schematic.components}:
            fired["done"] = True
            scene._push(DeleteCommand([b.id]))           # push during rebuild

    scene.selectionChanged.connect(on_selection_changed)
    try:
        scene._push(DeleteCommand([a.id]))
    finally:
        scene.selectionChanged.disconnect(on_selection_changed)

    assert fired["done"], "the re-entrant push path was not exercised"
    assert scene.schematic.components == []
    assert scene._comp_items == {}
