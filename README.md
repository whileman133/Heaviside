<p align="center">
  <img src="assets/icon.png" alt="Heaviside logo" width="120">
</p>

# Heaviside

[![CI](https://github.com/whileman133/Heaviside/actions/workflows/ci.yml/badge.svg)](https://github.com/whileman133/Heaviside/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/whileman133/Heaviside?include_prereleases&display_name=tag&sort=semver)](https://github.com/whileman133/Heaviside/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An opinionated [WYSIWYM](https://en.wikipedia.org/wiki/WYSIWYM) editor for building publication-quality circuit diagrams with typeset mathematical annotations. It's a streamlined, local-first desktop tool designed for researchers, engineers, and educators that integrates into LyX and Overleaf workflows.

![The Heaviside editor: component palette, schematic canvas, and live CircuiTikZ source and PDF preview](docs/images/screenshot-overview.png)

## Features

- Inline LaTeX label editing, rendered as typeset math directly on the canvas (WYSIWYM)
- Automatic junction dots where wires and pins connect
- Open-terminal dots at unconnected wire ends and pins (configurable)
- Line hops at crossings where wires do not connect (configurable)
- Manhattan-routed wires with per-wire line styles, widths, and endpoint markers/arrowheads
- A component library of two-terminal parts (R, C, L, diodes, sources), multi-terminal parts (op amps, MOSFETs, BJTs), grounds, and supply rails
- Block-diagram primitives: labeled boxes, circles, and free text, with wires connecting anywhere on a shape's perimeter
- 90° rotation, mirroring, resizing, copy/paste, and full undo/redo
- Grid-disciplined canvas with quarter-grid CircuiTikZ snapping
- Clean, human-readable CircuiTikZ source as the primary output
- Parallel PDF, EPS, and SVG export for inclusion in LaTeX, LyX, or web documents
- Live, rendered PDF preview of the current schematic
- Save/load via a JSON `.hv` file format

> **Built spec-first with AI assistance.** Heaviside was developed from a
> detailed written specification with substantial help from AI coding assistants.
> The implementation follows the spec, the test suite (660+ tests) and spec are
> kept in sync, and the full methodology is documented in
> [`docs/ai-development.md`](docs/ai-development.md). See
> [`CONTRIBUTING.md`](CONTRIBUTING.md) for more.

## Download

Pre-built apps (always the latest release):

- **macOS (Apple Silicon)** → [Heaviside-macos-arm64.zip](https://github.com/whileman133/Heaviside/releases/latest/download/Heaviside-macos-arm64.zip)
- **Windows (x64)** → [Heaviside-windows-x64.zip](https://github.com/whileman133/Heaviside/releases/latest/download/Heaviside-windows-x64.zip)
- **Linux (x64)** → [Heaviside-linux-x64.tar.gz](https://github.com/whileman133/Heaviside/releases/latest/download/Heaviside-linux-x64.tar.gz)

Or browse all releases (with checksums and release notes) on the
[Releases page](https://github.com/whileman133/Heaviside/releases).

> **Required: `pdflatex`.** The downloaded app bundles everything **except** a
> LaTeX installation. You must have `pdflatex` (from [TeX Live](https://tug.org/texlive/)
> or [MiKTeX](https://miktex.org/)), with the `circuitikz` package, on your
> `PATH` — it compiles the live preview and the PDF/`.tex`/EPS/SVG exports. The app
> warns at startup if it can't find it. Drawing and CircuiTikZ source generation
> work without it; the preview and exports do not.
>
> **Optional: Poppler (for EPS and SVG export).** Exporting to **EPS** or **SVG**
> additionally needs `pdftocairo` from [Poppler](https://poppler.freedesktop.org/).
> Everything else — including PDF export — needs only `pdflatex`, so you can skip
> Poppler unless you specifically export EPS or SVG.

> **First launch:** these builds are not code-signed/notarized (Heaviside is a
> free, open-source alpha), so macOS and Windows will warn on first open — see
> [Opening the app with macOS](#opening-the-app-with-macos) for more information. On **Linux**,
> extract the archive (`tar -xzf Heaviside-linux-x64.tar.gz`) and run the
> `Heaviside` executable inside the folder.

## Opening the app with macOS

The distributed `Heaviside.app` is **not signed with an Apple Developer ID or
notarized** (Heaviside is a free, open-source project). macOS Gatekeeper will
therefore block it on first launch with a message like *“Apple could not verify
‘Heaviside.app’ is free of malware that may harm your Mac or compromise your
privacy.”* This does **not** indicate a problem with the app — it is how macOS treats software that hasn’t been notarized through a paid Developer ID.

To open it the first time, do **one** of the following:

- **System Settings → Privacy & Security:** try to open the app once (and dismiss
  the warning), then open **System Settings → Privacy & Security**, scroll to the
  **Security** section near the bottom, and click **“Open Anyway”** next to the
  note about Heaviside. Confirm in the dialog. After this, it opens normally.
- **Or** clear the download quarantine from Terminal, then open it:

  ```sh
  xattr -dr com.apple.quarantine /Applications/Heaviside.app
  open /Applications/Heaviside.app
  ```

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
are MIT-licensed and impose no such requirement.

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
