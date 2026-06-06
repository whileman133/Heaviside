# Heaviside — Component Editor Specification

**Version:** 0.2
**Status:** Draft — measurement tool + data file built; runtime switchover and GUI pending.
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

2. **One data file** (`components/components.json`). It holds the registry +
   code-generation data for every CircuiTikZ symbol: pins, bbox, alignment, and
   palette metadata. Symbol *geometry* (the drawn paths) stays where it already
   is — the generated `tools/circuitikz_svgs/manifest.json` — so this file is just
   the registry/codegen layer that used to be magic numbers in code.

The app builds its registry and codegen tables from this file
(`app/components/library.py`). Adding or re-aligning a component is: measure →
add an entry → done. No editing of five files.

---

## 3. The data file

`components/components.json` maps each `kind` to a flat record. Example (an op-amp
and a resistor):

```jsonc
{
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
    "anchor_pin": null,                       // pin used to place the node (null = by centre)
    "scale": [1.0167, 1.0],                   // optional; default [1,1]
    "leads": [{"anchor":"out","to":[1.5,0]}], // optional; bridge stubs to the grid
    "variants": [{"name":"filled","token":"*","mode":"suffix"}]  // optional
  }
}
```

Fields:

| Field | Meaning |
|-------|---------|
| `display_name`, `category`, `labels` | Palette metadata + valid options-string slots. |
| `emission` | `two_terminal` (`to[…]`), `node` (single-terminal `node[…]`), or `multi_terminal` (`node[…, anchor=…]` + leads). |
| `tikz` | The CircuiTikZ keyword. |
| `bbox` | Bounding box `(x0,y0,x1,y1)` in GU (a design choice, kept snug for label placement). |
| `pins` | Each pin: `name`, grid `offset` (GU, multiple of 0.25), and the CircuiTikZ `anchor` it maps to (`null` for two-terminal/node, whose pins are the draw endpoints). |
| `anchor_pin`, `scale`, `leads` | **Alignment** for multi-terminal symbols — see §4. All computed, never hand-typed. Omitted when trivial. |
| `variants` | Boolean modifiers: `{name, token, mode}` where `mode` is `suffix` (`D`→`D*`) or `option` (append `, bodydiode`). Generalises the diode `filled` and MOSFET `body_diode` flags. |

`default_span` and `resizable` are derived (terminal-minus-origin for a two-pin
device; library kinds are never resizable), so they are not stored.

---

## 4. Alignment

A CircuiTikZ multi-terminal node's internal anchors do not land on the grid. Two
computed corrections bridge them to the pins' chosen grid positions:

- **`scale`** `[sx, sy]` — stretches the node so its anchors land on grid
  (emitted as `xscale=`/`yscale=` on the node).
- **`leads`** — short `\draw (node.anchor) -- (grid)` stubs from an anchor to its
  pin offset.

Both are **computed from the measurement** (`scale = grid ÷ measured`; a `lead`
just targets the pin's offset), so neither is a hand-entered constant. The bake
tool measures the anchors; the generator records whichever correction the
component uses. (Historically these lived in separate tables in `svgsym.py` and
the codegen with the well-known footgun that they could be combined by mistake;
as generated data the choice is mechanical, not a manual trap.)

---

## 5. Adding or aligning a component

This replaces the manual PROJECT_SPEC §5.5 procedure:

1. **Measure.** `bake.measure_anchors("<tikz keyword>", ["<anchor>", …])` prints
   each anchor's grid offset.
2. **Choose pin grid positions.** Snap each measured offset to the nearest 0.25
   GU (or pick a clean outward position, as the op-amp's ±1.5 does). Record
   `scale`/`leads` to bridge — the bake/generator computes these.
3. **Add the entry** to `components/components.json` (or its generator input).
4. **Rebuild & verify.** `python tools/generate_components.py`; the tests in
   `tests/test_components_library.py` check the registry/codegen reconstruct.

Symbol geometry comes from the existing `tools/export_circuitikz_svgs.py`
pipeline (add the keyword to its table and re-run), unchanged.

---

## 6. Implementation status

**Built** (additive; the live runtime is unchanged and all existing tests pass):

| Piece | File |
|-------|------|
| Measurement tool (render, parse geometry, **measure anchors**) | `app/components/bake.py` |
| Data file for all 33 CircuiTikZ symbols | `components/components.json` |
| Generator (consolidates today's values; for new components use the bake) | `tools/generate_components.py` |
| Loader → registry + codegen tables, **proven equal to today's `REGISTRY`** | `app/components/library.py`, `tests/test_components_library.py` |

**Pending** (each its own reviewed step):

- **Runtime switchover** — have `registry.py` and the `circuitikz` codegen read
  from `library.py` instead of their literals (the tests prove this is
  behaviour-preserving), then delete the magic-number tables. Bundle
  `components/components.json` in `heaviside.spec`.
- **Auto-measured geometry & placement** — let the bake also write the SVG
  geometry and SVG placement anchor, removing the last hand-copied constants in
  `svgsym.py` and folding the export script and the bake into one renderer.
- **Generic per-instance variants** — store a placed component's active variants
  as a map (with back-compat for the current `filled`/`body_diode` fields); this
  touches the `.hv` format (PROJECT_SPEC §9).
- **A GUI** over the bake + data file, if interactive authoring is wanted; the
  tool/data design above does not require one.
