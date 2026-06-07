r"""
Security regression tests for the LaTeX compile pipeline (app/preview/latex).

These stub out ``subprocess.run`` and ``shutil.which`` so they run anywhere,
with no real pdflatex installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.preview import latex, tools


class _FakeCompleted:
    returncode = 0
    stdout = b""
    stderr = b""


def test_compile_tex_disables_shell_escape(monkeypatch) -> None:
    r"""compile_tex must invoke pdflatex with ``-no-shell-escape``.

    A ``.hv`` file may come from an untrusted source, and label/text fields flow
    verbatim into the generated LaTeX. Passing ``-no-shell-escape`` explicitly
    guarantees a crafted label can never invoke ``\write18`` / external commands,
    regardless of the local TeX installation's default.
    """
    captured: dict = {}

    def _fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        cwd = kwargs.get("cwd")
        if cwd is not None:
            (Path(cwd) / "schematic.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        return _FakeCompleted()

    monkeypatch.setattr(tools.shutil, "which", lambda _name: "/usr/bin/pdflatex")
    tools.set_tool_paths({})  # resolve via (patched) PATH, no override
    monkeypatch.setattr(subprocess, "run", _fake_run)

    latex.compile_tex(
        r"\documentclass{standalone}\begin{document}x\end{document}"
    )

    assert "-no-shell-escape" in captured["cmd"]


def test_compile_tex_never_uses_shell(monkeypatch) -> None:
    """compile_tex must pass argv as a list and never use shell=True."""
    captured: dict = {}

    def _fake_run(cmd, *args, **kwargs):
        captured["cmd"] = cmd
        captured["shell"] = kwargs.get("shell", False)
        cwd = kwargs.get("cwd")
        if cwd is not None:
            (Path(cwd) / "schematic.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        return _FakeCompleted()

    monkeypatch.setattr(tools.shutil, "which", lambda _name: "/usr/bin/pdflatex")
    tools.set_tool_paths({})  # resolve via (patched) PATH, no override
    monkeypatch.setattr(subprocess, "run", _fake_run)

    latex.compile_tex(
        r"\documentclass{standalone}\begin{document}x\end{document}"
    )

    assert isinstance(captured["cmd"], list)
    assert captured["shell"] is False
