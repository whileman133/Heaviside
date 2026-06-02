#!/usr/bin/env bash
# Regenerate assets/icon.icns from assets/icon.png (macOS only).
#
# The source PNG need not be square: it is first padded onto a transparent
# square canvas (no distortion), then all standard iconset sizes are rendered
# and packed with iconutil.
set -euo pipefail
cd "$(dirname "$0")/.."

SRC="assets/icon.png"
OUT="assets/icon.icns"
[ -f "$SRC" ] || { echo "missing $SRC"; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SQUARE="$TMP/square.png"
ICONSET="$TMP/icon.iconset"
mkdir -p "$ICONSET"

# 1) Pad the (possibly non-square) source onto a transparent square canvas.
.venv/bin/python - "$SRC" "$SQUARE" <<'PY'
import sys
from PIL import Image
src, dst = sys.argv[1], sys.argv[2]
img = Image.open(src).convert("RGBA")
side = max(img.size)
canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2), img)
canvas.save(dst)
print(f"squared to {side}x{side}")
PY

# 2) Render each iconset size (square -> square, no distortion).
for sz in 16 32 128 256 512; do
  sips -z "$sz"   "$sz"   "$SQUARE" --out "$ICONSET/icon_${sz}x${sz}.png"      >/dev/null
  sips -z $((sz*2)) $((sz*2)) "$SQUARE" --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null
done

# 3) Pack.
iconutil -c icns "$ICONSET" -o "$OUT"
echo "wrote $OUT"
