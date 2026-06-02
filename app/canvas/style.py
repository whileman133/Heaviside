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

Maps from the thin (~0.3985 pt) strokes in the SVG manifest.
"""

LINE_W_THICK: float = LINE_W * 2.0
"""Stroke width for thick SVG strokes (~0.797 pt) such as device bodies."""

PIN_R: float = 3.0
"""Radius of pin indicator dots, in pixels."""

# ---------------------------------------------------------------------------
# Colors (ARGB hex strings — compatible with QColor(str) constructor)
# ---------------------------------------------------------------------------

COLOR_NORMAL    = "#FF000000"   # black
COLOR_SELECTED  = "#FF0055CC"   # blue highlight
COLOR_HOVER     = "#FF228B22"   # forest green
COLOR_GHOST     = "#80000000"   # 50 % transparent black (placement preview)
COLOR_PIN       = "#FFCC0000"   # dark red  (pin indicator dots)

# ---------------------------------------------------------------------------
# SVG symbol reference (see app/canvas/svgsym.py)
# ---------------------------------------------------------------------------

MANIFEST_PATH: str = str(resource_path("tools", "circuitikz_svgs", "manifest.json"))
"""Absolute path to the CircuiTikZ SVG export manifest."""

SVG_PT_PER_GU: float = 28.34765
"""SVG point units per grid unit.

Derived from the bipole terminal span: 56.6953 pt == 2 GU.  All component
exports share this single uniform scale, so it is used to convert every
symbol's SVG coordinates into local pixels.
"""
