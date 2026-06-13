# Changelog

All notable changes to Heaviside are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-06-13

Baseline release. Earlier 0.1–0.3 tags were withdrawn and the version reset to
0.1.0; this is the first release of the current line.

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

- **Auto-export defaults are now PDF + PNG.** Saving a schematic writes
  `<name>.pdf` and `<name>.png` siblings by default — the two formats that
  need only `pdflatex` (PDF is included natively by LyX/Overleaf; PNG is
  rendered by the app's own PDF engine). The TeX snippet, EPS, and SVG
  siblings are now opt-in (Preferences → Export); SVG/EPS are the only
  formats needing a PDF→vector converter (Poppler or Inkscape), so a
  converter-less system no longer fails an export on every save. Installs
  that saved an explicit choice keep it; otherwise the sibling set changes
  from `.tex`/`.svg`/`.png` to `.pdf`/`.png` until re-configured.

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

[0.1.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.1.0
