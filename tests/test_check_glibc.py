"""The release workflow's glibc ceiling check (scripts/check_glibc.py).

Only the readelf-output parser is tested here (the scan itself needs Linux and
binutils and runs in the release workflow)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "check_glibc", Path(__file__).resolve().parent.parent / "scripts" / "check_glibc.py")
check_glibc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_glibc)

# Trimmed `readelf --version-info --wide` output for a library that both defines
# its own versions and needs some from glibc.
_SAMPLE = """
Version symbols section '.gnu.version' contains 3 entries:
 Addr: 0x0000000000000a10  Offset: 0x000a10  Link: 4 (.dynsym)
  000:   0 (*local*)       2 (GLIBC_2.99)    3 (GLIBC_2.17)

Version definition section '.gnu.version_d' contains 2 entries:
  000000: Rev: 1  Flags: BASE  Index: 1  Cnt: 1  Name: libpython3.12.so.1.0
  0x001c: Rev: 1  Flags: none  Index: 2  Cnt: 1  Name: GLIBC_2.99

Version needs section '.gnu.version_r' contains 2 entries:
 Addr: 0x0000000000000b00  Offset: 0x000b00  Link: 5 (.dynstr)
  000000: Version: 1  File: libm.so.6  Cnt: 2
  0x0010:   Name: GLIBC_2.29  Flags: none  Version: 4
  0x0020:   Name: GLIBC_2.2.5  Flags: none  Version: 3
  0x0030: Version: 1  File: libc.so.6  Cnt: 2
  0x0040:   Name: GLIBC_2.38  Flags: none  Version: 6
  0x0050:   Name: GLIBC_PRIVATE  Flags: none  Version: 7
"""


def test_reads_only_the_version_needs_section():
    """Defined versions (GLIBC_2.99 above) and the symbol table don't count;
    GLIBC_PRIVATE is ignored; three-part versions parse."""
    assert check_glibc.required_glibc(_SAMPLE) == [(2, 29), (2, 2, 5), (2, 38)]


def test_ceiling_comparison_is_numeric():
    v = check_glibc.parse_version
    assert v("2.38") > v("2.35")
    assert v("2.4") < v("2.35")          # not a string comparison
    assert v("2.2.5") < v("2.17")
    assert max(check_glibc.required_glibc(_SAMPLE)) > v(check_glibc.DEFAULT_MAX)


def test_no_needs_section_means_nothing_required():
    assert check_glibc.required_glibc("Version symbols section '.gnu.version'\n") == []
