# Heaviside — Component Editor Specification

**Version:** 0.4
**Status:** Implemented — the registry, codegen, and canvas all build from the generated data file (no hand-stored geometry magic numbers), and per-instance variants are generic. A standalone authoring GUI is an optional follow-up.
**Author:** Wes H.

This document is governed by the living-document rule in [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) §0.

---

## 1. Purpose

The brittle part of Heaviside is the per-component CircuiTikZ geometry: pin
positions, the scaling/lead corrections that make a symbol grid-aligned, and the
SVG placement anchors. Today these are **hand-measured magic numbers** scattered
across `registry.py`, `svgsym.py`, the `circuitikz` codegen tables, and the
export script (the manual ritual in PROJECT_SPEC §5.5).

**The one requirement: generate grid-aligned components from the CircuiTikZ
library without hand-storing magic numbers.** The fix is to *measure* the symbol
instead of typing measured constants, and to keep the per-component data in one
generated file instead of five hand-edited ones.

Non-goals: this covers only CircuiTikZ symbols (the `BIPOLES`/`NODES`/`TRIPOLES`
the export script renders). The 6 bespoke kinds — the resizable annotations
(`open`, `short`) and the drawing primitives (`bipole`, `rect`, `circle`,
`text_node`) — are not command-derived and keep their hand-coded definitions.

---

## 2. How it works

Two pieces, both small:

1. **A measurement tool** (`app/components/bake.py`). Given a CircuiTikZ keyword,
   it renders the symbol with `latex`/`dvisvgm` and reads each pin's **anchor
   position automatically** (via `\pgfpointanchor`), returning a grid-unit offset.
   This is PROJECT_SPEC §5.5 Step 1 done by the machine — no human reads a
   coordinate off a figure. (`latex`/`dvisvgm` are a developer-tool dependency,
   not a shipped-app one.)

2. **One renderer, two outputs** (`tools/generate_components.py`). It renders
   every symbol (and variant) and writes:
   - `tools/circuitikz_svgs/manifest.json` — the symbol *geometry* (paths/glyphs,
     read by `svgsym.py`); and
   - `components/components.json` — the registry + codegen data (pins, bbox,
     leads, metadata) plus one `origin_svg` placement constant.

The app builds its registry (`registry.py`), codegen tables (`circuitikz.py`),
and canvas placement (`svgsym.py`) from this data. Adding or re-aligning a
component is: measure → add an entry → re-run the renderer. No editing of the
registry, the codegen tables, or `svgsym`'s placement.

---

## 3. The data file

`components/components.json` is `{origin_svg, components}`, where `components`
maps each `kind` to a flat record. Example (a resistor and an op-amp):

```jsonc
{
  "origin_svg": [15.0312, 15.0312],   // SVG point that every symbol's origin pin maps to
  "components": {
    "R": {
      "display_name": "Resistor", "category": "Bipoles",
      "emission": "two_terminal", "tikz": "R",
      "labels": ["l", "l_", "v", "v^", "i", "i_"],
      "bbox": [0.0, -0.25, 2.0, 0.25],
      "pins": [{"name": "in", "offset": [0,0], "anchor": null},
               {"name": "out", "offset": [2,0], "anchor": null}]
    },
    "op amp": {
      "display_name": "Op-Amp", "category": "Tripoles",
      "emission": "multi_terminal", "tikz": "op amp", "labels": ["l"],
      "bbox": [-1.5,-1.0,1.5,1.0],
      "pins": [{"name":"+","offset":[-1.5,0.5],"anchor":"+"}, ...],
      "anchor_pin": null,             // pin the node is placed by (null = by centre)
      "leads": [{"anchor":"+","to":[-1.5,0.5]}, ...],  // bridge each pin to the grid
      "variants": [{"name":"filled","token":"*","mode":"suffix"}]  // optional
    }
  }
}
```

Fields:

| Field | Meaning |
|-------|---------|
| `origin_svg` (top level) | The single SVG point that every symbol's origin pin maps to — see §4. |
| `display_name`, `category`, `labels` | Palette metadata + valid options-string slots. |
| `emission` | `two_terminal` (`to[…]`), `node` (single-terminal `node[…]`), or `multi_terminal` (`node[…, anchor=…]` + leads). |
| `tikz` | The CircuiTikZ keyword. |
| `bbox` | Bounding box `(x0,y0,x1,y1)` in GU (a design choice, kept snug for label placement). |
| `pins` | Each pin: `name`, grid `offset` (GU, multiple of 0.25), and the CircuiTikZ `anchor` it maps to (`null` for two-terminal/node, whose pins are the draw endpoints). |
| `anchor_pin`, `leads` | **Alignment** for multi-terminal symbols — see §4. Computed, never hand-typed. |
| `variants` | Boolean modifiers: `{name, token, mode}` where `mode` is `suffix` (`D`→`D*`) or `option` (append `, bodydiode`). Generalises the diode `filled` and MOSFET `body_diode` flags. |

`default_span` and `resizable` are derived (terminal-minus-origin for a two-pin
device; library kinds are never resizable), so they are not stored.

---

## 4. Alignment

Alignment is **lead-only** — one mechanism, no per-component scale. Every symbol
is rendered inside a **fixed bounding box** with its origin pin placed at TeX
`(0,0)`; a short `\draw (node.anchor) -- (grid)` lead then bridges every other
pin to its registry grid offset. Two consequences:

- **Placement is one constant.** Because the origin pin is always at TeX `(0,0)`
  and the bounding box is fixed, TeX origin maps to a single SVG point —
  `origin_svg` — for *every* symbol. The canvas transform (`svgsym.py`) is just
  `translate(-origin_svg)` then a uniform scale: no per-component anchors,
  rotation, or scale corrections.
- **The same leads drive canvas and LaTeX.** The leads are baked into the
  manifest geometry (canvas) and emitted by the codegen (LaTeX) from the same
  data, so the two agree by construction.

This replaces the former design, which used hand-measured per-component SVG
anchors plus a confusable mix of `xscale`/`yscale` corrections and bridge leads
(PROJECT_SPEC §5.5). Lead-only is simpler and fully derived from the measurements;
the trade-off is that symbols whose CircuiTikZ body is smaller than its grid span
(MOSFETs, BJTs) show a short lead stub instead of a stretched body.

---

## 5. Adding or aligning a component

This replaces the manual PROJECT_SPEC §5.5 procedure:

1. **Measure.** `bake.measure_anchors("<tikz keyword>", ["<anchor>", …])` prints
   each anchor's grid offset.
2. **Choose pin grid positions.** Snap each measured offset to the nearest 0.25
   GU (or pick a clean outward position, as the op-amp's ±1.5 does).
3. **Add the entry** to `components/components.json` (`components` map): emission,
   tikz, pins (name/offset/anchor), `anchor_pin`, labels, bbox, variants. The
   leads are computed (each non-origin pin → its offset).
4. **Render & verify.** `python tools/generate_components.py` rebuilds the
   manifest geometry and the data file; `tests/test_components_library.py` checks
   the registry/codegen, and the suite checks the canvas geometry and that the
   examples compile.
5. Add a `ComponentItem` mapping in `app/canvas/items.py` and the `kind` to
   `_DISPLAY_ORDER` in `registry.py`.

---

## 6. Implementation status

**Built** (all existing tests pass; examples compile; canvas geometry verified):

| Piece | File |
|-------|------|
| Measurement / render / parse core | `app/components/bake.py` |
| Unified renderer → `manifest.json` (geometry) + `components.json` (data) | `tools/generate_components.py` |
| Loader → registry `ComponentDef`s, codegen tables, `origin_svg` | `app/components/library.py` |
| Registry built from the data (33 SVG kinds derived; 6 bespoke literals kept) | `app/components/registry.py` |
| Codegen classification + lead-only alignment derived from the data | `app/codegen/circuitikz.py` |
| Canvas placement = `translate(-origin_svg)` + uniform scale (no per-component anchors) | `app/canvas/svgsym.py` |
| Bundles the data file | `heaviside.spec` |

The former hand-maintained magic numbers — registry `ComponentDef` literals, the
five codegen tables, and `svgsym`'s `_MULTI_ANCHORS` / bipole anchors / per-kind
scale — are all **removed**. The old `tools/export_circuitikz_svgs.py` is deleted
(the unified renderer supersedes it). MOSFET/BJT rendering changed slightly (a
short lead stub instead of a stretched body), in both canvas and LaTeX.

**Generic per-instance variants — done.** A placed component's active boolean
variants live in a generic `Component.variants` map (no more `DiodeComponent` /
`MosfetComponent` subclasses or `filled`/`body_diode` fields). The inspector
auto-generates a checkbox per variant the kind declares (`VariantSection`),
toggling is undoable (`SetVariantCommand`), and the `.hv` file stores a
`variants` map (reading the legacy keys for back-compat). Canvas geometry and
codegen pick the variant from the kind's declared `{name, token, mode}` via
`library.variant_tikz` / `library.variant_manifest_suffix`.

**Optional follow-up:**

- **A GUI** over the renderer + data file, if interactive authoring is wanted; the
  tool/data design above does not require one.
