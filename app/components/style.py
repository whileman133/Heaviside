"""
Fill/border style helpers shared by StyledComponent consumers.

A StyledComponent carries its appearance as three fields — ``fill_color``,
``border_width`` (pt), and ``line_style`` (raw TikZ draw tokens such as
``"dashed"``).  Both the canvas, the code generator, and the file loader need
to turn those fields into a TikZ draw-options string (and, for migrating legacy
rect files, to parse such a string back into the fields).  These pure functions
are the single source of truth for that mapping.

No Qt dependency.
"""

from __future__ import annotations

import re

_DEFAULT_BORDER_WIDTH = 0.4  # pt — matches the TikZ default and StyledComponent


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
    *, fill_color: str = "", border_width: float = _DEFAULT_BORDER_WIDTH, line_style: str = ""
) -> str:
    """Build a TikZ draw-options string from style fields.

    Order is ``line_style, line width, fill``.  A border width equal to the
    TikZ default (0.4 pt) and empty fill/line_style are omitted, so a fully
    default style composes to ``""`` (no brackets emitted by callers).
    """
    parts: list[str] = []
    if line_style:
        parts.append(line_style)
    if abs(border_width - _DEFAULT_BORDER_WIDTH) > 1e-6:
        bw = border_width
        bw_str = (
            str(int(bw)) if bw == int(bw)
            else (f"{bw:.1f}" if bw == round(bw, 1) else f"{bw:.2f}")
        )
        parts.append(f"line width={bw_str}pt")
    if fill_color:
        parts.append(f"fill={fill_color}")
    return ", ".join(parts)


def parse_style_options(options: str) -> tuple[str, float, str]:
    """Parse a TikZ draw-options string into ``(fill_color, border_width, line_style)``.

    The inverse of :func:`compose_style_options`.  Any tokens that are not a
    ``line width=…pt`` or ``fill=…`` clause are treated as ``line_style``.
    Returns defaults (``""``, 0.4, ``""``) for any missing part.
    """
    opts = options.strip()

    lw_match = re.search(r"line\s+width\s*=\s*([\d.]+)\s*pt", opts)
    border_width = float(lw_match.group(1)) if lw_match else _DEFAULT_BORDER_WIDTH
    opts = re.sub(r",?\s*line\s+width\s*=\s*[\d.]+\s*pt", "", opts).strip(", ")

    fill_match = re.search(r"fill\s*=\s*([^,]+)", opts)
    fill_color = fill_match.group(1).strip() if fill_match else ""
    opts = re.sub(r",?\s*fill\s*=\s*[^,]+", "", opts).strip(", ")

    line_style = opts.strip()
    return fill_color, border_width, line_style
