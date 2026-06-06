#!/usr/bin/env python3
"""
Build a standalone Heaviside app with PyInstaller — cross-platform.

    macOS         -> dist/Heaviside.app
    Windows/Linux -> dist/Heaviside/

Runs identically on macOS, Windows, and Linux (replacing the old build_app.sh +
make_icns.sh shell scripts). Steps:

  1. Regenerate the app icons (.ico + .icns) from assets/icon.png if stale.
  2. Ensure the third-party license texts the LGPLv3 notice references are present.
  3. Wipe build/ and dist/, then run PyInstaller against heaviside.spec.

The preview/export features still need pdflatex (and Poppler for EPS) on the
target machine — see the README. Run from anywhere:

    python scripts/build.py
    uv run python scripts/build.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

_ICON_PNG = _ROOT / "assets" / "icon.png"
_ICON_OUTPUTS = [_ROOT / "assets" / "icon.ico", _ROOT / "assets" / "icon.icns"]

_LICENSES = {
    _ROOT / "licenses" / "LGPL-3.0.txt": "https://www.gnu.org/licenses/lgpl-3.0.txt",
    _ROOT / "licenses" / "GPL-3.0.txt": "https://www.gnu.org/licenses/gpl-3.0.txt",
}


def _stale(output: Path) -> bool:
    """True if *output* is missing or older than the source PNG."""
    return (not output.exists()) or (_ICON_PNG.stat().st_mtime > output.stat().st_mtime)


def regenerate_icons() -> None:
    if any(_stale(o) for o in _ICON_OUTPUTS):
        print("Regenerating app icons (.ico + .icns) from assets/icon.png…")
        subprocess.run(
            [sys.executable, str(_ROOT / "scripts" / "make_icons.py")],
            check=True,
        )
    else:
        print("App icons up to date.")


def ensure_license_texts() -> None:
    """Fetch the canonical LGPL/GPL texts the bundled notice references.

    A failed fetch is non-fatal as long as the file already exists (LGPL-3.0.txt
    is committed). See licenses/THIRD_PARTY_LICENSES.md.
    """
    print("Ensuring third-party license texts…")
    for dest, url in _LICENSES.items():
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                data = resp.read()
            if data:
                dest.write_bytes(data)
        except Exception:
            pass  # offline / unreachable — fall back to whatever is committed
        if not dest.exists() or dest.stat().st_size == 0:
            print(
                f"WARNING: {dest.relative_to(_ROOT)} is missing/empty. The bundle's "
                f"LGPLv3 notice references it; fetch it from "
                f"https://www.gnu.org/licenses/ before distributing."
            )


def clean() -> None:
    print("Cleaning previous build…")
    for d in ("build", "dist"):
        shutil.rmtree(_ROOT / d, ignore_errors=True)


def build() -> None:
    print("Building with PyInstaller…")
    subprocess.run(
        ["pyinstaller", "--noconfirm", "--clean", "heaviside.spec"],
        cwd=_ROOT,
        check=True,
    )


def main() -> int:
    regenerate_icons()
    ensure_license_texts()
    clean()
    build()

    dist = _ROOT / "dist"
    print("\nDone. Output in: dist/")
    if dist.exists():
        for entry in sorted(dist.iterdir()):
            print(f"  {entry.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
