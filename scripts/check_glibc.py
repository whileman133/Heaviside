"""Fail if any binary in a Linux build needs a newer glibc than we support.

The Linux bundles are built on the oldest supported Ubuntu LTS runner so they
run on older distributions. A binary that requires a newer glibc symbol (from a
runner image change, or a dependency built against a newer system) would crash
at start on those systems, so the release workflow runs this over the PyInstaller
output and fails the build instead.

usage: python scripts/check_glibc.py DIR [--max 2.35]

Uses ``readelf --version-info`` (binutils, present on the Ubuntu runners) and
reads only the *version needs* section, so the result is what the dynamic loader
will actually demand, not every ``GLIBC_`` string a file happens to contain.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

#: The glibc of the oldest still-supported Ubuntu LTS (22.04), which the Linux
#: release builds run on. Debian 12 (2.36) and newer are above it.
DEFAULT_MAX = "2.35"

_NEED_RE = re.compile(r"Name:\s*GLIBC_(\d+(?:\.\d+)+)\b")


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(p) for p in text.split("."))


def required_glibc(readelf_output: str) -> list[tuple[int, ...]]:
    """glibc versions required in ``readelf --version-info`` output.

    Only the ``.gnu.version_r`` (version needs) section is read: a bundled copy
    of a library that *defines* ``GLIBC_*`` versions (in ``.gnu.version_d``)
    does not make the bundle require them."""
    found: list[tuple[int, ...]] = []
    in_needs = False
    for line in readelf_output.splitlines():
        if line.startswith("Version needs section"):
            in_needs = True
            continue
        if line.startswith("Version definition section") or line.startswith("Version symbols section"):
            in_needs = False
            continue
        if in_needs:
            m = _NEED_RE.search(line)
            if m:
                found.append(parse_version(m.group(1)))
    return found


def _is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(4) == b"\x7fELF"
    except OSError:
        return False


def scan(root: Path) -> dict[Path, tuple[int, ...]]:
    """Highest required glibc per ELF file under *root* (files needing none omitted)."""
    out: dict[Path, tuple[int, ...]] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        if not _is_elf(path):
            continue
        res = subprocess.run(["readelf", "--version-info", "--wide", str(path)],
                             capture_output=True, text=True)
        versions = required_glibc(res.stdout)
        if versions:
            out[path] = max(versions)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("root", type=Path)
    ap.add_argument("--max", default=DEFAULT_MAX, help=f"highest allowed glibc (default {DEFAULT_MAX})")
    args = ap.parse_args(argv)
    ceiling = parse_version(args.max)
    found = scan(args.root)
    if not found:
        print(f"::error::no ELF files needing glibc found under {args.root}")
        return 1
    fmt = lambda v: ".".join(map(str, v))  # noqa: E731
    overall = max(found.values())
    print(f"{len(found)} ELF files; highest glibc required: {fmt(overall)} (allowed: {args.max})")
    offenders = {p: v for p, v in found.items() if v > ceiling}
    for p, v in sorted(offenders.items(), key=lambda kv: kv[1], reverse=True):
        print(f"::error file={p}::needs glibc {fmt(v)} > {args.max}")
    return 1 if offenders else 0


if __name__ == "__main__":
    sys.exit(main())
