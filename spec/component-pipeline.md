# Heaviside — Component Pipeline Specification

**Version:** 0.6
**Status:** Implemented — the registry, codegen, and canvas build from the generated data file (no hand-stored geometry magic numbers); per-instance variants are generic; regeneration is fully automated (`components/generate_components.py`). The standalone authoring GUI this spec once defined (the "Component Editor") has been **removed** — see §6.
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
     scale, metadata) plus one `origin_svg` placement constant and the
     `circuitikz_version` stamp.

The app builds its registry (`registry.py`), codegen tables (`circuitikz.py`),
and canvas placement (`svgsym.py`) from this data. Adding or re-aligning a
component is: measure → add an entry → re-run the renderer. No editing of the
registry, the codegen tables, or `svgsym`'s placement.

---

## 3. The data file

`components/definitions.json` is `{origin_svg, circuitikz_version, components}`,
where `components` maps each `kind` to a flat record. Example (a resistor and
an op-amp):

```jsonc
{
  "origin_svg": [15.0312, 15.0312],   // SVG point that every symbol's origin pin maps to
  "circuitikz_version": "1.6.7",      // CircuiTikZ release the library was generated against
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
      "emission": "node", "tikz": "op amp", "labels": ["l"],   // node with anchored pins → multi-terminal
      "bbox": [-0.75,-0.5,0.75,0.5],  // computed from the rendered ink (§3)
      "pins": [{"name":"+","offset":[-0.75,0.25],"anchor":"+"}, ...],  // offset = scaled+snapped anchor (§4)
      "scale": [1.0504, 1.0],         // the per-axis grid-alignment scale (§4); omitted when [1,1]
      "variants": [{"name":"filled","token":"*","mode":"suffix"}]  // optional
    }
  }
}
```

Fields:

| Field | Meaning |
|-------|---------|
| `origin_svg` (top level) | The single SVG point that every symbol's origin pin maps to — see §4. |
| `circuitikz_version` (top level) | The CircuiTikZ release the library was generated against, measured at batch-generation time via the package's own `\pgfcircversion` macro (typeset into every measurement compile's log; `render.circuitikz_version` parses it, with the package log banner as fallback). Omitted when unknown. Incremental saves (`save_component`, `save_muxdemux`) preserve the existing stamp; only the batch regeneration (`generate_components.py`, `import_family.py --write`) re-measures it. Makes symbol/anchor drift against a user's newer CircuiTikZ diagnosable. |
| `display_name`, `category`, `labels` | Palette metadata + valid options-string slots. |
| `emission` | One of two LaTeX-syntax groups: `path` (`to[…]`) or `node` (`node[…]`). A `node` element with no anchored pins is a **single-terminal** node (`node[kind]`); one with anchored pins is a **multi-terminal** node (`node[…, xscale=…, yscale=…]`, centre-placed). Multi-terminal-ness is derived from the data by `library.is_multi_terminal_entry`, not a separate emission value. |
| `tikz` | The CircuiTikZ keyword. |
| `bbox` | Bounding box `(x0,y0,x1,y1)` in GU. **Computed**, not authored: the rendered ink extent (paths + glyphs) ∪ pin positions, rounded outward to 0.05 GU (`generate.compute_bbox`). Drives label clearance and the hit/selection region; tracks the drawn symbol. |
| `pins` | Each pin: `name`, grid `offset` (GU, multiple of 0.25), and the CircuiTikZ `anchor` it maps to (`null` for two-terminal/node, whose pins are the draw endpoints). |
| `scale` | **Alignment** for multi-terminal symbols (§4) — `[sx,sy]`, the node `xscale=`/`yscale=` that the uniform `best_alignment` algorithm derives from the measured anchors. Computed, never hand-typed; omitted when `[1.0,1.0]`. Pin `offset`s are the scaled-and-snapped measurements (some legitimately off-grid → magnet). There is **no `anchor_pin` and no `leads`** — every node is centre-placed and aligned by scale alone. |
| `muxdemux` | The two-parameter mux/demux **authoring rec** `{role, data_param, select_param}`, persisted in the stored record so the batch generator can re-render every `(data, select)` combo (`render_muxdemux`) from `definitions.json` alone. Entries carrying it also store `params` + the per-combo `n_data`; `render_store` routes them through `render_muxdemux`, never the plain-node path. |
| `variants` | Boolean modifiers: `{name, token, mode}` where `mode` is `suffix` (`D`→`D*`) or `option` (append `, bodydiode`). Generalises the diode `filled` and MOSFET `body_diode` flags. |

`default_span` and `resizable` are derived (terminal-minus-origin for a two-pin
device; library kinds are never resizable), so they are not stored.

> **Today `definitions.json` is *both* the authored input and the generated
> output** (the generator reads it, overwrites it, and provenance is only
> documented per-field above — `bbox`/`scale`/`offset`/`n_data` are derived, the
> rest authored). §9 specifies splitting it into a hand-edited *source* file and
> pure generated *artifacts*; that is migration step 3, enabled by §4.

---

## 4. Alignment

> **Implemented (v0.6).** The *uniform* alignment model below replaces the former
> `anchor_pin` / scale-vs-leads branching; the generator and the committed library
> reflect it, and the reproduction-contract test pins it.

Every symbol is rendered inside a **fixed bounding box**, **centre-placed** at
TeX `(0,0)`. Two things follow:

- **Placement is one constant.** Because every node is placed by its centre and
  the bounding box is fixed, TeX origin maps to a single SVG point —
  `origin_svg` — for *every* symbol. The canvas transform (`svgsym.py`) is just
  `translate(-origin_svg)` then a uniform pixel scale: no per-component placement
  anchors, no `anchor_pin`, no rotation.

- **One alignment knob, one algorithm, for every kind.** A node's CircuiTikZ
  anchors rarely land on the grid, so a single **per-axis scale** `[sx, sy]`
  (`node[KIND, xscale=…, yscale=…]`) stretches the symbol toward the grid. There
  is no `anchor_pin` branching and no `leads` — the special cases are gone.

### The uniform best-effort algorithm

`generate.best_alignment` (replacing `fit_alignment` / `compute_alignment`):

1. **Render unscaled** and measure every pin's anchor relative to the node
   centre (`render.measure_anchors`).
2. **Solve each axis independently.** For axis *a* with measured coordinates
   `vs`:
   - **Candidate scales** = `{1.0} ∪ { snap(v)/v : v ∈ vs, |v| > ε }`, where
     `snap(x)` rounds to the nearest `grid_gu`, clamped to
     `[scale_min, scale_max]`. (Each candidate is the scale that lands *one*
     coordinate exactly on the grid; `1.0` is always allowed.)
   - **Score** a candidate `s` by the number of coordinates that land on the
     grid after scaling — `|s·v − snap(s·v)| < snap_tolerance_gu`.
   - **Choose** the highest score, **tie-broken by `|s − 1|`** (least
     distortion — the scale closest to no-op), then by the smaller `s`
     (deterministic, so regeneration is bit-stable).
   - **Anisotropy cap.** If the two axis scales differ by more than
     `scale_anisotropy_max` (the ratio of the larger to the smaller), discard
     them and use a **single uniform scale** computed over *both* axes'
     coordinates. This protects symbols with thick diagonal strokes (a switch
     blade): the canvas buckets every stroke to a thin/thick canonical width
     ([`items.py`](../app/canvas/items.py)), so a strongly non-uniform node
     scale would shear the blade in the compiled LaTeX while the canvas stayed
     uniform — desyncing the two. The cap keeps the shear imperceptible
     (transistors ≈5–9% anisotropy stay per-axis) and forces switches/blocks
     (≈30–70%) uniform; their pins then land off-grid (magnet) rather than
     shearing.
3. **Bake pin offsets** as `snap(s·measured)`. Pins that *cannot* be brought
   on-grid within the bounds (a tube's slanted electrodes, a mux's diagonal
   selects) **stay off-grid** and are reached by the canvas wire **magnet** —
   this is the "best effort" contract: maximise on-grid pins, never distort
   past the bounds to force the rest.

`scale` is the only alignment field, omitted when `[1.0, 1.0]`. Both the canvas
(geometry baked with the scale) and the codegen (`xscale=`/`yscale=`) read the
same stored value, so they agree by construction. The generator **re-derives
the scale on every run** (`render_store`), so it is a *computed property of the
current CircuiTikZ library*, not a frozen constant.

**Aspect-ratio note.** Independent `sx`/`sy` can distort a symbol whose form
must stay isotropic (a logic gate's inversion bubble). Those kinds set their
**body size via a shape parameter** in the pre-pass below (keeping the bubble
round), *then* the uniform algorithm runs on the result — so non-uniform scale
only ever applies where distortion is acceptable.

### Configuration

The algorithm constants live in **[`components/generation.toml`](../components/generation.toml)**
(read by the generator via stdlib `tomllib`; no new dependency):

```toml
[alignment]
grid_gu              = 0.25   # the pin grid
scale_min            = 0.7    # scale bounds: fence the search off its degenerate
scale_max            = 1.3    #   collapse-to-origin optimum AND cap distortion (±30%)
snap_tolerance_gu    = 0.02   # how close to a grid line counts as "on grid"
scale_anisotropy_max = 1.15   # max sx/sy ratio before falling back to a uniform scale

[gates]                    # parametric logic gates (and/or/nand/…)
input_pitch_gu    = 0.5    # target spacing between adjacent gate inputs

[muxdemux]                 # parametric multiplexers/demultiplexers
data_pitch_gu     = 1.0    # spacing between adjacent data pins (sets Lh/Rh)
select_spacing_gu = 1.0    # spacing that sets the select-side width (w)
```

`scale_min`/`scale_max` are the **scale bounds**: they fence the
maximize-on-grid objective away from its collapse-to-origin optimum *and* cap
how far a symbol may be stretched/shrunk to reach the grid. At the chosen ±30%,
all 49 node kinds that can align do (43/49); tightening to ±20% would strand the
switches/European gates off-grid (magnet-reached) rather than distort them.
`[gates]`/`[muxdemux]` are the **height knobs** — raising `input_pitch_gu`
makes every logic gate taller (inputs further apart); the mux/demux pitches do
the same for those bodies. Per-component overrides remain possible in the
authored entry, but the defaults live here.

### Parametric pre-pass (variable pin count)

Gates and mux/demux have a configurable pin count, so a fixed measurement
won't do. They get **one extra step before** the uniform algorithm — set the
shape so the pins sit at the configured pitch, then align as usual:

- **Logic gates** (`generate._gate_height`): the native input pitch is linear in
  the CircuiTikZ gate `height`, so measure the pitch at `height=1`, solve for the
  height that yields `gates.input_pitch_gu`, and set it. (Height, not `yscale`,
  keeps the bubble round.)
- **Mux/demux** (`generate._muxdemux_combo`): derive `Lh`/`Rh` from the data
  count × `muxdemux.data_pitch_gu` and the select-side width `w` from the select
  count × `muxdemux.select_spacing_gu`.

After the pre-pass each concrete size runs through the **same** `best_alignment`,
so there is exactly one alignment algorithm in the codebase.

### Migration outcome (measured against CircuiTikZ 1.6.7)

Over the 49 node multi-terminal kinds, most fully grid-align; a handful keep
residual off-grid pins reached by the magnet — the vacuum tubes
(`triode`/`tetrode`/`pentode`), `rotaryswitch`, `ALU`, the `adder`, and
`fd op amp`, whose electrodes/operands sit at intrinsically off-grid anchors.

Three intended, visible consequences to eyeball on regeneration:
- **The op-amp family** (`op amp`, `instamp`, `gmamp`) loses its extended leads
  (decision: maximal uniformity) and renders compact, with pins at the scaled
  triangle edge rather than on long stubs.
- **Transistor footprints shift** — the algorithm prefers the grid-landing
  scale **closest to 1.0**, generally more compact than the old anchor-pinned
  targets, and the symbols are now centre-placed (centre at the component
  origin, not the base/gate pin).
- **The switches and a few blocks go uniform** via the anisotropy cap
  (`cute spdt`, `ebuffer`/`enot`, `ALU`, `adder`, `pentode`): a single scale
  rather than per-axis, so the blade/body isn't sheared — at the cost of some
  pins landing off-grid (magnet).

---

## 5. Adding or aligning a component

This replaces the manual PROJECT_SPEC §5.5 procedure:

1. **Measure.** `render.measure_anchors("<tikz keyword>", ["<anchor>", …])` prints
   each anchor's grid offset.
2. **Choose pin grid positions.** Snap each measured offset to the nearest 0.25
   GU (or pick a clean outward position, as the op-amp's ±1.5 does).
3. **Add the entry** to `components/definitions.json` (`components` map): emission,
   tikz, pins (name + `anchor`), labels, variants. The pin `offset`, the `scale`,
   and the `bbox` are **not authored** — the generator measures the anchors and
   derives all three on every run (§4, `generate.best_alignment` /
   `generate.compute_bbox`). There is no `anchor_pin` and no `leads`.
4. **Render & verify.** `python components/generate_components.py` validates
   every authored entry (`generate.validate_entry`, fail-fast pre-flight),
   rebuilds the geometry and the data file (computing each `bbox` from the
   rendered ink), and stamps the CircuiTikZ version it rendered against;
   `tests/test_components_library.py` checks the registry/codegen, and the
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
| Codegen classification + per-axis scale alignment derived from the data | `app/codegen/circuitikz.py` |
| Canvas placement = `translate(-origin_svg)` + uniform scale (no per-component anchors) | `app/canvas/svgsym.py` |
| Render/save core + alignment + validation (one renderer) | `app/components/generate.py` |
| Generation-time algorithm config (alignment bounds, gate/mux height knobs) | `components/generation.toml` |
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

**Authoring GUI — removed.** A standalone, form-driven editor
(`app/componenteditor/`, **Tools → Component Editor…**) once provided manual
scale/offset fix-ups for symbols the automated alignment couldn't land
on-grid. The automation has since closed that gap entirely — anchors are
measured (`\pgfpointanchor`), alignment is a *computed* property re-derived on
every batch run (`generate.realigned` preserves both centre-placed styles, and
mux/demux re-render from the persisted `muxdemux` rec), and off-grid pins are
permitted by design (the wire magnet reaches them) — so the GUI had no
remaining job and was removed. Its Qt-free core lives on as
`app/components/generate.py` (render/save/alignment plus the
`validate_entry` pre-flight); authoring is editing `definitions.json` and
re-running the generator (§5).

**Uniform alignment — done (step 2).** §4's single `best_alignment` algorithm
(centre-placed, per-axis scale with an anisotropy cap, best-effort grid-snap,
config-bounded) replaced the `anchor_pin` / scale-vs-leads branching: the old
`fit_alignment`/`compute_alignment`/`best_alignment_scale` and all
`anchor_pin`/`leads` handling are gone from `generate.py`, the codegen
(`circuitikz.py`), and the geometry body; `components/generation.toml` holds the
constants; the library was regenerated (reproduction-contract test green). No
`.hv` *format* change (pin **names** are the stable interface; offsets live in
the library), so no `_FORMAT_VERSION` bump — but saved figures re-export with the
new footprints. The two bundled examples that use moved components (the MUX's
gates, the boost converter's MOSFET) had their wires re-fit to the new pin
positions (an exact old→new pin-coordinate remap, Manhattan-validated on save);
the other four use only path bipoles/primitives and were untouched.

Next, **step 3** splits the data file into an authored source and generated
artifacts (§9) — a mechanical extraction that step 2 makes possible.

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
- **`generate.best_alignment(entry)`** — derives the per-axis `scale` for a
  candidate (§4), so a discovered transistor aligns automatically.

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
  "tikz": "and port",
  "param": {
    "name": "inputs", "min": 2, "max": 16, "default": 2,
    "option": "number inputs={n}",                 // appended to tikz per instance
    "input":  {"name": "in{i}", "anchor": "in {i}"},
    "output": {"name": "out", "anchor": "out"},
    "height_key": "tripoles/american and port/height",   // set body height (round bubble)
    "n_data": {"2": {"scale": […], "bbox": […], "height": …}, …, "16": {…}}
  },
  // plus the ordinary default-value fields: bbox, pins, scale
}
```

**Grid alignment (round bubbles).** This is the parametric **pre-pass** of §4.
CircuiTikZ lays the inputs in one vertical column, symmetric about the output, in
a **fixed-size body** — so the pitch *shrinks* as inputs grow. A non-uniform node
`yscale` would land them on the grid but stretch the inverting gates' round
inversion bubble into an ellipse. Instead, each value sets the CircuiTikZ gate
**`height`** (a shape setting, `param.height_key`) so the body grows *natively*
until the inputs reach `gates.input_pitch_gu` (§4 config). The per-value height
is solved from a measurement (the native pitch is linear in height;
`generate._gate_height`); the **uniform `best_alignment`** then runs on the
sized body — typically yielding only a small `xscale` (the height pre-pass
already handled y), so the bubble stays round.

**Generation.** `render_parametric` renders one geometry per value (keyed
`kind:N`), runs the pre-pass + `best_alignment` to derive per-N `scale`/`bbox`
(and `height` for gates), and computes the pins from the value. At its
**default** value the entry is an ordinary multi-terminal `node` record
(`pins`/`bbox`/`scale`), so the registry, palette, and codegen need no special
handling — only the variable-N runtime consults the `param` block.

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

---

## 9. Authored source vs. generated artifacts (planned — migration step 3)

> **Status: planned design.** Enabled by §4: once the uniform algorithm lands,
> no derived number is hand-authored, so this split is a mechanical extraction
> rather than a redesign. Sequenced *after* the §4 algorithm + regeneration
> (§7), because separating the files first would mean designing a schema half of
> which §4 deletes.

### The problem with one file

`components/definitions.json` is simultaneously the **authored input** the
generator reads and the **generated output** it overwrites. Provenance is
implicit and per-field (§3): `tikz`/`category` are intent; `bbox`/`scale`/`offset`/
`n_data` are output. Three concrete costs:

- A hand-edit to a derived field **silently reverts** on the next regeneration —
  the hazard the bootstrap scripts shout about with "DON'T RE-RUN."
- A round-trip can **lose authored-only structure** the generator doesn't know to
  preserve (exactly the mux/demux `params`/`n_data` loss we fixed by persisting
  the `muxdemux` rec).
- Reviewers can't tell intent changes from regeneration churn in a diff.

### The split

- **Authored source — `components/components.toml`** (the *only* hand-edited
  file): pure intent, **zero derived numbers**. Per kind:
  `display_name`, `category`, `labels`, `emission`, `tikz`, `variants`, the
  `param` / `muxdemux` *declarations* (no `n_data`), and `pins` as
  **`{name, anchor}`** for node kinds (offset is derived) or **`{name, offset}`**
  for path bipoles (the span *is* intent; no anchor). No `bbox`, no `scale`, no
  `anchor_pin`, no `leads`, no `n_data`, no `origin_svg`.
- **Generated artifacts — `definitions.json` + `geometry.json`**: pure build
  outputs, **never hand-edited**, fully reproducible. They carry every derived
  field (pin `offset`s, `scale`, `bbox`, per-N `n_data`, `origin_svg`,
  `circuitikz_version`) plus the authored fields copied through, since the app
  still loads only these two at runtime (no toolchain).
- **The generator is a pure function** `components.toml → (definitions.json,
  geometry.json)`. `validate_entry` (§5) becomes the **authored-schema
  validator** — mostly the checks it already runs, minus the ones on derived
  fields (e.g. the `bbox`-present check drops, since `bbox` becomes generated).

### Format: TOML, not SQLite/Excel

The source's jobs are git-diffable review, mergeable collaboration, comments,
schema validation, and being a deterministic build input. **TOML** satisfies all
five; SQLite/Excel fail four (binary blobs in git, unmergeable, no comments,
invisible in review). Each record is small (≈8 flat fields + a short `pins`
inline-array), so TOML stays readable. For spreadsheet-style **bulk** editing, a
~20-line CSV round-trip helper (flat fields ↔ CSV) gives Excel as an *editing
surface* while text stays the source of truth — the itch behind the
"store it in Excel" idea, without surrendering the properties above.

```toml
# components/components.toml  (authored — the only hand-edited component file)
[components."op amp"]
display_name = "Op-Amp"
category     = "Amplifiers"
emission     = "node"
tikz         = "op amp"
labels       = ["l"]
pins = [ {name = "+", anchor = "+"}, {name = "-", anchor = "-"}, {name = "out", anchor = "out"} ]
variants = [ {name = "filled", token = "*", mode = "suffix"} ]

[components.R]
display_name = "Resistor"
category     = "Resistors"
emission     = "path"
tikz         = "R"
labels       = ["l", "l_", "v", "v^", "i", "i_"]
pins = [ {name = "in", offset = [0, 0]}, {name = "out", offset = [2, 0]} ]  # span = intent
```

### Invariants and consequences

- `definitions.json` / `geometry.json` gain a **"generated — do not edit"**
  header; regeneration is the only way to change them, enforced by a test that
  fails if a regenerate produces a diff.
- The **reproduction-contract test** evolves from *“`render_store(load_authored())`
  reproduces the committed files”* to *“regenerate **from `components.toml`**
  reproduces the committed artifacts”* — `load_authored` just changes its source.
- The bootstrap scripts' "DON'T RE-RUN" hazard **disappears**: there is no
  derived data in the source to clobber, so the authoring scripts (or hand edits)
  only ever touch intent.
- **No `.hv` impact** — pin *names* remain the stable interface; the format is
  untouched, so no `_FORMAT_VERSION` bump.

### Migration (mechanical, after §4 + regeneration)

1. One-off: extract the authored subset of the regenerated `definitions.json`
   into `components/components.toml`.
2. Point `generate.load_authored()` at `components.toml`; have `write_store`
   emit only the two generated files.
3. Add the "generated — do not edit" banner + a regenerate-is-clean guard test.
4. Repoint `validate_entry` at the authored schema; retire the bootstrap
   scripts' self-authored tables (their content now lives in `components.toml`).
