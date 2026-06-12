#!/usr/bin/env python3
"""Render the README example-gallery screenshots: the full editor GUI.

Each shot launches the real ``MainWindow`` under Qt's offscreen platform —
component palette, schematic canvas, inspector, and the live CircuiTikZ
source/PDF preview — loads a bundled example, applies the light or dark theme,
waits for the async work to finish (math labels; the PDF preview when a LaTeX
toolchain is available), fits the canvas to the schematic, and grabs the whole
window to a PNG in ``docs/images/examples/``. The release workflow re-runs
this and commits the result to ``main`` whenever the rendered pixels change,
so the README gallery always matches the released editor.

Importing this module has no side effects (tests read the ``SHOTS`` manifest);
``main()`` bootstraps the run: QSettings are redirected to a throwaway
directory so the script neither reads nor pollutes the user's real
preferences, the startup update check is disabled, and the missing-dependency
warning is suppressed — both are modal dialogs that would block an offscreen
run.

Usage:
    uv run python scripts/render_screenshots.py              # writes all four
    uv run python scripts/render_screenshots.py --out DIR    # custom output dir
    uv run python scripts/render_screenshots.py --only NAME  # one manifest entry
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# The gallery: (example path, dark mode, output file name). At least one light
# and one dark so the README shows both palettes. Referenced by README.md —
# keep the file names in sync with the screenshot table there.
SHOTS: list[tuple[str, bool, str]] = [
    ("examples/Power Electronics/Boost Converter.hv", False, "boost-converter-light.png"),
    ("examples/Logic Circuits/4-1 MUX.hv", True, "mux-4-1-dark.png"),
    ("examples/Battery Models/Porous Electrode Interface.hv", True, "porous-electrode-dark.png"),
]

DEFAULT_OUT = _ROOT / "docs" / "images" / "examples"
WINDOW_SIZE = (1600, 1000)
SETTLE_TIMEOUT_S = 120.0


def _bootstrap() -> None:
    """Prepare an isolated, dialog-free app environment for window grabs."""
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    # Redirect settings BEFORE anything constructs a Preferences/QSettings:
    # the script must not read the developer's real prefs (theme, tool
    # overrides) nor persist the theme switching it does.
    settings_dir = tempfile.mkdtemp(prefix="heaviside-shot-settings-")
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, settings_dir)

    if QApplication.instance() is None:
        QApplication(sys.argv)

    # Both of these are modal dialogs that would block an offscreen run: the
    # one-time update-check disclosure, and the missing-dependency warning
    # (the CI runner deliberately installs only the preview toolchain).
    from app.ui import mainwindow as mw
    from app.ui.preferences import Preferences

    Preferences().check_updates_on_startup = False
    mw.check_dependencies = lambda: []


def _labels_idle() -> bool:
    """True when no async math-label render is queued or in flight."""
    from app.preview.mathrender import _dispatcher, _pool

    return not _dispatcher()._callbacks and _pool().activeThreadCount() == 0


def _settle(app, preview_state: dict) -> None:  # noqa: ANN001
    """Pump the event loop until the window's async work has finished.

    Waits for the math-label pipeline to drain and — when a LaTeX toolchain is
    present — for the live PDF preview to report ready (or error). Requires
    two consecutive idle checks so a cascading render can't slip through.
    """
    from app.preview import tools

    wait_preview = tools.resolve("pdflatex") is not None
    deadline = time.monotonic() + SETTLE_TIMEOUT_S
    stable = 0
    while time.monotonic() < deadline:
        app.processEvents()
        idle = _labels_idle() and (preview_state["done"] or not wait_preview)
        stable = stable + 1 if idle else 0
        if stable >= 2:
            return
        time.sleep(0.01)
    raise TimeoutError("window did not settle (labels/preview) in time")


def render_window_shot(hv_path: Path, *, dark: bool, out_path: Path) -> None:
    """Capture the full editor window showing *hv_path* to *out_path*.

    Caller must have run :func:`_bootstrap` (or provided an equivalent
    isolated QApplication environment).
    """
    from PySide6.QtWidgets import QApplication

    from app.ui.mainwindow import MainWindow

    app = QApplication.instance()
    if app is None:
        raise RuntimeError("call _bootstrap() first")

    window = MainWindow()
    try:
        # Track the live-preview compile triggered by the load below.
        preview_state = {"done": False}
        window._preview_worker.preview_ready.connect(
            lambda *_: preview_state.__setitem__("done", True))
        window._preview_worker.preview_error.connect(
            lambda *_: preview_state.__setitem__("done", True))

        window._set_theme_mode("dark" if dark else "light")
        window.resize(*WINDOW_SIZE)
        window.show()
        app.processEvents()

        if not window.load_path(hv_path):
            raise RuntimeError(f"failed to load {hv_path}")
        _settle(app, preview_state)

        window._view.fit_to_schematic()
        app.processEvents()

        pixmap = window.grab()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if not pixmap.save(str(out_path), "PNG"):
            raise OSError(f"failed to write {out_path}")
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


def main(argv: list[str]) -> int:
    out_dir = DEFAULT_OUT
    if "--out" in argv:
        out_dir = Path(argv[argv.index("--out") + 1])
    only = argv[argv.index("--only") + 1] if "--only" in argv else None
    shots = [s for s in SHOTS if only is None or s[2] == only]
    if not shots:
        print(f"no manifest entry named {only!r}", file=sys.stderr)
        return 2

    _bootstrap()
    for rel, dark, name in shots:
        render_window_shot(_ROOT / rel, dark=dark, out_path=out_dir / name)
        print(f"rendered {name}  ({'dark' if dark else 'light'})  <- {rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
