"""
Runtime resource resolution (works from source *and* when frozen).

The app reads two bundled data files at runtime — the application icon
(``assets/icon.png``) and the CircuiTikZ SVG geometry
(``components/geometry.json``).  When running from a source checkout
these sit under the project root.  When packaged with PyInstaller they are
unpacked under ``sys._MEIPASS`` (PyInstaller sets this in both one-file and
one-directory modes), so a plain ``__file__``-relative path no longer resolves.

:func:`resource_path` returns the correct absolute path in either case.  Callers
pass parts relative to the project root, matching the layout declared in
``heaviside.spec``'s ``datas`` list.
"""

from __future__ import annotations

import os
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


# Active component library selector. The curated, hand-tuned library lives in
# ``components/`` and is the default/fallback; an expanded library generated from
# the CircuiTikZ manual (``components/generate_library.py``) lives side by side in
# ``components/generated/`` and is switched in with HEAVISIDE_COMPONENT_LIB=manual.
# Keeping both on disk means the curated set is always the safe fallback.
COMPONENT_LIB_ENV = "HEAVISIDE_COMPONENT_LIB"


def component_lib_dir() -> Path:
    """Directory of the active component library (its ``definitions.json`` +
    ``geometry.json``). ``curated`` (default) → ``components/``; ``manual`` →
    ``components/generated/``."""
    choice = os.environ.get(COMPONENT_LIB_ENV, "curated").strip().lower()
    if choice == "manual":
        return resource_path("components", "generated")
    return resource_path("components")


def component_lib() -> str:
    """Name of the active component library: ``"manual"`` when
    ``HEAVISIDE_COMPONENT_LIB=manual``, else ``"curated"`` (the default)."""
    return ("manual"
            if os.environ.get(COMPONENT_LIB_ENV, "curated").strip().lower() == "manual"
            else "curated")


def component_data_path(name: str) -> Path:
    """Path to a data file (``definitions.json`` / ``geometry.json``) in the active
    component library."""
    return component_lib_dir() / name
