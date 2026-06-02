#!/usr/bin/env bash
# Build a standalone Heaviside app with PyInstaller.
#
#   macOS         -> dist/Heaviside.app
#   Windows/Linux -> dist/Heaviside/
#
# Re-runs are clean: build/ and dist/ are wiped first. On macOS the app icon is
# regenerated from assets/icon.png first if assets/icon.icns is missing or older
# than the PNG. See heaviside.spec for what gets bundled. Note the preview/export
# features still need pdflatex (and Poppler for EPS) on the target (see README).
set -euo pipefail
cd "$(dirname "$0")/.."

# Regenerate the macOS app icon from the PNG if the .icns is missing or stale
# (PNG newer). make_icns.sh is macOS-only (sips/iconutil), so skip elsewhere.
if [[ "$(uname)" == "Darwin" ]]; then
  if [[ ! -f assets/icon.icns || assets/icon.png -nt assets/icon.icns ]]; then
    echo "Regenerating app icon (assets/icon.icns) from assets/icon.png…"
    ./scripts/make_icns.sh
  else
    echo "App icon up to date."
  fi
fi

echo "Cleaning previous build…"
rm -rf build dist

echo "Building with PyInstaller…"
uv run pyinstaller --noconfirm --clean heaviside.spec

echo
echo "Done. Output in: dist/"
ls -1 dist/
