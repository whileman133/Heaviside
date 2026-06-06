# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller build spec for Heaviside.

Build:
    uv run pyinstaller heaviside.spec            # or: pyinstaller heaviside.spec

Output:
    macOS          -> dist/Heaviside.app   (drag to /Applications)
    Windows/Linux  -> dist/Heaviside/      (run the Heaviside executable inside)

Runtime dependencies that are NOT bundled (the app warns at startup if missing):
    * pdflatex   — from a TeX distribution (TeX Live / MiKTeX); needed for the
      LaTeX preview and the PDF/EPS exports.
    * pdftoppm / pdftocairo — from Poppler; needed for the preview image and
      EPS export.
Bundling a full TeX distribution is impractical, so these stay external. Pure
editing, source generation, and .tex export work without them.
"""

import sys
from glob import glob

from PyInstaller.utils.hooks import collect_data_files

# Runtime resources, paths relative to the project root, mirroring the layout
# app/resources.py expects.
datas = [
    ("assets/icon.png", "assets"),
    # The manifest is self-contained — it bakes in all symbol geometry including
    # resolved +/- glyph marks (see tools/export_circuitikz_svgs.py), so svgsym.py
    # reads ONLY the manifest at runtime. The intermediate .svg files are build
    # artifacts and are not bundled.
    ("tools/circuitikz_svgs/manifest.json", "tools/circuitikz_svgs"),
]
# Example schematics for the File → Open Example menu. Only the .hv sources are
# bundled — the co-located .pdf/.eps are regenerable and intentionally skipped.
datas += [(f, "examples") for f in glob("examples/*.hv")]
# qtawesome ships its icon fonts as package data (toolbar/ribbon glyphs).
datas += collect_data_files("qtawesome")
# Third-party license notices. The bundled Qt/PySide6 is LGPLv3, which requires
# the attribution notice and license text to travel *inside* the distributed
# application (.app / Heaviside/ folder). Ship the whole licenses/ folder.
datas += [(f, "licenses") for f in glob("licenses/*")]

# Trim clearly-unused heavyweight Qt modules to keep the bundle smaller. These
# are safe for this app (no web, 3D, QML, multimedia, charts, or networking).
# If a future feature needs one, remove it from this list.
excludes = [
    "tkinter",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtBluetooth",
    "PySide6.QtNetwork",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Heaviside",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=True,   # macOS: deliver file-open AppleEvents as argv
    target_arch=None,      # build for the host arch; set "universal2" for both
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.icns",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Heaviside",
)

# macOS: wrap the one-dir build in a proper .app bundle. The BUNDLE step is a
# no-op on other platforms, where dist/Heaviside/ is the deliverable.
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Heaviside.app",
        icon="assets/icon.icns",
        bundle_identifier="com.heaviside.editor",
        version="0.1.0",
        info_plist={
            "CFBundleName": "Heaviside",
            "CFBundleDisplayName": "Heaviside",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "Wesley Hileman",
            # Associate the .hv schematic document type with this app.
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Heaviside Schematic",
                    "CFBundleTypeExtensions": ["hv"],
                    "CFBundleTypeRole": "Editor",
                    "LSHandlerRank": "Owner",
                }
            ],
        },
    )
