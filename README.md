<p align="center">
  <img src="assets/icon.png" alt="Heaviside logo" width="120">
</p>

# Heaviside

[![CI](https://github.com/whileman133/Heaviside/actions/workflows/ci.yml/badge.svg)](https://github.com/whileman133/Heaviside/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/whileman133/Heaviside?include_prereleases&display_name=tag&sort=semver)](https://github.com/whileman133/Heaviside/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An opinionated [WYSIWYM](https://en.wikipedia.org/wiki/WYSIWYM) editor for building publication-quality circuit diagrams with typeset mathematical annotations. A desktop tool designed for researchers, educators, and engineers, integrating into LyX, Overleaf, and LaTeX workflows with minimum effort.

<table>
  <tr>
    <td align="center" colspan="2">
    <img src="docs/images/examples/boost-converter-light.png" alt="Boost converter example open in the Heaviside editor, light mode"><br>
      <sub><b>Boost Converter</b> · Power Electronics · light mode</sub>
    </td>
  </tr>
  <tr>
    <td align="center" width="50%">
      <img src="docs/images/examples/porous-electrode-dark.png" alt="Porous electrode interface model open in the Heaviside editor, dark mode"><br>
      <sub><b>Porous Electrode Interface</b> · Battery Models · dark mode</sub>
    </td>
    <td align="center" width="50%">
      <img src="docs/images/examples/mux-4-1-dark.png" alt="4:1 multiplexer logic example open in the Heaviside editor, dark mode"><br>
      <sub><b>4:1 MUX</b> · Logic Circuits · dark mode</sub>
    </td>
  </tr>
</table>

<p align="center"><sub>Bundled examples (<code>examples/</code>) open in the editor. The release pipeline re-captures these screenshots automatically, so they always match the latest release.</sub></p>

## Features

* **CircuiTikZ, visually.** Draw on a grid-snapped canvas and get clean, publication-ready CircuiTikZ source — with a live compiled PDF preview as you work.
* **Typeset math everywhere.** Component and wire labels are LaTeX (`$R_1$`, `$v(t)$`), rendered on the canvas as they'll appear in print.
* **Smart wiring.** Right-angle routing with automatic junction dots, open-terminal markers, and line hops at crossings.
* **Symbols and blocks.** Passives, sources, semiconductors, op-amps, and configurable logic gates — plus boxes, circles, and free text for block diagrams.
* **Exports that stay current.** Every save refreshes the `.tex` and compiled `.pdf`/`.svg`/`.eps` alongside your schematic, so your paper's figures never go stale.

> **Built spec-first with AI assistance.** Heaviside was developed from a detailed written specification with help from AI coding assistants. The test suite (1000+ tests) and spec are kept in sync.

## Download

- **macOS (Apple Silicon)** → [Heaviside-macos-arm64.dmg](https://github.com/whileman133/Heaviside/releases/latest/download/Heaviside-macos-arm64.dmg) — drag to Applications. *(Intel Macs: [build from source](#building-from-source).)*
- **Windows (x64)** → [Heaviside-windows-x64-setup.exe](https://github.com/whileman133/Heaviside/releases/latest/download/Heaviside-windows-x64-setup.exe) (installer) · [Heaviside-windows-x64.zip](https://github.com/whileman133/Heaviside/releases/latest/download/Heaviside-windows-x64.zip) (portable)
- **Linux (x64)** → [Heaviside-linux-x86_64.AppImage](https://github.com/whileman133/Heaviside/releases/latest/download/Heaviside-linux-x86_64.AppImage) — `chmod +x`, then run · [Heaviside-linux-x64.tar.gz](https://github.com/whileman133/Heaviside/releases/latest/download/Heaviside-linux-x64.tar.gz) (portable)

All releases, with checksums and notes, on the [Releases page](https://github.com/whileman133/Heaviside/releases).

> **LaTeX is optional.** Everything works without it except the live PDF preview
> and the PDF/EPS/SVG exports, which need `pdflatex` with the `circuitikz`
> package on your `PATH` (EPS/SVG also need
> [Poppler](https://poppler.freedesktop.org/)'s `pdftocairo`). Custom tool paths:
> **Preferences → Tools**.

## Getting started

1. **Launch Heaviside.** You're greeted by a welcome screen. Start a blank
   schematic with **File → New**, or explore a ready-made one via
   **File → Open Example ▸** (these ship with the app).
2. **Place components.** Drag symbols from the component palette on the left onto
   the canvas. They snap to the CircuiTikZ grid so everything stays aligned.
3. **Wire them up.** Drag from one component terminal to another; wires route at
   right angles and drop junction dots automatically.
4. **Label and style.** Select a component or wire to edit its labels (typeset
   math, e.g. `$R_1$`), value, orientation, and style in the properties panel.
5. **Watch the source and preview.** The CircuiTikZ source and a live compiled
   PDF preview update as you work. Press **Ctrl/Cmd+Return** to force a recompile.
6. **Save once, export forever.** **File → Save** writes the `.hv` source and, on
   every save, automatically refreshes the co-located `.tex` and image exports so
   your paper's figures stay current. You can also export on demand from the
   **File → Export** menu (`.tex`, `.pdf`, `.svg`, `.eps`, `.png`).

## Architecture

Heaviside is split into a **View** layer built on Qt and a
**Model** layer of plain Python. The model, comprising the schematic data, the component library, and the CircuiTikZ generator, holds the logic and is testable without a display. The UI and canvas sit on top of the model.

![Heaviside architecture: a Qt View layer (UI shell, canvas, undoable commands, preview engine) above a pure-Python Model layer (schematic model, component library, CircuiTikZ generator) that emits LaTeX source and a rendered preview](docs/images/architecture.svg)

```
app/
  canvas/      # QGraphicsScene/View, items, undo commands, SVG symbol rendering
  codegen/     # Schematic → CircuiTikZ source
  components/  # Component model + registry of component kinds
  preview/     # pdflatex compile worker and LaTeX templating
  schematic/   # data model, JSON I/O, validation
  ui/          # main window, palette, properties, source panel
main.py        # entry point
components/     # Generated symbol data (geometry.json, definitions.json) + generator
tests/         # pytest suite
```

## Building from source

Heaviside uses [`uv`](https://docs.astral.sh/uv/) and targets **Python ≥ 3.11**. Python dependencies (PySide6, pydantic, qtawesome) are declared in
[`pyproject.toml`](pyproject.toml) and installed by `uv`. (As when running a downloaded build, the preview and exports need `pdflatex` on your `PATH`, and EPS/SVG export additionally needs Poppler — see [Download](#download).)

```sh
uv run heaviside              # run from source

uv run pytest                 # full test suite with coverage
QT_QPA_PLATFORM=offscreen uv run pytest   # headless (CI / no display)
```

### Packaging a standalone app

Build a self-contained bundle with [PyInstaller](https://pyinstaller.org):

```sh
uv run python scripts/build.py    # or: uv run pyinstaller --noconfirm --clean heaviside.spec
```

`build.py` is cross-platform (macOS, Windows, Linux): it regenerates the app
icons from `assets/icon.png`, ensures the bundled license texts are present, and
runs PyInstaller. Output is `dist/Heaviside.app` on macOS and `dist/Heaviside/`
elsewhere. Build configuration lives in [`heaviside.spec`](heaviside.spec).

## Documentation

- [`PROJECT_SPEC.md`](PROJECT_SPEC.md) — the authoritative, living specification.
  Any behavioral change must keep this in sync (see its §0).
- [`CLAUDE.md`](CLAUDE.md) — instructions for AI agents working in this repo.

## Contributing

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md) for the
development setup, the test/spec sync rule, and how this codebase was built.

## License

Heaviside is released under the [MIT License](LICENSE).

Its GUI toolkit, **PySide6 (Qt for Python), is licensed under the LGPL v3**.
Using PySide6 as an ordinary dependency (the `uv run` workflow above) imposes no
extra obligations on you. The other Python dependencies (`pydantic`, `qtawesome`)
are MIT-licensed and impose no requirements.

### Redistributing the standalone app (LGPL compliance)

If you **redistribute the bundled `.app` / `.exe`** built with PyInstaller, the
LGPLv3 attaches obligations to that binary for the bundled Qt/PySide6. They are
satisfied out of the box by the files in [`licenses/`](licenses/), which the
build bundles **inside** the distributable (see `heaviside.spec`):

- **Notice + license text** — `licenses/THIRD_PARTY_LICENSES.md` plus
  `LGPL-3.0.txt` (and `GPL-3.0.txt`, fetched at build time) ship inside the
  `.app` / `Heaviside/` folder.
- **Corresponding source** — the notice links to the exact PySide6/Qt source
  releases bundled.
- **Relinking** — the build is a *directory* bundle (`.app` / onedir), so the Qt
  libraries are separate, user-replaceable files; do **not** switch to a
  PyInstaller *onefile* build, which would defeat this.

This keeps Heaviside itself fully MIT — the LGPL touches only the bundled Qt
portion, and you are not required to open any of your own code. See
`licenses/THIRD_PARTY_LICENSES.md` for the full details.
