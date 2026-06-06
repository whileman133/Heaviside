#!/usr/bin/env python3
"""
Regenerate assets/icon.ico from assets/icon.png (the Windows app icon).

Mirrors scripts/make_icns.sh (macOS): the source PNG need not be square — it is
padded onto a transparent square canvas (no distortion), then written as a
multi-resolution .ico so Windows can pick the right size for the taskbar, title
bar, Explorer, etc.

Usage:
    python scripts/make_ico.py            # uses assets/icon.png -> assets/icon.ico

Requires Pillow (in the dev dependency group).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

# Standard Windows .ico sizes (Pillow embeds all of them in one file).
_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    src = root / "assets" / "icon.png"
    out = root / "assets" / "icon.ico"

    if not src.exists():
        print(f"missing {src}", file=sys.stderr)
        return 1

    img = Image.open(src).convert("RGBA")

    # Pad onto a transparent square canvas (centre) so the icon is not distorted.
    side = max(img.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))

    square.save(out, format="ICO", sizes=_SIZES)
    print(f"wrote {out} ({', '.join(f'{w}x{h}' for w, h in _SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
