# Changelog

All notable changes to Heaviside are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-06-28

### Added
- **Rotate components in 45° increments.** `Ctrl+R` now turns the selection in 45°
  steps (was 90°), so a symbol can sit diagonally — handy alongside La Plata wires. The
  inspector's Rotation control offers all eight orientations (0–315°). A
  45° orientation puts the pins off the grid, reached by the existing wire magnet /
  pin-axis alignment. The step falls back to 90° when a wire is part of the selection
  (rotating free wire vertices 45° would leave them off-grid and invalid); a
  components-only rotation reshapes connected wires to follow.
- **La Plata wire routing (45° wires).** The wiring quick-bar now leads with two
  mutually-exclusive routing-mode tiles — **Manhattan** (90°, the default) and **La
  Plata** (45°) — with the active mode shown checked. La Plata adds a diagonal leg
  (octilinear routing) for the angled connections the CircuiTikZ manual uses in places.
  No new grid is needed: a 45° leg between two 0.25 GU points threads grid nodes, so it
  stays grid-valid. La Plata also routes **off off-grid pins** — scaled gates and
  **45°-rotated components** — with the 45° leg coming straight off the pin (no jog):
  the pin's 45° diagonal acts as an artificial grid line that the lead and its corner
  ride. The routing style is an editor setting (not saved); a diagonal
  segment is self-describing in the wire geometry. v1 limitations: line-hops are not
  drawn on diagonal segments, and mid-segment T-junctions onto a diagonal are not
  offered as a snap target (diagonal wires connect at their vertices).
- **Toggle auto-drawn open-circle terminals and junction dots per document.** The
  Document inspector's **Display** group gains two checkboxes: **Mark open wire ends**
  (the `ocirc` open circles at dangling wire ends) and **Draw junction dots** (the solid
  `circ` dots where wires/pins are tied). Both default on (the schematic-drawing
  convention) and apply to the canvas and the LaTeX export alike; turning one off
  suppresses that whole class of dots document-wide. (`.hv` format → 0.10.)
- **Wire ends may sit off-grid when aligned with a pin.** Every component pin's x and y
  now act as an *artificial grid line* extending across the canvas, so a wire end (while
  drawing, or while dragging a wire vertex/junction) can snap off the 0.25 GU grid to
  stay **collinear with any pin** — not just the pin it connects to. Each axis snaps to
  whichever is nearer, the grid or a pin's axis line, while the pin/wire magnet still
  wins when the cursor is closer to an actual connection point. When an end snaps to a
  pin line, a faint dashed **alignment guide** is drawn through that pin so it's clear
  why the end went off-grid; it clears when the cursor leaves every pin axis. This makes
  it easy to route clean Manhattan wires into the manual library's off-grid native
  anchors (scaled gates, transistors, BNC / seven-segment geometric anchors, …).
- **Wiring quick-bar gains wire + annotation tools.** The slim strip on the right edge
  of the canvas now leads with a **Manhattan wire** tool (click to enter Wire mode) and
  the **current** / **voltage** annotations (`short` / `open`, shown with italic *i* /
  *v* glyphs), above the existing Terminals connection markers — so the everyday wiring
  gestures are one click from the canvas.
- **Expose every anchor the CircuiTikZ manual documents — including geometric.** The
  component generator merges the manual's full per-keyword anchor list (via
  `extract_doc_anchors.extract(keep_geo=True)`) into each symbol, so every documented
  anchor becomes a wireable pin: electrical terminals, body-border anchors
  (`bpin`/`bout`/…), the **geometric** anchors (`north`/`center`/`left`/`right`/…, plus
  the `circle …`/`tube …` variants), internal centres, body-diode/circle taps, and the
  option-state `no…` anchors — e.g. a BNC's `left`/`right`/`center`, the seven-segment
  display's segments (`a`–`g`, `dot`), an `npn`'s body-diode and circle anchors. Only
  the label/decoration *draw-positions* (`label`/`text`/`tip`/`arrows`) and the
  non-referenceable `<anchor>.<direction>` compass sub-anchors are skipped. Single-point
  symbols (grounds, supplies, terminal markers) stay single-point — their compass isn't
  exposed, since that would break their `\node at` emission. Coincident anchors collapse
  (named terminals win over geometric aliases), so no duplicate pins appear. The
  parametric `muxdemux`'s body-border anchors remain the one known gap. Audit coverage
  with `components/extract_doc_anchors.py`.
- **Centre-tap terminals from the manual.** Components the CircuiTikZ manual documents
  with an on-axis centre tap now expose it as a wireable pin: the **inductor `midtap`**,
  the centre-tapped source windings (`ioosource`/`voosource`/`oosourcetrans` →
  `centerprim`/`centersec`; `tacdc`/`tdcac`/`tacac` → `ac mid in`/`ac mid out`), and the
  **transformer** primary/secondary centre taps (`L1.midtap`/`L2.midtap`, reached via the
  CircuiTikZ coil sub-node anchors).
- **Document-level diode scale.** The Document inspector gains a **Diode scale** field
  (the CircuiTikZ `diodes/scale` body size, default 0.8) — the manual recommends
  shrinking the large default body. The export emits `\ctikzset{diodes/scale=<value>}`
  and the canvas reflects it live (the diode body scales about its centre, leaving the
  leads). Replaces the former hard-coded constant.
- **Expanded component library generated from the manual (experimental, opt-in).**
  `components/generate_library.py` builds a component library covering the CircuiTikZ
  manual's components by combining the manual scrape with the existing render/geometry
  pipeline, writing it side by side with the curated set at
  `components/generated/{definitions,geometry}.json`. Switch the app onto it with the
  `HEAVISIDE_COMPONENT_LIB=manual` environment variable (default `curated` — the
  curated library remains the fallback). Terminals are discovered by probing each
  shape (the manual under-documents anchors), with CircuiTikZ anchor names used
  verbatim: ~399 components covering path bipoles (axial in/out + off-axis gates/
  wipers), single-point nodes, and centre-placed multi-terminal nodes (transistors,
  op-amps, gates, transformers) — plus measured `text_anchor`s and auto-detected
  option variants (e.g. `bodydiode`). Components render at their **true CircuiTikZ
  size** with their natural (mostly off-grid) pin positions — the manual library bakes
  **no** grid-alignment scale; wires reach pins via the canvas magnet / off-grid snap
  lines rather than the symbol being stretched onto the grid. Logic gates are **parametric** (`number inputs`
  2–8, pins/geometry measured per value), and transformer **polarity-dot anchors**
  are exposed as connection points (the user places dots themselves). Multi-terminal
  **BJTs** (`bjtnpn`/`bjtpnp`) are parametric in **collectors** and **emitters** (1–4
  each); they expose the primary base/collector/emitter terminals `B`/`C`/`E` (like
  the curated npn/pnp) and add the numbered branch terminals `C1…`/`E1…` only when
  more than one collector/emitter is configured. The **mux/demux** is parametric in
  data **inputs** (2–8) and **select** lines (1–4). IC **chips** (`dipchip`/`qfpchip`)
  are parametric in **num pins** (`pin 1…pin N`). Multi-pin shapes (chips, flip-flops,
  mux-family) now surface *all* their numbered pins, and their inner ``b``-prefixed
  *border* anchors are no longer mistaken for separate pins. Not yet handled: `.hv`
  cross-library compatibility.
- **One-step launch on the manual library.** `main.py` accepts a `--manual` flag (and
  a `run-manual.sh` launcher wraps it) that selects the manual-scraped library without
  setting the environment variable by hand. The curated library stays the default for
  plain launches, the tests, and the bundled examples (which don't yet load under the
  manual set).
- **Document symbol style (experimental, manual library).** A document-level **symbol
  style** switches whole CircuiTikZ families at once — **american/european resistors**
  and **cute/american/european inductors** (including transformers) — from the Document
  inspector, instead of separate per-style components. The generator auto-detects which
  components respond to each style axis and bakes the per-style geometry; the canvas
  renders the chosen style and codegen emits the matching global `\ctikzset`. Stored in
  the `.hv` file (format **0.7**; absent → all-american, so older files are unchanged).
- **Terminal markers follow the component they sit on.** A junction dot (or other
  Terminals-category marker) placed on a component's anchor now moves with that
  component when it's dragged — e.g. polarity dots on a transformer's dot anchors
  track the transformer. The follow is previewed live, commits with the move, and
  undoes together.
- **Manual library: documented body anchors exposed.** Components now expose the
  body/edge anchors the manual explicitly documents — a logic gate's `bin`/`bout`
  (so you can place an inversion bubble on the gate body) and a flip-flop's border
  anchors — in addition to the lead-tip wiring terminals. Chips' redundant probed
  `bpin N` border anchors stay stripped (the manual documents none for them).
- **Manual library: one mux/demux element, fixed body size, drag-to-resize.** The
  redundant `demux` kind is gone — a demultiplexer is just a **mirrored**
  multiplexer, so the single parametric `muxdemux` covers both. Its body is now a
  **fixed** size that no longer grows as you change the input/select counts (the
  pins repack inside it), and the placed instance is **resizable by dragging a 2D
  corner handle** (independent width/height) — connected wires follow, it's
  undoable, and the export scales to match. This 2D node-resize is reusable
  infrastructure (`RESIZABLE_NODE_KINDS` + `_ResizableNodeItem`) for future
  multi-pin blocks.
- **Manual links in the palette.** The open palette category now shows its **full
  manual section name** in the header (e.g. "RESISTIVE BIPOLES" for the short
  **Resistors** card — long names wrap within the palette pane rather than
  clipping) plus a small documentation-link button next to it that opens
  the matching section of the online
  [CircuiTikZ manual](https://rmano.github.io/circuitikz/node-The-components-list.html)
  in the browser. Covers every manual-library category with a documented section
  (and the curated-library categories that share one); bespoke groups with no
  manual section keep the short name and show no link.
- **Potentiometer wiper terminal.** Potentiometers (`pR` american, `epot`
  european) are now modelled as the three-terminal devices they are: in addition
  to the two end terminals they expose a **wiper** connection point at the
  CircuiTikZ `wiper` anchor, so you can wire the slider
  ([circuitikz#945](https://github.com/circuitikz/circuitikz/discussions/945)).
- **Transformer centre taps.** Every transformer (all three coil styles, air- and
  iron-core) now exposes a **primary** and a **secondary** winding centre-tap
  connection point (the coils' `midtap` anchors), for centre-tapped transformer
  circuits.
- **Doc-anchor discovery tool** (`components/extract_doc_anchors.py`). Parses the
  CircuiTikZ manual source to list every documented component anchor and flags the
  ones Heaviside does not yet expose — a discovery aid for finding missing anchors,
  per the maintainer's suggestion in the discussion above. It can also emit a
  **complete component-and-anchor catalog table** for *all* ~440 documented
  components (`--format md` or `--format latex`, the latter a `longtable` ready to
  drop into the CircuiTikZ manual). Two opt-in probes recover what the manual omits:
  `--probe` reads each shape's *complete* anchor set straight from the engine's
  symbol table (a latex-only compile, no geometry rendering — filling gaps the prose
  under-documents, e.g. the gyrator), and `--options` lists the options each
  component actually responds to (e.g. `bodydiode` for MOSFETs) by rendering it with
  each candidate option and keeping those that change the symbol. (Geometric anchors
  are filtered case-sensitively, so real upper-case single-letter terminals like `E`
  emitter and `S` source are no longer mistaken for the `e`/`s` compass points.)
- **Manual scraper prototype** (`components/scrape_manual.py`). Treats the CircuiTikZ
  manual as the authoritative source and scrapes it (parse only, no rendering) into a
  structured per-component JSON database: keyword, type, category (the manual
  subsection), description, documented anchors + sub-node anchors, demonstrated
  options and parameters — from the reference macros **and** from `node[…]`/`to[…]`
  draw examples (catching keys the macros omit, e.g. logic gates' `number inputs`) —
  plus per-category prose option candidates. ~403 components across 27 categories.
  Its `--format md` table flags anchors the manual didn't enumerate as *not listed*
  (vs a genuine empty set). An optional source-probe step then compiles each component
  to recover what the manual under-documents: `--probe` reads the complete anchor set
  from the engine (latex only, ~20s — fills the *not listed* gaps), and
  `--probe-options`/`--probe-params` render-diff which options/parameters actually
  apply (e.g. confirming `number inputs` on every gate).

- **Wiring quick-bar beside the canvas.** A slim vertical strip docked at the right
  edge of the canvas gives one-click access to the most common wiring parts — filled
  and open junction dots (`circ`/`ocirc`) and jumpers — without a trip to the category
  palette. It lists only the kinds the active library defines, so it adapts to the
  curated/manual switch.

### Removed
- **The curated component library is gone; the manual-scraped library is now the only
  one.** The hand-curated set in `components/{definitions,geometry}.json` and its
  authoring scripts (`add_library.py`, `add_digital.py`, `add_transformers.py`,
  `add_text_anchors.py`, `generate_components.py`, `import_family.py`, `_probe.py`) have
  been removed. The app always loads the manual-scraped library from
  `components/generated/` — the `HEAVISIDE_COMPONENT_LIB` environment variable, the
  `--manual` flag, and the `run-manual.sh` launcher are removed (plain `python main.py`
  now starts the full library). The palette no longer splits the registry's categories
  into the curated-only **Gates (Am)/(Eu)** / **Supplies** groups or applies a curated
  display order; it shows the manual library's own categories in the manual's section
  order. American/european/cute symbols remain available via the per-document symbol
  **style** axis (Document inspector), which now applies to every document.

### Changed
- **Palette lists components in manual order within each category.** Within a category
  the symbols now appear in the order the CircuiTikZ manual presents them (sorted by
  manual source position), instead of american-before-european then alphabetical — so
  the palette mirrors the manual section by section. The bespoke `short`/`open`
  annotations sit at the front of Resistors where the manual lists them, rather than
  trailing after the library kinds.
- **New documents default the diode body scale to 0.6** (the CircuiTikZ manual's
  recommended `diodes/scale`), down from 0.8. Existing files keep their saved value, and
  a pre-0.9 file that predates the field still loads at 0.8 so its diodes are unchanged;
  only new documents pick up 0.6 (adjustable any time via the Document inspector).
- **Dropped the invented "Annotations" palette category.** The `open`/`short` voltage
  and current annotations now live in the **Resistors** category — the section the
  CircuiTikZ manual documents them in ("Resistive bipoles") — instead of an
  "Annotations" group with no manual counterpart. Their behaviour (translucent line,
  span-drawn, i/v labels) is unchanged.
- **Generated LaTeX references component terminals by anchor name.** A connection to a
  path device's extra terminal — a thyristor `gate`, a potentiometer `wiper`, an
  inductor/transformer/source centre tap — now emits a named-anchor reference
  (`to[L, name=node_xxxx] …` then `(node_xxxx.midtap) -- …`) instead of an opaque raw
  coordinate, matching how multi-terminal nodes (op-amp/transistor pins) are already
  referenced. This also applies to **terminal markers placed on an anchor**: a `circ`
  dropped on a transformer winding or an inductor centre tap now emits
  `\node[circ] at (node_xxxx.A1) {};` rather than a bare coordinate. This makes the
  exported code more readable and truer to the CircuiTikZ manual's idiom. A device is
  named only when something actually references such a terminal (no noise on unconnected
  taps); the two axial `to[…]` endpoints stay literal coordinates (CircuiTikZ has no
  clean named anchor for them).
- **Document inspector consolidated; display options moved out of Preferences.** The
  Document tab's help text is gone (the controls are self-explanatory). The
  **Mark unconnected pins** and **Draw line-hops** options moved from Preferences →
  Appearance into the Document inspector, so they travel with the `.hv` file instead of
  being a per-machine app setting. Adds `.hv` format **0.9** (`mark_unconnected_pins`,
  `line_hops`, `diode_scale` in `config`); older files load unchanged.
- **American/European logic gates scale via CircuiTikZ body `height`/`width` keys.**
  Resizing one of these gates (corner drag or the new inspector **Size** fields) now
  emits `\ctikzset{tripoles/<kw>/height=…, …/width=…}` instead of node `xscale`/`yscale`
  — the CircuiTikZ-recommended way, which redraws the symbol at native stroke width
  rather than stretching the strokes. The inspector gains numeric **Height** and
  **Width** fields (the key values) for these gates. Gates that don't expose the keys
  (not/buffer, the ieeestd family) keep `xscale`/`yscale`. Pins move identically either
  way, so the canvas and export stay in sync. The generated library bakes each gate's
  default height/width (american 0.8/1.1, european 0.65/1.4).
- **Inversion-bubble side is a user-set property, no longer inferred.** A single-
  terminal node now carries a **Placement** property (`node_side`: Center/Left/Right/
  Above/Below), set in the inspector and emitted as a TikZ placement key —
  `\node[ocirc, left] at (x,y) {};` — so the symbol sits tangent on the chosen side.
  This replaces the previous gate-context inference (which guessed the side from where
  the bubble sat and was fragile under rotation/mirror). The bubble is now just an
  ordinary single-terminal node: no gate-anchor reference, no special post-`\draw`
  pass. Adds `.hv` format **0.8** (`node_side`, written only when set); older files
  load unchanged.
- **Inversion bubbles get a smart default side and show it on the canvas.** Dropping an
  `ocirc`/`notcirc` on a logic-gate body anchor now seeds the **Placement** side
  automatically (pointing away from the body, so it lands tangent) — still editable in
  the inspector. The canvas draws the bubble shifted to its chosen side (tangent
  preview) while its pin stays on the anchor.
- **Single-terminal nodes are emitted as standalone `\node at` commands.** Grounds,
  supplies, and terminal dots (and any single-point node kind) now generate
  `\node[kind, opts] at (x,y) {text};` instead of an inline `(x,y) node[kind]{}` path
  operation inside the shared `\draw`. They connect to wires purely by coordinate (no
  named anchor), so this changes nothing electrically; it matches the junction,
  open-circle, and inversion-bubble dots (all already standalone `\node` commands) and
  reads more clearly.
- **Palette categories are split into "CircuiTikZ" and "TikZ" sections.**
  The component categories are now grouped under two headers: CircuiTikZ (all the
  circuit symbol categories) and TikZ (our own vanilla-TikZ drawing primitives —
  rectangle, circle, text, and anything added later). Clicking a card in either
  still opens that category below.
- **Logic gates (and flip-flops/ALU/adder) resize by dragging, not a dropdown.**
  The discrete Size dropdown (25%–200%) is gone; select a gate and drag its corner
  handle to resize it continuously, **independently in width and height** (like the
  muxdemux). Connected wires follow and it's undoable. The exception is the curated
  library's height-keyed gates, which lock aspect (a uniform scale) so their
  inversion bubble stays a circle in the export. The corner-drag resize is shared,
  item-driven infrastructure across all scalable symbols.
- **Resize snaps to grid-aligned sizes, not fixed steps.** Dragging a resize handle
  is now fully continuous, with a gentle magnet at the sizes that land a pin on the
  grid (instead of snapping the corner to fixed 0.25 GU steps), so it's easy to pick
  a size where wires connect cleanly while still being able to set any size in
  between.
- **All four corners of a resizable element are now drag handles.** Previously only
  one corner could be grabbed; you can now resize from whichever corner is handiest.
  Each corner resizes with the diagonally-opposite corner held fixed, so the grabbed
  corner follows the cursor at the **same rate from any corner** (previously corners
  near the symbol's anchor changed the scale much faster than far ones).
- **Pin markers no longer hide small symbols.** Pin indicator dots are now drawn as a
  solid red ring with a translucent fill instead of an opaque red disc, so a symbol
  under the marker stays visible on the canvas and in the palette thumbnail while the
  pin is still clearly marked. Junction-dot kinds (`circ`/`ocirc`), whose symbol *is*
  the connection point, omit the marker entirely (the pin still exists for wiring).

### Fixed
- **Diode scale now works under the manual library (and for `photodiode`).** Diode
  detection was keyed off a `filled` variant name that only the curated diodes carry,
  so the manual-scraped diodes (whose fill lives in distinct `empty`/`full`/`stroke`
  *kinds*) were never recognised: the canvas body never rescaled and the export never
  emitted `\ctikzset{diodes/scale=…}`. Detection is now category-based (a two-terminal
  member of the `Diodes` family — the set CircuiTikZ's `diodes/scale` actually resizes),
  in both the runtime (`library.is_diode`) and the geometry generator
  (`generate.is_diode`); both libraries were re-baked so every diode body sits at the
  0.8 baseline. Tripoles in the family (thyristor/triac/GTO/PUT) are excluded — their
  gate anchor moves with the scale and would desync the canvas pin from the export.
- **Inversion bubbles (`ocirc`/`notcirc`) on gate body anchors, matching the manual.**
  An open circle (or `notcirc`) dropped on a logic gate's `bin N`/`bout` body anchor
  now forms a NAND/NOR/inverted-input bubble: it shows no red pin marker, is
  selectable/movable with a click (instead of starting a wire), magnet-snaps onto the
  anchor, and follows the gate when it moves. On the canvas the bubble is drawn
  **centred on the anchor** (a preview); on **export** it is **tangent** — the circle
  outside the body on the correct side — via the CircuiTikZ manual's idiom,
  `\node at (gate.bin N) [ocirc, left]{}` (the side chosen automatically: left for
  inputs, right for the output, rotated with the gate).
  (Single-point marker behaviour is keyed by kind as well as by the Terminals category.)
- **Terminal markers snap to the union of the grid and the connection points.** A
  single-point connection dot (a junction/inversion marker such as `circ`/`ocirc`/
  `notcirc`) now snaps to whichever is nearest the cursor: a 0.25 GU grid node or a
  component pin / wire point (off-grid included). So it keeps grid snapping everywhere
  it isn't near a pin, yet lands exactly on an **off-grid** pin (a scaled gate's /
  manual-library symbol's terminal) when the cursor is closest to one. Previously a
  pure grid snap let the dense grid pull dominate the small pin magnet, so such a dot
  could never reach the off-grid pin.
- **Dragging a terminal marker no longer magnets onto its own pin.** While a marker is
  dragged the schematic still holds it at its start position, so the magnet was pulling
  it straight back there — small moves snapped back and the dot jittered near other
  pins. The dragged marker's own pins are now excluded from the magnet.
- **Terminal markers re-snap to anchors when dragged.** Dragging a junction/inversion
  dot now magnet-snaps onto the nearest connection point — **throughout** the drag, not
  just on release — so it lands on off-grid pins instead of the grid, matching
  placement. (The drag move handler had grid-snapped every dragged item, so a marker
  could never reach an off-grid terminal mid-drag.)
- **Terminal markers (junction dots) snap onto off-grid pins when placed.** Placing a
  Terminals-category dot now magnet-snaps onto a nearby component pin or wire (the same
  magnet wire drawing uses), so it lands exactly on a pin even when that pin sits off
  the grid — previously the dot snapped to the grid and could never coincide with an
  off-grid terminal (the common case in the manual library).
- **Wires stay on their own pins when a node is resized.** Resizing a densely-pinned
  node (e.g. a many-input gate, whose input pins sit close together) could re-route a
  connected wire onto the wrong pin — the per-pin wire-follow ran sequentially, so one
  pin's new position colliding with another's old position mis-assigned wires. The
  resize now applies one simultaneous old→new pin map, so every wire follows its own
  pin.
- **Terminal markers can be selected, moved and deleted.** A single-point terminal
  marker (junction dot, open dot, the diamond/square poles — the Terminals category)
  *is* its own pin, so clicking it always auto-started a wire and the marker could
  never be grabbed. A press on such a marker now selects/drags it (move or delete as
  usual); start a wire from it by entering wire mode first.
- **RF antenna wavefronts render as arcs on canvas.** dvisvgm draws the antenna
  wavefronts as full circles clipped to a wedge; the SVG-geometry extractor ignored
  the clip, so the canvas showed whole concentric circles. The extractor now captures
  the clip region and the canvas clips to it, matching the compiled figure
  (`bareRXantenna`/`rxantenna`/etc.).
- **Seven-segment display and other thick-stroke art render at true weight.** The
  canvas mapped every stroke to a binary thin/thick weight, so the seven-segment's
  thick segment spines (and cute-switch contacts, battery/source bars) rendered too
  thin and looked wrong. Strokes well above the body weight now keep their true
  proportional width, matching LaTeX.
- **Palette category cards always show an icon.** The card icon was looked up from a
  hard-coded category→component map, so any category that map didn't know about (every
  category the manual-generated library adds, e.g. RF, Tubes, Transformers) rendered a
  blank card. Categories not in the curated map now fall back automatically to their
  first (most canonical) member's symbol, so new categories never break the icons.
- **Palette category order (manual library).** The manual-library palette now lists
  its category cards in the **manual's own section order** (Grounds, Resistive
  bipoles, Capacitors and inductors, …) instead of the arbitrary `definitions.json`
  order, matching the online manual.

## [0.4.0] - 2026-06-21

### Added
- **Node text for node-style components**
  ([#32](https://github.com/whileman133/Heaviside/issues/32)). Node-style
  components (transistors, op-amps, MOSFETs, logic gates, grounds, power rails)
  now have a separate **Node text** field for the text in their emitted
  `node[…] {TEXT}` slot — e.g. a transistor's `$Q_1$` or a rail's `$V_{cc}$` —
  distinct from the **Node options** bracket. Edit it in the inspector **or in
  place on the canvas**: a node element now has two editable text boxes, opened by
  double-clicking the node text. A node's options are edited through the inspector
  only — they are not shown on the canvas (which would clutter the node text), so
  double-clicking a node always edits its node text. The text renders live on the
  canvas at exactly the spot the compiled figure places it — measured per
  component shape, so a transistor's label sits just right of the symbol, an
  op-amp's inside it, a power rail's above it — with a transparent background to
  match CircuiTikZ, and always appears in the displayed CircuiTikZ source so the
  source matches what is rendered.

### Changed
- **Power-rail labels are now node text.** A power rail's voltage name is set in
  the new Node text field (the `{…}` slot) instead of an `l=` option. Existing
  files are migrated automatically on load: a rail's `l=` label moves into its
  node text, and any other options are preserved.
- The `.hv` file format is now **version 0.6** (adds the per-component
  `node_text`). Files from 0.1–0.5 still load unchanged; a 0.5-or-older build
  will refuse a 0.6 file rather than silently dropping the node text on save.
- **Pasting now previews the clipboard under the cursor.** Ctrl+V / ⌘V (and the
  Edit-menu Paste) attach the copied components and wires to the pointer as
  ghosts; a **left-click** drops them where you aim, while **Escape** or a
  **right-click** cancels. This replaces the old blind fixed-offset paste, so a
  paste no longer silently splits a wire or connects pins to whatever happened to
  sit under the offset. Right-click **"Paste here"** still drops the group at the
  clicked point in one step.

### Fixed
- **Paste no longer crashes** from the Edit menu or the Ctrl+V/⌘V shortcut
  ([#33](https://github.com/whileman133/Heaviside/issues/33)). The Paste action
  was connected straight to `scene.paste`, so Qt's `QAction.triggered` `checked`
  boolean was bound to paste's `at` argument and the "paste here" branch tried to
  subscript a bool (`TypeError: 'bool' object is not subscriptable`).

## [0.3.0] - 2026-06-20

### Changed
- **Line-hops now use CircuiTikZ's native `jump crossing` nodes** instead of a
  hand-drawn Bézier bump, so the exported figure uses the package the way it is
  intended (and gains its styling hooks, e.g. `crossing vertical`). Both crossing
  wires connect to the node's anchors — the hopping wire carries the arc, the
  crossed wire the gap. The on-canvas hop is redrawn to match. No `.hv` change:
  hops are derived geometry, never stored.

### Fixed
- The **CircuiTikZ Source** panel now reflects **line-hops** (and stays in step
  with the unconnected-pin marker), matching the compiled preview and the
  exported `.tex`. Previously the source panel omitted the hop bumps even though
  the figure and exports contained them.

## [0.2.0] - 2026-06-20

### Added
- **SI units (siunitx) and a custom LaTeX preamble**, in the inspector's
  **Document** tab ([#29](https://github.com/whileman133/Heaviside/issues/29)).
  **SI units (siunitx)** is **on by default** (most schematics use units at some
  point and it is cheap to load) and loads siunitx through CircuiTikZ so labels
  can use unit macros, e.g. `l=\qty{10}{\ohm}` or `\unit{\ampere}`. The **custom
  preamble** field splices arbitrary LaTeX (packages, macros, colours,
  `\ctikzset`) into the document preamble — the escape hatch for anything the
  inspector has no dedicated control for. Both settings travel with the `.hv`
  file, apply to the live preview and every export, and appear in the exported
  `.tex` (listed as comments in an `\input` snippet, which cannot add packages
  itself). With siunitx on, unit-macro labels also typeset live **on the canvas**
  (the on-canvas label renderer mirrors the document's siunitx setting), so
  `\qty{10}{\ohm}` is no longer blank on the canvas while rendering in the export.

### Changed
- **`.hv` file format bumped to `0.5`** to carry the new document preamble
  settings. Files from 0.1–0.4 load unchanged — an absent `siunitx` key defaults
  on (the new-document default) and the custom preamble to empty; a 0.4 or older
  build will refuse a 0.5 file rather than silently strip the new data.

## [0.1.0] - 2026-06-13

First public release of Heaviside. (Earlier 0.1–0.3 tags were withdrawn and the
version reset to 0.1.0 — this is the first release of the current line.)

### Added
- **Single-key component placement.** Press a key to start placing a component —
  defaults: `r`/`c`/`l`/`d` = resistor/capacitor/inductor/diode, `g` = ground,
  `t` = transistor, `v`/`i` = voltage/current annotation. Pressing another key
  **while a ghost is up swaps it** to that component, so you can change your mind
  without going back to the palette. The keys work window-wide (regardless of
  which panel has focus, but never while typing in a text field) and are
  **configurable** in **Preferences ▸ Shortcuts** (a key→component table with
  Add / Remove / Restore defaults). `s`/`w`/`p` stay reserved for the
  Select/Wire/Pan tools.
- **Right-click context menu for components and wires** with **Cut**, **Copy**,
  **Paste**, **Delete**, **Bring to Front**, and **Send to Back**. Right-clicking
  an unselected item targets just it; right-clicking inside a multi-selection acts
  on the whole group; right-clicking empty space still offers Paste. Menu **Paste
  drops the clipboard at the cursor** ("paste here"), while `Ctrl+V` keeps its
  fixed 1 GU offset.
- **Cut** (`Ctrl+X`, and the Edit menu) — copies the selection, then deletes it as
  one undoable step.
- **Front/back layering now works on every component**, not just drawing
  annotations (rect/text/circle/bipole) and wires. Plain circuit symbols
  (resistors, transistors, sources, …) can be sent to front/back too, on the
  canvas and in the generated LaTeX.

### Changed
- **Requires Python ≥ 3.12** (was 3.11). PySide6 6.11.1's bindings have a refcount
  bug that over-decrements `None` during ordinary Qt widget operations (seen
  building the component palette). On Python 3.11 (mortal `None`) this eventually
  crashes the process — deterministically on aarch64 (Raspberry Pi), intermittently
  elsewhere; on 3.12+ `None` is immortal (PEP 683), so the bug is harmless. The
  bundled downloads ship their own 3.12 interpreter, so only running from source on
  3.11 is affected.
- **Wire and Place modes now show a crosshair cursor**, so it's obvious at a glance
  when you're routing a wire or placing a component (previously the cursor stayed a
  plain arrow in Wire mode, which was easy to miss). Pan still shows the hand cursor;
  Select stays the arrow.
- **Rotate moved to `Ctrl+R`** (`⌘R` on macOS), freeing the plain `R` key to place
  a resistor (and to swap a live placement ghost). Rotate still turns the current
  selection or the placement ghost 90° clockwise.
- **Component palette reorganized into a 3-column grid of category cards** (was
  two columns), regrouped so related categories sit together (Resistors |
  Inductors | Capacitors, Diodes | Transistors | Switches, …). The panel is a
  little wider to fit the three columns.
- **Voltage and current annotations are now placed by drawing their span** —
  click the start point, then click the end point — instead of dropping a
  fixed-size ghost. The ghost appears after the first click and stretches to the
  cursor; the second click places the annotation. Endpoints **magnet-snap to
  component pins** (the same magnet wire drawing uses), so an annotation can be
  drawn exactly across a component. (Other components are unchanged: single-click
  ghost placement.)
- **File format bumped to 0.4.** `z_order` is now stored on every component (it
  was previously only saved for drawing annotations). Older files (0.1–0.3) still
  open; a 0.3-era build will refuse a 0.4 file rather than silently dropping a
  component's layer on save.
- **Voltage/current annotations (and the generic bipole) are now draggable from
  either endpoint.** Previously only the terminal handle could be dragged; now a
  press on either endpoint handle drags that end while the other stays fixed —
  the origin handle moves the component and adjusts its span so the terminal
  doesn't move (`MoveEndpointCommand`). Boxes (rect/circle) are unchanged (they
  still resize from the far corner only).
- **Clicking and holding an annotation endpoint drags it instead of starting a
  wire.** A current annotation's endpoints sit on connectable pins, so a press
  there used to auto-enter wire mode; the endpoint-drag gesture now takes
  priority, making the endpoints easy to grab and move.

### Fixed
- **Crash during heavy label rendering (notably on ARM/Raspberry Pi).** Canvas
  labels were typeset on worker threads that built the vector `QPainterPath`
  off the UI thread; when the UI thread garbage-collected at the same moment, the
  cross-thread Qt heap activity could corrupt memory and crash the app — reliably
  on aarch64, intermittently elsewhere. Workers now produce only the (Qt-free) SVG
  and the UI thread builds the path, so no Qt objects are ever created off the UI
  thread (verified: 0 crashes in 30 stress runs on aarch64, vs ~1 in 15 before).
- **Voltage/current annotation endpoints no longer "stick" near a pin.** Dragging
  an annotation endpoint had a 0.5 GU dead-zone that froze it near the other end
  and made the origin handle resist small drags. The endpoint now follows the
  cursor smoothly on the 0.25 GU grid (matching placement and wire vertices).
- **Labels with an equals sign now compile** (e.g. `l=$v=2$`). The value is
  brace-wrapped in the generated LaTeX (`l={$v=2$}`) so CircuiTikZ's option parser
  doesn't split on the inner `=` — previously such a label produced a "forgotten
  `$`" compile error. No escaping needed; just type it.

### Removed
- **Per-component keyboard shortcuts in the palette.** The category mnemonic
  letters (R/C/L/…) and the 1–9/0 "place the Nth component" digits — and their
  on-card letter / on-tile number badges — are gone; place components by clicking
  a category card then a component tile. `Ctrl+/` (focus the search box) is
  unchanged.

### Changed
- **Component alignment is now a single uniform algorithm.** Every multi-terminal
  symbol (transistors, op amps, switches, logic gates, …) is centre-placed and
  aligned to the grid by one per-axis `scale`, derived by measuring its CircuiTikZ
  anchors — replacing the former mix of anchor-pinned placement, per-component
  scales, and bridge "leads". Consequences in generated figures: op amps render
  compact (no extended input/output stubs); transistor footprints shift (and
  their symbols re-centre); switches/logic-gate bodies are no longer sheared (a
  per-axis distortion is capped, falling back to a uniform scale). Alignment
  constants (grid, scale bounds, anisotropy cap, gate/mux body sizing) live in the
  new `components/generation.toml`. Internal pipeline change; no `.hv` format
  change, but saved figures re-export with the new symbol geometry.

- **Auto-export defaults are TeX + PDF + PNG.** Saving a schematic writes
  `<name>.tex`, `<name>.pdf`, and `<name>.png` siblings by default. The `.tex`
  fragment is the primary output for the LaTeX/Overleaf/LyX audience (`\input` it
  into a paper) and is pure codegen — it needs no LaTeX at all; PDF and PNG need
  only `pdflatex`. EPS and SVG stay opt-in (Preferences → Export): they're the
  only formats needing a PDF→vector converter (Poppler or Inkscape), so a
  converter-less system never fails an export on every save.

### Added
- **The project is now an installable package, so `uv run heaviside` works.**
  Added a Hatchling `[build-system]`; uv installs the project editable and
  generates the `heaviside` console script (previously absent — `uv run heaviside`
  failed with "Failed to spawn"). Running from source via `uv run python main.py`
  still works too. No effect on the packaged builds.
- **`--no-latex` launch flag (developer/testing aid).** Launching with
  `--no-latex` (e.g. `uv run heaviside --no-latex`) makes the app behave as if no
  TeX toolchain is installed — the preview shows the "LaTeX not found" notice and
  canvas labels render via the bundled ziamath engine — so that experience can be
  checked on a machine that does have LaTeX.
- **Linux ARM (arm64/aarch64) release builds.** Releases now include
  `Heaviside-linux-aarch64.AppImage` and `Heaviside-linux-arm64.tar.gz` built
  on native ARM runners, so Raspberry Pi OS (64-bit) and other aarch64 Linux
  systems get a download-and-run binary instead of an x86_64-only one. Like
  the x64 binaries, they need glibc ≥ 2.38 (Debian 13 "Trixie" /
  Ubuntu 24.04 or newer); older distros build from source.
- **Inkscape works as the EPS/SVG export converter.** When Poppler's
  `pdftocairo` isn't installed, EPS and SVG export now fall back automatically
  to Inkscape (1.0+) — including Inkscape installs that aren't on `PATH` (the
  macOS app bundle, Windows Program Files). Poppler remains preferred when
  both are present; an explicit Inkscape path can be set in
  Preferences → Tools.

### Changed
- **Missing-LaTeX feedback moved into the preview pane.** When `pdflatex` isn't
  on the `PATH`, the preview no longer shows a red error (and no longer pops a
  "Missing Dependencies" dialog at startup). Instead the preview pane shows a
  light, centered **"LaTeX not found"** notice with an info icon, a short
  explanation, and an OS-specific install recommendation (MacTeX / MiKTeX /
  TeX Live, with the `apt` command on Debian/Raspberry Pi OS), plus a pointer to
  Preferences ▸ Tools. It clears automatically once LaTeX is installed or a path
  is configured.
- **No more startup pop-ups.** Removed the "Missing Dependencies" warning dialog
  (replaced by the in-preview notice above) and the one-time "Heaviside checks
  GitHub for updates" disclosure dialog. The automatic update check itself is
  unchanged (still on by default, opt-out in Preferences ▸ Updates).

### Fixed
- **Disabled toolbar icons no longer render near-black in dark mode.** When the
  app launched with a saved dark theme, disabled toolbar buttons (undo/redo)
  showed dark ink instead of the muted light ink. qtawesome derives its disabled
  icon color from the application palette at icon-creation time, and the toolbar
  is built before the dark palette is applied — so the icons captured the light
  palette. The disabled state is now pinned to the theme's muted ink explicitly.
- **Canvas labels now render with no LaTeX installed (packaged builds).** On a
  machine without `pdflatex`, the app falls back to the pure-Python `ziamath`
  engine — but the PyInstaller bundle was missing `latex2mathml`'s
  `unimathsymbols.txt` (loaded at import), so `import ziamath` failed silently and
  every math label rendered blank on the packaged Linux/Windows builds. The bundle
  now ships `latex2mathml`'s data alongside the `ziamath`/`ziafont` fonts, and a
  failed fallback import now logs a one-time warning instead of failing invisibly.
- **Preferences is reachable on Linux and Windows.** The Preferences menu item
  carried `QAction.PreferencesRole`, which on desktops with a global menu bar
  could be pulled out of the in-window menu into an application menu that isn't
  shown — making it unreachable off macOS. It now uses `NoRole` off macOS and
  appears in **both the Edit and File menus** there (macOS keeps the native
  application-menu placement).
- **Form controls (text fields, spin boxes) no longer render squished.** On the
  macOS native style these render at a compact ~21px while combo boxes are ~32px,
  so they looked cramped throughout the app. A `QProxyStyle` now floors their
  height (keeping fully native rendering — only the size hint changes), applied
  process-wide so every panel and dialog is consistent. Separately, the Properties
  inspector no longer puts a stylesheet on its scroll area (which had forced its
  controls into Qt's non-native rendering); the scrollbar is themed on the
  scrollbar widget and the body transparency comes from `autoFillBackground`.
- **Batch component regeneration is faithful again (developer tooling).**
  `components/generate_components.py` silently degraded 26 library entries: the
  parametric mux/demux lost their `params`/`n_data` and all 120 size-combo
  geometries, and centre-placed kinds with a uniform grid-alignment scale
  (flip-flops, transformers, ALU/adder, cute switches) had that scale stripped.
  The generator now routes mux/demux through their combo renderer (the
  authoring rec is persisted in `definitions.json`) and re-derives — rather
  than discards — uniform scales and pin offsets. `definitions.json` also now
  records the CircuiTikZ version it was generated against
  (`circuitikz_version`).
- **Windows: no more console window flashing over the app on every render.**
  Each `pdflatex`/`latex`/`dvisvgm`/converter run briefly opened a console
  window on Windows; all tool subprocesses are now launched with
  `CREATE_NO_WINDOW`.

### Removed
- **The Component Editor (Tools menu) is gone.** The GUI existed for manual
  scale/offset fix-ups when a symbol's pins couldn't be grid-aligned
  automatically; the pipeline now measures anchors (`\pgfpointanchor`),
  re-derives every alignment on regeneration, and tolerates off-grid pins by
  design, so the editor had no remaining job. Its Qt-free engine lives on as
  `app/components/generate.py`; authoring is editing
  `components/definitions.json` and re-running
  `components/generate_components.py` (which now also validates every entry
  before rendering).
- **The welcome screen no longer shows the *Help ▸ Keyboard Shortcuts &
  Gestures (F1)* hint line.** The screen now displays only the H(t) step
  diagram; the full reference is still available from the Help menu, the
  toolbar `?` button, and `F1`.

[0.5.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.5.0
[0.4.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.4.0
[0.3.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.3.0
[0.2.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.2.0
[0.1.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.1.0
