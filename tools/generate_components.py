#!/usr/bin/env python3
r"""
Batch component renderer (CLI).

Renders every CircuiTikZ symbol and writes ``tools/circuitikz_svgs/manifest.json``
(geometry) and ``components/components.json`` (registry/codegen data +
``origin_svg``).  The render/save logic lives in ``app/componenteditor/baker.py``
(shared with the GUI's Save, so there is one renderer).

Adding a component: add an entry to ``components/components.json`` (measure its
pin anchors with ``app/components/bake.py``) and re-run this tool.  Requires
``latex`` + ``dvisvgm`` (+ Ghostscript via ``LIBGS`` for filled-path symbols).

    python tools/generate_components.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.componenteditor import baker  # noqa: E402


def main() -> int:
    authored = baker.load_authored()
    manifest, components, origin = baker.render_store(authored)
    baker.write_store(manifest, components, origin)
    print(f"origin_svg = {origin}")
    print(f"Wrote {len(components)} components, {len(manifest)} manifest entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
