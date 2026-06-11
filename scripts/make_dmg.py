#!/usr/bin/env python3
"""
Build the macOS "drag to Applications" .dmg from a (signed) Heaviside.app.

    python scripts/make_dmg.py [OUTPUT.dmg] [APP.app]

Defaults: ``dist/Heaviside-macos-arm64.dmg`` from ``dist/Heaviside.app``.

Steps:
  1. Ensure the background art exists (regenerate via make_dmg_background.py).
  2. Combine the 1x/@2x PNGs into a hi-DPI ``dmg-background.tiff`` with macOS's
     ``tiffutil`` so Finder renders it crisply on Retina; fall back to the 1x PNG
     if ``tiffutil`` is unavailable.
  3. Run ``dmgbuild`` with ``packaging/dmg_settings.py``.

macOS only (uses ``tiffutil``/``hdiutil`` via dmgbuild). In CI the release
workflow installs ``dmgbuild`` ad-hoc and calls this after signing the .app, so
the resulting image is signed/notarizable as a unit.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ASSETS = _ROOT / "assets"
_BG_1X = _ASSETS / "dmg-background.png"
_BG_2X = _ASSETS / "dmg-background@2x.png"
_BG_TIFF = _ASSETS / "dmg-background.tiff"
_SETTINGS = _ROOT / "packaging" / "dmg_settings.py"


def _ensure_background() -> Path:
    """Make sure the PNGs exist, then build a hi-DPI .tiff (best) or use the PNG."""
    if not (_BG_1X.exists() and _BG_2X.exists()):
        print("Generating .dmg background art…")
        subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "make_dmg_background.py")],
            check=True,
        )
    if shutil.which("tiffutil"):
        # -cathidpicheck packs the 1x + @2x into one multi-resolution TIFF.
        subprocess.run(
            ["tiffutil", "-cathidpicheck", str(_BG_1X), str(_BG_2X),
             "-out", str(_BG_TIFF)],
            check=True,
        )
        return _BG_TIFF
    print("tiffutil not found — using the 1x PNG background (non-Retina).")
    return _BG_1X


def main(argv: list[str]) -> int:
    if sys.platform != "darwin":
        print("make_dmg.py only runs on macOS.", file=sys.stderr)
        return 1

    output = Path(argv[0]) if len(argv) > 0 else _ROOT / "dist" / "Heaviside-macos-arm64.dmg"
    app = Path(argv[1]) if len(argv) > 1 else _ROOT / "dist" / "Heaviside.app"
    if not app.exists():
        print(f"App bundle not found: {app}. Build it first (scripts/build.py).",
              file=sys.stderr)
        return 1

    background = _ensure_background()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    try:
        import dmgbuild
    except ImportError:
        print("dmgbuild is not installed. Install it with: pip install dmgbuild",
              file=sys.stderr)
        return 1

    # dmg_settings.py reads these.
    os.environ["DMG_APP_PATH"] = str(app)
    os.environ["DMG_BACKGROUND"] = str(background)

    print(f"Building {output.name} from {app.name}…")
    dmgbuild.build_dmg(str(output), "Heaviside", settings_file=str(_SETTINGS))
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
