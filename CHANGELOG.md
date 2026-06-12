# Changelog

All notable changes to Heaviside are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **A stray `}` in a label can no longer inject raw TeX.** Every user
  label/text field emitted inside a brace group in the generated CircuiTikZ
  (component options, node text, wire endpoint/mid labels, bipole `t=`) is now
  brace-balanced, so an unmatched `}` cannot escape its group and splice raw
  TeX into the document. Exported `.tex` documents and snippets also begin
  with a header comment warning that label fields are raw LaTeX taken verbatim
  from the `.hv` file and should be compiled **without** shell-escape.
- **Warning when a file's labels contain dangerous LaTeX.** Opening a `.hv`
  whose label/text fields contain potentially dangerous LaTeX commands
  (`\write18`, `\input`, …) now shows a warning, so an untrusted file can't
  smuggle them in silently.
- **The update notifier validates the release URL.** The download URL returned
  by the GitHub API is opened only when it is `https` on `github.com`;
  anything else falls back to the official releases page.
- **Private math-label disk cache.** The on-disk cache of compiled label SVGs
  moved from a shared temp directory to a per-user private (0700) directory,
  so another local user cannot pre-plant cache entries the app would trust.
- **Corrupt `.hv` files fail with a clean error.** NaN/Infinity numbers,
  malformed fields, and implausibly large files (over 32 MB) are now rejected
  with a descriptive load error instead of crashing the app.

### Added
- **Auto-generated README example screenshots.** The README now opens with
  bundled examples (Boost Converter, 4:1 MUX, Porous Electrode Interface)
  shown in the full editor — palette, canvas, inspector, and live source/PDF
  preview — mixing light and dark mode. The release pipeline re-captures them
  (`scripts/render_screenshots.py`) on every version tag and commits changes
  back to `main`, so the screenshots always match the latest release.
- **The unsaved-changes prompt now offers Save.** Closing or replacing a
  modified document asks **Save / Don't Save / Cancel** (Save is the default)
  instead of only offering to discard.
- **`.hv.bak` backups and safer saves.** Saving keeps a `.hv.bak` copy of the
  file being replaced, and writes are fsync'd before the atomic rename, so a
  crash or power loss mid-save can't cost you both the new and the old file.
- **Crash guard.** An internal error is logged to `heaviside-errors.log` in
  the app-data folder and reported in a dialog without quitting — you keep
  your session and can save your work.

### Changed
- **`.hv` format version bumped to 0.3.** The new version covers the optional
  wire/component fields added since 0.2 (endpoint markers and labels, scale,
  params, variants, line width, …) so an older build that would silently strip
  them refuses the file instead. 0.1/0.2 files still load unchanged; new
  documents declare 0.3.
- **Saving validates the document first** and refuses to overwrite a good file
  with a corrupt one — an invalid in-memory schematic raises a clear error and
  nothing is written.
- **Post-save auto-export runs in the background.** The auto-exported
  TeX/PDF/EPS/SVG/PNG siblings are now produced on a worker thread
  (single-flight; results reported in the status bar), so saving no longer
  freezes the UI while LaTeX runs.
- **Undo/Redo menu items track the edit history** (disabled when there is
  nothing to undo/redo), and the window's dirty marker clears when you undo
  back to the last-saved state.
- **Document-tab voltage/current style edits are undoable.**
- **Canvas item removal is grab-safe and rebuilds coalesce.** Removing a
  canvas item now always releases an in-flight mouse grab first, and a scene
  rebuild requested while one is already running is coalesced instead of
  recursing — hardening against use-after-free crashes when a command runs
  mid-gesture.

### Fixed
- **Nondeterministic crash (segfault) while math labels rendered in the
  background.** Each async label render created a short-lived Qt signals
  object whose final reference could be dropped on a worker thread —
  destroying a UI-thread object off-thread, which could crash the app (seen
  as a CI segfault under load). All results now flow through one permanent
  UI-thread dispatcher, and the ziamath fallback engine is serialised (its
  shared font state is not thread-safe).
- **Drag previews can no longer differ from the committed result.** The ghost
  geometry shown while dragging components, whole wires, box resizes, and
  vertices is now computed by the same shared functions
  (`app/schematic/reshape.py`) that the committed commands apply — previously
  the previews hand-mirrored the commit rules and had drifted (a junction tap
  on a co-dragged wire followed on commit but not in the preview).
- **Redundant fully-covered wires are detected despite float noise.** The
  contained-wire test now uses the same 6-decimal tolerance as all other
  connectivity comparisons, so off-grid pin coordinates can't hide a wire that
  lies entirely on top of others.
- **Wires connected to off-grid pins could silently detach.** Connectivity
  comparisons against a scaled gate's off-grid pins used exact float equality,
  so float noise during moves/rotations could quietly disconnect a wire; every
  coincidence test now goes through one rounded comparison convention.
- **Wire splits during a move/nudge could corrupt the wire.** A split site was
  computed against the wire's pre-move geometry but applied after the move had
  reshaped it; the split now re-resolves against the wire's current geometry
  at execution time.
- **Undo restores a removed wire completely.** Undoing a resize, vertex drag,
  junction drag, or group rotation that removed a wire now restores its
  labels, markers, line style, and stacking position verbatim — not just its
  points.
- **Group-rotating a mirrored component no longer detaches its wires.** The
  rotation step now accounts for the mirror being applied outermost, so the
  mirrored component's pins land where the rotation moved the wires.
- **Deleting both taps of a bus merges the rail back into one wire.**
  Sequential junction dissolves in one delete now compose instead of leaving
  the remaining segments split.
- **Releasing a resize or endpoint handle without moving no longer leaves a
  stale preview** on the canvas.
- **Junction dots suppressed via "no junction dots" no longer flicker during
  drags** — the live drag preview honours the same suppression as the final
  render.
- **Multi-edit inspector changes are computed against the live document.**
  Each edit in a bulk apply now reads the current state (not a stale
  snapshot), and a composite edit that fails part-way rolls back entirely
  instead of half-applying.
- **Inspector edits typed just before a save/export are no longer lost.**
  Pending debounced edits are flushed before Save / Save As / every export and
  the unsaved-changes prompt, and a programmatic reload no longer clobbers a
  field you are typing in.
- **Bipole labels with commas inside math are no longer mangled** (e.g.
  `t=$f(a,b)$` survives a round-trip through the inspector).
- **Hand-authored fill and line-style values survive unrelated inspector
  edits** instead of being snapped to the nearest preset.
- **Dark-mode readability** of the Help/About dialogs, the Preferences hint
  text, the welcome screen, and the status bar.
- **Dark mode now reaches native widgets everywhere.** On platforms whose
  theme ignores Qt's colour-scheme request (headless/offscreen sessions, bare
  Linux desktops), the inspector sidebar and other native controls stayed
  light in dark mode; an explicit theme-token palette now substitutes when
  the request is not honoured. A saved Light/Dark preference is also pinned
  at startup instead of only after the next toolbar toggle.
- **Faster startup: no more "Populating font family aliases" pause.** The
  CircuiTikZ source pane now asks for the platform's real fixed-width font
  instead of the generic "Monospace" family, which forced Qt to scan every
  installed font on systems without one (macOS).
- **Preview update failures now show in the status bar** instead of failing
  silently.
- **A corrupt PNG-resolution setting is clamped** to the dialog's 72–1200 dpi
  range instead of being used verbatim.
- **`.hv` tolerance fixes:** a rotation stored as `90.0` (a float) is accepted
  and normalised; a boolean `z_order` is rejected as the type error it is.

### Added
- **~60 new components — a big library build-out.** Filled out the symbol library
  with previously-missing CircuiTikZ parts (every one verified to render):
  - **Resistors/sensors:** varistor, photoresistor (LDR), NTC/PTC thermistors.
  - **Capacitors:** variable, ferroelectric, curved, capacitive-sensor, and
    piezoelectric/crystal.
  - **Inductors:** variable inductor and inductive sensor.
  - **Diodes:** thyristor (SCR) and TRIAC — each with a wireable **gate** terminal
    in addition to anode/cathode.
  - **Transistors:** N/P **IGBTs**, simplified N/P **MOS** (enhancement & depletion),
    and the **ISFET**.
  - **Tubes** (new category): triode, vacuum diode, pentode, tetrode — the
    multi-grid tubes expose their extra grid taps (tetrode: screen; pentode:
    screen + suppressor).
  - **Amplifiers:** fully-differential op-amp and Schmitt triggers (normal + inverting).
  - **Blocks** (new category): generic amplifier, ADC, DAC, low/high/band/all-pass
    filters, phase shifter, detector, VCO, and gyrator.
  - **Sources:** DC voltage/current, square-wave, triangle-wave, and noise sources.
  - **Switches:** SPST, cute NO/NC, cute SPDT (up/down/mid), rotary, reed, toggle.
  - **Instruments:** oscilloscope and a generic meter.
  - **Transducers** (new category): loudspeaker, microphone, buzzer.
  - **Antennas** (new category) and misc parts (asymmetric fuse, SQUID, light bulb).

  Four new palette categories were added — **Tubes**, **Blocks**, **Transducers**,
  **Antennas** — each with its own card icon and keyboard shortcut.
- **Transformer components for power electronics.** Two-winding **transformer**
  (air-core) and **transformer (iron core)** symbols in the **Inductors** palette
  group, with four grid-aligned terminals (primary + secondary) — each in
  **american**, **cute**, and **European** coil styles (six in all). The properties
  inspector has checkboxes to place a **winding-polarity dot** at any of the four
  winding ends, so you can set the transformer's dot convention.
- **MIPS datapath example.** A new bundled example (**Logic Circuits → MIPS
  Datapath**) showing a simple single-cycle datapath — PC, instruction/data
  memory, register file, sign-extend, the new **ALU**, an **adder** (PC+4), and
  **ALUSrc/MemtoReg multiplexers** — wired end to end.
- **Digital logic blocks for building processors/datapaths.** New components in the
  **Logic** palette group, drawn with authentic CircuiTikZ shapes: **D / SR / JK / T
  flip-flops**, a **multiplexer** and **demultiplexer**, an **ALU** (the classic
  notched trapezoid), and an **adder**. Their pins are grid-aligned where the symbol
  can be rescaled to manage it (flip-flops fully; mux/demux data lines), so wires
  snap onto them cleanly; the few that can't (a mux's select lines, the ALU
  operands) stay just off-grid and still connect. They have a **Size** dropdown in
  the inspector (25 %–200 %), like the logic gates, and render identically on-canvas
  and in LaTeX export.
- **Configurable multiplexer / demultiplexer.** The mux and demux have **Inputs**
  (or **Outputs**) and **Selects** spinboxes in the properties inspector — set the
  number of data lines (2–16) and select/control lines (1–4) independently, and the
  symbol resizes to match (just like a logic gate's input count).
- **Theme: System / Light / Dark.** The toolbar now offers three theme buttons —
  **System** (follow the OS appearance, a monitor icon), **Light** (sun), and
  **Dark** (moon) — rendered as a **segmented control** (one bordered pill with
  the active cell highlighted) so all three are visible and read as a grouped set
  with exactly one active at a time. Your choice is remembered between launches;
  **System** tracks the OS live.
- **Windows installer.** Releases now include `Heaviside-windows-x64-setup.exe`,
  an installer that sets Heaviside up (no admin required), adds a Start Menu
  shortcut and an uninstaller, and associates `.hv` files so you can
  double-click a schematic to open it. The portable `.zip` is still provided.
- **Linux AppImage.** Releases now include `Heaviside-linux-x86_64.AppImage`, a
  single run-anywhere file (no install, no root): `chmod +x` it and run. It
  carries a desktop menu entry and a `.hv` file association, and the portable
  `.tar.gz` is still provided.
- **Double-click a `.hv` file to open it.** Opening a schematic from the OS now
  loads it into the editor — via the Windows file association, a macOS Finder
  "open with", or a path on the command line.
- **Wire routing follows your cursor's path.** When you draw a wire out in one
  direction and then move perpendicular, the elbow now keeps the direction you
  first went (e.g. right-then-down stays right-then-down) instead of flipping once
  the second leg grows longer. The router remembers the leg's out-direction until
  you drop the next corner.
- **Move whole wires.** Select a wire and drag its body to reposition it. The
  wire translates rigidly and any wire joined to it at a junction follows at the
  shared point (so taps stay connected), as a single undoable step.
- **Edit several wires at once.** Select multiple wires (Shift-click or
  rubber-band) and the properties inspector edits their shared wire properties —
  line style, width, endpoint markers/labels, junction/termination dots, z-order,
  line-hops — applying each change to every selected wire as one undo step.
- **Resize logic gates.** Logic gates (AND/OR/NAND/…/inverter/buffer) now have a
  **Size** control in the properties inspector (25 %–200 %) and are placed compact
  by default (half size) — the placement preview already shows the compact size.
  A gate's pins sit exactly at its terminals at every size, with no extra stubs:
  wires connect to a scaled gate's pins directly, and that connection is an
  ordinary wire you can style. Existing files load unchanged (gates without a
  saved size stay full-size).
- **Wires snap to a component pin even when it is off-grid.** A wire endpoint
  snaps onto a pin that does not lie on the 0.25-GU grid — a scaled logic gate's
  terminal — so you can wire to gates at any size and the connection is a normal,
  styleable wire (instead of an unstyleable lead drawn by the gate). The router
  keeps the rest of the wire on the grid, so only the pin end is off-grid.
- **Bulk-edit shared properties across mixed component types.** Selecting
  components of different kinds together (e.g. a resistor and a capacitor) now
  shows the properties they have in common — stroke width, rotation, font, fill,
  line style, layer — and editing one applies it to every selected component as a
  single undo step. (Previously a mixed-kind selection showed only a count.)
  Kind-specific fields like the CircuiTikZ options string stay editable only when
  the whole selection is one kind.
- **Unified stroke / outline width.** A single **Stroke** control in the
  properties inspector sets the line width of any component — a circuit symbol's
  stroke *and* a block's (rectangle / circle / bipole) outline — replacing the
  former separate "Border width". Because it is one property, you can select a mix
  of symbols and blocks (e.g. a resistor and a rectangle) and set their widths
  together in one undo step. It renders proportionally on the canvas and emits a
  `line width=` option in the generated CircuiTikZ. (Older `.hv` files that stored
  a block's `border_width` load transparently into the unified width.)
- **Document properties tab.** The properties inspector is now split into two
  tabs: **Properties** (the per-object inspector) and **Document** (per-document
  CircuiTikZ voltage/current label conventions). The Document tab **replaces the
  Edit ▸ Document Settings… dialog** and applies changes live. It's shown
  automatically whenever nothing is selected.
- **Voltage & current annotations are drawn on the canvas.** A `v=` label now
  shows the CircuiTikZ-style polarity decoration — **American ± signs** at the
  terminals (the default) or a **European voltage arrow** alongside the body when
  the document voltage style is European — and an `i=` label shows a **current
  direction arrow** along the lead. Polarity and arrow direction follow the
  component's pin traversal (so rotated/mirrored parts decorate correctly), and
  switching the document's voltage/current style updates existing components
  immediately. (Readable convention, not a pixel-exact copy of CircuiTikZ.)
- **Light/dark toggle button on the toolbar.** A sun/moon button flips between
  light and dark mode on demand; once you use it, the app stops following the OS
  appearance for the rest of the session.
- **Keyboard shortcuts for the component palette.** Each category shows a subtle
  **letter** (R=Resistors, C=Capacitors, L=Inductors, D=Diodes, …); pressing it
  selects that category. The first ten components of the active category show a
  subtle **1–9/0** hint in the top-right of their tile; pressing the digit places
  that component. Keys are ignored while typing in a field, and the canvas keeps
  R/S/W/P (rotate/tools) while it's focused. (See the Help dialog, F1.)
- **Export to PNG**, plus an **auto-export PNG on save** preference (alongside the
  existing TeX/PDF/EPS/SVG). A new **PNG resolution** preference (default **300
  dpi**, publication grade) controls both Copy PNG and PNG export.
- **Dark mode (follows the system appearance).** The whole app — canvas (paper,
  grid, symbol/wire ink), toolbars, component palette, properties, source panel,
  the welcome screen, and the LaTeX preview — now switches between light and dark
  to match the OS appearance, live via `colorSchemeChanged`. Native dialogs and
  spin/combo boxes follow the system as before. In dark mode the preview is
  recompiled with a dark page and light ink so it no longer glares against the UI;
  **exports stay light** (the copied/saved figure is the publication artifact —
  white paper, black ink — so dark mode never affects what you put in a document).
- **Switches and choke.** Added a Switches category with normally-open (`nos`),
  normally-closed (`ncs`), push-button (`push button`), **opening** and
  **closing** switches, a **3-terminal SPDT** switch, plus a Choke (`cute choke`)
  inductor.
- **Battery, cell, and amplifier components.** Added a multi-cell **Battery**
  (`battery`) in Sources, an **Instrumentation Amplifier** (`inst amp`) and a
  **Transconductance Amplifier** (`gm amp`) in Amplifiers. The existing
  single-cell symbol (`battery1`) is relabeled **"Cell"** to distinguish it from
  the new Battery.
- **Multi-component property editing.** Shift-click (or rubber-band) to select
  several components, and when they're all the **same kind** the property
  inspector edits **all of them at once** — changing options, rotation, mirror,
  variants, inputs, fonts, styles, or layer applies to every selected component
  as a single undo step. (A mixed-kind selection shows just a count.)

### Fixed
- **Wires can now leave a thyristor/TRIAC gate vertically, not just horizontally.**
  Routing a wire from the gate (a pin that sits off-grid in both directions) always
  elbowed horizontally first, so dragging up or down didn't follow the cursor. The
  router now honours the drawing direction for these pins — vertical routing works
  just like horizontal — while pins that sit on a single lead line still extend
  along that lead.
- **Thyristor and TRIAC now render correctly in the CircuiTikZ output.** With their
  new gate terminal they have three pins, which tripped a two-pin assumption in the
  span calculation and collapsed the device to a zero-length `to[thyristor] (x,y)
  (x,y)` — so the exported figure showed only stray dots while the canvas looked
  fine. The span is now taken from the two main (anode/cathode) terminals, so the
  output matches the canvas.
- **Rotary and cute SPDT switches now match between the canvas and the LaTeX
  output.** Their switch blade was sheared (drawn thick and slanted) in the
  exported CircuiTikZ because the symbol was being stretched by a non-uniform
  scale to force its throws onto the grid; it now uses a uniform scale, so the
  rendered output matches what you see on the canvas.
- **Connected wires follow a gate's pins when you change its size or input count.**
  Rescaling a logic gate — or changing its number of inputs — moves its pins; wires
  connected to them now re-route to follow, keeping the schematic valid. (When you
  reduce the input count, a wire on a removed input is left snapped to the grid as
  a disconnected end you can rewire.) Previously the wires kept their old endpoints
  — which no longer landed on a pin or the grid — and the CircuiTikZ export failed
  with an "invalid schematic" error. Undo restores the original wiring exactly.
- **Off-grid pin connections survive a vertex drag.** Dragging a wire endpoint
  onto a scaled logic gate's (off-grid) pin now snaps the live preview onto the
  pin and commits there — instead of snapping to the nearest grid node and
  detaching. Dropping a vertex anywhere else still snaps to the grid as before.
- **Routing from an off-grid pin keeps the first jog off-grid.** A wire drawn from
  (or into) a scaled gate's off-grid pin now extends along the pin's own lead line
  and elbows onto the grid one segment later, instead of immediately snapping to
  the grid at the pin.
- **Wire vertices slide along an off-grid pin's axis.** Dragging a vertex that is
  collinear with an off-grid pin now keeps the off-grid coordinate (the segment
  into the pin stays straight) while the other coordinate snaps to the grid —
  instead of forcing the whole vertex to the grid and introducing a jog. Dragging
  away from the axis snaps to the grid as before, and the on-grid line *between*
  two adjacent off-grid pins is reachable (a pin only captures the vertex when the
  cursor is genuinely closer to it than to that grid line).
- **Suppressed terminal dots no longer reappear when you drag.** A wire with
  "No termination dots" (or a custom end marker) kept its open-circle terminals
  hidden when set, but any subsequent drag of *any* component or wire re-added
  them until the wire was clicked. The drag-time preview now honours the same
  opt-outs as the final render.
- **Wire labels no longer disappear when a wire is connected.** Connecting to the
  middle of a labelled wire (which splits it) — or dissolving a T-junction, or a
  move that collapses a wire — now preserves the wire's start/end labels, markers,
  line style, and other properties on the resulting wire(s), and restores them
  exactly on undo. (Previously a split/merge rebuilt the wire from scratch and
  dropped them.)
- **Shift-click now extends the selection.** Shift (or Ctrl/Cmd) clicking a
  component or wire adds it to — or toggles it in — the current selection instead
  of replacing it, so you can build a multi-selection by clicking.
- **Shift-click under a voltage/current annotation selects the element, not the
  annotation.** When an `open`/`short` annotation's arrow and label float across
  the elements it measures (e.g. the cell-voltage arrow in the porous-electrode
  example), a modifier-click on one of those elements now adds *that element* to
  the selection — matching a plain click — instead of the annotation on top of it.
- **European voltage arrows are drawn curved** on the canvas (a bowed arc with the
  arrowhead at the head end), matching CircuiTikZ, instead of a straight line.
- **European voltage/current convention no longer restyles component symbols.** It
  is now applied as a local `voltage=european` / `current=european` option on each
  annotated component rather than a global `\ctikzset`, so it only affects the
  v=/i= arrows — Heaviside already provides separate American/European *symbols* as
  distinct components.
- **Open annotation's European voltage label** now sits beside its curved arrow
  instead of centered on the line crossing it.
- **Current (`i=`) label now matches the LaTeX output.** The on-canvas current
  annotation is drawn the way CircuiTikZ draws it — a single **arrowhead** on the
  wire near the exit terminal (pointing in the current direction) with the value
  label **centred over the head** — instead of a full arrow stacked high above the
  component's value label. (The shaft is gone, so it never overlaps the body.)
- **Dark mode keeps native controls and themes them properly.** Forcing dark mode
  with the toolbar toggle now drives the OS colour scheme (`setColorScheme`, Qt
  6.8+), so the **native** form controls, dialogs, tooltips, tab bar, scrollbars,
  and the window background all re-render dark — no more half-light controls, and
  the controls stay native (not restyled). The inspector tabs are a plain native
  `QTabWidget` again. The Properties tab body now shows the panel background
  (clear, like the Document tab) instead of an inset white box, and the category
  cards' icon/name no longer draw an opaque box behind them.
- **Dark mode no longer leaves some text light.** Switching to dark mode via the
  toolbar now also re-inks the palette **category names** and the **properties
  inspector** title/section/hint labels (their pinned stylesheet colours did not
  follow the theme before).
- **Redundant fully-overlapping wires are removed.** A wire whose whole length
  lies on top of other wires (e.g. a lead dragged collinearly onto the rail it
  connects to) is now dropped as redundant when a move creates it (undoable),
  alongside the existing single-point degenerate-wire handling.
- **SPDT switch terminals now line up with its leads.** The 3-terminal SPDT
  switch was drawn with its pins off the symbol's actual poles — the output pins
  sat slightly inboard and above/below where the leads ended. It is now an
  anchor-pinned, scaled symbol (like the MOSFET/BJT), so the input and the two
  throws sit exactly on their terminals.
- **Dragging a component no longer leaves a stray "dead" wire (and phantom
  junction dot).** Moving a component so a connected lead's endpoint slid onto the
  point where the move also wanted to split that wire produced an invisible
  degenerate single-point wire — which then flashed a spurious connection dot at
  the terminal on later drags. The wire split now refuses to carve off a
  zero-length half (and the drag preview ignores any such leftover wire). The
  Boost Converter example, which already contained one, has been cleaned.
- **Stray degenerate wires are now visible.** As a safety net, a degenerate
  single-point wire (e.g. from an older file) is drawn on the canvas as a **red ✕**
  that can be selected and deleted, instead of being silently invisible.
- **Dragging a component off a wire junction no longer tears the net.** Dragging a
  component pin onto a junction (where a wire is split in two) and back used to
  drag **both** wire stubs off the node, leaving overlapping segments and an
  erroneous junction dot at the pin. A pin now follows only its own single lead;
  on a shared junction the existing net stays put and the component stays
  connected by a fresh **lead that rubber-bands** from the node to the pin's new
  position (shown live during the drag). Dragging onto a junction and back now
  restores the original wiring exactly.
- **Scrollbars look right again.** The themed panels' stylesheets had turned the
  CircuiTikZ source (and palette) scrollbars non-native, so they rendered with
  ugly default arrow buttons. They now use a clean themed scrollbar (a rounded
  muted handle, no arrows), and the properties inspector reserves room on the
  right so its native (overlay) scrollbar doesn't cover the fields.
- **Labels no longer vanish from the canvas after a one-off render failure.** A
  component label (e.g. a resistor's `l=$R$`) could render to nothing because the
  math-label cache stored a transient compile failure as a *permanent* empty
  sentinel — once `$R$` failed to compile once, it never rendered again. Failures
  are no longer cached (an empty/missing entry is retried and self-heals), and
  successful renders are written atomically so a partial write can't poison the
  cache either. Existing poisoned entries recover automatically. The in-memory
  render and **baseline memos** likewise no longer store a transient failure —
  so a one-off compile hiccup can't blank a label or shift label baselines for
  the rest of the session — and a **corrupted cached label SVG** on disk is
  detected, discarded, and recompiled instead of being trusted forever.
- **Copy PDF / Copy SVG now paste into Word, PowerPoint, and Google Docs.**
  Copy PDF placed the figure only under the `application/pdf` MIME type, which
  macOS wraps in a flavor Office doesn't recognize — so nothing pasted; Copy SVG
  also put the markup on the clipboard as plain text, so Office pasted the raw
  `<svg>` XML. Both now expose the figure under its real macOS pasteboard UTI
  (`com.adobe.pdf` / `public.svg-image`) for vector-aware apps **and** attach a
  300-dpi raster fallback so apps without vector paste still get the image; the
  SVG `text/plain` flavor is gone. (Exports are unchanged.)
- **Mirroring now matches in the LaTeX output.** A mirrored two-terminal
  component whose symbol has off-axis features (e.g. an LED's emission arrows)
  was reversed only *along* its axis in the generated CircuiTikZ, so those
  features ended up on the wrong side versus the canvas. Codegen now adds the
  `mirror` key so the perpendicular reflection matches the canvas Flip-X.
- **Mirroring a rotated two-terminal component no longer detaches it.**
  Mirroring a vertical (90°/270°) bipole — e.g. the boost converter's load
  resistor — moved its far terminal to the opposite side of its origin,
  detaching it from connected wires and rendering it in the wrong place in the
  LaTeX preview. The mirror is the canvas global Flip-X, which is applied *after*
  rotation, not before; `component_pin_positions`, `local_span_to_world`, and the
  code generator now all mirror after rotating, so a mirrored vertical bipole
  keeps its terminals on their grid cells and the preview matches the canvas at
  every rotation. (Off-axis symbol features still flip via the `mirror` key.)
- **Native dialogs, message boxes, and spin boxes.** Dropped the global
  form-control stylesheet that was cascading into the modal dialogs and message
  boxes (making them non-native); they — and spin/combo boxes — now use the
  platform style. The toolbars, palette, and Copy buttons stay themed.
- **Spin-box arrows restored** — the themed form styling had hidden the up/down
  arrows on number fields (Inputs, z-order, font size); spin boxes are native
  again. Reverted the modal dialogs (Preferences, Document Settings) to the
  native style. The component palette is a touch wider so cards aren't cut off,
  and the Copy PDF/SVG buttons show a pointer cursor and hover highlight.

### Changed
- **Logic gates now place at full size (100 %).** Newly placed gates default to
  100 % instead of 50 %, to match the scale of the new digital blocks. Existing
  schematics are unaffected (the saved size is preserved); you can still resize any
  gate with the inspector's **Size** dropdown.
- **Dotted canvas grid.** The background grid is now a subtle pattern of **dots**
  at the grid intersections instead of full ruled lines, so it orients the eye
  without competing with the schematic. The fine 0.25-GU dots appear only when
  zoomed in far enough to read.
- **"In use in document" is pinned to the bottom of the palette.** The placed-kinds
  section now sits in a fixed panel at the bottom of the palette sidebar and
  **scrolls independently** of the categories above, so it's always reachable.
- **Refreshed component palette.** Category cards now show the **actual symbol**
  of a representative component (rendered from the component itself) instead of a
  generic icon that often didn't fit; the component previews are **larger** (3
  columns); and the keyboard badges are subtle (no boxes).
- **Reorganized the Logic palette.** The boolean gates split by symbol style into
  **Gates (Am)** and **Gates (Eu)**, and the digital blocks (flip-flops, mux/demux,
  ALU, adder) get their own **Logic** category — so gates and building blocks no
  longer share one crowded group. The power rails (VCC/VDD/VEE/VSS) and batteries
  also split out of **Sources** into a **Supplies** category (the actual sources,
  incl. the european ones, stay in Sources).
- **Copy to clipboard is PNG-only now.** Copy PDF and Copy SVG were dropped: the
  common paste targets (Word, PowerPoint, Google Docs) rasterize a pasted figure
  anyway, so the extra buttons were misleading. Copy PNG renders at the **PNG
  resolution** preference. Vector output stays available via **File ▸ Export**
  (PDF/EPS/SVG/PNG).
- **Modernized the source & preview panels.** The CircuiTikZ source and LaTeX
  preview now share a consistent **bordered card** look with padded titles, a
  hairline divider, and **aligned title-bar heights**. The single Copy PNG action
  sits **inline with the LaTeX Preview title** as a compact icon button.
- **Signed & notarized macOS releases.** The release workflow now signs the
  macOS `.app` with a Developer ID Application certificate (hardened runtime +
  entitlements), submits it to Apple's notary service, and staples the ticket, so
  downloaded builds open without a Gatekeeper warning. Signing runs only when the
  signing secrets are configured; runs without them still produce an unsigned
  build. See `packaging/entitlements.plist` and `.github/workflows/release.yml`.
- **Palette categories tidied.** Within every category the american-style
  components are grouped before the european-style ones instead of interleaving.
- **Harmonized, modern UI theme.** The toolbars and main-window buttons/inputs
  now share the component palette's flat, light look (white surfaces, hairline
  dividers, muted icons, one soft-blue accent) instead of the previous native
  gray/3-D chrome. The top toolbar and left tool ribbon are white with a hairline
  divider and a soft-blue active-tool state; the Copy PDF/SVG buttons and the
  palette/properties line edits are flat and rounded. (Modal dialogs and
  combo/spin boxes stay native.) Centralized in a new `app/ui/theme.py`
  design-token module.
- **Full-height palette.** The component palette now spans the whole window
  height on the left; the CircuiTikZ source and LaTeX preview panels moved into
  the region to its right (no longer running underneath the palette).
- **Redesigned component palette.** The left panel is now an icon-tile picker:
  a search box (focus with `Ctrl+/`), an **In use in document** section that
  tracks the kinds you've placed, a 2-column **Categories** card grid, and the
  active category's components below. Component tiles are icon-only with the name
  on hover (tooltip) to stay compact; searching shows a flat results grid across
  all categories.

### Added
- **Copy figure to clipboard.** File ▸ *Copy Figure as PNG* (Ctrl+Shift+C),
  *PDF*, and *SVG* put the compiled schematic on the clipboard — a raster QImage
  (PNG, via QtPdf), the compiled PDF (`application/pdf`), or vector
  `image/svg+xml` (+ text fallback) — for pasting straight into slides, docs, or
  chat. **Copy PNG / PDF / SVG buttons** also sit below the LaTeX preview. Same
  toolchain needs as the corresponding exports (`pdflatex`; `pdftocairo` for SVG).
- **European / cute component variants.** Added a European-style resistor
  (Resistors), European and "cute" inductors (Inductors), and the full set of
  **European/IEC logic gates** — AND/OR/NAND/NOR/XOR/XNOR (parametric, 2–16
  inputs) plus NOT/buffer (Logic). All use CircuiTikZ's style-independent shape
  keywords (`european resistor`, `european inductor`, `cute inductor`,
  `european … port`) so they render the same shape on the canvas and in output
  regardless of the global style — and sit alongside the american symbols.
- **European sources & variable resistors.** Added European voltage/current
  sources `eV`/`eI` and their controlled forms `ecV`/`ecI` (Sources), plus a
  European variable resistor `evR` and potentiometer `epot` (Resistors), using
  the `european …` shape keywords.
- **European resistive sensor & american potentiometer.** Added a European
  resistive sensor `ethermistor` (the european thermistor equivalent,
  `european resistive sensor`) to pair with the american `thermistor`, and an
  american potentiometer `pR` to pair with the European `epot` (both Resistors).
- **Document Settings (Edit ▸ Document Settings…).** Choose the CircuiTikZ
  **american/european** style for voltage (`v=`) and current (`i=`) labels
  per-document. Stored in the `.hv` file (new `config` object; format bumped to
  `0.2`, older files still open with american defaults) and emitted as a
  picture-scoped `\ctikzset`, so it applies to the preview and exported figure.
- **Configurable tool paths (Preferences → Tools).** Set explicit paths to
  `pdflatex`, `latex`, `dvisvgm`, and `pdftocairo` when they aren't on your
  `PATH` or you want a specific install (à la LaTeXiT). Each field has a
  **Browse…** picker and live status (found-on-PATH / will-use-this-path /
  not-found); blank means auto-detect. Resolution now goes through a single
  `app/preview/tools` resolver (configured path → PATH).
- **LaTeX-free on-canvas equation labels.** Typeset component/wire/annotation
  labels now render via a bundled, pure-Python engine (**ziamath**, ~3 MB, ships
  the STIX Two Math font) when a system LaTeX install isn't available — so
  drawing, typeset canvas labels, CircuiTikZ source, and `.tex` export all work
  with no LaTeX. When `latex`/`dvisvgm` are present, the higher-fidelity LaTeX
  engine is still used. (The PDF preview pane and PDF/EPS/SVG image exports still
  require `pdflatex`.)
- **Preference: "Force the built-in (ziamath) label renderer."** A debug aid to
  use ziamath even when LaTeX is installed; toggling it re-typesets existing
  labels immediately.
- **Auto-export TeX snippet on save.** A new **Auto-export TeX on save**
  preference writes the includable `<name>.tex` snippet next to the `.hv` file on
  every save, alongside the existing PDF/EPS/SVG auto-export options. Unlike the
  image formats it is generated directly (no `pdflatex`), so it works with no
  LaTeX install. Multiple image formats still share a single compile.
- **SVG export.** A new **File → Export to SVG…** writes a vector SVG of the
  schematic, and an **Auto-export SVG on save** preference writes `<name>.svg`
  next to the `.hv` file. SVG uses the same Poppler `pdftocairo` tool as EPS
  (via the `-svg` flag), so it adds **no new dependency** beyond what EPS export
  already needs.

### Changed
- **Component emission types collapsed to two.** A component's `emission` is now
  either `path` (CircuiTikZ `to[…]` syntax) or `node` (`node[…]` syntax),
  replacing the former three-way `two_terminal`/`node`/`multi_terminal`. A `node`
  element is a single-terminal node (grounds, supplies) or a multi-terminal node
  (op amps, transistors, gates) depending purely on whether its pins carry
  CircuiTikZ anchors — the distinction is derived from the data, not a separate
  type. This affects only `components/definitions.json` and the Component Editor;
  saved `.hv` files are unaffected (they never stored emission). The generated
  LaTeX output is byte-for-byte unchanged.

### Security
- **`-no-shell-escape` now also guards the on-canvas math-label renderer.** Math
  labels are typeset the instant a `.hv` file is opened (no preview or export
  gesture needed) and label text flows verbatim into LaTeX, so this is the most
  exposed compile path. It now passes `-no-shell-escape` like the
  preview/export pipeline already did, so a crafted label in an untrusted `.hv`
  file can never invoke `\write18` / external commands regardless of the local
  TeX installation's default. The offline component renderer was hardened the
  same way (and given a compile timeout). Covered by `tests/test_latex_security.py`.

### Added
- **Current/voltage direction modifiers on the canvas.** The CircuiTikZ `<`/`>`
  modifiers now render correctly on the canvas (they already worked in the
  exported figure): `i<=` reverses the current arrow and draws it on the entry
  lead (the other side of the component), `v<=` swaps the voltage polarity, and
  `i>=`/`v>=` are the forward defaults. Position (`^`/`_`) and direction (`<`/`>`)
  combine, e.g. `i_<=`.
- **Open annotation decorations.** The `open` annotation component now draws its
  decoration like CircuiTikZ's `to[open, …]`: a current arrow **centered on the
  line** (label above) for `i=`, and `±` voltage signs at the terminals (label
  centered) for `v=`. `i<=` flips the arrow in place; `v<=` swaps the polarity.
- **Current on a `short`** is now centered on the middle of the wire on the
  canvas (matching the LaTeX output) rather than sitting out on the exit lead —
  a `short` has no body in the middle for the arrow to clear.
- **Larger current arrowheads** on the canvas, to better match CircuiTikZ's
  prominent current-flow arrow (the voltage arrow is unchanged).
- **Update notifier.** On startup (default on) Heaviside makes a single
  read-only check of the GitHub Releases page and tells you if a newer version
  is available — it never downloads or installs anything itself, and sends no
  information about you. Includes a one-time disclosure, a **Skip This Version**
  option, a **Help ▸ Check for Updates** menu item, and a **Preferences ▸
  Updates** toggle to turn it off.
- **macOS app now ships as a `.dmg`.** The macOS download is a
  drag-to-Applications disk image (with branded background art) instead of a zip,
  the conventional install experience. When signing is configured the release
  workflow signs, notarizes, and staples the `.dmg`; it also sidesteps macOS App
  Translocation by encouraging installation into `/Applications`.
- **Third-Party Licenses in the About dialog.** Help ▸ About now lists the key
  third-party components and adds a **Third-Party Licenses…** button that opens
  the bundled `licenses/` folder, so attributions are discoverable from the GUI.
- **Getting Started guide** in the README — a short first-run walkthrough
  (place, wire, label, preview, save/export).

### Changed
- **Canvas line weight now matches the CircuiTikZ output.** Wire and component
  stroke widths (and the ±/arrow annotation strokes) were ~2.4× bolder than the
  compiled figure; they are now scaled to the CircuiTikZ thin-stroke weight so the
  on-canvas drawing reads the same as the exported PDF.
- **Bundled examples are now grouped into categories.** The **File → Open
  Example** menu mirrors sub-folders under `examples/` (Battery Models, Control
  Systems, Power Electronics) as category submenus. Adding a sub-folder of `.hv`
  files creates a new category automatically — no code changes needed.
- **Auto-export TeX, SVG, and PNG on save are now on by default** (PDF and EPS
  remain off). A fresh install keeps the `.tex`, `.svg`, and `.png` siblings of
  your schematic current on every save. SVG/PNG need pdflatex (SVG also Poppler);
  a missing tool fails that export non-fatally without blocking the save. Turn
  any of them off in Preferences ▸ Export.
- **Preferences are now organised into tabs** (Export, Appearance, Tools,
  Updates) so the dialog stays compact instead of stacking every group
  vertically.
- **More breathing room for on-canvas component labels.** The gap between a
  component body and its typeset label was increased so labels no longer crowd
  tall symbols like inductors (the boost example's `L` sat right on the coil).
  Canvas display only — the exported CircuiTikZ figure is unchanged.
- **Complete third-party font attribution.** `licenses/THIRD_PARTY_LICENSES.md`
  now attributes the bundled math fonts (STIX Two Math, DejaVu Sans, via
  ziamath/ziafont) and every icon font qtawesome ships (Font Awesome, Material
  Design Icons, Phosphor, Remix, Elusive, Codicon), with the SIL OFL 1.1 and
  Apache-2.0 license texts added to the `licenses/` folder.

### Fixed
- **Current (`i=`) labels no longer float away from their components on the
  canvas.** The current label now clears the arrowhead on the lead (where the `i=`
  arrow actually sits) instead of the component body, so it hugs the wire as in
  the compiled figure — most visible on `short` segments and the current-annotation
  component. Canvas display only; the exported figure was already correct.
- **No-LaTeX math labels now work in packaged builds.** The PyInstaller bundle
  now ships the ziamath/ziafont font data (STIX Two Math, DejaVu Sans). Without
  it, the pure-Python math-label fallback — the feature that renders typeset
  labels with no TeX installed — was broken in the frozen app even though it
  worked from source. The fallback also degrades to raw text instead of raising
  if its fonts are ever missing.
- **Clearer error for a corrupt install.** A missing or truncated bundled
  component library (`definitions.json`) now raises a clear "installation is
  missing or corrupt" message instead of an opaque `JSONDecodeError` at startup.
- The math-render thread pool now drains on app quit, avoiding a possible
  "QThreadPool destroyed while threads are still running" warning.

### Removed
- Dead `scripts/make_ico.py` (superseded by the cross-platform
  `scripts/make_icons.py`).

## [0.2.0] - 2026-06-07

### Changed
- **The Component Editor is shown only when its toolchain is present.** It is a
  developer tool that renders/measures CircuiTikZ symbols via `latex` + `dvisvgm`,
  which a packaged end-user build does not ship — so the **Tools** menu now appears
  only when that toolchain is on `PATH`, instead of offering a half-working tool.
- **Engineer-facing palette categories.** Replaced the broad CircuiTikZ
  "Bipoles/Tripoles/Nodes" groupings with categories an EE recognises: Resistors,
  Capacitors, Inductors, Diodes, Transistors, Amplifiers, Sources, Instruments,
  Grounds, Supplies, Misc (plus Annotations, Drawing). The category is independent
  of terminal count, so the 4-terminal MOSFETs sit naturally under Transistors.
  The palette's category order is now a preference — an unlisted category still
  shows (after the listed ones) rather than silently hiding its components.
- **Clearer MOSFET names** so the flavours are distinguishable: `nigfete`/`nigfetd`
  → "N-MOSFET (enh.)" / "N-MOSFET (depl.)", `pigfete`/`pigfetd` → the P versions,
  `nfet`/`pfet` → "N-MOSFET (4-terminal)" / "P-MOSFET (4-terminal)" (the body-diode
  ones), and `njfet`/`pjfet` stay "N-JFET"/"P-JFET". Also `eC` → "Electrolytic
  Capacitor" to distinguish it from `pC` "Polarized Capacitor".
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
- **Logic-gate labels now render in exported output.** A gate's label slot was
  emitted as the bipole `l=` quick key, which CircuiTikZ's logic-port shapes
  reject (pdflatex warned and dropped the label). It is now emitted as
  `label=above:{…}`, placed above the body to match the canvas.
- **Old schematics survive a CircuiTikZ-library re-generation.** A `.hv` file
  stores only a component's `kind` (never its geometry), so regenerating against a
  new CircuiTikZ release flows appearance/alignment changes into existing files
  automatically. A new `_KIND_ALIASES` map (`schematic/io.py`) migrates any
  *renamed* kind on load, so a future symbol rename won't break old files.

### Added
- **CPE (Constant Phase Element)** — the `cpe` two-terminal bipole, for impedance
  / EIS equivalent-circuit work, under the Capacitors category.
- **Logic gates with a live input count.** The American gate family — AND, OR,
  NAND, NOR, XOR, XNOR (each **2–16 inputs**), plus NOT and Buffer — under a new
  **Logic** palette category. The multi-input gates are *parametric*: the input
  count is a per-instance integer you set in the inspector (a spinbox, undoable),
  and the symbol resizes live while every input pin stays grid-aligned. Each
  input count sets the CircuiTikZ gate **`height`** (so inputs reach the constant
  0.5 GU pitch as the body grows *natively*) rather than a node `yscale` — which
  keeps the inverting gates' round inversion bubble round and the proportions
  sensible. Built on a new generic parametric-component mechanism: a kind declares
  a `param` block in `definitions.json`; the generator renders one geometry per
  value (keyed `kind:N`) with per-N height/scale/bbox; the runtime resolves an
  instance's pins, geometry, bbox, and codegen from its value, and each
  height-setting gate is emitted in its own local `{ \ctikzset{…/height=H} \draw
  … }` group so the height reverts. The count is stored in the `.hv` file
  (`Component.params`).
- **19 new CircuiTikZ components**, bulk-imported via `components/import_family.py`
  with no per-component code (the registry/codegen/canvas derive everything from
  the data): bipoles `vR`, `eC`, `pC`, `fuse`, `lamp`, `ammeter`, `voltmeter`,
  `ohmmeter`, `battery1`, `varcap`, `memristor`, `thermistor`, `photodiode`,
  `tline`, `jumper`; and transistors `nfet`, `pfet`, `njfet`, `pjfet` (terminals
  auto-discovered, grid-snapped, and alignment auto-derived). `nfet`/`pfet` carry
  a fourth **bulk** terminal and a `body_diode` variant (toggles the intrinsic
  body diode), like the IGFET family.
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
    (`app/components/library.py`): `registry.py` derives every CircuiTikZ-symbol
    `ComponentDef` (keeping the 6 bespoke kinds as literals); `circuitikz.py`
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

[0.2.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.2.0
[0.1.0]: https://github.com/whileman133/Heaviside/releases/tag/v0.1.0
