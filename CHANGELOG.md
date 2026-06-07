# Changelog

All notable changes to Heaviside are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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
- **Old schematics survive a CircuiTikZ-library re-generation.** A `.hv` file
  stores only a component's `kind` (never its geometry), so regenerating against a
  new CircuiTikZ release flows appearance/alignment changes into existing files
  automatically. A new `_KIND_ALIASES` map (`schematic/io.py`) migrates any
  *renamed* kind on load, so a future symbol rename won't break old files.

### Added
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
    (`app/components/library.py`): `registry.py` derives the 33 CircuiTikZ-symbol
    `ComponentDef`s (keeping the 6 bespoke kinds as literals); `circuitikz.py`
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

[0.1.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.1.0
