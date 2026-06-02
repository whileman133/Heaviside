"""
Component data model — static definitions and per-instance component classes.

ComponentDef and PinDef are frozen dataclasses that live in the registry.
The Component hierarchy holds per-instance state for placed components;
ComponentDef.component_class points to the appropriate subclass for each kind.
"""

from __future__ import annotations

from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Per-instance component classes
# ---------------------------------------------------------------------------

@dataclass
class Component:
    """Base per-instance state for a placed circuit component."""

    id: str
    """UUID assigned at placement. Must be unique within a Schematic."""

    kind: str
    """CircuiTikZ keyword; must exist as a key in REGISTRY."""

    position: tuple[float, float]
    """(x, y) of the origin pin in schematic grid coordinates."""

    rotation: int
    """Clockwise rotation in degrees. Must be one of {0, 90, 180, 270}."""

    options: str
    """Raw CircuiTikZ to[] / node[] option string, e.g. "l=$R_1$, v=$V_s$"."""

    mirror: bool = False
    """Horizontal mirror applied before rotation."""

    label_offset: tuple[float, float] | None = None
    """Position of the options label in component-local pixel coordinates.

    ``None`` means the label has not been manually positioned; the canvas
    places it automatically when options are first set (see §8.3).  Once the
    user drags the label this is set to the chosen (dx, dy) offset and
    persisted to the file.
    """

    span_override: tuple[float, float] | None = None
    """Custom (dx, dy) from origin to terminal pin in component-local GU.

    ``None`` means use ``ComponentDef.default_span``.  Set when the user
    drags the terminal endpoint handle of a resizable component.  Only
    meaningful when ``ComponentDef.resizable`` is True.
    """


@dataclass
class DiodeComponent(Component):
    """A diode — supports the filled (``*``) CircuiTikZ variant."""

    filled: bool = False
    """Use the filled variant (e.g. ``D*``) when True."""


@dataclass
class MosfetComponent(Component):
    """A MOSFET — supports the bodydiode CircuiTikZ variant."""

    body_diode: bool = False
    """Draw the intrinsic body diode when True."""


@dataclass
class DrawingComponent(Component):
    """Non-circuit visual element (text_node, rect). Carries a z-order for layering."""

    z_order: int = 0
    """Canvas and code-generation layer.

    Positive values are drawn/emitted later (in front); negative values are
    drawn/emitted earlier (behind).  In the LaTeX output, items with
    z_order < 0 are emitted *before* the main ``\\draw`` block.
    On the Qt canvas, maps directly to ``QGraphicsItem.setZValue()``.
    """


# ── Capability mixins ──────────────────────────────────────────────────────
#
# FontedComponent and StyledComponent are standalone dataclass mixins (despite
# the ``Component`` suffix, kept for naming consistency and to preserve existing
# ``isinstance`` checks).  They are never instantiated alone — concrete classes
# compose them with the DrawingComponent base.
#
# CRITICAL: in every concrete class below, the mixins MUST be listed *before*
# DrawingComponent.  Dataclass fields are ordered by reverse-MRO; listing a
# mixin after DrawingComponent would place its (defaulted) fields ahead of
# Component's required fields and raise "non-default argument follows default
# argument" at import time.


@dataclass
class FontedComponent:
    """Mixin: font styling (shared by text_node and bipole)."""

    font_size: float = 12.0
    """Font size in points for the canvas preview and LaTeX output."""

    font_bold: bool = False
    """Bold weight (\\bfseries in LaTeX)."""

    font_italic: bool = False
    """Italic style (\\itshape in LaTeX)."""

    font_family: str = ""
    """Font family: ``""`` (document default), ``"serif"`` (\\rmfamily),
    ``"sans"`` (\\sffamily), ``"mono"`` (\\ttfamily).
    """


@dataclass
class StyledComponent:
    """Mixin: fill + border styling (shared by rect and bipole)."""

    fill_color: str = ""
    """TikZ fill color string (e.g. ``"yellow!20"``).  Empty = no fill (transparent)."""

    border_width: float = 0.4
    """Border/line width in points.  Default matches the TikZ default (0.4 pt)."""

    line_style: str = ""
    """Raw TikZ line-style tokens (e.g. ``"dashed"``, ``"dotted"``).  Empty = solid."""


@dataclass
class TextNodeComponent(FontedComponent, DrawingComponent):
    """Freestanding text annotation.  Carries font fields via FontedComponent."""


@dataclass
class RectComponent(StyledComponent, DrawingComponent):
    """Rectangle drawing element.

    Fill, border width, and line style are carried as StyledComponent fields;
    ``span_override`` holds the (width, height) in GU.  (``options`` is unused
    for rects — legacy files that stored the style there are migrated on load.)
    """


@dataclass
class BipoleComponent(FontedComponent, StyledComponent, DrawingComponent):
    """Generic labelled bipole with resizable width.

    Emitted as a standalone TikZ node (``\\node[draw, minimum width=W,
    minimum height=H]``) so the box exactly fills the span between the two
    pin coordinates regardless of size.

    ``options`` holds the raw CircuiTikZ-style option string; the ``t=`` slot
    sets the label displayed inside the box (e.g. ``"t=Processor"``).
    Other slots (``l=``, ``v=``, ``i=``) are stored in options but not
    rendered on the node itself (they are ignored at code-generation time).
    ``span_override`` overrides the default (1, 0) span when the user drags
    the terminal endpoint handle to resize.
    """

    font_size: float = 7.0
    """Override default: bipole box is smaller so 7 pt fits better than 12 pt."""


# ---------------------------------------------------------------------------
# Static component definitions
# ---------------------------------------------------------------------------

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

    component_class: type = Component
    """The Component subclass to instantiate for placed instances of this kind.

    Defaults to :class:`Component` (plain circuit element).  Overridden for
    kinds that need extra per-instance state, e.g. ``DiodeComponent``,
    ``MosfetComponent``, ``BipoleComponent``, ``TextNodeComponent``, and
    ``RectComponent``.  See the registry entries for the authoritative mapping.
    """
