#!/usr/bin/env python3
"""
Build the Heaviside Linux AppImage from the PyInstaller onedir build.

    python scripts/make_appimage.py [OUTPUT.AppImage] [SOURCE_DIR]

Defaults: ``dist/Heaviside-linux-<arch>.AppImage`` (``<arch>`` is the build
host's ``uname -m``: x86_64, aarch64, …) from ``dist/Heaviside``.

An AppImage is a single self-contained, no-root, "download-and-run" file — the
Linux analogue of the macOS ``.dmg`` and the Windows installer. It bundles the
whole onedir build plus the freedesktop integration files (a ``.desktop`` entry,
icon, ``.hv`` MIME definition and AppStream metainfo) so file managers can offer
"Open with Heaviside". Unlike Flatpak/Snap it is **not** sandboxed, so the app
can still shell out to the user's system ``pdflatex``.

Assembles an AppDir::

    Heaviside.AppDir/
      AppRun                 -> execs usr/bin/Heaviside/Heaviside
      heaviside.desktop      (copy of packaging/heaviside.desktop)
      heaviside.png          (256x256 icon rendered from assets/icon.png)
      usr/bin/Heaviside/...  (the PyInstaller onedir folder)
      usr/share/applications/heaviside.desktop
      usr/share/icons/hicolor/256x256/apps/heaviside.png
      usr/share/metainfo/heaviside.appdata.xml
      usr/share/mime/packages/heaviside-mime.xml

then runs ``appimagetool`` on it.

**Linux only.** On other platforms this is a no-op so it can sit in a
cross-platform build driver. ``appimagetool`` must be on ``PATH`` (or pointed to
by the ``APPIMAGETOOL`` env var); if it is missing the build is skipped with a
hint rather than failing.
"""

from __future__ import annotations

import os
import platform
import shutil
import stat
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGING = _ROOT / "packaging"
_DESKTOP = _PACKAGING / "heaviside.desktop"
_MIME = _PACKAGING / "heaviside-mime.xml"
_APPDATA = _PACKAGING / "heaviside.appdata.xml"
_ICON_SRC = _ROOT / "assets" / "icon.png"

#: Icon basename referenced by Icon=heaviside in the .desktop file.
_ICON_NAME = "heaviside"
_ICON_SIZE = 256

_APPRUN = """#!/bin/sh
# AppImage entry point: resolve our own directory and exec the bundled binary,
# forwarding any arguments (e.g. a .hv file path the file manager passed).
HERE="$(dirname "$(readlink -f "${0}")")"
exec "${HERE}/usr/bin/Heaviside/Heaviside" "$@"
"""


def _app_version() -> str:
    sys.path.insert(0, str(_ROOT))
    from app.version import __version__
    return __version__


def _find_appimagetool() -> str | None:
    """Locate appimagetool: the APPIMAGETOOL env var first, then PATH."""
    env = os.environ.get("APPIMAGETOOL")
    if env and Path(env).exists():
        return env
    return (shutil.which("appimagetool")
            or shutil.which(f"appimagetool-{platform.machine()}.AppImage"))


def _render_icon(dest: Path, size: int = _ICON_SIZE) -> None:
    """Render a square PNG icon of *size* from assets/icon.png (Pillow)."""
    from PIL import Image

    img = Image.open(_ICON_SRC).convert("RGBA")
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
    canvas.resize((size, size), Image.LANCZOS).save(dest, format="PNG")


def _build_appdir(source_dir: Path, appdir: Path | None = None) -> Path:
    """Assemble the AppDir tree from the onedir *source_dir*; return its path."""
    if appdir is None:
        appdir = _ROOT / "dist" / "Heaviside.AppDir"
    shutil.rmtree(appdir, ignore_errors=True)

    # The onedir build → usr/bin/Heaviside/.
    bindir = appdir / "usr" / "bin" / "Heaviside"
    shutil.copytree(source_dir, bindir)

    # Freedesktop integration files.
    (appdir / "usr" / "share" / "applications").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_DESKTOP, appdir / "usr" / "share" / "applications" / "heaviside.desktop")

    icon_dir = appdir / "usr" / "share" / "icons" / "hicolor" / f"{_ICON_SIZE}x{_ICON_SIZE}" / "apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    _render_icon(icon_dir / f"{_ICON_NAME}.png")

    (appdir / "usr" / "share" / "metainfo").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_APPDATA, appdir / "usr" / "share" / "metainfo" / "heaviside.appdata.xml")

    (appdir / "usr" / "share" / "mime" / "packages").mkdir(parents=True, exist_ok=True)
    shutil.copy2(_MIME, appdir / "usr" / "share" / "mime" / "packages" / "heaviside-mime.xml")

    # AppDir-root requirements: .desktop, icon, and an executable AppRun.
    shutil.copy2(_DESKTOP, appdir / "heaviside.desktop")
    _render_icon(appdir / f"{_ICON_NAME}.png")
    apprun = appdir / "AppRun"
    apprun.write_text(_APPRUN, encoding="utf-8")
    apprun.chmod(apprun.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    return appdir


def make_appimage(output: Path, source_dir: Path) -> Path:
    if sys.platform != "linux":
        print("make_appimage: Linux only; skipping on", sys.platform)
        return output

    if not source_dir.is_dir():
        raise SystemExit(
            f"Source folder not found: {source_dir} — build the app first "
            f"(pyinstaller heaviside.spec / scripts/build.py)."
        )

    tool = _find_appimagetool()
    if tool is None:
        raise SystemExit(
            "appimagetool not found. Install it (https://github.com/AppImage/"
            "appimagetool/releases) and put it on PATH, or set APPIMAGETOOL."
        )

    appdir = _build_appdir(source_dir)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    # The runtime architecture appimagetool embeds. Must follow the build host
    # (x86_64 on the x64 runner, aarch64 on the ARM runner) — a hardcoded value
    # would mislabel the other arch's build. An explicit ARCH env still wins.
    env.setdefault("ARCH", platform.machine())
    env["VERSION"] = _app_version()            # recorded in the AppImage metadata
    # CI runners frequently lack FUSE; let appimagetool (itself an AppImage) and
    # the produced image run by self-extracting instead of mounting.
    env.setdefault("APPIMAGE_EXTRACT_AND_RUN", "1")

    cmd = [tool, "--no-appstream", str(appdir), str(output)]
    print("Building AppImage:", " ".join(cmd))
    subprocess.run(cmd, check=True, env=env)

    if not output.exists():
        raise SystemExit(f"appimagetool reported success but {output} was not produced.")
    output.chmod(output.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print("AppImage:", output)
    return output


def main() -> None:
    argv = sys.argv[1:]
    output = (Path(argv[0]) if len(argv) >= 1
              else _ROOT / "dist" / f"Heaviside-linux-{platform.machine()}.AppImage")
    source = Path(argv[1]) if len(argv) >= 2 else _ROOT / "dist" / "Heaviside"
    make_appimage(output, source)


if __name__ == "__main__":
    main()
