"""
Tests for app/canvas/svgsym.py — symbol geometry, including glyph (+/-) marks.

Symbols whose CircuiTikZ output contains text marks (the +/- of a voltage or
controlled source, op-amp labels, etc.) record those as opaque <use> glyph
references in the geometry. svgsym reconstructs them by reading the original
.svg file. These tests guard that reconstruction so the marks don't silently
disappear (a regression that also surfaced as a packaging bug when the .svg
files were not bundled).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtGui", reason="PySide6 not importable")

from PySide6.QtGui import QGuiApplication  # noqa: E402

try:
    _APP = QGuiApplication.instance() or QGuiApplication([])
except Exception as exc:  # pragma: no cover - environment-dependent
    pytest.skip(f"Qt platform unavailable: {exc}", allow_module_level=True)

import json  # noqa: E402

from app.canvas.style import GEOMETRY_PATH  # noqa: E402
from app.canvas.svgsym import geometry_key, symbol_paths  # noqa: E402


def test_every_library_kind_resolves_to_geometry() -> None:
    """Every SVG-symbol registry kind maps to a present, non-empty geometry entry.

    Regression: a kind whose CircuiTikZ keyword contains a space (``flipflop D``,
    ``op amp``) must have its space sanitised to an underscore so the geometry
    lookup hits. A mismatch silently paints *nothing* (only pin dots), since the
    canvas item still builds from the registry bbox — so guard it here, across the
    whole library, rather than per kind.
    """
    from app.components.library import NON_LIBRARY_KINDS, load_library

    with open(GEOMETRY_PATH, encoding="utf-8") as fh:
        geometry = json.load(fh)
    for kind in load_library():
        if kind in NON_LIBRARY_KINDS:
            continue
        key = geometry_key(kind)
        assert key in geometry, f"{kind!r}: geometry key {key!r} not in geometry.json"
        assert symbol_paths(kind), f"{kind!r}: resolved to no symbol paths"


def test_geometry_key_reexports_library_definition() -> None:
    """svgsym's geometry_key is the canonical library function, not a copy."""
    from app.components import library

    assert geometry_key is library.geometry_key


def _expected_geometry_keys() -> list[tuple[str, str]]:
    """Every geometry key any canvas instance can request: ``(kind, key)``.

    Mirrors how the canvas builds its lookup (items.py: kind + param suffix +
    variant suffix, then geometry_key): the base symbol, every non-``dot``
    variant (``D`` -> ``D*``, ``nigfete`` -> ``nigfete_bodydiode``), and every
    supported parameter value/combo of every parametric kind (``and:2``…
    ``and:16``, ``mux:2:1``…``mux:8:3``).
    """
    from itertools import product

    from app.components.library import geometry_key as gk, load_library

    expected: list[tuple[str, str]] = []
    for kind, entry in load_library().items():
        expected.append((kind, gk(kind)))
        # Variants: suffix mode appends the token; option mode appends _token;
        # dot variants overlay marks on the base geometry (no key of their own).
        # Tokens can contain spaces (``schottky base``); the geometry is keyed by
        # the *sanitised* form, exactly as the runtime looks it up — it builds
        # ``kind + suffix`` and passes the whole thing through geometry_key
        # (items.py ``_geometry_kind`` -> svgsym ``symbol_paths``). Mirror that by
        # sanitising the assembled key here.
        for v in entry.get("variants", []):
            if v.get("mode") == "dot":
                continue
            if v["mode"] == "suffix":
                expected.append((kind, gk(gk(kind) + v["token"])))
            else:
                expected.append((kind, gk(gk(kind) + "_" + v["token"])))
        # Single-parameter kinds (logic gates, DIP/QFP chips): kind:N for every
        # *declared* value. ``step`` defaults to 1, but stepped kinds skip values
        # (a DIP has even pin counts only, a QFP multiples of four), so honour it
        # — the same clamp the runtime applies before looking geometry up.
        p = entry.get("param")
        if p:
            for n in range(int(p["min"]), int(p["max"]) + 1, int(p.get("step", 1))):
                expected.append((kind, f"{gk(kind)}:{n}"))
        # Multi-parameter kinds (mux/demux, multi-collector BJTs): kind:v1:v2 for
        # every value combo, in declaration order, honouring each spec's step.
        specs = entry.get("params")
        if specs:
            ranges = [range(int(s["min"]), int(s["max"]) + 1, int(s.get("step", 1)))
                      for s in specs]
            for combo in product(*ranges):
                expected.append(
                    (kind, gk(kind) + "".join(f":{v}" for v in combo))
                )
    return expected


def test_every_variant_and_param_combo_has_geometry() -> None:
    """Every variant key and every supported parameter value/combo of every
    library kind resolves to a present, non-empty geometry entry.

    The base-kind sweep above misses these: a missing variant key (``D*``) or
    param key (``and:7``, ``mux:4:2``) silently paints nothing when that variant/
    value is selected, and the byte-equality re-render test is slow and
    toolchain-gated. This fast sweep enumerates them all from the library data.
    """
    with open(GEOMETRY_PATH, encoding="utf-8") as fh:
        geometry = json.load(fh)
    for kind, key in _expected_geometry_keys():
        assert key in geometry, f"{kind!r}: geometry key {key!r} not in geometry.json"
        entry = geometry[key]
        assert entry.get("paths"), f"{kind!r}: geometry {key!r} has no paths"


def test_parametric_n_data_covers_every_combo() -> None:
    """Every parametric kind's ``n_data`` carries a record for every declared
    value/combo (the codegen and canvas read scale/leads/pins from it)."""
    from itertools import product

    from app.components.library import load_library

    for kind, entry in load_library().items():
        # The declared values are ``min..max`` on the spec's ``step`` grid (step
        # defaults to 1). A DIP chip steps by 2 (even pin counts), a QFP by 4, so
        # ``n_data`` is keyed by those stepped values only — derive ``want`` from
        # the same declared step the generator baked from, not a contiguous range.
        p = entry.get("param")
        if p:
            want = {str(n) for n in
                    range(int(p["min"]), int(p["max"]) + 1, int(p.get("step", 1)))}
            assert set(p.get("n_data", {})) == want, kind
        specs = entry.get("params")
        if specs:
            ranges = [range(int(s["min"]), int(s["max"]) + 1, int(s.get("step", 1)))
                      for s in specs]
            want = {",".join(str(v) for v in combo) for combo in product(*ranges)}
            assert set(entry.get("n_data", {})) == want, kind


def test_geometry_is_self_contained_for_glyph_kind() -> None:
    """The controlled-source's +/- marks are baked into the geometry (`glyphs`),
    so the app needs no .svg access at run time."""
    with open(GEOMETRY_PATH, encoding="utf-8") as fh:
        geometry = json.load(fh)
    entry = geometry[geometry_key("american controlled voltage source")]
    assert entry["glyphs"], "controlled voltage source must carry baked glyph marks"
    g = entry["glyphs"][0]
    assert g["d"].lstrip()[:1] in "Mm"          # real path geometry, not a placeholder
    assert len(g["matrix"]) == 6                 # baked affine transform


def test_cV_paths_all_real_geometry() -> None:
    """Every path returned for the controlled voltage source has real geometry —
    no opaque glyph-ref leaks.

    The +/- glyph marks are resolved into concrete filled paths; if svgsym let
    one through unresolved it would be an empty/degenerate path. All returned
    paths must carry actual elements.
    """
    paths = symbol_paths("american controlled voltage source")
    assert len(paths) >= 4   # diamond + strokes + the two resolved glyph marks
    for sp in paths:
        assert sp.path.elementCount() > 0


def test_cV_has_more_paths_than_plain_diamond() -> None:
    """The controlled voltage source resolves its glyph marks: it has strictly
    more paths than the bare geometry would (the body + its connecting strokes
    alone)."""
    cv = symbol_paths("american controlled voltage source")
    # A plain resistor carries no glyph marks — sanity that glyph kinds add paths.
    assert len(cv) > len(symbol_paths("R"))


def test_plain_resistor_unaffected() -> None:
    """A glyph-free symbol still renders its strokes."""
    r = symbol_paths("R")
    assert len(r) >= 1
    assert all(sp.path.elementCount() > 0 for sp in r)


def test_filled_diode_body_is_filled() -> None:
    """A filled diode (`full diode`) has a filled body path; the outline diode
    (`empty diode`) does not — so the two render visibly differently (regression).

    The manual library encodes fill as distinct kinds (`full diode` with a filled
    body vs `empty diode`, outline only) rather than as a suffix variant. The fill
    is a bare dvisvgm `<path>` (SVG default black fill); recording it as
    `fill='none'` previously made the two render identically.
    """
    assert not any(sp.filled for sp in symbol_paths("empty diode")), \
        "'empty diode' must be unfilled"
    assert any(sp.filled for sp in symbol_paths("full diode")), \
        "'full diode' must have a filled body"


def test_stroke_only_symbols_not_filled() -> None:
    """Pure outline symbols (inductor, capacitor, resistor) have no filled paths
    — a guard that the 'bare path = fill' rule does not over-fill stroked bodies."""
    for kind in ("L", "C", "R"):
        assert not any(sp.filled for sp in symbol_paths(kind)), kind


def test_symbol_path_carries_clip(monkeypatch) -> None:
    """A geometry path with a ``clip`` ``d`` (the RF antenna wedge that turns full
    wavefront circles into arcs) is parsed into a clip ``QPainterPath`` on its
    ``SymbolPath`` so the canvas can clip when painting."""
    import app.canvas.svgsym as S

    fake = {"R": {"paths": [
        {"d": "M0 0L1 0L1 1Z", "stroke_width": 0.4, "fill": "none",
         "clip": "M0 0L2 0L2 2Z"},
        {"d": "M0 0L1 1", "stroke_width": 0.4, "fill": "none"},
    ], "glyphs": []}}
    monkeypatch.setattr(S, "_geometry", lambda: fake)
    S.symbol_paths.cache_clear()
    try:
        paths = S.symbol_paths("R")
        assert paths[0].clip is not None and paths[0].clip.elementCount() > 0
        assert paths[1].clip is None
    finally:
        S.symbol_paths.cache_clear()
