# Contributing to Heaviside

Thanks for your interest in improving Heaviside. This guide covers how to set up
a development environment, the conventions the codebase follows, and the one
hard rule for every change.

## Development setup

Heaviside uses [`uv`](https://docs.astral.sh/uv/) for environment and dependency
management and targets **Python ≥ 3.12**.

```sh
uv sync                       # create the environment and install dependencies
uv run heaviside              # run the app
uv run pytest                 # run the full test suite with coverage
QT_QPA_PLATFORM=offscreen uv run pytest   # headless (CI / no display)
```

The preview pipeline shells out to `pdflatex` (TeX Live or MiKTeX, with the
`circuitikz` package). It is not needed to run the test suite — tests that
require it are skipped automatically when it is absent.

## Architecture in one paragraph

The codebase is layered so that all the real logic is testable without a display.
The **data model** (`app/schematic/`, `app/components/`) is plain Python
dataclasses plus pure functions — no Qt. The **code generator** (`app/codegen/`)
is a pure, deterministic `Schematic → CircuiTikZ` translator. The **command
layer** (`app/canvas/commands.py`) implements undo/redo as Qt-free command
objects over the model. Only `app/canvas/scene.py`, `view.py`, `items.py` and
`app/ui/` depend on Qt, and they are a thin shell that turns user input into
commands and paints the model. Keep new logic in the pure layers wherever
possible — that is what keeps the project easy to test and reason about.

The [architecture diagram](docs/images/architecture.svg) and the **Architecture**
section of the [README](README.md) show the module layout; `PROJECT_SPEC.md` is
the authoritative, living specification of behavior.

## The one hard rule: keep tests and spec in sync

A change is **not complete** until both of these are true:

1. **Tests** reflect the change. Add regression tests for bug fixes, update
   tests whose expected behavior changed, and remove tests for deleted behavior.
   New logic in the pure layers should be unit-tested; the test suite runs
   headless and is expected to stay green.

2. **`PROJECT_SPEC.md`** reflects the change. The spec is a living document (see
   its §0) and must stay in sync with the implementation at all times — prose,
   invariants, commands, keyboard shortcuts, acceptance criteria, and the §13
   test table.

3. **`CHANGELOG.md`** reflects user-facing changes. Add additions, changes,
   fixes, and removals under the `## [Unreleased]` heading. Internal-only
   refactors that change no behavior may be skipped.

Please run `QT_QPA_PLATFORM=offscreen uv run pytest` before opening a pull
request and confirm the suite passes.

## Cutting a release

Maintainers: the release procedure (version bumping across the four files that
carry the version, tagging, and publishing the binary build) is documented in
[`docs/releasing.md`](docs/releasing.md).

## Security note for the preview pipeline

A `.hv` file may be authored by someone other than the person opening it, and
label/text fields are emitted verbatim into the generated LaTeX (this is
intentional — users type real LaTeX math). The compile pipeline therefore passes
`pdflatex` its arguments as a list (never `shell=True`) and includes
`-no-shell-escape`, so a crafted label can never invoke shell commands. Any
change to `app/preview/latex.py` must preserve both properties; they are guarded
by `tests/test_latex_security.py`.

## How this codebase was built

Heaviside was developed spec-first with substantial help from AI coding
assistants (Anthropic's Claude). The full methodology — the model used for each
layer, where extended thinking was applied, and the spec-driven workflow — is
documented in [`docs/ai-development.md`](docs/ai-development.md). The spec is the source of truth: it was
written first and the implementation follows it, which is why the test suite and
specification are kept rigorously in sync. Contributions are reviewed on their
merits — correctness, tests, and spec alignment — regardless of how they were
authored.
