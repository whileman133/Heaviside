"""
Shared UI design tokens and stylesheet fragments (spec §10).

One flat, light visual language — white surfaces, hairline borders, muted icons,
and a single soft-blue accent — so the toolbars, buttons, and dialog form
controls match the component palette. Import these tokens rather than
hard-coding colours, to keep the look from drifting apart again.
"""

from __future__ import annotations

# -- Colour tokens ----------------------------------------------------------
#
# Two palettes (light / dark). The active values are module globals that the QSS
# functions below read at call time, so ``set_dark()`` followed by re-applying
# the stylesheets re-themes the chrome. The canvas counterpart is
# ``app/canvas/style.py``. Defaults are the light values, so callers that never
# switch are unchanged.

_LIGHT = {
    "SURFACE":       "#ffffff",   # primary surface (panels, chrome)
    "SURFACE_ALT":   "#fafafa",   # subtle raised surface (cards)
    "DIVIDER":       "#ececec",   # hairline chrome divider
    "BORDER":        "#cfcfcf",   # input borders
    "BORDER_SOFT":   "#dadada",   # card / soft borders
    "ACCENT":        "#5b87f0",   # the one accent (focus, active, default)
    "HOVER":         "#e8f0fe",   # soft-blue hover/active fill
    "HOVER_BORDER":  "#c5d9fb",   # hover/active border
    "PRESSED":       "#dce7fc",   # pressed fill
    "BUTTON_BG":     "#f4f6fb",   # resting flat-button fill
    "BUTTON_BORDER": "#d7def0",   # resting flat-button border
    "TEXT":          "#333333",   # body text
    "ICON":          "#555555",   # icon tint (toolbars, buttons)
    "ICON_MUTED":    "#888888",   # secondary icons
    # Dialog / reference-table inks (Help & About dialogs, hints).
    "TEXT_MUTED":    "#666666",   # secondary body text (hints, captions)
    "HEADING":       "#2c3e57",   # dialog section titles
    "TABLE_KEY":     "#4a6f9c",   # keycap / shortcut column ink
    "TABLE_HEADER_BG": "#ebf0f6", # reference-table group-header band
    # Welcome-screen step diagram (#AARRGGBB — QColor parses the alpha form).
    "WELCOME_STEP":  "#c85078af",
    "WELCOME_AXIS":  "#b4a0afbe",
    "WELCOME_LABEL": "#d26482aa",
}
_DARK = {
    "SURFACE":       "#2a2c30",
    "SURFACE_ALT":   "#303338",
    "DIVIDER":       "#3a3c42",
    "BORDER":        "#4a4d55",
    "BORDER_SOFT":   "#43464d",
    "ACCENT":        "#6f9bff",
    "HOVER":         "#2f3a52",
    "HOVER_BORDER":  "#3d4f78",
    "PRESSED":       "#37456a",
    "BUTTON_BG":     "#33363c",
    "BUTTON_BORDER": "#444751",
    "TEXT":          "#e6e6e6",
    "ICON":          "#c9ccd1",
    "ICON_MUTED":    "#9aa0a8",
    "TEXT_MUTED":    "#a8acb3",
    "HEADING":       "#d6dde8",
    "TABLE_KEY":     "#9dbbe0",
    "TABLE_HEADER_BG": "#343840",
    "WELCOME_STEP":  "#c88fb3e8",
    "WELCOME_AXIS":  "#b475828f",
    "WELCOME_LABEL": "#d295b0d8",
}

SURFACE = _LIGHT["SURFACE"]
SURFACE_ALT = _LIGHT["SURFACE_ALT"]
DIVIDER = _LIGHT["DIVIDER"]
BORDER = _LIGHT["BORDER"]
BORDER_SOFT = _LIGHT["BORDER_SOFT"]
ACCENT = _LIGHT["ACCENT"]
HOVER = _LIGHT["HOVER"]
HOVER_BORDER = _LIGHT["HOVER_BORDER"]
PRESSED = _LIGHT["PRESSED"]
BUTTON_BG = _LIGHT["BUTTON_BG"]
BUTTON_BORDER = _LIGHT["BUTTON_BORDER"]
TEXT = _LIGHT["TEXT"]
ICON = _LIGHT["ICON"]
ICON_MUTED = _LIGHT["ICON_MUTED"]
TEXT_MUTED = _LIGHT["TEXT_MUTED"]
HEADING = _LIGHT["HEADING"]
TABLE_KEY = _LIGHT["TABLE_KEY"]
TABLE_HEADER_BG = _LIGHT["TABLE_HEADER_BG"]
WELCOME_STEP = _LIGHT["WELCOME_STEP"]
WELCOME_AXIS = _LIGHT["WELCOME_AXIS"]
WELCOME_LABEL = _LIGHT["WELCOME_LABEL"]


def set_dark(on: bool) -> None:
    """Swap the chrome palette between light and dark.

    Rebinds the module-level tokens so the ``*_qss()`` builders below emit the
    new colours next time they are called. The caller must re-apply the
    stylesheets (and regenerate any tinted icons) — see
    ``MainWindow._apply_theme``. Pair with ``app.canvas.style.set_dark``.
    """
    globals().update(_DARK if on else _LIGHT)


def is_dark() -> bool:
    """True if the dark chrome palette is currently active."""
    return SURFACE == _DARK["SURFACE"]


def top_toolbar_qss() -> str:
    """Flat top toolbar: white with a hairline bottom divider, rounded hovers."""
    return f"""
        QToolBar {{ background: {SURFACE}; border: none;
                    border-bottom: 1px solid {DIVIDER}; spacing: 3px; padding: 4px 6px; }}
        QToolButton {{ background: transparent; border: 1px solid transparent;
                       border-radius: 5px; padding: 4px; }}
        QToolButton:hover {{ background: {HOVER}; border-color: {HOVER_BORDER}; }}
        QToolButton:pressed {{ background: {PRESSED}; }}
        QToolButton:checked {{ background: {HOVER}; border-color: {HOVER_BORDER}; }}
    """


def toolbar_dotted_divider_qss() -> str:
    """A vertical dotted divider for the top toolbar (object name
    ``toolbarDottedDivider``) — a dotted hairline drawn down the widget's centre to
    separate the theme group from the buttons on its right."""
    return f"""
        QWidget#toolbarDottedDivider {{ border-left: 2px dotted {ICON_MUTED};
                                        background: transparent; }}
    """


def ribbon_qss() -> str:
    """Flat left tool ribbon: white with a hairline right divider, soft-blue
    active state (matching the active category card), not the native highlight."""
    return f"""
        QToolBar {{ background: {SURFACE}; border: none;
                    border-right: 1px solid {DIVIDER}; spacing: 4px; padding: 6px 4px; }}
        QToolButton {{ background: transparent; border: 1px solid transparent;
                       border-radius: 6px; padding: 5px; min-width: 32px; min-height: 32px; }}
        QToolButton:hover {{ background: {HOVER}; border-color: {HOVER_BORDER}; }}
        QToolButton:pressed {{ background: {PRESSED}; }}
        QToolButton:checked {{ background: {HOVER}; border-color: {HOVER_BORDER}; }}
        QToolButton:checked:hover {{ background: {HOVER}; }}
    """


def flat_button_qss() -> str:
    """Just the flat rounded-pill ``QPushButton`` rules, for applying directly to
    a button whose ancestor's stylesheet would otherwise shadow the app style."""
    return f"""
        QPushButton {{ background: {BUTTON_BG}; border: 1px solid {BUTTON_BORDER};
                       border-radius: 6px; padding: 4px 12px; color: {TEXT}; }}
        QPushButton:hover {{ background: {HOVER}; border-color: {HOVER_BORDER}; }}
        QPushButton:pressed {{ background: {PRESSED}; }}
    """


# -- Side panels (CircuiTikZ source / LaTeX preview) -------------------------
# A consistent "card with a title bar" look: a rounded, bordered frame with a
# header strip (title + a hairline bottom divider). Used by both panels (§10.4/5)
# so they line up and share styling. Object names: the panel frame is passed by
# name; its header child must be named "panelHeader".

def panel_frame_qss(name: str) -> str:
    """Rounded, bordered card frame for a side panel (selected by object name)."""
    return f"#{name} {{ background: {SURFACE}; border: 1px solid {BORDER_SOFT}; border-radius: 6px; }}"


def panel_header_qss() -> str:
    """Header strip inside a panel: transparent, with a hairline bottom divider."""
    return f"#panelHeader {{ background: transparent; border: none; border-bottom: 1px solid {DIVIDER}; }}"


def panel_title_qss() -> str:
    """The panel's title label."""
    return f"border: none; background: transparent; font-weight: 600; font-size: 11px; color: {TEXT};"


def icon_button_qss() -> str:
    """Small flat icon-only buttons in a panel header (e.g. Copy PNG/PDF/SVG)."""
    return f"""
        QToolButton {{ border: 1px solid transparent; border-radius: 5px; padding: 3px; }}
        QToolButton:hover {{ background: {HOVER}; border-color: {HOVER_BORDER}; }}
        QToolButton:pressed {{ background: {PRESSED}; }}
    """


def line_edit_qss() -> str:
    """A themed ``QLineEdit`` (e.g. the palette search box). Native line edits
    follow the OS appearance, but the toolbar light/dark toggle can put the app in
    a mode the OS isn't in, so the search box is themed explicitly to match the
    surrounding chrome. The clear-button icon is left to the platform."""
    return f"""
        QLineEdit {{ background: {SURFACE_ALT}; border: 1px solid {BORDER};
                     border-radius: 5px; padding: 3px 6px; color: {TEXT};
                     selection-background-color: {HOVER}; }}
        QLineEdit:focus {{ border-color: {ACCENT}; }}
    """


def scrollbar_qss() -> str:
    """A clean, minimal scrollbar: a rounded muted handle, a transparent track,
    and no arrow buttons. Once any stylesheet is active on a scroll widget its
    scrollbars stop being native (and render with ugly default arrows), so the
    themed panels/palette style theirs explicitly to match."""
    return f"""
        QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
        QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 0; }}
        QScrollBar::handle:vertical {{
            background: {BORDER}; border-radius: 4px; min-height: 28px; margin: 2px; }}
        QScrollBar::handle:horizontal {{
            background: {BORDER}; border-radius: 4px; min-width: 28px; margin: 2px; }}
        QScrollBar::handle:hover {{ background: {ICON_MUTED}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{
            width: 0; height: 0; background: none; border: none; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
        QAbstractScrollArea::corner {{ background: transparent; border: none; }}
    """


# NOTE: form controls, dialogs, message boxes, tooltips, and tab bars are kept
# **native** and follow the OS appearance — and, when the user forces a mode with
# the toolbar toggle, the application colour scheme is driven via
# ``QGuiApplication.styleHints().setColorScheme`` (MainWindow._apply_color_scheme),
# so those native widgets re-render dark/light themselves. Restyling them via a
# stylesheet made them look non-native (and a window-level stylesheet broke the
# palette-based window background), so only the deliberately-flat chrome (toolbars,
# palette, side panels, scrollbars) and the canvas are themed by stylesheet/tokens.
