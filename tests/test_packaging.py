"""Static checks on the Linux AppImage packaging assets and assembly logic.

These cover the freedesktop integration files (``.desktop`` entry, ``.hv`` MIME
definition, AppStream metainfo) and the AppDir assembly in
``scripts/make_appimage.py`` — the parts that are easy to get subtly wrong and
cheap to verify without an ``appimagetool`` (the actual image build is Linux-only
and exercised in CI).
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from PIL import Image

_ROOT = Path(__file__).resolve().parent.parent
_PACKAGING = _ROOT / "packaging"

#: The single MIME type that ties the three integration files together.
_MIME_TYPE = "application/x-heaviside"

sys.path.insert(0, str(_ROOT / "scripts"))
import make_appimage  # noqa: E402


def _desktop_entries() -> dict[str, str]:
    text = (_PACKAGING / "heaviside.desktop").read_text(encoding="utf-8")
    entries = {}
    for line in text.splitlines():
        if "=" in line and not line.startswith("["):
            key, _, value = line.partition("=")
            entries[key.strip()] = value.strip()
    return entries


def test_desktop_entry_is_well_formed():
    e = _desktop_entries()
    assert e["Type"] == "Application"
    assert e["Name"] == "Heaviside"
    assert e["Icon"] == "heaviside"          # must match the AppDir-root heaviside.png
    assert "%f" in e["Exec"]                 # so a file manager can pass a .hv path
    assert e["Terminal"] == "false"
    # The advertised MIME type must be the shared one.
    assert e["MimeType"].rstrip(";") == _MIME_TYPE


def test_mime_definition_registers_hv():
    root = ET.parse(_PACKAGING / "heaviside-mime.xml").getroot()
    ns = "{http://www.freedesktop.org/standards/shared-mime-info}"
    mt = root.find(f"{ns}mime-type")
    assert mt is not None and mt.get("type") == _MIME_TYPE
    glob = mt.find(f"{ns}glob")
    assert glob is not None and glob.get("pattern") == "*.hv"


def test_appdata_is_consistent():
    root = ET.parse(_PACKAGING / "heaviside.appdata.xml").getroot()
    assert root.tag == "component"
    assert root.findtext("id") == "com.heaviside.editor"
    # Launchable points at our desktop id; provides the shared MIME type.
    assert root.find("launchable").text == "heaviside.desktop"
    media = [m.text for m in root.iter("mediatype")]
    assert _MIME_TYPE in media


def test_render_icon_makes_square_png(tmp_path):
    dest = tmp_path / "heaviside.png"
    make_appimage._render_icon(dest, size=256)
    with Image.open(dest) as img:
        assert img.size == (256, 256)
        assert img.mode == "RGBA"


def test_build_appdir_assembles_tree(tmp_path):
    # A stand-in onedir build: the executable plus an _internal payload file.
    source = tmp_path / "Heaviside"
    (source / "_internal").mkdir(parents=True)
    (source / "Heaviside").write_text("#!/bin/sh\n", encoding="utf-8")
    (source / "_internal" / "payload.bin").write_bytes(b"x")

    appdir = make_appimage._build_appdir(source, appdir=tmp_path / "Heaviside.AppDir")

    # The binary landed under usr/bin/Heaviside/.
    assert (appdir / "usr" / "bin" / "Heaviside" / "Heaviside").is_file()
    assert (appdir / "usr" / "bin" / "Heaviside" / "_internal" / "payload.bin").is_file()
    # AppDir-root requirements: .desktop, icon, executable AppRun.
    assert (appdir / "heaviside.desktop").is_file()
    assert (appdir / "heaviside.png").is_file()
    apprun = appdir / "AppRun"
    assert apprun.is_file()
    import os
    assert os.access(apprun, os.X_OK)
    assert "usr/bin/Heaviside/Heaviside" in apprun.read_text(encoding="utf-8")
    # Shared integration files installed under usr/share/.
    assert (appdir / "usr" / "share" / "applications" / "heaviside.desktop").is_file()
    assert (appdir / "usr" / "share" / "metainfo" / "heaviside.appdata.xml").is_file()
    assert (appdir / "usr" / "share" / "mime" / "packages" / "heaviside-mime.xml").is_file()
    assert (appdir / "usr" / "share" / "icons" / "hicolor" / "256x256" / "apps"
            / "heaviside.png").is_file()


@pytest.mark.skipif(sys.platform == "linux", reason="off-Linux no-op behaviour")
def test_make_appimage_is_noop_off_linux(tmp_path):
    out = tmp_path / "Heaviside-linux-x86_64.AppImage"
    # Returns the output path unchanged and does not raise or build anything.
    assert make_appimage.make_appimage(out, tmp_path / "Heaviside") == out
    assert not out.exists()


def test_make_appimage_arch_follows_build_host(tmp_path, monkeypatch):
    """The ARCH appimagetool embeds must follow the build host — the release
    matrix builds on x64 *and* arm64 runners, and a hardcoded value would
    mislabel the other arch's image. An explicit ARCH env still wins."""
    source = tmp_path / "Heaviside"
    source.mkdir()
    (source / "Heaviside").write_text("#!/bin/sh\n", encoding="utf-8")

    captured = {}

    def _fake_run(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        Path(cmd[-1]).write_bytes(b"AI")  # the "built" image

    monkeypatch.setattr(make_appimage.sys, "platform", "linux")
    monkeypatch.setattr(make_appimage, "_find_appimagetool",
                        lambda: "/usr/bin/appimagetool")
    monkeypatch.setattr(make_appimage, "_build_appdir",
                        lambda s: tmp_path / "Heaviside.AppDir")
    monkeypatch.setattr(make_appimage.subprocess, "run", _fake_run)
    monkeypatch.setattr(make_appimage.platform, "machine", lambda: "aarch64")
    monkeypatch.delenv("ARCH", raising=False)

    make_appimage.make_appimage(tmp_path / "Heaviside-linux-aarch64.AppImage", source)
    assert captured["env"]["ARCH"] == "aarch64"

    monkeypatch.setenv("ARCH", "x86_64")  # an explicit override is respected
    make_appimage.make_appimage(tmp_path / "Heaviside-linux-x86_64.AppImage", source)
    assert captured["env"]["ARCH"] == "x86_64"
