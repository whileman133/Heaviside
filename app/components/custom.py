"""
Custom-component capture pipeline (Qt-free).

A *custom component* is built by the user from a base built-in kind: they supply
scoped ``\\ctikzset`` customisations and extra node/path options, and Heaviside
renders that exact configuration through :mod:`app.components.render` to capture
its drawable geometry and re-measure its anchor positions. The result is a
:class:`~app.components.model.CustomComponentSpec` that is stored on the document
(so it travels with the ``.hv`` and re-renders without LaTeX) and registered at
runtime as an ordinary placeable kind (see :mod:`app.components.registry`).

This is the same render-and-measure pipeline the offline library generator
(``components/generate_library.py``) uses, run on a user-supplied configuration
instead of the manual scrape. Anchor positions come from the installed CircuiTikZ
— never invented.

Phase 1 customises an *existing* kind: the emitted output is still
``\\node[<base>, …]`` / ``to[<base>, …]`` with a scoped ``\\ctikzset``, so the
base's classification, ``tikz_keyword`` and pin→anchor map carry over unchanged
(codegen delegates via ``ComponentDef.base_kind``). No preamble macros are needed.
"""

from __future__ import annotations

from app.canvas.style import SVG_PT_PER_GU
from app.components import library, render
from app.components.model import Component, ComponentDef, CustomComponentSpec, PinDef

# These MUST match app/components/generate.py's fixed-bounding-box scheme. The
# ``origin_svg`` constant in components/generated/definitions.json — the single
# SVG point the canvas placement transform (app/canvas/svgsym.local_transform)
# maps every symbol's origin to — was measured with a (-3,-3)..(3,3) bounding box
# and a 2 pt standalone border. Rendering custom geometry the same way lets it
# place through that shared transform with no per-component origin.
_BBOX = 3.0
_BORDER_PT = 2

#: Every custom kind key carries this prefix so it can never collide with a
#: built-in CircuiTikZ keyword.
KIND_PREFIX = "custom:"

#: Default palette category for user-defined custom components. Always shown in the
#: palette (even empty) so its "new custom component" tile is reachable (§5.10).
CUSTOM_CATEGORY = "User Defined"


def is_custom_kind(kind: str) -> bool:
    """True if *kind* is a user-defined custom component (vs. a built-in)."""
    return kind.startswith(KIND_PREFIX)


def make_kind(display_name: str) -> str:
    """A custom kind key derived from a display name (prefixed, whitespace-collapsed)."""
    slug = " ".join(display_name.split()).lower()
    return KIND_PREFIX + (slug or "component")


def _opt_suffix(extra_options: str) -> str:
    """``", <opts>"`` for a non-empty option string, else ``""``."""
    e = extra_options.strip().strip(",").strip()
    return f", {e}" if e else ""


def _tex_coord(off) -> str:
    """Qt (y-down) GU offset -> CircuiTikZ (y-up) coordinate string."""
    return f"({off[0]:g},{-off[1]:g})"


def _render_body(rec: dict, extra_options: str) -> str:
    """The TikZ body for one custom component, in the fixed bounding box."""
    tikz = rec["tikz"]
    bbox = rf"\useasboundingbox ({-_BBOX},{-_BBOX}) rectangle ({_BBOX},{_BBOX});"
    opt = _opt_suffix(extra_options)
    if rec.get("emission") == "path":
        span = rec["pins"][1]["offset"]
        return bbox + "\n" + rf"\draw (0,0) to[{tikz}{opt}] {_tex_coord(span)};"
    return bbox + "\n" + rf"\node[{tikz}{opt}] (X) at (0,0) {{}};"


def _bbox_from_geometry(geo: dict, pins: list[dict]) -> tuple[float, float, float, float]:
    """Bounding box (GU, Qt y-down) from the captured geometry viewBox + the pins.

    The viewBox is in SVG points; it maps to GU through the same ``origin_svg`` +
    ``SVG_PT_PER_GU`` the canvas transform uses (no y-flip — the SVG y-axis already
    matches Qt's). Unioned with the pin offsets so terminals are always inside the box.
    """
    ox, oy = library.origin_svg()
    xs: list[float] = []
    ys: list[float] = []
    vb = (geo.get("viewBox") or "").split()
    if len(vb) == 4:
        vx, vy, vw, vh = (float(v) for v in vb)
        xs += [(vx - ox) / SVG_PT_PER_GU, (vx + vw - ox) / SVG_PT_PER_GU]
        ys += [(vy - oy) / SVG_PT_PER_GU, (vy + vh - oy) / SVG_PT_PER_GU]
    for p in pins:
        xs.append(float(p["offset"][0]))
        ys.append(float(p["offset"][1]))
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4))


def build_custom(name: str, display_name: str, category: str, base_kind: str,
                 ctikzset: list[str] | None, extra_options: str) -> CustomComponentSpec:
    """Render *base_kind* with the user's customisation and capture it as a spec.

    Raises :class:`app.components.render.RenderError` if the configuration does not
    compile (the caller surfaces the LaTeX log), or :class:`ValueError` if
    *base_kind* is not a known built-in.
    """
    rec = library.load_library().get(base_kind)
    if rec is None:
        raise ValueError(f"Unknown base kind: {base_kind!r}")
    ctikz = [s for s in (ctikzset or []) if s.strip()]
    extra = (extra_options or "").strip()

    # 1) Drawable geometry — rendered in the fixed bbox so the shared origin_svg
    #    transform places it (parse_geometry returns SVG-point paths/glyphs).
    body = _render_body(rec, extra)
    svg, log = render.render_svg(body, border_pt=_BORDER_PT, ctikzset=ctikz)
    geo = render.parse_geometry(svg)
    version = render.circuitikz_version(log)

    # 2) Re-measure the base's named anchors under the customisation; their
    #    positions can move (e.g. a transformer's coils with a different core),
    #    but the names — and hence codegen's anchor refs — do not. Axial path
    #    terminals (anchor=null) keep the base offset (they connect by coordinate).
    anchor_names = [p["anchor"] for p in rec["pins"] if (p.get("anchor") or "").strip()]
    keyword = rec["tikz"] + _opt_suffix(extra)
    measured = (render.measure_anchors(keyword, anchor_names, ctikzset=ctikz)
                if anchor_names else {})

    pins: list[dict] = []
    for p in rec["pins"]:
        anchor = p.get("anchor")
        if anchor and anchor in measured:
            mx, my = measured[anchor]
            off = [round(mx, 4), round(my, 4)]
        else:
            off = [float(p["offset"][0]), float(p["offset"][1])]
        pins.append({"name": p["name"], "offset": off, "anchor": anchor})

    if rec.get("emission") == "path" and len(pins) >= 2:
        (x0, y0), (x1, y1) = pins[0]["offset"], pins[1]["offset"]
        default_span = (round(x1 - x0, 4), round(y1 - y0, 4))
    else:
        default_span = (0.0, 0.0)

    return CustomComponentSpec(
        name=name,
        display_name=display_name,
        category=category,
        base_kind=base_kind,
        ctikzset=ctikz,
        extra_options=extra,
        pins=pins,
        bbox=_bbox_from_geometry(geo, pins),
        default_span=default_span,
        geometry=geo,
        ctikz_version=version,
    )


def spec_to_component_def(spec: CustomComponentSpec) -> ComponentDef:
    """Build a runtime :class:`ComponentDef` from a stored spec.

    ``tikz_keyword`` and ``label_slots`` are inherited from the base kind so the
    options inspector and codegen behave as they do for the built-in; codegen
    resolves classification and the pin→anchor map via ``base_kind``.
    """
    base = library.load_library().get(spec.base_kind, {})
    pins = [PinDef(name=p["name"], offset=tuple(p["offset"])) for p in spec.pins]
    return ComponentDef(
        kind=spec.name,
        display_name=spec.display_name,
        category=spec.category,
        bbox=tuple(spec.bbox),  # type: ignore[arg-type]
        pins=pins,
        label_slots=list(base.get("labels", [])),
        tikz_keyword=base.get("tikz", spec.base_kind),
        default_span=tuple(spec.default_span),  # type: ignore[arg-type]
        resizable=False,  # Phase 1: geometry is captured at a fixed size
        component_class=Component,
        base_kind=spec.base_kind,
        ctikzset=tuple(spec.ctikzset),
        extra_options=spec.extra_options,
        geometry=spec.geometry,
    )
