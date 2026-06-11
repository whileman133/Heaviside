#!/usr/bin/env python3
"""
Generate the macOS .dmg background art (the "drag to Applications" window).

Produces two PNGs in ``assets/`` sized to the dmg window (see
``packaging/dmg_settings.py``):

    assets/dmg-background.png        660 x 420   (1x)
    assets/dmg-background@2x.png    1320 x 840   (Retina)

``scripts/build.py`` / the release workflow combine these into a hi-DPI
``dmg-background.tiff`` (via ``tiffutil``) that Finder renders crisply at both
scales. The background draws only the title, an arrow, and instructions — the
real app icon and the ``Applications`` symlink are placed *on top* by dmgbuild at
the coordinates in ``packaging/dmg_settings.py``, so this art leaves those two
spots empty.

Pillow only, so it runs on any platform (the .dmg itself is built on macOS).

    python scripts/make_dmg_background.py
    uv run python scripts/make_dmg_background.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

_ROOT = Path(__file__).resolve().parent.parent
_ICON_PNG = _ROOT / "assets" / "icon.png"
_OUT_1X = _ROOT / "assets" / "dmg-background.png"
_OUT_2X = _ROOT / "assets" / "dmg-background@2x.png"

# Window geometry in points. MUST match window_rect / icon_locations in
# packaging/dmg_settings.py.
_W, _H = 660, 420
_APP_CENTER = (170, 235)
_APPS_CENTER = (490, 235)

#: Fallback accent if the icon can't be sampled.
_DEFAULT_ACCENT = "#3B6EA5"


def _accent_from_icon() -> tuple[int, int, int]:
    """Pick a saturated, mid-tone accent colour from the app icon so the art is
    on-brand. Falls back to a tasteful blue if the icon is unreadable."""
    try:
        img = Image.open(_ICON_PNG).convert("RGBA")
    except Exception:  # noqa: BLE001
        return ImageColor.getrgb(_DEFAULT_ACCENT)
    img.thumbnail((64, 64))
    colors = img.getcolors(maxcolors=64 * 64) or []
    best, best_score = None, -1.0
    for _count, (r, g, b, a) in colors:
        if a < 200:
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        sat = (mx - mn) / 255.0
        mid = 1.0 - abs((mx + mn) / 2 / 255.0 - 0.5) * 2  # peaks at mid lightness
        score = sat * 0.7 + mid * 0.3
        if score > best_score:
            best, best_score = (r, g, b), score
    return best or ImageColor.getrgb(_DEFAULT_ACCENT)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Load a clean sans-serif at *size*, preferring native macOS faces and
    falling back to the always-present bundled DejaVu Sans, then Pillow's
    built-in bitmap font."""
    # Prefer faces that Pillow/FreeType render with correct advance widths. The
    # SF Pro *variable* font (/System/Library/Fonts/SFNS.ttf) renders with loose,
    # un-kerned spacing in Pillow — e.g. a visible gap after a narrow "i" — so it
    # is deliberately omitted. The .dmg is only built on macOS, where Helvetica
    # Neue is always present; DejaVu Sans is the cross-platform fallback.
    candidates = [
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    try:  # ziafont ships DejaVuSans.ttf and is a declared dependency.
        import ziafont
        d = Path(ziafont.__file__).resolve().parent / "fonts" / "DejaVuSans.ttf"
        candidates.append(str(d))
    except Exception:  # noqa: BLE001
        pass
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:  # noqa: BLE001
            continue
    return ImageFont.load_default()


def _mix(c1, c2, t: float) -> tuple[int, int, int]:
    return tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))


def _draw(scale: int) -> Image.Image:
    """Render the background at integer *scale* (1 or 2)."""
    accent = _accent_from_icon()
    W, H = _W * scale, _H * scale
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)

    # Soft vertical wash: a faint accent tint at the top fading to white.
    top = _mix((255, 255, 255), accent, 0.10)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=_mix(top, (255, 255, 255), min(1.0, t * 1.4)))

    # Thin accent rule under the header.
    d.rectangle([0, 104 * scale, W, 106 * scale], fill=_mix((255, 255, 255), accent, 0.35))

    def centered(text, cx, cy, font, fill):
        l, t_, r, b = d.textbbox((0, 0), text, font=font)
        d.text((cx - (r - l) / 2, cy - (b - t_) / 2), text, font=font, fill=fill)

    ink = (40, 44, 52)
    sub = (110, 116, 126)
    centered("Heaviside", W / 2, 52 * scale, _font(34 * scale), ink)
    centered("CircuiTikZ Schematic Editor", W / 2, 84 * scale, _font(15 * scale), sub)

    # Arrow from the app slot toward Applications.
    ay = _APP_CENTER[1] * scale
    x0 = (_APP_CENTER[0] + 80) * scale
    x1 = (_APPS_CENTER[0] - 80) * scale
    shaft_h = 6 * scale
    head_w = 22 * scale
    head_h = 26 * scale
    d.rectangle([x0, ay - shaft_h // 2, x1 - head_w, ay + shaft_h // 2], fill=accent)
    d.polygon(
        [(x1 - head_w, ay - head_h // 2), (x1, ay), (x1 - head_w, ay + head_h // 2)],
        fill=accent,
    )

    centered(
        "To install, drag the Heaviside icon onto the Applications folder.",
        W / 2, 350 * scale, _font(14 * scale), sub,
    )

    return img


def main() -> int:
    _OUT_1X.parent.mkdir(parents=True, exist_ok=True)
    _draw(1).save(_OUT_1X)
    _draw(2).save(_OUT_2X)
    print(f"Wrote {_OUT_1X.relative_to(_ROOT)} and {_OUT_2X.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
