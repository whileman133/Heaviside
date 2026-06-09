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


# NOTE: there is intentionally no global form-control stylesheet. Dialogs, message
# boxes, spin boxes, and combo boxes keep their **native** look; only the toolbars
# (top_toolbar_qss/ribbon_qss) and the Copy buttons (flat_button_qss) are themed.
# A global QSS cascaded into child dialogs/message boxes and made them non-native,
# and styling combo/spin sub-controls broke their arrows / crashed offscreen.
