#!/usr/bin/env python3
r"""
Batch component renderer (CLI).

Renders every CircuiTikZ symbol and writes ``components/geometry.json``
(geometry) and ``components/definitions.json`` (registry/codegen data +
``origin_svg``).  The render/save logic lives in ``app/componenteditor/renderer.py``
(shared with the GUI's Save, so there is one renderer).

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

from app.componenteditor import renderer  # noqa: E402


def main() -> int:
    authored = renderer.load_authored()
    geometry, components, origin = renderer.render_store(authored)
    renderer.write_store(geometry, components, origin)
    print(f"origin_svg = {origin}")
    print(f"Wrote {len(components)} components, {len(geometry)} geometry entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
