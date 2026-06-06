# Changelog

All notable changes to Heaviside are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Component Editor foundation** (developer-facing; not yet wired into the
  running app — no behaviour change). Lets grid-aligned CircuiTikZ components be
  generated without hand-stored magic numbers:
  - A **measurement tool** (`app/components/bake.py`) that renders a symbol and
    reads its pin anchors automatically (the manual `PROJECT_SPEC.md` §5.5
    measurement, mechanised).
  - One flat **data file** (`components/components.json`, built by
    `tools/generate_components.py`) holding every CircuiTikZ symbol's pins, bbox,
    alignment, and metadata — the per-component numbers that were previously
    hand-maintained across `registry.py` and the `circuitikz` codegen tables.
  - A **loader** (`app/components/library.py`) that rebuilds the current
    `REGISTRY` and codegen tables from that file, with tests proving they match
    exactly.

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
