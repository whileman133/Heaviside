"""
Single runtime source of the application version.

The canonical version lives in ``pyproject.toml`` (``[project] version``). This
module surfaces it to the running app so nothing hardcodes a version string:

* Normally the installed package metadata carries it (``importlib.metadata``),
  which is populated from ``pyproject.toml`` at install/build time.
* When running from a source checkout that was never installed, fall back to
  reading ``pyproject.toml`` directly.

Exposed as ``__version__``.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _pkg_version


def _read_version() -> str:
    try:
        return _pkg_version("heaviside")
    except PackageNotFoundError:
        pass

    # Fallback: read pyproject.toml from the resource root. resource_path()
    # resolves to the repo root from a source checkout and to sys._MEIPASS in
    # a frozen build (heaviside.spec bundles pyproject.toml for exactly this) —
    # a plain __file__-relative path finds nothing inside a PyInstaller bundle,
    # which is how frozen builds shipped reporting version 0.0.0.
    from app.resources import resource_path

    pyproject = resource_path("pyproject.toml")
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return "0.0.0"

    try:
        import tomllib  # Python 3.11+
        return tomllib.loads(text)["project"]["version"]
    except Exception:
        import re
        m = re.search(
            r'^\s*version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE
        )
        return m.group(1) if m else "0.0.0"


__version__ = _read_version()
