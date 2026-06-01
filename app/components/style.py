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
