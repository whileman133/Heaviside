# Heaviside

A graphical editor for producing publication-quality circuit diagrams that
output valid [CircuiTikZ](https://github.com/circuitikz/circuitikz) LaTeX markup.
It targets researchers and engineers who author documents in LaTeX or LyX and
need schematics with typeset mathematical annotations.

- Grid-disciplined, fixed-component-size canvas for schematic entry
- Clean, human-readable CircuiTikZ source as the primary output
- Lossless save/load via a JSON `.ctikz` format
- Live, rendered PDF preview of the current schematic
- Wire-to-wire connectivity with automatic junction dots

## Requirements

- Python ≥ 3.11
- [`uv`](https://docs.astral.sh/uv/) for environment and dependency management
- `pdflatex` on your `PATH`, with the `circuitikz` package installed (TeX Live or
  MiKTeX) — used for the rendered preview
- [Poppler](https://poppler.freedesktop.org/) (`pdftoppm`) — used by `pdf2image`
  to rasterize the preview PDF

Python dependencies (PySide6, pydantic, pdf2image, qtawesome) are declared in
[`pyproject.toml`](pyproject.toml) and installed by `uv`.

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
