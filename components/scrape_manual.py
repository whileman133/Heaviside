#!/usr/bin/env python3
r"""Scrape the CircuiTikZ manual into a structured component database.

*** PROTOTYPE — parse only, no rendering. Reports/serialises, never mutates the
component data files. ***

The CircuiTikZ manual (``circuitikzmanual.tex``) is the authoritative, hand-curated
description of every component, anchor and option. This tool treats it as the source
of truth and scrapes it into one JSON record per component:

    {
      "keyword":     "npn",            # the to[]/node[] keyword (base, no styles)
      "type":        "node",           # "node" | "path"
      "category":    "Transistors",    # the manual subsection it is documented under
      "section":     "The components: list",
      "description": "...",            # the human description (raw manual LaTeX)
      "aliases":     "...",            # circuitdescbip alias list, if any
      "shape":       "...",            # explicit pgf shape name, if the macro gave one
      "anchors":     ["B", "C", "E", ...],          # documented (…) anchor list
      "subnode_anchors": ["L1.midtap", ...],         # documented […] sub-node list
      "options":     ["schottky base", "bodydiode", "photo"],  # see below
      "parameters":  ["collectors", "emitters"],     # options that take a value (=N)
    }

Confidence tiers (the manual documents these three things with decreasing rigour):

* **components / anchors** — fully structured: the ``\circuitdesc`` (node) and
  ``\circuitdescbip`` (path) macros carry the keyword, description, aliases, and the
  ``(…)``/``[…]`` anchor lists. High confidence.
* **per-component options / parameters** — from two places: the *comma decorations*
  the manual puts on a demonstrated keyword (``\circuitdesc{npn, schottky base}``,
  ``\circuitdesc{bjtnpn, collectors=1, emitters=2}``) **and** the option lists of
  ``node[…]``/``to[…]`` *draw examples* throughout the manual (this is the only place
  some keys appear — e.g. logic gates' ``number inputs=N``). A ``key=value`` form is a
  parameter; a bare key is a boolean option. A generic example keyword (``and port``)
  is attributed to every styled variant it stands for (``american``/``european``/
  ``ieeestd and port``). Matches inside inline code shown *as text* (``\texttt{…}``,
  ``\verb``, ``\lstinline``, ``verbatim``/``lstlisting``) are ignored — those are
  documentation like ``\texttt{node[\emph{component},...]}``, not real commands. High
  confidence, but only covers options the manual chose to *demonstrate*.
* **per-category option candidates** — harvested from the prose of each subsection
  (``\ctikzset{…}`` keys and option-like ``\texttt{…}`` tokens). The manual describes
  most options in running text scoped to a family ("for all transistors", "the
  *igfet* family"), so these are attached to the **category**, not individual
  components, and are best-effort (noisy). Emitted under ``category_options``.

Options have *no* declarative component→option mapping anywhere in CircuiTikZ (they
are ``\if…`` flags tested inside the drawing code), so the comma-decorations plus the
prose harvest are the most the manual alone can give.

**Optional source probe** (``--probe`` / ``--probe-options`` / ``--probe-params``).
After the manual scrape, each component can be *compiled* to recover from the engine
what the manual under-documents, writing extra ``probed_*`` fields onto the record
(it reuses the probe machinery in ``extract_doc_anchors``):

* ``--probe`` — the shape's **complete anchor set** (``probed_anchors``), read from the
  pgf symbol table with ``latex`` only (no rendering). Fills the manual's gaps, e.g.
  the gate siblings the manual leaves ``not listed``. ~20 s for the whole library.
* ``--probe-options`` — the options that **visibly change** each symbol
  (``probed_options``), by rendering it with every candidate option and diffing the
  ink. Needs ``latex`` + ``dvisvgm``; minutes for the whole library.
* ``--probe-params`` — which known parameters **apply** to each component
  (``probed_parameters``), by rendering ``<kw>, <param>=<probe value>`` and diffing.
  Verifies/propagates the manual's parameters (e.g. ``number inputs`` to every gate).
  Needs ``latex`` + ``dvisvgm``.

    python components/scrape_manual.py                  # summary to stderr, JSON to stdout
    python components/scrape_manual.py --summary         # just the summary
    python components/scrape_manual.py --component npn    # one component, pretty-printed
    python components/scrape_manual.py > circuitikz.json
    python components/scrape_manual.py --format md > components.md   # scannable table
    python components/scrape_manual.py --format md --probe > components.md   # + full anchors
    python components/scrape_manual.py --probe --probe-options --probe-params > deep.json

Reuses the LaTeX parser and render probes from ``extract_doc_anchors`` (sibling
module). The parse-only modes need just the manual source on disk; the ``--probe*``
modes need the LaTeX toolchain.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))   # import the sibling module
import extract_doc_anchors as eda                            # noqa: E402

# ---------------------------------------------------------------------------
# Section / category tracking
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(r"\\section\*?\{")
_SUBSECTION_RE = re.compile(r"\\subsection\*?\{")
# The section whose subsections are the component *categories*.
_COMPONENT_LIST_SECTION = "The components: list"


def _headings(manual: str, pattern: re.Pattern) -> list[tuple[int, str]]:
    """``(position, title)`` for every heading matching *pattern* (title de-TeX'd)."""
    out: list[tuple[int, str]] = []
    for m in pattern.finditer(manual):
        title, _ = eda._read_balanced(manual, m.end() - 1, "{", "}")
        out.append((m.start(), eda._clean_tex(title)))
    return out


def _locate(pos: int, headings: list[tuple[int, str]]) -> str:
    """The title of the heading immediately preceding *pos* (``""`` if none)."""
    title = ""
    for hpos, htitle in headings:
        if hpos <= pos:
            title = htitle
        else:
            break
    return title


# ---------------------------------------------------------------------------
# Keyword decoration → base keyword + options/parameters
# ---------------------------------------------------------------------------

def _split_keyword(field: str) -> tuple[str, list[str], list[str]]:
    """Decompose a demonstrated keyword field into ``(base, options, parameters)``.

    The manual decorates a keyword with the styles/parameters it wants to show, e.g.
    ``npn, schottky base`` → base ``npn`` + option ``schottky base``; ``bjtnpn,
    collectors=1, emitters=2`` → base ``bjtnpn`` + parameters ``collectors``,
    ``emitters``. The base is everything before the first comma; each remaining,
    comma-separated decoration is a parameter (it contains ``=``) or a boolean
    option."""
    parts = [p.strip() for p in field.split(",")]
    base = parts[0]
    options: list[str] = []
    parameters: list[str] = []
    for deco in parts[1:]:
        if not deco:
            continue
        if "=" in deco:
            key = deco.split("=", 1)[0].strip()
            if key and not _reject_key(key):
                parameters.append(key)
        elif not _reject_key(deco):
            options.append(deco)
    return base, options, parameters


# ---------------------------------------------------------------------------
# Prose option harvesting (per category, best-effort)
# ---------------------------------------------------------------------------

# A `\texttt{…}` token is an option *candidate* when it looks like a key: lowercase,
# short, words/slashes/spaces only (no math, no version numbers, no sentences).
_OPTION_TOKEN = re.compile(r"^[a-z][a-z0-9]*(?:[ /][a-z0-9]+)*$")
_CTIKZSET_KEY = re.compile(r"\\ctikzset\{\s*([a-z][a-z0-9 /]*?)\s*(?:=|/\.|,|\})")


def _harvest_prose_options(text: str, exclude: set[str]) -> list[str]:
    """Option-like keys mentioned in a chunk of manual prose: ``\\ctikzset{key}``
    keys and option-shaped ``\\texttt{…}`` tokens, reduced to their leaf (a slashed
    path ``tripoles/mos style/arrows`` → ``arrows``). De-duplicated, order preserved.
    *exclude* drops known non-options (component keywords, label slots, dimension and
    styling keys). Best-effort — candidates, not ground truth."""
    found: list[str] = []

    def add(tok: str) -> None:
        cand = " ".join(tok.split("/")[-1].split())     # leaf, whitespace-collapsed
        if not (_OPTION_TOKEN.match(cand) and 2 <= len(cand) <= 24):
            return
        if (cand in found or cand in exclude or eda._is_geo(cand)
                or cand in _PROSE_OPTION_STOP
                or cand in eda._OPTION_EXCLUDE_EXACT
                or any(s in cand for s in eda._OPTION_EXCLUDE_SUBSTR)):
            return
        found.append(cand)

    for m in _CTIKZSET_KEY.finditer(text):
        add(m.group(1))
    for m in re.finditer(r"\\texttt\{([^{}]*)\}", text):
        add(m.group(1))
    return found


# Words that show up in option-shaped \texttt tokens but are never component options
# (label slots, package/structural words, units). Pruned from the prose harvest.
_PROSE_OPTION_STOP = {
    "and", "or", "not", "the", "to", "with", "see", "section", "here", "true",
    "false", "default", "node", "draw", "path", "style", "color", "fill", "left",
    "right", "above", "below", "center", "circuitikz", "tikz", "american",
    "european", "options", "option", "key", "keys", "name", "scale",
}


# ---------------------------------------------------------------------------
# Draw-example option/parameter harvesting
#
# Some options/parameters are never put on a \circuitdesc keyword — the manual only
# shows them in running ``\draw … node[<kw>, <opt>=<val>] …`` / ``to[<kw>, …]``
# examples (e.g. logic gates' ``number inputs=N``). Scan those node/to option lists
# and attribute the options to the keyword they decorate.
# ---------------------------------------------------------------------------

# tikz positioning/styling keys that show up in example node[] lists but are not
# component options. (Component-option excludes from ``eda`` are also applied; the
# dimension *substring* filter is deliberately NOT, so ``number inputs`` survives.)
_NODE_OPT_EXCLUDE = {
    "name", "anchor", "at", "rotate", "scale", "xscale", "yscale", "shift",
    "xshift", "yshift", "pos", "every", "font", "align", "opacity", "overlay",
    "circuitikz", "node", "coordinate", "inner sep", "outer sep", "minimum width",
    "minimum height", "text width", "line width", "rounded corners",
    "transform shape", "node distance", "label", "thick", "thin",
    # relative-positioning keys (node[X, below right=of Y]) — placement, not options
    "above", "below", "above right", "above left", "below right", "below left",
    "right of", "left of", "above of", "below of",
    # common xcolor names (a coloured example node, e.g. node[circ, red], is not an
    # option of the component)
    "red", "green", "blue", "cyan", "magenta", "yellow", "black", "white", "gray",
    "grey", "orange", "violet", "purple", "brown", "pink", "lime", "olive", "teal",
}


def _reject_key(key: str) -> bool:
    """Whether *key* is not a real component option/parameter (positioning/styling
    key, label slot, geometry, color/opacity). Shared by the keyword-decoration
    parser, the draw-example harvester, and the probe pool so the three stay
    consistent."""
    return (key in _NODE_OPT_EXCLUDE or key in _PROSE_OPTION_STOP
            or key in eda._OPTION_EXCLUDE_EXACT or eda._is_geo(key)
            or any(s in key for s in ("opacity", "color")))


# A real node[]/to[] option list is short; anything longer is a false match (e.g. a
# ``to[\emph{component},...]`` written *as prose inside* a ``\texttt{…}``), so cap the
# scan to avoid runaway captures.
_MAX_BRACKET = 400


def _read_bracket(text: str, i: int) -> str | None:
    """Read a ``[ … ]`` group at ``text[i] == '['``, skipping ``{ … }`` groups (so a
    ``]`` inside a brace argument doesn't end it early). Returns the inner text, or
    ``None`` when the ``[`` is *not* a well-formed bracket within ``_MAX_BRACKET``
    chars — in particular when a ``}`` drives the brace count negative, which means
    the ``[`` sits inside an enclosing brace group (prose/verbatim, not a real
    command) and the matching ``]`` would otherwise be mis-found far downstream."""
    depth = brace = 0
    start = i + 1
    end = min(len(text), start + _MAX_BRACKET)
    while i < end:
        c = text[i]
        if c == "{":
            brace += 1
        elif c == "}":
            brace -= 1
            if brace < 0:                 # closing an *enclosing* group → malformed
                return None
        elif brace == 0:
            if c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    return text[start:i]
        i += 1
    return None                            # no close within the cap → not a real list


def _split_top(s: str) -> list[str]:
    """Split *s* on top-level commas (not inside ``{}`` or ``[]``)."""
    parts, buf, brace, brack = [], "", 0, 0
    for c in s:
        if c == "{":
            brace += 1
        elif c == "}":
            brace -= 1
        elif c == "[":
            brack += 1
        elif c == "]":
            brack -= 1
        if c == "," and brace == 0 and brack == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += c
    parts.append(buf)
    return [p.strip() for p in parts]


def _keyword_targets(token: str, components: dict) -> list[str]:
    """Component(s) an example's first token refers to: the keyword before any ``=``
    (a ``to[R=$R_1$]`` label), matched exactly, or — for a generic multi-word keyword
    like ``and port`` — every styled variant (``american``/``european``/``ieeestd
    and port``) it stands for."""
    name = " ".join(token.split("=", 1)[0].split())
    if not name:
        return []
    if name in components:
        return [name]
    if " " in name:        # generic → styled variants (logic ports, etc.)
        return [k for k in components if k.endswith(" " + name)]
    return []


def _classify_node_opt(token: str, components: dict) -> tuple[str | None, bool]:
    """``(key, is_parameter)`` for an example option token, or ``(None, False)`` if it
    is not a component option (positioning/styling key, label slot, geometry, or a
    component keyword). ``is_parameter`` is true for a ``key=value`` form."""
    is_param = "=" in token
    key = " ".join(token.split("=", 1)[0].split())
    if not (_OPTION_TOKEN.match(key) and 2 <= len(key) <= 24):
        return None, False
    if _reject_key(key) or key in components:
        return None, False
    return key, is_param


def _protected_spans(manual: str) -> list[tuple[int, int]]:
    """Character ranges holding *literal code shown as text* — inline ``\\texttt{…}``,
    ``\\verb<c>…<c>``, ``\\lstinline…``, and ``verbatim``/``lstlisting`` environments.
    A ``node[…]``/``to[…]`` inside one of these is documentation (e.g.
    ``\\texttt{node[\\emph{component},...]}``), not a real drawing command, so the
    harvester skips it. Real, rendered examples (``LTXexample``/``circuitikz`` bodies)
    are *not* protected — their code is genuine and we want it."""
    spans: list[tuple[int, int]] = []
    # verbatim / listing environments (multi-line)
    for m in re.finditer(r"\\begin\{(verbatim|Verbatim|lstlisting)\}.*?\\end\{\1\}",
                         manual, re.DOTALL):
        spans.append((m.start(), m.end()))
    # \verb<c>…<c> / \lstinline<c>…<c>  (c a punctuation delimiter, single line)
    for m in re.finditer(
            r"\\(?:verb|lstinline)\*?(?:\[[^\]]*\])?([^\sA-Za-z0-9{}]).*?\1", manual):
        spans.append((m.start(), m.end()))
    # brace-delimited inline code: \texttt{…} / \lstinline{…} (balanced)
    for m in re.finditer(r"\\(?:texttt|lstinline)\*?(?:\[[^\]]*\])?\{", manual):
        _inner, end = eda._read_balanced(manual, m.end() - 1, "{", "}")
        spans.append((m.start(), end))
    return sorted(spans)


def _in_spans(pos: int, starts: list[int], spans: list[tuple[int, int]]) -> bool:
    """Whether *pos* falls inside any (mostly disjoint) protected span."""
    import bisect
    i = bisect.bisect_right(starts, pos) - 1
    return i >= 0 and spans[i][0] <= pos < spans[i][1]


def _local_styles(manual: str) -> set[str]:
    """Names of styles the manual *defines for an example* via
    ``\\tikzset{<name>/.style=…}`` / ``\\ctikzset{<name>/.style=…}`` (e.g. a one-off
    ``red plus``). These are author conveniences, not component options, so the
    harvester must not attribute them to a component."""
    return {re.sub(r"/\.style.*", "", re.sub(r".*\{", "", m))
            for m in re.findall(r"\\c?tikzset\{[a-z][a-z0-9 ]*?/\.style", manual)}


def _harvest_node_examples(manual: str, components: dict) -> int:
    """Attribute options/parameters from ``node[…]``/``to[…]`` draw examples to their
    keyword, mutating the component records. Returns how many were added. Skips
    matches inside protected (inline-code/verbatim) spans, and example-local styles."""
    added = 0
    local = _local_styles(manual)
    spans = _protected_spans(manual)
    starts = [s for s, _ in spans]
    for m in re.finditer(r"(?<![A-Za-z])(?:node|to)\[", manual):
        if _in_spans(m.start(), starts, spans):       # documentation, not a command
            continue
        content = _read_bracket(manual, m.end() - 1)
        if content is None:               # not a well-formed option list (prose, etc.)
            continue
        parts = _split_top(content)
        targets: list[str] = []
        kw_idx = None
        for i, p in enumerate(parts):           # the keyword is the first token that
            targets = _keyword_targets(p, components)   # resolves to a component
            if targets:
                kw_idx = i
                break
        if kw_idx is None:
            continue
        for j, tok in enumerate(parts):
            if j == kw_idx:
                continue
            key, is_param = _classify_node_opt(tok, components)
            if not key or key in local:         # skip example-local custom styles
                continue
            for name in targets:
                bucket = components[name]["parameters" if is_param else "options"]
                if key not in bucket:
                    bucket.append(key)
                    added += 1
    return added


# ---------------------------------------------------------------------------
# Main scrape
# ---------------------------------------------------------------------------

def scrape(manual: str) -> dict:
    """Parse *manual* into ``{"components": {kw: record}, "category_options": {...}}``."""
    sections = _headings(manual, _SECTION_RE)
    subsections = _headings(manual, _SUBSECTION_RE)
    components: dict[str, dict] = {}

    def record(base: str, *, type_: str, pos: int) -> dict:
        sect = _locate(pos, sections)
        cat = _locate(pos, subsections)
        in_list = sect == _COMPONENT_LIST_SECTION
        r = components.get(base)
        if r is None:
            r = components[base] = {
                "keyword": base, "type": type_,
                "section": sect, "category": cat,
                "description": "", "aliases": "", "shape": "",
                "anchors": [], "subnode_anchors": [],
                "options": [], "parameters": [],
                "_cat_authoritative": in_list,
            }
        elif in_list and not r["_cat_authoritative"]:
            # A component is often demonstrated in a usage/tutorial section before
            # its reference entry; the reference list section gives the real category.
            r["section"], r["category"], r["_cat_authoritative"] = sect, cat, True
        return r

    def merge(dst: list, src: list) -> None:
        for x in src:
            if x not in dst:
                dst.append(x)

    # --- \circuitdesc{s O{1} m m m d() d[]}  — node components -----------------
    for m in re.finditer(r"\\circuitdesc(?![a-zA-Z])", manual):
        parsed = eda._parse_args(manual, m.end(), ["s", "o", "m", "m", "m", "p", "q"])
        if not parsed:
            continue
        a, _ = parsed
        base, opts, params = _split_keyword(a.get(2, ""))
        if not base:
            continue
        r = record(base, type_="node", pos=m.start())
        if not r["description"]:
            r["description"] = eda._clean_tex(a.get(3))
        if a.get(1):
            r["shape"] = r["shape"] or base
        merge(r["anchors"], eda._anchor_names(a.get(5)))
        merge(r["subnode_anchors"], eda._anchor_names(a.get(6)))
        merge(r["options"], opts)
        merge(r["parameters"], params)

    # --- \circuitdescbip{s o m d<> m m d() d[]}  — path bipoles ----------------
    for m in re.finditer(r"\\circuitdescbip\*?", manual):
        parsed = eda._parse_args(manual, m.end(), ["o", "m", "a", "m", "m", "p", "q"])
        if not parsed:
            continue
        a, _ = parsed
        base, opts, params = _split_keyword(a.get(1, ""))
        if not base:
            continue
        r = record(base, type_="path", pos=m.start())
        if not r["description"]:
            r["description"] = eda._clean_tex(a.get(3))
        if not r["aliases"]:
            r["aliases"] = eda._clean_tex(a.get(4))
        if a.get(0):
            r["shape"] = r["shape"] or a.get(0).strip()
        merge(r["anchors"], eda._anchor_names(a.get(5)))
        merge(r["subnode_anchors"], eda._anchor_names(a.get(6)))
        merge(r["options"], opts)
        merge(r["parameters"], params)

    # --- \showanchors demos enrich a matching component's anchors --------------
    for m in re.finditer(r"\\showanchors(?![a-zA-Z])", manual):
        parsed = eda._parse_args(manual, m.end(), ["o", "m", "m", "p"])
        if not parsed:
            continue
        a, _ = parsed
        base, _o, _p = _split_keyword(eda._first_token(a.get(1, "")))
        if base in components:
            merge(components[base]["anchors"], eda._anchor_names(a.get(3)))

    # --- options/parameters demonstrated in draw examples (not on the macro) ---
    _harvest_node_examples(manual, components)
    for r in components.values():        # tidy: stable order; a key is an option XOR a
        r["options"].sort()              # parameter — if seen both ways, keep it an option
        r["parameters"] = sorted(p for p in set(r["parameters"]) if p not in r["options"])

    # --- per-category prose option candidates ---------------------------------
    # Exclude tokens that are themselves component keywords (a keyword mentioned in
    # prose is a cross-reference, not an option) or known anchor names (an anchor
    # mentioned in prose is not an option) — leaving genuine style/option keys.
    exclude = set(components)
    for r in components.values():
        exclude.update(r["anchors"])
        exclude.update(r["subnode_anchors"])
    category_options = _scrape_category_options(manual, sections, subsections, exclude)

    for r in components.values():        # drop the internal bookkeeping flag
        r.pop("_cat_authoritative", None)
    return {"components": components, "category_options": category_options}


def _scrape_category_options(manual: str, sections, subsections,
                             exclude: set[str]) -> dict[str, list[str]]:
    """Harvest option candidates from each subsection's prose, keyed by category.
    Only subsections inside the component-list section are considered."""
    # Span of the component-list section.
    start = next((p for p, t in sections if t == _COMPONENT_LIST_SECTION), None)
    if start is None:
        return {}
    end = next((p for p, _ in sections if p > start), len(manual))
    subs = [(p, t) for p, t in subsections if start <= p < end]
    out: dict[str, list[str]] = {}
    for i, (p, title) in enumerate(subs):
        chunk_end = subs[i + 1][0] if i + 1 < len(subs) else end
        opts = _harvest_prose_options(manual[p:chunk_end], exclude)
        if opts:
            out[title] = opts
    return out


def _demacro(s: str) -> str:
    """Light de-TeX of a description for human-readable Markdown (the JSON keeps the
    raw manual LaTeX). Unwraps common inline font commands, drops cross-references,
    and normalises quotes/spacing."""
    for _ in range(2):       # twice handles one level of nesting
        s = re.sub(r"\\(?:texttt|textsc|emph|textbf|textit|text|mbox)\{([^{}]*)\}",
                   r"\1", s)
    s = re.sub(r"\\href\{[^{}]*\}\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\(?:label|footnote)\{[^{}]*\}", "", s)
    # cross-references: drop the ref *and* a now-dangling "see/section ~" connector
    # before it ("…, see~\ref{sec:hemt})" → "…)").
    s = re.sub(r"[,;]?\s*(?:see|section|sec\.?|cf\.?)?\s*~?"
               r"\\(?:ref|cref|Cref|pageref)\{[^{}]*\}", "", s)
    s = re.sub(r"\\(?:dots|ldots|cdots)\b", "…", s)
    # font/size switch commands (no argument), e.g. ``\scshape npn`` → ``npn``
    s = re.sub(r"\\(?:scshape|itshape|bfseries|mdseries|upshape|slshape|ttfamily|"
               r"sffamily|rmfamily|normalfont|em|tiny|scriptsize|footnotesize|small|"
               r"normalsize|large|Large|LARGE|huge|Huge)\b\s*", "", s)
    # common math symbols → Unicode (control word ends at a non-letter)
    for name, sym in {"pi": "π", "mu": "µ", "Omega": "Ω", "ohm": "Ω", "to": "→",
                      "times": "×", "cdot": "·", "pm": "±", "infty": "∞",
                      "alpha": "α", "beta": "β", "omega": "ω"}.items():
        s = re.sub(rf"\\{name}(?![a-zA-Z])", sym, s)
    s = s.replace("^3", "³").replace("^2", "²").replace("$", "")
    s = s.replace("``", '"').replace("''", '"').replace("~", " ")
    s = re.sub(r"\\[,;:!> ]", " ", s)               # thin/control spaces
    s = re.sub(r"\s+([)\].,])", r"\1", s)           # tidy space before punctuation
    return re.sub(r"\s+", " ", s).strip()


def format_markdown(db: dict) -> str:
    """A scannable Markdown report: components grouped under their category heading,
    one table per category, with the per-category prose option candidates noted."""
    comps = db["components"]
    cat_options = db["category_options"]
    probed = any("probed_anchors" in c or "probed_options" in c
                 or "probed_parameters" in c for c in comps.values())
    # Category order = first appearance in the manual (dict is in manual order).
    cat_order: list[str] = []
    for c in comps.values():
        if c["category"] not in cat_order:
            cat_order.append(c["category"])

    src = ("manual + source probe (anchors/options/parameters recovered by compiling "
           "each component)" if probed else "manual only")
    out = [
        "# CircuiTikZ components (scraped from the manual)",
        "",
        f"{len(comps)} components across {len(cat_order)} categories, scraped from "
        f"`circuitikzmanual.tex` by `components/scrape_manual.py` ({src}). Options and "
        "parameters come from the reference macros and `node[…]`/`to[…]` draw examples; "
        "the *family option candidates* under each heading are a best-effort harvest of "
        "the section prose (noisy — candidates, not ground truth).",
        "",
    ]
    if not probed:
        out += [
            "> **Anchors = _not listed_** means the manual's entry did not enumerate "
            "anchors there — usually because a representative sibling does (e.g. only "
            "the `and`/`or` gates list theirs). It does *not* mean the component has no "
            "anchors; run with `--probe` for the engine's complete per-component set.",
            "",
        ]
    for cat in cat_order:
        members = sorted((c for c in comps.values() if c["category"] == cat),
                         key=lambda c: c["keyword"].lower())
        out.append(f"## {cat}  ({len(members)})")
        out.append("")
        prose = cat_options.get(cat)
        if prose:
            out.append(f"*Family option candidates (prose): {', '.join(prose)}*")
            out.append("")
        out.append("| Component | Keyword | Type | Anchors | Options | Parameters |")
        out.append("|---|---|---|---|---|---|")
        for c in members:
            row = [
                eda._md_cell(_demacro(c["description"]) or c["keyword"]),
                eda._md_cell(f"`{c['keyword']}`"),
                "path" if c["type"] == "path" else "node",
                _anchor_cell(c), _option_cell(c), _param_cell(c),
            ]
            out.append("| " + " | ".join(row) + " |")
        out.append("")
    return "\n".join(out)


def _anchor_cell(c: dict) -> str:
    """Anchors for the table: the probed (complete, geo-filtered) set when present,
    else the manual's documented set (``*not listed*`` when it enumerated none)."""
    if "probed_anchors" in c:
        names = [a for a in c["probed_anchors"] if not eda._is_geo(a)]
        names += [f"{a} (sub-node)" for a in c["subnode_anchors"]]
        return eda._md_cell(", ".join(names)) if names else "—"
    anchors = list(c["anchors"]) + [f"{a} (sub-node)" for a in c["subnode_anchors"]]
    return eda._md_cell(", ".join(anchors)) if anchors else "*not listed*"


def _option_cell(c: dict) -> str:
    opts = sorted(set(c["options"]) | set(c.get("probed_options", [])))
    return eda._md_cell(", ".join(opts)) if opts else "—"


def _param_cell(c: dict) -> str:
    opts = set(c["options"]) | set(c.get("probed_options", []))
    params = sorted((set(c["parameters"]) | set(c.get("probed_parameters", []))) - opts)
    return eda._md_cell(", ".join(params)) if params else "—"


# ---------------------------------------------------------------------------
# Optional source probe (compiles components to recover what the manual omits).
# Reuses the probes in extract_doc_anchors; runs after the manual scrape.
# ---------------------------------------------------------------------------

def _anchor_pool(db: dict) -> list[str]:
    """Candidate anchor names for the probe: every anchor the manual mentions across
    all components (plus the sub-node tails) and the standard geometric set."""
    pool: list[str] = []
    for c in db["components"].values():
        for a in c["anchors"] + c["subnode_anchors"]:
            for name in (a, a.split(".")[-1]):
                if name not in pool:
                    pool.append(name)
    for g in sorted(eda._GEO):
        if g not in pool:
            pool.append(g)
    return pool


def _probe_value(param: str) -> str:
    """A value to try when probing whether *param* applies. Most node parameters are
    counts/dimensions, so a small integer exercises them; a param that needs another
    value type just fails to compile and is reported as not-applicable."""
    return "3"


def _probe_parameters(keyword: str, kind: str, params: list[str],
                      base: tuple | None) -> list[str]:
    """Which of *params* compile and change *keyword*'s geometry when given a value
    (vs the *base* signature) — i.e. actually apply to this component."""
    if base is None:
        return []
    out: list[str] = []
    for p in params:
        if not eda._SAFE_NAME.match(p):
            continue
        sig = eda._geometry_signature(keyword, kind, f"{p}={_probe_value(p)}")
        if sig is not None and sig != base:
            out.append(p)
    return out


def run_source_probe(db: dict, *, anchors: bool, options: bool, params: bool,
                     only: set[str] | None = None, workers: int = 12,
                     log=lambda msg: None) -> None:
    """Enrich the scraped *db* in place with engine-probed ``probed_anchors`` /
    ``probed_options`` / ``probed_parameters`` (whichever flags are set). Candidate
    pools are drawn from the whole *db*; *only* restricts which components are probed
    (e.g. for ``--component``). Parallel across components; render is per-temp-dir
    thread-safe (like ``_probe.py``)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    targets = [c for c in db["components"].values()
               if only is None or c["keyword"] in only]
    comps = db["components"]
    anchor_pool = _anchor_pool(db) if anchors else []
    option_pool: list[str] = []
    if options:
        pkg = eda.find_package_dir()
        option_pool = eda.harvest_options(pkg) if pkg else []
    param_pool = (sorted({p for c in comps.values() for p in c["parameters"]
                          if not _reject_key(p)})
                  if params else [])
    log(f"  source-probing {len(targets)} components"
        + (f" · {len(anchor_pool)} anchor names" if anchors else "")
        + (f" · {len(option_pool)} options" if options else "")
        + (f" · {len(param_pool)} parameters" if params else "")
        + f" ({workers} workers)…")

    def work(c: dict) -> tuple[str, dict]:
        kw, kind = c["keyword"], c["type"]
        res: dict = {}
        if anchors:
            a = eda.probe_anchors(kw, kind, anchor_pool)
            if a is not None:
                res["probed_anchors"] = a
        if options or params:
            base = eda._geometry_signature(kw, kind)
            if base is not None:
                if options:
                    res["probed_options"] = eda.probe_options(kw, kind, option_pool, base)
                if params:
                    res["probed_parameters"] = _probe_parameters(kw, kind, param_pool, base)
        return kw, res

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(work, c): c for c in targets}
        done = 0
        for fut in as_completed(futures):
            kw, res = fut.result()
            comps[kw].update(res)
            done += 1
            if done % 25 == 0 or done == len(targets):
                log(f"    {done}/{len(targets)} probed")


def _print_summary(db: dict, out=sys.stderr) -> None:
    comps = db["components"]
    nodes = sum(1 for c in comps.values() if c["type"] == "node")
    paths = len(comps) - nodes
    with_anchors = sum(1 for c in comps.values() if c["anchors"])
    with_opts = sum(1 for c in comps.values() if c["options"] or c["parameters"])
    cats: dict[str, int] = {}
    for c in comps.values():
        cats[c["category"]] = cats.get(c["category"], 0) + 1
    print(f"Scraped {len(comps)} components "
          f"({paths} path-style, {nodes} node-style) "
          f"in {len(cats)} categories.", file=out)
    print(f"  {with_anchors} have documented anchors; "
          f"{with_opts} have demonstrated options/parameters.", file=out)
    print(f"  {sum(len(v) for v in db['category_options'].values())} prose option "
          f"candidates across {len(db['category_options'])} categories.", file=out)
    pa = sum(1 for c in comps.values() if "probed_anchors" in c)
    po = sum(1 for c in comps.values() if c.get("probed_options"))
    pp = sum(1 for c in comps.values() if c.get("probed_parameters"))
    if pa or po or pp:
        print(f"  source-probed: {pa} with anchors, {po} with options, "
              f"{pp} with parameters.", file=out)
    print("  Components per category:", file=out)
    for cat, n in sorted(cats.items(), key=lambda kv: -kv[1]):
        print(f"    {n:4d}  {cat}", file=out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scrape the CircuiTikZ manual into a structured component DB.")
    ap.add_argument("--format", choices=["json", "md"], default="json",
                    help="stdout format: json (default) or md (a scannable table "
                         "grouped by category)")
    ap.add_argument("--summary", action="store_true",
                    help="print only the human summary (no JSON/MD on stdout)")
    ap.add_argument("--component", metavar="KEYWORD",
                    help="pretty-print a single component's record and exit")
    ap.add_argument("--probe", action="store_true",
                    help="source probe: add each shape's COMPLETE anchor set "
                         "(latex only, ~20s)")
    ap.add_argument("--probe-options", action="store_true",
                    help="source probe: add the options that change each symbol "
                         "(latex+dvisvgm, slow)")
    ap.add_argument("--probe-params", action="store_true",
                    help="source probe: add which known parameters apply to each "
                         "component (latex+dvisvgm, slow)")
    ap.add_argument("--manual", type=Path, default=None,
                    help="path to circuitikzmanual.tex (default: locate via kpsewhich)")
    args = ap.parse_args()

    manual_path = args.manual or eda._find_manual()
    if not manual_path or not manual_path.is_file():
        print("could not locate circuitikzmanual.tex (pass --manual PATH)", file=sys.stderr)
        return 1
    db = scrape(manual_path.read_text(encoding="utf-8", errors="replace"))

    if args.probe or args.probe_options or args.probe_params:
        print("Source-probing components (this compiles them; slow)…", file=sys.stderr)
        run_source_probe(
            db, anchors=args.probe, options=args.probe_options,
            params=args.probe_params,
            only={args.component} if args.component else None,
            log=lambda m: print(m, file=sys.stderr))

    if args.component:
        rec = db["components"].get(args.component)
        if rec is None:
            print(f"no component {args.component!r} "
                  f"(have {len(db['components'])})", file=sys.stderr)
            return 1
        print(json.dumps(rec, indent=2, ensure_ascii=False))
        return 0

    _print_summary(db)
    if not args.summary:
        if args.format == "md":
            print(format_markdown(db))
        else:
            print(json.dumps(db, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
