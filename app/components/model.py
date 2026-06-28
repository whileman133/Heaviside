"""
Component data model — static definitions and per-instance component classes.

ComponentDef and PinDef are frozen dataclasses that live in the registry.
The Component hierarchy holds per-instance state for placed components;
ComponentDef.component_class points to the appropriate subclass for each kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    """Raw CircuiTikZ to[] / node[] option string, e.g. "l=$R_1$, v=$V_s$".

    For a **node-style** kind (single- or multi-terminal node, see
    ``app.codegen.circuitikz.is_node_style``) this is the ``node[…]`` *bracket*
    only; the text in the trailing ``{…}`` slot lives in :attr:`node_text`."""

    mirror: bool = False
    """Horizontal mirror applied before rotation."""

    node_text: str = ""
    """Text for the ``{…}`` slot of a **node-style** component's emitted
    ``(x,y) node[…] {TEXT}`` (e.g. a transistor's ``$Q_1$`` or a power rail's
    ``$V_{cc}$``). Distinct from :attr:`options`, which carries the ``[…]`` bracket.

    Meaningful only for node-style kinds (``is_node_style``); path-style ``to[…]``
    components and drawing annotations ignore it. Persisted (``schematic/io.py``)
    only when non-empty. A legacy power-rail's ``l=`` label is migrated into this
    field on load (the rail's name now renders from the ``{…}`` slot)."""

    node_side: str = ""
    """Placement keyword for a **single-terminal node**'s emitted
    ``\\node[kind, <side>] at (x,y) {…}`` — one of ``""`` (centred on the
    coordinate, the default), ``"left"``, ``"right"``, ``"above"``, ``"below"``.

    A TikZ placement key sets the node's *anchor* to the opposite side, so the symbol
    sits on the named side of its coordinate: ``left`` ⇒ ``anchor=east`` ⇒ the body
    sits to the left, touching the point. This is how an inversion bubble (``ocirc``/
    ``notcirc``) is made **tangent** to a gate's input/output — the user picks the side
    explicitly (it is **not** inferred from gate context). Meaningful only for
    single-terminal node kinds (the inspector exposes it for those); other kinds ignore
    it. Persisted (``schematic/io.py``) only when non-empty."""

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

    variants: dict[str, bool] = field(default_factory=dict)
    """Active boolean variants, keyed by the variant names the component's
    *kind* declares in ``components/definitions.json`` (e.g. ``{"filled": True}``
    for a diode, ``{"body_diode": True}`` for a MOSFET).  A generic replacement
    for the former ``DiodeComponent.filled`` / ``MosfetComponent.body_diode``
    fields; the declared variants (name → TikZ token + mode) live in the
    component library and are surfaced via :mod:`app.components.library`.
    Only ``True`` entries are persisted (see ``schematic/io.py``).
    """

    params: dict[str, int] = field(default_factory=dict)
    """Integer parameters for a *parametric* kind (e.g. ``{"inputs": 4}`` for a
    logic gate's input count).  The kind declares the parameter (name, min, max,
    default) in ``components/definitions.json``; an empty/absent value means the
    declared default.  Surfaced via :mod:`app.components.library`; persisted in
    ``schematic/io.py`` only when it differs from the default."""

    scale: float = 1.0
    """Uniform size multiplier for the component's symbol, **meaningful for logic
    gates only** (``and``/``or``/``not``/``buffer``/… — every kind whose CircuiTikZ
    keyword ends in `` port``).

    Scales the gate body about its ``out`` pin; the input/output pins move to the
    **true scaled anchor** (``base_offset * scale``), generally off the 0.25-GU
    grid (see :func:`app.components.library.gate_layout`). A wire connects there
    directly — endpoints snap onto component pins (the magnet), so no lead stub is
    needed and the connection is an ordinary, styleable wire. Logic gates default
    to **0.5** at placement (compact); other kinds keep ``1.0`` and ignore this
    field. Persisted (``schematic/io.py``) only when it differs from 1.0."""

    line_width: float = 0.4
    """Stroke/outline width (pt) for the component, **CircuiTikZ default 0.4**.

    The single, unified width property for every drawable kind: the **stroke** of a
    circuit symbol *and* the **outline** of a block component (rect/circle/bipole).
    It lives on the base so all kinds share one field — there is no separate
    ``border_width``. (A dedicated mixin can't host it: a defaulted mixin field
    would have to precede ``Component``'s required ``id``/``kind``/… fields and
    raise the dataclass "non-default argument follows default argument" error.)

    Drawn proportionally on the canvas and emitted as a ``line width=<w>pt`` option
    — for symbols via :func:`app.codegen.circuitikz._line_width_opt` in the
    ``to[]`` / ``node[]``, and for block kinds via
    :func:`app.components.style.compose_style_options` in their ``\\draw`` / node
    option list. Meaningful for every kind except pure text (``text_node`` has no
    stroke). Persisted (``schematic/io.py``) only when it differs from 0.4; a legacy
    file's ``border_width`` on a block is read back into this field."""

    z_order: int = 0
    """Canvas and code-generation layer, shared by every component and wire.

    Positive values are drawn/emitted later (in front); negative values are
    drawn/emitted earlier (behind); the default 0 is the baseline of the plain
    circuit. In the LaTeX output a component with ``z_order < 0`` is emitted in
    its own ``\\draw`` statement *before* the main draw block and ``z_order > 0``
    *after* it (see :func:`app.codegen.circuitikz.generate`); a drawing
    annotation (rect/text/bipole) keeps its existing background/foreground rule.
    On the Qt canvas this maps directly to ``QGraphicsItem.setZValue()``.
    Persisted (``schematic/io.py``) only when non-zero."""


@dataclass
class DrawingComponent(Component):
    """Non-circuit visual element (text_node, rect, circle, bipole).

    A marker subclass: it carries no extra fields (``z_order`` now lives on the
    base :class:`Component`) but routes these kinds through the drawing-annotation
    code paths (no named circuit pins, emitted as standalone LaTeX commands) and
    is matched by ``isinstance`` checks throughout the canvas and code generator.
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
    """Mixin: fill + line style (shared by rect, circle, and bipole).

    The outline **width** is not here — it is the unified ``Component.line_width``
    (see its docstring), shared with circuit symbols, so one inspector control and
    one command edit the stroke/border width of every kind.
    """

    fill_color: str = ""
    """TikZ fill color string (e.g. ``"yellow!20"``).  Empty = no fill (transparent)."""

    line_style: str = ""
    """Raw TikZ line-style tokens (e.g. ``"dashed"``, ``"dotted"``).  Empty = solid."""


@dataclass
class TextNodeComponent(FontedComponent, DrawingComponent):
    """Freestanding text annotation.  Carries font fields via FontedComponent."""


@dataclass
class RectComponent(FontedComponent, StyledComponent, DrawingComponent):
    """Rectangle drawing element (also used for block-diagram boxes).

    Fill, border width, and line style are carried as StyledComponent fields;
    ``span_override`` holds the (width, height) in GU.  Font fields come from
    FontedComponent and style the centred text label.  ``options`` holds the
    raw LaTeX text fragment drawn centred inside the box (empty = no text).
    """


@dataclass
class CircleComponent(FontedComponent, StyledComponent, DrawingComponent):
    """Circle/ellipse drawing element (block-diagram node).

    Behaves exactly like :class:`RectComponent` — same fill/border/line-style,
    centred text in ``options``, and ``span_override`` = (width, height) of the
    bounding box (a circle when width == height, otherwise an ellipse) — except
    its only wire-connection points are the four cardinal points (N/S/E/W = the
    bounding-box edge midpoints).  A *sibling* of ``RectComponent`` (not a
    subclass) so code generation and canvas painting can distinguish the shapes.
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
    Both values must be multiples of 0.25 (the canvas minor grid; see
    PROJECT_SPEC §3.1)."""


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

    Defaults to :class:`Component` (plain circuit element — including diodes and
    MOSFETs, whose ``filled``/``body_diode`` are now generic variants).  Overridden
    only for the bespoke drawing kinds that carry extra per-instance state:
    ``BipoleComponent``, ``TextNodeComponent``, ``RectComponent``,
    ``CircleComponent``.  See the registry entries for the authoritative mapping.
    """

    text_anchor: tuple[float, float] = (0.0, 0.0)
    """(dx, dy) in GU (canvas y-down) from the node's centre to its CircuiTikZ
    ``text`` anchor — where the inline ``node[…] {…}`` text is **west-anchored**
    (its left edge sits here, extending right). Measured per multi-terminal kind
    (``components/add_text_anchors.py``) so the on-canvas node text lands exactly
    where the compiled figure places it (a transistor's label just right of the
    symbol, an op-amp's centred, a transformer's a unit above). ``(0, 0)`` for
    kinds without a measured anchor (single-terminal nodes keep their own rule)."""
