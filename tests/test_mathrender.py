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
def test_empty_cache_sentinel_self_heals() -> None:
    """A stale **empty** cache file (a failure marker from an older build, or a
    partial write) is treated as a miss and re-rendered — never returned as a
    permanent failure. Regression: a resistor's ``l=$R$`` label vanished because
    an empty sentinel had been cached for ``$R$``."""
    from app.preview.mathrender import _cache_dir, _cache_key, _compile_svg

    frag = r"$\Phi_\mathrm{heal,test}$"
    cache_file = _cache_dir() / f"{_cache_key(frag)}.svg"
    cache_file.write_text("", encoding="utf-8")  # poison with an empty sentinel
    render_path.cache_clear()
    try:
        svg = _compile_svg(frag)
        assert svg, "empty sentinel must be ignored and the fragment recompiled"
        assert cache_file.read_text(encoding="utf-8")  # healed: real content now
    finally:
        cache_file.unlink(missing_ok=True)


@pytestmark_tex
def test_failed_compile_writes_no_sentinel() -> None:
    """A genuine compile failure does not persist an empty sentinel, so it never
    blocks a later retry (the poisoning mechanism is gone for good)."""
    from app.preview.mathrender import _cache_dir, _cache_key, _compile_svg

    frag = r"$\notARealLatexCommand_{zz}$"
    cache_file = _cache_dir() / f"{_cache_key(frag)}.svg"
    cache_file.unlink(missing_ok=True)
    try:
        assert _compile_svg(frag) is None
        assert not cache_file.exists(), "a failure must not write a cache sentinel"
    finally:
        cache_file.unlink(missing_ok=True)


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


def test_render_async_single_dispatcher_no_per_request_qobject() -> None:
    """One process-lifetime dispatcher delivers every async result.

    Regression for a CI segfault: the old design created a signals QObject per
    request whose last Python reference was often dropped by the QRunnable's
    auto-delete on the *worker* thread — destroying a UI-thread-affine QObject
    from a worker thread (Qt UB, nondeterministic crash in the main thread's
    event dispatch). The dispatcher must be a singleton and its callback map
    must drain after delivery.
    """
    from PySide6.QtCore import QEventLoop, QTimer

    from app.preview.mathrender import _dispatcher, render_async

    set_force_ziamath(True)
    assert _dispatcher() is _dispatcher()
    assert not hasattr(_mr, "_live_signals"), "per-request signal set is gone"

    # Earlier test modules share the process-lifetime dispatcher and may have
    # tokens still in flight; only the tokens registered *here* must drain.
    tokens_before = set(_dispatcher()._callbacks)

    n = 12
    results: list[object] = []
    loop = QEventLoop()

    def make_done():  # noqa: ANN202
        def done(path) -> None:  # noqa: ANN001
            results.append(path)
            if len(results) == n:
                loop.quit()
        return done

    for i in range(n):
        render_async(rf"$x_{{{i % 3}}}$", make_done())
    QTimer.singleShot(20000, loop.quit)
    loop.exec()

    assert len(results) == n, "every async render must deliver exactly once"
    assert all(p is not None for p in results)
    leftover = set(_dispatcher()._callbacks) - tokens_before
    assert leftover == set(), "this test's callback tokens must drain"


def test_render_async_callbacks_run_on_ui_thread() -> None:
    """Results are delivered on the UI thread even under concurrent churn."""
    import threading

    from PySide6.QtCore import QEventLoop, QTimer

    from app.preview.mathrender import render_async

    set_force_ziamath(True)
    main_ident = threading.get_ident()
    idents: list[int] = []
    n = 8
    loop = QEventLoop()

    def done(path) -> None:  # noqa: ANN001
        idents.append(threading.get_ident())
        if len(idents) == n:
            loop.quit()

    render_path.cache_clear()  # force real worker-side renders, not memo hits
    for i in range(n):
        render_async(rf"$y_{{{i}}}$", done)
    QTimer.singleShot(20000, loop.quit)
    loop.exec()

    assert len(idents) == n
    assert set(idents) == {main_ident}


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


def test_pyinstaller_spec_bundles_ziamath_fonts() -> None:
    """The frozen app must bundle ziamath/ziafont/latex2mathml package data.

    ziamath/ziafont load STIX Two Math / DejaVu Sans, and the latex2mathml they
    pull in reads ``unimathsymbols.txt``, all at import time — so without these
    collect_data_files() lines `import ziamath` raises in the PyInstaller bundle
    (FileNotFoundError), the no-LaTeX math fallback is dead, and every canvas
    label renders blank even though it all works in the dev venv. This guards the
    heaviside.spec lines from silently regressing.
    """
    from pathlib import Path

    spec = (Path(__file__).resolve().parent.parent / "heaviside.spec").read_text(
        encoding="utf-8"
    )
    assert 'collect_data_files("ziamath")' in spec
    assert 'collect_data_files("ziafont")' in spec
    assert 'collect_data_files("latex2mathml")' in spec


def test_latex2mathml_data_file_is_collectable() -> None:
    """latex2mathml's ``unimathsymbols.txt`` (loaded at import) must be something
    PyInstaller's collect_data_files() actually finds — guards against the table
    moving or the dependency changing shape under us."""
    from PyInstaller.utils.hooks import collect_data_files

    files = collect_data_files("latex2mathml")
    assert any(src.endswith("unimathsymbols.txt") for src, _dest in files), files


def test_slot_reversed_detects_direction_modifier() -> None:
    """`<` means reversed; `>` and no modifier are forward."""
    from app.preview.mathrender import slot_reversed
    assert slot_reversed("i<") is True
    assert slot_reversed("i>") is False
    assert slot_reversed("i") is False
    assert slot_reversed("v<") is True
    assert slot_reversed("i^<") is True


def test_slot_side_ignores_direction_modifier() -> None:
    """`^`/`_` set the side; `<`/`>` set direction only and must not affect side."""
    assert slot_side("i<") == "above"     # i defaults above
    assert slot_side("i_>") == "below"    # _ forces below
    assert slot_side("v<") == "below"     # v defaults below
    assert slot_side("v^<") == "above"    # ^ forces above


def test_slot_fragments_and_display_accept_direction_modifiers() -> None:
    """`i<=`/`v>=` keys still contribute their value (with the modifier kept)."""
    assert slot_fragments(r"i<=$i$, v>=$V$") == [("i<", "$i$"), ("v>", "$V$")]
    assert label_display_latex(r"i<=$i$, v>=$V$") == r"$i$\ \ $V$"


# ---------------------------------------------------------------------------
# Caching robustness (all mocked — no latex/dvisvgm needed)
# ---------------------------------------------------------------------------

# A minimal dvisvgm-style SVG: one direct rule path, bottom ink edge at y=12.
_FAKE_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20">'
    '<path d="M0 2L10 12"/></svg>'
)


@pytest.fixture()
def _clean_memos():
    """Isolate the in-memory render/baseline memos around a test."""
    render_path.cache_clear()   # clears the render memo AND the baseline memo
    yield
    render_path.cache_clear()


def test_baseline_failure_not_memoised(monkeypatch, _clean_memos) -> None:
    """A failed baseline calibration falls back to 0.0 for that call only.

    Regression: ``_baseline_y`` was ``lru_cache(1)``, so one transient failure
    at startup pinned 0.0 for the session and vertically shifted every label.
    """
    calls = {"n": 0}

    def _fail_then_succeed(fragment, **kw):  # noqa: ANN001
        calls["n"] += 1
        return None if calls["n"] == 1 else _FAKE_SVG

    monkeypatch.setattr(_mr, "_compile_svg", _fail_then_succeed)
    assert _mr._baseline_y() == 0.0          # transient failure -> fallback
    assert _mr._baseline_y() == 12.0         # retried and recovered
    # The success IS memoised: a later failure does not regress the value.
    monkeypatch.setattr(_mr, "_compile_svg", lambda *a, **k: None)
    assert _mr._baseline_y() == 12.0


def test_render_path_does_not_cache_transient_failure(monkeypatch, _clean_memos) -> None:
    """A ``None`` render is never memoised, so the next request retries.

    Regression: ``lru_cache`` stored the ``None`` of a transient tool failure,
    blanking that fragment for the whole session.
    """
    calls = {"n": 0}

    def _fail_then_succeed(fragment, **kw):  # noqa: ANN001
        calls["n"] += 1
        return None if calls["n"] <= 1 else _FAKE_SVG  # the fragment SVG fails first

    monkeypatch.setattr(_mr, "_compile_svg", _fail_then_succeed)
    assert render_path("$R_1$", "latex") is None          # transient failure
    path = render_path("$R_1$", "latex")                  # retried -> succeeds
    assert path is not None and path.elementCount() > 0


def test_render_path_memoises_success(monkeypatch, _clean_memos) -> None:
    """A successful render is served from the memo (no recompile)."""
    calls = {"n": 0}

    def _count(fragment, **kw):  # noqa: ANN001
        calls["n"] += 1
        return _FAKE_SVG

    monkeypatch.setattr(_mr, "_compile_svg", _count)
    first = render_path("$R_1$", "latex")
    n_after_first = calls["n"]
    second = render_path("$R_1$", "latex")
    assert first is not None and second is not None
    assert calls["n"] == n_after_first       # no further compiles
    assert second is first                   # served from the memo


def test_render_memo_is_bounded(monkeypatch, _clean_memos) -> None:
    """The manual memo evicts oldest entries past its cap (no unbounded growth)."""
    monkeypatch.setattr(_mr, "_RENDER_MEMO_MAX", 4)
    monkeypatch.setattr(_mr, "_compile_svg", lambda fragment, **kw: _FAKE_SVG)
    for i in range(10):
        assert render_path(f"$x_{i}$", "latex") is not None
    assert len(_mr._render_memo) <= 4


def test_corrupt_disk_cache_entry_recompiles(monkeypatch, tmp_path, _clean_memos) -> None:
    """A corrupted (non-empty, unparseable) cached SVG is discarded and the
    fragment recompiled — instead of being trusted forever and raising
    ``ET.ParseError`` out of every render of that fragment (regression)."""
    import subprocess as _sp
    from pathlib import Path as _P

    frag = r"$R_\mathrm{corrupt}$"
    monkeypatch.setattr(_mr, "_cache_dir", lambda: tmp_path)
    cache_file = tmp_path / f"{_mr._cache_key(frag)}.svg"
    cache_file.write_text("<svg><unclosed", encoding="utf-8")   # corrupt, non-empty

    def _fake_run(cmd, *args, **kwargs):  # noqa: ANN001
        cwd = _P(kwargs["cwd"])
        if any("dvisvgm" in str(part) for part in cmd):
            (cwd / "m.svg").write_text(_FAKE_SVG, encoding="utf-8")
        else:
            (cwd / "m.dvi").write_bytes(b"\x00")

        class _R:
            returncode = 0
            stdout = b""
            stderr = b""
        return _R()

    monkeypatch.setattr(_mr._tools, "resolve", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(_mr.subprocess, "run", _fake_run)

    svg = _mr._compile_svg(frag)
    assert svg == _FAKE_SVG                                   # recompiled, not the junk
    assert cache_file.read_text(encoding="utf-8") == _FAKE_SVG  # cache healed


# ---------------------------------------------------------------------------
# Per-user disk cache directory (security)
# ---------------------------------------------------------------------------

@pytest.fixture()
def _fresh_cache_dir():
    """Clear the lru-cached directory around a test so patches take effect."""
    _mr._cache_dir.cache_clear()
    yield
    _mr._cache_dir.cache_clear()


def test_cache_dir_is_per_user_and_private(monkeypatch, tmp_path, _fresh_cache_dir) -> None:
    """The disk cache lives in a per-user, 0700 directory — not the old shared
    world-writable ``heaviside-mathcache`` path another local user could seed."""
    monkeypatch.setattr(_mr.tempfile, "gettempdir", lambda: str(tmp_path))
    d = _mr._cache_dir()
    assert d.parent == tmp_path
    assert d.name == f"heaviside-mathcache-{_mr._cache_user()}"
    assert d.name != "heaviside-mathcache"
    if hasattr(os, "getuid"):                       # POSIX-only assertions
        assert d.stat().st_uid == os.getuid()
        assert (d.stat().st_mode & 0o777) == 0o700


def test_cache_dir_falls_back_when_squatted(monkeypatch, tmp_path, _fresh_cache_dir) -> None:
    """On POSIX, a pre-existing directory owned by another uid is never trusted:
    the cache falls back to a fresh private mkdtemp."""
    if not hasattr(os, "getuid"):
        pytest.skip("POSIX-only ownership check")
    monkeypatch.setattr(_mr.tempfile, "gettempdir", lambda: str(tmp_path))
    squatted = tmp_path / f"heaviside-mathcache-{_mr._cache_user()}"
    squatted.mkdir()
    # Simulate foreign ownership: pretend our uid differs from the dir's owner.
    real_uid = os.getuid()
    monkeypatch.setattr(_mr.os, "getuid", lambda: real_uid + 1)
    d = _mr._cache_dir()
    assert d != squatted
    assert d.name.startswith("heaviside-mathcache-")
