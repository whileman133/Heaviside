"""
Bundled example schematics must always load in the current build.

The `.hv` files under ``examples/`` ship inside the app (File → Open Example).
A format-version bump that forgets to update them would orphan the examples —
they'd be shipped with the app yet rejected by its own loader. This test guards
against that: every example must load (and re-generate) under the current
loader, and must declare the current file-format version.

No Qt or LaTeX required — this is pure model I/O.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.codegen.circuitikz import generate
from app.schematic.io import _FORMAT_VERSION, load

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
# Recurse: examples are grouped into category sub-folders (File → Open Example).
_EXAMPLE_FILES = sorted(_EXAMPLES_DIR.rglob("*.hv"))


def test_examples_directory_is_not_empty() -> None:
    """There is at least one bundled example to load."""
    assert _EXAMPLE_FILES, f"no .hv examples found in {_EXAMPLES_DIR}"


@pytest.mark.parametrize(
    "path", _EXAMPLE_FILES,
    ids=[str(p.relative_to(_EXAMPLES_DIR)) for p in _EXAMPLE_FILES],
)
def test_example_loads_and_generates(path: Path) -> None:
    """Every bundled example loads under the current loader, declares the current
    format version, and generates CircuiTikZ without error."""
    schematic = load(path)                      # raises SchematicLoadError if rejected
    assert schematic.version == _FORMAT_VERSION, (
        f"{path.name} declares version {schematic.version!r}; the current format "
        f"is {_FORMAT_VERSION!r}. Re-save the example so it stays loadable."
    )
    # A loadable example should also generate valid output end to end.
    src = generate(schematic, y_flip=True)
    assert src.startswith(r"\begin{circuitikz}")
    assert src.rstrip().endswith(r"\end{circuitikz}")
