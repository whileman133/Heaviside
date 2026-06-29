# Heaviside — Component Pipeline Specification

**Version:** 0.6
**Status:** Implemented — the registry, codegen, and canvas build from the single generated library (`components/generated/{definitions,geometry}.json`, no hand-stored geometry magic numbers); per-instance variants are generic; regeneration is fully automated (`components/generate_library.py`, which scrapes the CircuiTikZ manual). The shipped library renders every symbol at its **true CircuiTikZ size** and bakes **no** grid-alignment scale; pins sit at native (mostly off-grid) anchors and are reached by the canvas wire magnet. The standalone authoring GUI this spec once defined (the "Component Editor") has been **removed** — see §6.
**Author:** Wes H.

This document is governed by the living-document rule in [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) §0.

---

## 1. Purpose

The brittle part of Heaviside used to be the per-component CircuiTikZ geometry:
pin positions, placement, and the SVG anchors, once **hand-measured magic numbers**
scattered across `registry.py`, `svgsym.py`, and the `circuitikz` codegen tables.

**The requirement: generate the whole component library from the CircuiTikZ manual
without hand-storing any magic numbers.** The fix is to *measure* each symbol
(`\pgfpointanchor`) instead of typing measured constants, and to keep the
per-component data in the generated files (`components/generated/`) instead of
several hand-edited ones. Symbols render at their true CircuiTikZ size; off-grid
pins are reached by the canvas wire magnet.

Non-goals: this covers only CircuiTikZ symbols (the `BIPOLES`/`NODES`/`TRIPOLES`
the renderer draws). The 6 bespoke kinds — the resizable annotations
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

2. **One renderer, two outputs** (`components/generate_library.py`). It scrapes the
   CircuiTikZ manual for component definitions, renders every symbol (and variant),
   and writes into `components/generated/`:
   - `geometry.json` — the symbol *geometry* (paths/glyphs, read by `svgsym.py`);
     and
   - `definitions.json` — the registry + codegen data (pins, bbox, metadata) plus
     one `origin_svg` placement constant and the `circuitikz_version` stamp.

The app builds its registry (`registry.py`), codegen tables (`circuitikz.py`),
and canvas placement (`svgsym.py`) from this data. Regenerating the library is:
re-run `components/generate_library.py`. No editing of the registry, the codegen
tables, or `svgsym`'s placement.

---

## 3. The data file

`components/generated/definitions.json` is `{origin_svg, circuitikz_version, components}`,
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
      "pins": [{"name":"+","offset":[-0.75,0.25],"anchor":"+"}, ...],  // offset = the measured native anchor (off-grid → magnet)
      // no "scale" (true-size; §4) and no "anchor_pin"/"leads"
      "variants": [{"name":"filled","token":"*","mode":"suffix"}]  // optional
    }
  }
}
```

Fields:

| Field | Meaning |
|-------|---------|
| `origin_svg` (top level) | The single SVG point that every symbol's origin pin maps to — see §4. |
| `circuitikz_version` (top level) | The CircuiTikZ release the library was generated against, measured at batch-generation time via the package's own `\pgfcircversion` macro (typeset into every measurement compile's log; `render.circuitikz_version` parses it, with the package log banner as fallback). Omitted when unknown. Re-measured by the batch regeneration (`generate_library.py`). Makes symbol/anchor drift against a user's newer CircuiTikZ diagnosable. |
| `display_name`, `category`, `labels` | Palette metadata + valid options-string slots. |
| `emission` | One of two LaTeX-syntax groups: `path` (`to[…]`) or `node` (`node[…]`). A `node` element with no anchored pins is a **single-terminal** node (`node[kind]`); one with anchored pins is a **multi-terminal** node (`node[kind]`, centre-placed, true-size). Multi-terminal-ness is derived from the data by `library.is_multi_terminal_entry`, not a separate emission value. |
| `tikz` | The CircuiTikZ keyword. |
| `bbox` | Bounding box `(x0,y0,x1,y1)` in GU. **Computed**, not authored: the rendered ink extent (paths + glyphs) ∪ pin positions, rounded outward to 0.05 GU (`generate.compute_bbox`). Drives label clearance and the hit/selection region; tracks the drawn symbol. |
| `pins` | Each pin: `name`, `offset` (GU), and the CircuiTikZ `anchor` it maps to (`null` for two-terminal path devices, whose pins are the draw endpoints). A two-terminal device's two **axial** terminals lie on the 0.25 GU grid; a multi-terminal node's pins sit at the **native** CircuiTikZ anchors (mostly off-grid → magnet). |
| `scale` | **Optional alignment** for multi-terminal symbols — `[sx,sy]`, an `xscale=`/`yscale=` node stretch. **The shipped library bakes none** (every symbol renders at true CircuiTikZ size, so this is omitted everywhere) — pins sit at their native anchors and are reached by the magnet. The generator still carries the `best_alignment` helper (§4) for the muxdemux pre-pass and any future grid-aligned library, but the manual library does not apply it. There is **no `anchor_pin` and no `leads`** — every node is centre-placed. |
| `muxdemux` | The two-parameter mux/demux **authoring rec** `{role, data_param, select_param}`, persisted in the stored record so the batch generator can re-render every `(data, select)` combo (`generate.render_muxdemux`) from the data alone. Entries carrying it also store `params` + the per-combo `n_data`. |
| `variants` | Boolean modifiers: `{name, token, mode}` where `mode` is `suffix` (`D`→`D*`) or `option` (append `, bodydiode`). Generalises the diode `filled` and MOSFET `body_diode` flags. |

`default_span` and `resizable` are derived (terminal-minus-origin for a two-pin
device; library kinds are never resizable), so they are not stored.

> **`components/generated/definitions.json` is a pure build artifact.** It is
> produced wholesale by `generate_library.py` scraping the CircuiTikZ manual — it
> is not hand-edited, and there is no separate authored source file to keep in
> sync. §9 (a planned authored-source/generated-artifact split) is therefore moot
> for the manual library and is retained only as historical design context.

---

## 4. Alignment

> **Not applied to the shipped library.** The shipped manual library bakes **no**
> grid-alignment scale: every symbol renders at its true CircuiTikZ size and its
> pins sit at the native (mostly off-grid) anchors, reached by the canvas wire
> magnet. The per-axis-scale algorithm below remains in the generator
> (`generate.best_alignment`) as shared infrastructure — used by the muxdemux
> pre-pass and available for any future grid-aligned library — but the manual
> library leaves `scale` omitted everywhere. Read this section as a description of
> that generator machinery, not of the current data file.

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

When applied, `scale` is the only alignment field, omitted when `[1.0, 1.0]`. Both
the canvas (geometry baked with the scale) and the codegen (`xscale=`/`yscale=`)
would read the same stored value, so they agree by construction. The generator
**re-derives the scale on every run**, so it is a *computed property of the
current CircuiTikZ library*, not a frozen constant. (For the manual library this
is moot — no scale is baked.)

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

`scale_min`/`scale_max` are the **scale bounds** the alignment helper would use:
they fence the maximize-on-grid objective away from its collapse-to-origin optimum
*and* cap how far a symbol may be stretched/shrunk to reach the grid. (The shipped
manual library applies no scale, so these bound only the muxdemux pre-pass and any
future grid-aligned library.) `[gates]`/`[muxdemux]` are the **height knobs** —
the mux/demux pitches set those bodies' sizes, and the gate input pitch the gate
bodies'. The defaults live here.

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

### Outcome for the shipped library

The shipped manual library applies **no** alignment scale: every multi-terminal
node is centre-placed and rendered at its true CircuiTikZ size, with its pins at
the native anchors. Most of those anchors are off the 0.25 GU grid, and every one
is reached by the canvas wire magnet (§5.4/§6.4 of `PROJECT_SPEC.md`). The
algorithm above is retained in `generate.py` only as shared infrastructure (the
muxdemux pre-pass uses `_scale_for`; a future grid-aligned library could opt in).

---

## 5. Regenerating the library

This replaces the manual PROJECT_SPEC §5.5 procedure. The whole library is
regenerated from the CircuiTikZ manual in one step:

1. **Run** `python components/generate_library.py`. It scrapes the CircuiTikZ
   manual for component definitions, measures each symbol's pin anchors
   (`render.measure_anchors`), renders the geometry, computes each `bbox` from the
   rendered ink (`generate.compute_bbox`), and writes
   `components/generated/{geometry.json, definitions.json}` with the
   `circuitikz_version` it rendered against. Symbols are written at their true
   CircuiTikZ size — no `scale`, no `anchor_pin`, no `leads`; off-grid pins are
   reached by the canvas magnet.
2. **Verify.** `tests/test_generated_library.py` and `tests/test_components_library.py`
   check the registry/codegen, and the suite checks the canvas geometry and that
   the examples compile.
3. *(Optional.)* Nothing else is needed for a plain symbol — the canvas item
   falls back to the generic `ComponentItem` and the palette shows the kind
   automatically (there is no display-order list to edit). Add a
   `ComponentItem` subclass + `ITEM_CLASSES` row in `app/canvas/items.py` *only*
   if the component needs special canvas behaviour (custom `boundingRect`,
   hit-testing, or resize).

---

## 6. Implementation status

**Built** (all existing tests pass; examples compile; canvas geometry verified):

| Piece | File |
|-------|------|
| Measurement / render / parse core | `app/components/render.py` |
| Manual-scraping renderer → `generated/geometry.json` + `generated/definitions.json` | `components/generate_library.py` |
| Loader → registry `ComponentDef`s, codegen tables, `origin_svg` | `app/components/library.py` |
| Registry built from the data (all CircuiTikZ symbol kinds derived; 6 bespoke literals kept) | `app/components/registry.py` |
| Codegen classification derived from the data (true-size nodes, no baked scale) | `app/codegen/circuitikz.py` |
| Canvas placement = `translate(-origin_svg)` + uniform scale (no per-component anchors) | `app/canvas/svgsym.py` |
| Shared render/measure/alignment helpers (used by the generator) | `app/components/generate.py` |
| Generation-time algorithm config (alignment bounds, gate/mux height knobs) | `components/generation.toml` |
| Bundles the data files | `heaviside.spec` |

The former hand-maintained magic numbers — registry `ComponentDef` literals, the
five codegen tables, and `svgsym`'s `_MULTI_ANCHORS` / bipole anchors — are all
**removed**; every per-component number is derived from the generated data. The old
`tools/export_circuitikz_svgs.py` is deleted (the unified renderer supersedes it).

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
`app/components/generate.py` (shared render/measure/alignment helpers); the library
is regenerated by re-running `components/generate_library.py` (§5).

**Single manual library — done.** The curated, hand-tuned library was dropped; the
manual-scraped library generated by `components/generate_library.py` is the one and
only library, and the `HEAVISIDE_COMPONENT_LIB` environment variable, the `--manual`
flag, and the `run-manual.sh` launcher are gone (`app.resources.component_lib_dir()`
always returns `components/generated/`). Manual symbols render at true CircuiTikZ
size with native (off-grid) pins and bake **no** grid-alignment scale; wires reach
pins via the canvas magnet. The bundled examples were ported to manual kind names;
the gate-based "4-1 MUX" example and its "Logic Circuits" category were removed
(manual logic gates are off-grid true-size symbols that example could not be
ported to), leaving 6 examples.

---

## 7. Whole-library import from the manual

The whole library is imported automatically: `components/generate_library.py`
scrapes the CircuiTikZ manual for component definitions and renders every one.
Because geometry and bbox are all derived, this is *mostly* mechanical. The key
primitive is:

- **`render.discover_terminals(keyword, candidates)`** — CircuiTikZ exposes no
  machine-readable terminal list (an unknown anchor resolves to the shape centre
  rather than erroring, and aliases like `B`/`base`/`G`/`gate` collapse to one
  point). This probes a candidate anchor list, drops anything that lands on the
  centre-fallback, and de-dupes by position — returning the shape's distinct
  wireable terminals `{name: (gu_x, gu_y)}`. The candidate *order* supplies the
  canonical name per terminal (the per-family naming convention).

Symbols are imported at true size: **two-terminal bipoles** (pins = the draw
endpoints) and **multi-terminal** nodes (pins = native anchors) both come through
with no hand-tuning, off-grid pins reached by the magnet. What does *not*
generalise: components that don't fit the `path`/`node` (single- or
multi-terminal) model with simple point terminals (multi-pin ICs, logic with
configurable pins, buses) — those need model work.

---

## 8. Parametric components (variable pin count)

Some symbols have a variable number of terminals — e.g. a logic gate with 2–16
inputs. These are **parametric**: the kind declares a `param` block, and an
instance carries an integer in `Component.params` (e.g. `{"inputs": 4}`).

```jsonc
"american and port": {
  "display_name": "AND Gate", "category": "Logic", "emission": "node",
  "tikz": "american and port",
  "param": {
    "name": "inputs", "min": 2, "max": 8, "default": 2,
    "option": "number inputs={n}",                 // appended to tikz per instance
    "n_data": {"2": {"pins": […], "bbox": […]}, …, "8": {…}}  // per-N pins/bbox
  },
  // the gate's body-size keys (height/width), used for sizing — see below
  "size_keys": {"path": "tripoles/american and port", "height": 0.8, "width": 1.1},
  // plus the ordinary default-value fields: pins, bbox
}
```

**Round bubbles via body size, not scale.** CircuiTikZ lays the inputs in one
vertical column, symmetric about the output, in a **fixed-size body** — so the
pitch *shrinks* as inputs grow. A non-uniform node `yscale` would stretch the
inverting gates' round inversion bubble into an ellipse, so gates are **never**
node-scaled: they are sized via the CircuiTikZ body **`height`**/`width` keys
(`size_keys`), which redraw the symbol at native stroke width and keep the bubble
round. The library bakes no grid-alignment scale; the parametric pins simply sit
at the rendered anchors (off-grid → magnet).

**Generation.** `render_parametric` renders one geometry per value (keyed
`kind:N`) and computes the per-N `pins`/`bbox`. At its **default** value the entry
is an ordinary multi-terminal `node` record (`pins`/`bbox`), so the registry,
palette, and codegen need no special handling — only the variable-N runtime
consults the `param` block.

**Runtime.** `library.resolved_pins` / `param_value` / `param_geometry_suffix` /
`param_n_data` resolve an instance's pins, geometry key, and bbox
from its value. `component_pin_positions` (connectivity), `ComponentItem`
(paint/bbox/pin dots), and the codegen all go through these. A size-keyed gate
is emitted in its **own local group** so the body keys revert:
`{ \ctikzset{…/height=H, …/width=W}  \draw … node[american and port, number inputs=N]; }`,
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

## 9. Authored source vs. generated artifacts (obsolete — historical plan)

> **Status: obsolete.** This section described a planned split of a *hand-authored*
> source file from the generated artifacts. The library is now scraped wholesale
> from the CircuiTikZ manual by `components/generate_library.py` — there is **no
> hand-authored source file** (`components/generated/{definitions,geometry}.json`
> are pure build artifacts, never hand-edited), so the split has no subject. The
> rest of this section is retained only as historical design context; the function
> names it mentions (`render_store`, `load_authored`, `write_store`,
> `save_component`) and the single `components/definitions.json` it refers to no
> longer exist.

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
