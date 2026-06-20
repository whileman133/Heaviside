r"""
Security regression tests for the LaTeX compile pipeline (app/preview/latex).

These stub out ``subprocess.run`` and ``shutil.which`` so they run anywhere,
with no real pdflatex installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.components import render as comp_render
from app.components.style import balance_braces, contains_dangerous_latex
from app.preview import latex, mathrender, tools


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


def test_mathrender_disables_shell_escape(monkeypatch, tmp_path) -> None:
    r"""The on-canvas math renderer must invoke ``latex`` with ``-no-shell-escape``.

    This is the path that fires the instant a ``.hv`` file is opened — every label
    is typeset with no user gesture, and the label text flows verbatim into the
    document body. It is therefore *more* exposed than the preview/export compile,
    so it must carry the same ``-no-shell-escape`` guard.
    """
    captured: dict = {}

    def _fake_run(cmd, *args, **kwargs):
        captured.setdefault("cmds", []).append(cmd)
        cwd = Path(kwargs["cwd"])
        if any("dvisvgm" in str(part) for part in cmd):
            (cwd / "m.svg").write_text("<svg></svg>", encoding="utf-8")
        else:
            captured["latex_cmd"] = cmd
            (cwd / "m.dvi").write_bytes(b"\x00")
        return _FakeCompleted()

    # Force a cache miss and a resolvable toolchain, with no real binaries run.
    monkeypatch.setattr(mathrender, "_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        mathrender._tools, "resolve",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(mathrender.subprocess, "run", _fake_run)

    mathrender._compile_svg(r"\strut $R_1$")

    assert "-no-shell-escape" in captured["latex_cmd"]
    assert isinstance(captured["latex_cmd"], list)


def test_component_render_disables_shell_escape(monkeypatch, tmp_path) -> None:
    """The offline component renderer must also pass ``-no-shell-escape``."""
    captured: dict = {}

    def _fake_run(cmd, *args, **kwargs):
        cwd = Path(kwargs["cwd"])
        if any("dvisvgm" in str(part) for part in cmd):
            (cwd / "sym.svg").write_text("<svg></svg>", encoding="utf-8")
        else:
            captured["latex_cmd"] = cmd
            (cwd / "sym.dvi").write_bytes(b"\x00")

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(comp_render.subprocess, "run", _fake_run)

    comp_render.render_svg(r"\draw (0,0) -- (1,0);")

    assert "-no-shell-escape" in captured["latex_cmd"]


# ---------------------------------------------------------------------------
# Dangerous-token detector (pure helper shared with the UI load-time warning)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    r"\write18{rm -rf ~}",
    r"$R_1$ \write18{evil}",          # after other text
    r"\write0{x}",                     # bare \write is dangerous too
    r"\immediate\write18{evil}",
    r"\input{/etc/passwd}",
    r"\include{secrets}",
    r"\openin1=secrets.txt",
    r"\openout5=out.txt",
    r"\read1 to \x",
    r"\csname write18\endcsname",
    r"\catcode`\$=0",
    r"\directlua{os.execute('id')}",
    r"\ShellEscape{id}",
])
def test_contains_dangerous_latex_true(text) -> None:
    assert contains_dangerous_latex(text) is True


@pytest.mark.parametrize("text", [
    "",
    "$R_1$",
    r"$\frac{V_i}{R}$",
    r"$\bar{R}_\mathrm{dl}$",
    "plain label, no commands",
    r"\includegraphics{fig.pdf}",      # \include must not match a longer command
    r"\inputencoding{utf8}",           # \input must not match a longer command
    r"\readline",                      # \read must not match a longer command
    r"writes a value of 18",           # no backslash, no command
    r"$\omega_0 = \frac{1}{\sqrt{LC}}$",
])
def test_contains_dangerous_latex_false(text) -> None:
    assert contains_dangerous_latex(text) is False


def test_contains_dangerous_latex_none_safe() -> None:
    assert contains_dangerous_latex(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Brace-balancing (structural containment for {…}-wrapped user text)
# ---------------------------------------------------------------------------

def test_balance_braces_neutralises_stray_close() -> None:
    # The unmatched } is escaped, so it cannot close an enclosing brace group.
    assert balance_braces(r"}\write18{evil}") == r"\}\write18{evil}"


def test_balance_braces_neutralises_stray_open() -> None:
    assert balance_braces("{unclosed") == r"\{unclosed"


def test_balance_braces_keeps_balanced_latex() -> None:
    for text in (r"\frac{a}{b}", r"$\bar{R}_\mathrm{dl}$", "plain",
                 r"\theta_{s,0}", "{a{b}c}", ""):
        assert balance_braces(text) == text


def test_balance_braces_keeps_escaped_braces() -> None:
    assert balance_braces(r"\{ \}") == r"\{ \}"
    # An escaped close brace does not pair with a real open brace.
    assert balance_braces(r"{a\}") == r"\{a\}"


def test_balance_braces_mixed() -> None:
    # One balanced pair survives; the extra close is escaped.
    assert balance_braces(r"{ok}}tail") == r"{ok}\}tail"


# ---------------------------------------------------------------------------
# Exported full documents carry the raw-LaTeX warning header
# ---------------------------------------------------------------------------

def test_build_tex_carries_security_comment() -> None:
    tex = latex.build_tex(r"\begin{circuitikz}\end{circuitikz}")
    assert tex.lstrip().startswith("%")            # leading comment block
    assert "shell-escape" in tex
    dark = latex.build_tex(r"\begin{circuitikz}\end{circuitikz}", dark=True)
    assert "shell-escape" in dark


def test_build_snippet_carries_security_comment() -> None:
    snippet = latex.build_snippet(r"\begin{circuitikz}\end{circuitikz}")
    assert "shell-escape" in snippet


# ---------------------------------------------------------------------------
# Document preamble settings (siunitx / custom preamble) — issue #29
# ---------------------------------------------------------------------------

_SRC = r"\begin{circuitikz}\end{circuitikz}"


def test_default_preamble_is_unchanged() -> None:
    """Defaults (siunitx off, no preamble) leave the output byte-for-byte."""
    assert latex.build_tex(_SRC) == latex.build_tex(_SRC, siunitx=False, extra_preamble="")
    assert r"\usepackage[american]{circuitikz}" in latex.build_tex(_SRC)
    assert "siunitx" not in latex.build_tex(_SRC)


def test_siunitx_adds_circuitikz_option() -> None:
    """The siunitx flag extends CircuiTikZ's option list, not a separate load."""
    tex = latex.build_tex(_SRC, siunitx=True)
    assert r"\usepackage[american,siunitx]{circuitikz}" in tex
    assert r"\usepackage[american]{circuitikz}" not in tex
    # Dark preview path too.
    assert "siunitx" in latex.build_tex(_SRC, siunitx=True, dark=True)
    # And the includable snippet documents the required option.
    assert r"\usepackage[american,siunitx]{circuitikz}" in latex.build_snippet(_SRC, siunitx=True)


def test_custom_preamble_spliced_before_begin_document() -> None:
    tex = latex.build_tex(_SRC, extra_preamble=r"\usepackage{mathtools}")
    assert r"\usepackage{mathtools}" in tex
    assert tex.index(r"\usepackage{mathtools}") < tex.index(r"\begin{document}")


def test_blank_custom_preamble_is_noop() -> None:
    assert latex.build_tex(_SRC, extra_preamble="   \n  ") == latex.build_tex(_SRC)


def test_snippet_documents_custom_preamble_as_comments() -> None:
    snippet = latex.build_snippet(_SRC, extra_preamble=r"\usepackage{mathtools}")
    # A snippet is \input into a body, so the preamble can only be documented.
    assert r"%   \usepackage{mathtools}" in snippet
