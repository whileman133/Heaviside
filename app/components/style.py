"""
Fill/border style helpers shared by StyledComponent consumers.

A StyledComponent carries its appearance as ``fill_color`` and ``line_style``
(raw TikZ draw tokens such as ``"dashed"``), and its outline width as the shared
``Component.line_width`` (pt).  The canvas and the code generator turn those into
a TikZ draw-options string; these pure functions are the single source of truth
for that mapping.

No Qt dependency.
"""

from __future__ import annotations

import re

_DEFAULT_LINE_WIDTH = 0.4  # pt — matches the TikZ default and Component.line_width


def split_top_level(options: str) -> list[str]:
    r"""Split an option string on commas not inside ``$...$`` / ``{...}`` or escaped.

    A backslash escapes the next character, so a LaTeX control sequence such as
    ``\,`` (thin space) is kept intact and its comma is not treated as an option
    separator.  The escaped character also does not toggle math/brace state
    (``\$``, ``\{``, ``\}``).  This is the shared, TikZ-style option splitter used
    by both the canvas label parser and the code generator.
    """
    segs: list[str] = []
    depth = 0
    in_math = False
    escaped = False
    buf: list[str] = []
    for ch in options:
        if escaped:
            buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            buf.append(ch)
            escaped = True
            continue
        if ch == "$":
            in_math = not in_math
        elif ch == "{" and not in_math:
            depth += 1
        elif ch == "}" and not in_math:
            depth = max(0, depth - 1)
        if ch == "," and depth == 0 and not in_math:
            segs.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    segs.append("".join(buf))
    return segs


def balance_braces(text: str) -> str:
    r"""Neutralise unmatched braces in user text destined for a ``{…}`` argument.

    Generated CircuiTikZ wraps user-authored label/text values in a brace group
    (``\node … {text};``, ``label=above:{text}``).  A stray unmatched ``}`` would
    close that group early and inject the remainder of the text as raw TeX into
    the document — a LaTeX-injection vector when the ``.hv`` file came from an
    untrusted source.  This is **structural containment, not escaping**: math
    labels are raw LaTeX by design, so balanced groups (``\frac{a}{b}``,
    ``\theta_{s,0}``) and backslash-escaped braces (``\{``/``\}``) pass through
    untouched; only the *unmatched* ``{``/``}`` are rewritten to their escaped
    literal forms, which keeps the brace group intact and renders a visible
    brace character instead of executing the tail.
    """
    # First pass: find the indices of unmatched braces (escape-aware).
    unmatched: set[int] = set()
    open_stack: list[int] = []
    escaped = False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
        elif ch == "{":
            open_stack.append(i)
        elif ch == "}":
            if open_stack:
                open_stack.pop()
            else:
                unmatched.add(i)        # closes a group it never opened
    unmatched.update(open_stack)        # opens that never close
    if not unmatched:
        return text
    # Second pass: rewrite just those characters.
    return "".join(
        ("\\{" if ch == "{" else "\\}") if i in unmatched else ch
        for i, ch in enumerate(text)
    )


#: TeX primitives/commands that can read or write files, reach the shell, or
#: rebind catcodes — none of which a schematic label has any business using.
#: ``\write18`` is listed before ``\write`` so the alternation matches it whole;
#: the lookahead stops ``\include`` matching ``\includegraphics`` etc.
_DANGEROUS_LATEX_RE = re.compile(
    r"\\(?:write18|write|immediate|input|include|openin|openout|read|"
    r"csname|catcode|directlua|ShellEscape)(?![a-zA-Z])"
)


def contains_dangerous_latex(text: str) -> bool:
    r"""True when *text* contains a high-risk TeX primitive.

    Detects ``\write18``/``\write``, ``\immediate``, ``\input``, ``\include``,
    ``\openin``/``\openout``, ``\read``, ``\csname``, ``\catcode``,
    ``\directlua`` and ``\ShellEscape`` anywhere in the text.  Benign math
    (``$\frac{V_i}{R}$``) does not trigger.  Pure and Qt-free, so both the code
    generator and the UI's load-time warning can share it.  This is a *warning*
    heuristic — the real defence is that every compile runs with
    ``-no-shell-escape`` (see app/preview/latex.py, app/preview/mathrender.py).
    """
    return bool(_DANGEROUS_LATEX_RE.search(text or ""))


def _is_single_brace_group(value: str) -> bool:
    """True if *value* is wholly enclosed in one matching ``{...}`` group."""
    if not (value.startswith("{") and value.endswith("}")):
        return False
    depth = 0
    for i, ch in enumerate(value):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and i != len(value) - 1:
                return False
    return depth == 0


def protect_label_commas(options: str) -> str:
    r"""Brace-protect option *values* whose commas TikZ would mis-split.

    pgfkeys splits a ``key=value`` option list on commas and — unlike
    :func:`split_top_level` — does **not** treat ``$...$`` as protecting them, so
    a label like ``v=$\phi(0,0)$`` is read as the bogus keys ``v=$\phi(0`` and
    ``0)$``.  Wrapping such a value in braces (``v={$\phi(0,0)$}``) makes pgfkeys
    treat it atomically; rendering is unchanged.  Values that are already a
    single ``{...}`` group, and segments without a comma, are left untouched.
    """
    out: list[str] = []
    for seg in split_top_level(options):
        key, eq, val = seg.partition("=")
        v = val.strip()
        if eq and "," in v and not _is_single_brace_group(v):
            out.append(f"{key.strip()}={{{v}}}")
        else:
            out.append(seg.strip())
    return ", ".join(s for s in out if s)


def compose_style_options(
    *, fill_color: str = "", line_width: float = _DEFAULT_LINE_WIDTH, line_style: str = ""
) -> str:
    """Build a TikZ draw-options string from style fields.

    Order is ``line_style, line width, fill``.  A line width equal to the
    TikZ default (0.4 pt) and empty fill/line_style are omitted, so a fully
    default style composes to ``""`` (no brackets emitted by callers).
    """
    parts: list[str] = []
    if line_style:
        parts.append(line_style)
    if abs(line_width - _DEFAULT_LINE_WIDTH) > 1e-6:
        bw = line_width
        bw_str = (
            str(int(bw)) if bw == int(bw)
            else (f"{bw:.1f}" if bw == round(bw, 1) else f"{bw:.2f}")
        )
        parts.append(f"line width={bw_str}pt")
    if fill_color:
        parts.append(f"fill={fill_color}")
    return ", ".join(parts)
