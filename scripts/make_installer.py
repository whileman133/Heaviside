#!/usr/bin/env python3
"""
Build the Heaviside Windows installer (Inno Setup) from the PyInstaller onedir
build.

    python scripts/make_installer.py [OUTPUT.exe] [SOURCE_DIR]

Defaults: ``dist/HeavisideSetup.exe`` from ``dist/Heaviside``.

Runs the Inno Setup compiler (``iscc``) on ``packaging/heaviside.iss``, passing
the app version (``app.version.__version__``) and paths on the command line so
nothing is hardcoded. The resulting installer is per-user (no UAC), adds Start
Menu / optional Desktop shortcuts, an uninstaller, and a ``.hv`` file
association.

**Windows only** (``iscc`` is an Inno Setup tool). On other platforms this is a
no-op so it can sit in a cross-platform build driver. In CI the release workflow
installs Inno Setup, builds the app, then calls this (after optionally signing
``Heaviside.exe``); the produced installer can then itself be signed.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ISS = _ROOT / "packaging" / "heaviside.iss"


def _find_iscc() -> str | None:
    """Locate the Inno Setup compiler: PATH first, then the usual install dirs."""
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    candidates = [
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _app_version() -> str:
    sys.path.insert(0, str(_ROOT))
    from app.version import __version__
    return __version__


def make_installer(output: Path, source_dir: Path) -> Path:
    if sys.platform != "win32":
        print("make_installer: Windows only; skipping on", sys.platform)
        return output

    if not source_dir.is_dir():
        raise SystemExit(
            f"Source folder not found: {source_dir} — build the app first "
            f"(pyinstaller heaviside.spec / scripts/build.py)."
        )

    iscc = _find_iscc()
    if iscc is None:
        raise SystemExit(
            "Inno Setup compiler (iscc) not found. Install it "
            "(choco install innosetup -y, or https://jrsoftware.org/isdl.php)."
        )

    version = _app_version()
    out_dir = output.resolve().parent
    out_base = output.stem  # filename without ".exe"

    cmd = [
        iscc,
        f"/DAppVersion={version}",
        f"/DSourceDir={source_dir.resolve()}",
        f"/DOutputDir={out_dir}",
        f"/DOutputBase={out_base}",
        str(_ISS),
    ]
    print("Building installer:", " ".join(cmd))
    subprocess.run(cmd, check=True)

    if not output.exists():
        raise SystemExit(f"iscc reported success but {output} was not produced.")
    print("Installer:", output)
    return output


def main() -> None:
    argv = sys.argv[1:]
    output = Path(argv[0]) if len(argv) >= 1 else _ROOT / "dist" / "HeavisideSetup.exe"
    source = Path(argv[1]) if len(argv) >= 2 else _ROOT / "dist" / "Heaviside"
    make_installer(output, source)


if __name__ == "__main__":
    main()
