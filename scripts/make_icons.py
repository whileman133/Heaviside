#!/usr/bin/env python3
"""
Regenerate the app icons from assets/icon.png — cross-platform, Pillow only.

Produces both platform formats from the single PNG source, with no OS-specific
tooling (no macOS ``sips``/``iconutil``), so the icons can be rebuilt on any
platform — macOS, Windows, or the Linux CI runner:

* ``assets/icon.ico``  — Windows (multi-resolution, 16–256 px)
* ``assets/icon.icns`` — macOS (Retina sizes up to 512@2x)

The source PNG need not be square: it is first padded onto a transparent square
canvas (centred, no distortion).

Usage:
    python scripts/make_icons.py            # both formats
    python scripts/make_icons.py --ico      # just the .ico
    python scripts/make_icons.py --icns     # just the .icns

Requires Pillow (in the dev dependency group).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "assets" / "icon.png"
_ICO = _ROOT / "assets" / "icon.ico"
_ICNS = _ROOT / "assets" / "icon.icns"

# Windows .ico embedded sizes.
_ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _squared_source() -> Image.Image:
    """Load icon.png and pad it onto a transparent square canvas (no distortion)."""
    img = Image.open(_SRC).convert("RGBA")
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    return canvas


def make_ico(square: Image.Image) -> None:
    square.save(_ICO, format="ICO", sizes=_ICO_SIZES)
    print(f"wrote {_ICO.relative_to(_ROOT)} "
          f"({', '.join(f'{w}x{h}' for w, h in _ICO_SIZES)})")


def make_icns(square: Image.Image) -> None:
    # Pillow derives the standard .icns members from a large master image; give
    # it a 1024x1024 so it can emit every size including 512@2x.
    master = square.resize((1024, 1024), Image.LANCZOS)
    master.save(_ICNS, format="ICNS")
    print(f"wrote {_ICNS.relative_to(_ROOT)} (up to 512x512@2x)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate app icons from assets/icon.png.")
    parser.add_argument("--ico", action="store_true", help="generate only the Windows .ico")
    parser.add_argument("--icns", action="store_true", help="generate only the macOS .icns")
    args = parser.parse_args()

    if not _SRC.exists():
        parser.error(f"missing {_SRC}")

    # No flag → both.
    do_ico = args.ico or not (args.ico or args.icns)
    do_icns = args.icns or not (args.ico or args.icns)

    square = _squared_source()
    if do_ico:
        make_ico(square)
    if do_icns:
        make_icns(square)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
