"""
Drawing constants shared across all canvas items.

All QPainter coordinates in ComponentItem subclasses are expressed as multiples
of GRID_PX (pixels per grid unit at 1:1 zoom).  Qt's QGraphicsView applies the
current zoom transform automatically, so these constants stay fixed at all zoom
levels — they define the *schematic-space* geometry of each symbol.
"""

from app.resources import resource_path

# ---------------------------------------------------------------------------
# Core spatial constants
# ---------------------------------------------------------------------------

GRID_PX: float = 60.0
"""Pixels per grid unit (GU) at 1:1 zoom."""

LEAD_LEN: float = 15.0
"""Length of lead-in / lead-out wire stubs, in pixels (= 0.25 GU)."""

# ---------------------------------------------------------------------------
# Stroke and pin geometry
# ---------------------------------------------------------------------------

LINE_W: float = 2.0
"""Default stroke width for component bodies and wires, in pixels.

Maps from the thin (~0.3985 pt) strokes in the SVG geometry.
"""

LINE_W_THICK: float = LINE_W * 2.0
"""Stroke width for thick SVG strokes (~0.797 pt) such as device bodies."""

PIN_R: float = 3.0
"""Radius of pin indicator dots, in pixels."""

# ---------------------------------------------------------------------------
# Colors (ARGB hex strings — compatible with the QColor(str) constructor)
# ---------------------------------------------------------------------------
#
# A switchable light/dark palette. Canvas code reads these **module-qualified**
# (``style.COLOR_NORMAL``, never ``from style import COLOR_NORMAL``) and re-reads
# them on every repaint, so ``set_dark()`` can swap the whole canvas at runtime —
# the chrome counterpart lives in ``app/ui/theme.py``. The defaults are the light
# values, so code that never calls ``set_dark`` is unchanged.

_LIGHT = {
    "COLOR_NORMAL":     "#FF000000",   # symbol/wire ink (black)
    "COLOR_SELECTED":   "#FF0055CC",   # blue selection highlight
    "COLOR_HOVER":      "#FF228B22",   # forest-green hover
    "COLOR_GHOST":      "#80000000",   # 50 % transparent ink (placement preview)
    "COLOR_PIN":        "#FFCC0000",   # dark red (pin indicator dots)
    "COLOR_BACKGROUND": "#FFFFFFFF",   # canvas paper; also backs labels / markers
    "COLOR_LABEL_BG":   "#FFFFFFFF",   # opaque backdrop behind typeset labels
    "COLOR_GRID":       "#FFBFBFBF",   # integer-lattice dots (stronger)
    "COLOR_GRID_SUB":   "#FFDCDCDC",   # 0.25 GU minor dots (lighter, still visible)
    "COLOR_GRID_FINE":  "#FFDCDCDC",   # (kept as an alias of the minor dot colour)
}
_DARK = {
    "COLOR_NORMAL":     "#FFE6E6E6",   # near-white ink on a dark canvas
    "COLOR_SELECTED":   "#FF5C9DFF",   # brighter blue (legible on dark)
    "COLOR_HOVER":      "#FF52D273",   # brighter green
    "COLOR_GHOST":      "#80FFFFFF",   # 50 % transparent light ink
    "COLOR_PIN":        "#FFFF6B6B",   # lighter red
    "COLOR_BACKGROUND": "#FF1E1F22",   # dark canvas paper
    "COLOR_LABEL_BG":   "#FF1E1F22",   # backdrop matches the dark canvas
    "COLOR_GRID":       "#FF60636B",   # integer-lattice dots (light on dark)
    "COLOR_GRID_SUB":   "#FF44464D",   # 0.25 GU minor dots (dimmer, still visible)
    "COLOR_GRID_FINE":  "#FF44464D",
}

# Active values — module globals, defaulting to light. ``set_dark`` rebinds them.
COLOR_NORMAL     = _LIGHT["COLOR_NORMAL"]
COLOR_SELECTED   = _LIGHT["COLOR_SELECTED"]
COLOR_HOVER      = _LIGHT["COLOR_HOVER"]
COLOR_GHOST      = _LIGHT["COLOR_GHOST"]
COLOR_PIN        = _LIGHT["COLOR_PIN"]
COLOR_BACKGROUND = _LIGHT["COLOR_BACKGROUND"]
COLOR_LABEL_BG   = _LIGHT["COLOR_LABEL_BG"]
COLOR_GRID       = _LIGHT["COLOR_GRID"]
COLOR_GRID_SUB   = _LIGHT["COLOR_GRID_SUB"]
COLOR_GRID_FINE  = _LIGHT["COLOR_GRID_FINE"]


def set_dark(on: bool) -> None:
    """Swap the canvas palette between light and dark.

    Rebinds the module-level ``COLOR_*`` globals so callers that read them as
    ``style.COLOR_*`` pick up the change on their next repaint. Pair with
    ``app.ui.theme.set_dark`` (chrome) and a canvas ``update()``.
    """
    globals().update(_DARK if on else _LIGHT)


def is_dark() -> bool:
    """True if the dark palette is currently active."""
    return COLOR_BACKGROUND == _DARK["COLOR_BACKGROUND"]

# Opacity applied to the voltage-annotation (open) connecting line so it reads
# as a translucent annotation rather than a solid/dashed wire (§5.9).
OPEN_ANNOTATION_OPACITY: float = 0.3

# ---------------------------------------------------------------------------
# SVG symbol reference (see app/canvas/svgsym.py)
# ---------------------------------------------------------------------------

GEOMETRY_PATH: str = str(resource_path("components", "geometry.json"))
"""Absolute path to the generated CircuiTikZ symbol geometry file."""

SVG_PT_PER_GU: float = 28.34765
"""SVG point units per grid unit.

Derived from the bipole terminal span: 56.6953 pt == 2 GU.  All component
exports share this single uniform scale, so it is used to convert every
symbol's SVG coordinates into local pixels.
"""
