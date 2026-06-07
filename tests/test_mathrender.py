"""
Tests for app/preview/mathrender — vector rendering of LaTeX label fragments.

The option-string parsing (``label_display_latex`` / ``_split_top_level``) is
pure and always runs.  The actual render path shells out to ``latex`` +
``dvisvgm`` and is skipped when either tool is missing.
"""

from __future__ import annotations

import os
import shutil

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtGui", reason="PySide6 not importable")

from PySide6.QtWidgets import QApplication  # noqa: E402

try:
    _APP = QApplication.instance() or QApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

from app.preview import mathrender as _mr  # noqa: E402
from app.preview.mathrender import (  # noqa: E402
    _active_engine,
    _split_top_level,
    editable_to_options,
    label_display_latex,
    options_to_editable,
    render_latex,
    render_path,
    set_force_ziamath,
    slot_fragments,
    slot_side,
)

_HAS_TEX = shutil.which("latex") is not None and shutil.which("dvisvgm") is not None


@pytest.fixture(autouse=True)
def _reset_engine():
    """Keep the module-level force flag from leaking between tests."""
    set_force_ziamath(False)
    yield
    set_force_ziamath(False)


# ---------------------------------------------------------------------------
# Pure option-string parsing (no LaTeX needed)
# ---------------------------------------------------------------------------

def test_split_top_level_ignores_commas_in_math_and_braces() -> None:
    """Commas inside $...$ or {...} do not split segments."""
    s = r"l=$U_\mathrm{ocp}(\theta_{s,0})$, v=$V_s$, mirror"
    assert _split_top_level(s) == [
        r"l=$U_\mathrm{ocp}(\theta_{s,0})$",
        " v=$V_s$",
        " mirror",
    ]


def test_label_display_extracts_label_values() -> None:
    """Recognised label keys contribute their value; flags are dropped."""
    assert label_display_latex(r"l=$\bar{R}_\mathrm{dl}$") == r"$\bar{R}_\mathrm{dl}$"
    assert (
        label_display_latex(r"l=$\bar{R}_\mathrm{dl}$, v=$V_s$")
        == r"$\bar{R}_\mathrm{dl}$\ \ $V_s$"
    )


def test_label_display_drops_flags_and_unknown_keys() -> None:
    """Styling flags (mirror) and non-label keys do not render."""
    assert label_display_latex("mirror") == ""
    assert label_display_latex(r"l=$R_1$, mirror, scale=2") == "$R_1$"


def test_label_display_empty_for_blank() -> None:
    assert label_display_latex("") == ""
    assert label_display_latex("l=") == ""


def test_slot_fragments_pairs_keys_and_values() -> None:
    """Side slots are returned as (key, latex); flags and t= are excluded."""
    assert slot_fragments(r"l=$R_1$, v=$V_s$") == [("l", "$R_1$"), ("v", "$V_s$")]
    # t= is the in-body bipole label, not a side slot.
    assert slot_fragments(r"t=$Z$, l=$R$") == [("l", "$R$")]
    # Empty value (thin space stays, but a bare empty value drops out).
    assert slot_fragments(r"l=$R$, v^=") == [("l", "$R$")]
    assert slot_fragments("mirror, scale=2") == []


def test_options_editable_roundtrip() -> None:
    """Options convert to one-per-line for editing and back to comma form."""
    opts = r"l=$R_1$, v=$V_s$, i=$I$"
    editable = options_to_editable(opts)
    assert editable == "l=$R_1$\nv=$V_s$\ni=$I$"
    assert editable_to_options(editable) == opts


def test_options_to_editable_keeps_math_commas() -> None:
    """Commas inside $...$/{...} are not treated as option separators."""
    opts = r"l=$U_\mathrm{ocp}(\theta_{s,0})$, v=$V$"
    assert options_to_editable(opts) == r"l=$U_\mathrm{ocp}(\theta_{s,0})$" + "\nv=$V$"


def test_editable_to_options_drops_blank_lines() -> None:
    assert editable_to_options("l=$R$\n\n  \nv=$V$") == "l=$R$, v=$V$"


def test_split_preserves_escaped_comma() -> None:
    r"""A LaTeX control sequence like ``\,`` (thin space) is not split on its
    comma. Regression: ``v^=\,`` lost its comma on the editing round-trip."""
    opts = r"l=$\bar{R}$, v^=\,"
    assert _split_top_level(opts) == [r"l=$\bar{R}$", r" v^=\,"]
    assert options_to_editable(opts) == "l=$\\bar{R}$\nv^=\\,"
    assert editable_to_options(options_to_editable(opts)) == opts
    assert slot_fragments(opts) == [("l", r"$\bar{R}$"), ("v^", r"\,")]


def test_slot_side_mapping() -> None:
    """^ forces above, _ forces below; l/i default above, v defaults below."""
    assert slot_side("l") == "above"
    assert slot_side("i") == "above"
    assert slot_side("v") == "below"
    assert slot_side("l_") == "below"
    assert slot_side("v^") == "above"
    assert slot_side("i_") == "below"


# ---------------------------------------------------------------------------
# Vector rendering (requires latex + dvisvgm)
# ---------------------------------------------------------------------------

pytestmark_tex = pytest.mark.skipif(
    not _HAS_TEX, reason="requires latex and dvisvgm"
)


@pytestmark_tex
def test_render_latex_produces_nonempty_path() -> None:
    """A math fragment renders to a non-empty, finite-sized QPainterPath."""
    path = render_latex(r"$\bar{R}_\mathrm{dl}$")
    assert path is not None
    assert path.elementCount() > 0
    br = path.boundingRect()
    assert br.width() > 1 and br.height() > 1
    # Baseline-normalised: left ink edge at x=0, baseline at y=0 (ascenders
    # above => negative top; "dl" subscript below => positive bottom).
    assert abs(br.left()) < 1e-6
    assert br.top() < 0 < br.bottom()


@pytestmark_tex
def test_render_latex_shared_baseline() -> None:
    """Different fragments share a baseline at y=0 regardless of glyph metrics."""
    for frag in (r"$x$", r"$xp$", r"$\Phi_\mathrm{s}$"):
        path = render_latex(frag)
        assert path is not None
        # x has no descender (bottom ~0); descenders push bottom positive, but
        # the baseline (y=0) is always the reference: top is at/above it.
        assert path.boundingRect().top() <= 1e-6


@pytestmark_tex
def test_render_latex_caches_on_disk() -> None:
    """A second render of the same fragment leaves a cached SVG on disk."""
    from app.preview.mathrender import _cache_dir, _cache_key

    frag = r"$\Phi_\mathrm{cache,test}$"
    render_path.cache_clear()
    render_latex(frag)
    cache_file = _cache_dir() / f"{_cache_key(frag)}.svg"
    assert cache_file.exists() and cache_file.read_text(encoding="utf-8")


@pytestmark_tex
def test_render_latex_empty_returns_none() -> None:
    assert render_latex("   ") is None


@pytestmark_tex
def test_render_async_delivers_on_event_loop() -> None:
    """render_async posts its result back through the Qt event loop."""
    from PySide6.QtCore import QEventLoop, QTimer

    from app.preview.mathrender import render_async

    box: dict = {}
    loop = QEventLoop()

    def done(path) -> None:  # noqa: ANN001
        box["path"] = path
        loop.quit()

    render_async(r"$V_s$", done)
    QTimer.singleShot(20000, loop.quit)
    loop.exec()
    assert "path" in box and box["path"] is not None
    assert box["path"].elementCount() > 0


# ---------------------------------------------------------------------------
# ziamath engine + selection (no LaTeX install required)
# ---------------------------------------------------------------------------

def test_ziamath_renders_without_latex() -> None:
    """The ziamath engine produces a non-empty, baseline-normalised path."""
    path = render_path(r"$\bar{R}_\mathrm{dl}$", "ziamath")
    assert path is not None and path.elementCount() > 0
    br = path.boundingRect()
    assert br.width() > 1 and br.height() > 1
    assert abs(br.left()) < 1e-6        # left ink edge normalised to x=0
    assert br.top() < 0 < br.bottom()   # ascender above baseline, subscript below


def test_ziamath_handles_text_fraction_and_greek() -> None:
    """Plain text, mixed text+math, fractions and Greek all render."""
    for frag in ("Processor", r"Gain $A_v$", r"$\frac{1}{sC}$", r"$\omega_0$"):
        p = render_path(frag, "ziamath")
        assert p is not None and not p.isEmpty(), frag


def test_ziamath_strips_math_delimiters() -> None:
    r"""``$…$`` delimiters typeset as math, not literal dollar glyphs (regression).

    ``ziamath.Latex`` is math-only and would draw the ``$`` as dollar signs; the
    renderer must use ``ziamath.Text`` so ``$x$`` typesets identically to the bare
    math ``x`` — i.e. its ink is no wider than a single glyph, not three.
    """
    delim = render_path(r"$x$", "ziamath")
    plain = render_path("x", "ziamath")
    assert delim is not None and plain is not None
    # With literal '$' glyphs the width would roughly triple; allow a small margin.
    assert delim.boundingRect().width() < plain.boundingRect().width() * 1.5


def test_force_ziamath_selects_engine() -> None:
    """The force flag overrides engine selection regardless of LaTeX presence."""
    set_force_ziamath(True)
    assert _active_engine() == "ziamath"
    set_force_ziamath(False)
    # auto: latex when present, else ziamath
    assert _active_engine() == ("latex" if _HAS_TEX else "ziamath")


def test_fallback_to_ziamath_when_latex_absent(monkeypatch) -> None:
    """With no latex/dvisvgm on PATH, auto-selection falls back to ziamath."""
    from app.preview import tools

    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    tools.set_tool_paths({})  # no explicit override
    set_force_ziamath(False)
    assert _active_engine() == "ziamath"


def test_render_latex_uses_ziamath_when_forced() -> None:
    """render_latex routes through the forced engine and returns a path."""
    set_force_ziamath(True)
    assert render_latex(r"$R_1$") is not None
