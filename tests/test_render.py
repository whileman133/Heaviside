"""
Tests for the measurement tool (``app/components/render.py``, spec §3).

Gated on ``latex`` + ``dvisvgm`` (a developer-tool dependency).  These prove the
automated anchor measurement reproduces the values PROJECT_SPEC §5.5 obtained by
hand — i.e. the magic numbers can be measured instead of stored.
"""

from __future__ import annotations

import shutil

import pytest

from app.components import render

pytestmark = pytest.mark.skipif(
    not (shutil.which("latex") and shutil.which("dvisvgm")),
    reason="latex/dvisvgm not installed",
)

_TOL = 0.02  # GU


def _close(a, b, tol=_TOL):
    return abs(a[0] - b[0]) < tol and abs(a[1] - b[1]) < tol


def test_op_amp_anchors_measured():
    a = render.measure_anchors("op amp", ["+", "-", "out"])
    # Codegen docstring (Qt y-down): + (-1.194,+0.492), - (-1.194,-0.492), out (1.194,0)
    assert _close(a["+"], (-1.19, 0.49))
    assert _close(a["-"], (-1.19, -0.49))
    assert _close(a["out"], (1.19, 0.0))


def test_nigfete_anchors_measured():
    a = render.measure_anchors("nigfete", ["gate", "drain", "source"])
    assert _close(a["gate"], (-0.98, 0.27))
    assert _close(a["drain"], (0.0, -0.77))
    assert _close(a["source"], (0.0, 0.77))


def test_npn_anchors_measured():
    a = render.measure_anchors("npn", ["B", "C", "E"])
    assert _close(a["B"], (-0.84, 0.0))
    assert _close(a["C"], (0.0, -0.77))
    assert _close(a["E"], (0.0, 0.77))


def test_discover_terminals_finds_distinct_terminals():
    # pgf resolves unknown anchors to the centre, so discovery must (a) drop the
    # fallback, (b) dedupe aliases by position, (c) name by candidate order.
    t = render.discover_terminals(
        "npn", ["base", "collector", "emitter", "B", "C", "E", "center", "bogus"]
    )
    assert set(t) == {"base", "collector", "emitter"}      # 3 distinct, friendly-named
    assert _close(t["base"], (-0.84, 0.0))
    assert _close(t["collector"], (0.0, -0.77))
    assert _close(t["emitter"], (0.0, 0.77))


def test_geometry_parsed():
    svg, _ = render.render_svg(r"\draw (0,0) to[R] (2,0);", border_pt=2)
    geo = render.parse_geometry(svg)
    assert geo["paths"], "resistor should have body paths"
    assert geo["viewBox"]


def test_parse_geometry_captures_rect_as_glyph():
    """A dvisvgm TeX rule (``<rect>``) — e.g. the overline of ``\\ctikztextnot{Q}``,
    the flip-flop's Q̄ — is captured as a filled glyph (a closed rectangle path +
    its transform) rather than silently dropped. Pure parse, no toolchain."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'>"
        "<g transform='matrix(2 0 0 2 1 1)'>"
        "<rect x='1' y='2' width='4' height='0.4'/>"
        "</g></svg>"
    )
    geo = render.parse_geometry(svg)
    assert len(geo["glyphs"]) == 1, "the rect rule must be captured as a glyph"
    g = geo["glyphs"][0]
    assert g["matrix"] == [2.0, 0.0, 0.0, 2.0, 1.0, 1.0]
    assert g["d"].startswith("M") and g["d"].endswith("Z")   # a closed rectangle
    assert g["d"].count("L") == 3                            # 4 corners


def test_parse_geometry_captures_clip_path():
    """A path clipped via ``clip-path='url(#id)'`` (dvisvgm clips e.g. the RF
    antenna's full-circle wavefronts to a wedge so only the arcs show) carries the
    clip region's ``d`` so the canvas can reproduce it. Pure parse, no toolchain."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'>"
        "<defs><clipPath id='clip1'><path d='M0 0L5 0L5 5Z'/></clipPath></defs>"
        "<g>"
        "<path d='M1 1L2 2' stroke='#000' fill='none' clip-path='url(#clip1)'/>"
        "<path d='M3 3L4 4' stroke='#000' fill='none'/>"
        "</g></svg>"
    )
    geo = render.parse_geometry(svg)
    assert geo["paths"][0]["clip"] == "M0 0L5 0L5 5Z"   # clipped path carries the wedge
    assert "clip" not in geo["paths"][1]                # unclipped path has none


def test_parse_geometry_clip_inherited_from_group():
    """A ``clip-path`` set on an ancestor ``<g>`` applies to its child paths."""
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'>"
        "<defs><clipPath id='c'><path d='M0 0L9 0L9 9Z'/></clipPath></defs>"
        "<g clip-path='url(#c)'><path d='M1 1L2 2' stroke='#000' fill='none'/></g>"
        "</svg>"
    )
    geo = render.parse_geometry(svg)
    assert geo["paths"][0]["clip"] == "M0 0L9 0L9 9Z"


def test_render_is_deterministic():
    svg1, _ = render.render_svg(r"\draw (0,0) to[R] (2,0);", border_pt=2)
    svg2, _ = render.render_svg(r"\draw (0,0) to[R] (2,0);", border_pt=2)
    assert svg1 == svg2


def test_diode_filled_geometry_differs():
    # The renderer handles the filled (*) variant body (needs Ghostscript via LIBGS).
    plain, _ = render.render_svg(r"\ctikzset{diodes/scale=0.8}\draw (0,0) to[D] (2,0);", border_pt=2)
    filled, _ = render.render_svg(r"\ctikzset{diodes/scale=0.8}\draw (0,0) to[D*] (2,0);", border_pt=2)
    assert render.parse_geometry(plain)["paths"] != render.parse_geometry(filled)["paths"]
