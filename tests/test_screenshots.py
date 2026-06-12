"""
Tests for scripts/render_screenshots.py — the README example-gallery renderer.

The release workflow re-runs the script and commits the PNGs to main, so this
guards: the manifest matches the bundled examples and the README, both themes
are represented, and an actual run captures the full editor GUI (not a blank
or canvas-only frame). The GUI shot runs in a subprocess because the script
isolates QSettings globally — in-process that would leak into other tests.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtGui", reason="PySide6 not importable")

from PySide6.QtGui import QColor, QImage  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "render_screenshots.py"

# Importing the module is side-effect-free (no QApplication, no QSettings
# redirect) — that is itself part of the contract this file relies on.
_spec = importlib.util.spec_from_file_location("render_screenshots", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_manifest_examples_exist_and_cover_both_themes() -> None:
    """Every manifest entry points at a bundled example; both palettes appear."""
    assert len(_mod.SHOTS) == 3
    names = [name for _, _, name in _mod.SHOTS]
    assert len(set(names)) == len(names), "output names must be unique"
    for rel, _dark, _name in _mod.SHOTS:
        assert (_ROOT / rel).is_file(), f"missing example: {rel}"
    darks = [dark for _, dark, _ in _mod.SHOTS]
    assert any(darks) and not all(darks), "need at least one dark and one light"


def test_readme_gallery_references_every_screenshot() -> None:
    """The README screenshot table and the manifest cannot drift apart."""
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    for _rel, _dark, name in _mod.SHOTS:
        assert f"docs/images/examples/{name}" in readme, f"README missing {name}"


def test_window_shot_captures_full_dark_gui(tmp_path) -> None:
    """A real script run grabs the whole editor window in dark mode.

    Asserts the capture is window-sized (palette + canvas + inspector +
    preview pane, not a canvas-only crop), predominantly dark, and visually
    structured (panels and schematic ink, not a blank fill).
    """
    name = "mux-4-1-dark.png"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "--out", str(tmp_path), "--only", name],
        cwd=_ROOT, env=env, capture_output=True, text=True, timeout=300,
    )
    assert proc.returncode == 0, f"script failed:\n{proc.stdout}\n{proc.stderr}"

    img = QImage(str(tmp_path / name))
    assert not img.isNull()
    assert (img.width(), img.height()) == _mod.WINDOW_SIZE

    samples = [
        QColor(img.pixel(x, y)).lightness()
        for x in range(0, img.width(), 32)
        for y in range(0, img.height(), 32)
    ]
    mean = sum(samples) / len(samples)
    assert mean < 110, f"dark-mode GUI should be predominantly dark (mean {mean:.0f})"
    assert max(samples) - min(samples) > 60, "capture should show real UI structure"
