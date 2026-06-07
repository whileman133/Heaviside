# Heaviside — Component Editor Specification

**Version:** 0.5
**Status:** Implemented — the registry, codegen, and canvas build from the generated data file (no hand-stored geometry magic numbers); per-instance variants are generic; and a standalone authoring GUI is provided (`python -m app.componenteditor` / Tools menu).
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

1. **A measurement tool** (`app/components/render.py`). Given a CircuiTikZ keyword,
   it renders the symbol with `latex`/`dvisvgm` and reads each pin's **anchor
   position automatically** (via `\pgfpointanchor`), returning a grid-unit offset.
   This is PROJECT_SPEC §5.5 Step 1 done by the machine — no human reads a
   coordinate off a figure. (`latex`/`dvisvgm` are a developer-tool dependency,
   not a shipped-app one.)

2. **One renderer, two outputs** (`components/generate_components.py`). It renders
   every symbol (and variant) and writes:
   - `components/geometry.json` — the symbol *geometry* (paths/glyphs,
     read by `svgsym.py`); and
   - `components/definitions.json` — the registry + codegen data (pins, bbox,
     leads, metadata) plus one `origin_svg` placement constant.

The app builds its registry (`registry.py`), codegen tables (`circuitikz.py`),
and canvas placement (`svgsym.py`) from this data. Adding or re-aligning a
component is: measure → add an entry → re-run the renderer. No editing of the
registry, the codegen tables, or `svgsym`'s placement.

---

## 3. The data file

`components/definitions.json` is `{origin_svg, components}`, where `components`
maps each `kind` to a flat record. Example (a resistor and an op-amp):

```jsonc
{
  "origin_svg": [15.0312, 15.0312],   // SVG point that every symbol's origin pin maps to
  "components": {
    "R": {
      "display_name": "Resistor", "category": "Resistors",
      "emission": "path", "tikz": "R",
      "labels": ["l", "l_", "v", "v^", "i", "i_"],
      "bbox": [0.0, -0.25, 2.0, 0.25],
      "pins": [{"name": "in", "offset": [0,0], "anchor": null},
               {"name": "out", "offset": [2,0], "anchor": null}]
    },
    "op amp": {
      "display_name": "Op-Amp", "category": "Amplifiers",
      "emission": "node", "tikz": "op amp", "labels": ["l"],   // node element with anchored pins → multi-terminal
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
| `emission` | One of two LaTeX-syntax groups: `path` (`to[…]`) or `node` (`node[…]`). A `node` element with no anchored pins is a **single-terminal** node (`node[kind]`); one with anchored pins (or an `anchor_pin`) is a **multi-terminal** node (`node[…, anchor=…]` + leads). Multi-terminal-ness is derived from the data by `library.is_multi_terminal_entry`, not a separate emission value. |
| `tikz` | The CircuiTikZ keyword. |
| `bbox` | Bounding box `(x0,y0,x1,y1)` in GU. **Computed**, not authored: the rendered ink extent (paths + glyphs) ∪ pin positions, rounded outward to 0.05 GU (`renderer.compute_bbox`). Drives label clearance and the hit/selection region; tracks the drawn symbol. |
| `pins` | Each pin: `name`, grid `offset` (GU, multiple of 0.25), and the CircuiTikZ `anchor` it maps to (`null` for two-terminal/node, whose pins are the draw endpoints). |
| `anchor_pin`, `scale`, `leads` | **Alignment** for multi-terminal symbols — see §4. `scale` is `[sx,sy]` (node `xscale=`/`yscale=`); `leads` bridge residuals. All computed, never hand-typed; each present only when used (the op amp has leads, BJTs a scale, MOSFETs both). |
| `variants` | Boolean modifiers: `{name, token, mode}` where `mode` is `suffix` (`D`→`D*`) or `option` (append `, bodydiode`). Generalises the diode `filled` and MOSFET `body_diode` flags. |

`default_span` and `resizable` are derived (terminal-minus-origin for a two-pin
device; library kinds are never resizable), so they are not stored.

---

## 4. Alignment

Every symbol is rendered inside a **fixed bounding box** with its origin pin
placed at TeX `(0,0)`. Two things follow:

- **Placement is one constant.** Because the origin pin is always at TeX `(0,0)`
  and the bounding box is fixed, TeX origin maps to a single SVG point —
  `origin_svg` — for *every* symbol. The canvas transform (`svgsym.py`) is just
  `translate(-origin_svg)` then a uniform pixel scale: no per-component placement
  anchors or rotation.

A multi-terminal node's other pins (CircuiTikZ's internal anchors) rarely land on
the grid, so two **computed** corrections bring them on:

- **`scale`** `[sx, sy]` — a per-axis node scale (`node[KIND, xscale=…, yscale=…]`)
  that stretches the symbol so its anchors land on the grid pins. Computed by
  `compute_alignment`: when a single ratio `target/measured` is consistent across
  all pins on an axis, that scale lands them exactly (e.g. the symmetric BJT
  collector/emitter); otherwise the best-fit ratio (minimising the worst residual)
  is used. This is preferred for symbols whose terminals fall *between* grid
  points (BJTs, MOSFETs) — it keeps the symbol intact rather than adding a
  diagonal stub.
- **`leads`** — short `\draw (node.anchor) -- (grid)` bridges. Used for the
  residual a scale can't remove (the MOSFET source's sub-grid y), and for symbols
  whose grid pins are *chosen outward* of the body so a clean axis-aligned lead is
  the natural fit (the op amp's `±1.5`, which scaling would distort).

Both are **computed from the measured anchors**, not hand-typed (`renderer.fit_alignment`,
shared by the editor's **Fit pins to grid** and the batch generator). The
**batch generator re-derives them on every run** (`render_store` → `realigned`),
so `scale`/`leads` are a *computed property of the current CircuiTikZ library*, not
a frozen constant — re-generating after a CircuiTikZ update reflows the alignment
automatically. The canvas (geometry baked with the scale + leads) and the codegen
(`xscale=`/`yscale=` + lead `\draw`s) read the same stored values, so they agree
by construction.

The **scale-vs-leads strategy** is driven by the authored `anchor_pin`: a
centre-placed symbol (`anchor_pin: null`) bridges every pin with a clean lead
(scaling would distort its form — e.g. the op-amp triangle), while an
anchor-pinned symbol stretches onto the grid and bridges only the residual. So:
the op amp uses leads only; BJTs use scale only; MOSFETs use scale plus one small
residual source lead.

---

## 5. Adding or aligning a component

This replaces the manual PROJECT_SPEC §5.5 procedure:

1. **Measure.** `render.measure_anchors("<tikz keyword>", ["<anchor>", …])` prints
   each anchor's grid offset (the editor's **Measure anchors** button).
2. **Choose pin grid positions.** Snap each measured offset to the nearest 0.25
   GU (or pick a clean outward position, as the op-amp's ±1.5 does).
3. **Add the entry** to `components/definitions.json` (`components` map): emission,
   tikz, pins (name/offset/anchor), `anchor_pin`, labels, variants. Run
   the editor's **Fit pins to grid** (or `compute_alignment`) to compute the
   `scale` + residual `leads` that land the pins on the grid (§4). The `bbox` is
   not authored — the renderer computes it from the ink extent (§3).
4. **Render & verify.** `python components/generate_components.py` rebuilds the
   geometry and the data file (computing each `bbox` from the rendered
   ink); `tests/test_components_library.py` checks the registry/codegen, and the
   suite checks the canvas geometry and that the examples compile.
5. *(Optional.)* Nothing else is needed for a plain symbol — the canvas item
   falls back to the generic `ComponentItem` and the palette shows the kind
   automatically (`_DISPLAY_ORDER` is a preference, not a requirement). Add a
   `ComponentItem` subclass + `ITEM_CLASSES` row in `app/canvas/items.py` *only*
   if the component needs special canvas behaviour (custom `boundingRect`,
   hit-testing, or resize).

---

## 6. Implementation status

**Built** (all existing tests pass; examples compile; canvas geometry verified):

| Piece | File |
|-------|------|
| Measurement / render / parse core | `app/components/render.py` |
| Unified renderer → `geometry.json` (geometry) + `definitions.json` (data) | `components/generate_components.py` |
| Loader → registry `ComponentDef`s, codegen tables, `origin_svg` | `app/components/library.py` |
| Registry built from the data (all CircuiTikZ symbol kinds derived; 6 bespoke literals kept) | `app/components/registry.py` |
| Codegen classification + scale/lead alignment derived from the data | `app/codegen/circuitikz.py` |
| Canvas placement = `translate(-origin_svg)` + uniform scale (no per-component anchors) | `app/canvas/svgsym.py` |
| Render/save core (shared by the CLI and the GUI) | `app/componenteditor/renderer.py` |
| Standalone authoring GUI | `app/componenteditor/window.py`, `__main__.py` |
| Bundles the data file | `heaviside.spec` |

The former hand-maintained magic numbers — registry `ComponentDef` literals, the
five codegen tables, and `svgsym`'s `_MULTI_ANCHORS` / bipole anchors — are all
**removed**; the per-component scale that remains is *computed* and stored in the
data file. The old `tools/export_circuitikz_svgs.py` is deleted (the unified
renderer supersedes it).

**Generic per-instance variants — done.** A placed component's active boolean
variants live in a generic `Component.variants` map (no more `DiodeComponent` /
`MosfetComponent` subclasses or `filled`/`body_diode` fields). The inspector
auto-generates a checkbox per variant the kind declares (`VariantSection`),
toggling is undoable (`SetVariantCommand`), and the `.hv` file stores a
`variants` map (reading the legacy keys for back-compat). Canvas geometry and
codegen pick the variant from the kind's declared `{name, token, mode}` via
`library.variant_tikz` / `library.variant_geometry_suffix`.

**Authoring GUI — done.** A standalone, form-driven editor
(`python -m app.componenteditor`, also **Tools → Component Editor…** in the app)
over the renderer + data file:

- A form for identity, emission, CircuiTikZ keyword, label slots, a **read-only
  auto bbox** (computed on Render, not editable), **editable `xscale`/`yscale`
  spin boxes**, and a **pins table** (name / X / Y / anchor); a variants field;
  and an *existing-component* picker to load and re-align any current symbol.
- **Measure anchors** runs `render.measure_anchors` and lists each pin's measured
  GU offset. **Fit pins to grid** computes the `scale` (+ residual leads) that
  lands the pins on the grid (§4) and fills the scale spin boxes — which can also
  be edited by hand. **Render & preview** renders the symbol on a **0.25 GU** grid,
  computes the **bbox from the rendered ink extent** (`renderer.compute_bbox`,
  shown in the read-only fields and **drawn dashed** for reference) with pin
  markers, and shows the derived `ComponentDef` + validation — and runs
  automatically when a component is picked from the *existing* list. **Pin
  extensions** (the grid-alignment leads, §4) are drawn in **red** to distinguish
  them from the symbol body; the editor isolates them by diffing the render
  against a leads-free render (the extra paths are the extensions). **Save** writes the entry into `definitions.json`
  and the geometry into `geometry.json` via `renderer.save_component` (the same
  render path as the CLI).
- The window is a thin shell over the Qt-free `draft` / `renderer` core; the core
  (validation, entry building, render, save, alignment) is unit-tested head-less,
  and the window is smoke-tested offscreen.

Because the alignment model auto-measures anchors and computes the scale/leads,
the editor needs no interactive click-to-place-pins / drag-to-draw-leads canvas —
the pins table plus the Measure/Fit helpers cover it.

---

## 7. Bulk import (scaling to the whole library)

Because geometry, alignment, and bbox are all derived, importing a CircuiTikZ
component is *mostly* data entry — and for whole families it can be largely
automated. Two primitives support this:

- **`render.discover_terminals(keyword, candidates)`** — CircuiTikZ exposes no
  machine-readable terminal list (an unknown anchor resolves to the shape centre
  rather than erroring, and aliases like `B`/`base`/`G`/`gate` collapse to one
  point). This probes a candidate anchor list, drops anything that lands on the
  centre-fallback, and de-dupes by position — returning the shape's distinct
  wireable terminals `{name: (gu_x, gu_y)}`. The candidate *order* supplies the
  canonical name per terminal (the per-family naming convention).
- **`renderer.fit_alignment(entry)`** — derives `scale`/`leads` for a candidate
  (§4), so a discovered, grid-snapped transistor aligns automatically.

**`components/import_family.py`** is a dry-run prototype that uses these to
generate *candidate* `definitions.json` entries for a family, render-verifies each,
and prints a review report + ready-to-paste JSON (it does not write the data file).
It shows the real curation cost: **two-terminal bipoles** import with zero curation
(input = keyword + display name; pins are the draw endpoints, everything else
derived), while **multi-terminal** families need only a naming convention and a
quick grid review. What does *not* generalise: components that don't fit the
`path`/`node` (single- or multi-terminal) model with simple point terminals
(multi-pin ICs, logic with configurable pins, buses) — those need model work, not
just a data entry.

---

## 8. Parametric components (variable pin count)

Some symbols have a variable number of terminals — e.g. a logic gate with 2–16
inputs. These are **parametric**: the kind declares a `param` block, and an
instance carries an integer in `Component.params` (e.g. `{"inputs": 4}`).

```jsonc
"and": {
  "display_name": "AND Gate", "category": "Logic", "emission": "node",
  "tikz": "and port", "anchor_pin": "out",
  "param": {
    "name": "inputs", "min": 2, "max": 16, "default": 2,
    "option": "number inputs={n}",                 // appended to tikz per instance
    "input":  {"name": "in{i}", "anchor": "in {i}", "x": -1.5, "pitch": 0.5},
    "output": {"name": "out", "anchor": "out", "offset": [0, 0]},
    "height_key": "tripoles/american and port/height",   // set body height (round bubble)
    "n_data": {"2": {"scale": […], "leads": […], "bbox": […], "height": …}, …, "16": {…}}
  },
  // plus the ordinary default-value fields: bbox, pins, scale, leads
}
```

**Grid alignment (round bubbles).** CircuiTikZ lays the inputs in one vertical
column, symmetric about the output, in a **fixed-size body** — so the pitch
*shrinks* as inputs grow. A non-uniform node `yscale` would land them on a
constant 0.5 GU grid pitch but would also stretch the inverting gates' round
inversion bubble into an ellipse. Instead, each value sets the CircuiTikZ gate
**`height`** (a shape setting, `param.height_key`) so the body grows *natively*
and the inputs reach the grid pitch with **no `yscale`** — only a small constant
`xscale` for x-alignment, so the bubble stays round. The per-value height is
solved from a measurement (the native pitch is linear in height;
`renderer._gate_height`) and stored in `n_data`.

**Generation.** `render_parametric` renders one geometry per value (keyed
`kind:N`), derives per-N `scale`/`leads`/`bbox` (and `height` for gates), and
computes the pins from the value. At its **default** value the entry is an
ordinary multi-terminal `node` record (`pins`/`bbox`/`scale`), so the registry,
palette, and codegen need no special handling — only the variable-N runtime
consults the `param` block.

**Runtime.** `library.resolved_pins` / `param_value` / `param_geometry_suffix` /
`param_n_data` resolve an instance's pins, geometry key, scale, bbox, and height
from its value. `component_pin_positions` (connectivity), `ComponentItem`
(paint/bbox/pin dots), and the codegen all go through these. A height-setting gate
is emitted in its **own local group** so the height reverts:
`{ \ctikzset{…/height=H}  \draw … node[and port, number inputs=N, xscale=…]; }`,
before the main `\draw` so its node name resolves for wires. The inspector's
`ParamSection` is a spinbox per declared parameter, undoable via `SetParamCommand`;
the value is persisted in `Component.params` (`schematic/io.py`).

**Labelling.** Logic-port shapes (CircuiTikZ keyword `<gate> port`) do **not**
accept the bipole-style `l=` quick key — pdflatex would warn and drop the label.
The codegen therefore rewrites a gate's `l=` slot to `label=above:{…}` (a node
option CircuiTikZ accepts), placing the label above the body to match where the
canvas draws the gate's `l` slot (above the lead axis; `ComponentItem._slot_direction`).
Other slots pass through unchanged. See `_gate_label_args` in `app/codegen/circuitikz.py`.
