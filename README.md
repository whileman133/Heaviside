# Heaviside

A graphical editor for producing publication-quality circuit diagrams that
output valid [CircuiTikZ](https://github.com/circuitikz/circuitikz) LaTeX markup.
It targets researchers and engineers who author documents in LaTeX or LyX and
need schematics with typeset mathematical annotations.

- Grid-disciplined, fixed-component-size canvas for schematic entry
- Clean, human-readable CircuiTikZ source as the primary output
- Lossless save/load via a JSON `.hv` format
- Live, rendered PDF preview of the current schematic
- Wire-to-wire connectivity with automatic junction dots

## Requirements

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/) for environment and dependency management
- `pdflatex` on your `PATH`, with the `circuitikz` package installed (TeX Live or
  MiKTeX) — used for the rendered preview
- *(optional)* [Poppler](https://poppler.freedesktop.org/) (`pdftocairo`) — only
  needed for **EPS export**. The preview is rendered by Qt's own PDF engine, so
  Poppler is not required for normal use.

Python dependencies (PySide6, pydantic, qtawesome) are declared in
[`pyproject.toml`](pyproject.toml) and installed by `uv`. The PDF preview uses
the `QtPdf` module that ships with PySide6 — no extra Python packages.

## Running

```sh
uv run heaviside        # or: uv run python main.py
```

## Tests

```sh
uv run pytest                 # full suite with coverage
uv run pytest --no-cov        # faster, no coverage
QT_QPA_PLATFORM=offscreen uv run pytest   # headless (CI / no display)
```

## Packaging a standalone app

Build a self-contained app with [PyInstaller](https://pyinstaller.org) (no
Python install required to run the result):

```sh
./scripts/build_app.sh        # or: uv run pyinstaller --noconfirm --clean heaviside.spec
```

Output:

- **macOS** → `dist/Heaviside.app` (drag to `/Applications`)
- **Windows / Linux** → `dist/Heaviside/` (run the `Heaviside` executable inside)

The bundle includes everything the app needs **except** `pdflatex` (TeX Live /
MiKTeX, with `circuitikz`), which the preview and exports compile with — bundling
a full TeX distribution is impractical, so it stays a user-installed dependency
and the app warns at startup if it is missing. (EPS export additionally needs
Poppler's `pdftocairo`, checked only when you actually export EPS.) Editing,
source generation, preview, and PDF/`.tex` export need only `pdflatex`. Build
configuration lives in [`heaviside.spec`](heaviside.spec).

## Documentation

- [`PROJECT_SPEC.md`](PROJECT_SPEC.md) — the authoritative, living specification.
  Any behavioral change must keep this in sync (see its §0).
- [`ARCHITECTURE.md`](ARCHITECTURE.md) — module layout and design overview.
- [`CLAUDE.md`](CLAUDE.md) — instructions for AI agents working in this repo.

## Project layout

```
app/
  canvas/      # QGraphicsScene/View, items, undo commands, SVG symbol rendering
  codegen/     # Schematic → CircuiTikZ source
  components/  # Component model + registry of component kinds
  preview/     # pdflatex compile worker and LaTeX templating
  schematic/   # data model, JSON I/O, validation
  ui/          # main window, palette, properties, source panel
main.py        # entry point
tools/         # build-time tooling (CircuiTikZ SVG export + manifest)
tests/         # pytest suite
```
