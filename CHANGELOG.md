# Changelog

All notable changes to Heaviside are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- **Paste no longer crashes** from the Edit menu or the Ctrl+V/⌘V shortcut
  ([#33](https://github.com/whileman133/Heaviside/issues/33)). The Paste action
  was connected straight to `scene.paste`, so Qt's `QAction.triggered` `checked`
  boolean was bound to paste's `at` argument and the "paste here" branch tried to
  subscript a bool (`TypeError: 'bool' object is not subscriptable`). The menu and
  shortcut now paste at the default offset; right-click "Paste here" is unaffected.

## [0.3.0] - 2026-06-20

### Changed
- **Line-hops now use CircuiTikZ's native `jump crossing` nodes** instead of a
  hand-drawn Bézier bump, so the exported figure uses the package the way it is
  intended (and gains its styling hooks, e.g. `crossing vertical`). Both crossing
  wires connect to the node's anchors — the hopping wire carries the arc, the
  crossed wire the gap. The on-canvas hop is redrawn to match. No `.hv` change:
  hops are derived geometry, never stored.

### Fixed
- The **CircuiTikZ Source** panel now reflects **line-hops** (and stays in step
  with the unconnected-pin marker), matching the compiled preview and the
  exported `.tex`. Previously the source panel omitted the hop bumps even though
  the figure and exports contained them.

## [0.2.0] - 2026-06-20

### Added
- **SI units (siunitx) and a custom LaTeX preamble**, in the inspector's
  **Document** tab ([#29](https://github.com/whileman133/Heaviside/issues/29)).
  **SI units (siunitx)** is **on by default** (most schematics use units at some
  point and it is cheap to load) and loads siunitx through CircuiTikZ so labels
  can use unit macros, e.g. `l=\qty{10}{\ohm}` or `\unit{\ampere}`. The **custom
  preamble** field splices arbitrary LaTeX (packages, macros, colours,
  `\ctikzset`) into the document preamble — the escape hatch for anything the
  inspector has no dedicated control for. Both settings travel with the `.hv`
  file, apply to the live preview and every export, and appear in the exported
  `.tex` (listed as comments in an `\input` snippet, which cannot add packages
  itself). With siunitx on, unit-macro labels also typeset live **on the canvas**
  (the on-canvas label renderer mirrors the document's siunitx setting), so
  `\qty{10}{\ohm}` is no longer blank on the canvas while rendering in the export.

### Changed
- **`.hv` file format bumped to `0.5`** to carry the new document preamble
  settings. Files from 0.1–0.4 load unchanged — an absent `siunitx` key defaults
  on (the new-document default) and the custom preamble to empty; a 0.4 or older
  build will refuse a 0.5 file rather than silently strip the new data.

## [0.1.0] - 2026-06-13

First public release of Heaviside. (Earlier 0.1–0.3 tags were withdrawn and the
version reset to 0.1.0 — this is the first release of the current line.)

### Added
- **Single-key component placement.** Press a key to start placing a component —
  defaults: `r`/`c`/`l`/`d` = resistor/capacitor/inductor/diode, `g` = ground,
  `t` = transistor, `v`/`i` = voltage/current annotation. Pressing another key
  **while a ghost is up swaps it** to that component, so you can change your mind
  without going back to the palette. The keys work window-wide (regardless of
  which panel has focus, but never while typing in a text field) and are
  **configurable** in **Preferences ▸ Shortcuts** (a key→component table with
  Add / Remove / Restore defaults). `s`/`w`/`p` stay reserved for the
  Select/Wire/Pan tools.
- **Right-click context menu for components and wires** with **Cut**, **Copy**,
  **Paste**, **Delete**, **Bring to Front**, and **Send to Back**. Right-clicking
  an unselected item targets just it; right-clicking inside a multi-selection acts
  on the whole group; right-clicking empty space still offers Paste. Menu **Paste
  drops the clipboard at the cursor** ("paste here"), while `Ctrl+V` keeps its
  fixed 1 GU offset.
- **Cut** (`Ctrl+X`, and the Edit menu) — copies the selection, then deletes it as
  one undoable step.
- **Front/back layering now works on every component**, not just drawing
  annotations (rect/text/circle/bipole) and wires. Plain circuit symbols
  (resistors, transistors, sources, …) can be sent to front/back too, on the
  canvas and in the generated LaTeX.

### Changed
- **Requires Python ≥ 3.12** (was 3.11). PySide6 6.11.1's bindings have a refcount
  bug that over-decrements `None` during ordinary Qt widget operations (seen
  building the component palette). On Python 3.11 (mortal `None`) this eventually
  crashes the process — deterministically on aarch64 (Raspberry Pi), intermittently
  elsewhere; on 3.12+ `None` is immortal (PEP 683), so the bug is harmless. The
  bundled downloads ship their own 3.12 interpreter, so only running from source on
  3.11 is affected.
- **Wire and Place modes now show a crosshair cursor**, so it's obvious at a glance
  when you're routing a wire or placing a component (previously the cursor stayed a
  plain arrow in Wire mode, which was easy to miss). Pan still shows the hand cursor;
  Select stays the arrow.
- **Rotate moved to `Ctrl+R`** (`⌘R` on macOS), freeing the plain `R` key to place
  a resistor (and to swap a live placement ghost). Rotate still turns the current
  selection or the placement ghost 90° clockwise.
- **Component palette reorganized into a 3-column grid of category cards** (was
  two columns), regrouped so related categories sit together (Resistors |
  Inductors | Capacitors, Diodes | Transistors | Switches, …). The panel is a
  little wider to fit the three columns.
- **Voltage and current annotations are now placed by drawing their span** —
  click the start point, then click the end point — instead of dropping a
  fixed-size ghost. The ghost appears after the first click and stretches to the
  cursor; the second click places the annotation. Endpoints **magnet-snap to
  component pins** (the same magnet wire drawing uses), so an annotation can be
  drawn exactly across a component. (Other components are unchanged: single-click
  ghost placement.)
- **File format bumped to 0.4.** `z_order` is now stored on every component (it
  was previously only saved for drawing annotations). Older files (0.1–0.3) still
  open; a 0.3-era build will refuse a 0.4 file rather than silently dropping a
  component's layer on save.
- **Voltage/current annotations (and the generic bipole) are now draggable from
  either endpoint.** Previously only the terminal handle could be dragged; now a
  press on either endpoint handle drags that end while the other stays fixed —
  the origin handle moves the component and adjusts its span so the terminal
  doesn't move (`MoveEndpointCommand`). Boxes (rect/circle) are unchanged (they
  still resize from the far corner only).
- **Clicking and holding an annotation endpoint drags it instead of starting a
  wire.** A current annotation's endpoints sit on connectable pins, so a press
  there used to auto-enter wire mode; the endpoint-drag gesture now takes
  priority, making the endpoints easy to grab and move.

### Fixed
- **Crash during heavy label rendering (notably on ARM/Raspberry Pi).** Canvas
  labels were typeset on worker threads that built the vector `QPainterPath`
  off the UI thread; when the UI thread garbage-collected at the same moment, the
  cross-thread Qt heap activity could corrupt memory and crash the app — reliably
  on aarch64, intermittently elsewhere. Workers now produce only the (Qt-free) SVG
  and the UI thread builds the path, so no Qt objects are ever created off the UI
  thread (verified: 0 crashes in 30 stress runs on aarch64, vs ~1 in 15 before).
- **Voltage/current annotation endpoints no longer "stick" near a pin.** Dragging
  an annotation endpoint had a 0.5 GU dead-zone that froze it near the other end
  and made the origin handle resist small drags. The endpoint now follows the
  cursor smoothly on the 0.25 GU grid (matching placement and wire vertices).
- **Labels with an equals sign now compile** (e.g. `l=$v=2$`). The value is
  brace-wrapped in the generated LaTeX (`l={$v=2$}`) so CircuiTikZ's option parser
  doesn't split on the inner `=` — previously such a label produced a "forgotten
  `$`" compile error. No escaping needed; just type it.

### Removed
- **Per-component keyboard shortcuts in the palette.** The category mnemonic
  letters (R/C/L/…) and the 1–9/0 "place the Nth component" digits — and their
  on-card letter / on-tile number badges — are gone; place components by clicking
  a category card then a component tile. `Ctrl+/` (focus the search box) is
  unchanged.

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

- **Auto-export defaults are TeX + PDF + PNG.** Saving a schematic writes
  `<name>.tex`, `<name>.pdf`, and `<name>.png` siblings by default. The `.tex`
  fragment is the primary output for the LaTeX/Overleaf/LyX audience (`\input` it
  into a paper) and is pure codegen — it needs no LaTeX at all; PDF and PNG need
  only `pdflatex`. EPS and SVG stay opt-in (Preferences → Export): they're the
  only formats needing a PDF→vector converter (Poppler or Inkscape), so a
  converter-less system never fails an export on every save.

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

[0.3.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.3.0
[0.2.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.2.0
[0.1.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.1.0
