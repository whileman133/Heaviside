"""
Tests for app/version.py — runtime version resolution.

Regression context: frozen (PyInstaller) builds shipped reporting version
0.0.0 because neither the package dist-info nor pyproject.toml was bundled,
so the update notifier nagged every launch. The version must resolve from
source checkouts AND inside a bundle (via sys._MEIPASS), and heaviside.spec
must keep bundling both sources.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_version_resolves_from_source_checkout() -> None:
    from app.version import __version__

    assert __version__ != "0.0.0"
    pyproject = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject


def test_frozen_fallback_reads_bundled_pyproject(tmp_path, monkeypatch) -> None:
    """With no package metadata and a frozen layout (sys._MEIPASS), the version
    must come from the bundled pyproject.toml — not collapse to 0.0.0."""
    from app import version as version_mod

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "heaviside"\nversion = "9.9.9"\n', encoding="utf-8")

    def _no_metadata(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(version_mod, "_pkg_version", _no_metadata)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert version_mod._read_version() == "9.9.9"


def test_frozen_without_any_source_falls_back_to_zero(tmp_path, monkeypatch) -> None:
    """Total resolution failure yields the 0.0.0 sentinel (which suppresses the
    startup update nag) rather than raising."""
    from app import version as version_mod

    def _no_metadata(_name: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr(version_mod, "_pkg_version", _no_metadata)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)  # empty dir
    assert version_mod._read_version() == "0.0.0"


def test_spec_bundles_version_source() -> None:
    """heaviside.spec must bundle pyproject.toml — PyInstaller does not bundle the
    package's dist-info metadata, so importlib.metadata can't resolve the version
    inside the frozen app and the bundled file is the only version source it has."""
    spec = (_ROOT / "heaviside.spec").read_text(encoding="utf-8")
    assert '("pyproject.toml", ".")' in spec
