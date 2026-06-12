"""
Resolution of the external command-line tools the app shells out to.

The app needs up to five executables: ``pdflatex`` (preview + PDF/EPS/SVG export),
``latex`` + ``dvisvgm`` (on-canvas math labels, when not using the ziamath
fallback), and ``pdftocairo`` or ``inkscape`` (EPS/SVG export — Poppler is
preferred, Inkscape is the automatic fallback).  This module is the single
place that decides *which* binary to run for a given tool name.

Resolution order, per tool:
  1. an explicit **user-configured path** (set in Preferences → Tools), when it is
     set and points at a runnable file;
  2. otherwise a lookup on ``PATH`` (after augmenting it with common install
     directories a Finder/Dock-launched GUI would not inherit);
  3. otherwise any well-known install locations for that tool
     (:data:`_EXTRA_TOOL_CANDIDATES` — e.g. Inkscape's .app/Program Files
     binaries, which its installers do not put on PATH).

This module has **no Qt dependency** so it stays usable headlessly (tests, the
codegen pipeline).  The UI layer reads the persisted paths from ``Preferences``
and pushes them in via :func:`set_tool_paths` (mirroring ``mathrender``'s
``set_force_ziamath``), so the preview/codegen modules never touch ``QSettings``.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

#: Tools the app may invoke. The configured-path keys mirror these names.
TOOLS: tuple[str, ...] = ("pdflatex", "latex", "dvisvgm", "pdftocairo", "inkscape")

#: Well-known install locations checked when a tool is neither overridden nor
#: on PATH. Inkscape's installers do not put the CLI binary on PATH on macOS
#: (it lives inside the .app bundle) or Windows (Program Files); without these
#: candidates "install Inkscape" would not be enough for most users. On
#: Windows ``inkscape.com`` is the console binary (proper stdio/exit codes);
#: ``inkscape.exe`` is the GUI one, kept as a fallback.
_EXTRA_TOOL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "inkscape": (
        "/Applications/Inkscape.app/Contents/MacOS/inkscape",
        r"C:\Program Files\Inkscape\bin\inkscape.com",
        r"C:\Program Files\Inkscape\bin\inkscape.exe",
    ),
}

# Directories where TeX and Poppler binaries commonly live on macOS but which a
# GUI app launched from Finder/Dock does NOT inherit: such an app gets only a
# minimal PATH (``/usr/bin:/bin:/usr/sbin:/sbin``), so the tools appear "missing"
# even when installed. We append these to PATH so the tools are found regardless
# of how the app was launched. Appending (not prepending) preserves any working
# PATH from a terminal launch.
_MAC_TOOL_DIRS = (
    "/Library/TeX/texbin",   # MacTeX / BasicTeX
    "/usr/local/bin",        # Intel Homebrew, MacPorts symlinks
    "/opt/homebrew/bin",     # Apple Silicon Homebrew
    "/opt/local/bin",        # MacPorts
)

#: Per-tool explicit path overrides (set from Preferences). Empty/missing means
#: "fall back to PATH". Only paths that point at a runnable file are honoured.
_overrides: dict[str, str] = {}


def ensure_tool_dirs_on_path() -> None:
    """Append common macOS TeX/Poppler bin dirs to PATH if absent.

    Idempotent; only adds directories that exist and are not already on PATH.
    No-op off macOS. Called before any ``shutil.which`` use so discovery behaves
    the same whether the app was launched from a terminal or from Finder/Dock.
    """
    if platform.system() != "Darwin":
        return
    parts = os.environ.get("PATH", "").split(os.pathsep)
    extra = [d for d in _MAC_TOOL_DIRS if os.path.isdir(d) and d not in parts]
    if extra:
        os.environ["PATH"] = os.pathsep.join([p for p in parts if p] + extra)


def is_runnable(path: str) -> bool:
    """True when *path* names an existing, executable file."""
    return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)


def path_on_path(name: str) -> str | None:
    """Where tool *name* resolves on the (augmented) ``PATH``, ignoring overrides.

    Used by the Preferences UI to show where a tool *would* be found when no
    explicit path is configured."""
    ensure_tool_dirs_on_path()
    return shutil.which(name)


def set_tool_paths(mapping: dict[str, str]) -> None:
    """Replace the explicit per-tool path overrides from *mapping*.

    Keys are tool names (see :data:`TOOLS`); a blank/whitespace value clears that
    tool's override (back to PATH discovery). Unknown keys are ignored. Stored
    verbatim — validity is checked lazily in :func:`resolve`, so a path that is
    not yet runnable simply falls back to PATH rather than erroring here.
    """
    for name in TOOLS:
        value = (mapping.get(name) or "").strip()
        if value:
            _overrides[name] = value
        else:
            _overrides.pop(name, None)


def set_tool_path(name: str, path: str) -> None:
    """Set (or clear, when *path* is blank) the explicit override for one tool."""
    set_tool_paths({**{n: _overrides.get(n, "") for n in TOOLS}, name: path})


def resolve(name: str) -> str | None:
    """Return the executable to run for tool *name*, or ``None`` if not found.

    A configured override wins when it points at a runnable file; otherwise the
    tool is looked up on the (augmented) ``PATH``; finally any well-known
    install locations (:data:`_EXTRA_TOOL_CANDIDATES`) are checked.
    """
    override = _overrides.get(name)
    if override and is_runnable(override):
        return override
    found = path_on_path(name)
    if found is not None:
        return found
    for candidate in _EXTRA_TOOL_CANDIDATES.get(name, ()):
        if is_runnable(candidate):
            return candidate
    return None


def available(name: str) -> bool:
    """True when tool *name* resolves to a runnable executable."""
    return resolve(name) is not None


def run_kwargs() -> dict[str, int]:
    """Extra ``subprocess.run`` keyword arguments for launching a tool.

    On Windows, a windowed (no-console) app that spawns a console child lets
    the child allocate its own console — a console window flashes over the app
    on every pdflatex/latex/dvisvgm/converter run. ``CREATE_NO_WINDOW``
    suppresses that. Empty on other platforms. (The numeric fallback keeps the
    Windows branch exercisable in tests on POSIX, where ``subprocess`` does not
    define the constant.)
    """
    if platform.system() != "Windows":
        return {}
    return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
