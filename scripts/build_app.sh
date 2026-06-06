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

# Ensure the canonical license texts are present for the LGPLv3 notice that ships
# inside the bundle (see licenses/THIRD_PARTY_LICENSES.md). LGPL-3.0.txt is
# committed; refresh it and fetch GPL-3.0.txt (referenced by the LGPLv3) if a
# network is available. A failed fetch is non-fatal as long as the files exist.
echo "Ensuring third-party license texts…"
fetch_license() {  # url, dest
  if command -v curl >/dev/null 2>&1; then
    curl -sSL --max-time 20 "$1" -o "$2" && return 0
  fi
  return 1
}
fetch_license https://www.gnu.org/licenses/lgpl-3.0.txt licenses/LGPL-3.0.txt || true
fetch_license https://www.gnu.org/licenses/gpl-3.0.txt  licenses/GPL-3.0.txt  || true
for f in licenses/LGPL-3.0.txt licenses/GPL-3.0.txt; do
  if [[ ! -s "$f" ]]; then
    echo "WARNING: $f is missing/empty. The bundle's LGPLv3 notice references it;"
    echo "         fetch it from https://www.gnu.org/licenses/ before distributing."
  fi
done

echo "Cleaning previous build…"
rm -rf build dist

echo "Building with PyInstaller…"
uv run pyinstaller --noconfirm --clean heaviside.spec

echo
echo "Done. Output in: dist/"
ls -1 dist/
