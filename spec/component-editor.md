# Heaviside — Component Editor Specification

**Version:** 0.1
**Status:** Draft (design — not yet implemented)
**Author:** Wes H.
**Relationship to the main spec:** This document specifies one feature of
Heaviside in depth so that [`PROJECT_SPEC.md`](../PROJECT_SPEC.md) stays focused.
It is governed by the same living-document rule (PROJECT_SPEC §0): when the
Component Editor is built or changed, this document and `PROJECT_SPEC.md` must be
updated in the same change set. Section references of the form "PROJECT_SPEC §5.5"
point into the main spec.

---

## Contents

- [0. Spec Maintenance](#0-spec-maintenance)
- [1. Purpose, Scope, and Audience](#1-purpose-scope-and-audience)
- [2. Motivation — The Problem This Solves](#2-motivation--the-problem-this-solves)
- [3. Glossary](#3-glossary)
- [4. Coordinate Spaces](#4-coordinate-spaces)
- [5. The Component Definition (Source of Truth)](#5-the-component-definition-source-of-truth)
- [6. Unified Alignment Model](#6-unified-alignment-model)
- [7. Derivation — How the App Consumes a Definition](#7-derivation--how-the-app-consumes-a-definition)
- [8. The Bake Pipeline](#8-the-bake-pipeline)
- [9. Editor Workflow](#9-editor-workflow)
- [10. Editor UI](#10-editor-ui)
- [11. Integration and Packaging](#11-integration-and-packaging)
- [12. Migration of the Existing Component Set](#12-migration-of-the-existing-component-set)
- [13. Consequences, Risks, and Open Decisions](#13-consequences-risks-and-open-decisions)
- [14. Implementation Phases](#14-implementation-phases)
- [15. Test Specification and Acceptance Criteria](#15-test-specification-and-acceptance-criteria)
- [16. Out of Scope](#16-out-of-scope)

---

## 0. Spec Maintenance

This document follows PROJECT_SPEC §0. In addition:

- When a Component Definition **field** is added, changed, or removed, update
  §5 (schema), §7 (derivation), and the §15 test table in the same change.
- When the **bake pipeline** or the **alignment model** changes, update §6/§8
  and re-bake every bundled definition (§12), exactly as PROJECT_SPEC §9.5 and
  the `tools/export_circuitikz_svgs.py` re-run rule require today.
- Keep the cross-references in `PROJECT_SPEC.md` (§5.2, §5.5, §5.6, §11) pointing
  at the right sections here; they are the contract this feature replaces.

---

## 1. Purpose, Scope, and Audience

### 1.1 Purpose

Provide a visual editor that imports a CircuiTikZ symbol from a generating
command, aligns it to the Heaviside grid, and captures its pins and labels — and
emits a **single declarative Component Definition** that is the sole source of
truth for that component everywhere in the app.

### 1.2 Audience and runtime (decided)

The editor is built **as a developer tool first, with a data model designed to
embed in the shipped app later** with no rework. Concretely:

- **Phase 1 (this spec's primary target):** a GUI tool run from a source
  checkout, where `latex` + `dvisvgm` (+ Ghostscript) are installed — the same
  toolchain `tools/export_circuitikz_svgs.py` already requires. Its output
  (Component Definitions and baked geometry) is **checked into the repo and
  bundled**. The shipped Heaviside app gains **no new runtime dependencies**.
- **Phase 2 (future, out of scope for the first build):** the same editor
  embedded in the app so end users add custom components at runtime. The data
  model, file format, and module boundaries in this spec are chosen so Phase 2
  is additive (a UI host + a user component store + a security sandbox), not a
  rewrite. See §13 for the consequences that Phase 2 introduces.

The single design constraint Phase 2 places on Phase 1: **all format,
geometry, derivation, and bake-orchestration logic must be Qt-free and live
outside the GUI layer** (see §11.3), so it can run head-less in the existing
test/CI path and be driven by either a standalone window or an in-app panel.

### 1.3 Scope (Phase 1)

The editor handles **CircuiTikZ symbols** — the components currently produced by
the `BIPOLES` / `NODES` / `TRIPOLES` tables in
`tools/export_circuitikz_svgs.py`. For one symbol it lets the author:

1. **Import** by entering the CircuiTikZ command/spec that generates it.
2. **Rescale** in x and y so the chosen pins land on the grid.
3. **Draw lead extensions** from a terminal anchor out to a grid coordinate.
4. **Place and name pins**, and declare the component's **label slots**.

and then save a Component Definition (§5).

### 1.4 Non-goals (Phase 1)

- Editing the **drawing primitives** (`text_node`, `rect`, `circle`, `bipole`).
  These are not derived from a CircuiTikZ command and keep their bespoke
  `ComponentItem` classes and codegen paths (PROJECT_SPEC §5.4, §7.7). The editor
  neither imports nor produces them.
- Authoring the per-instance label *rendering* convention (which side `l`/`v`/`i`
  sit on — PROJECT_SPEC §5.8). The definition only **declares** valid slot names;
  the existing on-canvas math/slot machinery is unchanged.
- A general TikZ/PGF drawing canvas. The editor measures and aligns CircuiTikZ's
  own output; it never hand-draws symbol geometry (PROJECT_SPEC §5.2 prohibits
  hand-drawn symbols, and that rule still holds).
- Running inside the shipped app for end users (that is Phase 2).

---

## 2. Motivation — The Problem This Solves

Adding or re-aligning one multi-terminal symbol today requires hand-editing
**five files in lockstep**, every one carrying numbers measured by hand:

| File | What must be edited by hand |
|------|------------------------------|
| `tools/export_circuitikz_svgs.py` | An entry in `BIPOLES`/`NODES`/`TRIPOLES`, including hand-written lead-routing TikZ like `\draw (X.drain) -- (0.0164,0.7295);`. Then re-run `latex`+`dvisvgm`. |
| `app/components/registry.py` | A `ComponentDef` with hand-measured `pins`, `bbox`, `default_span`. |
| `app/canvas/svgsym.py` | A `Placement` with a hand-measured SVG anchor (e.g. `(-61.7422, -32.14453)`) and `xscale`/`yscale` correction factors. |
| `app/codegen/circuitikz.py` | Entries in **five** parallel tables: `_MULTI_TERMINAL_KINDS`, `_MULTI_TERMINAL_ANCHOR_PIN`, `_PIN_TO_CTIKZ_ANCHOR`, `_MULTI_TERMINAL_EXTRA_OPTS`, `_MULTI_TERMINAL_LEADS`. |
| `app/canvas/items.py` | A `ComponentItem` subclass and an `ITEM_CLASSES` entry. |

The numbers come from the manual procedure in PROJECT_SPEC §5.5: place the node,
print each anchor with `\pgfpointanchor`+`\typeout`, divide by `28.348 pt/GU`,
snap to `0.25 GU`, then compute scale ratios or route bridge leads. §5.5 itself
documents the central footgun — scale-correction and bridge-lead-wires are **two
separate, mutually-exclusive mechanisms that behave differently on the canvas vs.
in the LaTeX output**, and combining them for the same axis silently
double-corrects the position.

Two facts make this tractable to fix:

1. **The runtime is already data-driven.** `svgsym.symbol_paths()` paints any
   symbol straight from the manifest; the base `ComponentItem.paint()` needs only
   a `kind`. Almost every `ComponentItem` subclass (`ResistorItem`,
   `CapacitorItem`, every diode, the sources, op-amp, BJTs) is an **empty
   one-liner** that exists only to carry identity. The bespoke subclasses
   (resizable annotations, drawing primitives, grounds, MOSFET bbox override) are
   *not* editor-produced symbols.
2. **The numbers are measurable, not magical.** Step 1 of §5.5 is exactly a
   `\pgfpointanchor` dump. A machine can emit and parse that dump during the same
   render that produces the SVG, so no human ever reads a coordinate off a
   compiled figure again.

This feature converts the five hand-edited files into **projections of one
Component Definition** (§7), and collapses the dual alignment mechanism into one
(§6).

---

## 3. Glossary

| Term | Definition |
|------|------------|
| **Component Definition (CD)** | The single declarative document describing one component: identity, source command, render options, alignment (scale + leads), pins, label slots, variants, and baked geometry. The source of truth; §5. |
| **Source command** | The exact CircuiTikZ body that generates the symbol, e.g. `\draw (0,0) to[R] (2,0);` or `\node[op amp](X) at (0,0){};`. Stored verbatim so the symbol is reproducible and re-bakeable. |
| **Bake** | Run the source command through `latex → dvisvgm`, parse the SVG into `paths`/`glyphs`, and read every CircuiTikZ anchor coordinate from a companion dump. Produces the geometry and the anchor table the editor and runtime consume. §8. |
| **CTikZ anchor** | A named connection point CircuiTikZ exposes on a node (`X.drain`, `X.+`, `X.out`). Its coordinate is *measured*, never assumed. |
| **Pin** | A Heaviside connection point: a name, a grid-aligned offset (GU) from the origin pin, and (usually) the CTikZ anchor it maps to. The first pin is the **origin pin** (local `(0,0)`). |
| **Lead extension** | A short wire from a CTikZ anchor to a grid target, drawn once and emitted in **both** the canvas geometry and the LaTeX output (§6). Replaces the two separate lead mechanisms. |
| **Alignment scale** | A single `(sx, sy)` applied identically to the baked geometry and to the emitted node's `xscale=`/`yscale=`, so canvas and LaTeX agree by construction (§6). Distinct from render-time scale (e.g. `diodes/scale`). |
| **Emission mode** | How codegen places the component: `two_terminal` (`to[KIND]`), `multi_terminal` (`node[KIND, anchor=…]` + leads), or `node` (single-terminal `node[KIND]`). §5.3. |
| **Variant** | A named **boolean** attribute a component instance can toggle, declared once in the definition and stored per-instance in a generic `variants: {name: bool}` map. Declaring one derives its storage, inspector checkbox, undo command, canvas geometry, and codegen with no per-attribute code (e.g. diode `filled` → `D*`; MOSFET `body_diode` → `, bodydiode`). §5.4, §7.4a. |
| **Definition store** | The bundled directory of Component Definitions the app loads at startup; replaces the hand-maintained registry/placement/codegen tables and the standalone manifest. §11. |

---

## 4. Coordinate Spaces

Getting alignment right means being explicit about four spaces. The editor and
the bake pipeline convert between them; the definition records values in the
space noted per field (§5.2).

| Space | Unit | Axis | Where it appears |
|-------|------|------|------------------|
| **CTikZ space** | pt (and GU = pt ÷ `28.34765`) | **y-up** | The source command, `\pgfpointanchor` dumps, lead targets. CircuiTikZ's native frame. |
| **SVG space** | SVG pt | y-down | `dvisvgm` output; the `paths`/`glyphs` `d` strings; the baked anchor coordinates. |
| **Grid (GU)** | GU | y-down (Qt) | `PinDef.offset`, `bbox`, `default_span`, lead targets after snapping. |
| **Local pixels** | px (`GRID_PX`/GU) | y-down | What `ComponentItem.paint()` draws; produced by `svgsym._local_transform`. |

Constants (from `app/canvas/style.py`): `SVG_PT_PER_GU = 28.34765`,
`GRID_PX = 60.0`. The SVG y-axis already matches Qt's, so **no axis flip** is
applied between SVG and local; the flip to watch is **CTikZ y-up → grid y-down**
(negate y) when converting an anchor dump to a pin offset. This is the single
most error-prone conversion in the manual procedure, and the bake pipeline owns
it so authors never perform it by hand.

---

## 5. The Component Definition (Source of Truth)

### 5.1 Form and location

One Component Definition per file, JSON, UTF-8, extension `.hvc`, stored in the
bundled **definition store** (§11.1). The definition is **both** the
human-editable source *and* the bundled runtime data — there is no separate
compiled manifest. Geometry is baked into the file (`geometry` block, §5.5), so
the app reads only the definition store at runtime, exactly as it reads only
`manifest.json` today.

> **Why one file with baked geometry, not source-plus-compiled-artifact.** The
> chosen model (decision: *unified spec, derive the rest*) makes the definition
> the one thing checked in, diffed, and reviewed. The intermediate `.svg`/`.dvi`
> are scratch and discarded after baking. If startup cost over a large store ever
> matters, an *optional* aggregated `components.json` may be produced as a pure
> build artifact (§11.4) — but the per-component `.hvc` files remain the source
> of truth. This is the same intermediate-vs-artifact split the project already
> uses for SVGs vs. the manifest.

### 5.2 Schema

```jsonc
{
  "cd_version": "1",                 // Component Definition format version (§5.7)

  // ── Identity ──────────────────────────────────────────────────────────
  "kind": "op amp",                  // REGISTRY key; matches .hv file `kind`
  "display_name": "Op-Amp",
  "category": "Tripoles",            // palette group (PROJECT_SPEC §5.4 order)
  "tikz_keyword": "op amp",          // string passed to to[]/node[]

  // ── Emission (§5.3) ───────────────────────────────────────────────────
  "emission": "multi_terminal",      // "two_terminal" | "multi_terminal" | "node"

  // ── Source command + render options (§8) ──────────────────────────────
  "source": {
    "body": "\\node[op amp](X) at (0,0){};",  // verbatim CircuiTikZ body
    "node_id": "X",                  // the node name leads/anchors reference
    "border_pt": 10,                 // standalone `border=` for the render
    "ctikzset": [],                  // e.g. ["diodes/scale=0.8"] (§5.4 render scale)
    "preamble": []                   // extra \usepackage / \ctikzset lines, if any
  },

  // ── Alignment (§6) ─────────────────────────────────────────────────────
  "alignment": {
    "scale": [1.0, 1.0],             // [sx, sy]; baked into geometry AND emitted
    "anchor_pin": "out",             // node placed so this pin's CTikZ anchor = its grid coord
                                     //   (omit / null for emission "node" and "two_terminal")
    "leads": [                       // lead extensions, CTikZ-anchor → grid target (GU, y-down)
      { "anchor": "+",   "to": [-1.5,  0.5] },
      { "anchor": "-",   "to": [-1.5, -0.5] },
      { "anchor": "out", "to": [ 1.5,  0.0] }
    ]
  },

  // ── Pins (§5 + §7) ─────────────────────────────────────────────────────
  // First pin is the origin (local (0,0)). offset in GU (y-down). `ctikz_anchor`
  // is the measured CircuiTikZ anchor this pin maps to (null = coordinate-only pin, §13.6).
  "pins": [
    { "name": "+",   "offset": [-1.5,  0.5], "ctikz_anchor": "+",   "connects": true },
    { "name": "-",   "offset": [-1.5, -0.5], "ctikz_anchor": "-",   "connects": true },
    { "name": "out", "offset": [ 1.5,  0.0], "ctikz_anchor": "out", "connects": true }
  ],

  // ── Labels / behavior ──────────────────────────────────────────────────
  "label_slots": ["l"],              // valid options-string slots (PROJECT_SPEC §5.8 declares only)
  "bbox": [-1.5, -1.0, 1.5, 1.0],    // (x0,y0,x1,y1) GU; auto-proposed from geometry, editable
  "default_span": [0.0, 0.0],        // origin→terminal (GU); = 2nd pin offset for two-terminal
  "resizable": false,
  "variants": [],                    // §5.4

  // ── Baked geometry (§5.5, §8) — produced by the bake, not hand-edited ──
  "geometry": {
    "viewBox": "…", "width_pt": "…pt", "height_pt": "…pt",
    "paths":  [ { "d": "M…", "stroke_width": 0.3985, "fill": "#000", "stroke": "#000" } ],
    "glyphs": [ { "d": "…", "matrix": [1,0,0,1,0,0], "stroke_width": 0.3985 } ],
    "anchors": { "+": [ax, ay], "-": [bx, by], "out": [cx, cy], "text": [tx, ty] },  // SVG pt
    "origin_svg": [ox, oy],          // SVG pt mapped to local (0,0); = anchor of origin pin
    "baked_with": { "toolchain": "TeX Live 2024 / circuitikz X.Y", "scale": [1.0,1.0] }
  }
}
```

The `geometry` block is exactly today's manifest entry (`viewBox`, `width_pt`,
`height_pt`, `paths`, `glyphs`) **plus** the measured `anchors` table and the
resolved `origin_svg`. Everything above `geometry` is authored; `geometry` is
generated.

### 5.3 Emission modes

The single `emission` field replaces the implicit dispatch scattered across
`tools/export_circuitikz_svgs.py` (which table) and `app/codegen/circuitikz.py`
(`_TWO_TERMINAL_KINDS` vs `_MULTI_TERMINAL_KINDS`).

| `emission` | Source-command shape | LaTeX output | Examples |
|------------|----------------------|--------------|----------|
| `two_terminal` | `\draw (0,0) to[KIND] (2,0);` | `to[KIND, opts]` between two pin coords | R, C, L, D, V, I, sources |
| `multi_terminal` | `\node[KIND](X) at (0,0){}; <leads>` | `node[KIND, anchor=A, scale, opts]` at origin-pin coord + lead `\draw`s | op amp, MOSFETs, BJTs |
| `node` | `\draw (0,0) node[KIND]{};` | `node[KIND, opts]` at the pin coord | grounds, power rails |

For `two_terminal`, alignment is trivial and `alignment.anchor_pin` is unused:
the two pins are the fixed `to[]` endpoints (default 2 GU apart), the origin is
the `in`/`+`/`anode` pin, and `scale`/`leads` are normally empty.

### 5.4 Variants (custom boolean attributes) and render scale

A **variant** is a named **boolean** attribute a component instance can turn on —
generalizing the hardcoded `DiodeComponent.filled` (diode `*`) and
`MosfetComponent.body_diode` (MOSFET `, bodydiode`). Variants are **fully generic**:
declaring one in a definition wires up its per-instance storage, inspector control,
undo, canvas geometry, and codegen with **no per-attribute code** (§7.4a).

**Definition side.** Each declared variant carries the TikZ it injects and the
geometry baked with that variant active:

```jsonc
"variants": [
  {
    "name": "filled",            // per-instance boolean key (unique within the CD)
    "label": "Filled",           // inspector checkbox label
    "tikz": "*",                 // injected token
    "tikz_mode": "suffix",       // "suffix" (D → D*)  |  "option" (append ", <tikz>")
    "source_body": "\\draw (0,0) to[D*] (2,0);",  // command that bakes this variant
    "geometry": { /* baked like §5.5, with this variant active and others off */ }
  }
]
```

**Per-instance side.** A placed component stores `variants: {name: bool}` on the
base `Component` (§7.4a) — this replaces the named `filled`/`body_diode` fields and
the `DiodeComponent`/`MosfetComponent` subclasses.

**Geometry selection.** Variant geometries are full re-renders, **not** overlays,
so they do not compose. Phase 1 models variants as **independent booleans of which
at most one is active at a time** (true for both `filled` and `body_diode`): the
canvas paints the active variant's `geometry`, otherwise the base geometry. A
definition needing *combinations* must bake one geometry per active-set (keyed by
the sorted active names); the editor emits combination geometry only when the
author opts in, and `log`s any combination it did not bake. Mutually-exclusive
groups (radio rather than checkbox) are out of scope for Phase 1.

**Scope — booleans only.** Variants model boolean attributes. Non-boolean
CircuiTikZ styling (enumerated shapes, numeric options, colors) is handled by the
per-instance free-form `options` string the user already types (PROJECT_SPEC §5.8),
not by variants.

**Render scale ≠ alignment scale.** Render-time scale that affects *only the body*
(not pins) — the diode `diodes/scale=0.8` — lives in `source.ctikzset`, **not** in
`alignment.scale`; it is a rendering parameter, not a grid correction, and must
stay equal to codegen's `DIODE_SYMBOL_SCALE` (PROJECT_SPEC §5.2). The editor
surfaces it as a per-definition render option and the §15 test asserts the two
stay in sync.

### 5.5 Baked geometry

Produced by §8. Identical in meaning to a manifest entry today, so the existing
`svgsym.parse_path()` and `symbol_paths()` consume it unchanged once they read
from a definition instead of `manifest.json`. The added `anchors`/`origin_svg`
are what let the editor compute pins and leads with zero hand measurement, and
let §15 verify alignment numerically.

### 5.6 Invariants

A definition is **valid** iff:

1. `kind` is unique across the store and non-empty.
2. `emission ∈ {two_terminal, multi_terminal, node}`.
3. Every `pins[*].offset` is a multiple of **0.25 GU** (the canvas minor grid,
   PROJECT_SPEC §3.1). The editor enforces this; the loader re-checks it.
4. The first pin is the origin and has `offset == [0,0]` **or** the definition
   records a non-origin `origin_svg` consistent with it (two-terminal sources
   place origin at the `+` pin at `(0,0)`).
5. For `multi_terminal`: `alignment.anchor_pin` names a pin; every pin with a
   non-null `ctikz_anchor` resolves in `geometry.anchors`.
6. **Single-correction rule (replaces §5.5's footgun):** for each axis of each
   pin, the pin is brought onto the grid by **either** `alignment.scale` **or** a
   lead in `alignment.leads`, **never both**. The editor enforces this live
   (§9.4) and the loader re-checks it (§7.5): after applying `scale` to the
   measured anchor, a pin already on-grid must not also have a lead, and a
   lead's target must equal the pin's grid offset.
7. `label_slots` are unique; `bbox` contains all pin offsets.
8. Baked `geometry.baked_with.scale == alignment.scale` (geometry was baked with
   the alignment scale applied — §6.2). A mismatch means the definition was
   edited after baking and must be re-baked.

A startup load that fails any check is reported the way PROJECT_SPEC §9.3 reports
schematic load failures, naming the first failing check and the offending `kind`.

### 5.7 Versioning

`cd_version` is the Component-Definition format version, independent of the app
version, the spec version, and the `.hv` `_FORMAT_VERSION` (PROJECT_SPEC §9.4).
It changes only when the CD schema changes. The loader accepts a known set and
rejects unknown versions with a descriptive error. Bundled definitions must
always be at the current `cd_version` (the §12/§15 analogue of the
`test_examples.py` rule).

---

## 6. Unified Alignment Model

**Decision:** the two confusable mechanisms in PROJECT_SPEC §5.5 (uniform
`xscale`/`yscale` correction vs. bridge lead wires) are unified into **one
editor-driven model with two composable tools**, each feeding both the canvas and
the LaTeX output through a single value.

### 6.1 One scale, two consumers

`alignment.scale = [sx, sy]` is applied:

- **At bake time** to the rendered geometry (§8.3), so the geometry stored in the
  definition is *already grid-correct*. This **removes the per-component
  `xscale`/`yscale` from `svgsym.Placement`** — the local transform becomes a pure
  `translate(-origin_svg) → optional rotate → uniform GRID_PX/SVG_PT_PER_GU`.
- **In codegen** as the node's `xscale=sx, yscale=sy` option (today's
  `_MULTI_TERMINAL_EXTRA_OPTS`), derived directly from the same field.

Because the *same numbers* drive both, the canvas and the LaTeX cannot disagree —
the class of bug §5.5 warns about becomes unrepresentable. Mirroring composes as
codegen already does it: `xscale=sx` becomes `xscale=-sx` (the existing regex
substitution in `_render_multi_terminal`), so the editor's scale and the
per-instance mirror remain orthogonal.

### 6.2 One lead list, two consumers

`alignment.leads = [{anchor, to}, …]` is emitted:

- **At bake time** as `\draw (NODE.anchor) -- (to);` appended to the source body
  (today's hand-written `TRIPOLES` lead strings), so the exported geometry
  extends to the grid target.
- **In codegen** as `\draw (node_id.anchor) -- (to);` (today's
  `_MULTI_TERMINAL_LEADS`).

Same anchor, same target, both places — agreement by construction.

### 6.3 Which tool for which pin

The editor chooses per pin, and the choice is mechanical:

1. Bake → read the measured anchor (GU, y-down).
2. If a **uniform** scale lands the off-grid pins on the grid (the MOSFET/BJT
   case: one ratio fixes all terminals at once), apply `scale`; those pins need
   **no** lead. Scale is preferred when it resolves several pins together.
3. Any pin still off-grid after scale gets a **lead** to its snapped grid offset.
   Leads are preferred when an anchor is already rectilinearly close (the op-amp
   case) or when a diagonal extension is needed that a uniform scale can't give.
4. The single-correction invariant (§5.6.6) guarantees no axis is corrected twice.

This is the §5.5 "Step 3 — choose a strategy" decision, but made interactively
with live numeric feedback instead of by hand, and enforced rather than
documented.

---

## 7. Derivation — How the App Consumes a Definition

At startup the app loads the definition store and **derives** every structure
that is hand-maintained today. No definition field is duplicated into Python.

### 7.1 `REGISTRY` (replaces `app/components/registry.py` entries)

`ComponentDef(kind, display_name, category, bbox, pins, label_slots,
tikz_keyword, default_span, resizable, component_class)` is built field-for-field
from the definition (`pins` → `PinDef(name, offset)`; `component_class` per §7.4).
The hand-written `_RESISTOR`, `_OPAMP`, … literals are deleted; `REGISTRY` becomes
`{cd.kind: build_component_def(cd) for cd in load_store()}`.

### 7.2 Symbol geometry + placement (replaces `manifest.json` + `svgsym` tables)

`svgsym` reads `geometry.paths`/`glyphs` from the definition (same shape as a
manifest entry). `_local_transform(kind)` is built from `geometry.origin_svg`
(anchor), the definition's rotation convention, and the uniform scale only — the
hand-coded `_MULTI_ANCHORS`, `_HORIZONTAL_BIPOLES`, `_VERTICAL_SOURCES`, and the
per-component `xscale`/`yscale` all go away (scale is pre-baked, §6.1).

### 7.3 Codegen tables (replaces the five tables in `app/codegen/circuitikz.py`)

| Today's table | Derived from |
|---------------|--------------|
| `_TWO_TERMINAL_KINDS` | `{cd.kind for cd if cd.emission == "two_terminal"}` |
| `_MULTI_TERMINAL_KINDS` | `{cd.kind for cd if cd.emission == "multi_terminal"}` |
| `_MULTI_TERMINAL_ANCHOR_PIN` | `cd.alignment.anchor_pin` + that pin's `ctikz_anchor` |
| `_PIN_TO_CTIKZ_ANCHOR` | `{pin.name: pin.ctikz_anchor for pin in cd.pins}` |
| `_MULTI_TERMINAL_EXTRA_OPTS` | format `cd.alignment.scale` → `"xscale=sx, yscale=sy"` |
| `_MULTI_TERMINAL_LEADS` | `[(lead.anchor, pin_for(lead)) for lead in cd.alignment.leads]` |

The import-time validation in `_validate_codegen_tables()` is subsumed by the
definition invariants (§5.6) — a `multi_terminal` definition cannot load without
a complete pin→anchor mapping, so the "silent fallback to bare coordinates"
failure mode is structurally prevented.

### 7.4 Item class (replaces most of `app/canvas/items.py`)

Every editor-produced symbol maps to **one generic `SvgSymbolItem`** — the
current base `ComponentItem`, parameterized by `kind`, already does the data-driven
painting. The empty per-kind subclasses (`ResistorItem`, every diode, sources,
op-amp, BJTs) are deleted and `ITEM_CLASSES` falls back to `SvgSymbolItem` for any
`kind` not explicitly overridden. Bespoke classes are **kept** only for the
non-editor kinds: `_ResizableTwoTerminalItem` (open/short/bipole),
`_GroundBase` (bbox + symbol detail), `_MosfetItem` (bbox override), and the
drawing primitives. Per-instance state needs **no per-kind subclass**: the base
`Component` carries a generic `variants: dict[str, bool]` (§7.4a), so
`DiodeComponent`/`MosfetComponent` collapse into `Component` (retained only as
deserialization aliases for legacy `.hv` files). `component_class` therefore stays
`Component` for every editor-produced kind.

### 7.4a Variant plumbing (replaces the hardcoded `filled`/`body_diode` pieces)

Declaring a variant (§5.4) derives every layer below with **no per-attribute
code** — replacing the two hardcoded implementations that exist today:

| Layer | Today (hardcoded ×2) | Derived from `variants` |
|-------|----------------------|-------------------------|
| Per-instance state | `DiodeComponent.filled`, `MosfetComponent.body_diode` | `Component.variants: {name: bool}` |
| Inspector | `DiodeSection`, `BodyDiodeSection` (`app/ui/properties.py`) | one auto-generated checkbox per declared variant, labelled `variant.label` |
| Undo command | `SetFilledCommand`, `SetBodyDiodeCommand` | one generic `SetVariantCommand(component_id, name, value)` |
| Canvas geometry | `*` / `_bodydiode` manifest-key suffixing in `svgsym` | the active variant's baked `geometry` (§5.4) |
| Codegen | hardcoded `D*` (suffix) / `, bodydiode` (option) | `tikz` + `tikz_mode` of each active variant |

**`.hv` persistence — this crosses into PROJECT_SPEC §9.** A component instance
stores its active variants as a map, e.g. `"variants": {"filled": true}` (omitted
entirely when empty / all-false, so unaffected files are byte-identical). For
**back-compat** the loader reads the legacy keys `"filled": true` and
`"body_diode": true` into the `variants` map; the saver writes only the new
`variants` map. Because this changes the on-disk schema, when the feature is
implemented PROJECT_SPEC §9.2 (schema) and §9.3 (validation/back-compat) **must be
updated in the same change set**, and a regression test must load both a
pre-variants `.hv` (legacy keys) and a new-format one (§15). Whether this warrants
a `_FORMAT_VERSION` bump is a PROJECT_SPEC §9.4 decision at implementation time —
the additive, back-compatible read suggests not, but the call belongs with §9.4's
"can this build read this file?" rule.

### 7.5 Load-time validation

The loader runs §5.6 on every definition and the cross-definition checks
(unique `kind`, unique categories ordering) before building `REGISTRY`. Failure
is fatal at startup for a bundled store (a packaging bug) and surfaced as a
skip-with-error for a user store in Phase 2.

---

## 8. The Bake Pipeline

The bake is the automated form of PROJECT_SPEC §5.5 Steps 1–4. It reuses the
existing renderer and parser; the **new** part is emitting and reading the anchor
dump so measurement is mechanical.

**Bake authors no metadata.** It only (re)generates the `geometry` block of an
*already-authored* definition; it never creates `kind`, `pins`, `alignment`, or
any other authored field. Definitions are authored by the editor (§9) or, for the
existing component set, by the migration script (§12); bake fills in their
geometry. (So bake does not, by itself, define the `.hvc` files for the existing
parts — §12.1 does that, and bake is the geometry sub-step it calls.)

### 8.1 Inputs and outputs

Input: a definition's `source` block + `alignment` (scale + leads) + the list of
`ctikz_anchor` names referenced by pins/leads. Output: the `geometry` block
(§5.5), written back into the definition.

### 8.2 Render

Build a `standalone` document (the existing `_DOC` template) with
`source.border_pt`, `source.preamble`, `source.ctikzset`, and a body consisting
of `source.body` **plus** the lead `\draw`s (§6.2) **plus** an anchor-dump
`\path let … \pgfextra{\typeout{…}}` for each referenced anchor and for the
origin pin. Run `latex -interaction=nonstopmode` then `dvisvgm --no-fonts`.

### 8.3 Apply scale, parse geometry

If `alignment.scale != [1,1]`, wrap the symbol in a TikZ scope (or emit the
node with `xscale/yscale`) so the **rendered** geometry is already corrected
(§6.1); record it in `geometry.baked_with.scale`. Parse the SVG with the existing
`parse_svg()` logic (`paths`, `glyphs` with baked `matrix`) — unchanged from
`tools/export_circuitikz_svgs.py`.

### 8.4 Read anchors (the automation)

Parse the `\typeout` lines for each anchor's pt coordinate, convert to the
relevant spaces (§4), and store `geometry.anchors` (SVG pt) and `origin_svg`. The
editor uses these to (a) propose pins at the measured anchors, (b) show how far
each pin is from the grid, and (c) compute the scale/lead needed — none of which
a human computes. This is §5.5 Step 1 + Step 2 + the `local_x/local_y` check from
Step 4, done by the machine.

### 8.5 Determinism and re-bake

As today, `latex`/`dvisvgm` are byte-stable for a fixed toolchain (PROJECT_SPEC
§5.2). `geometry.baked_with.toolchain` records the toolchain string so a drift
across TeX Live / circuitikz versions is visible in diffs. A `bake-all` command
re-bakes every bundled definition (the analogue of re-running
`export_circuitikz_svgs.py`); the §15 golden tests catch unintended geometry
change.

### 8.6 When bake runs (triggering)

Bake is **explicit and asynchronous** — never synchronous-on-keystroke, since one
render is seconds of `latex`+`dvisvgm` and must not block the UI:

- **On import / on demand.** The author triggers a bake with the **Bake** action
  (§10) after entering or changing the source command; the initial
  import → bake → preview is the first thing that happens (§9 step 2). The CLI
  `bake` / `bake-all` (§8.5) is the head-less equivalent used for migration (§12)
  and CI.
- **Geometry-affecting edits mark the definition _stale_.** Editing any field that
  changes what is rendered — `source.*` (body, ctikzset, preamble, border_pt),
  `alignment.scale`, `alignment.leads`, or a variant's `source_body` —
  invalidates the baked `geometry` and sets a stale flag; the machine-checkable
  part is invariant §5.6.8 (`geometry.baked_with.scale == alignment.scale`).
  Metadata-only edits (pin names/offsets/`connects`, `label_slots`, `bbox`,
  `default_span`, `resizable`, `display_name`, `category`, and a variant's
  `label`/`tikz`/`tikz_mode`) do **not** invalidate geometry, so they need no
  re-bake.
- **Save requires fresh geometry.** A stale definition cannot be saved (§5.6.8) —
  the author re-bakes first. After a re-bake the editor re-reads
  `geometry.anchors` and re-validates the pins/leads against the new measurements
  (an anchor may have shifted), flagging any pin now off-grid.
- **Optional debounced auto-bake.** As a convenience the editor MAY auto-trigger a
  bake a short debounce after a geometry-affecting edit settles, delivered on a
  background worker exactly like the on-canvas math renderer (PROJECT_SPEC §5.8
  `render_async`) and updating the preview when it lands. It never replaces the
  explicit Bake action: always async, never blocking, and a missing/failed
  toolchain degrades to the button with the log shown.

---

## 9. Editor Workflow

A single component is authored in this order. Each step gives immediate,
numeric, on-grid feedback.

1. **Import.** Enter `tikz_keyword`, `emission`, and the `source.body` (a
   sensible default body is offered per emission mode). Set `display_name`,
   `category`, render options.
2. **Bake & preview.** Run §8. The rendered symbol appears on a GU grid with the
   measured CTikZ anchors marked. If `latex`/`dvisvgm` fail, show the log
   (matching `tools/export_circuitikz_svgs.py`'s `[WARN]` behavior) and stop.
3. **Place pins.** Click a measured anchor to promote it to a pin; name it; mark
   `connects`. The first pin placed is the origin. The editor shows each pin's
   offset in GU and its distance from the nearest grid node.
4. **Align.** Adjust `scale` (x/y) and watch pins move toward the grid; for any
   pin still off-grid, draw a lead from its anchor to the snapped target. The
   single-correction rule (§5.6.6) is enforced — the UI refuses to both scale and
   lead the same axis of a pin, explaining why.
5. **Label.** Declare `label_slots`. (Per-instance label *placement* is the
   existing §5.8 machinery; the editor only lists valid slot names.)
6. **Variants.** Optionally add variants (§5.4); each is baked from its own body.
7. **bbox & span.** Accept or tune the auto-proposed `bbox`/`default_span`.
8. **Validate & save.** Run §5.6; on success write the `.hvc`. The editor can
   immediately reload it into a throwaway `REGISTRY` to render a palette
   thumbnail and a sample placement, proving the round-trip.

### 9.1 Re-editing

Opening an existing `.hvc` restores all authored fields and the baked geometry.
Changing `source`/`alignment` marks geometry stale and requires a re-bake before
save (invariant §5.6.8); see §8.6 for the full triggering rules and which edits
do and do not invalidate geometry.

---

## 10. Editor UI

Phase 1 is a standalone window (`python -m app.componenteditor` or a
`tools/component_editor.py` entry); Phase 2 hosts the same widgets in a dialog.
Layout:

- **Left — Source panel:** identity fields, emission combo, `source.body` text
  area, render options, **Bake** button with a stale-geometry indicator (§8.6),
  and the `latex`/`dvisvgm` log.
- **Center — Alignment canvas:** the baked symbol on a GU grid; measured anchors
  as markers; placed pins as draggable dots with live GU readouts; leads drawn as
  rubber-band segments that snap to grid; the bbox as an editable rectangle.
- **Right — Inspector:** pin list (name, offset, ctikz_anchor, connects), scale
  spin-boxes (x/y) with a live "pins on grid" indicator, lead list, label slots,
  variants, default span, resizable toggle.
- **Bottom — Output preview:** the derived `ComponentDef`, the would-be codegen
  node line for a sample placement, and validation results — so the author sees
  exactly what §7 will produce before saving.

The center canvas reuses canvas primitives (grid rendering, `GRID_PX`, snapping)
where practical, but must not depend on a live `SchematicScene`; it operates on
the definition under edit.

---

## 11. Integration and Packaging

### 11.1 Definition store

Bundled definitions live in a single directory (proposed:
`components/definitions/*.hvc`), resolved through `resource_path()` exactly like
`tools/circuitikz_svgs/manifest.json` is today (PROJECT_SPEC §11.1). The store is
the **runtime resource**; the `.svg`/`.dvi` scratch from baking is **not**
bundled.

### 11.2 Startup

`app/components/registry.py` builds `REGISTRY` from the store (§7.1); `svgsym`
reads geometry from the store (§7.2); `circuitikz.py` derives its tables (§7.3).
The standalone `manifest.json` and the per-kind/placement/codegen tables are
removed once §12 migration is complete and green.

### 11.3 Module boundaries (so Phase 2 is additive)

- **Qt-free core** (`app/components/definition.py` or similar): the CD dataclass,
  JSON (de)serialization, §5.6 validation, §7 derivation, and bake orchestration
  (subprocess + parse + anchor read). Runs head-less; covered by the existing
  non-Qt test path.
- **GUI** (`app/componenteditor/…`): all widgets, the alignment canvas, the
  standalone window. Imports the core; nothing in the core imports it.

This mirrors the existing split where `geometry.py`/`wiregeometry.py` are Qt-free
and the scene/items are Qt.

### 11.4 Optional aggregated artifact

If a large store ever makes per-file startup loading measurably slow, a build
step may emit `components/components.json` (all definitions concatenated) as a
pure artifact the runtime prefers when present. The `.hvc` files stay
authoritative; the aggregate is regenerated, never hand-edited. Not needed for
the ~40-component Phase-1 set.

---

## 12. Migration of the Existing Component Set

The 39 current components must become definitions **without changing rendered or
generated output**. Plan:

1. **Generate definitions from the current tables.** A one-shot script reads
   `REGISTRY`, `svgsym` placements, the codegen tables, and `manifest.json` and
   emits one `.hvc` per kind, carrying the *current* geometry verbatim (so the
   first migration step changes nothing on disk that the app renders).
2. **Re-bake under the unified model.** Re-run §8 for the multi-terminal kinds so
   scale is baked into geometry (§6.1) and `svgsym` scale is dropped. This is the
   only step that changes geometry bytes; it is gated by the golden tests below.
3. **Switch the runtime to the store** (§7) behind the same tests.
4. **Delete** the hand-maintained literals/tables and the empty `ComponentItem`
   subclasses (§7.4).

Acceptance for migration: for every existing `kind` and every variant, the
derived `ComponentDef` equals today's, the rendered `symbol_paths()` match within
sub-pixel tolerance, and `generate()` output is byte-identical for the bundled
examples (PROJECT_SPEC §9.5 / `test_examples.py`).

---

## 13. Consequences, Risks, and Open Decisions

These are the "consequences not yet thought of" — surfaced for decision.

1. **Toolchain drift is now visible but real.** Re-baking on a different TeX
   Live / circuitikz than the checked-in geometry was baked with will shift
   coordinates. Mitigation: keep baked geometry checked in (don't re-bake on
   every build), record `baked_with.toolchain`, and gate re-bakes with golden
   tests. **Open:** pin a toolchain in CI for byte-stable bakes?
2. **Scale-baked-into-geometry changes the manifest format.** Every multi-terminal
   symbol is re-baked once (§12.2). One-time, test-gated, but it is a real
   geometry change in the diff. **Open:** accept the one-time churn (recommended)
   vs. keep scale at display time (less clean, preserves §5.5's two mechanisms).
3. **Pin-grid granularity — resolved to 0.25 GU.** The pin grid is **0.25 GU**
   everywhere (the canvas minor grid, PROJECT_SPEC §3.1), reconciling a former
   docs disagreement (the `PinDef.offset` docstring once said "multiples of 0.5").
   Code and docs now agree: `model.py`'s docstring, `test_registry`
   (`test_all_pins_on_quarter_grid`), PROJECT_SPEC §5.5/§7.2/§13, and this spec.
   Existing pins sit on the coarser 0.5 grid (a subset of 0.25), so nothing
   moved. Note this is the *pin/coordinate* grid; the resizable-component **span**
   still commits on 0.5 GU on purpose, so rect/circle half-span connection points
   land back on 0.25 (see `drag.py` `commit_endpoint_drag`).
4. **Coordinate-only pins** (a pin not backed by a named CTikZ anchor) need a
   codegen story (emit a bare coordinate, or a lead from the nearest anchor).
   Phase 1 can require every pin to map to a CTikZ anchor and defer arbitrary
   pins; flag any imported symbol that has a connection point with no anchor.
5. **Generic `SvgSymbolItem` and thumbnails.** Palette thumbnail rendering
   (PROJECT_SPEC §5.3) must work from the generic item; verify the MOSFET/ground
   bbox overrides are preserved when their subclasses are kept (§7.4).
6. **Re-export tooling overlap.** `tools/export_circuitikz_svgs.py` and the
   editor's bake do the same render+parse. **Decide:** refactor the shared
   render/parse into the Qt-free core and have both call it (recommended), so
   there is one renderer, not two.
7. **Phase 2 security (deferred but pre-noted).** Importing an arbitrary
   CircuiTikZ command means running arbitrary LaTeX. For the dev tool this is
   fine. For an end-user feature it is code execution: `pdflatex`/`latex` must run
   with shell-escape **disabled** and ideally sandboxed, and the existing
   `tests/test_latex_security.py` posture must extend to editor input. Do **not**
   ship Phase 2 without this.
8. **`.hv` compatibility.** Saved schematics reference `kind` strings. As long as
   migrated definitions keep the same `kind`s, existing `.hv` files load
   unchanged. A user-authored custom `kind` (Phase 2) absent from a given install
   would fail load the way an unknown component does — needs a Phase-2 policy.
9. **Two sources of truth for diode scale.** `DIODE_SYMBOL_SCALE` (codegen) and
   the render `ctikzset` must stay equal (PROJECT_SPEC §5.2). The definition now
   *holds* the render value; codegen should derive its constant from the
   definition store too, or a test must assert equality (§15).
10. **Custom attributes (variants) are generic, with two bounded limits.** Per the
    decision to make variants fully generic (§5.4, §7.4a), declaring a boolean
    attribute derives storage, UI, command, geometry, and codegen with no
    per-attribute code, and the per-instance value moves into a `.hv` `variants`
    map (back-compat read of legacy `filled`/`body_diode`; a PROJECT_SPEC §9
    change at implementation time). Two cases stay deferred because they need
    extra baked geometry or a different control: **combinations** of two
    simultaneously-active variants (2ᴺ renders) and **mutually-exclusive groups**
    (radio, not checkbox). The real cases (`filled`, `body_diode`) are single
    independent booleans, so neither blocks Phase 1; the editor must `log` any
    combination it does not bake rather than silently drop it.

---

## 14. Implementation Phases

| Phase | Deliverable |
|-------|-------------|
| **0** | Qt-free CD dataclass (incl. `variants`) + per-instance `Component.variants` map + JSON I/O + §5.6 validation + golden tests. No UI. |
| **1** | Bake pipeline (§8) reusing/refactoring the existing render+parse; CLI `bake`/`bake-all`. |
| **2** | Derivation (§7), incl. generic variant plumbing (§7.4a): app loads a store of definitions for the **existing** set behind a feature flag, with byte-identical codegen + sub-pixel geometry (§12), auto-generated inspector checkboxes + `SetVariantCommand`, and `.hv` `variants` round-trip with legacy back-compat. |
| **3** | Editor GUI (§9, §10): the user-facing MVP — import command, rescale to grid, draw leads, place pins + labels, declare boolean variants, save. |
| **4** | Delete the hand-maintained tables, empty subclasses, and the hardcoded `filled`/`body_diode` pieces; flip the runtime to the store by default. |
| **5 (future)** | Combinatorial / mutually-exclusive variant geometry (§13.10); then Phase-2 embedding + security (§13.7). |

The user-facing MVP described in the request is **Phase 3**, but it rests on
Phases 0–2 existing first so that what the editor saves is actually what the app
consumes.

---

## 15. Test Specification and Acceptance Criteria

Mirrors PROJECT_SPEC §13 in style. New test modules:

| Module | Covers |
|--------|--------|
| `tests/test_component_def.py` | CD (de)serialization round-trip; every §5.6 invariant (valid + each failing case); `cd_version` accept/reject (§5.7). |
| `tests/test_cd_derivation.py` | §7: a definition derives the correct `ComponentDef`, `_PIN_TO_CTIKZ_ANCHOR`, `_MULTI_TERMINAL_*`, and emission classification; the single-correction rule (§5.6.6) is enforced. |
| `tests/test_bake.py` | §8, gated on `latex`/`dvisvgm` like `test_mathrender.py`: a known command bakes to expected `anchors`/`origin_svg` (within tolerance) and parses `paths`/`glyphs`; determinism (same input → same bytes). |
| `tests/test_cd_migration.py` | §12 golden: every migrated definition reproduces today's `ComponentDef`; `symbol_paths()` matches within sub-pixel tolerance; `generate()` is byte-identical for the bundled examples. |
| `tests/test_variants.py` | §5.4 / §7.4a: a declared variant derives a per-instance `variants` toggle, the active variant's geometry is selected on canvas, and codegen applies the right `suffix`/`option`; a generic `SetVariantCommand` round-trips through undo; `.hv` save/load round-trips the `variants` map **and** reads legacy `filled`/`body_diode` keys into it (back-compat). |
| `tests/test_examples.py` (extend) | Bundled definitions all load at the current `cd_version`; the diode render scale equals `DIODE_SYMBOL_SCALE` (§13.9). |

**Acceptance criteria.**

- **AC-1 (no regression):** with the store enabled, all existing tests pass and
  `generate()` output for `examples/*.hv` is byte-identical to pre-migration.
- **AC-2 (alignment):** for a multi-terminal definition, every pin lands on the
  0.25 GU grid in both the canvas (`symbol_paths` + `_local_transform`) and the
  LaTeX (`node[...anchor...] + leads`), verified numerically — no human reads a
  coordinate.
- **AC-3 (single source of truth):** changing one field in one `.hvc` changes the
  registry, the canvas symbol, and the codegen output consistently, with no other
  file edited. Deleting the hand-maintained tables leaves all tests green.
- **AC-4 (round-trip):** the editor saves a definition that reloads into a
  `REGISTRY` and renders a correct palette thumbnail and sample placement.
- **AC-5 (footgun closed):** it is impossible to save a definition that corrects
  one pin axis by both scale and lead (§5.6.6), proven by a rejected-case test.

---

## 16. Out of Scope

- End-user (in-app) component authoring and a user component store (Phase 2; the
  data model is ready, the host UI and security are not built here).
- Editing the drawing primitives (`text_node`/`rect`/`circle`/`bipole`).
- A general TikZ drawing surface or round-tripping arbitrary TikZ.
- Changing the per-instance label-placement convention (PROJECT_SPEC §5.8).
- Bundling a TeX toolchain; baking always uses the developer's installed
  `latex`/`dvisvgm` (PROJECT_SPEC §11.1).
