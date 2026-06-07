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
    """Keep the module-level overrides from leaking between tests."""
    tools.set_tool_paths({})
    yield
    tools.set_tool_paths({})


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
