"""Render the transistor accessory combinations bundled with the component library."""

from __future__ import annotations

from itertools import combinations, product
import json
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from app.components import generate as gen, library, render
from app.preview import tools


def _choices(label, *choices):
    return {"label": label, "choices": [{"label": "Default", "options": []}, *(
        {"label": name, "options": list(tokens)} for name, tokens in choices
    )]}


_CIRCLE = _choices("Circle", ("Transistor circle", ("tr circle",)))
_PMOS_CIRCLE = _choices(
    "Circle", ("Transistor circle", ("tr circle",)),
    ("No gate circle", ("nocircle",)),
    ("Empty gate circle", ("emptycircle",)),
    ("Transistor circle and no gate circle", ("tr circle", "nocircle")),
    ("Transistor circle and empty gate circle", ("tr circle", "emptycircle")),
)
_GATE = _choices("Gate options", ("No gate", ("nogate",)))
_DOUBLE_GATE = _choices("Gate options", ("No gate", ("nogate",)), ("Double gate", ("doublegate",)))
_SPLIT_GATE = _choices("Gate options", ("No gate", ("nogate",)),
                       ("Split gate", ("circuitikz/tripoles/hemt/split gate=true",)),
                       ("Split gate and no gate", ("circuitikz/tripoles/hemt/split gate=true", "nogate")))
_MOS_ARROW = _choices("Source arrow", ("Source arrow", ("arrowmos",)))
_HEMT_ARROW = _choices("Source arrow",
                       ("1", ("circuitikz/tripoles/hemt/source arrow=1",)),
                       ("−1", ("circuitikz/tripoles/hemt/source arrow=-1",)))
_ACCESSORIES = {
    **dict.fromkeys(("npn", "pnp"), (("bodydiode", "nobase", "photo", "schottky base"), (_CIRCLE,))),
    **dict.fromkeys(("nigbt", "pigbt", "Lnigbt", "Lpigbt"), (("bodydiode", "nobase"), (_CIRCLE,))),
    **dict.fromkeys(("nmos", "nmosd"), (("bodydiode", "bulk", "ferroel gate"), (_MOS_ARROW, _CIRCLE, _GATE))),
    **dict.fromkeys(("pmos", "pmosd"), (("bodydiode", "bulk", "ferroel gate"), (_MOS_ARROW, _PMOS_CIRCLE, _GATE))),
    "hemt": (("bodydiode",), (_HEMT_ARROW, _CIRCLE, _SPLIT_GATE)),
    "GaN hemt": (("bodydiode",), (_CIRCLE, _GATE)),
    **dict.fromkeys(("nfet", "nfetd", "pfet", "pfetd"), (("bodydiode", "ferroel gate"), (_CIRCLE, _GATE))),
    **dict.fromkeys(("nigfete", "nigfetebulk", "nigfetd", "pigfete", "pigfetebulk", "pigfetd"),
                    (("bodydiode", "ferroel gate", "solderdot"), (_CIRCLE, _DOUBLE_GATE))),
    **dict.fromkeys(("njfet", "pjfet", "isfet"), (("bodydiode",), (_CIRCLE,))),
    **dict.fromkeys(("nujt", "pujt"), (("bodydiode",), (_CIRCLE, _GATE))),
    **dict.fromkeys(("ngfet", "pgfet"), (("bodydiode",), ())),
}
_LABELS = {"bodydiode": "Body diode", "nobase": "No base", "photo": "Photo",
           "schottky base": "Schottky base", "bulk": "Bulk", "ferroel gate": "Ferroelectric gate",
           "solderdot": "Solder dot"}


def _name(token: str) -> str:
    return token.removeprefix("circuitikz/tripoles/hemt/").replace(" ", "_").replace("=", "_")


def _combinations(toggles, groups):
    subsets = [subset for n in range(len(toggles) + 1) for subset in combinations(toggles, n)]
    choices = [[c["options"] for c in group["choices"]] for group in groups]
    for subset, *selected in product(subsets, *choices):
        yield (*subset, *(token for choice in selected for token in choice))


def _render_pages(entry: dict, cases: list[tuple[str, ...]]) -> list[tuple[dict, dict]]:
    pages = []
    # The PNP shares a helper anchor whose underlying gate dimension is undefined.
    base_names = [p["anchor"] for p in entry["pins"]
                  if p["anchor"] and not (entry["tikz"] == "pnp" and p["anchor"] == "nogate")]
    for index, options in enumerate(cases, start=1):
        body = gen.render_body(entry, option=", " + ", ".join(options))
        pins = list(base_names)
        if "doublegate" in options:
            pins.append("G2")
        if "bulk" in options:
            pins.append("bulk")
        checks = [
            rf"\ifcsname pgf@anchor@\hvshape @{pin}\endcsname"
            rf"\pgfpointanchor{{X}}{{{pin}}}\typeout{{HVACC {index}:{pin}:\the\pgf@x:\the\pgf@y}}\fi"
            for pin in dict.fromkeys(pins)
        ]
        pages.append("\n".join((
            r"\begin{circuitikz}", body, r"\makeatletter",
            r"\edef\hvshape{\csname pgf@sh@ns@X\endcsname}",
            *checks, r"\makeatother", r"\end{circuitikz}",
        )))
    source = (rf"\documentclass[multi=circuitikz,border={gen.BORDER_PT}pt]{{standalone}}" "\n"
              r"\usepackage[american]{circuitikz}" "\n" r"\begin{document}" "\n"
              + "\n".join(pages) + "\n" r"\end{document}")
    with TemporaryDirectory() as tmp:
        work = Path(tmp)
        (work / "accessories.tex").write_text(source, encoding="utf-8")
        latex = subprocess.run(
            ["latex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "accessories.tex"],
            cwd=work, capture_output=True, text=True, timeout=120, **tools.run_kwargs(),
        )
        if latex.returncode:
            raise render.RenderError("Transistor accessory LaTeX render failed", latex.stdout)
        svg = subprocess.run(
            ["dvisvgm", "--no-fonts", "--page=1-", "--output=page-%p.svg", "accessories.dvi"],
            cwd=work, capture_output=True, text=True, timeout=120,
            env=render._render_env(), **tools.run_kwargs(),
        )
        files = sorted(work.glob("page-*.svg"), key=lambda p: int(p.stem.split("-")[1]))
        if svg.returncode or len(files) != len(cases):
            raise render.RenderError("Transistor accessory SVG render failed", svg.stderr)
        positions: dict[int, dict] = {}
        for page, pin, x, y in re.findall(r"HVACC (\d+):([^:]+):(-?[\d.]+)pt:(-?[\d.]+)pt", latex.stdout):
            positions.setdefault(int(page), {})[pin] = [
                gen._grid_offset(float(x) / render.TEXPT_PER_GU),
                gen._grid_offset(-float(y) / render.TEXPT_PER_GU),
            ]
        return [(render.parse_geometry(file.read_text(encoding="utf-8")), positions.get(i, {}))
                for i, file in enumerate(files, start=1)]


def add_accessories(kind: str, entry: dict, definition: dict, geometry: dict, origin: tuple) -> None:
    """Add native combination geometry and inspector metadata to one definition."""
    if kind not in _ACCESSORIES:
        return
    toggles, groups = _ACCESSORIES[kind]
    key = library.geometry_key(kind)
    cases = [case for case in _combinations(toggles, groups) if case]
    all_tokens = list(dict.fromkeys((*toggles, *(
        token for group in groups for choice in group["choices"] for token in choice["options"]
    ))))
    definition["variants"] = [
        {"name": _name(token), "token": token, "mode": "option",
         **({"label": _LABELS[token]} if token in _LABELS else {})}
        for token in all_tokens
    ]
    definition["accessory_groups"] = [
        {"label": group["label"], "choices": [
            {"label": c["label"], "options": [_name(token) for token in c["options"]]}
            for c in group["choices"]
        ]} for group in groups
    ]
    results = _render_pages(entry, cases)
    by_shape = {}
    definition["accessories"] = records = {}
    for options, (shape, measured) in zip(cases, results, strict=True):
        identity = json.dumps({k: shape[k] for k in ("paths", "glyphs")}, sort_keys=True)
        if identity not in by_shape:
            by_shape[identity] = f":accessories:{len(by_shape) + 1}"
            geometry[key + by_shape[identity]] = shape
        pins = [{**p, "offset": measured.get(p["anchor"], p["offset"])} for p in definition["pins"]]
        names = {p["name"] for p in pins}
        for name in ("G2", "bulk"):
            if name in measured and name not in names:
                offset = measured[name]
                if all(p["offset"] != offset for p in pins):
                    pins.append({"name": name, "anchor": name, "offset": offset})
        record = {"geometry": by_shape[identity], "bbox": gen.compute_bbox(shape, origin, pins)}
        if pins != definition["pins"]:
            record["pins"] = pins
        records["|".join(sorted(map(_name, options)))] = record
        if len(options) == 1:
            geometry[library.geometry_key(f"{key}_{options[0]}")] = shape
