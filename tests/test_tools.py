"""
Tests for app/preview/tools — external tool path resolution.

Pure (no Qt): an explicit configured path wins when it points at a runnable
file; otherwise resolution falls back to PATH. Validity is checked lazily, so a
non-runnable override is ignored rather than erroring.
"""

from __future__ import annotations

import os
import stat

import pytest

from app.preview import tools


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Keep the module-level overrides / forced-missing from leaking between tests."""
    tools.set_tool_paths({})
    tools.set_forced_missing(set())
    yield
    tools.set_tool_paths({})
    tools.set_forced_missing(set())


def _make_exe(path) -> str:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def test_override_wins_when_runnable(tmp_path, monkeypatch):
    exe = _make_exe(tmp_path / "pdflatex")
    # PATH lookup would return something else; the override must take precedence.
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/elsewhere")
    tools.set_tool_path("pdflatex", exe)
    assert tools.resolve("pdflatex") == exe
    assert tools.available("pdflatex") is True


def test_nonrunnable_override_falls_back_to_path(tmp_path, monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda name: "/usr/bin/frompath")
    tools.set_tool_path("dvisvgm", str(tmp_path / "does-not-exist"))
    # The bogus override is ignored; resolution falls through to PATH.
    assert tools.resolve("dvisvgm") == "/usr/bin/frompath"


def test_resolve_none_when_absent(monkeypatch):
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    assert tools.resolve("pdftocairo") is None
    assert tools.available("pdftocairo") is False


def test_forced_missing_overrides_path_and_override(tmp_path, monkeypatch):
    """``--no-latex`` (set_forced_missing) makes a tool resolve as absent ahead of
    every other source — even a runnable override or a PATH hit — and clears back."""
    exe = _make_exe(tmp_path / "pdflatex")
    monkeypatch.setattr(tools.shutil, "which", lambda name: exe)
    tools.set_tool_path("pdflatex", exe)            # would normally win
    assert tools.available("pdflatex") is True

    tools.set_forced_missing({"pdflatex"})
    assert tools.resolve("pdflatex") is None
    assert tools.available("pdflatex") is False
    # Other tools are unaffected.
    assert tools.resolve("dvisvgm") == exe

    tools.set_forced_missing(set())
    assert tools.available("pdflatex") is True


def test_forced_missing_ignores_unknown_names():
    """Only real TOOLS names are honoured (a typo can't silently disable nothing
    or everything)."""
    tools.set_forced_missing({"bogus"})
    assert tools._forced_missing == set()


def test_blank_clears_override(tmp_path, monkeypatch):
    exe = _make_exe(tmp_path / "latex")
    tools.set_tool_path("latex", exe)
    assert tools.resolve("latex") == exe
    tools.set_tool_path("latex", "   ")  # blank clears it
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    assert tools.resolve("latex") is None


def test_set_tool_paths_ignores_unknown_keys(tmp_path):
    exe = _make_exe(tmp_path / "pdflatex")
    tools.set_tool_paths({"pdflatex": exe, "bogus": "/x"})
    assert tools.resolve("pdflatex") == exe
    assert "bogus" not in tools._overrides


def test_is_runnable(tmp_path):
    exe = _make_exe(tmp_path / "tool")
    assert tools.is_runnable(exe) is True
    assert tools.is_runnable(str(tmp_path / "missing")) is False
    assert tools.is_runnable(str(tmp_path)) is False  # a directory is not runnable
    assert tools.is_runnable("") is False


def test_inkscape_is_a_known_tool():
    """"inkscape" is a first-class tool: overridable in Preferences → Tools and
    persisted like the others (the dialog builds its rows from TOOLS)."""
    assert "inkscape" in tools.TOOLS


def test_extra_candidates_resolve_when_path_misses(tmp_path, monkeypatch):
    """A tool absent from PATH resolves through its well-known install
    locations (Inkscape's installers don't put the CLI binary on PATH)."""
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    exe = _make_exe(tmp_path / "inkscape")
    monkeypatch.setattr(tools, "_EXTRA_TOOL_CANDIDATES", {"inkscape": (exe,)})
    assert tools.resolve("inkscape") == exe


def test_extra_candidates_skip_nonrunnable(tmp_path, monkeypatch):
    """A candidate that doesn't exist (or isn't executable) is ignored."""
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    missing = str(tmp_path / "nope" / "inkscape")
    monkeypatch.setattr(tools, "_EXTRA_TOOL_CANDIDATES", {"inkscape": (missing,)})
    assert tools.resolve("inkscape") is None


def test_override_beats_extra_candidates(tmp_path, monkeypatch):
    """An explicit configured path wins over the well-known locations."""
    monkeypatch.setattr(tools.shutil, "which", lambda name: None)
    candidate = _make_exe(tmp_path / "candidate-inkscape")
    override = _make_exe(tmp_path / "my-inkscape")
    monkeypatch.setattr(tools, "_EXTRA_TOOL_CANDIDATES", {"inkscape": (candidate,)})
    tools.set_tool_paths({"inkscape": override})
    assert tools.resolve("inkscape") == override


def test_run_kwargs_hides_console_window_on_windows(monkeypatch):
    """On Windows the tool subprocesses must be launched with CREATE_NO_WINDOW —
    without it, every pdflatex/dvisvgm run flashes a console window over the
    (windowed, no-console) app."""
    monkeypatch.setattr(tools.platform, "system", lambda: "Windows")
    kwargs = tools.run_kwargs()
    assert kwargs.get("creationflags") == getattr(
        tools.subprocess, "CREATE_NO_WINDOW", 0x08000000
    )


def test_run_kwargs_empty_off_windows(monkeypatch):
    """creationflags is Windows-only; other platforms must add nothing."""
    monkeypatch.setattr(tools.platform, "system", lambda: "Linux")
    assert tools.run_kwargs() == {}


def test_compile_tex_forwards_run_kwargs(monkeypatch, tmp_path):
    """The pdflatex invocation actually passes the platform kwargs through
    (regression: the console-window flash on Windows)."""
    from pathlib import Path

    from app.preview import latex

    captured: dict = {}

    class _Done:
        returncode = 0
        stdout = b""
        stderr = b""

    def _fake_run(cmd, *args, **kwargs):
        captured.update(kwargs)
        (Path(kwargs["cwd"]) / "schematic.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        return _Done()

    monkeypatch.setattr(tools.platform, "system", lambda: "Windows")
    monkeypatch.setattr(tools.shutil, "which", lambda _name: "/usr/bin/pdflatex")
    monkeypatch.setattr(latex.subprocess, "run", _fake_run)

    latex.compile_tex(r"\documentclass{standalone}\begin{document}x\end{document}")

    assert captured["creationflags"] == getattr(
        tools.subprocess, "CREATE_NO_WINDOW", 0x08000000
    )
