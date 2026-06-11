"""
Light/dark theme palette switching (spec §10 Theme).

The canvas palette (``app.canvas.style``) and the chrome palette
(``app.ui.theme``) each expose ``set_dark()`` / ``is_dark()`` and swap a set of
module-level colour tokens that consumers read live. These tests pin the swap
and — importantly — always restore the light defaults so global state does not
leak into other tests.
"""

from __future__ import annotations

import pytest

from app.canvas import style
from app.ui import theme


@pytest.fixture(autouse=True)
def _restore_light():
    """Guarantee both palettes are back to light after each test."""
    yield
    style.set_dark(False)
    theme.set_dark(False)


def test_canvas_palette_defaults_to_light() -> None:
    assert not style.is_dark()
    assert style.COLOR_BACKGROUND == style._LIGHT["COLOR_BACKGROUND"]
    assert style.COLOR_NORMAL == style._LIGHT["COLOR_NORMAL"]


def test_canvas_palette_set_dark_swaps_and_restores() -> None:
    style.set_dark(True)
    assert style.is_dark()
    assert style.COLOR_BACKGROUND == style._DARK["COLOR_BACKGROUND"]
    assert style.COLOR_NORMAL == style._DARK["COLOR_NORMAL"]
    # Background and ink invert: a dark canvas needs light ink.
    assert style.COLOR_BACKGROUND != style.COLOR_NORMAL
    style.set_dark(False)
    assert not style.is_dark()
    assert style.COLOR_BACKGROUND == style._LIGHT["COLOR_BACKGROUND"]


def test_chrome_palette_set_dark_swaps_and_restores() -> None:
    assert not theme.is_dark()
    light_surface = theme.SURFACE
    theme.set_dark(True)
    assert theme.is_dark()
    assert theme.SURFACE == theme._DARK["SURFACE"]
    assert theme.SURFACE != light_surface
    theme.set_dark(False)
    assert theme.SURFACE == light_surface


def test_chrome_qss_reflects_active_palette() -> None:
    """The QSS builders read the active tokens at call time, so re-applying them
    after a swap yields dark chrome (the mechanism MainWindow._apply_theme uses)."""
    theme.set_dark(True)
    qss_dark = theme.top_toolbar_qss()
    assert theme._DARK["SURFACE"] in qss_dark
    theme.set_dark(False)
    qss_light = theme.top_toolbar_qss()
    assert theme._LIGHT["SURFACE"] in qss_light
    assert qss_dark != qss_light


def test_line_width_matches_circuitikz_thin_stroke() -> None:
    """The canvas stroke width is the CircuiTikZ thin stroke (~0.3985 pt) mapped to
    the grid scale, so on-canvas line weight matches the compiled figure rather
    than the earlier (~2.4x) bolder strokes. Guards against reverting to 2.0 px."""
    expected = style.GRID_PX * (0.3985 / style.SVG_PT_PER_GU)
    assert style.LINE_W == pytest.approx(expected, abs=1e-6)
    assert style.LINE_W == pytest.approx(0.84, abs=0.02)
    assert style.LINE_W_THICK == pytest.approx(2.0 * style.LINE_W, abs=1e-6)


def test_palettes_define_matching_token_sets() -> None:
    """Every token exists in both palettes (set_dark swaps them wholesale), and
    the dialog/welcome tokens added for dark-mode readability are present."""
    assert set(theme._LIGHT) == set(theme._DARK)
    for token in ("TEXT_MUTED", "HEADING", "TABLE_KEY", "TABLE_HEADER_BG",
                  "WELCOME_STEP", "WELCOME_AXIS", "WELCOME_LABEL", "WELCOME_HINT"):
        assert token in theme._LIGHT
        assert getattr(theme, token) == theme._LIGHT[token]


def test_welcome_tokens_carry_alpha() -> None:
    """The welcome diagram tokens are #AARRGGBB strings (QColor's alpha form),
    and they differ between light and dark so the diagram stays readable."""
    for token in ("WELCOME_STEP", "WELCOME_AXIS", "WELCOME_LABEL", "WELCOME_HINT"):
        for palette in (theme._LIGHT, theme._DARK):
            value = palette[token]
            assert value.startswith("#") and len(value) == 9
        assert theme._LIGHT[token] != theme._DARK[token]
