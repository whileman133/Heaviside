"""
Shared UI design tokens and stylesheet fragments (spec §10).

One flat, light visual language — white surfaces, hairline borders, muted icons,
and a single soft-blue accent — so the toolbars, buttons, and dialog form
controls match the component palette. Import these tokens rather than
hard-coding colours, to keep the look from drifting apart again.
"""

from __future__ import annotations

# -- Colour tokens ----------------------------------------------------------
SURFACE = "#ffffff"        # primary surface (panels, chrome)
SURFACE_ALT = "#fafafa"    # subtle raised surface (cards)
DIVIDER = "#ececec"        # hairline chrome divider
BORDER = "#cfcfcf"         # input borders
BORDER_SOFT = "#dadada"    # card / soft borders
ACCENT = "#5b87f0"         # the one accent (focus, active, default)
HOVER = "#e8f0fe"          # soft-blue hover/active fill
HOVER_BORDER = "#c5d9fb"   # hover/active border
PRESSED = "#dce7fc"        # pressed fill
BUTTON_BG = "#f4f6fb"      # resting flat-button fill
BUTTON_BORDER = "#d7def0"  # resting flat-button border
TEXT = "#333333"           # body text
ICON = "#555555"           # icon tint (toolbars, buttons)
ICON_MUTED = "#888888"     # secondary icons


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
