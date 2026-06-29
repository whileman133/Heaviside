#!/usr/bin/env python3
r"""Discover documented component anchors from the CircuiTikZ manual source.

*** DISCOVERY AID — REPORTS ONLY, NEVER MUTATES THE DATA FILES ***

A reading tool in the spirit of ``components/_probe.py`` ("ground truth — never
invents"): it parses the CircuiTikZ manual (``circuitikzmanual.tex``) to recover,
per component, the **named anchors** the manual documents, then cross-references
``components/definitions.json`` to flag anchors Heaviside does not yet expose as
pins. It answers the maintainer's suggestion in circuitikz discussion #945 —
"identify the main anchors from the documentation .tex" — but it only *reports*;
adding a discovered anchor is a deliberate, measured edit (the offsets must still
be measured with ``app/components/render.py``, as for the potentiometer wiper and
the transformer centre taps).

How the manual encodes anchors (definitions in ``ctikzmanutils.sty``):

* ``\circuitdesc{s O{1} m m m d() d[]}`` — a **node** component. ``#3`` is the
  shape/keyword; ``#6`` is a ``(name/angle/dist, …)`` list of node anchors
  (drawn via ``N.<name>``); ``#7`` is a ``[name/angle/dist, …]`` list of
  **sub-node** anchors (``N-<name>``).
* ``\circuitdescbip{s o m d<> m m d() d[]}`` — a **path-style bipole**. ``#3`` is
  the keyword; ``#7`` the ``(…)`` anchor list (``B.<name>``); ``#8`` the ``[…]``
  sub-node list (``B-<name>``).
* ``\showanchors[O{}]{m}{m}d()`` — a bare shape demo; ``#2`` is the node spec
  (first token is the shape name) and ``#4`` the ``(…)`` anchor list.

Geographical anchors (``north``/``east``/``center``/…) are filtered out by default,
since every shape carries them and they are not the "special" anchors of interest.

Two output modes:

* ``--format report`` (default) — the Heaviside diagnostic above: documented anchors
  with an ``[ok]``/``[MISSING]`` cross-reference against ``definitions.json``.
* ``--format md`` / ``--format latex`` — a **complete catalog table** of *every*
  component the manual documents (all ``\circuitdesc``/``\circuitdescbip`` entries,
  ~480 before de-duplication) with its anchors. This is the
  "components-and-anchors" reference table the CircuiTikZ maintainer asked for in
  discussion #945; the LaTeX form is a ``longtable`` ready to drop into the manual.

By default the catalog anchor lists reflect only what the manual *documents* (the
``(…)``/``[…]`` lists of the description macros plus ``\showanchors`` demos). Two
opt-in probes recover what the manual leaves out:

* ``--probe`` — replace the documented anchors with each shape's **complete** anchor
  set, read straight from the engine. This does **not** render geometry: it compiles
  the component with ``latex`` only, looks up its pgf shape, and asks
  ``\ifcsname pgf@anchor@<shape>@<name>`` for every candidate name (the union of all
  anchors the manual mentions). That returns CircuiTikZ's *fully-resolved* anchor
  table — after all the shared-macro reuse, ``\inheritanchor`` chains, and the
  keyword→shape indirection that make a purely static source scan unreliable. Fills
  the gaps where a component's own entry under-documents its anchors (e.g. the
  gyrator). ~20 s for the whole library.
* ``--options`` — add an **Options** column listing the options each component
  actually responds to (e.g. ``bodydiode`` for MOSFETs). Options have *no*
  declarative component→option mapping in the source — they are ``\if…`` flags
  tested inside the drawing routines — so the only reliable signal is to **render**
  the component with each candidate option (``latex`` + ``dvisvgm``) and keep those
  that change its ink. Candidates are the boolean toggles harvested from the package
  source. Implies ``--probe``; a few minutes for the whole library.

    python components/extract_doc_anchors.py                    # diagnostic report
    python components/extract_doc_anchors.py --missing          # only not-yet-exposed
    python components/extract_doc_anchors.py --format md        # full catalog (markdown)
    python components/extract_doc_anchors.py --format latex     # full catalog (LaTeX longtable)
    python components/extract_doc_anchors.py --format md --geo  # include geographical anchors
    python components/extract_doc_anchors.py --format latex --probe    # complete anchor sets
    python components/extract_doc_anchors.py --format latex --options  # + supported options

The parse-only modes need only the manual source on disk (no LaTeX run); ``--probe``
and ``--options`` need the LaTeX toolchain.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))      # so the optional --probe path can import ``app``
DEFINITIONS = Path(__file__).with_name("generated") / "definitions.json"

# Generic geometric / housekeeping anchor *directions* — not the component-specific
# anchors this tool is meant to surface. These are the pgf compass anchors and their
# abbreviations, the box-corner/edge directions, and the geometric convenience
# anchors CircuiTikZ adds (e.g. the quadpole ``up``/``down``/``left up`` and the
# tube/circle envelope points), all defined as pure geometric points in the package,
# not electrical terminals.
_GEO = {
    "center", "centre", "mid", "base", "border", "text",
    "north", "south", "east", "west", "n", "s", "e", "w",
    "north east", "north west", "south east", "south west",
    "ne", "nw", "se", "sw",
    "left", "right", "up", "down", "top", "bottom",
    "left up", "left down", "right up", "right down",
    "up left", "up right", "down left", "down right",
    "top left", "top right", "bottom left", "bottom right",
    "top center", "bottom center", "left center", "right center",
    "top centre", "bottom centre", "left centre", "right centre",
}

# Geometric anchors also appear on a shape's *sub-envelope*, named ``<prefix> <dir>``
# — e.g. a tube's ``tube top center`` or a circled transistor's ``circle bottom``.
# Such a name is geometric when the part after the prefix is a bare direction (so
# ``tube top`` is dropped but ``circle C`` / ``circle base`` electrical points stay
# — ``C`` is not a direction). ``base`` *is* a direction, so ``circle base`` drops;
# the terminal survives under its plain ``B``/``base`` alias. (``inner``/``outer`` are
# *not* listed alone: ``inner dot A1`` etc. are real polarity-dot anchors, kept
# anyway since ``dot A1`` is not a direction.)
#
# The prefixes are the shape *envelopes* CircuiTikZ exposes geometric points on:
# the tube/circle/block bodies, the chip ``inset``/``narrow`` rectangle, a
# transistor ``body`` edge, the hex-shape outline, the connector ``plug``/``socket``
# bodies, and the ``center`` edge-midpoints. Real terminals that merely begin with
# one of these words survive because their remainder is not a bare direction
# (``body C in``, ``plug in``, ``center tap``, the single-word ``centerprim``).
_GEO_PREFIXES = (
    "tube ", "circle ", "block ", "inset ", "narrow ", "body ", "center ",
    "plug ", "socket ", "inner hex ", "outer hex ",
)


def _is_geo(name: str) -> bool:
    """Whether *name* is a generic geometric/housekeeping anchor (compass point,
    box corner/edge, or a ``<envelope> <direction>`` point) rather than a
    component-specific terminal.

    The match is **case-sensitive**: CircuiTikZ writes geometric anchors in lower
    case (``north``, ``ne``, ``e``, ``up``) while terminals are upper case
    (``E`` emitter, ``S`` source, ``B``, ``G``), and ``E`` must not be mistaken for
    east. ``_GEO`` is therefore all lower case and compared without folding."""
    if name in _GEO:
        return True
    for p in _GEO_PREFIXES:
        if name.startswith(p) and name[len(p):] in _GEO:
            return True
    return False


def _find_manual() -> Path | None:
    """Locate ``circuitikzmanual.tex`` via kpsewhich, falling back to a scan of the
    TeX distribution's doc tree."""
    try:
        out = subprocess.run(["kpsewhich", "circuitikzmanual.tex"],
                             capture_output=True, text=True, timeout=15)
        p = Path(out.stdout.strip())
        if p.is_file():
            return p
    except (OSError, subprocess.SubprocessError):
        pass
    # kpsewhich does not index docs; scan the standard texmf doc location.
    for base in ("/usr/local/texlive", "/usr/share/texlive", "/usr/share/texmf"):
        for cand in Path(base).rglob("circuitikzmanual.tex") if Path(base).is_dir() else []:
            return cand
    return None


def _read_balanced(text: str, i: int, open_ch: str, close_ch: str) -> tuple[str, int]:
    """Read a balanced ``open_ch … close_ch`` group starting at ``text[i] == open_ch``.

    Returns ``(inner, j)`` where ``inner`` is the content between the delimiters and
    ``j`` is the index just past the closing delimiter. Nested same-type delimiters
    are tracked so e.g. a brace group inside an anchor list does not end it early."""
    assert text[i] == open_ch
    depth = 0
    start = i + 1
    while i < len(text):
        c = text[i]
        if c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    return text[start:], len(text)   # unbalanced: take the rest (defensive)


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i] in " \t\r\n%":
        if text[i] == "%":                       # skip a TeX comment to end of line
            while i < len(text) and text[i] != "\n":
                i += 1
        else:
            i += 1
    return i


def _parse_args(text: str, i: int, sig: list[str]) -> tuple[dict, int] | None:
    """Parse a macro argument sequence per *sig* starting at ``text[i]``.

    *sig* tokens: ``s`` star, ``m`` ``{…}``, ``o`` ``[…]`` (optional), ``a`` ``<…>``
    (optional), ``p`` ``(…)`` (optional), ``q`` ``[…]`` (optional, distinct slot).
    Returns ``({index: value}, end)`` with present optional args keyed by position,
    or ``None`` on a hard parse failure (a missing mandatory ``{…}``)."""
    out: dict[int, str] = {}
    for n, tok in enumerate(sig):
        i = _skip_ws(text, i)
        if i >= len(text):
            return None
        if tok == "s":
            if text[i] == "*":
                out[n] = "*"; i += 1
        elif tok == "m":
            if text[i] != "{":
                return None
            val, i = _read_balanced(text, i, "{", "}")
            out[n] = val
        elif tok in ("o", "q"):
            if text[i] == "[":
                val, i = _read_balanced(text, i, "[", "]")
                out[n] = val
        elif tok == "a":
            if text[i] == "<":
                val, i = _read_balanced(text, i, "<", ">")
                out[n] = val
        elif tok == "p":
            if text[i] == "(":
                val, i = _read_balanced(text, i, "(", ")")
                out[n] = val
    return out, i


def _anchor_names(spec: str | None, keep_geo: bool = False) -> list[str]:
    """Anchor names from a ``name/angle/dist, …`` list (the bit before each first
    ``/``), order preserved, de-duplicated. Geographical names are dropped unless
    *keep_geo* is set."""
    if not spec:
        return []
    # Strip TeX line comments (a ``%`` to end-of-line) the anchor list may span,
    # then collapse internal whitespace so a name broken across lines reads cleanly.
    spec = re.sub(r"(?<!\\)%[^\n]*", "", spec)
    names: list[str] = []
    for part in spec.split(","):
        name = " ".join(part.split("/", 1)[0].split())
        if name and (keep_geo or not _is_geo(name)) and name not in names:
            names.append(name)
    return names


def _clean_tex(s: str | None) -> str:
    """A description fragment tidied for a table cell: TeX comments and footnotes
    stripped, whitespace collapsed (the LaTeX/math is otherwise left intact so it
    typesets correctly when the table is dropped into the manual)."""
    if not s:
        return ""
    s = re.sub(r"(?<!\\)%[^\n]*", "", s)
    s = re.sub(r"\\footnote\{[^{}]*\}", "", s)
    s = re.sub(r"\\footnotemark\b", "", s)
    return " ".join(s.split())


def _first_token(nodespec: str) -> str:
    """The shape name from a ``\\showanchors`` node spec — the leading token before
    any option (``ground, scale=1.5`` → ``ground``; ``rotary switch=…`` →
    ``rotary switch``)."""
    s = nodespec.strip()
    s = re.split(r"[=,]", s, 1)[0]
    return s.strip()


def extract(manual: str, *, keep_geo: bool = False) -> dict[str, list[str]]:
    """Map ``keyword/shape → [anchor names]`` from the manual source.

    Merges anchors when the same keyword appears in several entries (american /
    european variants, ``\\showanchors`` demos). With *keep_geo* the geographical
    anchors (``north``/``center``/``left``/…) are kept too, for callers that want
    **every** documented anchor rather than only the "special" ones."""
    found: dict[str, list[str]] = {}

    def add(key: str, anchors: list[str]) -> None:
        if not key:
            return
        bucket = found.setdefault(key, [])
        for a in anchors:
            if a not in bucket:
                bucket.append(a)

    def names(spec: str | None) -> list[str]:
        return _anchor_names(spec, keep_geo=keep_geo)

    # \circuitdesc{s O{1} m m m d() d[]}  — node components.
    for m in re.finditer(r"\\circuitdesc(?![a-zA-Z])", manual):
        parsed = _parse_args(manual, m.end(), ["s", "o", "m", "m", "m", "p", "q"])
        if not parsed:
            continue
        args, _ = parsed
        add(args.get(2, "").strip(), names(args.get(5)) + names(args.get(6)))

    # \circuitdescbip{s o m d<> m m d() d[]}  — path-style bipoles.
    for m in re.finditer(r"\\circuitdescbip\*?", manual):
        # The optional star is consumed by the regex; parse the rest.
        parsed = _parse_args(manual, m.end(), ["o", "m", "a", "m", "m", "p", "q"])
        if not parsed:
            continue
        args, _ = parsed
        add(args.get(1, "").strip(), names(args.get(5)) + names(args.get(6)))

    # \showanchors[O{}]{nodespec}{text}d()  — bare shape demos.
    for m in re.finditer(r"\\showanchors(?![a-zA-Z])", manual):
        parsed = _parse_args(manual, m.end(), ["o", "m", "m", "p"])
        if not parsed:
            continue
        args, _ = parsed
        add(_first_token(args.get(1, "")), names(args.get(3)))

    return {k: v for k, v in found.items() if v}


def extract_catalog(manual: str, *, keep_geo: bool = False) -> list[dict]:
    """Every component the manual documents, with its anchors — the full catalog.

    Returns a keyword-sorted list of records::

        {keyword, kind ('path'|'node'), desc, aliases, shape, anchors, subnode}

    Built from the ``\\circuitdesc`` (node) and ``\\circuitdescbip`` (path) reference
    macros — one row per distinct keyword, merging the american/european style
    variants — and enriched with any extra anchors a ``\\showanchors`` demo documents
    for the same keyword/shape. Unlike :func:`extract`, components with no special
    anchors are **kept** (their ``anchors`` is empty) so the table is exhaustive."""
    by_kw: dict[str, dict] = {}

    def rec(keyword: str, *, kind: str, desc: str = "", aliases: str = "",
            shape: str = "") -> dict:
        keyword = keyword.strip()
        r = by_kw.get(keyword)
        if r is None:
            r = by_kw[keyword] = {"keyword": keyword, "kind": kind, "desc": desc,
                                  "aliases": aliases, "shape": shape,
                                  "anchors": [], "subnode": []}
        elif not r["desc"] and desc:                  # first style variant wins
            r["desc"] = desc
        return r

    def merge(dst: list[str], src: list[str]) -> None:
        for a in src:
            if a not in dst:
                dst.append(a)

    # \circuitdesc{s O{1} m m m d() d[]}  — node components.
    for m in re.finditer(r"\\circuitdesc(?![a-zA-Z])", manual):
        parsed = _parse_args(manual, m.end(), ["s", "o", "m", "m", "m", "p", "q"])
        if not parsed:
            continue
        a, _ = parsed
        kw = a.get(2, "").strip()
        r = rec(kw, kind="node", desc=_clean_tex(a.get(3)), shape=kw)
        merge(r["anchors"], _anchor_names(a.get(5), keep_geo))
        merge(r["subnode"], _anchor_names(a.get(6), keep_geo))

    # \circuitdescbip{s o m d<> m m d() d[]}  — path-style bipoles.
    for m in re.finditer(r"\\circuitdescbip\*?", manual):
        parsed = _parse_args(manual, m.end(), ["o", "m", "a", "m", "m", "p", "q"])
        if not parsed:
            continue
        a, _ = parsed
        r = rec(a.get(1, ""), kind="path", desc=_clean_tex(a.get(3)),
                aliases=_clean_tex(a.get(4)), shape=(a.get(0) or "").strip())
        merge(r["anchors"], _anchor_names(a.get(5), keep_geo))
        merge(r["subnode"], _anchor_names(a.get(6), keep_geo))

    # \showanchors demos enrich a catalog row whose keyword or shape they match.
    show: dict[str, list[str]] = {}
    for m in re.finditer(r"\\showanchors(?![a-zA-Z])", manual):
        parsed = _parse_args(manual, m.end(), ["o", "m", "m", "p"])
        if not parsed:
            continue
        a, _ = parsed
        tok = _first_token(a.get(1, ""))
        if tok:
            show.setdefault(tok, [])
            merge(show[tok], _anchor_names(a.get(3), keep_geo))
    for r in by_kw.values():
        for key in (r["keyword"], r["shape"]):
            if key in show:
                merge(r["anchors"], show[key])

    return sorted(by_kw.values(), key=lambda r: r["keyword"].lower())


def _catalog_anchor_cell(r: dict, *, geo: bool) -> list[str]:
    """The anchor names for a catalog row — the probed (complete) set when present,
    else the documented set — geographical names dropped unless *geo*, sub-node
    anchors flagged."""
    if "probed_anchors" in r:
        names = [a for a in r["probed_anchors"] if geo or not _is_geo(a)]
    else:
        names = list(r["anchors"])
    return names + [f"{a} (sub-node)" for a in r["subnode"]]


def _source_note(catalog: list[dict], geo: bool) -> str:
    """A one-line provenance note: parsed-from-manual vs render-probed."""
    probed = any("probed_anchors" in r for r in catalog)
    opts = any("options" in r for r in catalog)
    how = ("anchors render-probed from the installed CircuiTikZ"
           if probed else "anchors as documented in the manual")
    if opts:
        how += "; options render-probed (those that visibly change the symbol)"
    geo_note = ("geographical anchors included" if geo else
                "geographical anchors (north/east/center/…) omitted")
    return f"{how}; {geo_note}"


def format_markdown(catalog: list[dict], *, geo: bool = False) -> str:
    """The catalog as a GitHub-flavoured Markdown table."""
    with_opts = any("options" in r for r in catalog)
    head = ["Component", "Keyword", "Type", "Anchors"] + (["Options"] if with_opts else [])
    lines = [
        f"<!-- {len(catalog)} components from the CircuiTikZ manual; "
        f"{_source_note(catalog, geo)}. "
        f"Generated by components/extract_doc_anchors.py. -->",
        "",
        "| " + " | ".join(head) + " |",
        "|" + "---|" * len(head),
    ]
    for r in catalog:
        anchors = _catalog_anchor_cell(r, geo=geo)
        row = [_md_cell(r["desc"]), _md_cell(f"`{r['keyword']}`"),
               "path-style" if r["kind"] == "path" else "node",
               _md_cell(", ".join(anchors)) if anchors else "—"]
        if with_opts:
            opts = r.get("options", [])
            row.append(_md_cell(", ".join(opts)) if opts else "—")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _md_cell(s: str) -> str:
    """Escape a value for a Markdown table cell (pipes and newlines)."""
    return s.replace("|", r"\|").replace("\n", " ")


_LATEX_ESCAPE = {
    "\\": r"\textbackslash{}", "_": r"\_", "#": r"\#", "&": r"\&",
    "%": r"\%", "$": r"\$", "{": r"\{", "}": r"\}",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def _tt(s: str) -> str:
    """A LaTeX ``\\texttt`` cell with special characters escaped (for keywords and
    anchor names, which are verbatim identifiers — not pre-formatted LaTeX)."""
    return r"\texttt{" + "".join(_LATEX_ESCAPE.get(c, c) for c in s) + "}"


def _tt_list(names: list[str]) -> str:
    """A comma-separated ``\\texttt`` cell (or ``--`` when empty). A ``(sub-node)``
    suffix on a name is set in normal type, not typewriter."""
    if not names:
        return "--"
    out = []
    for a in names:
        if a.endswith(" (sub-node)"):
            out.append(_tt(a[:-len(" (sub-node)")]) + " (sub-node)")
        else:
            out.append(_tt(a))
    return ", ".join(out)


def format_latex(catalog: list[dict], *, geo: bool = False) -> str:
    """The catalog as a LaTeX ``longtable`` ready to ``\\input`` into the manual.

    Component descriptions are emitted verbatim (they are already manual LaTeX, e.g.
    ``\\scshape npn`` or math); keywords, anchor and option names are ``\\texttt``
    with TeX specials escaped. An Options column is added when options were probed."""
    with_opts = any("options" in r for r in catalog)
    if with_opts:
        colspec = r"@{}p{0.21\linewidth} l l p{0.30\linewidth} p{0.22\linewidth}@{}"
        header = (r"\textbf{Component} & \textbf{Keyword} & \textbf{Type} & "
                  r"\textbf{Anchors} & \textbf{Options} \\")
    else:
        colspec = r"@{}p{0.28\linewidth} l l p{0.40\linewidth}@{}"
        header = (r"\textbf{Component} & \textbf{Keyword} & \textbf{Type} & "
                  r"\textbf{Anchors} \\")
    out = [
        r"% Component / anchor" + (" / option" if with_opts else "") + " reference,"
        r" generated by",
        r"%   components/extract_doc_anchors.py --format latex"
        + (" --probe" if any("probed_anchors" in r for r in catalog) else "")
        + (" --options" if with_opts else ""),
        rf"% {len(catalog)} components from the CircuiTikZ manual; {_source_note(catalog, geo)}.",
        r"\begingroup\small",
        rf"\begin{{longtable}}{{{colspec}}}",
        r"\toprule", header, r"\midrule\endfirsthead",
        r"\toprule", header, r"\midrule\endhead",
        r"\bottomrule\endfoot",
    ]
    for r in catalog:
        anchors = _tt_list(_catalog_anchor_cell(r, geo=geo))
        typ = "path" if r["kind"] == "path" else "node"
        desc = r["desc"] or _tt(r["keyword"])         # fall back to the keyword
        cells = [desc, _tt(r["keyword"]), typ, anchors]
        if with_opts:
            cells.append(_tt_list(r.get("options", [])))
        out.append(" & ".join(cells) + r" \\")
    out += [r"\end{longtable}", r"\endgroup"]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Rendering probe (opt-in, slow). The manual documents only *some* of each
# shape's anchors and none of its options declaratively, so the only robust way
# to recover the complete sets is to compile each component and ask the engine.
# Needs the LaTeX toolchain (latex + dvisvgm) via ``app.components.render``.
# ---------------------------------------------------------------------------

# Characters allowed in a candidate name injected into TeX — a guard so a stray
# manual token can never break (or worse, inject into) the probe compile.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9 .+_-]+$")

# Option keys never worth probing: label/annotation slots, universal styling, and
# any value-taking dimension/parameter or choice key. These either change every
# component's geometry (noise) or need an argument (won't apply bare).
_OPTION_EXCLUDE_EXACT = {
    "a", "f", "i", "l", "n", "t", "v",                  # label / annotation slots
    "a2base", "l2base", "no topmark",                   # label-base positioning
    "color", "fill", "draw", "dashed", "dotted", "solid", "thick", "thin",
    "mirror", "mirrored", "invert", "inverted",         # universal geometry flips
    "box", "boxed", "box only", "boxed only",           # generic block framing
    "american", "european", "cute", "raised",
}
_OPTION_EXCLUDE_SUBSTR = (
    "width", "height", "distance", " pos", "len", "fraction", "scale", "number",
    "segments", "style", "symbols", " text", "anchors", "origin", "color",
    "leads", "lateral", "shape", "annotation", "label",
)


def _probe_body(keyword: str, kind: str, *, option: str | None = None) -> tuple[str, str]:
    """The TeX body placing *keyword* (a node, or a path bipole) and the node id
    its shape is bound to (``X`` for a node, ``B`` for the named bipole)."""
    opt = f", {option}" if option else ""
    if kind == "path":
        return rf"\draw (0,0) to[{keyword}{opt}, name=B] (2,0);", "B"
    return rf"\node[{keyword}{opt}] (X) at (0,0) {{}};", "X"


def probe_anchors(keyword: str, kind: str, pool: list[str]) -> list[str] | None:
    """The anchors *keyword*'s shape actually defines, among the candidate *pool*.

    Places the component, reads its pgf shape name, then asks pgf which pool names
    resolve to a real ``\\anchor`` via ``\\ifcsname pgf@anchor@<shape>@<name>`` — so
    undefined names are skipped without error and without the centre-fallback false
    positives a coordinate probe suffers. Returns the defined names in pool order,
    or ``None`` if the component itself fails to compile.

    Limited to names in *pool* (no engine call enumerates anchors), so the pool must
    be rich — the union of every anchor the manual mentions makes a good one. Cannot
    see sub-node anchors (``midtap``), which live on internal nodes, not the shape."""
    from app.components import render
    body, nid = _probe_body(keyword, kind)
    safe = [a for a in pool if _SAFE_NAME.match(a)]
    lines = [body, r"\makeatletter",
             rf"\edef\hv@shape{{\csname pgf@sh@ns@{nid}\endcsname}}"]
    lines += [rf"\ifcsname pgf@anchor@\hv@shape @{a}\endcsname\typeout{{HVHAS {a}}}\fi"
              for a in safe]
    lines.append(r"\makeatother")
    try:
        # latex only — we read the symbol table from the log, not any geometry.
        log = render.compile_log("\n".join(lines), border_pt=6, node_id=nid, anchors=[])
    except render.RenderError:
        return None
    have = {l.split("HVHAS ", 1)[1].strip() for l in log.splitlines() if "HVHAS " in l}
    return [a for a in safe if a in have]


def _geometry_signature(keyword: str, kind: str,
                        option: str | None = None) -> tuple | None:
    """A hashable signature of *keyword*'s drawn geometry (the path data), or
    ``None`` if it fails to compile. Two signatures differ iff the rendered ink
    differs — the test for whether an *option* affects the component."""
    from app.components import render
    body, _ = _probe_body(keyword, kind, option=option)
    try:
        svg, _ = render.render_svg(body, border_pt=6)
    except render.RenderError:
        return None
    g = render.parse_geometry(svg)
    return (tuple(p.get("d", "") for p in g.get("paths", [])),
            tuple(gl.get("d", "") for gl in g.get("glyphs", [])))


def probe_options(keyword: str, kind: str, options: list[str],
                  base: tuple | None) -> list[str]:
    """Which of *options* visibly change *keyword*'s geometry (vs the *base*
    signature). An option that fails to compile or leaves the ink unchanged is not
    reported — so the noisy candidate pool self-filters per component."""
    if base is None:
        return []
    out: list[str] = []
    for opt in options:
        if not _SAFE_NAME.match(opt):
            continue
        sig = _geometry_signature(keyword, kind, opt)
        if sig is not None and sig != base:
            out.append(opt)
    return out


def candidate_anchor_pool(catalog: list[dict]) -> list[str]:
    """Every anchor name the manual mentions (across all components), plus the
    standard geographical anchors — the search space for :func:`probe_anchors`."""
    pool: list[str] = []
    for r in catalog:
        for a in r["anchors"] + r["subnode"]:
            tail = a.split(".")[-1]              # a sub-node ``L1.midtap`` → ``midtap``
            for name in (a, tail):
                if name not in pool:
                    pool.append(name)
    for g in sorted(_GEO):
        if g not in pool:
            pool.append(g)
    return pool


def find_package_dir() -> Path | None:
    """The CircuiTikZ ``tex/generic`` source dir (holding ``pgfcirc*.tex``), via
    kpsewhich (these *are* indexed) or a scan."""
    try:
        out = subprocess.run(["kpsewhich", "pgfcirctripoles.tex"],
                             capture_output=True, text=True, timeout=15)
        p = Path(out.stdout.strip())
        if p.is_file():
            return p.parent
    except (OSError, subprocess.SubprocessError):
        pass
    for base in ("/usr/local/texlive", "/usr/share/texlive", "/usr/share/texmf"):
        if Path(base).is_dir():
            for cand in Path(base).rglob("pgfcirctripoles.tex"):
                return cand.parent
    return None


def harvest_options(pkg_dir: Path) -> list[str]:
    """Candidate option keys from the CircuiTikZ source: the boolean ``.add
    code``/``.code`` toggles defined via ``\\ctikzset``/``\\pgfkeys`` whose key is a
    plain identifier, minus the universal/value-taking ones (:data:`_OPTION_EXCLUDE_*`).
    These are *candidates*; :func:`probe_options` decides per component which apply."""
    pat = re.compile(
        r"\\(?:ctikzset|pgfkeys)\{(?:/tikz/)?([a-z][a-z0-9 ]*?)/\.(?:add code|code)\b")
    found: list[str] = []
    for f in sorted(pkg_dir.glob("pgfcirc*.tex")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in pat.finditer(text):
            name = m.group(1).strip()
            if (name and name not in found
                    and name not in _OPTION_EXCLUDE_EXACT
                    and not any(s in name for s in _OPTION_EXCLUDE_SUBSTR)):
                found.append(name)
    return sorted(found)


def run_probe(catalog: list[dict], *, do_options: bool, workers: int = 12,
              log=lambda msg: None) -> None:
    """Probe every component's real anchors (and, if *do_options*, its supported
    options) by compilation, writing ``probed_anchors`` / ``options`` onto each
    catalog row in place. Parallel across components (render is per-temp-dir
    thread-safe, like ``components/_probe.py``)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    anchor_pool = candidate_anchor_pool(catalog)
    option_pool: list[str] = []
    if do_options:
        pkg = find_package_dir()
        option_pool = harvest_options(pkg) if pkg else []
        log(f"  {len(option_pool)} candidate options harvested from the package source")
    log(f"  probing {len(catalog)} components against {len(anchor_pool)} anchor names"
        + (f" and {len(option_pool)} options" if do_options else "")
        + f" ({workers} workers)…")

    def work(r: dict) -> tuple[str, list[str] | None, list[str] | None]:
        anchors = probe_anchors(r["keyword"], r["kind"], anchor_pool)
        opts = None
        if do_options and anchors is not None:
            base = _geometry_signature(r["keyword"], r["kind"])
            opts = probe_options(r["keyword"], r["kind"], option_pool, base)
        return r["keyword"], anchors, opts

    results: dict[str, tuple] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, r): r for r in catalog}
        done = 0
        for fut in as_completed(futures):
            kw, anchors, opts = fut.result()
            results[kw] = (anchors, opts)
            done += 1
            if done % 25 == 0 or done == len(catalog):
                log(f"    {done}/{len(catalog)} components probed")

    for r in catalog:
        anchors, opts = results.get(r["keyword"], (None, None))
        if anchors is not None:
            r["probed_anchors"] = anchors
        if opts is not None:
            r["options"] = opts


def _exposed_anchors() -> dict[str, set[str]]:
    """``tikz keyword → {anchor names Heaviside exposes as pins}`` from
    definitions.json.

    A pin maps to a documented anchor by its ``anchor`` field — possibly a sub-node
    ref (``-L1.midtap``), so the bare tail (``midtap``) is recorded too — **or** by
    its own name, since a coordinate-connected pin (``anchor: null``, e.g. the
    potentiometer wiper or thyristor gate) carries the anchor's name as its pin
    name."""
    data = json.loads(DEFINITIONS.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for comp in data.get("components", {}).values():
        tikz = comp.get("tikz", "")
        names = out.setdefault(tikz, set())
        for p in comp.get("pins", []):
            names.add(p.get("name", ""))             # coordinate-connected pins
            a = p.get("anchor")
            if a:
                names.add(a)
                names.add(a.lstrip("-").split(".")[-1])   # sub-node tail, e.g. midtap
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract CircuiTikZ component anchors from the manual source.")
    ap.add_argument("--format", choices=["report", "md", "latex"], default="report",
                    help="report: Heaviside diagnostic (default); "
                         "md/latex: full component-and-anchor catalog table")
    ap.add_argument("--geo", action="store_true",
                    help="(md/latex) include the standard geographical anchors")
    ap.add_argument("--probe", action="store_true",
                    help="(md/latex) compile each component (latex only, no render) "
                         "to recover its COMPLETE anchor set from the engine, not "
                         "just what the manual lists (~20s; needs latex)")
    ap.add_argument("--options", action="store_true",
                    help="(md/latex) also render-probe which options each component "
                         "supports, e.g. bodydiode (implies --probe; needs "
                         "latex+dvisvgm; a few minutes)")
    ap.add_argument("--missing", action="store_true",
                    help="(report) show only anchors not yet exposed by a matching component")
    ap.add_argument("--manual", type=Path, default=None,
                    help="path to circuitikzmanual.tex (default: locate via kpsewhich)")
    args = ap.parse_args()

    manual_path = args.manual or _find_manual()
    if not manual_path or not manual_path.is_file():
        print("could not locate circuitikzmanual.tex (pass --manual PATH)", file=sys.stderr)
        return 1

    doc = manual_path.read_text(encoding="utf-8", errors="replace")

    if args.format != "report":
        catalog = extract_catalog(doc, keep_geo=args.geo)
        if args.probe or args.options:
            print("Render-probing components (this is slow)…", file=sys.stderr)
            run_probe(catalog, do_options=args.options,
                      log=lambda m: print(m, file=sys.stderr))
        text = (format_markdown(catalog, geo=args.geo) if args.format == "md"
                else format_latex(catalog, geo=args.geo))
        print(text)
        return 0

    documented = extract(doc)
    exposed = _exposed_anchors()

    print(f"# CircuiTikZ documented anchors  (source: {manual_path})")
    print(f"# {len(documented)} components carry special (non-geographical) anchors.\n")
    for key in sorted(documented):
        anchors = documented[key]
        have = exposed.get(key, set())
        marks = [(a, ("ok" if a in have else "MISSING")) for a in anchors]
        if args.missing and all(s == "ok" for _, s in marks):
            continue
        shown = ", ".join(f"{a} [{s}]" for a, s in marks)
        matched = "" if key in exposed else "  (no component with this tikz keyword)"
        print(f"{key:32s} {shown}{matched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
