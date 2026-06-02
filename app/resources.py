"""
Runtime resource resolution (works from source *and* when frozen).

The app reads two bundled data files at runtime — the application icon
(``assets/icon.png``) and the CircuiTikZ SVG manifest
(``tools/circuitikz_svgs/manifest.json``).  When running from a source checkout
these sit under the project root.  When packaged with PyInstaller they are
unpacked under ``sys._MEIPASS`` (PyInstaller sets this in both one-file and
one-directory modes), so a plain ``__file__``-relative path no longer resolves.

:func:`resource_path` returns the correct absolute path in either case.  Callers
pass parts relative to the project root, matching the layout declared in
``heaviside.spec``'s ``datas`` list.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _base_dir() -> Path:
    """Directory that bundled resources are rooted at.

    Frozen builds expose ``sys._MEIPASS``; from source we walk up from this
    module (``app/resources.py`` → ``app`` → project root).
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass is not None:
        return Path(meipass)
    return Path(__file__).resolve().parents[1]


def resource_path(*parts: str) -> Path:
    """Absolute path to a bundled resource given *parts* relative to the root.

    Example: ``resource_path("assets", "icon.png")``.
    """
    return _base_dir().joinpath(*parts)
