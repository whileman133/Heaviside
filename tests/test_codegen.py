"""
Phase 4 tests — CircuiTikZ code generation.

All tests are pure: no Qt, no filesystem, no LaTeX.
"""

from __future__ import annotations

import copy
import uuid

import pytest

from app.codegen.circuitikz import generate, _fmt
from app.schematic.model import Component, Schematic, Wire


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())


def _schematic(*components, wires=()) -> Schematic:
    return Schematic(
        version="0.1",
        name="test",
        components=list(components),
        wires=list(wires),
    )


def _comp(kind: str, position=(0.0, 0.0), rotation=0, options="", mirror=False) -> Component:
    return Component(
        id=_uid(),
        kind=kind,
        position=position,
        rotation=rotation,
        options=options,
        mirror=mirror,
    )


def _wire(points) -> Wire:
    return Wire(id=_uid(), points=points)


# ---------------------------------------------------------------------------
# test_resistor_horizontal
# ---------------------------------------------------------------------------

def test_resistor_horizontal() -> None:
    """Single resistor at (0,0), rotation 0, no labels → (0,0) to[R] (2,0)."""
    src = generate(_schematic(_comp("R")))
    assert "(0,0) to[R] (2,0)" in src


# ---------------------------------------------------------------------------
# test_resistor_with_labels
# ---------------------------------------------------------------------------

def test_resistor_with_options() -> None:
    """Resistor with options string → to[R, l=$R_1$, v=$V$]."""
    comp = _comp("R", options="l=$R_1$, v=$V$")
    src = generate(_schematic(comp))
    assert "to[R, l=$R_1$, v=$V$]" in src


# ---------------------------------------------------------------------------
# test_resistor_rotated_90
# ---------------------------------------------------------------------------

def test_resistor_rotated_90() -> None:
    """Resistor at (0,0) rotation 90 → terminal pin rotated correctly.

    Span (2,0) rotated 90° CW in Qt Y-down space → (0,2).
    Origin (0,0), terminal (0,2).
    """
    comp = _comp("R", rotation=90)
    src = generate(_schematic(comp))
    assert "(0,0) to[R] (0,2)" in src


# ---------------------------------------------------------------------------
# test_capacitor_horizontal
# ---------------------------------------------------------------------------

def test_capacitor_horizontal() -> None:
    """Capacitor at (2,0), rotation 0 → (2,0) to[C] (4,0)."""
    src = generate(_schematic(_comp("C", position=(2.0, 0.0))))
    assert "(2,0) to[C] (4,0)" in src


# ---------------------------------------------------------------------------
# test_inductor_horizontal
# ---------------------------------------------------------------------------

def test_inductor_horizontal() -> None:
    """Inductor at (0,0), rotation 0 → (0,0) to[L] (2,0)."""
    src = generate(_schematic(_comp("L")))
    assert "(0,0) to[L] (2,0)" in src


# ---------------------------------------------------------------------------
# test_diode_horizontal
# ---------------------------------------------------------------------------

def test_diode_horizontal() -> None:
    """Diode at (0,0), rotation 0 → (0,0) to[D] (2,0)."""
    src = generate(_schematic(_comp("D")))
    assert "(0,0) to[D] (2,0)" in src


# ---------------------------------------------------------------------------
# test_voltage_source
# ---------------------------------------------------------------------------

def test_voltage_source() -> None:
    """Voltage source at (0,0), rotation 0 → (0,0) to[V] (0,2)."""
    src = generate(_schematic(_comp("V")))
    assert "(0,0) to[V] (0,2)" in src


# ---------------------------------------------------------------------------
# test_opamp_node
# ---------------------------------------------------------------------------

def test_opamp_node() -> None:
    """Op-amp produces node[op amp] syntax."""
    comp = _comp("op amp", position=(1.0, 2.0))
    src = generate(_schematic(comp))
    assert "node[op amp]" in src
    assert "(1,2)" in src


# ---------------------------------------------------------------------------
# test_nmos_node
# ---------------------------------------------------------------------------

def test_nmos_node() -> None:
    """nigfete is placed as node[nigfete] with its geometry correction (spec §7.2).

    The symbol is anchored at the gate pin and stretched horizontally by
    xscale=1.0167 to align the drain/source pins to the 0.5-GU grid.
    """
    comp = _comp("nigfete", position=(0.0, 0.0))
    src = generate(_schematic(comp))
    assert "node[nigfete, xscale=1.0167, anchor=gate]" in src


# ---------------------------------------------------------------------------
# test_wire_straight
# ---------------------------------------------------------------------------

def test_wire_straight() -> None:
    """Two-point wire [(0,0),(2,0)] → (0,0) -- (2,0)."""
    src = generate(_schematic(wires=[_wire([(0.0, 0.0), (2.0, 0.0)])]))
    assert "(0,0) -- (2,0)" in src


# ---------------------------------------------------------------------------
# test_wire_manhattan
# ---------------------------------------------------------------------------

def test_wire_manhattan() -> None:
    """Three-point wire [(0,0),(2,0),(2,2)] → (0,0) -- (2,0) -- (2,2)."""
    src = generate(_schematic(wires=[_wire([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)])]))
    assert "(0,0) -- (2,0) -- (2,2)" in src


# ---------------------------------------------------------------------------
# test_coordinate_formatting_integer
# ---------------------------------------------------------------------------

def test_coordinate_formatting_integer() -> None:
    """Coordinate 2.0 is formatted as '2', not '2.0' or '2.00'."""
    assert _fmt(2.0) == "2"
    assert _fmt(0.0) == "0"
    assert _fmt(-3.0) == "-3"


# ---------------------------------------------------------------------------
# test_coordinate_formatting_half
# ---------------------------------------------------------------------------

def test_coordinate_formatting_half() -> None:
    """Coordinate 1.5 is formatted as '1.5', not '1.50'."""
    assert _fmt(1.5) == "1.5"
    assert _fmt(0.5) == "0.5"
    assert _fmt(-2.5) == "-2.5"


# ---------------------------------------------------------------------------
# test_empty_schematic
# ---------------------------------------------------------------------------

def test_empty_schematic() -> None:
    """Empty schematic produces a valid (empty) circuitikz environment."""
    src = generate(_schematic())
    assert r"\begin{circuitikz}" in src
    assert r"\end{circuitikz}" in src
    assert r"\draw" in src


# ---------------------------------------------------------------------------
# test_generate_is_pure
# ---------------------------------------------------------------------------

def test_generate_is_pure() -> None:
    """Calling generate() twice on the same Schematic produces identical output."""
    s = _schematic(
        _comp("R", position=(0.0, 0.0), options="l=$R_1$"),
        _comp("C", position=(2.0, 0.0)),
        wires=[_wire([(0.0, 0.0), (2.0, 0.0)])],
    )
    assert generate(s) == generate(s)


# ---------------------------------------------------------------------------
# Junction dots — \node[circ]
# ---------------------------------------------------------------------------

def test_junction_emits_circ_node() -> None:
    s = _schematic(
        wires=[
            Wire(id="a", points=[(0.0, 2.0), (2.0, 2.0)]),
            Wire(id="b", points=[(2.0, 0.0), (2.0, 2.0)]),
            Wire(id="c", points=[(2.0, 2.0), (4.0, 2.0)]),
        ]
    )
    src = generate(s)
    assert r"\node[circ] at (2,2) {};" in src
    # The circ node sits after the \draw path's terminating ';'.
    assert src.index(";") < src.index(r"\node[circ]")


def test_no_junction_no_circ_node() -> None:
    s = _schematic(wires=[_wire([(0.0, 0.0), (2.0, 0.0)])])
    assert r"\node[circ]" not in generate(s)


def test_junction_node_count_matches() -> None:
    """Two separate 3-way junctions → two circ nodes."""
    s = _schematic(
        wires=[
            Wire(id="a1", points=[(0.0, 0.0), (2.0, 0.0)]),
            Wire(id="a2", points=[(2.0, 0.0), (2.0, 2.0)]),
            Wire(id="a3", points=[(2.0, 0.0), (4.0, 0.0)]),
            Wire(id="b1", points=[(0.0, 6.0), (2.0, 6.0)]),
            Wire(id="b2", points=[(2.0, 6.0), (2.0, 8.0)]),
            Wire(id="b3", points=[(2.0, 6.0), (4.0, 6.0)]),
        ]
    )
    assert generate(s).count(r"\node[circ]") == 2


def test_open_endpoint_emits_ocirc_node() -> None:
    """A free wire endpoint (not on any pin) emits \\node[ocirc]."""
    s = _schematic(wires=[_wire([(0.0, 0.0), (4.0, 0.0)])])
    src = generate(s)
    assert src.count(r"\node[ocirc]") == 2
    assert r"\node[ocirc] at (0,0) {};" in src
    assert r"\node[ocirc] at (4,0) {};" in src


def test_pin_connected_endpoint_no_ocirc() -> None:
    """A wire endpoint that sits on a component pin does not get \\node[ocirc]."""
    # Resistor at (0,0) → pins at (0,0) and (2,0); wire from (2,0) to (5,0).
    r = _comp("R", position=(0.0, 0.0))
    s = _schematic(r, wires=[_wire([(2.0, 0.0), (5.0, 0.0)])])
    src = generate(s)
    assert r"\node[ocirc] at (2,0) {};" not in src
    assert r"\node[ocirc] at (5,0) {};" in src


def test_no_open_endpoints_no_ocirc() -> None:
    """A wire whose both ends land on pins emits no \\node[ocirc]."""
    r1 = _comp("R", position=(0.0, 0.0))
    r2 = _comp("R", position=(6.0, 0.0))
    s = _schematic(r1, r2, wires=[_wire([(2.0, 0.0), (6.0, 0.0)])])
    assert r"\node[ocirc]" not in generate(s)
