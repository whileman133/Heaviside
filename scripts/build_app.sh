#!/usr/bin/env bash
# Build a standalone Heaviside app with PyInstaller.
#
#   macOS         -> dist/Heaviside.app
#   Windows/Linux -> dist/Heaviside/
#
# Re-runs are clean: build/ and dist/ are wiped first. See heaviside.spec for
# what gets bundled. Note the preview/export features still need pdflatex and
# Poppler installed on the target machine (see README).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "Cleaning previous build…"
rm -rf build dist

echo "Building with PyInstaller…"
uv run pyinstaller --noconfirm --clean heaviside.spec

echo
echo "Done. Output in: dist/"
ls -1 dist/
