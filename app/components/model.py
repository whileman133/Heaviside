"""
Component data model — static definitions only.

ComponentDef and PinDef are frozen dataclasses that live in the registry.
They are never instantiated per placed component; see schematic/model.py for
the per-instance Component dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PinDef:
    """Named connection point on a component, at a fixed offset from its origin."""

    name: str
    """Logical pin name, e.g. 'in', 'out', 'plus', 'minus', 'gate'."""

    offset: tuple[float, float]
    """(dx, dy) from the component origin in grid units (GU).
    Both values must be multiples of 0.5."""


@dataclass(frozen=True)
class ComponentDef:
    """Static definition of one component type. One instance per kind in the registry."""

    kind: str
    """CircuiTikZ keyword, e.g. 'R', 'C', 'op amp'. Used as the registry key."""

    display_name: str
    """Human-readable name shown in the palette, e.g. 'Resistor'."""

    category: str
    """Palette group, e.g. 'Passives', 'Amplifiers', 'Sources', 'MOSFETs'."""

    bbox: tuple[float, float, float, float]
    """Bounding box (x0, y0, x1, y1) relative to the component origin, in GU."""

    pins: list[PinDef]
    """All named pins. The first pin is treated as the origin/anchor pin."""

    label_slots: list[str]
    """Valid label slot names for this component, e.g. ['l', 'l_', 'v', 'v^', 'i', 'i_']."""

    tikz_keyword: str
    """The exact string passed to CircuiTikZ to[] or node[] argument."""

    default_span: tuple[float, float]
    """(dx, dy) from the origin pin to the terminal pin, in GU.
    For two-terminal devices this equals the offset of the second pin.
    For multi-terminal devices this is (0, 0)."""

    resizable: bool = False
    """If True, the terminal pin can be dragged after placement to resize the
    component.  Only meaningful for two-terminal components.  The actual span
    at a given instance is stored in Component.span_override."""
