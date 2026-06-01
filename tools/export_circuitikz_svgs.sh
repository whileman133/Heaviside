#!/usr/bin/env bash
# export_circuitikz_svgs.sh
#
# Renders every CircuiTikZ component to a normalised SVG and collects the
# path data into a JSON manifest at OUT_DIR/manifest.json.
#
# Usage:
#   chmod +x tools/export_circuitikz_svgs.sh
#   ./tools/export_circuitikz_svgs.sh          # outputs to tools/circuitikz_svgs/
#   OUT_DIR=/tmp/svgs ./tools/export_circuitikz_svgs.sh
#
# Requirements: latex, dvisvgm, python3 (all standard on macOS with MacTeX or
#               any Linux TeX Live install).
#
# Output layout:
#   OUT_DIR/
#     bipoles/   R.svg, C.svg, ...
#     tripoles/  nigfete.svg, npn.svg, ...
#     manifest.json   — { "R": { "kind": "bipole", "paths": [...], "viewBox": "..." }, ... }

set -euo pipefail

# Let dvisvgm find Ghostscript via Homebrew if LIBGS isn't already set.
if [[ -z "${LIBGS:-}" ]]; then
  _gs_lib="$(brew --prefix ghostscript 2>/dev/null)/lib/libgs.dylib"
  [[ -f "$_gs_lib" ]] && export LIBGS="$_gs_lib"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR/circuitikz_svgs}"
WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir -p "$OUT_DIR/bipoles" "$OUT_DIR/tripoles"

# ---------------------------------------------------------------------------
# Component lists
# ---------------------------------------------------------------------------

BIPOLES=(
  # Passives
  R C L
  # Diodes / semiconductors
  D D* Do Dz Dtz Dtv Dav Dv Dsch
  # Sources
  V I vsource isource vsourcesin isourcesin vsourceam isourceam
  vsourcesquare vsourcetri dcvsource dcisource
  cV cI
  # Switches
  nos ncs spst
  # Misc
  battery battery1 battery2
  short open
  ammeter voltmeter ohmmeter
  lamp bulb
  fuse afuse
  thermistor
  potentiometer generic
)

NODES=(
  # Ground / reference symbols (single-terminal, placed as node at (0,0))
  ground rground sground nground pground cground eground
)

TRIPOLES=(
  # MOSFETs
  nmos pmos nmosd pmosd
  nfet pfet nfetd pfetd
  nigfete pigfete nigfetd pigfetd nigfetebulk pigfetebulk
  # BJTs
  npn pnp
  # JFETs
  njfet pjfet
  # IGBTs
  nigbt pigbt
  # Op-amps / amplifiers
  "op amp" "plain amp" "en amp" "gm amp"
  "inst amp" "fd op amp" "fd inst amp"
  # Logic gates (american)
  "american and port" "american nand port"
  "american or port"  "american nor port"
  "american xor port" "american xnor port"
  # Logic gates (european)
  "european and port"  "european nand port"
  "european or port"   "european nor port"
  "european xnor port" "european xor port"
  "european not port"  "european buffer port"
  # Other
  spdt toggleswitch
  thyristor triac
  hemt isfet
  mixer circulator
  adder splitter wilkinson
)

# ---------------------------------------------------------------------------
# Helper: slugify a component name to a safe filename
# "op amp" → "op_amp",  "american and port" → "american_and_port"
# ---------------------------------------------------------------------------
slug() { echo "$1" | tr ' ' '_'; }

# ---------------------------------------------------------------------------
# Helper: render one component to SVG, return path to output file
#
#   render_bipole  "R"  → OUT_DIR/bipoles/R.svg
#   render_tripole "npn"→ OUT_DIR/tripoles/npn.svg
# ---------------------------------------------------------------------------
render_bipole() {
  local name="$1"
  local slug
  slug=$(slug "$name")
  local tex="$WORK_DIR/${slug}.tex"
  local dvi="$WORK_DIR/${slug}.dvi"
  local svg="$OUT_DIR/bipoles/${slug}.svg"

  cat > "$tex" <<TEX
\documentclass[border=2pt]{standalone}
\usepackage[american]{circuitikz}
\begin{document}
\begin{circuitikz}
  \draw (0,0) to[${name}] (2,0);
\end{circuitikz}
\end{document}
TEX

  latex -interaction=nonstopmode -output-directory="$WORK_DIR" "$tex" \
    > "$WORK_DIR/${slug}.log" 2>&1 || { echo "  [WARN] latex failed for bipole '$name'"; return 1; }
  dvisvgm --no-fonts "$dvi" -o "$svg" \
    > "$WORK_DIR/${slug}.dvisvgm.log" 2>&1 || { echo "  [WARN] dvisvgm failed for bipole '$name'"; return 1; }
  echo "$svg"
}

# ---------------------------------------------------------------------------
# Per-component terminal lead routing for multi-terminal parts.
#
# A bare `node[kind]{}` places the shape's terminal anchors wherever the
# circuitikz shape code puts them — which is NOT on any integer/half grid.
# Heaviside's canvas is a *preview* of the circuitikz output, and its logical
# model only allows connections on a 0.5 grid, so for the multi-terminal parts
# we actually use we draw explicit lead wires from each named anchor out to a
# clean grid coordinate (relative to the node `center`).  dvisvgm then captures
# those grid-aligned wire endpoints as the symbol's terminals, so the exported
# SVG already has on-grid terminals and the app needs no run-time bridging.
#
# Manhattan routing (`-|` / `|-`) lets the wire *end* land exactly on the grid
# target while the bend absorbs the off-grid anchor.  Pure `--` is used where
# the anchor already shares the target's x or y (no bend needed).
#
# TRIPOLE_LEADS[slug] holds the \draw lines (";"-free, newline-separated) added
# after the node.  Parts with no entry export as a bare node (back-compat).
declare -A TRIPOLE_LEADS
TRIPOLE_LEADS[nigfete]='\draw (X.drain)  -- (0.0164,0.7295);
  \draw (X.source) -- (0.0164,-0.7705);
  \draw (X.gate)   -| (-0.9836,-0.2705);'

TRIPOLE_LEADS[nigfetd]='\draw (X.drain)  -- (0.0164,0.7295);
  \draw (X.source) -- (0.0164,-0.7705);
  \draw (X.gate)   -| (-0.9836,-0.2705);'

TRIPOLE_LEADS[pigfete]='\draw (X.drain)  -- (0.0164,-0.7295);
  \draw (X.source) -- (0.0164,0.7705);
  \draw (X.gate)   -| (-0.9836,0.2705);'

TRIPOLE_LEADS[pigfetd]='\draw (X.drain)  -- (0.0164,-0.7295);
  \draw (X.source) -- (0.0164,0.7705);
  \draw (X.gate)   -| (-0.9836,0.2705);'

TRIPOLE_LEADS[op_amp]='\draw (X.out)  -- (1.5,0);
  \draw (X.+)    -| (-1.5,-0.5);
  \draw (X.-)    -| (-1.5, 0.5);'

# NPN BJT: base=left(0,0), collector=top-right(1,-1), emitter=bottom-right(1,1).
# CTikZ anchors sit slightly off-grid; leads extend them to clean 0.5-GU snap.
TRIPOLE_LEADS[npn]='\draw (X.C) -- (0.0129,1);
  \draw (X.E) -- (0.0129,-1);
  \draw (X.B) -- (-1,0);'

# PNP BJT: same geometry but emitter at top, collector at bottom.
TRIPOLE_LEADS[pnp]='\draw (X.E) -- (0.0129,1);
  \draw (X.C) -- (0.0129,-1);
  \draw (X.B) -- (-1,0);'

render_node() {
  local name="$1"
  local slug
  slug=$(slug "$name")
  local tex="$WORK_DIR/${slug}.tex"
  local dvi="$WORK_DIR/${slug}.dvi"
  local svg="$OUT_DIR/nodes/${slug}.svg"

  # Place node at (0,0) with a short vertical lead from the connection point
  # so the SVG origin aligns with the pin coordinate.
  cat > "$tex" <<TEX
\documentclass[border=2pt]{standalone}
\usepackage[american]{circuitikz}
\begin{document}
\begin{circuitikz}
  \draw (0,0) node[${name}] {};
\end{circuitikz}
\end{document}
TEX

  latex -interaction=nonstopmode -output-directory="$WORK_DIR" "$tex" \
    > "$WORK_DIR/${slug}.log" 2>&1 || { echo "  [WARN] latex failed for node '$name'"; return 1; }
  dvisvgm --no-fonts "$dvi" -o "$svg" \
    > "$WORK_DIR/${slug}.dvisvgm.log" 2>&1 || { echo "  [WARN] dvisvgm failed for node '$name'"; return 1; }
  echo "$svg"
}

render_tripole() {
  local name="$1"
  local slug
  slug=$(slug "$name")
  local tex="$WORK_DIR/${slug}.tex"
  local dvi="$WORK_DIR/${slug}.dvi"
  local svg="$OUT_DIR/tripoles/${slug}.svg"

  local leads="${TRIPOLE_LEADS[$slug]:-}"

  cat > "$tex" <<TEX
\documentclass[border=10pt]{standalone}
\usepackage[american]{circuitikz}
\begin{document}
\begin{circuitikz}
  \node[${name}] (X) at (0,0) {};
  ${leads}
\end{circuitikz}
\end{document}
TEX

  latex -interaction=nonstopmode -output-directory="$WORK_DIR" "$tex" \
    > "$WORK_DIR/${slug}.log" 2>&1 || { echo "  [WARN] latex failed for tripole '$name'"; return 1; }
  dvisvgm --no-fonts "$dvi" -o "$svg" \
    > "$WORK_DIR/${slug}.dvisvgm.log" 2>&1 || { echo "  [WARN] dvisvgm failed for tripole '$name'"; return 1; }
  echo "$svg"
}

# ---------------------------------------------------------------------------
# Render all components
# ---------------------------------------------------------------------------
mkdir -p "$OUT_DIR/nodes"

echo "=== Rendering nodes ==="
for name in "${NODES[@]}"; do
  echo -n "  $name ... "
  render_node "$name" > /dev/null && echo "OK" || true
done

echo "=== Rendering bipoles ==="
for name in "${BIPOLES[@]}"; do
  echo -n "  $name ... "
  render_bipole "$name" > /dev/null && echo "OK" || true
done

echo "=== Rendering tripoles ==="
for name in "${TRIPOLES[@]}"; do
  echo -n "  $name ... "
  render_tripole "$name" > /dev/null && echo "OK" || true
done

# ---------------------------------------------------------------------------
# Build manifest.json  (python3 for clean JSON + SVG parsing)
# ---------------------------------------------------------------------------
echo "=== Building manifest.json ==="
python3 - "$OUT_DIR" <<'PYEOF'
import sys, os, json, re, glob

out_dir = sys.argv[1]
manifest = {}

def parse_svg(svg_path, kind, name):
    with open(svg_path) as f:
        content = f.read()

    # viewBox
    vb = re.search(r"viewBox='([^']*)'", content)
    viewbox = vb.group(1) if vb else ""

    # width / height in pt
    w = re.search(r"width='([^']*)'", content)
    h = re.search(r"height='([^']*)'", content)

    # All path elements — extract d + stroke-width + fill
    paths = []
    for m in re.finditer(r"<path ([^>]*)/>", content):
        attrs_str = m.group(1)
        d     = re.search(r"d='([^']*)'", attrs_str)
        sw    = re.search(r"stroke-width='([^']*)'", attrs_str)
        fill  = re.search(r"fill='([^']*)'", attrs_str)
        stroke= re.search(r"stroke='([^']*)'", attrs_str)
        if d:
            paths.append({
                "d":            d.group(1),
                "stroke_width": float(sw.group(1)) if sw else 0.3985,
                "fill":         fill.group(1)   if fill   else "none",
                "stroke":       stroke.group(1) if stroke else "#000",
            })

    return {
        "kind":    kind,
        "name":    name,
        "viewBox": viewbox,
        "width_pt":  w.group(1) if w else "",
        "height_pt": h.group(1) if h else "",
        "paths":   paths,
    }

for kind in ("nodes", "bipoles", "tripoles"):
    for svg_path in sorted(glob.glob(os.path.join(out_dir, kind, "*.svg"))):
        slug = os.path.splitext(os.path.basename(svg_path))[0]
        name = slug.replace("_", " ")
        manifest[slug] = parse_svg(svg_path, kind.rstrip("s"), name)

out_path = os.path.join(out_dir, "manifest.json")
with open(out_path, "w") as f:
    json.dump(manifest, f, indent=2)

print(f"  Wrote {len(manifest)} entries → {out_path}")
PYEOF

echo ""
echo "Done. SVGs in $OUT_DIR, manifest at $OUT_DIR/manifest.json"
echo ""
echo "To implement a component in Qt, look up its entry in manifest.json,"
echo "read the 'paths' array, and translate each 'd' string to QPainterPath calls:"
echo "  M x y  → path.moveTo(x, y)"
echo "  L x y  → path.lineTo(x, y)"
echo "  H x    → path.lineTo(x, path.currentPosition().y())"
echo "  V y    → path.lineTo(path.currentPosition().x(), y)"
echo "  Z      → path.closeSubpath()"
echo "  (scale from SVG pt units to GRID_PX as needed)"
