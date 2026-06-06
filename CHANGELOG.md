# Changelog

All notable changes to Heaviside are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- The application version now has a single source of truth (`pyproject.toml`),
  surfaced at runtime via `app/version.py`. The About dialog and the macOS bundle
  metadata read from it instead of hardcoding a version string.
- The "unknown `.hv` version" load error now explains that the file was likely
  saved by a newer release and prompts the user to update Heaviside. (The
  file-format version remains independent of the app version — it changes only
  when the on-disk format changes.)

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
