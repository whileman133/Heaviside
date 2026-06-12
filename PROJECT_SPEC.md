# Heaviside — Specification

**Version:** 0.4  
**Status:** Draft (alpha — interfaces, file format, and architecture may change)  
**Author:** Wes H.

**Related specifications:** focused per-feature specs live in [`spec/`](spec/)
and are indexed in [`spec/README.md`](spec/README.md). This document remains the
master; the feature specs expand on sections referenced inline (e.g. §5.5 →
[`spec/component-editor.md`](spec/component-editor.md)).

---

## Contents

- [0. Spec Maintenance (Mandatory)](#0-spec-maintenance-mandatory)
- [1. Purpose and Scope](#1-purpose-and-scope)
- [2. Glossary](#2-glossary)
- [3. Grid and Coordinate System](#3-grid-and-coordinate-system)
- [4. Data Model](#4-data-model)
- [5. Component Registry and Painting](#5-component-registry-and-painting)
- [6. Canvas Behavior](#6-canvas-behavior)
- [7. Code Generation](#7-code-generation)
- [8. Preview and Export](#8-preview-and-export)
- [9. File Format](#9-file-format)
- [10. UI Layout](#10-ui-layout)
- [11. Project Structure](#11-project-structure)
- [12. Package Management and Development Environment](#12-package-management-and-development-environment)
- [13. Test Specification and Acceptance Criteria](#13-test-specification-and-acceptance-criteria)
- [14. Out of Scope (v1)](#14-out-of-scope-v1)

---

## 0. Spec Maintenance (Mandatory)

**This specification is a living document and must stay in sync with the implementation at all times.**

Whenever a feature is **added, changed, or removed** — by a human or an AI agent — the same change set MUST also update this specification so that the spec always describes the software as it actually behaves. A change is not complete until the spec reflects it. Specifically:

- **Adding a feature:** document its behavior in the relevant section(s) (data model, canvas behavior, code generation, UI, etc.), add or update any affected invariants, commands, keyboard shortcuts, and acceptance criteria, and add corresponding test entries in Section 13.
- **Changing a feature:** edit every section that describes the old behavior so no stale description remains. Search the whole document for affected terms.
- **Removing a feature:** delete its description (do not leave orphaned references) and move it to Section 14 (Out of Scope) if it is deferred rather than abandoned.
- **Version bump:** increment the spec **Version** field for any substantive behavioral change, and note new behavior under the appropriate section. Keep the spec **Version** aligned with the project version carried in `pyproject.toml`, `heaviside.spec`, and `CHANGELOG.md` (see `docs/releasing.md`).
- **Changelog:** record user-facing additions, changes, fixes, and removals under the `## [Unreleased]` heading in `CHANGELOG.md` in the same change set.

AI agents working on this project are explicitly required to follow this rule on every task that touches behavior. If a requested change would make the code and spec disagree, update both in the same change; if that is not possible, flag the discrepancy rather than silently letting them diverge. (Process notes for implementing this project with an AI assistant live in [`docs/ai-development.md`](docs/ai-development.md), separate from this behavior specification.)

---

## 1. Purpose and Scope

This document specifies a graphical editor for creating publication-quality circuit diagrams that output valid CircuiTikZ LaTeX markup. The tool targets researchers and engineers who author documents in LaTeX or LyX and require schematics with typeset mathematical annotations (e.g., component labels containing equations).

### 1.1 Goals

- Provide a grid-disciplined, fixed-component-size canvas for schematic entry
- Emit clean, human-readable CircuiTikZ source as the primary output format
- Support lossless save/load via a JSON schematic format
- Support arbitrary CircuiTikZ options strings on components (e.g. labels, colors, styles)
- Provide a rendered PDF preview of the current schematic
- Support wire-to-wire connectivity with automatic junction (connection) dots
- Be extensible: adding a new component type requires adding one registry entry and one `ComponentItem` subclass

### 1.2 Non-Goals (v1)

- Netlist export or circuit simulation
- PCB layout or component footprints
- SPICE integration
- LyX/LaTeX editor plugin or inset integration
- Round-trip parsing of existing CircuiTikZ source into the visual canvas
- Arbitrary free-rotation (only 0°, 90°, 180°, 270°)
- Hierarchical or multi-sheet schematics
- Bus wiring

---

## 2. Glossary

| Term | Definition |
|------|------------|
| **Grid unit (GU)** | The fundamental spatial unit of the canvas. 1 GU = 1 CircuiTikZ coordinate unit. All coordinates, component positions, and wire endpoints are integer multiples of 0.25 GU (the minor grid, §3.1). |
| **Component** | A placed instance of a component type on the canvas (e.g., a specific resistor at a specific location). |
| **ComponentDef** | A static definition of a component type: its CircuiTikZ keyword, bounding box, pin locations, and label slots. Lives in the component registry. |
| **ComponentItem** | A `QGraphicsItem` subclass responsible for painting one component type on the canvas using `QPainter`. One subclass per component type. |
| **Pin** | A named connection point on a component, located at a fixed offset from the component origin (every pin offset is a multiple of 0.25 GU). Its absolute position is on the 0.25 GU grid whenever the component is. |
| **Wire** | A Manhattan-routed (horizontal + vertical segments only) polyline connecting pins, other wires, and/or open grid points. |
| **Junction** | A coordinate where wires/pins are electrically tied together such that a connection must be marked — defined by the degree rule in §6.4. Rendered as a solid dot on the canvas and emitted as `\node[circ]` in the output. |
| **Open endpoint** | A wire endpoint that does not coincide with any *connecting* component pin. Rendered as an open circle on the canvas and emitted as `\node[ocirc]` in the output. Interior wire vertices are never open endpoints. A voltage annotation (`open`) pin does not connect, so a wire ending on one is still open (see `NON_CONNECTING_KINDS`). |
| **Options string** | A raw CircuiTikZ option string stored on a component, passed verbatim into the `to[]` or `node[]` argument (e.g. `l=$R_1$, v=$V_s$, color=red`). Replaces the former per-slot label dict. |
| **Schematic** | The complete logical description of a circuit: a list of components, a list of wires, and metadata. |
| **Registry** | The global static dictionary mapping CircuiTikZ keywords to `ComponentDef` objects. |
| **Origin** | The canvas coordinate (0, 0), located at the top-left of the canvas grid. Y increases downward, consistent with Qt's coordinate system. |
| **GRID_PX** | The number of screen pixels per grid unit at 1:1 zoom. All `QPainter` drawing coordinates are expressed as multiples of this constant. |

---

## 3. Grid and Coordinate System

### 3.1 Grid Definition

- The canvas grid has spacing of **1.0 GU**.
- All component placements, wire vertices, and junctions snap to **0.25 GU** increments — the *minor grid* (`SNAP_GU = 0.25`). This lets components be centred a quarter-cell off the half-grid (e.g. the IGFET MOSFET, whose body does not sit on the half-grid) while every connected wire stays grid-valid in any direction; arrow keys nudge one minor cell (0.25 GU).
- **Off-grid pins (one exception).** A scaled logic gate's pins sit at the true scaled anchor (`base × scale`), generally off the 0.25 GU grid (§5.4). A wire endpoint may rest off-grid **only** when it coincides with such a pin (the magnet snaps it there, §6.4); the Manhattan corner routing into it may carry the pin's off-grid coordinate. Validation therefore allows an off-grid wire coordinate **only when it lines up with a component pin's off-grid coordinate** (the same off-grid x, or the same off-grid y); any other off-grid value is still an error. Interior wire geometry stays on the grid otherwise.
- The grid is rendered as a **dotted lattice** in the canvas background (a dot at each grid intersection rather than full ruled lines, so the grid orients the eye without competing with the schematic ink). The integer-GU intersections carry slightly larger/stronger dots; the 0.25 GU minor intersections get small faint dots that are drawn **only when zoomed in far enough** to read as distinct (skipped when the on-screen cell is too small, keeping zoomed-out views clean and cheap). Dots are stroked at a constant **device** size (a round-cap pen, drawn with world-matrix disabled) so they stay crisp and small at every zoom level instead of scaling with the view transform.
- Proximity radii for snapping/grabbing (`PIN_SNAP_GU` = 0.125, `VERTEX_HIT_GU` / `PIN_GRAB_GU` = 0.15) are kept below half the 0.25 spacing so a click is never ambiguous between adjacent nodes.

### 3.2 Coordinate Convention

- X increases to the right.
- Y increases downward (Qt native convention).
- Component position is defined as the location of its **origin pin** (the `in` or leftmost pin for two-terminal devices) in grid coordinates.
- All pin offsets in `ComponentDef` are relative to the component origin, before rotation is applied.

### 3.3 Rotation

- Rotation is restricted to **0°, 90°, 180°, 270°** (multiples of 90°).
- Rotation is applied about the component origin pin.
- Pin positions are rotated accordingly at placement time.
- CircuiTikZ direction keywords (`right`, `up`, `left`, `down`) are derived from the rotation angle at code generation time.

### 3.4 Zoom and Pan

- The canvas supports continuous zoom via scroll wheel and pinch gesture.
- Pan via middle-mouse drag or spacebar + left-mouse drag.
- A "fit to schematic" action (`Ctrl+0`) zooms to show all placed components with a fixed margin. It frames the union of the **visible** items' scene bounds, **not** `QGraphicsScene.itemsBoundingRect()` — the latter includes invisible/empty helper items pinned at the scene origin (hidden inline label editors, empty wire-label items), which would inflate the rect from `(0,0)` to a schematic placed far from the origin and zoom the view way out (regression `test_view_fit_ignores_origin_helper_items`). Opening a schematic from disk (**File → Open**) runs the same fit automatically so the loaded circuit is framed in the view (deferred one event-loop tick so the viewport has its final size before `fitInView` runs).
- Zoom does not affect grid unit size or snap behavior — those remain in schematic coordinates.

---

## 4. Data Model

All persistent state is represented by plain Python dataclasses. The UI layer holds no schematic state independently; it derives all display from the model.

### 4.1 `ComponentDef`

Defined once per component type in the registry. Never instantiated per placed component.

```python
@dataclass(frozen=True)
class PinDef:
    name: str                        # e.g. "in", "out", "plus", "minus"
    offset: tuple[float, float]      # (dx, dy) from component origin, in GU

@dataclass(frozen=True)
class ComponentDef:
    kind: str                        # CircuiTikZ keyword, e.g. "R", "C", "op amp"
    display_name: str                # Human-readable, e.g. "Resistor"
    category: str                    # palette group, e.g. "Resistors", "Diodes", "Transistors"
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) relative to origin, in GU
    pins: list[PinDef]
    label_slots: list[str]           # valid slot names for this kind, shown as UI hint
    tikz_keyword: str                # CircuiTikZ node/path keyword
    default_span: tuple[float, float]  # (dx, dy) from origin to terminal pin, in GU
    resizable: bool = False          # True → terminal pin drag handle shown at instance
    component_class: type = Component  # Component subclass to instantiate for placed instances
```

`component_class` defaults to `Component` — used for all CircuiTikZ symbols, including diodes and MOSFETs (whose `filled`/`body_diode` are generic `variants`, §5.4). It is overridden only for the bespoke drawing kinds that carry extra per-instance state: `TextNodeComponent` for `text_node`, `RectComponent` for `rect`, `CircleComponent` for `circle`, `BipoleComponent` for `bipole`. All of the last group extend the `DrawingComponent` base and compose capability mixins (`FontedComponent`, `StyledComponent`) for font and fill/border state respectively (`text_node` is font-only; `rect`, `circle`, and `bipole` carry both). The deserializer in `schematic/io.py` uses this pointer to construct the correct subclass without a type-discriminator field in the JSON.

### 4.2 `Component` hierarchy

One instance per placed component. `Component` is the base; subclasses add kind-specific fields.

```python
@dataclass
class Component:                        # circuit components (R, C, L, op amp, …)
    id: str                             # UUID, assigned at placement
    kind: str                           # Must exist as key in REGISTRY
    position: tuple[float, float]       # (x, y) of origin pin in schematic coordinates
    rotation: int                       # 0, 90, 180, or 270 degrees
    options: str                        # raw CircuiTikZ option string, e.g. "l=$R_1$, v=$V_s$"
    mirror: bool = False                # global horizontal mirror, applied after rotation (§7 Mirror)
    label_offset: tuple[float, float] | None = None  # legacy; persisted but no longer affects display (§5.8)
    span_override: tuple[float, float] | None = None  # custom span for resizable components
    variants: dict[str, bool] = field(default_factory=dict)  # active boolean variants (§5.4)
    line_width: float = 0.4             # UNIFIED stroke/outline width (pt) for every drawable kind —
                                        #   circuit-symbol stroke AND block-component (rect/circle/bipole)
                                        #   outline. One field on the base (a defaulted mixin field can't
                                        #   precede the required base fields); there is no border_width.
                                        #   Canvas scales by line_width/0.4; codegen emits `line width=<w>pt`
                                        #   (symbols via _line_width_opt, blocks via compose_style_options).
    scale: float = 1.0                  # LOGIC GATES ONLY (kinds whose CircuiTikZ keyword ends in " port").
                                        #   Uniform body multiplier about the `out` pin; pins sit at the true
                                        #   scaled anchor (base × scale, generally off-grid; no leads — §5.4).
                                        #   Gates default to 0.5 at placement; other kinds keep 1.0. Persisted
                                        #   only when ≠ 1.0; absent → 1.0 (back-compat).

# Diodes and MOSFETs are plain Components: their `filled` / `body_diode` toggles
# are generic `variants` entries (the kind declares them in definitions.json).

@dataclass
class DrawingComponent(Component):      # base for text_node, rect, bipole
    z_order: int = 0                    # layer order (negative = behind circuit elements)

# Capability mixins — standalone dataclasses, never instantiated alone.
# CRITICAL: concrete classes must list mixins BEFORE DrawingComponent, or
# dataclass reverse-MRO field ordering raises "non-default argument follows
# default argument" at import.
@dataclass
class FontedComponent:                  # mixed into text_node, rect, circle, and bipole
    font_size: float = 12.0             # points; emitted as \fontsize{N} in LaTeX
    font_bold: bool = False             # \bfseries
    font_italic: bool = False           # \itshape
    font_family: str = ""               # "" = default, "serif"/"sans"/"mono"

@dataclass
class StyledComponent:                  # mixed into rect, circle, and bipole
    fill_color: str = ""                # TikZ fill color, e.g. "yellow!20"; "" = transparent
    line_style: str = ""                # raw TikZ line-style tokens, e.g. "dashed"; "" = solid
    # outline WIDTH is the unified Component.line_width (shared with symbols) — no separate field

@dataclass
class TextNodeComponent(FontedComponent, DrawingComponent):
    pass

@dataclass
class RectComponent(FontedComponent, StyledComponent, DrawingComponent):  # span_override = (w,h)
    pass                                # options holds the centred text label

@dataclass
class CircleComponent(FontedComponent, StyledComponent, DrawingComponent):  # span_override = (w,h)
    pass                                # like rect; only N/S/E/W connect (a sibling, not a subclass)

@dataclass
class BipoleComponent(FontedComponent, StyledComponent, DrawingComponent):
    font_size: float = 7.0              # override: smaller box default
```

`label_offset` is a **legacy** field. Labels now auto-place on their conventional sides and are not draggable (§5.8), so `label_offset` no longer affects display. It is still round-tripped in the file format (a two-element JSON array; absent or `null` loads as `None`) and set by `MoveOptionsLabelCommand` for back-compat, but the canvas ignores it.

### 4.3 `Wire`

```python
@dataclass
class Wire:
    id: str                          # UUID
    points: list[tuple[float, float]]  # Ordered Manhattan path vertices, in schematic coords
    line_style: str = ""             # raw TikZ line-style tokens (e.g. "dashed"); "" = solid
    line_width: float = 0.4          # pt (TikZ default 0.4); drawn proportionally on canvas
    no_junction_dots: bool = False   # exclude this wire from junction-dot placement (§6.4)
    no_termination_dots: bool = False  # suppress open-circle terminals at this wire's free ends (§6.4)
    hop_mode: str = ""               # per-wire line-hop override: ""/"never"/"always" (§6.4)
    start_marker: str = ""           # custom decoration at points[0]; "" = none (see WIRE_MARKER_KINDS)
    end_marker: str = ""             # custom decoration at points[-1]; "" = none (see WIRE_MARKER_KINDS)
    start_label: str = ""            # text/math label just beyond points[0]; "" = none
    end_label: str = ""              # text/math label just beyond points[-1]; "" = none
    start_label_placement: str = ""  # "" = off-end, "above", "below" (start label)
    end_label_placement: str = ""    # "" = off-end, "above", "below" (end label)
    mid_label: str = ""              # text/math label drawn over the wire (solid bg); "" = none
    mid_label_pos: float = 0.5       # mid_label position as a fraction of arc-length (0..1)
    z_order: int = 0                 # layer (front/back) + hop priority at a crossing (§6.4)
    # All vertices lie on 0.25 GU boundaries
    # All consecutive segment pairs are strictly horizontal or vertical
    # The point list is kept minimal: no consecutive duplicates and no
    # redundant collinear interior vertices (see §6.4 "Wire Simplification")
```

`no_junction_dots` flags a wire as an *annotation* rather than a real electrical connection: when set, the wire is skipped entirely by `junction_points()` (§6.4), so the solid `circ` dots it would otherwise create (where it meets other wires/pins) are suppressed — other wires/pins at the same coordinate still count, so a dot they independently justify is unaffected. Useful e.g. for leads into a voltage annotation. It does not change connectivity for code generation otherwise.

`no_termination_dots` likewise excludes the wire from `open_endpoints()` (§6.4), suppressing the `ocirc` open-circle markers at the wire's own dangling ends. It still counts toward *other* wires' connection detection (an endpoint of another wire landing on it stays connected), so only this wire's free ends lose their terminals. The **drag-time** open-circle preview (`DragController.update_ocirc_preview`) applies the *same* opt-outs as `open_endpoints()` — `no_termination_dots` and a custom end marker both suppress the auto terminal — so dragging any component/wire never resurrects a suppressed terminal.

`hop_mode` is a per-wire override of line-hop behaviour (§6.4 "Line-hops"), one of `WIRE_HOP_MODES = ("", "never", "always")`: `""` (**default**) follows the global line-hops preference (§10.8) and the `z_order` priority; `"never"` means this wire never draws a hop bump but **may still be hopped over** by a crossing wire; `"always"` means it always hops at its crossings, overriding both the global preference and `z_order`. Independent of the dot-suppression flags; connectivity is unaffected. Edited from the inspector as a tri-state checkbox (§10.3).

**Custom endpoint markers.** `start_marker` (at `points[0]`) and `end_marker` (at `points[-1]`) place a *user-chosen* decoration at a wire end — distinct from the topology-derived `circ`/`ocirc` dots above. The valid kinds are listed in `WIRE_MARKER_KINDS`: `""` (none), `"arrow"` (filled `Latex` tip), `"stealth"` (sharp filled `Stealth` tip), `"open"` (outlined `Latex[open]` tip), and `"bar"` (a perpendicular `Bar` terminal). All exist primarily to draw **block diagrams**, and each end chooses independently. A marker is the user's explicit choice, so an end bearing one is **excluded from `open_endpoints()`** — the marker replaces the automatic open-circle terminal at that specific end (the other end is unaffected, and the marked end still counts as a connection for other wires). Markers do not interact with `junction_points()`. The arrow tips come from TikZ's `arrows.meta` library, which the export pipeline loads (§8.4).

**Endpoint labels.** `start_label` (beyond `points[0]`) and `end_label` (beyond `points[-1]`) place a text/math caption at a wire end — e.g. an arrow marker terminating *into* `$y(t)$`. Each is a raw LaTeX fragment (same convention as a text annotation's content): `$…$` typesets as math, plain text renders verbatim. **Label placement.** Each endpoint label has an independent placement — `start_label_placement` / `end_label_placement` ∈ `WIRE_LABEL_PLACEMENTS` = `("", "above", "below")` — controlling where it sits relative to the endpoint:
- `""` (default) — *off the end*: on the far side of the endpoint along the terminal segment, with a small gap (`_WIRE_LABEL_GAP` ≈ 0.1 GU) clearing the wire end / arrow tip. The anchor is derived from the terminal segment's outward direction *in emitted (post-Y-flip) space*, so it stays on the correct side under the preview flip (horizontal segment → `west`/`east`; vertical → `south`/`north`).
- `"above"` / `"below"` — tuck the label **beside the wire at the endpoint**, extending *inward* (back along the terminal segment) so its outward edge sits one gap inside the endpoint and it **never crosses the endpoint** into a connected rect/circle; it is also offset one gap to the side so it **never overlaps the wire**. The side depends on the terminal segment's orientation: a **horizontal** segment reads literally — `"above"` = above the wire, `"below"` = below; a **vertical** segment reads as **left**/**right** — `"above"` = left of the wire, `"below"` = right. Emitted as a *corner* anchor (`south east`/`south west`/`north east`/`north west`) placed one gap inward-and-aside of the endpoint, so the box grows away from both the endpoint and the wire.

`"above"`/`"below"` exist so a label on a wire meeting a **block-diagram shape** (rect/circle) — where the off-end direction points *into* the shape — sits cleanly beside the wire without overlapping the shape or the line. Labels are orthogonal to markers and to the automatic dots — they do **not** suppress an `ocirc` (a labelled open terminal is allowed; the arrow marker, if present, is what suppresses it). In the LaTeX output each non-empty label is a `\node[anchor=…, inner sep=0] at (x,y) {…};` whose anchor is set per the placement above. `inner sep=0` strips the node's default ~3.3 pt padding so the visible gap equals the 0.1 GU offset and matches the canvas (whose label clearance has no padding). On the canvas the label is typeset math (via the shared async `render_async` path, §8.4) positioned just beyond the endpoint. **Double-clicking a rendered label** opens an in-place editor — the shared `LabelTextItem` (`QGraphicsTextItem`) pre-filled with the raw LaTeX fragment, positioned at the label; **Enter** or focus-loss commits via `set_wire_start_label`/`set_wire_end_label`, **Escape** cancels, mirroring component-label editing (§5.8). The display label is hidden while editing and restored when editing ends (commit *or* cancel, via `LabelTextItem`'s end-callback). A label can also be **started from a bare endpoint**: double-clicking *any* wire endpoint (no label yet) — free **or connected to a component pin / drawing element** — opens the same editor for that end (§6.4), so no inspector trip is needed to add one.

**Layer / hop priority.** `z_order` layers the wire exactly like `DrawingComponent.z_order` (positive = front, negative = behind, 0 = default) and additionally decides **which wire hops** where two wires cross without connecting: the higher-`z_order` wire arcs over the other (ties broken by position in the wire list). See §6.4 "Line-hops". On the canvas it maps to the wire item's `setZValue`; in the LaTeX output a `z_order < 0` wire is emitted before the main `\draw` block (behind) and a `z_order > 0` wire after it (in front), interleaved with `DrawingComponent`s by z-order; `z_order == 0` wires stay in the shared `\draw` path (§7.6).

**Mid-wire label.** `mid_label` is a text/math caption drawn **over** the wire — centred on the wire at the fractional arc-length position `mid_label_pos` ∈ [0, 1] (`wire_point_at_fraction`), with an **opaque (white) backdrop** so the line does not run through the text. Same LaTeX-fragment convention as the endpoint labels. Use for captioning a signal/bus mid-run. It is **draggable along the wire** on the canvas: pressing the rendered label and dragging projects the cursor onto the polyline (`wire_fraction_at_point`) and, on release, commits the new fractional position via `set_wire_mid_label_pos` (`SetWireMidLabelPosCommand`); the position is a fraction of arc-length, so it survives reshaping the wire. Double-clicking the label opens the same in-place editor (`begin_label_edit("mid")`). A mid-label is added through the inspector's **Middle** field (it appears at the midpoint), then dragged/edited on the canvas. In the LaTeX output it is a `\node[fill=white, inner sep=1pt] at (x,y) {…};` emitted after the wire draw so it paints on top.

`line_style` / `line_width` / `no_junction_dots` / `no_termination_dots` / `hop_mode` / `start_marker` / `end_marker` / `start_label` / `end_label` / `start_label_placement` / `end_label_placement` / `mid_label` / `mid_label_pos` / `z_order` are edited via the wire property inspector (§10.3) and the canvas, and are undoable (`SetWireLineStyleCommand` / `SetWireLineWidthCommand` / `SetWireNoJunctionDotsCommand` / `SetWireNoTerminationDotsCommand` / `SetWireHopModeCommand` / `SetWireStartMarkerCommand` / `SetWireEndMarkerCommand` / `SetWireStartLabelCommand` / `SetWireEndLabelCommand` / `SetWireStartLabelPlacementCommand` / `SetWireEndLabelPlacementCommand` / `SetWireMidLabelCommand` / `SetWireMidLabelPosCommand` / `SetWireZOrderCommand`). All are persisted only when non-default, so plain wires' JSON is unchanged; old files without them load as solid / 0.4 pt / no markers. On the canvas the pen width is proportional (`LINE_W × line_width/0.4`, so the 0.4 pt default renders at `LINE_W` — the CircuiTikZ-matched thin weight, ≈0.84 px at 1:1), and endpoint markers render at the wire ends as on-canvas approximations of their export tips (filled/concave/outlined triangles, or a bar). In the LaTeX output a wire that has a non-default style **or** an endpoint marker is emitted as its own `\draw[<spec>] (…) -- (…);` statement (default wires stay in the shared `\draw` path); the arrow spec (an `arrows.meta` form such as `-{Latex}`, `{Latex}-`, or `{Stealth}-{Latex}`) leads the option list, followed by any style options. See §8.

**Connectivity.** Wires connect to component pins, to other wires, and to bare
grid points purely by **coincident coordinates** — there is no explicit
endpoint-reference field. Two wires are electrically joined wherever they share
a vertex coordinate, and a wire is connected to a pin when one of its vertices
equals that pin's coordinate. Connection (junction) dots are derived from this
geometry, not stored (see §6.4 "Junctions and segment splitting" and §7.6).

### 4.4 `Schematic`

The root document object.

```python
@dataclass
class Schematic:
    version: str                     # File-format version (§9.4), e.g. "0.3"; normalised on save
    name: str                        # User-visible schematic name
    components: list[Component]
    wires: list[Wire]
    metadata: dict[str, Any]         # Arbitrary key-value store for future use
    voltage_style: str = "american"  # document voltage-label style: "american"/"european" (§7.2)
    current_style: str = "american"  # document current-label style: "american"/"european" (§7.2)
```

Document-level `voltage_style` / `current_style` (`LABEL_STYLES = ("american",
"european")`) select the CircuiTikZ arrow convention for `v=`/`i=` labels for the
whole figure; they are edited in the inspector's **Document** tab (§10.3/§10.9)
and saved in the `.hv` `config` object (§9.2). Codegen applies the european
convention as a **local** `voltage=european` / `current=european` option on each
component that carries a `v=` / `i=` annotation — **not** a global `\ctikzset`,
which also restyles some component *symbols*. Because Heaviside provides separate
american/european *symbols* as distinct components, the convention must only
affect the annotation arrows (§7.2).

### 4.5 Invariants

The following must hold at all times for a valid schematic:

1. All `Component.kind` values exist as keys in `REGISTRY`.
2. All `Component.rotation` values are in `{0, 90, 180, 270}`.
3. All `Wire.points` vertices lie on 0.25 GU boundaries (the minor grid, §3.1) — **except** an off-grid coordinate that lines up with a component pin's off-grid coordinate (a scaled logic gate's terminal: the endpoint resting on it, or a Manhattan corner collinear with it). Otherwise, because every pin offset is a multiple of 0.25 and components snap to the 0.25 grid, a wire endpoint that follows a moved pin stays on grid.
4. All consecutive wire segment pairs are strictly horizontal or vertical (Manhattan constraint).
5. No two components share the same `id`; no two wires share the same `id`.
6. Every wire has **at least two points** (`len(points) ≥ 2`) — enforced by
   `validate()` (and therefore rejected on load, §9.3). A degenerate 0/1-point
   wire draws nothing and breaks the canvas/codegen paths that assume
   `points[0]`/`points[-1]` exist.

Note: distinct wires **may** share vertex coordinates — that is how connections
and multi-wire junctions are formed (see §6.4). Sharing a coordinate is a valid
connection, not an id collision.

**Connectivity convention — `point_key`.** Coordinate-coincidence comparisons
go through the **single** connectivity convention `point_key(pt)`
(`app/schematic/model.py`): a 6-decimal-place rounding of both coordinates.
Every wire↔pin and wire↔wire coincidence test — junctions, open endpoints,
wire-following on move/resize/rotate, splits, merges, and the canvas's
decoration dict/set keys — compares through this key, so float noise from
off-grid pins (a scaled logic gate's terminals) can never silently detach a
wire. Stored geometry stays **unrounded**; only comparisons use the key.

---

## 5. Component Registry and Painting

### 5.1 Registry Structure

The registry is a module-level dictionary:

```python
REGISTRY: dict[str, ComponentDef] = { ... }
```

It is populated at import time from static definitions. No runtime mutation.

### 5.2 ComponentItem and Painting

Each component type has a corresponding `QGraphicsItem` subclass that implements `paint()` using `QPainter`. All drawing coordinates are expressed in pixels, derived from a shared set of constants defined in `app/canvas/style.py`:

```python
GRID_PX  = 60     # pixels per grid unit at 1:1 zoom
LINE_W   ≈ 0.84   # stroke width for bodies/wires — matched to the CircuiTikZ
                  #   thin stroke: GRID_PX * 0.3985pt / SVG_PT_PER_GU
PIN_R    = 3.0    # radius of pin indicator dot
LEAD_LEN = 15     # length of lead-in/lead-out wire stubs, in pixels
```

All `ComponentItem` subclasses import these constants, ensuring consistent proportions across all component types regardless of zoom level (Qt's `QGraphicsView` scales the painter automatically).

#### Symbol Source — SVG Reference Files

**All component symbols must be derived from CircuiTikZ SVG exports.** Hand-drawn symbols are prohibited — they will inevitably diverge from what CircuiTikZ actually renders and produce previews that don't match the canvas.

Symbols come from **CircuiTikZ renders** produced by the single deterministic Python pipeline `components/generate_components.py`. It renders each component (and variant) with `latex` + `dvisvgm` (`[american]` option), in a fixed bounding box with origin-at-zero / lead-to-grid placement, and writes two files: the **self-contained** `components/geometry.json` (symbol geometry, read by `app/canvas/svgsym.py`) and `components/definitions.json` (registry/codegen data + the single `origin_svg` placement constant; see [`spec/component-editor.md`](spec/component-editor.md)). **The application reads only those two files at run time.** Output is byte-stable (dvisvgm writes no timestamp), so re-running on the same toolchain reproduces identical files.

Each component declares an **`emission`** type in `definitions.json` that selects which CircuiTikZ syntax renders it (and later generates — §5.6); this is independent of its palette `category`. There are exactly two emission types, named for the syntax they produce:

| `emission` | LaTeX syntax | Examples |
|------------|----------------|---------|
| `path` | `\draw (0,0) to[kind] (2,0);` | R, C, L, D |
| `node` | `\draw (0,0) node[kind] {};` (single point), or `\node[kind, anchor=…] (X) at (0,0) {}; <leads>` (multi-terminal) | ground, vcc, … (single point); op amp, nigfete, npn (multi-terminal) |

A **`node`** element comes in two flavours, distinguished by the *data* rather than a third emission type: a **single-point** node (grounds, power rails — pins carry no CircuiTikZ anchor) placed at one coordinate, and a **multi-terminal** node (op amps, transistors, logic gates — at least one pin maps to a CircuiTikZ anchor, or an `anchor_pin` is set) placed by one anchor with computed lead routing extending each other terminal to a grid-aligned coordinate (`library.is_multi_terminal_entry`; §4 of [`spec/component-editor.md`](spec/component-editor.md)). The component set is exactly what the registry uses (every entry in `definitions.json`, plus the 6 bespoke kinds).

**Geometry schema.** Each entry is keyed by component name and holds `kind`, `name`, `viewBox`, `width_pt`/`height_pt`, and two geometry lists (both in SVG point coordinates):

- **`paths`** — the stroked/filled body geometry: `{d, stroke_width, fill, stroke}`.
- **`glyphs`** — text marks (the `+`/`−` of sources, a flip-flop's D/Q/CLK). dvisvgm emits these as `<use>` references into `<defs>`; the pipeline **resolves them at build time** into `{d, matrix, stroke_width}`, where `matrix` is the composed affine (enclosing-group matrix ∘ `<use>` translation). This is why the geometry is self-contained — no `<use>`/`<defs>` indirection survives, so the app needs no SVG access. A TeX **rule** (e.g. the overline of `\ctikztextnot{Q}` — the flip-flop's Q̄) is emitted as a `<rect>`; `render.parse_geometry` captures it as a glyph too (a closed rectangle path + the rect's transform), so the bar paints on the canvas rather than being dropped (regression: `test_parse_geometry_captures_rect_as_glyph`). `svgsym.symbol_paths` paints each glyph as a filled body via `QTransform(*matrix)` then the component transform.

**Diode body scale.** CircuiTikZ's default diode body is visually large next to the other bipoles, so every diode-family symbol (`D`/`zD`/`sD`/`tD`/`zzD`/`leD` and their filled `*` variants) is rendered with `\ctikzset{diodes/scale=0.8}` and the code generator emits the **same** picture-scoped `\ctikzset{diodes/scale=0.8}` for any schematic containing a diode (see §7.2). `DIODE_SYMBOL_SCALE` in `app/codegen/circuitikz.py` and `DIODE_SCALE` in the export script are the two sources of truth and **must match**. The scale shrinks only the body (the 2-GU span and pin positions are unchanged — leads auto-extend), and it does not affect the MOSFET body-diode (a tripole shape), so the canvas and the rendered output stay in sync.

To add a new component: add an entry to `components/definitions.json` (measure its pins with `app/components/render.py`) and re-run `components/generate_components.py` (see [`spec/component-editor.md`](spec/component-editor.md)). That is the whole procedure for a plain symbol — the registry, codegen tables, and canvas all derive from the data; the canvas item falls back to the generic `ComponentItem`, and `_DISPLAY_ORDER` is only a preference so the palette shows it automatically. No `ITEM_CLASSES` entry, `_DISPLAY_ORDER` edit, or `svgsym.py` anchor is required (placement is the single `origin_svg` constant). Only a component needing special item behaviour (custom `boundingRect`/hit-testing/resize) also needs a `ComponentItem` subclass + `ITEM_CLASSES` row.

To implement a new `ComponentItem`, look up the component in `geometry.json`, read the `paths` (and `glyphs`) arrays, and translate each path `d` string into `QPainterPath` calls:

| SVG command | `QPainterPath` equivalent |
|-------------|--------------------------|
| `M x y` | `path.moveTo(x, y)` |
| `L x y` | `path.lineTo(x, y)` |
| `H x` | `path.lineTo(x, path.currentPosition().y())` |
| `V y` | `path.lineTo(path.currentPosition().x(), y)` |
| `C x1 y1 x2 y2 x y` | `path.cubicTo(x1, y1, x2, y2, x, y)` |
| `Z` | `path.closeSubpath()` |

Coordinates are scaled from SVG pt units to `GRID_PX` by dividing by the SVG `viewBox` height and multiplying by the component's height in pixels. The SVG y-axis matches Qt's (y-down), so no axis flip is required.

**Fill rule.** dvisvgm emits a solid body (the filled diode triangle, transistor/LED arrowheads, …) as a **bare** `<path>` — no `stroke` and no `fill` attribute — which in SVG is the default **black fill**; the body's outline is a separate `fill='none'` stroke path. The export pipeline records a bare path's fill as `#000` (the SVG default), so `svgsym.symbol_paths` treats a path as **filled** unless its fill is the explicit `none`. (An earlier version recorded the absent attribute as `none`, which made `D` and `D*` — and every other solid body — render identically; the regression test is `test_filled_diode_body_is_filled`.) `stroke_width` values in the geometry are relative — thin strokes (≈0.4pt) map to `LINE_W`; thick strokes (≈0.8pt) map to `LINE_W * 2`. The `glyphs` list is always filled.

The mapping from `kind` to item class is:

```python
ITEM_CLASSES: dict[str, type[ComponentItem]] = {
    "R":       ResistorItem,
    "C":       CapacitorItem,
    "L":       InductorItem,
    "D":       DiodeItem,
    "op amp":  OpAmpItem,
    "nigfete": NigfeteItem,
    "V":       VoltageSourceItem,
    "I":       CurrentSourceItem,
    "vsource": AcVoltageSourceItem,
    "isource": AcCurrentSourceItem,
    "cV":      VcvsItem,
    "cI":      VccsItem,
}
```

Each `ComponentItem` subclass:

- Implements `boundingRect()` based on `ComponentDef.bbox` scaled by `GRID_PX`.
- Implements `paint()` drawing the component symbol as `QPainterPath` geometry translated from the SVG reference.
- Renders pin indicator dots at all `PinDef` offsets.
- Adjusts pen color and style based on item state: normal, selected (highlight color), hover, and ghost (semi-transparent, used during placement).
- Renders `Component.options` as **typeset math**, not raw LaTeX. The options string is parsed into annotation slots (`l`/`v`/`i`/`a` families) and each slot's value is rendered to a vector `QPainterPath` and drawn on its conventional side of the body (see §5.8). Child items are never clipped by the parent's bounding rect.

The on-canvas label system (§5.8) is:

- **Display** — one `_SlotLabel` child per non-empty annotation slot, placed above/below the body and counter-rotated to stay upright. Slot labels are non-interactive (display only). Each slot has a preferred clearance from the lead axis — currents (`i`) hug the wire, other slots clear the body's perpendicular thickness — but when several slots share a side (e.g. a label `l` and a current `i`, both defaulting above) they are **stacked outward** so they never overlap: a running outer edge per side pushes each successive slot at least one label-row height beyond the previous one, even when their preferred clearances differ.
- **Editing** — double-clicking a component (or any of its slot labels) activates a single child `LabelTextItem` (`QGraphicsTextItem`) that edits the options string. The editor is **centred over the component body** (regardless of where the slot labels sit) and shows the options **one slot per line** (`options_to_editable()` splits top-level commas to newlines; `editable_to_options()` joins them back with `, ` on commit). Commas inside `$...$`/`{...}` or escaped as a LaTeX control sequence (e.g. `\,`) are *not* split. **Enter** commits, **Shift+Enter** inserts a newline, **Escape** cancels. While editing, the slot labels are hidden and the editor shows a solid white rounded backdrop with a blue border; the whole component is raised to `_EDIT_Z` so nothing overlaps the editor. `TextNodeItem` edits its free text verbatim (no comma/newline conversion).
- **No dragging** — labels auto-place on their sides and are not draggable. `set_label_interactive()` is a no-op. (`Component.label_offset` and `MoveOptionsLabelCommand` remain in the model/command layer for file back-compat but no longer affect display.)
- **`text_node` and `bipole`** label text is rendered inline (centred) by the item's own `paint()` using the same vector renderer, with raw-text fallback.

### 5.3 Palette Thumbnails

The component palette renders each component's thumbnail by instantiating its `ComponentItem` and painting it into a `QPixmap` at a fixed small scale. No separate thumbnail assets are needed.

### 5.4 v1 Component Set

#### Bipoles

**Passives** — two-terminal components with pins `in` (0,0) and `out` (2,0), default span (2,0), label slots `l`, `l_`, `v`, `v^`, `i`, `i_`:

| Kind | Display Name |
|------|-------------|
| `R` | Resistor |
| `C` | Capacitor |
| `L` | Inductor |

**Diodes** — all share pins `anode` (0,0) and `cathode` (2,0), default span (2,0), and label slots `l`, `l_`, `v`, `v^`, `i`, `i_`. Each declares a `filled` variant (§5.4): when `Component.variants["filled"]` is `True` the canvas uses the `*` SVG and the codegen emits `KIND*`.

| Kind | Display Name | Filled variant |
|------|-------------|----------------|
| `D` | Diode | `D*` |
| `zD` | Zener Diode | `zD*` |
| `sD` | Schottky Diode | `sD*` |
| `tD` | Tunnel Diode | `tD*` |
| `zzD` | TVS Diode | `zzD*` |
| `leD` | LED | `leD*` |

**Sources (Fixed)** — pins `+` (0,0) and `-` (0,2), default span (0,2):

| Kind | Display Name | Label Slots |
|------|-------------|-------------|
| `V` | Voltage Source | `l`, `l_`, `v`, `v^` |
| `I` | Current Source | `l`, `l_`, `i`, `i_` |
| `vsourcesin` | AC Voltage Source | `l`, `l_`, `v`, `v^` |
| `isourcesin` | AC Current Source | `l`, `l_`, `i`, `i_` |

**Sources (Dependent)** — pins `+` (0,0) and `-` (0,2), default span (0,2):

| Kind | Display Name | Label Slots |
|------|-------------|-------------|
| `cV` | VCVS | `l`, `l_`, `v`, `v^` |
| `cI` | VCCS | `l`, `l_`, `i`, `i_` |

The **Generic Bipole** (`bipole`) component appears in the **Misc** group — see §7.7.

Each component's `bbox` is **computed**, not hand-chosen: `components/generate_components.py` takes the extent of the rendered ink (paths + glyphs) unioned with the pin positions and rounds it outward to 0.05 GU (`renderer.compute_bbox`; see [`spec/component-editor.md`](spec/component-editor.md) §3). So every box is snug to the actual symbol — the resistor/inductor stay tight perpendicular to the leads (≈±0.25 around the zigzag/humps), the capacitor reaches its plates (±0.45), and the LED's box follows its emission arrows (asymmetric, y0=−0.6, y1=0.3). This drives label clearance (§5.8) and the hit/selection region (§5.4), so the box tracks the drawn symbol rather than a typed constant.

**Variants (per-instance boolean attributes).** A component's *kind* may declare boolean variants in `components/definitions.json` (`{name, token, mode}`); the active set is stored generically in `Component.variants` (a `{name: bool}` map). The Properties panel's `VariantSection` auto-generates one checkbox per declared variant, and toggling pushes an undoable `SetVariantCommand`. A `suffix`-mode variant appends a keyword suffix (diode `filled` → `KIND*` and the `*` SVG); an `option`-mode variant adds a node option (MOSFET `body_diode` → `bodydiode` and the `*_bodydiode` SVG, e.g. `node[nigfete, bodydiode, anchor=gate]`). This generalises the former hardcoded `filled`/`body_diode` fields. In the `.hv` file only active variants are stored (`"variants": {"filled": true}`); legacy `filled`/`body_diode` keys are still read for back-compat (§9).

#### Tripoles

**Amplifiers:**

| Kind | Display Name | Pins | Label Slots |
|------|-------------|------|-------------|
| `op amp` | Op-Amp | `+` (-1.5,0.5), `-` (-1.5,-0.5), `out` (1.5,0) | `l` |

**MOSFETs:**

| Kind | Display Name | Pins | Label Slots |
|------|-------------|------|-------------|
| `nigfete` | NMOS | `gate` (0,0), `drain` (1.0,-1.0), `source` (1.0,0.5) | `l` |
| `nigfetd` | NMOS (depletion) | `gate` (0,0), `drain` (1.0,-1.0), `source` (1.0,0.5) | `l` |
| `pigfete` | PMOS | `gate` (0,0), `source` (1.0,-0.5), `drain` (1.0,1.0) | `l` |
| `pigfetd` | PMOS (depletion) | `gate` (0,0), `source` (1.0,-0.5), `drain` (1.0,1.0) | `l` |

All four MOSFET variants share the same pin x-offset (≈0.98 GU from gate, bridged to the 1.0 GU registry pin with a lead wire). N-channel symbols have `drain` at the top (`-1.0` Qt y) and `source` at the bottom (`+0.5` Qt y); P-channel symbols are mirrored with `source` at top (`-0.5` Qt y) and `drain` at bottom (`+1.0` Qt y). Enhancement mode = three channel dashes; depletion mode = solid channel line.

**BJTs:**

| Kind | Display Name | Pins | Label Slots |
|------|-------------|------|-------------|
| `npn` | NPN BJT | `base` (0,0), `collector` (1.0,−1.0), `emitter` (1.0,1.0) | `l` |
| `pnp` | PNP BJT | `base` (0,0), `emitter` (1.0,−1.0), `collector` (1.0,1.0) | `l` |

Both BJTs are placed with `anchor=B` (base pin) at `Component.position` and **scaled** (`xscale=1.1905, yscale=1.2987`, computed from the measured anchors) so the collector/emitter land exactly on the (1.0, ±1.0) GU registry pins — the symbol is stretched onto the grid rather than bridged with a diagonal stub (see [`spec/component-editor.md`](spec/component-editor.md) §4; MOSFETs are scaled likewise, with one small residual lead for the source's sub-grid y). For NPN: collector at top-right (Qt y = −1.0), emitter at bottom-right (Qt y = +1.0). For PNP: emitter at top-right, collector at bottom-right.

#### Digital blocks (flip-flops, multiplexers, ALU)

Digital MIPS-datapath blocks in the **Logic** category, drawn with native CircuiTikZ shapes — `flipflop` (the `flipflop D/SR/JK/T` presets) and the configurable `muxdemux` (also exposed by the predefined `ALU`, `demux`, and `one bit adder` styles). Each is a **centre-placed** multi-terminal node (`anchor_pin` null, like the op amp). The native anchors are off the 0.25-GU grid (e.g. ±0.56, ±0.84, ±0.98), so generation applies a **best-effort grid alignment**: `renderer.best_alignment_scale` picks a single uniform scale that lands as many pins as possible on the grid (ties broken toward 1.0), bakes it into the rendered geometry, and stores it as the symbol's `scale`. Flip-flops align **fully** (one scale lands all four corners on the grid); a mux/demux aligns its data lines while the slanted **select** pins, and the **ALU** operands, can't be brought on-grid by a uniform scale and stay off-grid (a wire snaps onto them via the magnet, exactly as for a scaled logic gate — §5.6, §6.4; only those kinds are exempt from the on-grid-pin invariant). No lead bridges: pins sit at the scaled anchors. They carry **no `l` label slot** — the raw pgf shapes reject the bipole `l=` quick key and are self-labelled by their pin glyphs (D/Q/CLK, A/B, …).

#### Extended component library (build-out)

Beyond the families above, the library covers a broad set of additional CircuiTikZ symbols, authored through the same data-driven pipeline (`components/add_library.py` → `renderer.save_component`; every keyword is verified to compile against the installed CircuiTikZ — see `components/_probe.py`). They use the existing emission machinery, so no new model/canvas/codegen code is needed:

- **Two-terminal bipoles** (`emission: "path"`): more resistors/sensors (`varistor`, `photoresistor`, NTC/PTC `thermistor`), capacitors (`vC`, `feC`, `cC`, `sC`, `piezoelectric`), inductors (`vL`, `sL`), thyristors (`thyristor`/SCR, `triac`), sources (`dcvsource`, `dcisource`, `vsourcesquare`, `vsourcetri`, `vsourceN`), switches (`spst`, `cute open/closed switch`, `reed`, `toggle switch`), instruments (`oscope`, `rmeter`), transducers (`loudspeaker`, `mic`, `buzzer`), signal-processing/RF **Blocks** (`amp`, `adc`, `dac`, `lowpass`/`highpass`/`bandpass`/`allpass`, `phaseshifter`, `detector`, `vco`), and misc (`afuse`, `squid`, `bulb`).
  - **Thyristor/triac gate (an off-axis pin on a path device).** `thyristor` and `triac` are two-terminal `to[…]` devices that additionally carry a third **`gate`** pin. Unlike the two anode/cathode terminals (which lie on the device axis), the gate sticks out perpendicular to it, at the native CircuiTikZ `gate` anchor (offset ≈ `(1.7, −0.77)` GU — off the 0.25 grid, so magnet-connected). No codegen change is needed: the device is still emitted as an anonymous `to[…]` (the gate stub is part of the symbol), and a wire connects to the gate **by coordinate**, because `component_pin_positions` transforms the gate offset with the *same* rotate-then-mirror transform CircuiTikZ uses to place the drawn stub — verified to coincide at all eight rotation×mirror cases (`test_codegen.test_thyristor_gate_pin_coincides_with_circuitikz_anchor`). The gate is `pins[2]`, so the axial terminal stays `pins[1]` for the resize/`default_span` machinery; `thyristor`/`triac` join the off-grid-pin invariant exemption.
- **Anchor-pinned tripoles** (placed by one anchor + grid-aligning `scale`, like the BJTs): IGBTs (`nigbt`/`pigbt`), simplified MOS (`nmos`/`pmos`/`nmosd`/`pmosd`), and `isfet`. (This model needs a *per-axis* scale to land both off-axis pins on the grid, which is only safe when the pins are symmetric — a non-uniform scale shears the symbol's strokes anisotropically in the output, see the switches below.)
- **Centre-placed multi-terminal nodes** (best-effort *uniform* grid alignment, like the digital blocks): electron **Tubes** (`triode`, `diodetube`, `pentode`, `tetrode` — the multi-grid tubes expose the extra grid taps: `tetrode` adds a `screen` grid, `pentode` adds `screen` + `suppressor`), the fully-differential op-amp (`fd op amp`), the Schmitt triggers (`schmitt`/`invschmitt`), the `gyrator`, and the **SPDT/rotary switches** (`cute spdt up/down/mid`, `rotaryswitch`). These keep native off-grid anchors (magnet-connected) and join `mux`/`demux`/`ALU`/`adder` in the on-grid-pin invariant exemption (`tests/test_registry._OFFGRID_PIN_KINDS`). **A centre-placed node must scale *uniformly*** (`best_alignment_scale` returns a single factor): a non-uniform scale shears the drawn strokes (e.g. a slanted switch blade) in the LaTeX output, but the canvas re-strokes every path at a uniform width — desyncing canvas from output. The SPDT/rotary switches were originally authored anchor-pinned, whose forced-symmetric grid targets produced exactly that non-uniform shear; they are centre-placed for a uniform scale (`tests/test_registry.test_centre_placed_nodes_use_uniform_scale`).
- **Single-terminal node**: `antenna` (placed at its feed point, like a ground).

Not included (out of scope for the data-driven pipeline — they need parametric pin layout / new canvas items): the configurable IC packages (`dipchip`/`qfpchip`) and the multi-port RF/DSP block library (`mixer`, `circulator`, `oscillator`, …).

Like a logic gate, every digital block is **scalable** (`library.is_scalable`): the inspector **Size** dropdown (§10.3) sets `Component.scale`, which multiplies the baked alignment scale in the emitted `xscale`/`yscale` (and the canvas transform). At 100 % the pins are grid-aligned (best-effort); other multipliers may push them off-grid, where the magnet still connects.

| Kind | Display Name | CircuiTikZ shape | Pins |
|------|-------------|------------------|------|
| `flipflop D` | D Flip-Flop | `flipflop D` | `D`, `clk`, `Q`, `Qbar` |
| `flipflop SR` | SR Flip-Flop | `flipflop SR` | `S`, `R`, `Q`, `Qbar` |
| `flipflop JK` | JK Flip-Flop | `flipflop JK` | `J`, `clk`, `K`, `Q`, `Qbar` |
| `flipflop T` | T Flip-Flop | `flipflop T` | `T`, `clk`, `Q`, `Qbar` |
| `mux` | Multiplexer | `muxdemux` (parametric) | `in0…in{N-1}`, `out`, `sel0…sel{M-1}` |
| `demux` | Demultiplexer | `muxdemux` (parametric) | `in`, `out0…out{N-1}`, `sel0…sel{M-1}` |
| `ALU` | ALU | `ALU` (notched trapezoid) | `A`, `B`, `result`, `op0`, `op1`, `zero` |
| `adder` | Adder | `one bit adder` | `A`, `B`, `sum`, `cin` |

The fixed-geometry kinds (flip-flops, ALU, adder) are authored by [`components/add_digital.py`](components/add_digital.py) via `renderer.save_component` (measure anchors, render geometry, merge into both data files); re-run it after a CircuiTikZ upgrade.

**Parametric mux/demux (two parameters).** The `mux` and `demux` are **multi-parameter** kinds: a data-line count (`inputs`/`outputs`, 2–16) and a select-line count (`selects`, 1–4), each a spinbox in the inspector (§10.3). The configurable `muxdemux` trapezoid is rendered for **every value combination** (`renderer.render_muxdemux`, driven by the entry's `muxdemux` recipe + `params` list) — `Lh`/`Rh` track the data count (2× keeps the data-pin pitch constant), `w` tracks the select count. Because the trapezoid's anchors don't sit on the grid and shift per combo, each combo's pins are **measured** and baked into `n_data["<data>,<select>"]["pins"]` along with the concrete `muxdemux def` option codegen re-emits; geometry is keyed `mux:<data>:<select>`. This is the general multi-parameter mechanism (logic gates are the single-parameter case, §5.6); see §10.3 for the inspector and §4.2 for `Component.params`.

#### Transformers (quadpoles)

Two-winding transformers in the **Inductors** category, drawn with CircuiTikZ's **quadpole** shapes — `transformer` (air-core) and `transformer core` (iron-core), each in three coil styles: **american** (`transformer`), **cute** (`cute transformer`) and **european** (`european transformer`), and likewise for the cored versions (six kinds total). The coil shape is the CircuiTikZ `inductor=american`/`cute`/`european` choice; because the `european` value only takes effect as a *scoped* `\ctikzset` (a node option reaches `cute` but not the european rectangle), the cute/european entries carry a static `ctikzset` field (`library.node_ctikzset`) and codegen wraps the node in its own `{ \ctikzset{inductor=…} \draw …; }` group so the setting reverts — the same group mechanism a parametric gate's body-height uses (`circuitikz._node_group_lines`). The terminals/dots are identical across the styles. Like the op amp and the digital blocks, each is a **centre-placed** multi-terminal node (`anchor_pin` null) with the same best-effort grid alignment: the native winding terminals sit at ±1.05 GU, so a baked uniform scale (≈0.952) lands all four on the grid — primary `p1`/`p2` (left, CircuiTikZ anchors `A1`/`A2`) and secondary `s1`/`s2` (right, `B1`/`B2`). They are **not user-scalable** (no Size dropdown — they behave like the other passive symbols, keeping only the fixed baked alignment scale) and carry **no `l` label slot** (the raw quadpole rejects the bipole `l=` key) — caption a transformer with a nearby text annotation. **Rotation and mirroring** are emitted with **`transform shape`**: a CircuiTikZ quadpole flips its internal coils when its node is rotated by an odd 90° (crossing the terminal leads) or mirrored (the coils face outward), whereas `transform shape` reorients the shape rigidly — matching the canvas — without changing the terminal anchors (so connected wires still meet them); codegen adds it only for `transformer*` kinds when the instance is rotated or mirrored.

**Polarity dots.** A transformer's winding-polarity dots are a per-instance choice: four independent **`dot` variants** (`dot_p1`/`dot_p2`/`dot_s1`/`dot_s2`) surface as checkboxes in the inspector (`VariantSection`, §10.3). A `dot` variant is unlike the `suffix`/`option` variants — it changes neither the node keyword nor the geometry key (`variant_tikz`/`variant_geometry_suffix` skip it); instead it is a separate mark: codegen emits `(node.<inner dot A1…>) node[circ]{}` for each checked dot, and the canvas paints a filled circle at the dot's measured `offset` (`library.dot_marks`). So the base (dot-less) symbol is rendered once and the chosen dots are overlaid. Authored by [`components/add_transformers.py`](components/add_transformers.py) (measure terminal + `inner dot` anchors → best-effort align → `renderer.save_component`); re-run after a CircuiTikZ upgrade.

#### Nodes (single-terminal)

Single-terminal components are placed as `\node[kind] at (x,y) {};` in the generated LaTeX. They have one pin (`in` at (0,0)) and `default_span=(0,0)`.

| Kind | Display Name | Canvas Symbol | Label Slots |
|------|-------------|--------------|-------------|
| `ground` | Ground | Three horizontal bars of decreasing width | — |
| `sground` | Signal Ground | Downward-pointing triangle | — |
| `cground` | Chassis Ground | Horizontal bar with three diagonal ticks | — |
| `rground` | Reference Ground | Single horizontal bar | — |
| `nground` | Noiseless Ground | Wider horizontal bars | — |
| `pground` | Protective Earth | Bar with diagonal ticks | — |
| `eground` | Earth Ground | Protective earth variant | — |
| `vcc` | VCC | Upward-pointing triangle with bar | `l` |
| `vdd` | VDD | Upward-pointing triangle | `l` |
| `vee` | VEE | Downward-pointing triangle with bar | `l` |
| `vss` | VSS | Downward-pointing triangle | `l` |

**Power rails** (`vcc`, `vdd`, `vee`, `vss`) support an optional `l=` label slot. The label is emitted as `label=right:{value}`, placing the voltage name to the right of the symbol bar — the conventional schematic position for power-rail net names.

#### Annotations

| Kind | Display Name | Pins | Default Span | Resizable | Label Slots |
|------|-------------|------|-------------|-----------|-------------|
| `open` | Voltage Annotation | `in` (0,0), `out` (2,0) | (2,0) | Yes | `v`, `v^`, `v_`, `i`, `i_` |

The `open` component renders a **translucent** (mostly-opaque) solid line between its two endpoints — drawn at `OPEN_ANNOTATION_OPACITY` (0.5) so it is visually distinct from both solid and dashed wires — with a voltage/current annotation from the options string. Unlike other components, its annotation **value label** is **centred over the middle of the line** rather than offset to a side, while its decoration — a current arrowhead centred on the line, or `±` voltage signs at the terminals — is drawn like CircuiTikZ's `to[open, …]` (see §5.8). When selected, square drag handles appear at both endpoints; dragging the terminal handle resizes the span (updating `Component.span_override`). The resize is undoable via `ResizeCommand`. Connected wires follow the moved endpoint.

#### Drawing Annotations

Drawing annotations are non-circuit visual elements that appear in the palette under the **Drawing** category. They have no *named* circuit pins. `text_node` does not participate in connectivity at all; `rect` and `circle` are special cases that support **block-diagram wiring** — wires connect to grid points on their boundary (the rect's full perimeter; the circle's four cardinal points) and follow when the shape is moved or resized, but those connection points are never junction-dot sites. Drawing annotations are emitted as standalone LaTeX commands around the main `\draw` block in the generated output.

| Kind | Display Name | Pins | Default Span | Resizable | Inspector Controls |
|------|-------------|------|-------------|-----------|-------------------|
| `text_node` | Text | none | (0,0) | No | Text content field, Font size spinbox (6–72 pt), Bold/Italic checkboxes, Font family combo, Z-order spinbox, Rotation buttons (0°/90°/180°/270°) |
| `rect` | Rectangle | edge grid points (any 0.25 GU point on the perimeter) | (1,1) | Yes (corner drag) | Text content field, Font size spinbox, Bold/Italic checkboxes, Font family combo, Line style combo, Border width spinbox (pt), Fill color combo, Move to front/back buttons, Z-order spinbox |
| `circle` | Circle | four cardinal points (N/S/E/W = bounding-box edge midpoints) | (0.5,0.5) | Yes (corner drag) | Text content field, Font size spinbox, Bold/Italic checkboxes, Font family combo, Line style combo, Border width spinbox (pt), Fill color combo, Move to front/back buttons, Z-order spinbox |

#### Bipole Component (`bipole`)

The `bipole` kind is a generic labelled rectangular box representing an arbitrary two-terminal subsystem (named "Generic Bipole" to the user; it sits in the **Misc** palette category). Wires connect to its left (`in`) and right (`out`) pins. Although two-terminal, it is **not** emitted via the CircuiTikZ `to[...]` path syntax — it is rendered as a standalone `\node` (see Code generation below), like the other `DrawingComponent` kinds.

| Kind | Display Name | Category | Pins | Default Span | Resizable |
|------|-------------|----------|------|-------------|-----------|
| `bipole` | Generic Bipole | Misc | `in` (0,0), `out` (1,0) | (1,0) | Yes (right endpoint drag) |

**Model:** `BipoleComponent(FontedComponent, StyledComponent, DrawingComponent)` — composes both capability mixins (gains `font_*` for the label and `fill_color`/`line_style` for the box, plus the unified `Component.line_width` for the outline) over the `DrawingComponent` base (`z_order`). `options` holds a CircuiTikZ-style option string; the `t=` slot sets the label inside the box. Other slots (`l=`, `v=`, `i=`) are stored in options but not rendered in the LaTeX output (they don't apply to a standalone TikZ node).

**Canvas rendering (`BipoleItem`):** Extends both `_DrawingAnnotationBase` (for z-order) and `_ResizableTwoTerminalItem` (for span/resize). Draws a rectangle of half-height `_BIPOLE_HALF_H` (0.25 GU) centered on the connecting line, from the origin pin to the terminal pin, using the `fill_color` and `line_style` from the StyledComponent fields and the unified `line_width` for the outline. The pen style is resolved from `line_style` via the shared `_resolve_pen_style()` helper (same mapping as `RectItem`), so dashed/dotted borders render on the canvas. The `t=` label is drawn centered inside the rectangle. Pin dots appear at both endpoints. A square resize handle at the terminal (right) endpoint is shown when selected. The hit region is the full rectangle interior plus the resize handle.

**Resizing:** Dragging the right endpoint handle changes `span_override`. The resize directly controls the box width in both the canvas preview and the LaTeX output. Committed via `ResizeCommand`.

**Properties inspector:** The capability sections that apply to a bipole are `BipoleLabelSection` (label `t=` + other options), `FontSection`, `FillBorderSection` (line style + fill), `StrokeWidthSection` (the unified outline width, shared with symbols), `TransformSection` (rotation + mirror), and `LayerSection` (front/back + z-order). See §10.3 for the section architecture. `line_style` is edited through the shared `FillBorderSection` (the same control rect uses), so bipoles support dashed/dotted borders.

**Inline label editing:** Double-clicking a `BipoleItem` activates an inline text editor centred inside the box showing only the `t=` label text (not the full options string). On commit the edited text is spliced back into `options` using `_replace_bipole_label`, preserving all other slots. The painted label is suppressed while the editor is active.

**Fill color, line style, outline width** — fill and line style are carried by the shared `StyledComponent` mixin (same fields as rect): `fill_color: str` (default `""`, TikZ color e.g. `"yellow!20"`, empty = transparent; palette None/White/Light gray/Yellow/Blue/Green/Red) and `line_style: str` (default `""` = solid, raw TikZ tokens e.g. `"dashed"`). The **outline width** is the unified `Component.line_width` (default `0.4` pt) — the same field circuit symbols use for their stroke, so there is no separate `border_width`. Each is saved in JSON only when non-default and rendered on canvas (`_resolve_tikz_color` for fill; pixel-equivalent width for the outline). Edited via per-field undoable commands (`SetFillColorCommand`, `SetComponentLineWidthCommand`, `SetLineStyleCommand`).

**Code generation:** Bipole is NOT in `_TWO_TERMINAL_KINDS`. It is handled in the same background/foreground drawing-annotation passes as `rect` and `text_node`, via `_bipole_node_line()`. Emits a standalone TikZ node whose dimensions are derived from `span_override` so the box exactly fills the pin-to-pin space (example with a 3 cm custom span):
```latex
\node[draw, minimum width=3cm, minimum height=0.5cm] at (1.5,0) {Processor};
% with fill and outline (line) width:
\node[draw, minimum width=3cm, minimum height=0.5cm, ..., fill=yellow!20, line width=1.5pt] at (1.5,0) {Processor};
% with rotation:
\node[draw, minimum width=3cm, minimum height=0.5cm, rotate=-90] at (0,1.5) {Processor};
% empty label:
\node[draw, minimum width=3cm, minimum height=0.5cm] at (1.5,0) {};
```
`minimum width` = `span_override` length in GU (= cm in CircuiTikZ's default coordinate system); `minimum height` = 0.5 cm (2 × `_BIPOLE_HALF_H_GU` = 2 × 0.25 GU, matching standard bipole height). The TikZ `rotate=` value is the negated canvas rotation (TikZ is CCW, canvas is CW). The style tokens (`line_style`, then `line width=`, then `fill=`) are composed by the shared `compose_style_options()` helper (`app/components/style.py`) and appended when non-default — the same helper used for `rect`. Wires whose endpoints coincide with the bipole's `in`/`out` pin coordinates connect naturally at the node's left/right edges.

**Text node (`text_node`):**  
`TextNodeComponent.position` is the `at` coordinate of the `\node`. `TextNodeComponent.options` is the text content (the `{…}` argument). The following fields on `TextNodeComponent` control text appearance:
- `font_size: float` (default `12.0`) — font size in points; emits `\fontsize{N}` in the `font=` option when it differs from the default.
- `font_bold: bool` (default `False`) — emits `\bfseries` in the `font=` option.
- `font_italic: bool` (default `False`) — emits `\itshape`.
- `font_family: str` (default `""`) — `"serif"` → `\rmfamily`, `"sans"` → `\sffamily`, `"mono"` → `\ttfamily`; empty = document default.

The canvas item draws the text centered at the position using a QFont with matching bold/italic/StyleHint settings so the preview closely matches the LaTeX output. When options is empty a dashed placeholder box with "Text" hint is shown. **Double-clicking** a text node activates inline editing: the drawn text is replaced by a `LabelTextItem` editor centred on the component body, styled with the same font; committing (Enter, Return, or focus-loss) fires the normal `edit_component_options` command; Escape cancels. The properties inspector shows a **Text content** field, **Font size (pt)** spinbox, **Bold** and **Italic** checkboxes, and a **Font family** combo (Default / Serif / Sans-serif / Monospace). Font size changes are undoable via `SetFontSizeCommand`; bold/italic/family changes are undoable via `SetTextStyleCommand`. Code generation emits:
```latex
\node[font=\fontsize{SIZE}{LEADING}\selectfont\bfseries\itshape\sffamily] at (x,y) {text};
% or, when no styling is applied:
\node at (x,y) {text};
```

**Rectangle (`rect`):**  
`RectComponent.position` is the first corner (top-left when span is positive). `RectComponent.span_override` (or `default_span` = (2,2) when not set) gives the offset `(dx, dy)` to the opposite corner. The draw style is carried by the shared `StyledComponent` fields — `line_style` (e.g. `dashed`, `dotted`, `dash dot`) and `fill_color` — plus the unified `Component.line_width` (pt) for the outline, and composed into the `\draw[…]` argument by `compose_style_options()` (the same helper used for `bipole`). The canvas item draws the rectangle with the selected style and shows a square drag handle at the far corner when selected (no circuit pin dots). Resizing via the corner handle is undoable via `ResizeCommand`; style edits via the per-field `SetFillColorCommand` / `SetComponentLineWidthCommand` / `SetLineStyleCommand`.

**Centred text (block-diagram label).** `RectComponent.options` holds a raw LaTeX text fragment (same convention as a `text_node`: `$…$` typesets as math, plain text renders verbatim) drawn **centred** (horizontally and vertically) inside the box. Appearance is controlled by the `FontedComponent` fields (`font_size` default 12 pt, `font_bold`, `font_italic`, `font_family`). On the canvas the fragment is typeset via the shared async `render_async` path (`RectItem._request_vector` → centred `_vec_path`, raw-text fallback until it renders), mirroring `TextNodeItem`/`BipoleItem`. **Double-clicking** a rect activates an in-place editor (the shared `LabelTextItem`) centred in the box, pre-filled with `options`; **Enter** or focus-loss commits via `edit_component_options` (`EditCommand`), **Escape** cancels — the painted text is suppressed while editing. The text is edited verbatim (no comma↔newline conversion). The **Text content** inspector field edits the same `options`.

**Edge wire connections.** Every 0.25 GU grid point on the rectangle's perimeter (`rect_perimeter_points`) is a wire-connection point — exposed through `component_connection_points` (which returns the perimeter for a rect, named pins otherwise). A wire endpoint landing on an edge is **connected** (no open-circle terminal; see §6.4) and **follows the rectangle** when it is moved (uniform translation) or **resized** (an anchored scale about the fixed corner: a connection point P maps to `position + (P − position)·(new_span/old_span)`, snapped to 0.25 GU, so each edge point stays on its corresponding edge). Move-follow reuses the shared `reshape_wire_points`; resize-follow is computed by `reshape.compute_box_resize_reshape` (applied by `ResizeCommand`, and ghosted from the same function by the live preview in `drag.py`). Rect edge points do **not** trigger junction dots, but **are** offered as wire-drawing snap targets (`nearest_connection_point`) and as **SELECT-mode wire auto-start** targets: clicking an *unconnected* edge point (tightly, within `PIN_GRAB_GU`) starts a new wire there, exactly like clicking a free component pin (`unconnected_pin_at` uses `nearest_connection_point`). The connection points are drawn as small **muted-red dots** around the perimeter (`RectItem._connection_dots_local`, `_CONN_DOT_COLOR`/`_CONN_DOT_R` — smaller and more translucent than a component pin) so the connection rail is visible without being obtrusive; clicking the rect *interior* (not on a dot) still selects/drags it. `RectComponent.options` holds the rect's centred text, loaded verbatim; its draw style lives in the dedicated `StyledComponent` fields (see §9.4).

New rects default to `z_order = -10` (behind circuit elements). TikZ color strings in `fill=` (e.g. `yellow!20`, `gray!15`) are resolved to Qt colors using the `color!percent` mixing formula (percent% of the named color blended with white) before rendering on the Qt canvas. The hit region for selection is the full rectangle interior (not just a band along the diagonal), so clicking anywhere inside the rect selects it.

The `LayerSection` of the inspector — shared by all `DrawingComponent` kinds (text_node, rect, bipole) — shows **Move to front** and **Move to back** buttons. Wires carry their own `z_order` too and have the **same buttons** in `WireStyleSection` (§10.3). Both kinds drive the shared scene methods `bring_to_front(obj_id)` / `send_to_back(obj_id)`, which operate over **one combined z-stack** of every z-ordered object (drawing components **and** wires — they share the canvas `setZValue` and the codegen background/foreground blocks): "Move to front" sets `z_order` to `max(all other z-ordered objects' z_orders, 0) + 1`; "Move to back" to `min(…, 0) - 1`. The `0` baseline guarantees front is `≥ 1` (in front of the plain circuit elements at z 0) and back is `≤ -1` (behind them). Each dispatches to `set_wire_z_order` or `set_component_z_order` by id, so the move is undoable (`SetWireZOrderCommand` / `SetZOrderCommand`) and updates the Z-order spinbox. Code generation emits:
```latex
\draw[dashed, line width=1.5pt, fill=yellow!20] (x1,y1) rectangle (x2,y2);
% solid with no extra options:
\draw (x1,y1) rectangle (x2,y2);
% with centred text (emitted as a separate node at the rect centre, after the
% rectangle; default font → no [font=...] bracket):
\draw (0,0) rectangle (4,2);
\node at (2,1) {$H(s)$};
% styled text reuses the text_node font option:
\node[font=\fontsize{10}{12}\selectfont\bfseries\sffamily] at (2,1) {$H(s)$};
```
A text-free rect emits only its `\draw … rectangle …` line (byte-identical to pre-0.4 output); the centred `\node` is added only when `options` is non-empty (`_centered_text_line`, which shares `_font_opts_bracket` with `_text_node_line`).

**Circle (`circle`):**  
`CircleComponent` behaves **exactly like `rect`** — same `StyledComponent` draw style, same `FontedComponent` centred text in `options` (inline edit, inspector Text content + Font sections), same `span_override` = (width, height) bounding box, same corner-drag resize, same default `z_order = -10` — with two differences:
1. **Shape.** `CircleItem` (a subclass of `RectItem` overriding only the outline paint and the hit `shape()`) draws an **ellipse inscribed in the bounding box** — a true circle when width == height, otherwise an ellipse. Code generation emits `\draw[style] (cx,cy) circle (r);` when square, else `\draw[style] (cx,cy) ellipse (rx and ry);` (centre = box centre; `rx = |dx|/2`, `ry = |dy|/2`), via `_circle_line`; a centred text `\node` follows (the shared `_centered_text_line`) when `options` is non-empty.
2. **Connection points.** Only the **four cardinal points** N/S/E/W — the bounding-box edge midpoints (`circle_connection_points`, surfaced through `component_connection_points`) — accept wires, versus the rect's full perimeter. They are drawn as the same muted-red connection dots (`CircleItem._connection_dots_local` returns just the four), are wire-drawing snap targets, and auto-start a wire when an unconnected one is clicked (like rect edges, above). They follow on move (uniform translation) and resize (the same anchored-scale mapping, which keeps each cardinal point on its edge: N/S on the vertical centre line, E/W on the horizontal centre line). `CircleComponent` is a **sibling** of `RectComponent` (not a subclass) so code generation and painting distinguish the two shapes.

**Z-order (`DrawingComponent.z_order`):** An integer field on `DrawingComponent` (default 0), stored in the JSON file (omitted when 0 for backward compat). Applies to `text_node`, `rect`, and `circle`. On the Qt canvas, maps to `QGraphicsItem.setZValue()`. In the LaTeX output, controls emission order:
- `z_order < 0` → emitted **before** the main `\draw` block (behind circuit elements in the PDF).
- `z_order ≥ 0` → emitted **after** the `\draw` block and junction/open-endpoint nodes (in front).

Changed via `SetZOrderCommand` (undoable) through `scene.set_component_z_order()`.

**SetFontSizeCommand:** An undoable command that sets `TextNodeComponent.font_size`.

**SetTextStyleCommand:** An undoable command that sets `font_bold`, `font_italic`, and `font_family` together on a `TextNodeComponent`. All three values are stored and restored atomically so a single undo reverts the entire style change.

**SetZOrderCommand:** An undoable command that sets `DrawingComponent.z_order`.

The palette category display order is: **Resistors → Capacitors → Inductors → Diodes → Transistors → Tubes → Amplifiers → Blocks → Gates (Am) → Gates (Eu) → Logic → Switches → Sources → Supplies → Instruments → Grounds → Transducers → Antennas → Misc → Annotations → Drawing** — engineer-facing groups rather than the CircuiTikZ bipole/tripole classification. (**Tubes** = electron tubes; **Blocks** = signal-processing / RF blocks — amplifier/ADC/DAC/filter/phase-shifter/detector/VCO/gyrator; **Transducers** = loudspeaker/microphone/buzzer; **Antennas** = antenna. These are ordinary registry categories, set on each entry, so they need no special handling in `_palette_category`.) The palette refines the raw registry `category` for display (`palette._palette_category`): the raw **Logic** category splits three ways — the boolean **gates** split by symbol style into **Gates (Am)** / **Gates (Eu)**, while the logic **blocks** (flip-flops, mux/demux, ALU, adder — identified by their `flipflop`/`muxdemux`/`ALU`/`one bit adder` shape keyword, `palette._is_logic_block`) form their own **Logic** category. The power-supply rails + batteries (`_SUPPLY_KINDS`: `vcc`/`vdd`/`vee`/`vss`/`battery`/`battery1`) split out of the raw **Sources** category into a palette-only **Supplies** group (the actual sources, including the european ones, stay in Sources). This is a UI-grouping concern; the model `ComponentDef.category` is unchanged. The order is a *preference*: a component whose category is not listed still appears (after the listed groups), so a new category never silently hides its components. **Within** a category, the explicit display order (`registry._DISPLAY_ORDER`, via `registry.display_rank`) wins — so the **Inductors** group reads inductors → transformers → choke (all six transformer styles grouped, even though the european inductor `eL` then precedes the american transformers). For kinds **not** in that order, the palette falls back to grouping american-style first and european-style after (`palette._is_european_style`, derived from the `european …` CircuiTikZ keyword), so the two conventions don't interleave (`palette._within_category_key`).

**American vs. european shapes.** Both conventions are offered as *distinct
components* sitting side by side in their category, rather than a global toggle:
the american resistor `R` and **european resistor** `eR`, the variable resistor
`vR`/`evR`, the potentiometer `pR`/`epot`, and the resistive sensor
`thermistor`/`ethermistor` (Resistors); the
american inductor `L` with the **european** `eL` and **cute** `cuteL` inductors
(Inductors); and the american gates (`and`, `or`, `nand`, `nor`,
`xor`, `xnor`, `not`, `buffer`) in **Gates (Am)** alongside their european/IEC
counterparts (`eand`, `eor`, `enand`, `enor`, `exor`, `exnor`, `enot`, `ebuffer`)
in **Gates (Eu)**; and the
european **sources** `eV`/`eI` and controlled `ecV`/`ecI` (Sources). Each
european kind uses CircuiTikZ's *style-independent* shape keyword (`european
resistor`, `european inductor`, `cute inductor`, `european and port`, `european
voltage source`, `variable european resistor`, …), so its
shape is fixed in both the canvas and the output regardless of any global
`resistor`/`inductor`/`logic ports` style. The european AND/OR-family gates are
**parametric** (2–16 inputs) exactly like the american ones, with their own
measured per-N alignment and the `tripoles/european … port/height` body-height
key (§5.5); `enot`/`ebuffer` are fixed single-input. (This is orthogonal to the
document **voltage/current label** styles of §7.2, which are a per-document
setting.)

**Logic-gate size (`Component.scale`).** Logic gates (every kind whose CircuiTikZ
keyword ends in `` port``) carry a per-instance uniform size multiplier,
`Component.scale`, edited from the inspector's **Size** dropdown
(`library.gate_scale_options`: 25 %–200 %, floored at 25 %) and **defaulting to
1.0** at placement (`library.default_scale` / `DEFAULT_GATE_SCALE`) — full size, to
match the digital blocks (§5.4). The body scales
about the `out` pin (the origin), and each pin sits at its **true scaled anchor**
(`base × scale`), generally **off the 0.25-GU grid** (`library.gate_layout`
returns `pin_offset == anchor_offset` — no snapping). There is **no lead stub**:
a wire connects at the pin directly, because a wire endpoint **snaps onto a
component pin even when that pin is off-grid** (the pin magnet, §6.4) and that
connection is an ordinary, **styleable** wire. The canvas draws the scaled body
and pin dots at the scaled anchors (`ComponentItem.paint`); `component_pin_positions`
returns the scaled anchors, so wiring/connectivity/component-follow use them. In
the LaTeX output a scaled multi-input gate scales its body **height** (× `scale`,
in the surrounding `\ctikzset` group) and **xscale** (× `scale`, no yscale), which
lands each `.in k` anchor exactly at `base × scale`, so a connecting wire attaches
at the named `(node.in k)` anchor with no bridge lead (§8). Single-input gates
(`not`/`buffer`) have both pins on the x-axis, so they scale by `xscale`/`yscale`
directly. At scale 1.0 gates are unchanged (base pins on the grid).

Because pins are at the true scaled anchor, **any** scale is wire-connectable —
there is no even-input parity constraint and the scale list is no longer
restricted to grid-aligning values. The only off-grid coordinates this introduces
are component pins and the Manhattan corners that route into them; validation
permits those specifically (§3.1).

**Wires follow the pins when the geometry changes.** Both a **scale** change
(`SetComponentScaleCommand`, pins move to `base × scale`) and an **input-count**
change (`SetParamCommand`, the inputs redistribute about the output) relocate a
gate's pins, so connected wires **follow** them — exactly as a move/resize does
(§6.6) — via the shared `_follow_pins`: each connected wire's terminal approach is
re-routed from its last on-grid vertex to the pin's new position with
`route_pin_aware` (the off-grid-aware router, §6.4), keeping the wire valid (only
the pin and its pin-aligned corner are off-grid). Pin identity is by **name** for
a param change: a surviving pin moves to its new position, while a pin that is
**removed** (fewer inputs) leaves its wire snapped to the grid — a valid,
disconnected end the user can rewire. A follow that collapses a wire to a point
removes it; **undo** restores the original wiring (and any removed wire) verbatim,
geometry and style/labels. Without this the wire would keep its old endpoint —
now off-grid and on no pin — making the schematic invalid and the CircuiTikZ
export fail.

### 5.5 Multi-Terminal Pin Geometry — Alignment Procedure

> **Superseded.** This manual procedure no longer applies to the running app.
> Every CircuiTikZ symbol's pins, alignment, geometry, and placement are now
> generated by `components/generate_components.py` (which renders each symbol in a
> **fixed bounding box** with a single constant placement origin and a **computed**
> scale and/or lead alignment) into `components/definitions.json` + `geometry.json`,
> and `REGISTRY`, the codegen tables, and the `svgsym` canvas transform are all
> built from that data. The per-component scale/leads are measured, not
> hand-typed. See [`spec/component-editor.md`](spec/component-editor.md) §4 — that
> is the authoritative procedure for adding/aligning a component. The
> detailed steps below are retained only as historical background on the geometry
> the generator now produces automatically.

CircuiTikZ multi-terminal nodes have internal pin anchor positions that do not
fall on the 0.25-GU canvas grid. This section documents the procedure for
aligning them when adding a new component.

#### Background: two independent lead/correction mechanisms

There are **two entirely separate mechanisms** that are easy to confuse. Both
may be needed for a single component, but they serve different purposes:

| Mechanism | Where | Purpose |
|-----------|-------|---------|
| **Tripole lead routing** in `tools/export_circuitikz_svgs.py` (the `TRIPOLES` table) | Canvas SVG export only | Extends the exported SVG paths so their endpoints (= the values read by `svgsym.py`) land on the grid. Has **no effect** on the LaTeX output. |
| **`_MULTI_TERMINAL_LEADS`** in `app/codegen/circuitikz.py` | LaTeX output only | Emits explicit `\draw (node_id.PIN) -- (grid_coord)` bridge wires in the generated LaTeX to bridge from a CTikZ anchor to the registry grid position. Has **no effect** on the canvas. |
| **`_MULTI_TERMINAL_EXTRA_OPTS`** (`xscale`/`yscale`) in `app/codegen/circuitikz.py` | LaTeX output only | Stretches the CTikZ symbol so its anchors land on the grid — **no bridge wires needed**. Also requires a matching scale in `svgsym.py` `Placement` when TRIPOLE_LEADS are NOT used for the canvas. |
| **Logic-gate `Component.scale`** (per instance) in `_multi_terminal_line` | LaTeX **and** canvas | Multi-input gates fold the user size into the body **height** (× `scale`) and **xscale** (× `scale`, no yscale), landing each `.in k` anchor at `base × scale`; single-input gates (not/buffer) use `xscale`/`yscale`. **No lead stubs** — pins are at the true scaled anchor and a wire attaches at `(node.in k)` directly (§5.4). The canvas mirrors the scaled body + scaled pins. At scale 1.0 nothing changes. |

**Critical invariant — scale correction and bridge wires are mutually exclusive
for a given pin.** If `_MULTI_TERMINAL_EXTRA_OPTS` contains an xscale/yscale
that moves a pin onto the grid, then `_MULTI_TERMINAL_LEADS` for that component
must be `[]` (empty). Adding both double-corrects the position and produces
misaligned symbols in the LaTeX output. Conversely, if bridge wires are used,
do not add a scale correction for the same axis. (The per-instance logic-gate
`Component.scale` does **not** use bridge leads at all: it sizes the gate so the
`.in k` anchors land at `base × scale` — via the body **height** for multi-input
gates, `xscale`/`yscale` for not/buffer — and connecting wires attach at those
anchors directly, §5.4.)

#### Step 1 — Measure actual CTikZ pin positions

In a LaTeX document, place the node at `(0,0)` and use `\pgfpointanchor` to
print each pin's coordinates in pt:

```latex
\node[KIND] (X) at (0,0) {};
\path let \p1=(X.pinname) in
  \pgfextra{\typeout{pinname x=\x1 y=\y1}};
```

Divide by **28.348 pt/GU** to convert to grid units. Note that these are in
CircuiTikZ's Y-up space; negate Y to get Qt Y-down offsets.

Alternatively, read the terminal endpoint coordinates directly from the
re-exported `geometry.json` after adding TRIPOLE_LEADS (Step 4).

#### Step 2 — Choose registry pin positions (0.25-GU snap)

Round each measured pin position to the nearest 0.25 GU (the canvas minor grid,
§3.1). These become the `PinDef.offset` values in `REGISTRY`. The registry pins
define where wires connect on the canvas, so they must be on-grid.

#### Step 3 — Choose an alignment strategy

For each off-grid pin axis (x or y), choose **one** of the two strategies below.
Do not mix them for the same axis.

**Strategy A — Scale correction** (preferred when error > ~3 px on any axis):

```
xscale = snapped_pin_x / measured_pin_x
yscale = snapped_pin_y / measured_pin_y
```

- Add to `_MULTI_TERMINAL_EXTRA_OPTS` in `app/codegen/circuitikz.py`:
  ```python
  "KIND": "xscale=1.181, yscale=1.287",
  ```
- Set `_MULTI_TERMINAL_LEADS["KIND"] = []` — **no bridge wires**.
- If TRIPOLE_LEADS are NOT used for the canvas (Strategy A also corrects canvas):
  add a matching `Placement(..., xscale=..., yscale=...)` in `app/canvas/svgsym.py`.
- If TRIPOLE_LEADS ARE used for the canvas (SVG already extended to grid):
  no `svgsym.py` scale is needed — the canvas is already correct.

Examples: MOSFETs (xscale only, no TRIPOLE_LEADS), BJTs (xscale+yscale, with TRIPOLE_LEADS).

**Strategy B — Bridge lead wires** (only when CTikZ anchors are already
rectilinearly aligned with the grid, i.e. error ≤ ~3 px and diagonal leads
are not needed):

- Add TRIPOLE_LEADS in the export script to draw leads to the grid target.
- Add `_MULTI_TERMINAL_LEADS["KIND"] = [(pin_name, ctikz_anchor), ...]` in codegen.
- Set `_MULTI_TERMINAL_EXTRA_OPTS["KIND"]` to `""` or omit it — **no scale correction**.

Example: op amp (lead wires from `.+`, `.-`, `.out` to ±1.5/0.5 GU grid positions,
no xscale/yscale because CTikZ op amp leads are already axis-aligned with the grid).

#### Step 4 — Update the SVG export leads (canvas only)

In `tools/export_circuitikz_svgs.py`, add or update the component's entry in the
`TRIPOLES` table with `leads` that draw lead stubs from each CTikZ anchor to the
**snapped** registry pin coordinates (expressed in CTikZ space with the node at
`(0,0)`). Re-run the script to regenerate the SVGs and `geometry.json`.

After regeneration, read the lead endpoint SVG coordinates from `geometry.json`
to determine the correct `svgsym.py` anchor:

```python
anchor = (base_lead_svg_x, base_lead_svg_y)  # primary pin lead final point
```

Verify local pixel coordinates from anchor:
```python
local_x = (pin_svg_x - anchor_x) / 28.348  # should match registry offset
local_y = (pin_svg_y - anchor_y) / 28.348
```

#### Step 5 — Update the component data file (no longer hand-edited code)

The `REGISTRY` entry and the codegen tables for a CircuiTikZ symbol are **no
longer hand-written**. They are built at import time from
`components/definitions.json` by `app/components/library.py` (see
[`spec/component-editor.md`](spec/component-editor.md)). Add the component's pins,
alignment (`anchor_pin`/`scale`/`leads`), and metadata as one entry in that
file (via `components/generate_components.py`, using `app/components/render.py` to
measure the anchors); the `bbox` is computed from the rendered ink extent. The
kind then appears automatically (`_DISPLAY_ORDER` is only a preference).
The five codegen tables (`_MULTI_TERMINAL_KINDS`, `_MULTI_TERMINAL_ANCHOR_PIN`,
`_PIN_TO_CTIKZ_ANCHOR`, `_MULTI_TERMINAL_EXTRA_OPTS`, `_MULTI_TERMINAL_LEADS`) and
the `REGISTRY` literal are derived from that data — they are not edited directly.

The SVG geometry (`geometry.json`, Step 4) and the `svgsym.py` placement anchor
are still produced/edited as described above; folding those into the data file is
the remaining Component-Editor step.

### 5.6 Extensibility

To add a new CircuiTikZ component type:

1. Add an entry to `components/definitions.json` (emission, `tikz`, pins with their measured offsets/anchors, `anchor_pin`, labels, variants) — measure the pin anchors with `app/components/render.py`. The leads/scale and the `bbox` are computed automatically (the `bbox` from the rendered ink extent ∪ pins).
2. Run `components/generate_components.py` to render the geometry into `geometry.json` and rebuild `definitions.json`. `REGISTRY`, the codegen tables, and the `svgsym` placement all build from this data — no `registry.py`/`circuitikz.py`/`svgsym.py` constants are edited (see [`spec/component-editor.md`](spec/component-editor.md)).
3. *(Only if the component needs special canvas behaviour.)* Add a `ComponentItem` subclass + an `ITEM_CLASSES` row in `app/canvas/items.py` — for a custom `boundingRect`, hit-testing, or resize. Plain symbols need nothing here: the lookup is `ITEM_CLASSES.get(kind, ComponentItem)`, and the base class paints any kind from its geometry. `_DISPLAY_ORDER` in `registry.py` is a preference, not a requirement — an unlisted kind still appears (at the end of its palette category); edit it only to fix an unusual position.
4. No changes to the schematic model, code generator, or UI layout are required.

(The bespoke non-CircuiTikZ kinds — `open`/`short`/`bipole`/`rect`/`circle`/`text_node` — instead keep a hand-written `ComponentDef` literal in `registry.py`.)

### 5.7 Component Symbol Conventions

All canvas symbols follow **American/IEEE style**, matching the `[american]` CircuiTikZ option used to generate the SVG reference files. This ensures pixel-accurate visual correspondence between the canvas and the compiled LaTeX output.

#### General Drawing Rules

- All symbols are drawn as `QPainterPath` geometry translated from `components/geometry.json`. No external image assets are used.
- Stroke width is `LINE_W` for normal strokes and `LINE_W * 2` for thick strokes (e.g. gate electrodes). At palette thumbnail scale (`_THUMB_SIZE`px), stroke width is reduced to `LINE_W_THIN` to prevent fine detail from filling in.
- Pin indicator dots of radius `PIN_R` are drawn at every `PinDef` offset. They are visible in normal and selected states; suppressed in ghost (placement preview) state.
- No label text is drawn inside palette thumbnails. Component identity is conveyed by shape alone.
- Each symbol is scaled so its bounding box fills the `ComponentDef.bbox` area with consistent padding on all sides.

#### Junction (Connection) Dots

A solid filled dot (radius slightly larger than `PIN_R`) is drawn on the canvas
at every junction coordinate derived per §6.4 (degree ≥ 3). Junction dots are
non-interactive overlay items, drawn above wires, and correspond exactly to the
`\node[circ]` connection nodes emitted by the code generator (§7.6). They are
recomputed from wire/pin geometry whenever the schematic changes; they are not
stored in the model.

Because junction (and open-circle) overlay items are keyed by coordinate, they
are destroyed and recreated whenever geometry changes (e.g. a group rotate),
unlike component/wire items which persist across rebuilds. To avoid a
use-after-free during painting, the scene sets `QGraphicsScene.NoIndex`: the
default BSP index defers item removal, but `_rebuild_items` drops the last
reference to a removed overlay item immediately (PySide then frees the C++
object), so a deferred index would later paint a dangling pointer. `NoIndex`
keeps the scene's item list consistent synchronously with `removeItem`.

#### Open-Circle Nodes (Unconnected Wire Endpoints)

An open circle (same radius as a junction dot, unfilled) is drawn on the canvas
at every wire endpoint that does not coincide with any component pin. Open-circle
nodes are non-interactive overlay items drawn above wires, and correspond exactly
to the `\node[ocirc]` nodes emitted by the code generator (§7.6). Only the first
and last point of each wire are candidates; interior vertices are never open
endpoints. Like junction dots, they are recomputed whenever the schematic changes
and are not stored in the model. During a **live drag preview** (vertex drag or
component drag), open-circle items track the previewed endpoint positions in
real time — they do not wait for the drag to be committed.

#### Open-Circle Nodes (Unconnected Component Pins)

When the **Mark unconnected component pins** display preference (§10.8) is on,
the canvas also draws an open circle (the same `OpenCircleItem`) at every
component pin that nothing connects to — the canvas counterpart of
`generate(..., mark_unconnected_pins=True)` (§7.6), derived from
`unconnected_pins()`. These are kept in a separate `_pin_circle_items` map so
they never collide with the wire-endpoint circles, and are reconciled in
`_rebuild_items` on every schematic change. `SchematicScene.set_mark_unconnected_pins()`
toggles the preference and rebuilds immediately. During a **live drag preview**,
`DragPreviewController.update_pin_circle_preview()` recomputes them from the
dragged components' live positions and previewed wire points so the markers
follow the gesture (e.g. a pin that picks up or loses a wire mid-drag) rather
than waiting for commit. When the preference is off, no such items exist.

### 5.8 On-Canvas Math Rendering (WYSIWYM labels)

Component labels, `text_node` content, and `bipole` box text are shown as
**typeset math**, rendered to vector by `app/preview/mathrender.py`. Two engines
produce a `QPainterPath` from a fragment; both normalise to a shared baseline so
they place identically (`render_path(fragment, engine)`):

- **`latex`** (the reference) reuses the exact toolchain that produces the
  component symbols (§5.2): a fragment is wrapped in a `standalone` document and
  run through `latex → dvisvgm --no-fonts → SVG`, parsed by `svgsym.parse_path()`.
  The `latex` invocation passes **`-no-shell-escape`** for the same untrusted-input
  reason as the full-schematic compile (§8.1) — and more pressingly, because a
  label is typeset the instant a `.hv` file is *opened* (no preview/export gesture
  required), so this is the most exposed LaTeX path. Covered by
  `tests/test_latex_security.py`.
- **`ziamath`** is a pure-Python, **no-install** fallback (a declared dependency
  that bundles the STIX Two Math OpenType-MATH font, with DejaVu Sans via
  `ziafont` for plain text). Both packages load their fonts at import time, so the
  PyInstaller bundle must ship their package data (`collect_data_files` for
  `ziamath` and `ziafont` in `heaviside.spec`; guarded by
  `test_pyinstaller_spec_bundles_ziamath_fonts`) — otherwise this fallback is dead
  in the frozen app even though it works from source. The import guard treats a
  missing module *or* a missing bundled font as "fallback unavailable" and degrades
  to raw text rather than crashing. It uses `ziamath.Text`,
  which renders mixed text with inline math **delimited by `$…$`** — matching the
  fragment convention the LaTeX engine consumes (`\strut %FRAGMENT%`), so plain
  text renders verbatim and `$…$` spans typeset as math. (`ziamath.Latex` is
  math-only and would draw the `$` delimiters as literal dollar glyphs.) The SVG —
  glyphs as `<symbol viewBox><path>` placed by `<use x y width height>`, rule
  geometry as `<rect>`, baseline at y=0, coordinates in pt at the requested
  `size` — is converted to a `QPainterPath` by `_ziamath_svg_to_path()`. This
  renders canvas labels even with **no `latex`/`dvisvgm` installed** (a subset,
  not pixel-exact to LaTeX).

No raster step is involved either way, so labels stay crisp at every zoom.

**Engine selection.** `_active_engine()` picks `latex` when `latex`+`dvisvgm`
are on `PATH`, else `ziamath`. A debug preference (§10.8, `set_force_ziamath()`)
forces `ziamath` even when LaTeX is present; toggling it re-typesets all existing
labels via `SchematicScene.retypeset_labels()`. Renders are cached in-process per
`(fragment, engine)`; the LaTeX SVG additionally caches on disk.

- **Baseline normalisation.** Every fragment is typeset behind a leading
  `\strut`, which pins the baseline to a constant device-y across all fragments.
  `render_latex()` returns the path normalised so the **baseline is at y=0** and
  the **left ink edge at x=0** (ascenders negative, descenders positive). This
  lets sibling labels on the same side share a baseline. `_baseline_y()`
  calibrates the constant once from a render of `x` (a zero-depth glyph).
- **Sizing.** Paths are in LaTeX pt at the template's 10 pt body size; callers
  scale by `GRID_PX / _PT_PER_GU` (× `font_size / 10` where a per-component font
  size applies), matching the QFont sizing used for text fallback.
- **Per-side placement (orientation-aware).** `slot_fragments(options)` parses
  the options string into `(slot_key, latex)` pairs for the side-placed families
  (`l`/`v`/`i`/`a`); the in-body `t=` slot and styling flags are excluded.
  `ComponentItem._slot_geometry()` derives the on-screen lead axis from the
  *actual* lead terminals (`_lead_terminals_local()`, through the item
  transform); resizable components (open/short/bipole) override it so the axis
  and centre track the actual span, not the default registry bbox.
  `_slot_direction(key, geom)` then chooses the offset direction. **All
  families** (`l`/`v`/`i`/`a`) are **traversal-relative**: the plain/`^` form
  sits left of the lead direction, the `_` form sits right, with `slot_side()`
  supplying the per-family default (`l` above, `v` below) and the `^`/`_`
  override. Because the preview's Y-flip makes the rendered PDF a faithful
  visual match of the canvas, the on-screen side equals the side CircuiTikZ
  draws the annotation on — for horizontal and rotated elements alike (e.g. on a
  90°-rotated capacitor `l_` lands screen-left and `v^` screen-right, the
  opposite sides CircuiTikZ produces). Voltage uses this **same** basis as the
  label rather than a separate absolute-screen heuristic (which collapsed `l_`
  and `v^` onto the same side on rotated components).
  - **Voltage-source default-`v` flip.** A **voltage source** (`V`/`cV`/`vsourcesin`,
    listed in `_VOLTAGE_SOURCE_KINDS`) draws its *default* (unsuffixed) `v=`
    label on the **opposite** side from a passive — CircuiTikZ's source voltage
    convention (the `+` terminal leads). `_slot_direction` flips the bare `v` to
    the `above` side for those kinds; current sources (`I`/`cI`/`isourcesin`)
    follow the passive default, and the explicit `v^`/`v_` forms are
    component-independent (not flipped).
  Labels are **centred on the component** and offset by the body's
  perpendicular half-thickness (bbox half-height for horizontal leads,
  half-width for vertical) plus a gap; **current** (`i=`) labels are the
  exception — their arrow rides on the thin **exit lead**, so the label clears
  the **arrowhead** (a small lead-relative gap), *not* the body, and therefore
  does not float above tall-bodied or `short` parts (see the decoration note below).
  Each `_SlotLabel` is counter-rotated to stay upright and stacks outward when
  several share a direction.
  - **Voltage/current decorations (`_AnnotationDecoration`).** Each `v=`/`i=`
    slot also draws a CircuiTikZ-style decoration beside its text, following the
    package default (`nooldvoltagedirection`, i.e. the european/electric-field
    sign convention) for an `\usepackage[american]{circuitikz}` document:
    - **Voltage, american style** (document `voltage_style="american"`, default):
      a `+` glyph at the **first-traversed** pin and a `−` at the **second**
      (`+` left / `−` right for a left-to-right passive), upright and hugging the
      body on the label side. This matches the compiled, **Y-flipped** CircuiTikZ
      preview (the canvas's visual ground truth): the preview negates Y so its
      orientation equals the canvas's, and there the `+` lands at the first pin.
    - **Voltage, european style**: a **curved arc** (quadratic Bézier) bowing toward
      the label side, with the arrowhead at the head end — from the `+` terminal
      toward the `−` (first → second pin; `v<` reverses it). Matches CircuiTikZ's
      european voltage arrow shape, not a straight line.
    - **Current** (`i=`): a bare **arrowhead** (no shaft, so it never overlaps the
      body), drawn **larger** than the european voltage arrow (`_CUR_ARROW_HEAD` /
      `_CUR_ARROW_HEAD_W`) to match CircuiTikZ's prominent current arrow, **on the
      wire near the exit (second-pin) lead** at `_CUR_ARROW_TIP`·half-span, pointing
      in the traversal (first → second pin) direction — matching CircuiTikZ's
      default `i=` placement. **Bodyless** parts (`short`/`open`,
      `_CURRENT_CENTERED_KINDS`) have no body in the middle, so their arrow is
      centred on the wire's **midpoint** instead (again matching CircuiTikZ). The value label is centred **directly
      over the arrowhead** (offset along the lead axis toward the second pin) at the
      **arrowhead clearance** (`_CUR_ARROW_HEAD_W/2 + gap`, lead-relative — *not* the
      body's perpendicular thickness, which floated the label above the wire), so it
      does **not** stack perpendicularly above an `l=` label on the same side —
      they clear each other *along* the wire (`L` centred over the body, `i_L` over
      the exit-lead head).
    - **Direction / polarity modifiers (`<` / `>`).** CircuiTikZ's `<`/`>` on a
      `v=`/`i=` key are honoured on the canvas (parsed by `slot_reversed`; `>`
      and no-modifier are the default/forward sense, `<` is reversed):
      - **`i<`** reverses the current arrow direction **and** moves the arrowhead
        to the **entry** (first-pin) lead — current flowing the other way is drawn
        on the other side of the component (`i=`/`i>` ride the exit lead).
      - **`v<`** swaps the polarity: the `−` leads (american) / the european arrow
        points second → first pin (`v=`/`v>` keep `+` at the first pin).
      The position modifiers `^`/`_` (which side) are independent of `<`/`>`
      (which direction), so e.g. `i_<` is a below-the-wire reversed current.
    - **Open annotation (`open`).** The bodyless `open` annotation component draws
      its decoration like CircuiTikZ's `to[open, …]`:
      - **`i=`**: the arrowhead is **centred on the line** (`_centered`) with the
        value label **above** it, rather than out on a lead — `i<` then only flips
        the arrow direction in place (it does not move, having no body).
      - **`v=`** (american): the `±` signs are drawn at the **terminals** (just off
        the line, since there is no body to clear) while the value label stays
        **centred** on the line; `v<` swaps the polarity as for any component. In
        the **european** style the curved voltage arrow is drawn and the label sits
        **beside the arrow** (not centred on the line, where it would cross it).
    The decoration is counter-rotated like the label so the ± glyphs stay upright,
    while the arrows follow the on-screen lead axis (so a rotated/mirrored body
    decorates correctly). The **european voltage arrow** reserves a perpendicular
    band (`_DEC_BAND`) between the body and its text so the two never overlap; the
    **american signs** sit at the terminals, and the **current arrowhead** rides on
    the wire (no shaft, no band, no perpendicular stacking) — see above.
    Polarity/direction are derived from the pin-0 → pin-1 traversal order — the
    same basis the label sides and codegen use — and the decoration colour
    follows the component's interactive/theme ink. The document
    `voltage_style`/`current_style` (§7.2) selects american vs european; changing
    it calls `SchematicScene.relayout_annotations()` to update existing
    components. This is a readable, convention-faithful representation, **not** a
    pixel-exact reproduction of CircuiTikZ's arrow geometry; `^`/`_` side
    overrides and the voltage-source default-`v` flip are honoured, but other
    direction modifiers (`invert`, `i<`/`i>`) are not parsed.
    - **Centred placement.** `ComponentItem._labels_centered_on_axis()` (default
      `False`) lets a component pin its labels *over* the lead axis instead of
      beside it: when `True` the base clearance is zeroed and the label centre
      is placed at the component centre (siblings still stack along the offset
      direction). The `open` voltage annotation overrides it to `True` so its
      label sits over the middle of the line, matching where CircuiTikZ draws
      the arrow label. A centred label is painted over an opaque white backdrop
      padded by `_LABEL_BG_PAD` (3 px) so the annotation line does not appear to
      run into the text. Centred slots draw their own connecting line, so they get
      **no** `_AnnotationDecoration` (no ± signs / arrow).
- **Hover association.** Hovering the component body *or* any of its slot labels
  highlights the whole group (body + all slot labels) in `COLOR_HOVER`, so it is
  clear the labels belong to that component. `_SlotLabel` forwards hover events
  to `ComponentItem._set_hovered()`, which repaints the body and every slot.
- **Caching.** Two tiers: an in-process `lru_cache` of parsed paths, and an
  on-disk cache of compiled SVG text keyed by a content hash (with a
  `_RENDER_VERSION` prefix so template changes invalidate it). Only **successful**
  renders are persisted, written atomically (temp file + rename) so a partial
  write can't be read back as content. A failure is **not** cached: an empty or
  missing entry is a miss and is retried, so a one-off transient failure never
  poisons a good fragment permanently (the bug where an `l=$R$` label vanished
  because an empty sentinel had been cached for `$R$`). The in-process cache still
  prevents re-shelling for a genuinely bad fragment within a session. Reopening a
  file re-parses cached SVGs without invoking `latex`.
- **Async, non-blocking.** `render_async(fragment, on_done)` runs the compile on
  a bounded `QThreadPool` (2 workers) and delivers the result back on the UI
  thread via a queued signal. Until the path arrives items show raw text; if the
  active engine yields nothing (e.g. `latex`/`dvisvgm` missing *and* the fragment
  is outside ziamath's subset) the raw-text rendering simply persists, so the
  canvas never blocks and degrades gracefully. Queued callbacks guard with
  `shiboken6.isValid` so a render landing after its item was deleted is dropped.
- **Single result dispatcher (thread-safety invariant).** All async results flow
  through **one** process-lifetime `_RenderDispatcher` QObject, created lazily on
  the UI thread (so `render_async` must be called on the UI thread); callbacks
  are token-keyed and the map is touched only on the UI thread. Per-request
  QObjects are prohibited: a request-scoped signals object's last Python
  reference ends up held by the `QRunnable`, which the pool auto-deletes on the
  **worker** thread — destroying a UI-thread-affine QObject off-thread is Qt
  undefined behaviour and manifested as a nondeterministic CI segfault in the
  main thread's event dispatch. Pinned by
  `test_render_async_single_dispatcher_no_per_request_qobject` and
  `test_render_async_callbacks_run_on_ui_thread`.
- **ziamath is serialised.** ziamath/ziafont lay out glyphs through shared
  module-level font state, so `_ziamath_path` holds a lock — the two pool
  workers never typeset through ziamath concurrently.

### 5.9 Quarter-Grid Placement

The minor grid is **0.25 GU** (§3.1). This granularity exists because some
CircuiTikZ shapes are not symmetric about a half-grid point — most notably the
IGFET MOSFET (`nigfete`/`nigfetd` and the p-channel variants), whose gate sits
≈0.27 GU below the drain–source centre. Anchored at its (grid-aligned) gate pin,
the transistor body lands ~0.25 GU off from where a symmetric 2 GU bipole
(source, R, C) centres; nudging it one minor cell (0.25 GU) lines its body up
with the neighbouring bipoles.

Because pins, wire vertices, and junctions **all** live on the same 0.25 grid,
this needs no special-casing:

- Arrow keys nudge the selection one minor cell (0.25 GU) in any direction via
  the normal `MoveCommand`; connected wires follow (§6.6) and an auto-elbow lands
  on a 0.25 node, so the result is grid-valid regardless of direction — there is
  no "perpendicular-to-a-lead" failure and no nudge is ever rejected.
- A plain **click** (press+release with no drag) preserves the component's
  position — `commit_component_drag` detects that the item never left its start
  position and pushes no move. A real **drag** re-snaps to the 0.25 grid (the
  component is live-snapped during `mouseMoveEvent`).

---

## 6. Canvas Behavior

### 6.1 Interaction Modes

The canvas operates in one of the following mutually exclusive modes at any time:

| Mode | Trigger | Cursor |
|------|---------|--------|
| **Select** | Default; press `S` or `Escape`; click ↖ in tool ribbon | Arrow |
| **Place** | Click component in palette | Crosshair + ghost component |
| **Wire** | Press `W`; click ⌁ in tool ribbon | Pen |
| **Pan** | Press `P`; click ✋ in tool ribbon; or hold `Space` + drag (transient) | Open/closed hand |

**Select**, **Wire**, and **Pan** are the three primary tools and are always accessible via the tool ribbon (§10.7). **Place** is entered automatically by clicking a palette entry and exits to **Select** on `Escape` or right-click. The tool ribbon buttons stay in sync with keyboard-driven mode changes.

### 6.2 Component Placement

1. User clicks a component in the palette → canvas enters **Place** mode.
2. A ghost (semi-transparent) rendering of the component follows the cursor, snapping to 0.25 GU. The ghost is built at the kind's **default placement scale** (`library.default_scale` — compact for logic gates, §5.4), so the preview matches what `place_component` will commit.
3. Left-click places the component at the snapped position and records an undoable `PlaceCommand`.
4. Right-click or `Escape` cancels placement and returns to **Select** mode. `Escape` is registered as a **window-level shortcut** so it fires regardless of which widget (palette, canvas, etc.) currently holds keyboard focus — clicking a palette entry to start placement does not require a subsequent click on the canvas before Escape works.
5. After placement, the canvas remains in **Place** mode for rapid repeated placement of the same component type.

### 6.3 Component Selection and Movement

- Left-click a component to select it (deselects others). Pressing on a
  component's **body** selects/drags it; pressing on a free pin instead starts a
  wire (see §6.4 "Auto-enter wire mode").
- **`Shift+click`** (or `Ctrl`/`Cmd+click`) adds an item to — or toggles it in —
  the selection, building a multi-selection. (Qt's own additive modifier is
  Ctrl-only, so the scene handles this explicitly in `mousePressEvent` to also
  honour Shift.) The hit-tested item is resolved by `_selectable_item_at`, which
  mirrors a plain click: it returns the top-most item whose **own** shape is under
  the cursor and is directly selectable, and does **not** climb from a
  non-selectable child (a slot label / annotation decoration) to its parent. This
  matters where an `open`/`short` annotation's arrow and label float across the
  elements it measures — a modifier-click on such an element selects the element
  beneath, exactly as a plain click would, rather than the annotation.
- Rubber-band drag (drag on empty canvas) selects all components and wire segments within the rectangle.
- `Ctrl+A` selects all.
- Selected components can be dragged; the component item snaps to 0.25 GU **during** the drag (not only on release), so the visual position always lands on a grid point. Movement records a `MoveCommand`. Component drag/selection is enabled **only in Select mode** — in Place/Wire/Pan modes component items are non-movable and non-selectable so a stray press cannot desync an item from its model position.
- **Wires follow the components they connect to.** When a component moves (by drag or by arrow-key nudge), any wire endpoint coinciding with one of its pins moves by the same delta — **provided that pin is the wire's sole lead.** A connected endpoint that would leave its adjacent segment diagonal gets an auto-elbow inserted to stay Manhattan; if both ends of a wire ride the same move, the whole polyline translates rigidly. **Junction re-stretch rule.** If a moving pin sits on a coordinate where **two or more wire endpoints** coincide (a junction — e.g. a component pin dragged onto a node where a rail is split), those wires do **not** follow (only a pin that is the *unique* wire endpoint at its coordinate carries its lead — the `endpoint_count == 1` gate). Dragging every stub off the node would tear the rail apart and leave overlapping segments and a phantom junction dot. Instead the component **stays connected**: the move reshape (`reshape.compute_move_reshape`, applied by `MoveCommand`) grows a fresh **re-stretch lead** from the node (the pin's pre-move coordinate) to the pin's new position — a `route()`-shaped 2-segment Manhattan wire with a new id, captured for exact undo (removed on undo, re-added on redo). So dragging a component onto a junction (its single lead collapses and is removed) and back **fully restores the original topology** — the lead re-grows identically. The live drag preview ghosts these leads (`DragPreviewController._update_restretch_preview`, throwaway non-interactive `WireItem`s) so the preview matches the committed result rather than showing a momentarily-disconnected component. (The all-components-moved and explicitly-selected-wire cases below still translate rigidly regardless, and create no re-stretch leads.) **When all components in the schematic are moved together (select-all drag), every wire translates rigidly regardless of connectivity** — free (open-circle) endpoints move with the rest of the circuit instead of being left behind. **Explicitly-selected wires** (rubber-band selection includes wire items) are also translated rigidly as part of the drag — the scene passes the selected wire IDs to `MoveCommand` via the `wire_ids` parameter, and the preview treats those wires the same way. The reshape is part of the same `MoveCommand` and is fully reversed on undo. A live ghost of the reshaped, simplified wires is shown during the drag.
- **Whole-wire drag (move a wire).** Pressing a wire's **body** (not a vertex handle, mid-label, or endpoint handle — those gestures take priority) and dragging translates the whole wire. The pressed wire is selected if it wasn't, and every selected wire translates together; the scene drives this through `DragPreviewController.preview_wire_drag` (live ghost) and commits a `MoveCommand([], delta, wire_ids=…)`. **Junction taps follow.** Any *other* wire joined to a translated wire at a shared vertex or segment point has that shared point move by the same delta and reshapes Manhattan to stay connected, while its far (e.g. pinned) end stays put — so dragging a bus repositions it without detaching its taps. (Implemented in `reshape.compute_move_reshape`: a non-explicit wire endpoint that lies on a translated wire's pre-move polyline, `_point_on_polyline`, is treated as a hit.) The whole move is one undoable step.
- `R` rotates the selection 90° CW around the bounding-box centroid of the selected component positions (snapped to 0.25 GU); records a `GroupRotateCommand`. When a single component is selected the centroid equals its own position, so it spins in place. Connected wires are reshaped or rigidly rotated according to whether their other endpoint is inside or outside the selection (see §6.6 `GroupRotateCommand` note). `Component.label_offset` is cleared for each rotated component so the label auto-repositions. In **Place** mode `R` cycles the ghost's rotation instead.
- Arrow keys nudge selected components by `NUDGE_GU` (0.25 GU, one minor-grid cell) per keypress, in any direction. Connected wires follow (§6.6) and stay grid-valid.
- `Delete` or `Backspace` deletes the current selection — components (and any wires connected to their pins) **and** any directly-selected wires; records a `DeleteCommand`.

### 6.4 Wire Routing

A wire is an ordered list of Manhattan-routed points (§4.3). The whole feature is
built on **one** routing primitive and **one** simplification pass, reused by
every code path (drawing preview, drawing commit, vertex drag, component
follow). Duplicating the corner/elbow math across paths is the historical source
of preview-vs-commit disagreement and accumulating vertices, and is prohibited.

The same single-source rule applies one level up. The wire-follow/reshape rules
for component moves, whole-wire drags, box resizes, terminal-pin drags, and
vertex drags are defined **once**, as pure Qt-free functions in
`app/schematic/reshape.py` (`compute_move_reshape`, `compute_pin_drag_reshape`,
`compute_box_resize_reshape`, `reshape_wire_points`, `move_vertex_points`,
`reshape_junction_wire`). They mutate nothing and return a `WireReshapeResult`
(new point lists plus collapsed/contained/re-stretch side-effect info); the
undoable commands **apply** the result (keeping their verbatim-undo
bookkeeping) and the drag previews **render** the same result as ghosts, so a
preview cannot disagree with the committed outcome. `tests/test_drag_parity.py`
pins this with end-to-end gesture scenarios asserting that the last-frame ghost
geometry equals the post-commit model exactly — re-forking a rule fails those
tests.

#### The routing primitive

`route(a, b)` returns the Manhattan path between two points as either `[a, b]`
(when they already share an x or y) or `[a, corner, b]` (one auto-corner). The
corner orientation defaults to **dominant axis** (`vfirst=None`): travel along the
longer axis first — horizontal-first when `|bx − ax| ≥ |by − ay|`, otherwise
vertical-first. A caller may pass `vfirst` to force the orientation. An "elbow"
inserted to keep a shifted segment Manhattan is simply `route(a, b)` with the
corner orientation the caller needs — the same function, never a re-implementation.

**Drawing uses a heading memory.** While a wire is being drawn, the scene passes
`route_pin_aware` (and thus `route`) a `vfirst` derived from the cursor's **locked
out-direction**: the first leg keeps the axis the cursor first went out along
(`SchematicScene._update_wire_heading` locks it once the cursor leaves the last
vertex by ≥ 0.5 GU with a clear dominant axis; reset at each committed vertex).
So if you go out horizontally and then drag down, the elbow stays *right-then-down*
instead of flipping to *down-then-right* once the vertical leg grows longer — the
wire traces the path the cursor took. There is still no modifier key; the user
steers by dropping intermediate vertices, and the memory just makes the default
elbow match the gesture. (A **single-axis** off-grid pin's lead direction still
overrides the heading — the adjacent leg keeps the pin's lone off-grid coordinate
so the wire continues along the lead. But a pin off-grid in **both** axes — the
thyristor/triac gate — has no single lead axis, so it follows the heading: *either*
orientation keeps a valid corner because the corner inherits one of the pin's own
off-grid coordinates, so you can route up/down from the gate exactly as you route
left/right. See `route_pin_aware`, §5.4.)

#### Drawing

A wire-in-progress is an ordered list of committed vertices. The first comes
from the starting click; the live **wire ghost** previews `route(last_vertex,
cursor)` from the last committed vertex to the snapped cursor and updates on
every move.

1. In **Wire** mode, left-click a pin, an existing wire, or any grid point to begin a wire (the first vertex).
2. Move cursor → the ghost shows the `route()` L from the last committed vertex to the cursor, oriented by the cursor's locked out-direction (the heading memory above).
3. Left-click on an empty grid point **commits the previewed L** — both its auto-corner (if any) and the clicked point become committed vertices — and routing continues from there. This is how the user places intermediate corners and steers the path.
4. Left-click on a pin, an existing wire vertex, or a wire segment **finalizes** the wire (committing the previewed L to that target) and returns to **Select** mode (see "Finalizing").
5. Double-click finalizes the wire at the cursor and **exits Wire** mode (returns to **Select**) — whether the cursor lands on a connectable target or on an empty grid point (which becomes a free open endpoint).
6. `Escape` discards the wire in progress (clearing the ghost) without leaving Wire mode.

On finalize the committed point list is passed through `simplify_points`; if it
collapses to fewer than two distinct points the wire is **discarded** (no
degenerate zero-length wire is created). A committed wire is recorded as a
`WireCommand` (or a `MacroCommand` when a split is also required).

#### Snapping

The snapped cursor target is resolved (within `PIN_SNAP_GU` = 0.125 GU) with this priority:

1. **Component pin** — connects to the pin, **even when the pin is off the 0.25 GU
   grid** (a scaled logic gate's terminal). The pin pass measures from the **raw
   (unsnapped) cursor** (`wire_snap_target(raw_gu=…)`), because an off-grid pin's
   nearest grid node can fall outside `PIN_SNAP_GU`; the wire endpoint then rests
   on the exact pin coordinate. The wire/vertex/segment passes (2–3) and the grid
   fallback (4) still use the grid-snapped cursor.
2. **Existing wire vertex** — connects to that wire, forming a junction.
3. **Point on an existing wire segment** — connects mid-segment (see "Junctions and segment splitting").
4. Otherwise the bare **0.25 GU grid node** under the cursor.

Priorities 1–3 are **connectable** targets; the ghost's end marker distinguishes
a connectable snap (ring) from a plain grid-node anchor (dot), and connectable
targets are what finalize the wire. When a leg routes **from or into an off-grid
pin** (a scaled gate's terminal), the leg adjacent to the pin is oriented to keep
the pin's off-grid coordinate (`SchematicScene._route`), so the wire extends from
the pin along its own lead line and only *then* elbows onto the grid — instead of
jumping to the grid immediately at the pin. The corner inherits the pin's off-grid
coordinate (validation permits a wire coordinate that lines up with a pin, §3.1);
with neither endpoint off-grid the corner is kept on the grid as usual.

The same off-grid handling applies to **dragging an existing wire vertex**
(`SchematicScene._vertex_drag_target`, used by both the live preview and the
commit so they agree). From the raw (unsnapped) cursor: a nearby pin / wire vertex
/ wire segment may win (the magnet — so the vertex can connect, including onto an
off-grid pin); otherwise each axis snaps independently to the grid **or to an
off-grid pin axis line of the dragged wire** (`_wire_offgrid_pin_axes`). When both
a magnet target and the axis snap exist, the one the raw cursor is **closer** to
wins (ties → the magnet), so a pin stays grabbable without capturing a position
the cursor is nearer to — in particular the on-grid line *between* two adjacent
off-grid pins (one `PIN_SNAP_GU` from each) stays reachable. The
effect is that a vertex collinear with an off-grid pin can be slid *along that
pin's axis* — its off-grid coordinate is preserved so the segment into the pin
stays straight — while its other coordinate snaps to the grid; dragging away from
the axis snaps fully to the grid. `move_wire_vertex` / `move_junction` apply the
same rule (`_snap_vertex_target`) so a direct call snaps consistently. The
click-vs-drag test also considers genuine raw-cursor movement (a drop onto an
off-grid pin whose nearest grid node equals the press node still reads as a drag).

#### Finalizing and mode transitions

- In **Select** mode, left-clicking an **unconnected** pin (a pin with no wire endpoint on it) auto-switches to **Wire** mode and begins a wire there. Clicking a connected pin, or a component body, does normal selection/drag instead. The auto-start uses a tight grab radius so a press near a component's centre still selects/drags the component.
- In **Select** mode, a **double-click on blank canvas** (no wire or component hit) enters **Wire** mode, starting a free wire from the snapped 0.25 GU grid point.
- In **Select** mode, **double-clicking a wire's rendered label** — an endpoint label (`_WireEndLabel`) or the mid-wire label (`_WireMidLabel`) — opens its in-place text editor (§4.3) instead of routing. These checks run **before** the wire-body check so a rendered label isn't shadowed by the "double-click wire → edit mid-label" gesture. The **mid-wire label is also draggable**: a left-press on it (handled in the scene's `mousePressEvent`, ahead of vertex-drag/selection) starts a drag that slides it along the wire; `mouseMoveEvent` previews and `mouseReleaseEvent` commits the new fractional position. A no-movement press falls through to the double-click edit.
- In **Select** mode, **double-clicking a wire endpoint** (any first/last vertex, via `wire_vertex_at` — endpoints are draggable, including connected ones) opens that endpoint's label editor (§4.3) — so a label can be started even when none is set yet and there is no rendered label to click. This applies to free endpoints **and endpoints connected to a component pin or a drawing element**. Interior vertices and the segment body fall through to the wire-body (split-and-start) gesture below. This check runs after the rendered-label check and before the wire-body check.
- In any mode, **`Tab` while the cursor hovers a wire** cycles styling at the cursor without selecting (handled in `SchematicView.event()`, ahead of Qt's focus navigation, via `SchematicScene.cycle_at`), by what the cursor is over, in priority order: (1) a rendered **endpoint label** (`_WireEndLabel`) → cycle that label's **placement** (`WIRE_LABEL_PLACEMENTS`: off-end → above/left → below/right → off-end); (2) a wire **endpoint** (free *or* connected — connected endpoints are draggable so `wire_vertex_at` returns them) → cycle that end's **marker** (`WIRE_MARKER_CYCLE`: none → arrow → stealth → open → bar → none), so an arrowhead *into* a block-diagram shape is cyclable with Tab; (3) a **wire body** (or interior vertex) → cycle the **line style** (`WIRE_LINE_STYLE_CYCLE`: solid → dashed → dotted → dash-dot → solid). **`Shift+Tab`** steps backward. Each step is an undoable `set_wire_*` command. When a label editor is focused, `Tab` is left to the editor. If the cursor is over nothing, `Tab` keeps its normal focus-navigation behaviour.
- In **Select** mode, **double-clicking on a wire body or interior vertex** (no modifier) **splits the wire there and starts a new wire** from that point: it snaps to the nearest point on the wire (`_wire_snap_point`) and enters **Wire** mode routing from it; the target wire is split when the new wire commits (the split is bundled into the commit). **Alt + double-click** on a wire instead opens its **mid-label inline editor** (`begin_label_edit("mid")`) — add or edit the over-the-wire "Middle" caption (§4.3). (Double-clicking an *endpoint* edits that endpoint's label instead, per the rule above.) The wire check takes priority over the component double-click check so that wires near or inside a component's bounding box remain reachable.
- A wire that terminates on a **connectable** target — a pin, an existing wire vertex, or a wire segment — finalizes and returns to **Select** mode.
- A **double-click** ends the wire and **exits Wire** mode (returns to **Select**), regardless of where it lands: on a connectable target the end connects there; on an empty grid node the end becomes an open `ocirc` endpoint. (Previously a double-click in empty space stayed in Wire mode for continued routing; it now exits.)
- **Sticky wire style.** New wires inherit a remembered style (`SchematicScene._new_wire_style`: `line_style`, `line_width`, `start_marker`, `end_marker`), applied in `add_wire`. The template is updated whenever the user **touches a wire's style** — selecting a single wire (`_on_selection_changed` captures all four fields), **or editing a field** via the inspector / Tab-cycle (each `set_wire_line_style` / `set_wire_line_width` / `set_wire_start_marker` / `set_wire_end_marker` records the new value into `_new_wire_style`). So the user can pick a template wire (e.g. one with an arrow endpoint), or just cycle/set a style, and keep drawing wires in it. It resets to the defaults (solid / 0.4 pt / no markers) for a new document (a fresh scene).

#### Junctions and segment splitting

- Where wires (and pins) meet, a solid **connection dot** is drawn and emitted as `\node[circ]` (see §7.6). The dot rule is based on the **degree** of a coordinate — the number of wire segment-ends meeting there (an endpoint counts 1, a pass-through/interior vertex counts 2) plus 1 for a coincident pin. **Degree ≥ 3 → dot.** A straight pass-through, a lone corner, two wires meeting end-to-end, and a pin with a single wire all have degree 2 and get no dot. (In this model coincident wire points are electrically joined; a true non-connecting **crossing** — one wire's segment passing through another's with *no shared vertex* — is never a connection, and is the case the optional **line-hop** decoration marks, see "Line-hops" below.) A wire with `no_junction_dots=True` (§4.3) is **excluded from the degree count entirely**, so annotation leads do not create dots; other wires/pins at the coordinate are still counted normally. A **degenerate single-point wire** (`len(points) < 2`, which can linger in an old file) connects nothing and is likewise excluded — both by the static `junction_points()` and by the **live drag preview** (`DragPreviewController.update_junction_preview`), which must skip it too or a sub-cell drag (whose rounded pin lands back on the point) would push the degree to 3 and flash a phantom dot. Such a wire should **never** be created by the editor (see the no-degenerate-half guard below), but as a safety net for an old/hand-edited file `WireItem` paints a single-point wire as a **red ✕ marker** (`_DEGEN_X_R`) that is **selectable and deletable** — so a stray one is visible and removable rather than an invisible source of phantom dots.
- Wire endpoints that do not coincide with any component pin are drawn as **open circles** and emitted as `\node[ocirc]` (see §7.6). Only the first and last point of each wire are candidates; interior vertices are never open endpoints. A wire with `no_termination_dots=True` (§4.3) is **excluded from `open_endpoints()`**, so its free ends get no terminal — while it still counts as a connection for other wires ending on it. An end carrying a **custom marker** (`start_marker`/`end_marker`, §4.3) is likewise excluded at that specific end, so the marker (e.g. an arrowhead) replaces the automatic open-circle terminal there. The connecting-pin set used here is `component_connection_points` (not the bare named pins), so a wire end on a **rect edge** (§5.4, any 0.25 GU perimeter point) or a **circle cardinal point** (N/S/E/W) counts as connected and gets no open circle. Those box connection points do **not** contribute to the junction degree count above (no spurious dot where a wire meets a box boundary).
- When a wire connects to the **middle of another wire's segment** or to an existing wire's **intermediate (corner) vertex** — whether by drawing a new wire onto it, by dragging an existing wire vertex onto it, or by **placing or moving a component** such that one of its pins lands mid-segment — the target wire is **split into two independent wire objects** at the connection point so each half is separately selectable and deletable, and a junction dot is drawn. Connecting at an existing *endpoint* (first or last vertex) does not split. The split is bundled with the triggering command (`WireCommand`, `MoveWireVertexCommand`, `PlaceCommand`, or `MoveCommand`) inside a `MacroCommand` so it is one undoable action. Component operations that trigger splits: initial placement, drag-drop, arrow-key nudge, and paste. **No-degenerate-half guard.** A split's `(index, point)` is computed against the wire's *pre-move* geometry, but applied **after** the bundled `MoveCommand` reshaped that wire — which can have moved the wire's own endpoint onto the split point. `SplitWireCommand` therefore re-checks at `do()` time: it treats the corner-split case only for a *genuine intermediate* vertex (`0 < idx < len−1`), no-ops when the point now coincides with an endpoint, and refuses any split that would carve off a `< 2`-point half. This is the single place a degenerate single-point wire could otherwise be created — every other wire-mutating command (`Move`/`Resize`/`GroupRotate`/`MoveWireVertex`/`MoveJunction`) already **removes** a wire that collapses to a point, and `add_wire` rejects a `< 2`-point input.
  - **Fully-contained wire (a second degenerate class).** A wire whose entire polyline lies on top of other wires' segments (collinear overlap) is **redundant** — it draws nothing new and forms no connection — so it is removed. The move reshape runs `wire_contained_by_others()` (a pure per-segment collinear-coverage test in `model.py`; coverage and collinearity use the same 6-decimal tolerance as `point_key` — both derive from one constant, `_KEY_DECIMALS`/`KEY_EPS` — so float noise from off-grid pins cannot hide a contained wire) over the wires *it touched* (reshaped or re-stretched) after a move, and drops any now fully covered by the others (captured for exact undo); processing is sequential so one of a coincident/duplicate pair survives. This catches e.g. a lead dragged collinearly onto the rail it connects to. Only move-touched wires are candidates, so an unrelated redundant wire elsewhere is left alone.
- When a wire is **deleted** and the deletion dissolves a T-junction (a free endpoint now has exactly two remaining wire neighbors and is not a component pin), those two stubs are automatically **merged** into a single wire. The merge is bundled with the `DeleteCommand` inside a `MacroCommand` so delete + merge is one undoable action. Undoing restores the deleted wire and re-splits the merged wire back into its two halves.

#### Line-hops

A **line-hop** is a small semicircular bump drawn on one wire where it crosses another *without connecting*, so the crossing reads unambiguously as "no connection". Like junction dots and open circles it is **pure decoration, derived and never stored** — computed by `wire_crossings(schematic, default_on)` (returns a list of `WireHop(point, wire_id, orientation, seg_index)`; `default_on` is the global preference, see "Toggle" below).

- **Where.** A hop is placed where a **horizontal** segment of one wire and a **vertical** segment of another cross **strictly interior to both** segments, with **no vertex** of either wire at the point (and, defensively, the point is not a junction or component connection point). Because the editor splits a wire whenever another wire's *endpoint* lands mid-segment, by render time every genuine connection already shares a vertex — so such a vertex-free crossing is provably non-connecting. (All wires are Manhattan, so crossings are always H×V — trivial interval math.)
- **Which wire hops.** At a crossing, a wire may hop only if its `hop_mode` allows it there: `"never"` can never hop (but may be hopped *over*), `"always"` can always hop, `""` (default) can hop only when the global preference is on. Among the wires allowed to hop, the winner is chosen by **tier** (`"always"` outranks default), then higher `z_order`, then later position in `schematic.wires`. If neither may hop, no bump is drawn. So `"always"` forces a hop even with the global preference off or a lower `z_order`, and `"never"` cleanly hands the bump to the crossing wire. The bump bulges perpendicular to the hopping wire — upward for a horizontal segment, rightward for a vertical one — so it never overdraws the crossed wire regardless of stacking.
- **Excluded wires.** A wire with `no_junction_dots=True` (an annotation lead, not a real connection) is excluded from hops in **either** role — neither hopping nor hopped over — paralleling its exclusion from junction dots. (This is distinct from `hop_mode="never"`, which still allows the wire to be hopped over.)
- **Toggle.** Drawing hops is a **display preference** (`display/line_hops`, §10.8), **on by default** (the schematic-drawing convention). It is passed as the `default_on` argument of `wire_crossings()` — governing only default-mode (`hop_mode==""`) wires, so per-wire `"always"`/`"never"` overrides still apply when it is off. The scene flag `_line_hops` (set via `SchematicScene.set_line_hops`) and `generate(..., mark_line_hops=…)` therefore **always** run the detector and feed each wire item / the LaTeX output its bumps (§7.6) — the preference no longer gates the call, only the default-mode wires. On the canvas and in export each bump is one cubic-Bézier approximation of a semicircle of radius `HOP_RADIUS_GU` (the single GU source of truth shared by canvas pixels and LaTeX coordinates). Each hop is matched to the segment it lies on **by coordinate**, so a hop that no longer falls on the polyline is simply skipped — the same hop list works for committed and live-preview geometry.
- **Live during gestures.** Hops update *live* while a gesture is in flight, not just on commit: `SchematicScene._refresh_preview_hops()` recomputes `wire_crossings` against the **in-progress** geometry — each wire's drag-preview points where a reshape is active (component move, wire-vertex/junction drag, endpoint resize), plus any transient `extra_wires`. It is called after each drag-preview frame in `mouseMoveEvent` (parallel to the live junction/open-circle previews in `DragPreviewController`), and reassigns every wire item's `.hops`. When **drawing** a new wire, `_draw_preview_hops` feeds the in-progress polyline as a synthetic `extra_wire`; its hops paint on the dashed `WirePreviewItem` (a higher-`z_order` existing wire it crosses shows the bump instead). Discarding the in-progress wire (`_cancel_wire`) recomputes committed hops so any preview bump clears.

Connectivity is **purely geometric and never stored**: two coordinates are
electrically joined precisely when they are equal under the `point_key`
convention (6-dp rounding, §4.5 — stored geometry is unrounded; only
comparisons are rounded, so off-grid pin float noise cannot detach a wire).
Junction dots, open-circle
ends, and which wires follow a moved component are all *derived* from point
coincidence whenever the schematic changes — so dragging an endpoint onto a pin
connects it and dragging it away disconnects it, with no bookkeeping. The set of
"connecting" coordinates for a component is `component_connection_points`: the
named-pin coordinates for ordinary components, the full 0.25 GU perimeter for a
`rect`, and the four cardinal points for a `circle` (so block-diagram boxes
connect along their boundary, §5.4). The companion `component_pin_positions`
(named pins only) is used where boundary points must *not* expand the set —
junction-dot degree counting, the resize terminal pin, unconnected-pin detection,
pin-dot drawing, and SELECT-mode wire auto-start.

#### Editing existing wires

- In **Select** mode **every** wire vertex is draggable and shows a grab handle — intermediate corners, free endpoints, **and endpoints connected to a component pin or drawing element**. Dragging one moves that vertex, re-routing each adjacent segment through `route()` to stay Manhattan and simplifying afterward; recorded as a `MoveWireVertexCommand`. The **live drag preview** mirrors this exactly — segments are re-routed and `simplify_points` is applied on every mouse-move, so the ghost never shows redundant collinear vertices and no diagonal segments appear before the mouse is released. A dropped vertex snaps to a connectable target (pin / other wire) just like a drawn endpoint. **Dragging a junction drags every wire that meets there, preserving each wire's orientation into the junction.** When the grabbed vertex coincides with other wires' vertices (a junction), the drag captures the whole coincident group (`_coincident_vertices`) and moves them together via `move_junction` → a single **`MoveJunctionCommand`** (plus any split of a third wire the junction lands on). Each wire is reshaped by `reshape_junction_wire`, which **keeps the orientation of the segment entering the junction**: a wire arriving vertically still arrives vertically, one arriving horizontally still horizontally. It does this by *relocating the adjacent corner* along the terminal axis (when the junction neighbour is an interior corner) or inserting an orientation-preserving elbow (when the neighbour is the far endpoint of a 2-point wire) — rather than the plain horizontal-first elbow used for an ordinary single-vertex move, which would flip the approach to the wrong side. The live preview mirrors this exactly. **During the drag** a highlighted, enlarged dot (`JunctionDragItem`) follows the cursor and the resting junction dot at the origin is hidden (`DragPreviewController._show_junction_preview` / `clear_junction_preview`), so it is clear the whole junction — not just one wire end — is moving. A lone vertex is a group of one (the ordinary single-vertex move via `MoveWireVertexCommand` / `_move_vertex_points`; horizontal-first elbows, no highlight dot). **Junction dots also grow and highlight on hover** (`JunctionItem` accepts hover events) to signal they are draggable. **Dragging a connected endpoint off its pin/edge disconnects it** (the endpoint no longer coincides with the pin, so it becomes a free `ocirc` terminal and the pin becomes unconnected); component-follow still moves a connected endpoint when the *component* is moved (handled by `MoveCommand`, independent of direct vertex dragging). A press exactly on a connected endpoint therefore starts a wire-vertex drag (to disconnect) rather than selecting/dragging the component — grab the component by its body instead. If a drag collapses the wire to a single point (its simplified path drops below two vertices), the wire is **removed** rather than left as a degenerate single-point wire — and `undo` restores it. (This mirrors `MoveCommand`'s handling of a collapsed wire-following.)
- A vertex grab is a **drag** only if the cursor moves to a different snapped grid node between press and release; otherwise it is a plain **click** that **selects the wire** (and pushes no command). The click/drag test is on cursor *movement*, **not** on whether the snapped cursor differs from the vertex's old position — a vertex may be grabbed from up to `VERTEX_HIT_GU` away, so a stationary click whose snapped position differs from the vertex must not be misread as a drag (which would teleport the vertex onto the cursor, e.g. onto a pin, spuriously inserting a junction dot). This also makes a short wire — whose vertex-grab zones can cover most of its length — selectable and deletable by clicking near its ends, where a free open-circle endpoint sits.
- Wire **selection hit-testing** uses a thin band along the actual segments (and the vertex handles), not the wire's bounding rectangle, so a wire does not steal clicks from nearby components.
- A click that lands at an **intermediate vertex** (an L-corner shared by two adjacent segments of the same wire) is treated as an interior hit (rank 0), not an endpoint touch, so that wire wins selection over an adjacent wire stub whose endpoint happens to coincide with the corner.

#### Wires follow the components they connect to

When a component moves (drag or arrow-key nudge), every wire endpoint coincident
with one of its pins shifts by the same delta. If only one end of a wire moves,
its adjacent segment is re-routed through `route()` (an auto-corner is inserted
if it would otherwise go diagonal); if both ends share the delta the whole
polyline translates rigidly. A wire that collapses to a single point is removed.
The reshape is part of the same `MoveCommand` and is fully reversed on undo (see
§6.3).

#### Wire Simplification

Wire point lists are kept minimal at all times via the single `simplify_points`
pass: consecutive duplicate points are removed and redundant collinear interior
vertices are collapsed (e.g. `(0,0)–(2,0)–(5,0)` becomes `(0,0)–(5,0)`).
Simplification runs when a wire is created, when an endpoint follows a moved
component, and when a vertex is dragged; the code generator also simplifies
defensively so output is always minimal.

Wires do not auto-route around components in v1 — the user routes manually.

### 6.5 Component Properties

- **Double-clicking the options label** on the canvas (the `LabelTextItem` child) activates in-place text editing of the full options string. Committing with **Enter**, **Return**, or by clicking away (focus loss) fires an `EditCommand`; committing empty text clears the options. **Escape** cancels without changes. The label double-click check runs before the component check.
- **Dragging the options label** repositions it freely within the parent component's coordinate system. On mouse release a `MoveOptionsLabelCommand` is pushed, storing the new `(dx, dy)` component-local pixel offset as `Component.label_offset`. The drag is only possible in **Select** mode.
- **Double-clicking the component body** in **Select** mode opens the in-place options editor **and** emits `SchematicScene.component_double_clicked(component_id)` to open the **Properties Panel** simultaneously. While editing, all key events (Backspace, Delete, arrows, Escape) are routed to the editor rather than the canvas hotkeys.
- The **Properties Panel** shows a single `QLineEdit` for the options string, with the component's valid `label_slots` shown as hint text below the field. The field accepts any CircuiTikZ option string verbatim.
- Rotation and mirror controls are also in this panel.
- Changes are applied immediately (300 ms debounce) and record an `EditCommand`.

### 6.6 Undo / Redo

- Full undo/redo stack using the **Command pattern**.
- `Ctrl+Z` undoes the last command; `Ctrl+Shift+Z` or `Ctrl+Y` redoes.
- The undo stack is per-session and is not persisted to the JSON save file.
- Commands:

| Command | Inverse |
|---------|---------|
| `PlaceCommand` | Remove component |
| `DeleteCommand` | Restore component(s) and removed wires (connected and directly-selected) |
| `MoveCommand` | Move component(s) back and restore reshaped wires' original points |
| `WireCommand` | Remove wire |
| `SplitWireCommand` | Remove the two half-wires and restore the original wire |
| `MergeWireCommand` | Split the merged wire back into the two original halves |
| `MoveWireVertexCommand` | Restore the wire's original point list (or re-add the wire if the drag collapsed it to a point) |
| `EditCommand` | Restore previous options string |
| `MoveOptionsLabelCommand` | Restore previous `label_offset` value |
| `RotateCommand` | Restore previous rotation value |
| `MirrorCommand` | Restore previous mirror state |
| `GroupRotateCommand` | Restore component positions, rotations, label offsets, and all affected wire points (re-adding any boundary wire that collapsed and was removed) |
| `SetDocumentPropertiesCommand` | Restore the previous document voltage/current styles (§10.9) |
| `MacroCommand` | Composite of the above (e.g. a split + add, or a multi-component move) |

Notes:

- `MoveCommand` also drags connected wire endpoints with the component and captures each affected wire's original points so undo restores them exactly (see §6.3).
- `SplitWireCommand` replaces a wire with two independent halves when another wire connects mid-segment; it is normally bundled with the triggering `WireCommand` / `MoveWireVertexCommand` in a `MacroCommand` so the connection is one undoable action (see §6.4). The split site is resolved from the stored *point* against the wire's **current** geometry at `do()` time — the constructor's index is only a hint and never trusted, because an earlier command in the same macro (a move or nudge) may already have reshaped/simplified the wire, and an index computed against the pre-move geometry would corrupt the polyline. When the point is no longer on the wire, or sits at one of its endpoints, the command (and its undo) is a clean no-op.
- `MergeWireCommand` merges two wire stubs that share a free endpoint into one wire; it is bundled after a `DeleteCommand` inside a `MacroCommand` when the deletion dissolves a T-junction (see §6.4). The two wires are **re-resolved at `do()` time**: if a referenced wire id no longer exists (an earlier merge in the same macro consumed it — deleting both taps of a bus dissolves two junctions that share the rail), the merge falls back to whichever wire currently ends at the merge point, so sequential merges compose into one wire instead of silently no-opping.
- `DeleteCommand` accepts both component ids and wire ids, removing components, the wires connected to their pins, and any directly-selected wires.
- Commands that **remove** a wire as a side effect (a `Resize`/`MoveWireVertex`/`MoveJunction`/`GroupRotate` collapse, a redundant fully-contained wire after a move) capture the pristine `Wire` object and its list position, so undo restores the wire **verbatim** — labels, markers, line style, and stacking position included — at its original index.
- `GroupRotateCommand` is used by `rotate_selected_cw()` for all rotations (single or multi-component). It rotates positions around the bounding-box centroid of the selection (snapped to the 0.25 GU grid), increments each component's `rotation` by 90°, clears `label_offset` (reset to auto), rotates all selected and internal wire vertices, and reshapes boundary wires (one endpoint on a selected pin, one not) using the same elbow logic as `MoveCommand`. If a boundary wire's reshape collapses it to a single point (its moving end folds onto its fixed end), the wire is **removed** rather than left as a degenerate single-point wire — and `undo` re-adds it (same guard `MoveCommand` applies to wire-following). For a **mirrored** component the rotation field steps by **−90 instead of +90**: the mirror is applied *outermost* (after rotation, §4.2/§7.2), so `R90·M·R(r) = M·R(r−90)` — using +90 would send the pins to the mirror-image of where the geometric rotation moved the wires and detach them.
- `MacroCommand.do()` **unwinds on failure**: if a child command raises, the already-executed children are undone in reverse order before the exception propagates, so a failed composite (e.g. a multi-component inspector edit) never leaves a half-applied document. `UndoStack.push` records nothing when `do()` raises.
- The stack is `UndoStack` (`app/canvas/commands.py`). Besides `push` (apply + record) it offers `record()` — record an **already-applied** command without re-executing it, used by `scene.batch()`, which applies each command immediately at push time (so later commands in the batch compute against the **live** document state) and records the wrapping `MacroCommand` once at the end. The stack also tracks the **save point**: `mark_save_point()` records the history position at each successful save/load/new, and `is_modified()` is true iff the current position differs from it — so undoing back to the saved state clears the window's dirty marker, while divergent edits (undo past the save point, then a new push) make the saved state unreachable and the document stays modified until the next save. The main window derives its modified state and the **Undo/Redo actions' enabled state** (`can_undo()` / `can_redo()`, synced after every command/undo/redo) from the stack.

### 6.7 Copy / Paste

- `Ctrl+C` copies selected components and the wires entirely within the selection.
- `Ctrl+V` enters **Place** mode with the copied group as a ghost; left-click places it and assigns new UUIDs to all pasted items.
- `Ctrl+D` duplicates with a fixed offset of (1, 1) GU.

### 6.8 Graphics Item Lifetime (Memory-Safety Invariant)

Because PySide ties a `QGraphicsItem`'s C++ lifetime to its Python reference
count, the C++ object is freed the instant its last Python reference is dropped.
If the scene still holds a raw pointer to that item, the next paint dereferences
freed memory and the process segfaults. To make this class of bug structurally
impossible, the scene observes the following invariants:

- **Single removal chokepoint.** Every item leaves the scene through
  `SchematicScene._remove_item(...)` — including the drag-preview ghosts in
  `drag.py`. Callers pass `dict.pop(key)` directly, so the tracking-dict entry
  and the scene item are dropped together, and the item is always detached from
  the scene (`removeItem`) *before* its last reference dies. `removeItem`
  synchronously clears the scene's other internal pointers (selection, focus,
  mouse grabber, hover).
- **Grab-safe removal.** Before `removeItem`, `_remove_item` checks whether the
  scene's current `mouseGrabberItem()` is the item being removed *or any of its
  descendants* and explicitly `ungrabMouse()`s it first (defence in depth over
  Qt's own cleanup), so a command pushed mid-gesture can never leave Qt holding
  a grab on a freed item. Code ordering inside event handlers ("push only after
  `super().mouseReleaseEvent()`") is therefore no longer load-bearing for
  memory safety.
- **Re-entrant rebuilds coalesce.** `_rebuild_items` is a guarded wrapper: a
  call arriving while a rebuild is already running (e.g. a `selectionChanged`
  handler pushing a command mid-rebuild) logs a warning, sets a pending flag,
  and returns; the outer invocation loops until the model and items are
  reconciled. The outermost call still returns synchronously with the items
  up to date.
- **No lingering references.** Nothing retains a reference to an item after it
  has been removed; conversely, every live item is owned by exactly one tracking
  structure (`_comp_items`, `_wire_items`, `_junction_items`,
  `_open_circle_items`, `_ghost`, or `_wire_preview`).
- **`NoIndex` item method.** The scene uses `QGraphicsScene.NoIndex` rather than
  the default BSP tree. The BSP index *defers* item removal, which would let a
  freed item linger in the index until the next paint; `NoIndex` keeps the item
  list consistent synchronously with `removeItem`. (This class of frequently
  mutated scene gains nothing from the BSP index anyway.)

These invariants are guarded by `test_no_index_method` (deterministic), by a
randomized paint-after-every-mutation fuzz test (probabilistic — a use-after-free
only faults nondeterministically, so it cannot be checked deterministically
without a native memory sanitizer), and by the structural-guard tests in
`test_scene.py` (`test_push_during_grab_ungrabs_before_removal`,
`test_remove_item_ungrabs_grabbing_child`,
`test_remove_item_keeps_unrelated_grab`, `test_reentrant_rebuild_coalesces`,
`test_push_during_rebuild_signal_is_safe`). See §13.

---

## 7. Code Generation

### 7.1 Output Format

The code generator produces a self-contained `circuitikz` environment:

```latex
\begin{circuitikz}
  \draw
    % wires and components
  ;
\end{circuitikz}
```

### 7.2 Mapping Rules

#### Document label styles (voltage / current)

At the top of the `circuitikz` environment, `generate()` emits a picture-scoped
`\ctikzset{voltage=european}` and/or `current=european` **only for the
non-default (european) values** of the schematic's `voltage_style`/`current_style`
(§4). So an american document (the default) produces byte-for-byte the same
output as before this feature, and a european document carries its own style
inside the source — making the `.tex` snippet self-contained regardless of the
host document's preamble. (The standalone preview template still sets
`voltage=american, current=american` globally; the picture-scoped setting
overrides it per figure.)

#### Two-Terminal Components (R, C, L, D, sources)

Each two-terminal component with origin at `(x0, y0)` and terminal pin at `(x1, y1)` (after rotation) maps to:

```latex
(x0, y0) to[KIND, LABELS] (x1, y1)
```

Where `OPTIONS` is the component's options string, e.g.:

```latex
(0,0) to[R, l=$R_1$, v=$V_R$] (2,0)
```

**Mirror.** A mirrored component (`Component.mirror`) is the canvas Flip-X —
`scale(-1, 1)` applied **after** rotation, i.e. a global horizontal flip of the
already-rotated component (the canvas `QTransform` applies `scale` outermost; see
§4.2). The far terminal is therefore the **rotated** span with its world *x*
negated about the component origin — the same `rotate-then-mirror` order used by
`component_pin_positions` and `local_span_to_world`. Consequences for a bipole:

- *Rotation ≡ 0 (mod 180)* — the bipole lies along the Flip-X direction, so the
  far terminal flips across the origin (the span reverses).
- *Rotation ≡ 90 (mod 180)* — the bipole lies **on** the Flip-X axis, so both
  terminals stay on their grid cells (the span is unchanged from the unmirrored
  component). This is what keeps a mirrored vertical bipole — e.g. the boost
  converter's load resistor — attached to its wires instead of detaching when the
  far terminal flips to the wrong side.

`to[…]` emits the bipole between those two terminal coordinates (in their natural
order) plus the CircuiTikZ `mirror` key, which supplies the reflection
*perpendicular* to the axis so off-axis features (an LED's emission arrows, a
voltage label's side) land where the canvas Flip-X puts them at every rotation.
The key is emitted only when `mirror` is set; unmirrored output is unchanged.
(Multi-terminal nodes flip via `xscale=-1` instead, §7.2.)

**Comma protection.** The options string is mostly passed through verbatim, but
each `key=value` label slot whose *value* contains a comma is brace-wrapped
(`v=$\phi(0,0)$` → `v={$\phi(0,0)$}`) by `protect_label_commas()` (in
`app/components/style.py`). TikZ's pgfkeys parser splits the `to[]`/`node[]`
option list on commas and — unlike the canvas parser — does **not** treat
`$...$` as protecting them, so an unwrapped comma inside a math label would be
read as a bogus key and fail compilation. Values already enclosed in a single
`{...}` group are left untouched. The shared `split_top_level()` splitter
(commas not inside `$...$`/`{...}` or escaped) is the single source of truth for
this, used by both the code generator and the canvas label parser (§5.8).

**Brace balancing (TeX-injection containment).** Every user text the generator
emits inside a `{…}` brace group — the options string, node/text content, wire
endpoint/mid labels, the bipole `t=` label, centred box text — is first passed
through `balance_braces()` (`app/components/style.py`): stray unmatched `}`
characters are neutralised so they cannot close the enclosing group early and
splice raw TeX into the rest of the document, and unmatched `{` are closed so
the group still compiles. Balanced (and escaped `\{`/`\}`) braces pass through
untouched, so legitimate LaTeX is unchanged. The options string is
brace-balanced *before* comma protection. The companion helper
`contains_dangerous_latex()` (same module) detects high-risk commands
(`\write18`, `\input`, `\include`, `\openout`, …) and powers the load-time
warning (§10.1); it is detection only — labels are never rewritten beyond
brace balancing. Exported documents and snippets additionally carry a security
header comment (§8.4/§8.5). Covered by `tests/test_latex_security.py` and the
containment cases in `tests/test_codegen.py`.

**Diode scale.** When the schematic contains any diode-family component
(`D`/`zD`/`sD`/`tD`/`zzD`/`leD`), the generator emits a picture-scoped
`\ctikzset{diodes/scale=0.8}` as the first line inside `\begin{circuitikz}` (the
factor is `DIODE_SYMBOL_SCALE` in `app/components/library.py` — single-sourced
there, like the kind→geometry mapping `geometry_key()`, so the canvas symbol
geometry and the emitted LaTeX can never disagree). This shrinks CircuiTikZ's oversized default
diode to match the canvas SVGs (§5.3) and the user's exported snippet alike;
being inside the environment's group it never leaks into the user's other
figures. The line is omitted entirely when no diode is present.

#### Multi-Terminal Components (Tripoles)

These map to CircuiTikZ `node` syntax, placed by a named anchor rather than
the node center:

```latex
(pin_x, pin_y) node[KIND, anchor=ANCHOR, OPTS] (NODEID) {LABEL}
```

Where `(pin_x, pin_y)` is the absolute coordinate of the registry pin
corresponding to `ANCHOR`, and `NODEID` is `node_<first8charsofUUID>`.

CircuiTikZ's internal pin anchors do not land on the 0.25-GU grid, so the node is
placed by its origin pin (`anchor=…`, or by centre for the op amp) and aligned
with a **computed** per-axis scale (`xscale=`/`yscale=`) and/or short bridge
leads — both derived from the measured anchors and stored in
`components/definitions.json` (see [`spec/component-editor.md`](spec/component-editor.md)
§4). The canvas geometry carries the same scale + leads, so the two agree.
BJTs/MOSFETs are scaled onto the grid; the op amp extends leads to its outward
pins.

```latex
% op amp (placed by centre): clean leads bridge every terminal to its grid pin
(node_id.+) -- (pin_plus_coord)
(node_id.-) -- (pin_minus_coord)
(node_id.out) -- (pin_out_coord)
% npn (placed anchor=B): scaled so C/E land on grid — no leads
(0,1) node[npn, xscale=1.1905, yscale=1.2987, anchor=B] (node_id) {}
% nigfete (placed anchor=gate): scaled, plus a short residual lead for the source
(0,1) node[nigfete, xscale=1.0204, yscale=0.962, anchor=gate] (node_id) {}
(node_id.source) -- (source_coord)
```

#### Named Anchor References

Wire endpoints and two-terminal component terminals that coincide with a
multi-terminal pin are rendered as named anchor references instead of bare
coordinates:

```latex
(78.5,80) to[R] (node_abc123.gate)
(node_abc123.out) -- (node_def456.+)
```

This makes connections explicit and produces cleaner output. **Scaled logic gates** are no exception: a scaled gate's pins sit at the true `.in k`/`.out` anchor (sized via the body height/xscale so the anchor lands at `base × scale`, §5.4), so a wire ending on such a pin is emitted as the named `(node.in k)` reference like any other multi-terminal pin — there is no lead stub and no off-grid/snapped mismatch.

#### Wires

Each wire maps to a bare `\draw` path. The wire's point list is **simplified**
(consecutive duplicates and redundant collinear vertices removed) before output,
so a straight run is always emitted as a single segment:

```latex
(x0, y0) -- (x1, y1) -- ... -- (xn, yn)
```

A wire with a non-default style (§4.3) or a custom endpoint marker is instead
emitted as its **own** `\draw[<spec>] (…) -- (…);` statement. The option `<spec>`
is built as the arrow specification first, followed by the style options
(`compose_style_options`). The arrow spec is an `arrows.meta` form
`{<start-tip>}-{<end-tip>}` where each tip comes from the marker kind
(`arrow`→`Latex`, `stealth`→`Stealth`, `open`→`Latex[open]`, `bar`→`Bar`); an
absent marker omits its tip, so an end-only arrow is `-{Latex}` and a start-only
one is `{Latex}-`. The end tip lands on the last point and the start tip on the
first; `arrows.meta` tips auto-orient to point outward. The library is loaded by
the export template (§8.4) and listed in the snippet preamble (§8.5).

A non-empty `mid_label` (§4.3) additionally emits `\node[fill=white, inner sep=1pt]
at (x,y) {…};` at the point `wire_point_at_fraction(points, mid_label_pos)` — after
the wire draw, so the opaque fill paints over the line behind the text.

### 7.3 Coordinate Output

- Coordinates are output as decimal numbers rounded to 2 decimal places.
- If all coordinates are integers or half-integers, they are output without trailing zeros (e.g., `2` not `2.00`, `1.5` not `1.50`).
- **Normalisation toward the origin.** Before emission, `generate()` translates the schematic so its drawn bounding box starts near `(0,0)` (`_translate_to_origin`). The canvas places schematics in the middle of a large scene, so stored coordinates are typically offset by tens of GU; this shift makes the generated source start near the origin and far more readable, with no change to the rendered figure (CircuiTikZ crops to the bounding box). The shift is `floor(min)` over all component pin positions and wire vertices — a **whole number of GU**, so grid alignment is preserved exactly — applied only to *absolute* coordinates (component `position`, wire `points`); *relative* values (`span_override`, `label_offset`, pin offsets) are unchanged. A schematic already at the origin is emitted unchanged. The translation is internal to codegen: the saved `.hv` file and the on-canvas coordinates are untouched.

### 7.4 Node Labels

- The CircuiTikZ node name for a multi-terminal component is derived from its UUID prefix: `node_<first8charsofUUID>`.
- Wire endpoints that connect to named pins reference these node names.

### 7.5 Code Generator Interface

```python
def generate(schematic: Schematic) -> str:
    """
    Pure function. Takes a Schematic and returns a CircuiTikZ string.
    Raises ValueError if the schematic violates any invariant.
    """
```

No side effects. No global state. The same `Schematic` always produces the same output.

### 7.6 Junction and Open-Endpoint Nodes

After the `\draw` path, the generator emits standalone node statements — first
junction dots, then open-endpoint circles — before `\end{circuitikz}`.

**Junction dots** — one per coordinate with degree ≥ 3 (§6.4):

```latex
\node[circ] at (x, y) {};
```

**Open-endpoint circles** — one per wire endpoint not coinciding with any
*connecting* component pin (§6.4). Pins of `NON_CONNECTING_KINDS` (the `open`
voltage annotation, which renders as `to[open]` — an open circuit) do **not**
connect a wire end, so a wire that only touches one stays open:

```latex
\node[ocirc] at (x, y) {};
```

**Degenerate wires** (fewer than two points) have no segment and connect
nothing: connectivity helpers (`open_endpoints`, `unconnected_pins`,
`junction_points`) ignore them, and `generate()` skips them so they emit no stray
lone coordinate in the `\draw` path. (Such a single-point wire previously made a
real endpoint at the same coordinate look connected, hiding its circle.)

Both sets of nodes are placed after the path's terminating `;`. Coordinates use
the same formatting rules as §7.3. Both sets are **derived** from the schematic
geometry at generation time — they are not stored in the model.

**Unconnected-pin circles** (optional) — when `generate()` is called with
`mark_unconnected_pins=True`, an additional `\node[ocirc]` is emitted at every
*component pin* that nothing connects to (`unconnected_pins()` in
`app/schematic/model.py`: a pin with no wire vertex on its coordinate and no
second connecting component pin sharing it). Pins of `NON_CONNECTING_KINDS` —
currently the `open` **voltage annotation**, which renders as `to[open]` (an
open circuit that draws nothing) — are ignored entirely: they form no
connection, so they neither suppress a real pin's circle nor get one of their
own. The current annotation `short` is a real closed wire and is *not* in this
set, so it does connect. This is the pin-side counterpart of the open-endpoint
circles above, and the two sets are disjoint by construction. The
flag is driven by the **Mark unconnected component pins** display preference
(§10.8) and defaults to `False`, so output is unchanged unless requested. All
call sites that should honor the preference (source panel, preview compilation,
and the PDF/EPS/TeX exports) pass the current preference value through. The Qt
canvas mirrors the same markers — see §10.5.

**Line-hops** (optional) — when `generate()` is called with `mark_line_hops=True`, the hopping wire's `--` path is split at each crossing returned by `wire_crossings()` (§6.4) and a small semicircular bump is inserted there as a single cubic Bézier — `… -- (p0) .. controls (c1) and (c2) .. (p3) -- …` — sized by `HOP_RADIUS_GU` (the same GU constant the canvas uses, so the exported and on-screen arcs match). All bump coordinates pass through the same `_y` Y-flip as the rest of the figure, so the bump flips with it (no arc-angle sign juggling). The flag is driven by the **Draw line-hops** display preference (§10.8), defaults to `False` at the `generate()` layer (the app passes the preference, which defaults on), so output is unchanged unless requested.

**Wire z-layering** — wires participate in the same z-order layering as drawing annotations (§7.7): a wire with `z_order < 0` is emitted as its own `\draw` statement in the **background** block (before the shared path, interleaved with `DrawingComponent`s by ascending z-order), `z_order > 0` in the **foreground** block (after), and `z_order == 0` stays in the shared `\draw` path / per-wire styled statement as before — so default wires' output is unchanged. **Background wires use absolute coordinates, not named anchors.** A multi-terminal node (op amp, MOSFET, BJT) is defined in the main `\draw` block, so a background wire ending on one of its pins must **not** emit the named-anchor reference (e.g. `(node_abc.gate)`) — that would point at a node defined *later*, a LaTeX compile error. The background-layer emit therefore passes `pin_coord_to_ref=None` so those endpoints fall back to bare coordinates; the registry pin coords already connect exactly via the node's scale / bridge leads (see the codegen module docstring), so there is no geometric loss. Foreground wires come after the node definitions and keep named anchors.

### 7.7 Drawing Annotation Commands

After junction and open-endpoint nodes, the generator emits standalone commands for drawing annotations (`text_node`, `rect`, `circle`). These produce nothing inside the `\draw` path block — `_component_lines()` returns `[]` for drawing kinds.

**Text nodes** (`text_node`):

```latex
\node[font=\fontsize{SIZE}{LEADING}\selectfont, rotate=R] at (x,y) {text};
% when span_override is None and rotation is 0:
\node at (x,y) {text};
```

`LEADING` = `SIZE × 1.2`, formatted with the same rules as §7.3. `rotate=R` is emitted only when `Component.rotation != 0`; when both font and rotate options are present they appear together in the same `[…]` option list.

**Rotation direction:** `Component.rotation` stores the angle as Qt applies it on the canvas. TikZ `rotate=` uses the opposite convention (CCW, standard math), so the emitted angle is `(-rotation) % 360`. For example, `Component.rotation=90` → `rotate=270`.

**Rectangles** (`rect`):

```latex
\draw[STYLE] (x1,y1) rectangle (x2,y2);
% when the style is default (solid):
\draw (x1,y1) rectangle (x2,y2);
% when the rect has centred text, a second node follows at the box centre:
\draw (0,0) rectangle (4,2);
\node at (2,1) {$H(s)$};
```

`(x2,y2) = (x1+dx, y1+dy)` where `(dx,dy)` is `span_override` when set, or `default_span = (2,2)` otherwise. `STYLE` is composed by `compose_style_options()` from `line_style`, the unified `line_width`, and `fill_color` — the bracket is omitted when all are default. When `Component.options` (the centred text) is non-empty, `_centered_text_line` appends a separate `\node[font=…] at (cx,cy) {text};` centred on the rectangle (`(cx,cy) = (x1+dx/2, y1+dy/2)`); the font option reuses `_text_node_line`'s composition (omitted at the default 12 pt / no styling), and a text-free rect emits only the `\draw` line. The `y_flip` transform applies to all coordinates.

**Circles** (`circle`):

```latex
\draw[STYLE] (cx,cy) circle (r);
% non-square bounding box → ellipse:
\draw[STYLE] (cx,cy) ellipse (rx and ry);
% with centred text, a node follows (shared with rect):
\draw (1,1) circle (1);
\node at (1,1) {$\Sigma$};
```

Centre `(cx,cy) = (x0+dx/2, y0+dy/2)`, radii `rx = |dx|/2`, `ry = |dy|/2` (`_circle_line`). `circle (r)` is emitted when `rx == ry`, otherwise `ellipse (rx and ry)`. `STYLE` and the centred text `\node` work exactly as for rect (the latter via the shared `_centered_text_line`).

---

## 8. Preview and Export

### 8.1 Full Schematic Preview

A rendered PDF preview of the complete schematic is produced by:

1. Wrapping the generated CircuiTikZ in a minimal `.tex` document.
2. Running `pdflatex` in a temporary directory via `subprocess`. The argument
   vector is always passed as a list (never `shell=True`) and includes
   `-no-shell-escape`: because a `.hv` file may originate from an untrusted third
   party and label/text fields are emitted verbatim into the generated LaTeX,
   disabling shell-escape explicitly guarantees a crafted label can never invoke
   `\write18` / external commands, independent of the local TeX installation's
   default. Covered by `tests/test_latex_security.py`.
3. Rendering the output PDF page to a `QImage` with Qt's own PDF engine
   (`PySide6.QtPdf.QPdfDocument`) — `pdf_to_qimage()` loads the PDF bytes from an
   in-memory `QBuffer` and renders page 0 at the worker's DPI. No external
   process and no Poppler dependency. (The `QByteArray` backing the buffer is
   held in a local for the buffer's lifetime — `QBuffer` references it without
   copying.)
4. Displaying the image in the preview panel.

The preview is triggered by:
- A **Compile** button (toolbar)
- Automatically, with a 500 ms debounce (`_SCHEMATIC_DEBOUNCE_MS`), after any schematic edit. The short delay is practical because the render step is now Qt-native (QtPdf, §8.4) and turns around quickly; the debounce only needs to coalesce a burst of rapid edits.

Compilation runs in a `QThread` (`PreviewWorker`). The main thread is never blocked. While compiling, a spinner is shown in the preview panel. If `pdflatex` returns a non-zero exit code, the error log is shown in the preview panel in place of the image. An exception raised while *refreshing* the source/preview (e.g. generation failing on an invalid intermediate state) is reported in the **status bar** ("Preview update failed: …") rather than swallowed silently.

The worker thread is always stopped before the application exits: `PreviewWorker` connects its (idempotent) `shutdown()` to `QApplication.aboutToQuit`, and the main window's `closeEvent` also calls it. This covers every exit path (window close, `app.quit()`, or a teardown that bypasses the window) and prevents Qt's "QThread destroyed while still running" abort.

### 8.2 Equation Preview

The Properties Panel does not provide per-field equation previews. Component annotations are rendered as typeset math directly on the canvas (§5.8); the full schematic preview (§8.1) remains the authoritative rendered view of the complete diagram.

### 8.3 Options Label Auto-Placement (legacy)

> **Superseded by §5.8.** Labels now render as per-slot vector math auto-placed on conventional sides of the body; they are not draggable and do not use `label_offset`. The legacy single-label auto-placement below still runs in the scene (it sets `label_offset` via a bundled `MoveOptionsLabelCommand`) for file/undo back-compat, but the canvas ignores the result.

When a component's options string transitions from empty to non-empty for the first time (i.e. `Component.label_offset` is still `None`), the scene runs an auto-placement pass before committing the `EditCommand`. The algorithm:

1. Builds eight candidate positions (above-centre, right-middle, below-centre, left-middle, and the four diagonal corners) at a fixed clearance distance from the component bbox in component-local pixel coordinates.
2. Maps each candidate label rect to scene coordinates and scores it by total overlap area with every other component's bounding box.
3. Selects the lowest-overlap candidate, preferring above-centre when there is a tie.
4. If the default above-centre position has zero overlap, no `label_offset` is set.
5. Otherwise the chosen offset is recorded via a `MoveOptionsLabelCommand` bundled with the `EditCommand` inside a `MacroCommand` so both are undone together.

### 8.4 LaTeX Template

The minimal template used for full schematic preview:

```latex
% Generated by Heaviside.
% SECURITY: label and text fields below are raw LaTeX taken verbatim from the
% schematic (.hv) file. If that file came from an untrusted source, compile
% WITHOUT shell-escape (the default; e.g. `pdflatex -no-shell-escape`) so a
% crafted label can never execute external commands via \write18.
\documentclass[border=4pt]{standalone}
\usepackage[american]{circuitikz}
\usetikzlibrary{arrows.meta}
\ctikzset{voltage=american, current=american, resistor=american}
\begin{document}
% CIRCUITIKZ_SOURCE
\end{document}
```

Every full generated document begins with the `_SECURITY_HEADER` comment block
shown above (and the snippet header, §8.5, carries the same warning): label and
text fields flow verbatim into the LaTeX, so anyone re-compiling the exported
source by hand must not enable shell-escape on a file from an untrusted source.
Heaviside's own compiles always pass `-no-shell-escape` (§8.1). The **dark
preview template** (`_SCHEMATIC_TEMPLATE_DARK`, used by `build_tex(dark=True)`
for the on-screen preview only, §10.1) adds a `\pagecolor`/`\color` pair whose
colours are **derived from the canvas dark palette**
(`app/canvas/style._DARK`: `COLOR_BACKGROUND` / `COLOR_NORMAL`) so the canvas
and the dark preview can never drift apart; exports always use the light
template.

`\usetikzlibrary{arrows.meta}` provides the named arrow tips (`Latex`,
`Stealth`, `Latex[open]`, `Bar`) used by wire endpoint markers (§4.3, §7.2). The
includable snippet (§8.5) lists the same library in its required-preamble
comment so host documents load it too.

The string `% CIRCUITIKZ_SOURCE` is replaced verbatim by the output of
`generate(schematic, y_flip=True)`.  Two conventions govern the two call sites:

- **Source panel** — calls `generate(schematic)` (Y-down, matching Qt canvas
  convention) so the source is human-readable and consistent with what the user
  sees on screen.
- **Preview compilation** — calls `generate(schematic, y_flip=True)` to negate
  all Y coordinates before passing the source to `build_tex()`.  Using
  `yscale=-1` on the TikZ environment would flip the visual output but leave
  path *directions* unchanged, causing polarised components (voltage sources,
  diodes, etc.) to render with inverted polarity markers.  Negating Y in the
  coordinates themselves corrects both orientation and path direction.
- **American style** — `\usepackage[american]{circuitikz}` plus
  `\ctikzset{voltage=american, current=american, resistor=american}` ensures
  component symbols match the canvas (§5.6).

The `border=4pt` option on `standalone` provides a small uniform margin.

### 8.5 Export to TeX

**File → Export to TeX…** (`Ctrl+E`) writes the schematic as an includable
CircuiTikZ `.tex` snippet, chosen via a save dialog (default filename derived
from the current document, defaulting to `untitled.tex`). The output is produced
by `build_snippet()` in `app/preview/latex.py`:

```latex
% CircuiTikZ schematic exported from Heaviside.
% Include in your document with \input{<this file>}.
% Your document preamble must contain:
%   \usepackage[american]{circuitikz}
%   \usetikzlibrary{arrows.meta}
%   \ctikzset{voltage=american, current=american, resistor=american}
% SECURITY: label and text fields below are raw LaTeX taken verbatim from the
% schematic (.hv) file. If that file came from an untrusted source, compile
% WITHOUT shell-escape (the default; e.g. `pdflatex -no-shell-escape`).
\begin{circuitikz}
  ...
\end{circuitikz}
```

The snippet is a bare `circuitikz` environment preceded by a comment listing the
preamble packages the host document must load, plus the same shell-escape
security warning the full document template carries (§8.4) — it deliberately omits
`\documentclass` and `\begin{document}` so it can be `\input` into an existing
document rather than compiled on its own. The source is generated with
`generate(schematic, y_flip=True)` (Y-up convention, like preview compilation in
§8.4) so the included figure renders in the same orientation as the canvas.

### 8.6 Export to PDF / EPS

**File → Export to PDF…**, **File → Export to EPS…**, and **File → Export to
SVG…** write a compiled image of the schematic, suitable for `\includegraphics`
in a LaTeX document (or any other consumer). All reuse the §8.1 compile pipeline:

1. `generate(schematic, y_flip=True)` → `build_tex()` → `compile_tex()` yields
   PDF bytes (run synchronously on the UI thread; the status bar shows
   "Compiling…").
2. **PDF export** writes those bytes directly.
3. **EPS export** converts them with `pdf_to_eps()`, which runs
   `pdftocairo -eps`. The `-eps` flag emits Encapsulated PostScript with a tight
   bounding box derived from the PDF crop box.
4. **SVG export** converts them with `pdf_to_svg()`, which runs
   `pdftocairo -svg` — the same Poppler tool as EPS, so SVG adds **no dependency**
   beyond the one EPS already requires. The standalone PDF is already cropped
   tight, so the SVG inherits that extent. Both share the `_pdf_to_vector` helper.

5. **PNG export** renders the compiled PDF to a `QImage` via `pdf_to_qimage`
   (QtPdf — no extra dependency) at the **PNG resolution** preference (default 300
   dpi, §10.8) and saves it with `QImage.save`.

Unlike the §8.5 `.tex` snippet, these formats require `pdflatex` to be available
at export time (and `pdftocairo` for EPS/SVG), but the result is a self-contained
image that does not need the host document to load `circuitikz`. Compile or
conversion failures are reported in a dialog (the `pdflatex` log is included for
compile errors) and leave no file behind. For a `pdflatex`/`lualatex` workflow,
PDF is the natural choice; EPS is for `latex`+`dvips` PostScript workflows; SVG
suits the web and vector editors (Inkscape, Illustrator); PNG is a portable
raster for slides/docs.

**Copy to clipboard.** **File ▸ Copy Figure as PNG** (`Ctrl+Shift+C`), and a Copy
PNG icon button in the LaTeX preview header (§10.5), place the compiled figure on
the clipboard as a raster image (`pdf_to_qimage` at the PNG-resolution preference →
`clipboard().setImage`) for pasting into slides/docs/chat. **Only PNG is offered**:
the common paste targets (Word, PowerPoint, Google Docs) rasterize a pasted figure
regardless of the source flavor, so the earlier Copy PDF / Copy SVG (with macOS
pasteboard-UTI vector flavors) were dropped as misleading — vector output stays
available via **Export** (PDF/EPS/SVG). The copied figure always uses the **light**
export template (`build_tex` without `dark`), so dark mode never affects what is
pasted. Needs `pdflatex`; failures report as for export.

### 8.7 Dependencies

- `pdflatex` is needed for the **PDF preview pane** (§8.1) and the **PDF/EPS/SVG image exports** (§8.6). Checked at startup (`check_dependencies`); a warning dialog is shown if not found.
- **Tool discovery.** Which binary runs for each external tool (`pdflatex`, `latex`, `dvisvgm`, `pdftocairo`) is resolved by `app/preview/tools.py`: an explicit **user-configured path** (Preferences → Tools, §10.8) wins when it points at a runnable file, otherwise the tool is looked up on the `PATH` (augmented on macOS with common install dirs a Finder/Dock launch would not inherit). The UI pushes the configured paths into `tools.set_tool_paths()` at startup and when preferences change; `tools` has no Qt dependency, so the codegen/preview modules never touch `QSettings`.
- **On-canvas equation labels need no LaTeX.** They render via the bundled, pure-Python **ziamath** engine when `latex`/`dvisvgm` are absent (§8.4); when those are present the higher-fidelity LaTeX engine is used instead. So drawing, typeset canvas labels, CircuiTikZ source generation, and the `.tex` export all work with no LaTeX install — only the PDF preview and image exports require it.
- The PDF preview is rendered by the `QtPdf` module that ships with PySide6 — no external process and no Poppler. There is no `pdf2image`/Poppler dependency for the preview.
- `pdftocairo` (Poppler) is required **only** for EPS and SVG export (§8.6). It is checked on demand in `pdf_to_eps`/`pdf_to_svg` (not at startup), so users who never export EPS or SVG are not warned about a missing Poppler.
- The `circuitikz` LaTeX package must be installed in the TeX distribution (for the preview and image exports).

---

## 9. File Format

### 9.1 Save Format

Schematics are saved as UTF-8 JSON files (no byte-order mark) with the extension `.hv`.

Saving **validates first**: `save()` runs `validate()` (§4.5) on the in-memory
document and raises `SchematicSaveError` — writing **nothing** — when it is
invalid, so a corrupted in-memory state can never overwrite a good file on
disk. Serialisation uses `allow_nan=False`, so a stray NaN/Infinity in a
numeric field also fails the save cleanly instead of producing a file the
loader would reject.

Saving is **atomic and durable**: the JSON is written to a sibling
**per-process** temporary file (`<name>.tmp.<pid>`, so two processes saving
the same path cannot collide), **fsync'd**, and then renamed over the target
via `os.replace`, so an interrupted or failed write never corrupts an existing
file (the temp file is removed on failure). Before the rename, an existing
destination is copied to a **`<name>.bak`** sibling (e.g. `circuit.hv.bak`) as
a one-deep backup of the file being replaced; the copy is best-effort and a
failure to write the `.bak` never blocks the save itself.

### 9.2 Schema

```json
{
  "version": "0.3",
  "name": "My Schematic",
  "config": {
    "voltage_style": "american",
    "current_style": "american"
  },
  "components": [
    {
      "id": "3f2a1b4c-...",
      "kind": "R",
      "position": [0.0, 0.0],
      "rotation": 0,
      "mirror": false,
      "options": "l=$R_1$, v=$V_R$"
    }
  ],
  "wires": [
    {
      "id": "9a8b7c6d-...",
      "points": [[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]]
    }
  ],
  "metadata": {}
}
```

The top-level **`config`** object (added in format 0.2) holds document-level
CircuiTikZ conventions: `voltage_style` and `current_style`, each `"american"`
or `"european"` (§4 / §7.2). A 0.1 file has no `config`; it loads with american
defaults, and an unrecognised style value is coerced to american rather than
failing the load. `save` always writes the object at the current version.

A component may also carry optional keys when non-default: `span_override`,
`z_order`, the unified `line_width` (stroke/outline width), drawing-style fields
(`fill_color`/`line_style`/font fields), and **`variants`** — a `{name: true}`
map of active boolean variants
(§5.4), e.g. `"variants": {"filled": true}`. Only active variants are written.
For back-compat the loader also reads the legacy top-level `filled` /
`body_diode` keys (written by pre-variants builds) into the variants map; the
saver writes only the `variants` map. This is an additive, back-compatible change
that does not bump the format version (§9.4).

**Kind stability across CircuiTikZ re-generations.** A component stores only its
`kind` string — never its geometry, pins, or bbox, all of which are looked up
fresh from the registry at load time. So re-running `components/generate_components.py`
against a new CircuiTikZ library is safe by construction: appearance/alignment
changes flow into old files automatically, and the `kind` (a separate field from
the CircuiTikZ `tikz` keyword) is the stable identifier. The *one* thing that can
break an old file is a **renamed/removed kind**; `_KIND_ALIASES` in `schematic/io.py`
(`{old_kind: current_kind}`, applied before the registry lookup) migrates those so
old files keep loading. An unmapped unknown kind is rejected with a clear
validation error (§9.3), never silently corrupted.

### 9.3 Validation on Load

On file load, the application:

1. Refuses an implausibly large file **before reading it**: anything over
   `_MAX_FILE_BYTES` (32 MB; a real schematic is a few hundred KB at most) is
   rejected via `stat()` so a hostile/corrupt file cannot exhaust memory.
2. Parses the JSON with the `NaN`/`Infinity`/`-Infinity` literals **rejected**
   (`parse_constant`); as belt-and-braces, every numeric field is additionally
   checked finite (`_finite`) during deserialisation — a non-finite coordinate
   would otherwise poison geometry math far from the load site.
3. Validates JSON schema structure. Every typed field rejects a
   wrong-typed value with a descriptive `SchematicLoadError` (never a raw
   `TypeError`/`ValueError`/`KeyError`). Type checks are strict where it
   matters: a boolean is **not** accepted for an integer field (e.g.
   `z_order`), while a `rotation` stored as an **integral float** (`90.0`) is
   accepted and normalised to `int` (a tolerance for hand-edited files).
4. Verifies the `version` field is a known file-format version (§9.4).
5. Validates all invariants listed in Section 4.5 — including that every wire
   has at least two points, so a degenerate wire in a corrupt file fails the
   load instead of crashing the canvas/codegen later.
6. On validation failure, shows an error dialog with a description of the first failing check and does not load the file.
7. After a successful load, the main window scans the document's label/text
   fields with `contains_dangerous_latex()` (§7.2) and shows a **warning**
   when any contains potentially dangerous LaTeX commands (`\write18`,
   `\input`, …) — the file still opens (labels are typeset with
   `-no-shell-escape` regardless, §8.1), but the user is told before
   re-compiling the export by hand (§10.1).

### 9.4 Versioning

The JSON `version` field is the **file-format version** (`_FORMAT_VERSION` in `schematic/io.py`), tracked **independently of both the application version and the spec version**. It changes *only* when the on-disk format changes — not on every app release — so it remains a reliable answer to the one question it exists for: "can this build read this file?" (Most app releases ship UI, component, or bug-fix changes that leave the format untouched, and such a release must not restamp saved files with a new format number.) The loader accepts any version in `_KNOWN_VERSIONS` (`{"0.1", "0.2", "0.3"}`); `save` always writes the **current** format version (`0.3`). The bumps are backward-compatible (older files still open with defaults for the missing fields) — `save` then re-stamps them at the current version. A file whose `version` is not recognised is rejected with a descriptive error that tells the user the file was likely saved by a newer release and to update Heaviside.

Format versions:

- **0.1** — the initial (pre-1.0) format. The on-disk shape is **not yet stable**: while the project is in its early (alpha) phase it may change between releases without migration support, so a `.hv` file is not guaranteed to load in a later version. There are no earlier formats, so the loader performs no migration; it validates the file against the current schema and rejects anything it does not recognise. Once the format stabilises it will be promoted to `1.0` and later changes will document migration rules.
- **0.2** — added the top-level `config` object (document voltage/current label styles, §9.2). A 0.1 file loads unchanged with american defaults.
- **0.3** — covers the optional wire/component fields accumulated since 0.2 (`start_marker`/`end_marker`, the endpoint/mid labels and placements, `hop_mode`, `z_order`, `line_width`, `scale`, `params`, `variants`, `span_override`), so an **older build that would silently strip them refuses the file** instead of corrupting it on its next save. 0.1/0.2 files load unchanged; new documents declare 0.3.

### 9.5 Bundled Examples

Example schematics live under `examples/` as `.hv` files, grouped into
**category sub-folders** (e.g. `examples/Battery Models/…`, `examples/Power
Electronics/…`). They are bundled into the app recursively, preserving that
structure (the PyInstaller spec adds `examples/**/*.hv` with each file's parent
as its destination; the co-located `.pdf`/`.eps` exports are regenerable and not
bundled or tracked). **File → Open Example ▸** mirrors the folders: each
sub-directory becomes a **category submenu** of its `.hv` files, and any `.hv`
placed directly in `examples/` is listed (uncategorised) at the top level
(resolved via `resource_path("examples")`, so it works from a source checkout and
when frozen). Selecting an example loads it as a *template*: it is opened and the
view is fit to it (§5), but `_current_path` is left unset so **Save** prompts for
a new location rather than overwriting the read-only bundled file. If no examples
are present the submenu shows a disabled placeholder.

Because the examples ship inside the app, they must always load under the
**current** file-format version (§9.4): a version bump that leaves them on an old
version would ship examples the app's own loader rejects. Whenever
`_FORMAT_VERSION` changes, every example under `examples/` must be re-saved to the
new version (load + `io.save`, which normalises the version). `tests/test_examples.py`
enforces this — it loads every bundled example and asserts each declares
`_FORMAT_VERSION` and generates without error.

---

## 10. UI Layout

### 10.1 Main Window

```
┌─────────────────────────────────────────────────────────────┐
│  Menu Bar: File | Edit | View | Tools | Help                │
├─────────────────────────────────────────────────────────────┤
│  Toolbar: New | Open | Save | | Undo | Redo | | Compile  …  ? │
├────┬─────────┬───────────────────────────┬──────────────────┤
│Tool│ Palette │                           │  Properties      │
│ ↖  │         │        Canvas             │  Panel           │
│ ⌁  │Resistors│   (QGraphicsView)         │                  │
│ ✋  │Diodes   │                           │  (context-       │
│    │Transist…│                           │   sensitive)     │
│    │Sources  ├──────────────┬────────────┴──────────────────┤
│    │ (full   │ Source Panel │  LaTeX Preview (draggable)     │
│    │ height) │ (CircuiTikZ) │                                │
├────┴─────────┴──────────────┴────────────────────────────────┤
│  Status bar: cursor coords | zoom level | compile status    │
└─────────────────────────────────────────────────────────────┘
```

The **palette spans the full window height** on the left; the canvas + properties
and, beneath them, the source/preview strip occupy the region to its right (so the
source and preview no longer run underneath the palette).

**Visual language.** A single flat theme unifies the chrome with the component
palette — defined as colour tokens + stylesheet fragments in `app/ui/theme.py`
(import the tokens; don't hard-code colours). The window background is the
active surface (`MainWindow` sets its palette `Window`/`WindowText` colours,
inherited by the central area, panels, splitter gaps, status bar). Both
**toolbars** carry a hairline divider (`theme.top_toolbar_qss` / `ribbon_qss`),
icons tinted `theme.ICON`, rounded soft-blue hovers (`theme.HOVER`), and the
active tool shown as a soft-blue fill (the one accent `theme.ACCENT`) rather than
the native highlight. **Form controls stay native and follow the colour scheme.** Form controls (line edits, spin boxes, combo boxes, checkboxes), dialogs, message boxes, tooltips, tab bars, native scrollbars, and the window background keep the platform-native look — restyling them via a stylesheet looked non-native (and a window-level stylesheet broke the palette-based window background). They follow light/dark **natively**: while the app tracks the OS appearance the OS drives them directly, and when the user **forces a mode** with the toolbar toggle — or launches with a saved Light/Dark override, which `__init__` now pins the same way — `MainWindow._apply_color_scheme` drives the application colour scheme via `QGuiApplication.styleHints().setColorScheme(Dark/Light)` (Qt 6.8+), so the native widgets re-render dark/light themselves. **Fallback when the platform ignores the request:** not every platform theme supports scheme forcing (Qt's `offscreen` platform — headless tests and the README screenshot job — and bare Linux sessions without a desktop theme); when `styleHints().colorScheme()` still disagrees after the set, `_apply_color_scheme` installs an explicit application palette built from the theme tokens (`_token_palette`: Window/Base/Button/Text/Highlight/Disabled roles from `SURFACE`/`SURFACE_ALT`/`BUTTON_BG`/`TEXT`/`ACCENT`/`ICON_MUTED`), and restores the captured pristine platform palette when the pin is released (System mode) or honoured again — previously the inspector sidebar stayed light in dark mode on such platforms. `_apply_window_palette` then re-snapshots the live `QApplication.palette()` (so native children inherit the right Base/Text/Button roles) and overrides only Window/WindowText with the chrome surface colour. Only the deliberately-flat chrome — toolbars, palette tiles, palette **search box**, side panels, and their scrollbars — is themed by scoped stylesheet/tokens, and the Copy PNG/PDF/SVG buttons set `theme.flat_button_qss()` directly (+ a pointer cursor). The inspector's header/section/hint labels carry a pinned colour and are re-inked on a swap (§10.3); plain field-row labels follow the palette.

**Theme — light / dark (follows the OS appearance, with a manual toggle).** The
chrome and the canvas each carry a **switchable two-palette** design:
`app/ui/theme.py` (chrome tokens) and `app/canvas/style.py` (canvas `COLOR_*`,
including `COLOR_BACKGROUND`, `COLOR_GRID*`, and the symbol/wire ink
`COLOR_NORMAL`) both expose `set_dark(on)` / `is_dark()` that rebind their
module-level tokens. Canvas items read the colours **module-qualified**
(`style.COLOR_*`) and re-read them on every repaint, so a swap takes effect on the
next `update()`. `MainWindow` resolves the OS appearance via
`QGuiApplication.styleHints().colorScheme()` (the `_system_is_dark` helper)
**before** building the UI, and stays in sync by connecting `colorSchemeChanged`.
A **theme radio group on the top toolbar** exposes **System / Light / Dark** as
three checkable buttons in an exclusive `QActionGroup` (`_theme_group`, keyed in
`_theme_actions`): all three are visible (a **monitor** for System, a **sun** for
Light, a **moon** for Dark) and exactly one is active at a time. Triggering a
button calls `_set_theme_mode` with that mode. To match the flat icon bar, the
three render as plain **flat `QToolButton`s** — identical to the other toolbar
buttons, auto-raised, sharing the toolbar's icon size and its standard soft-blue
`:checked` tint on the active one — inside a zero-spacing container
(`_theme_group_box`); the tight spacing (vs the toolbar's own 3px) is what reads
them as a group. The icons re-tint via the standard `_themed_icon` list, so a
theme change re-inks them automatically; `_sync_theme_action` only flips which is
checked. A **dotted vertical divider** (`_theme_divider`, object name
`toolbarDottedDivider`, styled by `theme.toolbar_dotted_divider_qss()` in the
muted-icon ink) separates the theme group from the help/bug buttons on its right,
distinct from the solid separators used elsewhere on the bar; it uses a vertical
`Expanding` size policy so it runs almost the full height of the toolbar, and is
re-styled on each theme change. The active mode is held in `_theme_mode`:

* **System** — `_follow_system = True`; the theme tracks the OS appearance live
  (`_on_color_scheme_changed` applies OS changes), and the application colour
  scheme is left `Unknown` so native widgets follow the OS directly.
* **Light** / **Dark** — pin the theme; `_apply_color_scheme` drives
  `QGuiApplication.styleHints().setColorScheme(Light/Dark)` so native widgets
  re-render to match, and OS changes are ignored.

`_set_theme_mode` applies a mode and **persists** it: `Preferences.dark_override`
stores `None` (System) / `"light"` / `"dark"`, re-read at the **next launch**
(`MainWindow.__init__` resolves it *before* building the UI). So the chosen mode —
including System — survives a relaunch. `_sync_theme_action` checks the button
matching the active mode (leaving the other two unchecked) so a programmatic mode
change keeps the group in sync.
`MainWindow._apply_theme` swaps both palettes,
re-applies the toolbar/ribbon stylesheets, re-tints the registered toolbar icons,
asks each side panel to `apply_theme()` (the palette also invalidates its
ink-coloured thumbnail cache and rebuilds the tile grids; the **properties
inspector** re-inks its header / section / hint labels — these carry an explicit
stylesheet that pins the colour, so `PropertiesPanel.apply_theme` restyles each by
object-name tag (`headerLabel`/`sectionLabel`/`hintLabel`); the palette's category
**card names** likewise carry pinned ink and are re-inked when the cards rebuild),
repaints the welcome screen and canvas, and sets the window palette. Labels with a
stylesheet do not follow the window palette, so any themed text needs an explicit
re-ink on a swap; plain field-row labels (no stylesheet) follow the palette and do
not. On macOS the **native** form
controls already follow the system appearance, so they are deliberately left
alone — **except** the two chrome controls that the OS would otherwise leave
light when the toolbar toggle forces a mode the OS isn't in: the **palette search
box** (themed via `theme.line_edit_qss()`) and the **canvas scrollbars** (the
`SchematicView` gets `theme.scrollbar_qss()`); both are re-applied on a swap. The
**grid dots** read `style.COLOR_GRID` (integer) / `COLOR_GRID_SUB` (0.25 GU minor)
— opaque, visibility-tuned values in *both* palettes (a too-faint dark grid was
the reason they are opaque rather than low-alpha). The **on-screen LaTeX preview
follows the theme** — in dark mode the
preview is recompiled with `build_tex(source, dark=True)`, which gives the
standalone document a dark `\pagecolor` and a light default `\color` (matching
the canvas palette), and the preview panel's background matches so the figure
blends in. This is the only consumer of the dark template: the preview worker
(`PreviewWorker.set_dark`) sets it, and `_apply_theme` recompiles a shown preview
on a swap. **All exports stay light** — `build_tex` defaults to the light
template and the PDF/EPS/SVG/PNG export path and `build_snippet` never pass
`dark`, so the distributed figure remains white-paper/black-ink regardless of the
UI theme.

**Tools menu.** **Tools ▸ Component Editor…** opens the standalone component
editor (`app/componenteditor/window.py`) — a developer tool for authoring/aligning
CircuiTikZ component symbols (it writes `components/definitions.json` +
`geometry.json`; see [`spec/component-editor.md`](spec/component-editor.md)). It
can also be launched independently with `python -m app.componenteditor`.
Because it renders/measures symbols via `latex` + `dvisvgm`, the **Tools menu is
shown only when that toolchain is on `PATH`** (`_component_editor_available`) — a
packaged end-user build, which ships no toolchain, hides it.

**Welcome screen.** The canvas slot is a `QStackedWidget`: page 0 is a painted
`_WelcomeScreen`, page 1 is the live `SchematicView`. Before any document is
active the welcome screen shows **only** the Heaviside unit step function H(t)
as a centred diagram, with one faint hint line pointing to the Help dialog
(*Help ▸ Keyboard Shortcuts & Gestures (F1)*). The screen is replaced (one-way)
by the live view as soon as a document is created/opened or component placement
begins.

**Help dialog.** The full shortcut and gesture reference lives in
`_HelpDialog`, opened from **Help ▸ Keyboard Shortcuts & Gestures** (shortcut
`F1`, `QKeySequence.HelpContents`) **or the right-aligned help (`?`) button on
the toolbar** (both trigger the shared `self._act_help` action). It is a
scrollable (`QScrollArea`) dialog with two titled sections — **Keyboard
Shortcuts** and **Mouse & Gestures** — each rendered as a `_RefTable` (a
read-only two-column `QTableWidget`: keys/gesture | description). The
**description column wraps** onto multiple lines and the table auto-sizes its
height to its content (its own scrollbars are off; `_RefTable.resizeEvent`
re-wraps and re-measures, pinning a fixed height so the outer scroll area
scrolls everything). Rows are grouped under full-width bold header bands, built
from the module-level `_HELP_SHORTCUT_GROUPS` / `_HELP_GESTURE_GROUPS` tables.
The shortcut groups are File, Edit, View, Tools & canvas, and a
*Tab — cycle the item under the cursor* group documenting each implemented
hover-cycle target (label position, endpoint marker, wire line style) plus
`Shift+Tab` reverse. The gesture groups are Selecting, Moving & resizing,
Wiring, Editing text, and Navigating. The descriptions are full sentences and
must stay in sync with the actual shortcuts (§10.6) and gestures (§6.4). The
**Help menu** also contains *Report a Bug…* and *About Heaviside* (`_AboutDialog`).

**Report a bug.** **Help ▸ Report a Bug…** and a **bug (🐞) toolbar button** next
to the help (`?`) button (both the shared `self._act_report_bug` action) open the
project's GitHub issues page (`_ISSUES_URL` =
`https://github.com/whileman133/Heaviside/issues`) in the user's default browser
via `QDesktopServices.openUrl`, so users can file a report without leaving the app.

**Save flow, modified state, and unsaved changes.** The window's modified state
**derives from the undo stack's save point** (§6.6): `_is_modified()` is
`undo_stack.is_modified()` (OR'd with a small manual-dirty flag for non-command
mutations), and `_mark_saved()` calls `mark_save_point()` after every successful
save/open/new — so **undoing back to the saved state clears the dirty marker**,
rather than any edit pinning it until the next save. The **Undo/Redo actions'
enabled state tracks the stack** (`can_undo()`/`can_redo()`, re-synced after
every command, undo, or redo), so the menu items grey out when there is nothing
to do. Any action that would discard a modified document (New, Open, Open
Example, OS "open with", window close) goes through `_confirm_discard()`, which
shows the standard **Save / Don't Save / Cancel** triad with **Save as the
default button**: *Save* runs the normal save (prompting for a path if needed —
a cancelled or failed save is treated as Cancel, so work is never silently
dropped), *Don't Save* discards, *Cancel* aborts the triggering action. Before
the prompt — and before every Save / Save As / export — the window calls
`_flush_inspector_edits()` so a pending debounced inspector edit lands in the
document first (§10.3). A `SchematicSaveError` from `save()` (an invalid
document, §9.1) is reported in an error dialog and the existing file is left
untouched. After a successful **Open**, the window warns if any label/text
field contains potentially dangerous LaTeX (`_warn_dangerous_latex`, §9.3).

**Crash guard.** `main.py` installs `sys.excepthook` and `threading.excepthook`
handlers (`_install_excepthooks`): an uncaught exception is **logged** —
appended with a timestamp to `heaviside-errors.log` in the user **app-data
directory** (`QStandardPaths.AppDataLocation`, falling back to the temp dir) —
echoed to stderr, and (on the GUI thread, once a `QApplication` exists)
reported in a dialog that names the log file. The handler deliberately does
**not** exit: the user keeps the session and can save their work. Every step of
the handler is individually guarded so it can never raise itself.

### 10.2 Component Palette

Left panel, fixed width `_PALETTE_WIDTH` (≈272px). A search box at the top
(focus with `Ctrl+/`), then a **scrolling region** holding the Categories /
active-category / search-results sections, and a **pinned bottom region**
holding "in use in document". The two regions **scroll independently** (each has
its own `QScrollArea`), so the placed kinds stay reachable at the bottom no
matter how far the categories are scrolled. The sections are
`_CollapsibleSection`s:

- **In use in document** — icon tiles for the distinct kinds already placed,
  ordered by category. Lives in the **pinned bottom panel** (a hairline divider
  above it; height capped at `_IN_USE_MAX_H` ≈ three tile rows, scrolling within
  itself past that). Rebuilt on `schematic_changed`; the whole bottom panel is
  hidden when the document is empty (or while a search is active).
- **Categories** — a **2-column grid** of cards (the split gate groups use the
  compact names "Gates (Am)" / "Gates (Eu)" so they fit). Each card shows the
  **actual symbol of a representative component** for that category (`_CATEGORY_REP` → rendered by
  `_category_pixmap`, so the icons always match the components rather than using a
  decorative stand-in), the category name, and a subtle right-aligned **letter**
  (its keyboard shortcut, no box). Clicking a card (or pressing its letter) makes
  it the **active** category (highlighted); category order follows §5.4. The
  palette refines the raw registry `category` via `_palette_category`: the raw
  **Logic** category splits into the boolean **Gates (Am)** / **Gates (Eu)** (by
  symbol style) and a **Logic** blocks group (flip-flops, mux/demux, ALU, adder),
  and the supply rails + batteries (`_SUPPLY_KINDS`) split out of **Sources** into
  a **Supplies** group (a palette-only grouping; the model `category` is unchanged).
- **&lt;active category&gt;** — the components in the active category; the first ten
  tiles carry a subtle 1–9/0 keyboard hint in the **top-right** corner.

Components are **icon-only tiles**: a thumbnail rendered from the component's own
`ComponentItem` (`_THUMB_SIZE`=48 in a `_TILE_SIZE`=64 tile, 3 columns — enlarged
for readability, cached), with the `display_name` + kind shown as a hover
**tooltip** rather than inline. Clicking a tile calls `scene.start_placement(kind)`
(enters **Place** mode). The scroll area uses the shared **`theme.scrollbar_qss`**
(a clean rounded handle, no arrow buttons) — needed because once a stylesheet is
active on a scroll widget Qt stops drawing its scrollbars natively. The same
style is applied to the source panel (§10.4) and to the **properties inspector**
scroll area — the inspector's body is made transparent (so it shows the tab pane,
not an opaque white box, §10.3), and a scoped stylesheet on a scroll widget
de-natives its scrollbars, so it uses the same clean themed scrollbar. (Its
**form controls** stay native and follow the colour scheme.) It also reserves a
right margin so the scrollbar doesn't cover the fields.

**Keyboard shortcuts** (`MainWindow.keyPressEvent` → `_handle_palette_shortcut`,
handled at the window level so it only fires for keys no focused child consumed —
text inputs keep their typing and the canvas keeps R/S/W/P while focused, with no
fragile focus checks): a **letter** (`_CATEGORY_LETTERS`, a unique mnemonic per
category) selects that category; **digits 1–9 / 0** place the 1st–10th component of
the active category via `place_active_index`. Modifier chords are ignored so they
never shadow menu accelerators.

A non-empty **search** replaces the categories/active/in-use sections with a flat
**Search results** grid of every component whose `kind` or `display_name` matches
(across all categories); clearing the box restores the normal view.

### 10.3 Properties Panel

- Right panel, fixed width ~250px, hosting **two tabs** (a **native** `QTabWidget`, no custom stylesheet): **Properties** (the per-object inspector described here) and **Document** (per-document settings, §10.9). With nothing selected the inspector surfaces the **Document** tab; selecting any object switches to **Properties** (`_on_selection_changed` sets the current tab). The tab bar, form controls, and scrollbar are **native** and follow the light/dark colour scheme (§10) — they are deliberately not restyled.
- **Properties tab:** header showing the `ComponentDef.display_name` and `kind`, followed by a vertical scroll area of **capability sections**. The scroll viewport/content are made **transparent** via object-name-scoped stylesheet rules (`#inspViewport`/`#inspContent`, so the transparency doesn't cascade onto the native form controls) so the body shows the tab pane like the Document tab, rather than the `QScrollArea`'s opaque `Base` fill (which on the native style read as a distinct white/inset box). A scoped stylesheet de-natives a scroll widget's scrollbars, so the inspector's scrollbar is themed with the shared `theme.scrollbar_qss()` (a clean rounded handle, like the source panel) and re-applied on a light/dark swap (`_apply_scroll_style`).
- Empty when nothing is selected.
- Selecting a single **wire** shows the wire inspector instead of the component sections (header "Wire").
- **Multi-select bulk edit.** When **several components** are selected (no wires), the panel binds editor sections that load values from the first component; **editing any field applies it to every selected component as one undo step**. The bind is via `InspectorSection.bind_multi`, and each write goes through `InspectorSection._apply`, which wraps the per-component commands in `scene.batch()` (one `MacroCommand`, §6). Two cases:
  - **Same kind** — every section that applies to the kind is shown (header `N × <name>`), so the full per-kind editor is available across the selection.
  - **Mixed kinds** (e.g. a resistor + a capacitor) — only the *shared*, kind-independent capability sections are shown (header `N items selected (M types — shared properties)`), and only those that apply to **every** selected component. A section is eligible iff its `multi_kind_safe` class flag is set: `FontSection`, `FillBorderSection`, `StrokeWidthSection`, `TransformSection`, `LayerSection`. Kind-specific sections (`OptionsSection`, `TextContentSection`, `BipoleLabelSection`, `VariantSection`, `ParamSection`) stay `multi_kind_safe = False` because their value (a free-text options/label string, or a kind-structural variant/param) has a different meaning per kind, so they bind only when the whole selection shares one kind.

  A mixed selection that shares **no** eligible section (e.g. a path symbol whose stroke/rotation apply, plus a `rect` for which neither does), or any selection that includes wires, falls back to a count-only view (`show_multi_select`).

**Architecture — capability sections.** The panel is composed of `InspectorSection` widgets rather than one monolithic panel per component type. Each section edits one capability and declares which components it `applies_to` (by `isinstance` against the model hierarchy and the `FontedComponent` / `StyledComponent` mixins). On selection the panel walks an ordered section list, `bind`-ing (showing) the sections that apply and `unbind`-ing (hiding) the rest; the first visible section's leading separator is suppressed. Adding a component type that combines existing capabilities needs no new panel — the sections compose. Section → applicability:

| Section | Applies to | Controls |
|---------|-----------|----------|
| `OptionsSection` | plain circuit (not `DrawingComponent`) | CircuiTikZ options field + slot hint |
| `TextContentSection` | `text_node`, `rect`, `circle` | text-content field (stored in `options`) |
| `BipoleLabelSection` | `bipole` | `t=` label field + other-options field + hint |
| `VariantSection` | any kind that declares variants in `definitions.json` | one checkbox per declared boolean variant (e.g. diode **Filled**, MOSFET **Body diode**, a transformer's four **polarity-dot** `dot` variants); auto-generated (uses the variant's `label` if present), `SetVariantCommand` |
| `ParamSection` | any parametric kind (`library.is_parametric`) | one spinbox per declared parameter — a logic gate's **Inputs** (`param`), a mux/demux's **Inputs**/**Outputs** + **Selects** (`params`); auto-generated, `SetParamCommand` per name |
| `FontSection` | `FontedComponent` (text_node, rect, circle, bipole) | size / bold / italic / family |
| `FillBorderSection` | `StyledComponent` (rect, circle, bipole) | line style, fill |
| `StrokeWidthSection` | every drawable kind except `text_node` (symbols **and** blocks) | the unified stroke/outline width (`line_width`, pt); `SetComponentLineWidthCommand` |
| `ScaleSection` | scalable kinds (`library.is_scalable` — logic gates **and** the digital blocks: flip-flops, mux/demux, ALU, adder) | **Size** dropdown (25 %–200 %; multiplies the symbol's baked alignment scale, pins at the true anchor) for `scale`; `SetComponentScaleCommand` (§5.4) |
| `TransformSection` | all but `rect`/`circle` (their rotation is a codegen no-op) | rotation buttons; mirror checkbox (circuit + bipole only) |
| `LayerSection` | `DrawingComponent` (text_node, rect, circle, bipole) | move front/back buttons + z-order spinbox |

`WireStyleSection` is a section for **wires** (not Components, so it is outside the component `applies_to` loop). When a single wire is selected, `PropertiesPanel.show_wire(wire_id)` unbinds the component sections and binds it via `bind_wire`. **Multi-wire bulk edit:** when **several wires** are selected (no components), `show_wires(wire_ids)` binds them via `bind_wires` (header `N wires selected`), loading the controls from the first wire; **editing any field applies it to every selected wire as one undo step** — each write goes through `WireStyleSection._apply_wires`, which wraps the per-wire scene setters in `scene.batch()` (one `MacroCommand`, §6). The panel header already reads **Wire**, so the section has no title of its own; its controls are grouped under bold sub-headers for clarity:

- **Line** — **Style** (solid/dashed/dotted/dash-dot) and **Width (pt)**.
- **Endpoint arrows** — **Start** / **End** marker combos (None/Arrow/Stealth/Open arrow/Bar).
- **Endpoint labels (text / $math$)** — **Start** and **End** rows, each a label text field with its **position** combo (Off end / Above-left / Below-right) **side-by-side on one row** (the text field gets the larger stretch); then a **Middle** text field.
- **Connection dots** — **No junction dots** and **No termination dots** checkboxes.
- **Layer** — **Move to front** / **Move to back** buttons (the shared `bring_to_front` / `send_to_back` scene methods, §5.4 — they span the combined wire + drawing-component z-stack), a **Z-order** spinbox (layers the wire and sets its hop priority, §4.3/§6.4), and a tri-state **Line hops** checkbox (`_HopModeCheckBox`) that cycles `hop_mode` on click: dash = default (follow the global preference and z-order), unchecked = never, checked = always (§6.4).

These write through `set_wire_line_style` / `set_wire_line_width` / `set_wire_start_marker` / `set_wire_end_marker` / `set_wire_start_label` / `set_wire_end_label` / `set_wire_start_label_placement` / `set_wire_end_label_placement` / `set_wire_mid_label` / `set_wire_no_junction_dots` / `set_wire_no_termination_dots` / `set_wire_z_order` / `set_wire_hop_mode` (the line-style combo/width spinbox debounce 300 ms; the checkboxes, marker combos, and placement combos commit immediately; the label fields commit on `editingFinished` — Enter or focus-out — *not* per keystroke, so a re-bind can't jerk the cursor mid-edit, and `bind_wires` additionally skips a label field that currently has focus). *Off end* placement sits the label beyond the endpoint; the side options tuck it beside the wire at the endpoint (above/below a horizontal wire, left/right of a vertical one). The endpoint markers are independent of the automatic junction/termination dots and exist mainly to draw block diagrams (the arrowhead, also Tab-cyclable on the canvas); the endpoint labels caption signal lines; the **Middle** field adds an over-the-wire mid-label (§4.3) that is then dragged/edited on the canvas (or edited by double-clicking the wire). Selection routing (`MainWindow`) queries both `selected_component_ids()` and `selected_wire_ids()` to choose single component / single wire / multi-component bulk edit / **multi-wire bulk edit** (`show_wires`) / count-only multi-select / empty.

All section edits funnel through `SchematicScene` methods that push undoable commands. Text/options fields and the fill/border controls debounce commits 300 ms; checkboxes, rotation, mirror, and z-order commit immediately.

**Pending debounced edits are flushed, never dropped.** Every section exposes
`flush_pending_edits()`, which commits a debounced edit whose timer is still
running; it is called when a section **unbinds/rebinds** (selection change),
and the panel-level `PropertiesPanel.flush_pending_edits()` is called by the
main window (`_flush_inspector_edits`) before **Save, Save As, every export,
and the unsaved-changes prompt** — so text typed just inside the debounce
window still lands in the saved file. Conversely, a **programmatic reload**
(re-binding after an external change) skips any field that currently has
focus, so a refresh can never clobber in-progress typing, and combo loads
preserve a hand-authored fill/line-style value that does not match a preset
instead of snapping it to the nearest entry. Each multi-edit write computes
its per-object commands against the **live** document state inside
`scene.batch()` (commands apply at push time, §6.6), and a composite that
fails part-way is unwound by `MacroCommand` rather than half-applied.

The bottom strip (height 260px) holds the source panel and preview panel side by
side in a horizontal `QSplitter`. Because the generated CircuiTikZ lines are
short, the preview gets the larger initial share of the width (initial sizes
≈ 440 / 840); the user can drag the handle to rebalance, and neither pane is
collapsible.

Both bottom-strip panels share a consistent **card** look: a rounded, bordered
frame (`theme.panel_frame_qss`) with a **header strip** — a padded title and a
hairline bottom divider (`theme.panel_header_qss` / `panel_title_qss`, header
object name `panelHeader`). The two headers use the **same fixed height**
(`_PreviewPanel._HEADER_H` = 30px) so their title bars line up. Both follow the
light/dark theme via `apply_theme`.

### 10.4 Source Panel

- Left pane of the bottom strip; card frame with a "CircuiTikZ Source" header.
- Read-only, frameless `QPlainTextEdit` showing the current generated CircuiTikZ
  source (the card supplies the border), in the platform's fixed-width font
  (`QFontDatabase.systemFont(FixedFont)` — requesting a literal "Monospace"
  family made Qt scan every installed font on platforms without one).
- Updates live (debounced 300ms) as the schematic changes.
- Syntax is not highlighted in v1.

### 10.5 Preview Panel

- Right pane of the bottom strip; card frame matching the source panel, resizable
  via the splitter (minimum width ~240px), and re-renders to fit on resize.
- Header: a "LaTeX Preview" title with a single **Copy PNG** flat **icon-only**
  `QToolButton` (`fa5s.image`, `theme.icon_button_qss`) inline on the right (§8.6).
- The image area uses the figure's page colour (`style.COLOR_BACKGROUND`) so the
  rendered schematic blends in; shows error text on compilation failure.
- The panel is always visible; content appears after first compile.

### 10.6 Keyboard Shortcuts

| Action | Shortcut |
|--------|----------|
| New schematic | `Ctrl+N` |
| Open | `Ctrl+O` |
| Save | `Ctrl+S` |
| Save As | `Ctrl+Shift+S` |
| Export to TeX | `Ctrl+E` |
| Undo | `Ctrl+Z` |
| Redo | `Ctrl+Shift+Z` |
| Copy | `Ctrl+C` |
| Paste | `Ctrl+V` |
| Delete | `Delete` / `Backspace` |
| Select All | `Ctrl+A` |
| Preferences | `Ctrl+,` |
| Select mode | `S` |
| Wire mode | `W` |
| Pan mode (persistent) | `P` |
| Rotate selection 90° CW | `R` |
| Focus palette search | `Ctrl+/` |
| Select component category (by its keycap letter) | letter key |
| Place 1st–10th component of the active category | `1`–`9` / `0` |
| Cycle, while hovering: endpoint-label position (over a label) / endpoint marker (over any endpoint) / line style (over the body) | `Tab` / `Shift+Tab` |
| Cancel / Select mode | `Escape` |
| Pan (transient) | `Space` + drag |
| Compile preview | `Ctrl+Return` |
| Fit to schematic | `Ctrl+0` |
| Zoom in / out | `Ctrl++` / `Ctrl+-` |
| Keyboard Shortcuts & Gestures (Help dialog) | `F1` |

### 10.7 Tool Ribbon

A narrow vertical ribbon toolbar is docked on the **left edge** of the window (Qt `LeftToolBarArea`), between the window edge and the component palette. It contains three exclusive checkable buttons:

| Button | Symbol | Mode | Shortcut |
|--------|--------|------|----------|
| Select | ↖ | Select | `S` / `Escape` |
| Wire | ⌁ | Wire | `W` |
| Pan | ✋ | Pan | `P` |

- The buttons form an exclusive group — exactly one is checked at all times (Place mode leaves the last-active tool highlighted).
- Clicking a button invokes the corresponding `enter_*_mode()` on the scene.
- The scene's `mode_changed` signal keeps the buttons in sync when mode changes originate from the keyboard.
- The ribbon is non-movable (cannot be dragged to another dock area).

### 10.8 Preferences

**Edit → Preferences…** (`Ctrl+,`) opens a modal `PreferencesDialog`
(`app/ui/preferences.py`). On macOS the action carries `QAction.PreferencesRole`
so Qt relocates it to the standard application menu. Settings are persisted via
`QSettings` (keyed by the organization/application names set in `main.py`) and
accessed through the typed `Preferences` wrapper rather than raw string keys.
The dialog reads current values on open and writes them back only on **OK**;
**Cancel** discards changes. Accepting the dialog refreshes the source panel and
recompiles the preview so a display change (e.g. marking unconnected pins) is
reflected immediately. Settings are organised into tabs — **Export**
(auto-export-on-save formats + PNG resolution), **Appearance** (Display and
Rendering options), **Tools** (external-tool paths), and **Updates** (the
startup update check, §11.2) — so the dialog stays compact as options grow.

Current settings:

| Setting | Key | Default | Effect |
|---------|-----|---------|--------|
| Auto-export TeX on save | `export/auto_tex_on_save` | **on** | After a successful save of `<name>.hv`, also write the includable `<name>.tex` snippet (§8.5) to the same directory. Pure Python — needs no LaTeX install. |
| Auto-export PDF on save | `export/auto_pdf_on_save` | off | After a successful save of `<name>.hv`, also write `<name>.pdf` to the same directory. |
| Auto-export EPS on save | `export/auto_eps_on_save` | off | After a successful save, also write `<name>.eps` to the same directory. |
| Auto-export SVG on save | `export/auto_svg_on_save` | **on** | After a successful save, also write `<name>.svg` to the same directory. Needs pdflatex + pdftocairo; a missing tool fails this export non-fatally (status bar), never the save. |
| Auto-export PNG on save | `export/auto_png_on_save` | **on** | After a successful save, also write `<name>.png` (rendered at the PNG-resolution preference) to the same directory. Needs pdflatex. |
| PNG resolution | `export/png_dpi` | **300** | Dots-per-inch for **Copy PNG** and **PNG export / auto-export** (300 dpi is publication grade). A spin box (72–1200) in the Auto-export group. The accessor **clamps the stored value to 72–1200 on read**, so a corrupt or hand-edited settings file cannot drive a pathological render size. |
| Mark unconnected component pins | `display/mark_unconnected_pins` | off | Draw an open circle at every component pin with no wire attached — on the **canvas**, and as `\node[ocirc]` in the preview, source panel, and exports (§7.6). |
| Draw line-hops | `display/line_hops` | **on** | Draw a small semicircular bump on the higher-`z_order` wire wherever two wires cross without connecting (§6.4) — on the **canvas**, and as a Bézier bump in the preview, source panel, and exports (§7.6). |
| Force built-in (ziamath) renderer | `render/force_ziamath` | off | Typeset on-canvas equation labels with the bundled pure-Python ziamath engine even when system LaTeX is present (§8.4). A debug aid; ziamath is used automatically anyway when LaTeX is absent. Toggling re-typesets existing labels (`retypeset_labels`). |
| Tool paths | `tools/pdflatex`, `tools/latex`, `tools/dvisvgm`, `tools/pdftocairo` | empty (auto-detect) | Explicit path to each external tool (§8.7). Blank = discover on `PATH`. A **Tools** group in the dialog provides a field + **Browse…** per tool with live status (found-on-PATH / will-use-this-path / not-found). Applied via `tools.set_tool_paths()` on accept, then the dependency check, label re-typeset, and recompile re-run. |

When any is enabled, `_do_save()` calls `_auto_export()`. The `.tex` snippet is
generated directly (`generate()` → `build_snippet()`, pure Python — **no LaTeX
install required**); the image formats share a **single** compile (reusing the
§8.6 pipeline), with the one PDF converted via `pdf_to_eps()` / `pdf_to_svg()` /
`pdf_to_qimage()` (PNG) as requested. This keeps an `\input{<name>.tex}` or `\includegraphics{<name>.pdf}`
(or `.eps`/`.svg`) in a LaTeX document in sync with the schematic without a manual
export step. Enabling only TeX auto-export therefore works with no `pdflatex` present.

Auto-export never blocks or aborts the save: it runs only *after* the `.hv`
is written, and any failure (invalid schematic, missing `pdflatex`/`pdftocairo`,
or a `pdflatex` error) is reported in the status bar only — not as a modal
dialog, which would be intrusive on every save; a failure in one format does
not block the others. The export runs on a **background worker**: at save time
the UI thread captures an immutable `_AutoExportJob` (the generated source,
target paths, and enabled formats — the worker never reads the live document),
which `_AutoExportTask` (a `QRunnable` on the global `QThreadPool`) executes
off-thread, delivering its status-bar message back via a queued signal
(`_AutoExportSignals.finished`). So **saving never freezes the UI** while
LaTeX runs. Jobs are **single-flight**: a save while an export is still
running queues (and replaces) one pending job, which runs when the current
one finishes — rapid saves coalesce instead of piling up compiles.

### 10.9 Document properties (inspector "Document" tab)

The **Document** tab of the inspector (§10.3, `DocumentPropertiesPanel`) edits the
*per-document* CircuiTikZ conventions — the **voltage** and **current** label
styles (american / european, §7.2) — as opposed to the app-wide Preferences
(§10.8). It **replaced the former modal Edit ▸ Document Settings… dialog**. Edits
apply **live and are undoable**: changing a combo pushes a
`SetDocumentPropertiesCommand` (§6.6) onto the scene's undo stack and emits
`DocumentPropertiesPanel.document_changed`, which the main window
(`_on_document_props_changed`) turns into `SchematicScene.relayout_annotations()`
(re-place the on-canvas ± signs / arrows for the new style) + `schematic_changed`
(mark modified, refresh the source panel and preview — the styles flow into
`generate()`). The panel reloads its combos from the document via `refresh()` on
New / Open / Open-Example. The settings persist in the `.hv` `config` object
(§9.2), so they travel with the document rather than the application.

---

## 11. Project Structure

```
heaviside/
├── main.py                        # Entry point; constructs QApplication and MainWindow
├── heaviside.spec                 # PyInstaller build spec (see §11.1)
├── scripts/
│   ├── build.py                   # Cross-platform PyInstaller build helper (+ .dmg on macOS)
│   ├── make_icons.py              # Regenerate assets/icon.ico + icon.icns from icon.png
│   ├── make_dmg_background.py     # Generate the macOS .dmg "drag to Applications" art
│   ├── make_dmg.py                # Build the macOS .dmg (dmgbuild + packaging/dmg_settings.py)
│   ├── make_installer.py          # Build the Windows installer (iscc + packaging/heaviside.iss)
│   └── render_screenshots.py      # Render the README example gallery (offscreen Qt → docs/images/examples/)
├── packaging/
│   ├── entitlements.plist         # macOS hardened-runtime entitlements (signing)
│   ├── dmg_settings.py            # dmgbuild layout for the drag-to-Applications image
│   └── heaviside.iss              # Inno Setup script for the Windows installer (§11.1)
├── components/
│   ├── generate_components.py     # Batch renderer CLI (→ geometry.json + definitions.json)
│   ├── geometry.json              # Generated symbol geometry (bundled runtime resource)
│   └── definitions.json            # Generated per-component registry/codegen data + origin_svg
├── examples/                      # Bundled example .hv schematics in category sub-folders (File → Open Example, §9.5)
├── app/
│   ├── resources.py               # resource_path(): frozen-safe bundled-file resolution
│   ├── update.py                  # Opt-out update notifier (GitHub Releases check, §11.2)
│   ├── canvas/
│   │   ├── scene.py               # SchematicScene(QGraphicsScene) + interaction state machine
│   │   ├── geometry.py            # Pure geometry helpers (snap/coord conversion, span
│   │   │                          #   rotation mapping, segment proximity) — no Qt scene state
│   │   ├── wiregeometry.py        # WireGeometry: wire snapping / hit-testing queries over
│   │   │                          #   the schematic (stateless; used by the scene)
│   │   ├── drag.py                # DragPreviewController: drag state + live drag previews
│   │   │                          #   (component move, vertex drag, endpoint resize)
│   │   ├── view.py                # SchematicView(QGraphicsView)
│   │   ├── items.py               # ComponentItem subclasses, WireItem, WirePreviewItem,
│   │   │                          #   JunctionItem, ITEM_CLASSES map
│   │   ├── svgsym.py              # SVG-geometry → QPainterPath symbol geometry loader
│   │   ├── style.py               # GRID_PX, LINE_W, PIN_R, LEAD_LEN, colors, and constants
│   │   └── commands.py            # Undo/redo command classes
│   ├── components/
│   │   ├── model.py               # ComponentDef, PinDef, Component (+ variants) dataclasses
│   │   ├── registry.py            # REGISTRY: bespoke literals + library-derived kinds
│   │   ├── library.py             # loads components/definitions.json → ComponentDefs,
│   │   │                          #   codegen tables, origin_svg, variant helpers
│   │   └── render.py              # render a symbol + measure pin anchors (latex/dvisvgm)
│   ├── componenteditor/           # Component editor (spec/component-editor.md)
│   │   ├── renderer.py            # Qt-free render/save core (shared with the CLI)
│   │   ├── draft.py               # editing model: validation + preview helpers
│   │   ├── window.py              # standalone Qt editor window
│   │   └── __main__.py            # python -m app.componenteditor
│   ├── schematic/
│   │   ├── model.py               # Component, Wire, Schematic dataclasses + geometry helpers
│   │   │                          #   (simplify_points, component_pin_positions,
│   │   │                          #    junction_points, wire_splits_at)
│   │   ├── io.py                  # save(schematic, path) and load(path) → Schematic
│   │   └── validate.py            # validate(schematic) → list[str] (error messages)
│   ├── codegen/
│   │   └── circuitikz.py          # generate(schematic) → str
│   ├── preview/
│   │   ├── worker.py              # PreviewWorker(QThread)
│   │   ├── tools.py               # external-tool path resolution (pdflatex/latex/dvisvgm/pdftocairo)
│   │   ├── mathrender.py          # on-canvas math labels → QPainterPath (latex or ziamath engine)
│   │   └── latex.py               # build_tex / build_snippet / pdf_to_eps / pdf_to_svg, helpers
│   └── ui/
│       ├── mainwindow.py          # MainWindow(QMainWindow)
│       ├── palette.py             # ComponentPalette(QWidget)
│       ├── properties.py          # PropertiesPanel(QWidget)
│       ├── theme.py               # shared design tokens + flat stylesheet fragments
│       ├── preferences.py         # Preferences (QSettings), PreferencesDialog
│       └── sourcepanel.py         # SourcePanel(QWidget)
└── tests/
    ├── test_model.py              # model + validation + geometry helpers (simplify,
    │                              #   junctions, splits, point_key)
    ├── test_validate.py           # validate() invariants exercised directly (wire ≥ 2 points)
    ├── test_transforms.py         # transform-consistency tripwire (rotate-then-mirror copies agree)
    ├── test_codegen.py            # code generation incl. junction \node[circ]
    ├── test_io.py
    ├── test_registry.py
    ├── test_commands.py           # undo/redo for all command classes
    ├── test_geometry.py           # pure canvas geometry helpers (no Qt scene)
    ├── test_wiregeometry.py       # WireGeometry snapping / hit-testing (no Qt scene)
    ├── test_scene.py              # SchematicScene/SchematicView interaction (offscreen Qt)
    ├── test_drag_parity.py        # drag-preview ghost geometry == committed result (offscreen Qt)
    ├── test_preferences.py        # Preferences (QSettings) + dialog
    ├── test_update.py             # update notifier: version compare + release selection + async
    ├── test_theme.py              # light/dark palette swap (canvas style + chrome theme)
    ├── test_palette.py            # component palette: cards, active category, search, in-use (offscreen Qt)
    ├── test_tools.py              # external-tool path resolution (override vs PATH)
    ├── test_mainwindow.py         # MainWindow auto-export + label re-typeset (offscreen Qt)
    ├── test_welcome.py            # welcome screen + Help dialog reference tables (offscreen Qt)
    ├── test_preview_render.py     # QtPdf preview rendering (offscreen Qt + pdflatex)
    ├── test_mathrender.py         # on-canvas math vector rendering + slot parsing (offscreen Qt; LaTeX render gated, ziamath/engine tests ungated)
    ├── test_svgsym.py             # symbol geometry incl. glyph (+/-) reconstruction
    ├── test_components_library.py # definitions.json → registry/codegen reconstruction
    ├── test_render.py             # symbol render + automatic anchor measurement (gated)
    ├── test_latex_security.py     # LaTeX-pipeline security: -no-shell-escape + no shell=True (mathrender, preview, component render)
    ├── test_componenteditor.py    # editor renderer/draft core + offscreen window smoke
    └── test_screenshots.py        # README example-gallery renderer: manifest/README sync + framed render (offscreen Qt)
```

Note: the `assets/components/` directory has been removed. All component rendering is handled programmatically via `ComponentItem.paint()`.

`scratch_canvas.py` was a temporary development harness used during Phases 5–8 and has been removed now that the full UI shell (Phase 9) is in place.

### 11.1 Packaging (PyInstaller)

The app ships as a self-contained bundle built with PyInstaller from
[`heaviside.spec`](heaviside.spec) via the cross-platform `scripts/build.py`
(`uv run python scripts/build.py`). Output is `dist/Heaviside.app` on macOS (a
proper `.app` bundle with the `.icns` icon and a `.hv` document-type
association), and `dist/Heaviside/` on Windows/Linux (the Windows `.exe` carries
the `.ico` icon). `build/` and `dist/` are git-ignored.

**Opening a `.hv` from the OS.** `main.py` opens a `.hv` given to it by the OS
file association: on **Windows** the installer's association invokes
`Heaviside.exe "<file>"`, so the path arrives as `argv` (`_schematic_arg`); on
**macOS** Finder delivers a `QFileOpenEvent`, routed by an application event
filter (`_FileOpenFilter`); on **Linux** the AppImage's `.desktop` entry
(`Exec=heaviside %f`) and its `.hv` MIME definition let a file manager pass the
path as `argv`, also handled by `_schematic_arg`. All call the shared
`MainWindow.load_path`. At launch there is nothing to discard; a runtime macOS
"open with" first guards unsaved work (`_confirm_discard`).

**Windows installer (`HeavisideSetup.exe`).** The Windows release artifact is an
**Inno Setup** installer ([`packaging/heaviside.iss`](packaging/heaviside.iss),
built by `scripts/make_installer.py`, which runs `iscc` with the version/paths
passed on the command line) — wrapping the `dist/Heaviside/` onedir build. It
installs per-user (no UAC), adds Start Menu / optional Desktop shortcuts and an
uninstaller (Add or remove programs), and registers the `.hv` association
(`HKA\Software\Classes`, open command `"{app}\Heaviside.exe" "%1"`). The portable
`.zip` is still published alongside it. The installer and `.exe` are
Authenticode-signed in CI when the `WINDOWS_CERT_*` secrets are set, mirroring the
conditional macOS signing (§11 / `docs/releasing.md`).

**macOS disk image (`.dmg`).** The macOS release artifact is a
drag-to-Applications **`.dmg`**, not a bare zip — the conventional install UX,
and it nudges the user to move the app to `/Applications` (which avoids macOS
App Translocation running it from a read-only random mount). `scripts/make_dmg.py`
builds it from the (signed) `dist/Heaviside.app` using `dmgbuild` and
`packaging/dmg_settings.py`; `scripts/make_dmg_background.py` (Pillow) generates
the "drag here →" background art (committed PNGs at 1x/@2x, combined into a
hi-DPI `.tiff` via `tiffutil`). `scripts/build.py` calls this automatically on
macOS when `dmgbuild` is available; the release workflow signs the app, builds
the `.dmg`, then signs/notarizes/staples the **`.dmg`** as one unit (`dmgbuild`
is installed ad-hoc there, so it is not a project dependency and the locked
environment stays frozen).

**Linux AppImage (`Heaviside-linux-x86_64.AppImage`).** The Linux release artifact
is a self-contained **AppImage** — a single no-root, run-anywhere file, the Linux
analogue of the `.dmg`/installer — built by `scripts/make_appimage.py` from the
`dist/Heaviside/` onedir build. AppImage (unlike Flatpak/Snap) is **not**
sandboxed, so the app keeps direct access to the user's system `pdflatex`. The
script assembles an **AppDir** — the onedir under `usr/bin/Heaviside/`; an `AppRun`
that execs the bundled binary forwarding `argv`; a 256×256 icon rendered from
`assets/icon.png` (Pillow); and the freedesktop integration files
[`packaging/heaviside.desktop`](packaging/heaviside.desktop) (a Graphics/Science
menu entry, `Exec=heaviside %f`), [`packaging/heaviside-mime.xml`](packaging/heaviside-mime.xml)
(the `application/x-heaviside` MIME type, glob `*.hv`), and
[`packaging/heaviside.appdata.xml`](packaging/heaviside.appdata.xml) (AppStream
metainfo, component id `com.heaviside.editor`) — then runs `appimagetool` on it.
The portable `.tar.gz` is still published alongside it. `scripts/build.py` calls
this automatically on Linux when `appimagetool` is on `PATH` (else it prints a
hint and leaves the onedir); the release workflow fetches `appimagetool` ad-hoc
and sets `APPIMAGE_EXTRACT_AND_RUN=1` so it runs without FUSE on the runner. No
Linux code-signing.

**App icon.** The single source is `assets/icon.png`. `scripts/make_icons.py`
(Pillow only, so it runs on any platform — no macOS `iconutil`/`sips`) generates
both `assets/icon.ico` (Windows) and `assets/icon.icns` (macOS) from it;
`heaviside.spec` selects the right format per platform. `build.py` regenerates
both automatically when either is missing or older than the PNG, so updating the
icon is just: replace `icon.png`, rebuild. The source PNG need not be square — it
is padded onto a transparent square canvas first, so the icon is never distorted.
(After replacing the icon you may need to clear the OS icon cache — relaunch the
Dock on macOS, or note Windows caches `.exe` icons — to see the change on an
already-seen bundle.)

**README gallery screenshots.** The README's 2×2 example gallery
(`docs/images/examples/*.png`) is generated, not hand-captured.
`scripts/render_screenshots.py` opens four bundled examples (Boost Converter,
4:1 MUX, ESC Cell Model, Porous Electrode Interface) in the real `MainWindow`
under Qt's offscreen platform — palette, canvas, inspector, and the live
CircuiTikZ source/PDF preview — two on the light theme and two on the dark
one, then grabs the whole 1600×1000 window. Before each grab it waits for the
async work to settle: the math-label pipeline drains (the dispatcher's
callback map empties and the pool idles) and, when a `pdflatex` is available,
the preview worker reports ready/error; then the view is fit to the
schematic. Importing the script is side-effect-free (tests read the `SHOTS`
manifest); `main()`'s bootstrap redirects QSettings to a throwaway directory
(the developer's real preferences are neither read nor written), disables the
startup update check, and stubs `check_dependencies` — both produce modal
dialogs that would block an offscreen run. The release workflow's
`screenshots` job installs a minimal texlive (pdflatex + standalone +
circuitikz, no dvisvgm — math labels go through the deterministic bundled
ziamath fallback) so the preview pane compiles, re-runs the script on every
version tag, and commits the images to `main` only when the pixels changed
(`[skip ci]` docs-only commit), so the gallery always matches the latest
release. The manifest (`SHOTS`) is the single source: tests assert each entry
exists under `examples/`, both themes are represented, and the README
references every output file (§13, `tests/test_screenshots.py`).

**Runtime resources.** Three resources are read at runtime and must be bundled:
`assets/icon.png`, `components/geometry.json` (symbol geometry), and
`components/definitions.json` (per-component registry/codegen data + the
`origin_svg` placement constant — read by `app/components/library.py`). The
geometry is **self-contained** — it bakes in every symbol's geometry, including
the resolved `+`/`−` glyph marks (as `glyphs` entries with a baked affine matrix;
see §5.3), so `svgsym.py` reads only the geometry plus the single
`origin_svg` constant. Because a frozen app cannot resolve `__file__`-relative
paths the way a source checkout does, all call sites (`main.py`,
`app/ui/mainwindow.py`, `app/canvas/style.py`, `app/components/library.py`) go
through `resource_path()` in `app/resources.py`, which roots paths at
`sys._MEIPASS` when frozen and at the project root otherwise.

**Bundled package data.** `heaviside.spec` additionally collects the font data
shipped by three dependencies via `collect_data_files`: `qtawesome` (toolbar/ribbon
icon fonts) and `ziamath`/`ziafont` (STIX Two Math + DejaVu Sans, loaded at import
time for the no-LaTeX math fallback — omitting these silently breaks that fallback
in the frozen app; see §5.8). The whole `licenses/` folder is bundled too, so the
required third-party notices (Qt/PySide6 LGPLv3, plus the SIL OFL 1.1 / Apache-2.0
/ MIT / CC-BY texts for the bundled fonts) travel inside the distributed
application. The About dialog (Help → About) surfaces a **Third-Party Licenses…**
button that opens this folder, and a one-line attribution summary, so the
acknowledgements are discoverable from the GUI as well as on disk.

**Not bundled.** `pdflatex` (with `circuitikz`) remains an external
user-installed dependency (§8.4) — bundling a TeX distribution is impractical.
The PDF preview is rendered by the bundled `QtPdf` module (PySide6), so the
bundle needs no Poppler for normal use; `pdftocairo` (Poppler) is only needed if
you export EPS. Editing, source generation, preview, and `.tex`/PDF export work
with just `pdflatex`; the startup dependency check (§8.4) warns when it is
absent.

**macOS PATH augmentation.** A GUI app launched from Finder/Dock inherits only a
minimal `PATH` (`/usr/bin:/bin:/usr/sbin:/sbin`), so TeX/Poppler tools installed
under `/Library/TeX/texbin` or `/opt/homebrew/bin` appear "missing" even when
present. `app/preview/latex.py` therefore calls `_ensure_tool_dirs_on_path()`
(idempotent; macOS-only; appends the standard tool directories that exist)
before every `pdflatex`/`pdftocairo` lookup in `check_dependencies`,
`compile_tex`, and `pdf_to_eps`. This makes a Finder-launched bundle behave the
same as a terminal launch. The list of directories is `_MAC_TOOL_DIRS`.

### 11.2 Update Notifier

Heaviside does **not** self-update. `app/update.py` is an opt-out *notifier*:
when enabled (the default, `Preferences.check_updates_on_startup`), the app makes
a single read-only HTTPS request to the GitHub Releases API on startup, compares
the newest published release to the running `__version__`, and — if a newer one
exists — shows a non-blocking prompt offering to open the download page in the
browser. Nothing is downloaded or installed automatically, and no information
about the user is sent.

Behaviour and invariants:

- **Default on, with a one-time disclosure.** The first time the startup check
  would run, a plain-language dialog explains that the app contacts GitHub on
  launch and how to turn it off (`Preferences.update_check_disclosed` records
  that it has been shown). Help ▸ **Check for Updates** runs the same check on
  demand and always reports a result (including "you're up to date").
- **Pre-releases are considered.** The probe queries the releases *list*
  (`/releases?per_page=…`), not `/releases/latest` — the latter silently omits
  pre-releases, which alpha builds are tagged as (§docs/releasing.md). Drafts are
  always ignored; the newest by version wins (`is_newer`, a hand-rolled
  comparison so no `packaging` dependency is bundled).
- **Fail-silent.** Any network/parse error (offline, rate-limited, malformed)
  yields "no update found" — never an error dialog. The probe runs on a worker
  thread (`check_async`); only the UI prompt touches widgets.
- **Skip a version.** The startup prompt offers "Skip This Version"
  (`Preferences.skipped_update_version`) so a declined update does not re-prompt
  every launch; a manual check ignores the skip.
- **The release URL is validated before opening.** The download link in the
  prompt comes from the GitHub API response, so it is treated as untrusted:
  `_safe_release_url` passes through only `https` URLs whose host is
  `github.com` (or a subdomain) and **falls back to the official releases page**
  (`RELEASES_PAGE_URL`) for anything else — a compromised or spoofed API
  response cannot steer the user's browser to an arbitrary URL.

The version logic and release selection (`check_for_update`, injectable `fetch`)
are pure and unit-tested without a network or event loop; see §13 and
`tests/test_update.py`.

---

## 12. Package Management and Development Environment

### 12.1 Tool: uv

All Python dependency management uses **uv**. No `pip`, `virtualenv`, or `conda` commands are used directly. uv manages the virtual environment, dependency resolution, and script execution.

Install uv (once, system-wide):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 12.2 Project Initialization

The project uses a standard `pyproject.toml` as its single source of truth for metadata and dependencies. To initialize a fresh clone:

```bash
uv sync
```

This creates `.venv/` in the project root, resolves all dependencies from `pyproject.toml`, and installs them. No further setup is required.

### 12.3 `pyproject.toml`

```toml
[project]
name = "heaviside"
version = "0.1.0"
description = "Graphical editor for CircuiTikZ circuit diagrams"
requires-python = ">=3.11"
dependencies = [
    "PySide6>=6.5",
    "pydantic>=2.0",
    "qtawesome>=1.4.2",
]

[project.scripts]
heaviside = "main:main"

[dependency-groups]
dev = [
    "pytest>=7.0",
    "pytest-qt>=4.2",
    "pytest-cov>=4.0",
    "pyinstaller>=6.20.0",
    "pillow>=12.2.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "--cov=app --cov-report=term-missing"

[tool.coverage.run]
omit = [
    "app/ui/*",
    "app/canvas/scene.py",
    "app/canvas/view.py",
    "app/canvas/items.py",
]
```

### 12.4 Common Commands

| Task | Command |
|------|---------|
| Install / sync environment | `uv sync` |
| Install including dev dependencies | `uv sync --group dev` |
| Run the application | `uv run heaviside` |
| Run all tests | `uv run pytest` |
| Run tests, no coverage | `uv run pytest --no-cov` |
| Add a new dependency | `uv add <package>` |
| Add a dev-only dependency | `uv add --group dev <package>` |
| Upgrade all dependencies | `uv sync --upgrade` |

### 12.5 Offscreen Testing

Integration tests that instantiate Qt widgets require a virtual display. Set the following environment variable to use Qt's offscreen platform, which requires no display server:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest
```

This is the expected invocation in any headless environment (e.g., a remote machine without a desktop session).

### 12.6 Python Version

The project targets **Python 3.11** or later. This is enforced by the `requires-python` field in `pyproject.toml`. uv will raise an error if the active Python interpreter does not meet this constraint.

### 12.7 System Dependencies

The following must be installed separately from uv — they are not Python packages:

| Dependency | Purpose | Install |
|------------|---------|---------|
| `pdflatex` | Compiling LaTeX previews | TeX Live (`texlive-full`) or MiKTeX |
| `circuitikz` | LaTeX package for circuit diagrams | Included in `texlive-science` or MiKTeX package manager |
| `pdftocairo` *(optional)* | PDF→EPS/SVG conversion for **EPS and SVG export only** | Poppler (`poppler-utils` on Debian/Ubuntu) |

The PDF preview is rendered by the `QtPdf` module bundled with PySide6, so no Poppler is needed for normal use. The application checks for `pdflatex` on the system `PATH` at startup and shows a warning dialog if it is missing; `pdftocairo` is checked only when EPS or SVG export is invoked.

---

## 13. Test Specification and Acceptance Criteria

### 13.1 Philosophy

Tests are organized into three tiers: **unit tests** covering pure logic with no UI or filesystem dependencies, **integration tests** covering interactions between layers, and **acceptance criteria** defining the conditions under which v1 is considered complete. The UI layer (canvas painting, mouse interaction) is not unit-tested; it is covered by the acceptance criteria via manual verification.

### 13.2 Unit Tests

All unit tests live in `tests/` and are run with `pytest`. They must pass with no network access, no display server, and no LaTeX installation.

**Slow tests (`--run-slow`).** A test marked `@pytest.mark.slow` is **skipped by default** and runs only with `pytest --run-slow` (see `tests/conftest.py`). The one slow test is `test_render_store_reproduces_committed_files`, which re-renders every symbol through `latex`/`dvisvgm` (~3 min) to verify the committed `components/*.json` are reproducible. It is a **local/manual** check, **not run in CI**: it needs the TeX toolchain (which CI does not install) *and* the exact CircuiTikZ version the committed files were rendered with, so running it in CI would be both slow and version-fragile. Run it locally after regenerating components (`python components/generate_components.py`).

The individual test functions are the authoritative, self-documenting list of
unit-level behavior; this section summarises what each test file covers rather
than enumerating every function (which would duplicate the suite and inevitably
drift out of date). Read the named file in `tests/` for the exact assertions.

#### Model and Validation (`test_model.py`)

Covers the data-model dataclasses, the mixin composition that defines which
components carry font/style/drawing capabilities (guarding the base-ordering that
dataclass field inheritance is sensitive to), and the `validate()` invariants:
known `kind`, allowed rotations (90° multiples), positions and wire vertices on
the 0.25 GU grid, Manhattan-only wire segments, and unique component ids. Also
covers the block-diagram connection-point helpers — `rect_perimeter_points`,
`circle_connection_points`, and `component_connection_points` — and how a wire
ending on a rect edge or circle cardinal point counts as connected. The
**`point_key` connectivity convention** (§4.5) is pinned here: the key rounds
float noise away, and `junction_points` / `wire_splits_at` tolerate
float-noise coordinates from off-grid pins instead of missing the coincidence.

#### Schematic Validation (`test_validate.py`)

Exercises `validate()` directly (no Qt, no LaTeX), separate from the
load-path consequences covered in `test_io.py`: the **wire ≥ 2 points**
invariant (§4.5) — an empty or single-point `Wire.points` is flagged with a
descriptive message and a two-point wire is accepted — alongside the existing
invariant checks.

#### Transform Consistency (`test_transforms.py`)

A **tripwire** for the clockwise-rotate-then-mirror transform, which is
implemented independently in several places: `component_pin_positions` (model
connectivity), `local_span_to_world` / `world_delta_to_local` (endpoint-drag
math), `GroupRotateCommand._rot90cw` (group rotation), and the canvas
`QTransform` built by `ComponentItem`. The module asserts **pairwise
agreement for every rotation × mirror combination**, so a sign drift in any
one copy — which would silently detach wires or paint components away from
their pins — fails CI immediately. (The Qt-dependent checks run offscreen.)

#### Code Generation (`test_codegen.py`)

The largest unit file: verifies the `Schematic → CircuiTikZ` mapping end to end.
This includes the **document voltage/current styles** (american emits no
`\ctikzset` line, so output is unchanged; european emits a picture-scoped
`\ctikzset{voltage=european}` / `current=european`, combined when both), the
`to[…]` syntax for every two-terminal kind (R, C, L, the diode
family with filled/`*` variants and the picture-scoped `diodes/scale`), the
`node[…]` syntax and computed scale/lead alignment for multi-terminal kinds
(op amp leads; IGFET family `anchor=gate` + `xscale`/`yscale` + residual lead;
BJT `xscale`/`yscale`; body-diode option), single-terminal nodes, and wires. It covers the determinism guarantee (`generate()` is pure),
coordinate formatting (`_fmt`'s integer/half-integer/2-decimal rules),
origin normalisation (§7.3 — a far-from-origin schematic emits source near `(0,0)`,
relative geometry and grid alignment preserved, already-at-origin unchanged), the
Y-flip convention, junction (`circ`) and open-endpoint (`ocirc`) node emission with all
their suppression rules (pin-connected, voltage-annotation, degenerate-wire,
custom-marker, and `no_junction_dots`/`no_termination_dots` cases), wire styling
and z-layering (shared vs. standalone `\draw`, background/foreground ordering and
the named-anchor-vs-absolute-coordinate rule), endpoint markers (`arrows.meta`
tips), endpoint/mid labels and their placement and Y-flip-aware anchoring, drawing
annotations (text nodes, rects, circles with centred text and fonts), bipole
node styling, and the `build_tex`/`build_snippet`/`pdf_to_eps`/`pdf_to_svg`
helpers (`arrows.meta` loading, preamble documentation, EPS/SVG conversion —
including the missing-`pdftocairo` error and a round-trip when present). It
also covers the **brace-balancing containment** (§7.2) at each emission site —
a stray `}` in a text node, wire label, bipole label, or centred box text
stays inside its brace group, balanced LaTeX is untouched — plus
degenerate-wire rejection by generation-time validation and the **dark preview
template** colours matching the canvas dark palette
(`test_dark_template_colors_match_canvas_palette`).

#### File I/O (`test_io.py`)

Covers `save`/`load` round-trips for every component and wire field, the
**document `config`** (voltage/current styles round-trip; `save` writes the
`config` object at the current format version; a `0.1` file with no `config`
loads with american defaults; an unrecognised style value coerces to american),
the "defaults are omitted from the JSON" rule (which keeps plain documents
compact), and load-time validation: every typed field rejects the wrong type with a
`SchematicLoadError`, unknown versions and malformed/invalid JSON raise
descriptive errors, and invariant violations are caught on load. Also covers that
a `rect`'s `options` text is loaded verbatim (never parsed as a style string),
`mid_label_pos` clamping, and that `save` is atomic UTF-8 with no BOM and leaves
no `.tmp` file behind (even when the write fails). The **save/load hardening**
(§9.1/§9.3/§9.4) is pinned here: format **0.3** round-trips and 0.1/0.2 files
still load; an **invalid schematic raises `SchematicSaveError` and writes
nothing** (an existing good file is never clobbered); overwriting keeps a
**`.bak`** of the previous file; NaN/Infinity literals (and string-coerced
NaN) raise a load error; an **oversized file is refused** before being read;
empty/single-point wires are rejected on load; `load` never leaks a raw
exception; and the rotation/`z_order` type strictness — an integral float
`90.0` rotation is accepted, a non-integral float or boolean is rejected.

#### Registry (`test_registry.py`)

Cross-checks registry integrity: every `kind` resolves to a `ComponentItem` —
explicit `ITEM_CLASSES` entry or the generic fallback (`test_all_kinds_resolve_to_a_component_item`);
`_DISPLAY_ORDER` is a preference, so an unlisted kind still appears
(`test_display_order_is_a_preference_not_exhaustive`); every
`PinDef.offset` lies on the 0.25 GU grid (`test_all_pins_on_quarter_grid`),
two-terminal `default_span` equals the terminal pin offset, no duplicate kinds,
and that `circle` is registered like `rect` (Drawing category, no pins,
resizable, `CircleComponent`).

#### Geometry Helpers (`test_model.py`)

Covers the pure geometry and connectivity helpers in `app/schematic/model.py`.
`simplify_points` collapses duplicate and redundant collinear vertices while
preserving endpoints and genuine elbows (including the second-pass dedup for the
A–B–A → A case) without mutating its input. `junction_points` emits a dot exactly
where the degree (wire segment-ends plus coincident pins) is ≥ 3 — 3-/4-way
meetings, T-splits, pin-on-pass-through — and not for pass-throughs, corners,
end-to-end meetings, or a lone pin-plus-wire. `open_endpoints` and
`unconnected_pins` are the complementary dangling-terminal sets (free wire ends
vs. wireless pins), with their suppression rules for pin connections, voltage
annotations (`NON_CONNECTING_KINDS`), degenerate single-point wires, custom
endpoint markers, and the `no_junction_dots`/`no_termination_dots` flags.
`wire_crossings` places one line-hop per genuine (non-connecting) H×V crossing on
the higher-`z_order` wire and respects the `hop_mode` overrides (`never`/`always`)
and the global preference, drawing no hop where the crossing is actually a
connection or on annotation/collinear/self-crossing wires. Also covers
`wire_splits_at` / `wire_corner_splits_at` (mid-segment and elbow split sites) and
`component_pin_positions` (the rotate-then-mirror transform). Two named
regressions are pinned here: `test_junction_no_spurious_dot_after_u_turn_drag`
(an auto-elbow landing on a pin must not fabricate a junction dot) and the
crossing tie-break ordering.

#### Canvas Geometry (`test_geometry.py`)

The pure, Qt-scene-free helpers in `app/canvas/geometry.py` are unit-tested directly: `snap_gu` rounding; `scene_to_gu`/`gu_to_scene` round-trip; `snap_point_gu`; the `local_span_to_world` / `world_delta_to_local` rotation mapping (round-trip across all four rotations and both mirror states, plus known clockwise/mirror values); `dist2_to_segment` interior-vs-endpoint and degenerate cases; and `wire_proximity_key` (empty polyline → None, interior hit outranks endpoint touch, intermediate-vertex hit promoted to rank 0).

#### Wire Geometry (`test_wiregeometry.py`)

`WireGeometry` (wire snapping / hit-testing over a `Schematic`, no Qt scene) is unit-tested directly: `nearest_pin` radius behavior; `all_pin_positions`; `wire_snap_target` priority (pin over wire), grid fallback, snap-onto-segment, and own-wire exclusion; `vertex_is_draggable` (every in-range vertex draggable, incl. a connected endpoint — drag to disconnect); `wire_vertex_at` (returns a connected endpoint); `unconnected_pin_at` (free pin detected, connected pin skipped); and `click_select_wire_id` preferring a pass-through wire over the grabbed stub.

#### Commands (`test_commands.py`)

In addition to the undo/redo behaviors in §13.3, the pure (Qt-free) command layer is unit-tested directly, including: `MoveCommand` wire-following (endpoint follows, rigid translate when both ends ride, auto-elbow, exact undo; select-all rigid translate of free endpoints; explicit `wire_ids` rigid translate for selected free wires; partial-move leaves unselected free endpoints anchored; a pin on a multi-wire junction does not drag the net but grows a fresh **re-stretch lead** to its new position — `test_move_off_multiwire_junction_restretches_lead`, `test_move_single_lead_still_follows` — with undo/redo restoring it); `SplitWireCommand` split-into-two / undo (two halves replace original, undo restores original); **wire decoration preservation** (`test_split_wire_preserves_labels_and_style` / `test_merge_wire_preserves_labels_and_style` / `test_move_collapsed_wire_restored_with_labels_on_undo` — a split routes start/end labels+markers to the matching half and whole-wire style to both, a merge carries each surviving end's label and the line style, and a move that collapses a labelled wire restores it verbatim on undo); **whole-wire move** (`test_move_wire_only_translates_and_taps_follow` — `MoveCommand([], delta, wire_ids=…)` rigidly translates a wire while a junction tap follows at the shared vertex and its far end stays); `MergeWireCommand` merge-two-halves / undo; `MoveWireVertexCommand` reshape + simplify + undo, plus collapse-to-a-point removes the wire (not a degenerate single-point wire) with undo/redo restoring it; `DeleteCommand` with component and wire ids; `MacroCommand` composing split + add (3 wires) as one undoable unit; `MoveOptionsLabelCommand` set/undo/redo/clear of `label_offset`; `SetWireLineStyleCommand` / `SetWireLineWidthCommand` / `SetWireNoJunctionDotsCommand` / `SetWireNoTerminationDotsCommand` do/undo/redo on a wire; **endpoint-label placement** (`SetWireStartLabelPlacementCommand` / `SetWireEndLabelPlacementCommand` do/undo/redo, setting `start_label_placement` / `end_label_placement`); **rect block-diagram edge connections** (`test_move_drags_wire_connected_to_rect_edge` / `test_move_rect_edge_wire_undo_restores` — a wire endpoint on a rect edge follows a `MoveCommand` and undo restores it; `test_resize_rect_far_edge_wire_follows_scaled` / `test_resize_rect_edge_wire_undo_restores` — under `ResizeCommand` a far-edge connection scales about the fixed corner while a near-edge connection stays put, with exact undo); **circle block-diagram cardinal connections** (`test_move_drags_wire_connected_to_circle_cardinal` — a wire on a circle's cardinal point follows a `MoveCommand`; `test_resize_circle_cardinal_wire_follows_scaled` / `test_resize_circle_cardinal_wire_undo_restores` — under `ResizeCommand` a far cardinal point scales about the fixed corner while the near one stays, with exact undo); and `GroupRotateCommand` (single-component spin-in-place, two-component centroid rotation, internal wire vertex rotation, boundary wire reshaping, undo/redo, plus a boundary wire that collapses to a point under the rotation is removed rather than left degenerate, with undo/redo restoring it). The newer regression coverage pins: **execution-time split resolution** (`test_split_wire_resolves_index_after_move_reshapes_wire` and the no-op cases — a split whose point is no longer on the wire, or that a bundled move reshaped onto an endpoint, is a clean undo-aware no-op); **sequential merge composition** (`test_sequential_merges_sharing_a_wire_compose` — deleting both taps of a bus merges the rail into one wire); **`MacroCommand` failure unwinding** (`test_macro_unwinds_executed_children_on_failure`, `test_undo_stack_push_failure_records_nothing`); **verbatim restore at the original index** of a wire removed by a vertex-drag / resize / junction-drag / group-rotate collapse (labels, markers, style, stacking included); **mirrored group rotation** (`test_group_rotate_mirrored_component_pins_follow`, `…_boundary_wire_stays_attached` — the −90 step, §6.6); **float-noise tolerance** in wire-following and deletion (`test_move_follows_wire_with_float_noise_endpoint`, `test_delete_removes_wire_with_float_noise_endpoint` — the `point_key` convention, §4.5); and the **`UndoStack` save point** (`mark_save_point`/`is_modified` round-trips, divergent-edit unreachability, and `record()` not re-executing).

#### Preview Worker (`test_worker.py`)

`PreviewWorker` thread lifecycle: `shutdown()` stops the background `QThread`; it is idempotent (safe to call from both `closeEvent` and `aboutToQuit`); and emitting `QApplication.aboutToQuit` stops the thread even when the window's `closeEvent` never fired.

#### Preview Render (`test_preview_render.py`)

`pdf_to_qimage` (QtPdf): a compiled schematic PDF renders to a non-null `QImage`; a higher DPI yields a proportionally larger raster (same source page); garbage input raises cleanly (`CompileError`/`RuntimeError`) rather than crashing. Requires `pdflatex`; no Poppler involved.

#### Math Render (`test_mathrender.py`)

On-canvas math rendering and option-slot parsing (§5.8). Pure-logic tests always run: `_split_top_level` ignores commas inside `$…$`/`{…}`; `slot_fragments` pairs side-slot keys with values and drops `t=`, flags, and empty values; `slot_side` maps `^`/`_`/family to above/below; `label_display_latex` extraction. LaTeX render tests are gated on `latex`+`dvisvgm`: a fragment renders to a non-empty baseline-normalised `QPainterPath` (left ink at x=0, baseline at y=0); different fragments share the baseline; empty input yields `None`; the compiled SVG is cached on disk; and `render_async` delivers its result through the Qt event loop. The **ziamath** engine tests need no LaTeX (it is a bundled dependency): it renders text, mixed text+math, fractions and Greek to a non-empty baseline-normalised path; `$…$` delimiters typeset as math rather than literal dollar glyphs (`test_ziamath_strips_math_delimiters` — guards the `ziamath.Text` vs math-only `ziamath.Latex` choice); `_active_engine` selects `latex` when present else `ziamath`; `set_force_ziamath` forces ziamath; and auto-selection falls back to ziamath when `latex`/`dvisvgm` are absent. A packaging guard (`test_pyinstaller_spec_bundles_ziamath_fonts`) asserts `heaviside.spec` keeps the `collect_data_files` lines for `ziamath` and `ziafont`, so their import-time fonts (STIX Two Math, DejaVu Sans) cannot silently drop out of the frozen bundle. The **cache hardening** is pinned here too: a transient render or baseline failure is **not memoised** (it is retried and self-heals; successes are memoised in a bounded memo), a **corrupt on-disk cache entry is discarded and recompiled** rather than trusted, and the disk cache directory is **per-user and private (0700)**, falling back to a fresh `mkdtemp` when the expected path has been squatted by another user. The **async delivery invariant** (§5.8) is pinned ungated: `test_render_async_single_dispatcher_no_per_request_qobject` — one process-lifetime dispatcher, no per-request signals QObject (the off-thread QObject-destruction segfault class), and this test's callback tokens drain after delivery; `test_render_async_callbacks_run_on_ui_thread` — results land on the UI thread even with the memo cleared and the pool churning.

#### Symbol Geometry (`test_svgsym.py`)

`symbol_paths` glyph reconstruction (from the **self-contained** geometry, §5.3): `test_geometry_is_self_contained_for_glyph_kind` — `cV`'s `+`/`−` marks are baked into the geometry's `glyphs` list (real path `d` + a 6-element affine `matrix`), so no `.svg` access is needed at run time; every path returned for `cV` has real geometry (no unresolved glyph-ref leaks through as an empty path); a glyph-bearing kind (`cV`) returns strictly more paths than a glyph-free one (`R`); and a plain symbol still renders its strokes. Guards against the `+`/`−` marks silently disappearing. **Fill rule** (§5.2): `test_filled_diode_body_is_filled` — the filled diode `D*` has a filled body path while plain `D` does not (so toggling the filled option visibly updates the canvas); `test_stroke_only_symbols_not_filled` — pure outline symbols (`L`/`C`/`R`) have no filled paths, guarding the rule against over-filling stroked bodies. Library-integrity tripwires were added: every library kind (including every variant and parametric-value combination) resolves to geometry, and `svgsym`'s `geometry_key` is a re-export of the single `app/components/library.py` definition (§7.2) — so the canvas and codegen kind→geometry mappings cannot drift.

#### LaTeX Security (`test_latex_security.py`)

The LaTeX-pipeline security guarantees (§7.2, §8.1, §8.4): every compile path —
the preview/export pipeline, the on-canvas math-label renderer, and the
offline component renderer — passes `-no-shell-escape` with the argument
vector as a list (never `shell=True`); `balance_braces` neutralises stray
open *and* close braces while leaving balanced and escaped braces untouched;
`contains_dangerous_latex` flags the high-risk commands and accepts benign
label text (and `None`); and both `build_tex` and `build_snippet` carry the
SECURITY header comment in their output.

#### Preferences (`test_preferences.py`)

The `Preferences` wrapper and `PreferencesDialog` (§10.8), exercised against an
isolated `QSettings` backed by a temp INI file (never touching the real user
store): **TeX/SVG/PNG auto-export default on and PDF/EPS off**
(`test_export_defaults`), mark-unconnected-pins defaults off, **line-hops
defaults on** (`test_line_hops_default_on_and_roundtrip`); TeX/PDF/EPS/SVG/PNG, the
`force_ziamath` render flag, and the display flags round-trip and persist across
new `Preferences` instances over the
same backing file; `_to_bool` normalizes the string booleans `QSettings` may
return; the **per-tool path** accessors default to empty and round-trip (trimmed);
and the dialog persists all checkbox state **and the tool-path fields** on accept
and discards them on cancel. The **update preferences** default correctly
(`check_updates_on_startup` **on**, `skipped_update_version` empty,
`update_check_disclosed` off) and round-trip.

#### Update Notifier (`test_update.py`)

The opt-out update notifier (§11.2). Pure version logic: `is_newer` compares
numerically (`0.10.0` > `0.9.0`), ranks a final release above its own
pre-release (`1.0.0` > `1.0.0-alpha`), tolerates a leading `v`, and treats an
unparseable remote as not-newer. Release selection (`check_for_update` with an
injected `fetch`): picks the newest release including pre-releases by default,
excludes them on request, ignores drafts, returns `None` when up to date / only
older / the list is empty / the fetch failed, skips malformed entries, and
truncates the notes. **URL validation** (§11.2): `_safe_release_url` passes
`https` github.com URLs through and falls back to the releases page for
non-https schemes, foreign hosts, and look-alike domains. Network handling:
`_fetch_releases` returns `None` on an
`urlopen` error or non-JSON body (mocked). Async: `check_async` delivers its
result on the Qt event loop (offscreen).

#### External tools (`test_tools.py`)

`app/preview/tools` resolution (§8.7), pure (no Qt): a configured override wins
when it points at a runnable file; a non-runnable override is ignored and
resolution falls back to `PATH`; `resolve` is `None` when neither yields a tool;
a blank value clears an override; `set_tool_paths` ignores unknown keys; and
`is_runnable` accepts only existing executable files (not directories or blanks).

#### README gallery screenshots (`test_screenshots.py`)

The release-time README gallery renderer (§11.1 "README gallery screenshots"),
offscreen Qt: the `SHOTS` manifest names exactly four bundled examples that all
exist on disk, with unique output names and at least one light **and** one dark
entry; the README references every output file (manifest↔README drift guard);
and an actual script run (in a **subprocess** — the script redirects QSettings
globally, which must not leak into the test process; importing the module is
asserted side-effect-free by the same arrangement) captures the full dark-mode
editor window: the PNG is exactly `WINDOW_SIZE`, predominantly dark, and shows
real UI structure rather than a blank fill or a canvas-only crop.

#### Component palette (`test_palette.py`)

The redesigned palette (§10.2), offscreen Qt: a category card is built per
registry category and a default active category is selected; selecting a card
makes it active and retitles the active section; the **in use** panel (pinned at
the bottom, §10.2) is hidden for an empty document and appears once a kind is
placed; a non-empty search hides the categories/active sections and shows the
flat results grid (restored on clear); and clicking a tile calls
`scene.start_placement(kind)`.

#### Main window (`test_mainwindow.py`)

`MainWindow` auto-export and label re-typeset, against an isolated `QSettings`
and offscreen Qt. The **explicit-palette fallback** (§10.1) is pinned here:
forcing Dark on a platform that ignores `setColorScheme` (offscreen) flips the
application palette's Base/Button/Text roles dark, Light flips them back, and
System mode restores the pristine platform palette; the source pane's font is
asserted to be the platform fixed-width font (no "Monospace" alias scan). The TeX-snippet auto-export writes `<name>.tex` **without**
invoking the compiler (asserted by failing the test if `_compile_to_pdf` is
called) — confirming it needs no `pdflatex`; with every auto-export preference
off, nothing is written; and `SchematicScene.retypeset_labels()` runs over a
labelled component without error (used when the math engine / ziamath preference
changes, §8.4); and the **Document** inspector tab (`DocumentPropertiesPanel`)
writes the chosen voltage/current styles onto the schematic **live** and emits
`document_changed` (and `refresh()` reloads silently), and the inspector tabs
switch between **Properties** (object selected) and **Document** (nothing
selected) (§10.9); and **copy-to-clipboard** (§8.6) — copy-as-PNG sets a
non-null clipboard image; copy-as-PDF exposes both the `com.adobe.pdf` UTI and
`application/pdf` plus a raster fallback; copy-as-SVG exposes `public.svg-image` +
`image/svg+xml` plus a raster fallback and asserts **no** `text/plain` flavor
(the Office XML-paste regression); compile + convert are stubbed, so no LaTeX.
The preview panel's Copy PDF/SVG buttons emit the copy-request signals. The
**toolbar theme radio group** (`_theme_actions`, §10) flips both palettes and pins
the choice (`_follow_system=False`), after which an OS appearance change is ignored;
`_apply_theme` also swaps the canvas + chrome palettes together and re-applies the
toolbar stylesheet. The **save/load flow** (§10.1) is covered: `_confirm_discard`
offers Save/Don't Save/Cancel and actually saves (a cancelled save path is
treated as Cancel); a `SchematicSaveError` is reported without clobbering the
file; the **Undo/Redo actions track the stack** and the **modified state
follows the undo-stack save point**; a new document declares the current
format version; `load_path` opens a schematic, reports a bad file, and
**warns about dangerous LaTeX** in labels; and **auto-export runs off the UI
thread, is single-flight, and a failure in one format does not block the
others**. MainWindow tests neutralise the startup
dependency check (an autouse fixture) so a missing tool never pops a modal that
would hang a headless run.

#### Welcome screen & Help dialog (`test_welcome.py`)

The Help dialog's reference tables (`_HELP_SHORTCUT_GROUPS` /
`_HELP_GESTURE_GROUPS`, §10.1) are well-formed `(title, [(keys, description),…])`
groups whose descriptions are full sentences (end with `.`); a few anchor
shortcuts (`S`/`W`/`P`/`R`/`Ctrl+N`/`Ctrl+S`/`Ctrl+Z`/`Ctrl+Shift+Z`) and the
three `Tab (over…)` hover-cycle rows plus `Shift+Tab` are present; anchor
gestures (drag-endpoint, double-click, scroll-to-zoom) are present; `_HelpDialog`
builds with a `QScrollArea` (its `_RefTable`s wrap descriptions) and paints
without error; and the (diagram-only) `_WelcomeScreen` paints at typical and
small sizes. `test_help_action_wired_to_toolbar_and_menu` checks the help action
is on the toolbar with the bug-report button just after it (last);
`test_report_bug_opens_github_issues` checks **Report a Bug** is on the toolbar
and in a menu and that triggering it opens `_ISSUES_URL` via
`QDesktopServices.openUrl`.

### 13.3 Integration Tests

Integration tests run against `SchematicScene` / `SchematicView` (file `test_scene.py`). They require Qt with an offscreen platform (`QT_QPA_PLATFORM=offscreen`) but no LaTeX installation. In a headless environment Qt's platform plugin needs the system GL/EGL libraries present; if Qt cannot initialise, the scene tests skip rather than fail. As with §13.2, `test_scene.py` is the authoritative list; this is a summary of coverage.

**Preview↔commit parity** (`test_drag_parity.py`) drives real drag gestures offscreen, captures the last-frame ghost geometry (including re-stretch lead ghosts), commits, and asserts the committed model equals the preview exactly. The matrix covers: a straight drag pulling a connected wire, a component+wire co-drag where a junction tap follows, a re-stretch lead, contained-wire removal, a box resize with attached wires, and vertex drags that merge or collapse. These tests exist to fail if the shared reshape rules in `app/schematic/reshape.py` (§6.4) are ever forked between preview and commit again. **Scene memory-safety guards** (§6.8) are pinned in `test_scene.py`: removal ungrabs a grabbing item or descendant (and leaves unrelated grabs alone), a command pushed mid-grab is safe, and re-entrant `_rebuild_items` calls coalesce. **Float-noise containment** is pinned in `test_model.py` (`test_wire_contained_tolerates_float_noise`, `test_key_eps_derived_from_point_key_decimals`).

These exercise the full editor on a live scene: component placement, move, delete, and label editing each updating the model and being undoable (including a 20-deep undo/redo stack returning to the empty state), 0.25 GU snapping (including mid-drag, not just on release), and that items are movable only in SELECT mode. They cover the wiring state machine end to end — the in-progress wire ghost and its cursor-tracking snap markers, dominant-axis corner routing, the auto-enter/auto-exit transitions between SELECT and WIRE mode, intermediate-anchor drops, and the various double-click behaviors (entering WIRE mode from blank canvas or a wire body, the Alt+double-click mid-label editor, and per-end label editors on free and connected endpoints). Vertex and junction dragging is covered: reshaping stays Manhattan and simplified in the live preview, connected endpoints drag off their pin to disconnect, junctions drag all coincident wires together while preserving each wire's orientation into the junction, with the drag highlight/affordance items. The Tab-cycle family is covered per target (endpoint-label placement, endpoint markers, and body/interior line style, each with correct priority and wrap/reverse), as are new-wire style inheritance, block-diagram rect-edge and circle-cardinal connection dots and auto-start, open-circle and unconnected-pin item tracking, the canvas line-hop bumps (population, toggling, z-order/`hop_mode` hopper selection, and live preview during draw/drag), combined wire+component z-ordering (`bring_to_front`/`send_to_back` with a z=0 baseline, undoable), the thin-band wire hit area, and split-on-join / merge-on-delete connectivity (mid-segment and L-corner splits as single undoable actions). Two regressions are pinned as memory-safety guards for the §6.8 graphics-item-lifetime invariant: `test_no_index_method` / `test_group_rotate_then_delete_then_paint_does_not_crash` (the scene uses `QGraphicsScene.NoIndex` so a freed coordinate-keyed dot cannot leave a dangling BSP-index pointer) and `test_random_mutation_sequences_never_crash_paint` (randomized place/wire/rotate/delete/undo/redo sequences painting through a real view after every step). The **`scene.batch()`** grouping is covered: several edits inside a batch collapse into one MacroCommand (a single undo reverts them all), and a one-command batch is pushed directly. The **slot labels & annotation decorations** (§5.8) are covered: per-side slot labels appear/hide with the options string and land on the correct (traversal-relative) sides under rotation, with the voltage-source default-`v` flip and same-side stacking; a `v=` slot draws the american ± signs by default and the european arrow when the document `voltage_style` is european (updating live via `relayout_annotations`), an `i=` slot draws a current arrow, a plain `l=` and the centred `open` annotation draw none, and the decoration axis follows the on-screen traversal direction (vertical on a 90°-rotated body). The **dotted-grid** background paints onto a device-space painter without error. Newer scene regressions pin: off-grid-pin routing and vertex drags (the first jog stays on the pin's lead line, a dragged vertex lands on — or slides along the axis of — an off-grid pin, and the spot between two adjacent off-grid pins stays reachable); a drag does **not** resurrect a suppressed termination dot (the live preview honours the same junction/termination opt-outs as the committed render, `test_open_endpoints_overrides_match_committed`); wires follow a gate's pins when its scale or input count changes; whole-wire drags translate the wire with junction taps following; and releasing a resize/endpoint handle without moving leaves no stale preview.

The **inspector** (`test_properties.py`) additionally covers **multi-select bulk edit**: `PropertiesPanel.show_components` over several same-kind components binds the sections (`bind_multi`) and an `OptionsSection` edit applies to all of them as one undo step; over a **mixed-kind** selection it binds only the shared `multi_kind_safe` sections — a resistor + capacitor edit a shared stroke-width and rotation as one undo step (`OptionsSection` suppressed), and a resistor + rectangle (a symbol + a block) edit the **unified** stroke/outline width together via the single `StrokeWidthSection` while the symbol-only options and block-only fill are suppressed. **Logic-gate size** is covered by `test_scale_section_applies_to_gates_only_and_is_undoable` (the `ScaleSection` shows for gates only and pushes an undoable `SetComponentScaleCommand`), with the geometry/codegen side tested elsewhere: true-scaled-anchor pins and the full-size (1.0) placement default (`test_logic_gate_placed_full_size_pins_on_grid`, `test_set_component_scale_is_undoable`, and the placement-ghost default scale `test_placement_ghost_uses_default_scale` in `test_scene.py`), the `scale` round-trip + back-compat (`test_component_scale_roundtrip` in `test_io.py`), the height/xscale sizing with **no lead stubs** even for even-input gates (`test_scaled_*` in `test_codegen.py`), and that a wire to a scaled gate's pin attaches at the true `(node.in k)` anchor (`test_wire_to_scaled_gate_pin_attaches_at_node_anchor`). The **off-grid pin magnet** is covered in `test_wiregeometry.py` (`test_wire_snap_target_grabs_offgrid_gate_pin_via_raw_cursor`, `test_unconnected_pin_at_grabs_offgrid_gate_pin`), the **validation relaxation** in `test_model.py` (`test_wire_endpoint_on_offgrid_gate_pin_is_valid`, `test_wire_offgrid_vertex_not_on_a_pin_is_invalid`, `test_wire_corner_aligned_with_offgrid_pin_is_valid`), and the **on-grid corner routing** in `test_scene.py` (`test_route_to_offgrid_pin_keeps_corner_on_grid`). **Multi-wire bulk edit** is covered by `test_multi_wire_select_edits_all_as_one_undo_step` (`show_wires` over several wires binds them and a line-width or marker edit applies to all as one undo step). The inspector's **edit-loss guards** (§10.3) are pinned in `test_properties.py`: unbinding a section (or calling the panel-level `flush_pending_edits`) commits a pending debounced edit instead of dropping it; a programmatic reload does not clobber a focused field; a bipole `t=` label whose math contains commas (`$f(a,b)$`) survives extraction and round-trips; combo loads preserve hand-authored fill/line-style values that match no preset; and a Document-tab style change is undoable (`test_document_panel_change_is_undoable`).

### 13.4 Acceptance Criteria

The following criteria define v1 completion. Each must be verified manually by the author against a working build on the development machine.

#### AC-1: Component Placement
- [ ] Every registered component type appears in the palette, grouped by category.
- [ ] Each component can be placed on the canvas by clicking the palette entry and clicking the canvas.
- [ ] Placed components snap to the 0.25 GU grid visibly and consistently.
- [ ] A ghost preview follows the cursor during placement.
- [ ] Pressing `Escape` cancels placement without modifying the schematic.

#### AC-2: Canvas Interaction
- [ ] Components can be selected by clicking and deselected by clicking elsewhere.
- [ ] Multiple components can be selected with rubber-band drag and `Ctrl+click`.
- [ ] Selected components can be moved by dragging; movement snaps to 0.25 GU.
- [ ] Arrow key nudging moves selected components by 0.25 GU per keypress, in any direction.
- [ ] Delete key removes selected components and their connected wires.
- [ ] Canvas pan and zoom work via scroll wheel and middle-mouse drag.
- [ ] "Fit to schematic" correctly frames all placed components.
- [ ] Opening a schematic from disk fits the view to the loaded circuit.

#### AC-3: Wiring
- [ ] Pressing `W` enters wire mode.
- [ ] Clicking a pin starts a wire anchored to that pin.
- [ ] The wire preview (ghost) shows a two-segment Manhattan path following the cursor.
- [ ] The preview corner orientation follows the dominant axis (longer leg first); clicking empty space drops an intermediate vertex to steer the route.
- [ ] Double-clicking terminates the wire and exits Wire mode (returns to Select); clicking a second pin also terminates it.
- [ ] The resulting wire is rendered correctly on the canvas.
- [ ] `Escape` cancels a wire in progress without adding it to the schematic.
- [ ] Clicking an empty grid point mid-wire drops an intermediate anchor and continues routing.
- [ ] Clicking a free pin in Select mode auto-enters Wire mode; terminating on a pin returns to Select mode.
- [ ] Wires snap to existing wire vertices and to points on existing wire segments.
- [ ] Connecting to the middle of a wire splits it and shows a solid junction dot; the same dot appears wherever 3+ wire ends (or a pin + a pass-through wire) meet.
- [ ] Two wires that cross without sharing a vertex show a line-hop bump on the higher-`z_order` wire (on by default); the bump appears **live** while drawing a new wire across another or dragging an existing wire into a crossing. Changing a wire's z-order in the inspector flips which one hops, and the Preferences toggle hides/shows the bumps everywhere (canvas, source, preview, export).
- [ ] The inspector's tri-state **Line hops** checkbox sets a wire to **never** (it yields the bump to the crossing wire) or **always** (it hops even with the global preference off / a lower z-order), or back to **default** (dash).
- [ ] Dragging a wire's vertex reshapes it while staying Manhattan; dragging a connected endpoint off its pin/edge disconnects it.
- [ ] Moving a component drags its connected wire endpoints along (with a live ghost during the drag).
- [ ] A directly-selected wire can be deleted with `Delete`.
- [ ] Clicking near a wire selects it only when near the line itself, not anywhere in its bounding box.

#### AC-4: Properties
- [ ] Double-clicking a component opens the Properties Panel.
- [ ] The options string field is shown, pre-populated with the component's current options.
- [ ] Typing a LaTeX string (e.g., `$R_1$`) into a label field updates the canvas label display.
- [ ] The full schematic preview updates shortly after any change (500 ms debounce).
- [ ] Rotation and mirror controls change the component orientation on the canvas immediately.

#### AC-5: Undo / Redo
- [ ] `Ctrl+Z` undoes the last action for all command types (place, move, delete, wire, edit).
- [ ] `Ctrl+Shift+Z` redoes the last undone action.
- [ ] Undo and redo work correctly through a sequence of at least 10 mixed operations.

#### AC-6: Code Generation
- [ ] The source panel shows valid CircuiTikZ source at all times.
- [ ] The source updates within 300ms of any schematic change.
- [ ] The generated source compiles without error in a standard TeX Live installation with `circuitikz` installed.
- [ ] Component labels containing LaTeX math (e.g., `$\frac{V}{2}$`) appear correctly in the compiled output.

#### AC-7: Preview
- [ ] Pressing `Ctrl+Return` or the Compile button triggers a preview render.
- [ ] The rendered PDF preview appears in the preview panel within ~1 second of a change on a standard machine (500 ms debounce + a fast pdflatex/QtPdf turnaround).
- [ ] The preview matches the source panel output visually.
- [ ] A LaTeX compilation error is reported in the preview panel with the relevant error text visible.
- [ ] The main UI remains responsive during compilation (main thread not blocked).

#### AC-8: Save and Load
- [ ] `Ctrl+S` saves the schematic to a `.hv` file.
- [ ] The saved file is valid UTF-8 JSON readable in a text editor.
- [ ] Loading a saved file restores all components, wires, labels, rotations, and mirror states exactly.
- [ ] Loading a corrupted or invalid file shows an error dialog and leaves the current schematic unchanged.

#### AC-9: End-to-End Smoke Test
The following scenario must complete without error:
1. Launch the application.
2. Place a voltage source, two resistors, an op-amp, and connecting wires to form a simple inverting amplifier circuit.
3. Assign LaTeX labels to all components, including at least one equation of the form `$\frac{R_2}{R_1}$`.
4. Compile the preview and verify it matches the intended circuit visually.
5. Save the schematic to a `.hv` file.
6. Close and relaunch the application.
7. Load the saved file and verify all components, wires, and labels are restored identically.
8. Compile the preview again and verify it matches the pre-save output.

### 13.5 Test Coverage Target

- Unit tests: **≥ 90% line coverage** on `app/schematic/`, `app/components/`, and `app/codegen/`.
- Integration tests: **≥ 70% line coverage** on `app/canvas/commands.py`.
- The UI layer (`app/ui/`, `app/canvas/scene.py`, `app/canvas/view.py`, `app/canvas/items.py`) is excluded from coverage requirements and covered by acceptance criteria only.

Coverage is measured with `pytest-cov` and reported as part of the test run:

```bash
pytest --cov=app --cov-report=term-missing
```

---

## 14. Out of Scope (v1)

The following are explicitly deferred to future versions:

- LyX/LaTeX editor integration
- Round-trip parsing of existing `.tex` files into the canvas
- Free-angle rotation
- Auto-routing wires around placed components
- Bus wiring and net labels
- Circuit simulation or netlist export
- Component parameter sweep or annotation from simulation results
- Dark mode
- Collaborative editing
- Printing directly from the application
- Native (LaTeX-free) rendering and SVG export — see §14.1

### 14.1 Native (LaTeX-free) rendering — future work

**Not committed; an idea recorded for later.** Today the live preview and the
PDF/EPS/SVG exports all require a full LaTeX/CircuiTikZ install (gigabytes via TeX
Live / MiKTeX), which is the main install-friction point. A *native* renderer
could produce the preview and an SVG export with no LaTeX on the user's machine,
keeping the LaTeX→PDF path as the canonical, pixel-perfect reference and offline
fallback. This is a "fast/offline mode," **not** a replacement for the LaTeX path.

What already supports this (no new work):

- **Symbols are already LaTeX-free at runtime.** `components/geometry.json` bakes
  each symbol's SVG path data at *build* time (the one-time generator needs
  `latex`+`dvisvgm`); the canvas renders it purely from that store via
  `app/canvas/svgsym.py` (§5.3). Wires, junction/open dots, and label *text
  placement* are native too.

What is missing, in increasing difficulty:

1. **Scene → SVG emitter** (bounded). No `canvas → SVG` serializer exists; the
   canvas paints to Qt internally. Since the geometry is already `QPainterPath`s
   in known coordinates, emitting `<path>` elements is mechanical. Good first
   slice to validate fidelity against the circuitikz SVG: symbols + wires + dots +
   plainly-placed labels, **without** voltage/current arrows yet.
2. **CircuiTikZ annotation parity** (the real work; *partially done*). The canvas
   now draws a native, convention-faithful representation of the voltage/current
   annotations alongside the `l=`/`v=`/`i=` text: american ± signs, european
   voltage arrows, and current direction arrows (`_AnnotationDecoration`, §5.8),
   honouring the document `voltage_style`/`current_style`, the pin-traversal
   polarity, and the `^`/`_` side overrides. It is **not** a pixel-exact
   reproduction of circuitikz's arrow geometry, and direction modifiers beyond the
   common slots (`invert`, `i<`/`i>`, free-form styling) are not interpreted — the
   codegen path (`app/codegen/circuitikz.py`) still passes `comp.options` verbatim
   into `to[…]` for the exported/compiled output. A higher-fidelity route reuses
   the build-time measurement (the machinery that produced `geometry.json`):
   render sample annotated components, measure circuitikz's arrow paths and label
   anchors, and **bake the annotation geometry** so the native renderer merely
   *places* pre-measured circuitikz geometry — high fidelity, no runtime LaTeX.
3. **Math labels** (hardest to make truly LaTeX-free). The canvas currently shells
   out to `latex`+`dvisvgm` (cached, optional, falls back to raw text — see
   `app/preview/mathrender.py`). **KaTeX does not solve the export case**: it emits
   HTML+MathML+CSS, not SVG, so it cannot be cleanly embedded in a standalone
   `.svg`. For export, prefer **MathJax's SVG output** (bundled as a JS asset — a
   few MB vs. gigabytes of TeX) or a font-glyph renderer.

Design stance: keep LaTeX as canonical; define the supported-options subset
explicitly; warn/fall back at its boundary rather than render silently-wrong
output; sequence the work behind the item-1 scene→SVG spike so fidelity is
validated before committing to the annotation-parity tail.
