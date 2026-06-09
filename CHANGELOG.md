# Changelog

All notable changes to Heaviside are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Dark mode (follows the system appearance).** The whole app — canvas (paper,
  grid, symbol/wire ink), toolbars, component palette, properties, source panel,
  the welcome screen, and the LaTeX preview — now switches between light and dark
  to match the OS appearance, live via `colorSchemeChanged`. Native dialogs and
  spin/combo boxes follow the system as before. In dark mode the preview is
  recompiled with a dark page and light ink so it no longer glares against the UI;
  **exports stay light** (the copied/saved figure is the publication artifact —
  white paper, black ink — so dark mode never affects what you put in a document).
- **Switches and choke.** Added a Switches category with normally-open (`nos`),
  normally-closed (`ncs`), push-button (`push button`), **opening** and
  **closing** switches, a **3-terminal SPDT** switch, plus a Choke (`cute choke`)
  inductor.
- **Battery, cell, and amplifier components.** Added a multi-cell **Battery**
  (`battery`) in Sources, an **Instrumentation Amplifier** (`inst amp`) and a
  **Transconductance Amplifier** (`gm amp`) in Amplifiers. The existing
  single-cell symbol (`battery1`) is relabeled **"Cell"** to distinguish it from
  the new Battery.
- **Multi-component property editing.** Shift-click (or rubber-band) to select
  several components, and when they're all the **same kind** the property
  inspector edits **all of them at once** — changing options, rotation, mirror,
  variants, inputs, fonts, styles, or layer applies to every selected component
  as a single undo step. (A mixed-kind selection shows just a count.)

### Fixed
- **Copy PDF / Copy SVG now paste into Word, PowerPoint, and Google Docs.**
  Copy PDF placed the figure only under the `application/pdf` MIME type, which
  macOS wraps in a flavor Office doesn't recognize — so nothing pasted; Copy SVG
  also put the markup on the clipboard as plain text, so Office pasted the raw
  `<svg>` XML. Both now expose the figure under its real macOS pasteboard UTI
  (`com.adobe.pdf` / `public.svg-image`) for vector-aware apps **and** attach a
  300-dpi raster fallback so apps without vector paste still get the image; the
  SVG `text/plain` flavor is gone. (Exports are unchanged.)
- **Mirroring now matches in the LaTeX output.** A mirrored two-terminal
  component whose symbol has off-axis features (e.g. an LED's emission arrows)
  was reversed only *along* its axis in the generated CircuiTikZ, so those
  features ended up on the wrong side versus the canvas. Codegen now adds the
  `mirror` key so the perpendicular reflection matches the canvas Flip-X.
- **Mirroring a rotated two-terminal component no longer detaches it.**
  Mirroring a vertical (90°/270°) bipole — e.g. the boost converter's load
  resistor — moved its far terminal to the opposite side of its origin,
  detaching it from connected wires and rendering it in the wrong place in the
  LaTeX preview. The mirror is the canvas global Flip-X, which is applied *after*
  rotation, not before; `component_pin_positions`, `local_span_to_world`, and the
  code generator now all mirror after rotating, so a mirrored vertical bipole
  keeps its terminals on their grid cells and the preview matches the canvas at
  every rotation. (Off-axis symbol features still flip via the `mirror` key.)
- **Native dialogs, message boxes, and spin boxes.** Dropped the global
  form-control stylesheet that was cascading into the modal dialogs and message
  boxes (making them non-native); they — and spin/combo boxes — now use the
  platform style. The toolbars, palette, and Copy buttons stay themed.
- **Spin-box arrows restored** — the themed form styling had hidden the up/down
  arrows on number fields (Inputs, z-order, font size); spin boxes are native
  again. Reverted the modal dialogs (Preferences, Document Settings) to the
  native style. The component palette is a touch wider so cards aren't cut off,
  and the Copy PDF/SVG buttons show a pointer cursor and hover highlight.

### Changed
- **Signed & notarized macOS releases.** The release workflow now signs the
  macOS `.app` with a Developer ID Application certificate (hardened runtime +
  entitlements), submits it to Apple's notary service, and staples the ticket, so
  downloaded builds open without a Gatekeeper warning. Signing runs only when the
  signing secrets are configured; runs without them still produce an unsigned
  build. See `packaging/entitlements.plist` and `.github/workflows/release.yml`.
- **Palette categories tidied.** The **Supplies** category was merged into
  **Sources** (power rails now live there), and within every category the
  american-style components are grouped before the european-style ones instead of
  interleaving.
- **Harmonized, modern UI theme.** The toolbars and main-window buttons/inputs
  now share the component palette's flat, light look (white surfaces, hairline
  dividers, muted icons, one soft-blue accent) instead of the previous native
  gray/3-D chrome. The top toolbar and left tool ribbon are white with a hairline
  divider and a soft-blue active-tool state; the Copy PDF/SVG buttons and the
  palette/properties line edits are flat and rounded. (Modal dialogs and
  combo/spin boxes stay native.) Centralized in a new `app/ui/theme.py`
  design-token module.
- **Full-height palette.** The component palette now spans the whole window
  height on the left; the CircuiTikZ source and LaTeX preview panels moved into
  the region to its right (no longer running underneath the palette).
- **Redesigned component palette.** The left panel is now an icon-tile picker:
  a search box (focus with `Ctrl+/`), an **In use in document** section that
  tracks the kinds you've placed, a 2-column **Categories** card grid, and the
  active category's components below. Component tiles are icon-only with the name
  on hover (tooltip) to stay compact; searching shows a flat results grid across
  all categories.

### Added
- **Copy figure to clipboard.** File ▸ *Copy Figure as PNG* (Ctrl+Shift+C),
  *PDF*, and *SVG* put the compiled schematic on the clipboard — a raster QImage
  (PNG, via QtPdf), the compiled PDF (`application/pdf`), or vector
  `image/svg+xml` (+ text fallback) — for pasting straight into slides, docs, or
  chat. **Copy PNG / PDF / SVG buttons** also sit below the LaTeX preview. Same
  toolchain needs as the corresponding exports (`pdflatex`; `pdftocairo` for SVG).
- **European / cute component variants.** Added a European-style resistor
  (Resistors), European and "cute" inductors (Inductors), and the full set of
  **European/IEC logic gates** — AND/OR/NAND/NOR/XOR/XNOR (parametric, 2–16
  inputs) plus NOT/buffer (Logic). All use CircuiTikZ's style-independent shape
  keywords (`european resistor`, `european inductor`, `cute inductor`,
  `european … port`) so they render the same shape on the canvas and in output
  regardless of the global style — and sit alongside the american symbols.
- **European sources & variable resistors.** Added European voltage/current
  sources `eV`/`eI` and their controlled forms `ecV`/`ecI` (Sources), plus a
  European variable resistor `evR` and potentiometer `epot` (Resistors), using
  the `european …` shape keywords.
- **European resistive sensor & american potentiometer.** Added a European
  resistive sensor `ethermistor` (the european thermistor equivalent,
  `european resistive sensor`) to pair with the american `thermistor`, and an
  american potentiometer `pR` to pair with the European `epot` (both Resistors).
- **Document Settings (Edit ▸ Document Settings…).** Choose the CircuiTikZ
  **american/european** style for voltage (`v=`) and current (`i=`) labels
  per-document. Stored in the `.hv` file (new `config` object; format bumped to
  `0.2`, older files still open with american defaults) and emitted as a
  picture-scoped `\ctikzset`, so it applies to the preview and exported figure.
- **Configurable tool paths (Preferences → Tools).** Set explicit paths to
  `pdflatex`, `latex`, `dvisvgm`, and `pdftocairo` when they aren't on your
  `PATH` or you want a specific install (à la LaTeXiT). Each field has a
  **Browse…** picker and live status (found-on-PATH / will-use-this-path /
  not-found); blank means auto-detect. Resolution now goes through a single
  `app/preview/tools` resolver (configured path → PATH).
- **LaTeX-free on-canvas equation labels.** Typeset component/wire/annotation
  labels now render via a bundled, pure-Python engine (**ziamath**, ~3 MB, ships
  the STIX Two Math font) when a system LaTeX install isn't available — so
  drawing, typeset canvas labels, CircuiTikZ source, and `.tex` export all work
  with no LaTeX. When `latex`/`dvisvgm` are present, the higher-fidelity LaTeX
  engine is still used. (The PDF preview pane and PDF/EPS/SVG image exports still
  require `pdflatex`.)
- **Preference: "Force the built-in (ziamath) label renderer."** A debug aid to
  use ziamath even when LaTeX is installed; toggling it re-typesets existing
  labels immediately.
- **Auto-export TeX snippet on save.** A new **Auto-export TeX on save**
  preference writes the includable `<name>.tex` snippet next to the `.hv` file on
  every save, alongside the existing PDF/EPS/SVG auto-export options. Unlike the
  image formats it is generated directly (no `pdflatex`), so it works with no
  LaTeX install. Multiple image formats still share a single compile.
- **SVG export.** A new **File → Export to SVG…** writes a vector SVG of the
  schematic, and an **Auto-export SVG on save** preference writes `<name>.svg`
  next to the `.hv` file. SVG uses the same Poppler `pdftocairo` tool as EPS
  (via the `-svg` flag), so it adds **no new dependency** beyond what EPS export
  already needs.

### Changed
- **Component emission types collapsed to two.** A component's `emission` is now
  either `path` (CircuiTikZ `to[…]` syntax) or `node` (`node[…]` syntax),
  replacing the former three-way `two_terminal`/`node`/`multi_terminal`. A `node`
  element is a single-terminal node (grounds, supplies) or a multi-terminal node
  (op amps, transistors, gates) depending purely on whether its pins carry
  CircuiTikZ anchors — the distinction is derived from the data, not a separate
  type. This affects only `components/definitions.json` and the Component Editor;
  saved `.hv` files are unaffected (they never stored emission). The generated
  LaTeX output is byte-for-byte unchanged.

## [0.2.0] - 2026-06-07

### Changed
- **The Component Editor is shown only when its toolchain is present.** It is a
  developer tool that renders/measures CircuiTikZ symbols via `latex` + `dvisvgm`,
  which a packaged end-user build does not ship — so the **Tools** menu now appears
  only when that toolchain is on `PATH`, instead of offering a half-working tool.
- **Engineer-facing palette categories.** Replaced the broad CircuiTikZ
  "Bipoles/Tripoles/Nodes" groupings with categories an EE recognises: Resistors,
  Capacitors, Inductors, Diodes, Transistors, Amplifiers, Sources, Instruments,
  Grounds, Supplies, Misc (plus Annotations, Drawing). The category is independent
  of terminal count, so the 4-terminal MOSFETs sit naturally under Transistors.
  The palette's category order is now a preference — an unlisted category still
  shows (after the listed ones) rather than silently hiding its components.
- **Clearer MOSFET names** so the flavours are distinguishable: `nigfete`/`nigfetd`
  → "N-MOSFET (enh.)" / "N-MOSFET (depl.)", `pigfete`/`pigfetd` → the P versions,
  `nfet`/`pfet` → "N-MOSFET (4-terminal)" / "P-MOSFET (4-terminal)" (the body-diode
  ones), and `njfet`/`pjfet` stay "N-JFET"/"P-JFET". Also `eC` → "Electrolytic
  Capacitor" to distinguish it from `pC` "Polarized Capacitor".
- **Component alignment is re-derived on every generation, not frozen.** The batch
  generator (`components/generate_components.py`) now recomputes each
  multi-terminal symbol's `scale`/`leads` from a fresh anchor measurement
  (`renderer.fit_alignment`, shared with the editor's *Fit pins to grid*), instead
  of preserving the stored values. So regenerating after a CircuiTikZ-library
  update reflows the grid alignment automatically — the alignment is now a computed
  property of the current library, not a hand-frozen constant. The scale-vs-leads
  strategy follows the authored `anchor_pin` (centre-placed → leads, so the op-amp
  triangle isn't distorted; anchor-pinned → scale + residual lead).
- **Adding a CircuiTikZ component is now data-only.** A plain symbol needs just a
  `components/definitions.json` entry + re-running the generator — no canvas or
  registry code edits. The ~30 trivial per-kind `ComponentItem` subclasses
  (`ResistorItem`, `DiodeItem`, every ground/rail, …) were removed; the canvas
  resolves a kind via `ITEM_CLASSES.get(kind, ComponentItem)`, so plain symbols
  use the generic base class. Only kinds with special behaviour (MOSFETs,
  resizable annotations, drawing primitives) keep a subclass. `_DISPLAY_ORDER` is
  now a *preference*, not an exhaustive list — an unlisted kind still appears in
  the palette (at the end of its category), so the brittle "must list every kind"
  assert is gone.

### Fixed
- **Logic-gate labels now render in exported output.** A gate's label slot was
  emitted as the bipole `l=` quick key, which CircuiTikZ's logic-port shapes
  reject (pdflatex warned and dropped the label). It is now emitted as
  `label=above:{…}`, placed above the body to match the canvas.
- **Old schematics survive a CircuiTikZ-library re-generation.** A `.hv` file
  stores only a component's `kind` (never its geometry), so regenerating against a
  new CircuiTikZ release flows appearance/alignment changes into existing files
  automatically. A new `_KIND_ALIASES` map (`schematic/io.py`) migrates any
  *renamed* kind on load, so a future symbol rename won't break old files.

### Added
- **CPE (Constant Phase Element)** — the `cpe` two-terminal bipole, for impedance
  / EIS equivalent-circuit work, under the Capacitors category.
- **Logic gates with a live input count.** The American gate family — AND, OR,
  NAND, NOR, XOR, XNOR (each **2–16 inputs**), plus NOT and Buffer — under a new
  **Logic** palette category. The multi-input gates are *parametric*: the input
  count is a per-instance integer you set in the inspector (a spinbox, undoable),
  and the symbol resizes live while every input pin stays grid-aligned. Each
  input count sets the CircuiTikZ gate **`height`** (so inputs reach the constant
  0.5 GU pitch as the body grows *natively*) rather than a node `yscale` — which
  keeps the inverting gates' round inversion bubble round and the proportions
  sensible. Built on a new generic parametric-component mechanism: a kind declares
  a `param` block in `definitions.json`; the generator renders one geometry per
  value (keyed `kind:N`) with per-N height/scale/bbox; the runtime resolves an
  instance's pins, geometry, bbox, and codegen from its value, and each
  height-setting gate is emitted in its own local `{ \ctikzset{…/height=H} \draw
  … }` group so the height reverts. The count is stored in the `.hv` file
  (`Component.params`).
- **19 new CircuiTikZ components**, bulk-imported via `components/import_family.py`
  with no per-component code (the registry/codegen/canvas derive everything from
  the data): bipoles `vR`, `eC`, `pC`, `fuse`, `lamp`, `ammeter`, `voltmeter`,
  `ohmmeter`, `battery1`, `varcap`, `memristor`, `thermistor`, `photodiode`,
  `tline`, `jumper`; and transistors `nfet`, `pfet`, `njfet`, `pjfet` (terminals
  auto-discovered, grid-snapped, and alignment auto-derived). `nfet`/`pfet` carry
  a fourth **bulk** terminal and a `body_diode` variant (toggles the intrinsic
  body diode), like the IGFET family.
- Linux (x64) build: the release workflow now produces a
  `Heaviside-linux-x64.tar.gz` alongside the macOS and Windows binaries.
- Design spec for a **Component Editor** (`spec/component-editor.md`) and a new
  `spec/` directory for focused per-feature specifications (indexed by
  `spec/README.md`, linked from `PROJECT_SPEC.md`). The Component Editor is a
  planned developer-first tool that imports a CircuiTikZ symbol from its
  generating command, automates grid alignment, and emits one declarative
  Component Definition as the single source of truth — replacing the
  five-hand-maintained-files procedure documented in `PROJECT_SPEC.md` §5.5.
- **Component Editor** — generate grid-aligned CircuiTikZ components without
  hand-stored magic numbers:
  - A **measurement tool** (`app/components/render.py`) renders a symbol via
    `latex`/`dvisvgm` and reads its pin anchors automatically (the manual
    `PROJECT_SPEC.md` §5.5 measurement, mechanised).
  - A **renderer** (`app/componenteditor/renderer.py`, driven by the
    `components/generate_components.py` CLI) renders every symbol in a fixed bounding
    box and writes both `components/geometry.json` (geometry) and
    `components/definitions.json` (pins, bbox, alignment, metadata, plus one
    `origin_svg` placement constant). Replaces the old
    `tools/export_circuitikz_svgs.py`, which is removed.
  - **The registry, code generator, and canvas all build from this data**
    (`app/components/library.py`): `registry.py` derives every CircuiTikZ-symbol
    `ComponentDef` (keeping the 6 bespoke kinds as literals); `circuitikz.py`
    derives its classification + alignment tables; and `svgsym.py`'s canvas
    transform is just `translate(-origin_svg)` + a uniform pixel scale. The removed
    magic numbers: the registry literals, the five hand-maintained codegen tables,
    and `svgsym`'s `_MULTI_ANCHORS` / bipole anchors.
  - **Alignment is computed, not hand-typed.** Multi-terminal symbols whose
    CircuiTikZ terminals fall between grid points are scaled onto the grid by a
    measured per-axis `node[xscale=…, yscale=…]` (BJTs exactly; MOSFETs plus one
    small residual lead); the op amp extends clean leads to its outward pins. Both
    scale and leads are derived from the measurements and stored in the data file.
  - **Per-instance variants are generic.** A placed component's boolean variants
    (diode `filled`, MOSFET `body_diode`) now live in a generic
    `Component.variants` map instead of the `DiodeComponent`/`MosfetComponent`
    subclasses, driven by the variants its kind declares in `definitions.json`.
    The Properties panel auto-generates a checkbox per variant, undoable via a
    single `SetVariantCommand`. The `.hv` file stores a `variants` map (only
    active ones); pre-variants files with legacy `filled`/`body_diode` keys still
    load (back-compatible, no format-version bump).
  - **Bulk-import tooling for whole CircuiTikZ families.** `render.discover_terminals`
    finds a shape's wireable terminals by probing a candidate anchor list and
    filtering by position (CircuiTikZ exposes no terminal list — unknown anchors
    resolve to the centre, and aliases collapse), and `components/import_family.py`
    is a dry-run prototype that auto-generates and render-verifies candidate
    `definitions.json` entries for a family. Two-terminal bipoles import with zero
    curation (keyword + name); multi-terminal families need only a naming
    convention + grid review.
  - **A standalone Component Editor GUI** (`python -m app.componenteditor`, also
    **Tools → Component Editor…**). A form-driven, developer-facing tool to author
    or re-align a CircuiTikZ symbol: pick an existing component (which renders and
    previews it immediately), enter the keyword/emission/pins, **Measure anchors**
    automatically, **Fit pins to grid** to compute the scale/leads (or set the
    `xscale`/`yscale` by hand), **Render & preview** on a 0.25 GU grid, and
    **Save** into `definitions.json` + `geometry.json`. Shares the one render/save
    core with the CLI.
  - **Pin extensions shown in red.** In the editor preview, the grid-alignment
    leads (the short extensions that bridge a symbol's CircuiTikZ anchor to its
    grid pin) are drawn in red, distinct from the black symbol body — so it's
    clear which parts of a component are the symbol and which are added to make it
    grid-aligned. (Isolated by diffing the render against a leads-free render.)
  - **The bounding box is computed, not hand-typed.** Each component's `bbox` is
    derived from the rendered ink extent (paths + glyphs) unioned with the pin
    positions, rounded outward to 0.05 GU (`renderer.compute_bbox`), so it tracks
    the drawn symbol — driving label clearance and the hit/selection region. The
    editor shows it read-only (computed on Render and drawn dashed for reference);
    it is no longer an editable field.

### Fixed
- Canvas label overlap: when a component carries both a label and a current
  annotation that default to the same side (e.g. an inductor with `l=$L$,
  i=$i_L$`), the two no longer render on top of each other — same-side slots now
  stack outward by at least one row. (The LaTeX output was already correct; this
  was a canvas-only placement bug.)
- The bundled example schematics now load in the current build. They previously
  declared an older `.hv` format version (`0.2`) that the consolidated loader no
  longer accepted, so opening them raised a version error. A new test
  (`tests/test_examples.py`) loads every bundled example so this can't regress on
  a future format change.

### Changed
- Generated CircuiTikZ source is now normalised toward the origin: coordinates
  start near `(0,0)` instead of wherever the schematic sat on the canvas
  (previously ~75 GU off). The rendered figure is unchanged — CircuiTikZ crops to
  its bounding box — but the source is far more readable and easier to hand-edit.
  The shift is a whole number of GU, so grid alignment is preserved, and the saved
  `.hv` file and on-canvas coordinates are untouched.
- The application version now has a single source of truth (`pyproject.toml`),
  surfaced at runtime via `app/version.py`. The About dialog and the macOS bundle
  metadata read from it instead of hardcoding a version string.
- The "unknown `.hv` version" load error now explains that the file was likely
  saved by a newer release and prompts the user to update Heaviside. (The
  file-format version remains independent of the app version — it changes only
  when the on-disk format changes.)
- Consolidated the build tooling into cross-platform Python scripts:
  `scripts/build.py` (replaces `build_app.sh`) and `scripts/make_icons.py`
  (replaces `make_icns.sh` + `make_ico.py`), generating both the Windows `.ico`
  and macOS `.icns` from `assets/icon.png` with Pillow only — no macOS-specific
  tooling, so icons can be regenerated on any platform.
- The Windows `.exe` now carries the Heaviside icon (the build previously passed
  the macOS `.icns`, which Windows ignored).

## [0.1.0] - 2026-06-06

First public, open-source **alpha** release. The editor, code generator, and
preview pipeline are functional and the test suite (660+ tests) passes headless
and runs in CI — but the architecture, UI, and `.hv` file format are not yet
stable and may change before `1.0`.

### Added
- MIT `LICENSE`.
- `CONTRIBUTING.md` with development setup, the test/spec sync rule, and the
  preview-pipeline security note.
- GitHub Actions CI (`.github/workflows/ci.yml`) running the headless test
  suite on Python 3.11 and 3.12.
- Dependabot configuration for GitHub Actions and Python dependencies.
- `docs/ai-development.md` — the AI-assisted implementation guide, extracted
  from the specification.
- This changelog.

### Changed
- Hardened the LaTeX preview pipeline: `pdflatex` is now invoked with
  `-no-shell-escape` (arguments are passed as a list, never via a shell) so a
  label in an untrusted `.hv` file can never execute shell commands. Guarded by
  `tests/test_latex_security.py`.
- Slimmed `PROJECT_SPEC.md` to focus on behavior: the AI-Assisted Implementation
  Guide (former §14) moved to `docs/ai-development.md`.
- The `.hv` file format uses a single pre-1.0 version, **`0.1`**, which is **not
  yet stable** and may change between alpha releases without migration support.
  The loader performs no backward-compatibility migration and rejects
  unrecognised versions.

### Removed
- Legacy `.hv` load-time migrations: the `labels`-dict → options-string
  conversion, the `rect` style-in-`options` → `StyledComponent` fields
  conversion, and the old `text_node` `span_override` → `font_size` conversion.

[0.2.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.2.0
[0.1.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.1.0
