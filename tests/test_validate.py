"""
Schematic validation tests — invariant checks in app.schematic.validate.

The load-path consequences of these invariants (SchematicLoadError) are
covered in tests/test_io.py; this file exercises validate() directly.

No Qt, no LaTeX required.
"""

from __future__ import annotations

import uuid

from app.schematic.model import Component, Schematic, Wire
from app.schematic.validate import validate


def _uid() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Wires must have at least two points (spec/model invariant)
# ---------------------------------------------------------------------------

def test_validate_flags_empty_points_wire() -> None:
    s = Schematic(version="0.1", name="w", wires=[Wire(id="w1", points=[])])
    errors = validate(s)
    assert any("at least two points" in e for e in errors)


def test_validate_flags_single_point_wire() -> None:
    s = Schematic(version="0.1", name="w",
                  wires=[Wire(id="w1", points=[(1.0, 1.0)])])
    errors = validate(s)
    assert any("at least two points" in e for e in errors)


def test_validate_accepts_two_point_wire() -> None:
    s = Schematic(version="0.1", name="w",
                  wires=[Wire(id="w1", points=[(0.0, 0.0), (2.0, 0.0)])])
    assert validate(s) == []


# ---------------------------------------------------------------------------
# Non-finite coordinates report errors instead of crashing _is_on_grid
# ---------------------------------------------------------------------------

def test_validate_nonfinite_wire_point_reports_error_not_crash() -> None:
    s = Schematic(version="0.1", name="n", wires=[
        Wire(id="w1", points=[(0.0, 0.0), (float("nan"), 0.0)]),
        Wire(id="w2", points=[(0.0, 1.0), (float("inf"), 1.0)]),
    ])
    errors = validate(s)          # must not raise OverflowError/ValueError
    assert any("w1" in e and "0.25 GU" in e for e in errors)
    assert any("w2" in e and "0.25 GU" in e for e in errors)


def test_validate_clean_schematic_passes() -> None:
    s = Schematic(version="0.1", name="ok", components=[
        Component(id=_uid(), kind="R", position=(0.0, 0.0), rotation=0,
                  options=""),
    ], wires=[Wire(id=_uid(), points=[(0.0, 0.0), (2.0, 0.0)])])
    assert validate(s) == []
