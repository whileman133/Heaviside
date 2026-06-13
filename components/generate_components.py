#!/usr/bin/env python3
r"""
Batch component renderer (CLI).

Renders every CircuiTikZ symbol and writes ``components/geometry.json``
(geometry) and ``components/definitions.json`` (registry/codegen data +
``origin_svg`` + the ``circuitikz_version`` generation stamp).  The render/save
logic lives in ``app/components/generate.py`` (one renderer, shared with the
incremental authoring scripts in this directory).

Adding a component: add an entry to ``components/definitions.json`` (measure its
pin anchors with ``app/components/render.py``) and re-run this tool.  Requires
``latex`` + ``dvisvgm`` (+ Ghostscript via ``LIBGS`` for filled-path symbols).

    python components/generate_components.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# components/generate_components.py → repo root is two levels up.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.components import generate  # noqa: E402


def main() -> int:
    authored = generate.load_authored()
    # Pre-flight: fail fast with readable messages before any rendering.
    problems = {k: errs for k, e in authored.items()
                if (errs := generate.validate_entry(k, e))}
    if problems:
        for kind, errs in sorted(problems.items()):
            for e in errs:
                print(f"INVALID {kind}: {e}", file=sys.stderr)
        return 1
    version = generate.measure_circuitikz_version()
    geometry, components, origin = generate.render_store(authored)
    generate.write_store(geometry, components, origin, circuitikz_version=version)
    print(f"origin_svg = {origin}")
    print(f"circuitikz version = {version or 'unknown'}")
    print(f"Wrote {len(components)} components, {len(geometry)} geometry entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
