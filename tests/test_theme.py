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
