#!/usr/bin/env bash
# Launch Heaviside on the expanded, manual-scraped component library
# (HEAVISIDE_COMPONENT_LIB=manual) in one step, instead of typing the env var or
# the --manual flag each time. The curated library remains the default for plain
# `python main.py`, the tests, and the bundled examples.
#
# Note: the manual library does not yet cover every curated kind (drawing
# primitives, some basics), so the bundled examples won't open under it.
set -euo pipefail
cd "$(dirname "$0")"

PY="python"
[ -x ".venv/bin/python" ] && PY=".venv/bin/python"

exec "$PY" main.py --manual "$@"
