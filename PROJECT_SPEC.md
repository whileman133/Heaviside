# Heaviside — Specification

**Version:** 0.3  
**Status:** Draft  
**Author:** Wes H.

---

## 0. Spec Maintenance (Mandatory)

**This specification is a living document and must stay in sync with the implementation at all times.**

Whenever a feature is **added, changed, or removed** — by a human or an AI agent — the same change set MUST also update this specification so that the spec always describes the software as it actually behaves. A change is not complete until the spec reflects it. Specifically:

- **Adding a feature:** document its behavior in the relevant section(s) (data model, canvas behavior, code generation, UI, etc.), add or update any affected invariants, commands, keyboard shortcuts, and acceptance criteria, and add corresponding test entries in Section 13.
- **Changing a feature:** edit every section that describes the old behavior so no stale description remains. Search the whole document for affected terms.
- **Removing a feature:** delete its description (do not leave orphaned references) and move it to Section 15 (Out of Scope) if it is deferred rather than abandoned.
- **Version bump:** increment the spec **Version** field for any substantive behavioral change, and note new behavior under the appropriate section.

AI agents working on this project are explicitly required to follow this rule on every task that touches behavior. If a requested change would make the code and spec disagree, update both in the same change; if that is not possible, flag the discrepancy rather than silently letting them diverge (see Section 14.3).

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
- The grid is rendered as a light line pattern in the canvas background. Integer-GU lines are drawn at normal weight; the 0.5 GU midline at reduced opacity; the 0.25/0.75 GU minor lines faintest, so the unit cell stays readable on the denser lattice.
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
- A "fit to schematic" action (`Ctrl+0`) zooms to show all placed components with a fixed margin. Opening a schematic from disk (**File → Open**) runs the same fit automatically so the loaded circuit is framed in the view (deferred one event-loop tick so the viewport has its final size before `fitInView` runs).
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
    category: str                    # e.g. "Bipoles", "Tripoles", "Nodes"
    bbox: tuple[float, float, float, float]  # (x0, y0, x1, y1) relative to origin, in GU
    pins: list[PinDef]
    label_slots: list[str]           # valid slot names for this kind, shown as UI hint
    tikz_keyword: str                # CircuiTikZ node/path keyword
    default_span: tuple[float, float]  # (dx, dy) from origin to terminal pin, in GU
    resizable: bool = False          # True → terminal pin drag handle shown at instance
    component_class: type = Component  # Component subclass to instantiate for placed instances
```

`component_class` defaults to `Component`. Overridden in the registry for kinds that carry extra per-instance state: `DiodeComponent` for diodes, `TextNodeComponent` for `text_node`, `RectComponent` for `rect`, `BipoleComponent` for `bipole`. All of the last group extend the `DrawingComponent` base and compose capability mixins (`FontedComponent`, `StyledComponent`) for font and fill/border state respectively. The deserializer in `schematic/io.py` uses this pointer to construct the correct subclass without a type-discriminator field in the JSON.

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
    mirror: bool = False                # horizontal mirror before rotation
    label_offset: tuple[float, float] | None = None  # legacy; persisted but no longer affects display (§5.8)
    span_override: tuple[float, float] | None = None  # custom span for resizable components

@dataclass
class DiodeComponent(Component):        # all diode types
    filled: bool = False                # True → emit KIND* and use filled SVG

@dataclass
class MosfetComponent(Component):       # nigfete, nigfetd, pigfete, pigfetd
    body_diode: bool = False            # True → emit "bodydiode" option and use *_bodydiode SVG

@dataclass
class DrawingComponent(Component):      # base for text_node, rect, bipole
    z_order: int = 0                    # layer order (negative = behind circuit elements)

# Capability mixins — standalone dataclasses, never instantiated alone.
# CRITICAL: concrete classes must list mixins BEFORE DrawingComponent, or
# dataclass reverse-MRO field ordering raises "non-default argument follows
# default argument" at import.
@dataclass
class FontedComponent:                  # mixed into text_node and bipole
    font_size: float = 12.0             # points; emitted as \fontsize{N} in LaTeX
    font_bold: bool = False             # \bfseries
    font_italic: bool = False           # \itshape
    font_family: str = ""               # "" = default, "serif"/"sans"/"mono"

@dataclass
class StyledComponent:                  # mixed into rect and bipole
    fill_color: str = ""                # TikZ fill color, e.g. "yellow!20"; "" = transparent
    border_width: float = 0.4           # border/line width in pt (TikZ default 0.4)
    line_style: str = ""                # raw TikZ line-style tokens, e.g. "dashed"; "" = solid

@dataclass
class TextNodeComponent(FontedComponent, DrawingComponent):
    pass

@dataclass
class RectComponent(StyledComponent, DrawingComponent):  # span_override = (w,h)
    pass

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
    start_marker: str = ""           # custom decoration at points[0]; "" = none (see WIRE_MARKER_KINDS)
    end_marker: str = ""             # custom decoration at points[-1]; "" = none (see WIRE_MARKER_KINDS)
    start_label: str = ""            # text/math label just beyond points[0]; "" = none
    end_label: str = ""              # text/math label just beyond points[-1]; "" = none
    mid_label: str = ""              # text/math label drawn over the wire (solid bg); "" = none
    mid_label_pos: float = 0.5       # mid_label position as a fraction of arc-length (0..1)
    # All vertices lie on 0.25 GU boundaries
    # All consecutive segment pairs are strictly horizontal or vertical
    # The point list is kept minimal: no consecutive duplicates and no
    # redundant collinear interior vertices (see §6.4 "Wire Simplification")
```

`no_junction_dots` flags a wire as an *annotation* rather than a real electrical connection: when set, the wire is skipped entirely by `junction_points()` (§6.4), so the solid `circ` dots it would otherwise create (where it meets other wires/pins) are suppressed — other wires/pins at the same coordinate still count, so a dot they independently justify is unaffected. Useful e.g. for leads into a voltage annotation. It does not change connectivity for code generation otherwise.

`no_termination_dots` likewise excludes the wire from `open_endpoints()` (§6.4), suppressing the `ocirc` open-circle markers at the wire's own dangling ends. It still counts toward *other* wires' connection detection (an endpoint of another wire landing on it stays connected), so only this wire's free ends lose their terminals.

**Custom endpoint markers.** `start_marker` (at `points[0]`) and `end_marker` (at `points[-1]`) place a *user-chosen* decoration at a wire end — distinct from the topology-derived `circ`/`ocirc` dots above. The valid kinds are listed in `WIRE_MARKER_KINDS`: `""` (none), `"arrow"` (filled `Latex` tip), `"stealth"` (sharp filled `Stealth` tip), `"open"` (outlined `Latex[open]` tip), and `"bar"` (a perpendicular `Bar` terminal). All exist primarily to draw **block diagrams**, and each end chooses independently. A marker is the user's explicit choice, so an end bearing one is **excluded from `open_endpoints()`** — the marker replaces the automatic open-circle terminal at that specific end (the other end is unaffected, and the marked end still counts as a connection for other wires). Markers do not interact with `junction_points()`. The arrow tips come from TikZ's `arrows.meta` library, which the export pipeline loads (§8.4).

**Endpoint labels.** `start_label` (beyond `points[0]`) and `end_label` (beyond `points[-1]`) place a text/math caption at a wire end — e.g. an arrow marker terminating *into* `$y(t)$`. Each is a raw LaTeX fragment (same convention as a text annotation's content): `$…$` typesets as math, plain text renders verbatim. The label sits on the far side of the endpoint along the terminal segment, with a small gap (`_WIRE_LABEL_GAP` ≈ 0.1 GU) clearing the wire end / arrow tip. Labels are orthogonal to markers and to the automatic dots — they do **not** suppress an `ocirc` (a labelled open terminal is allowed; the arrow marker, if present, is what suppresses it). In the LaTeX output each non-empty label is a `\node[anchor=…, inner sep=0] at (x,y) {…};` whose anchor is derived from the terminal segment's outward direction *in emitted (post-Y-flip) space*, so it stays on the correct side under the preview flip. `inner sep=0` strips the node's default ~3.3 pt padding so the visible gap equals the 0.1 GU offset and matches the canvas (whose label clearance has no padding). On the canvas the label is typeset math (via the shared async `render_async` path, §8.4) positioned just beyond the endpoint. **Double-clicking a rendered label** opens an in-place editor — the shared `LabelTextItem` (`QGraphicsTextItem`) pre-filled with the raw LaTeX fragment, positioned at the label; **Enter** or focus-loss commits via `set_wire_start_label`/`set_wire_end_label`, **Escape** cancels, mirroring component-label editing (§5.8). The display label is hidden while editing and restored when editing ends (commit *or* cancel, via `LabelTextItem`'s end-callback). A label can also be **started from a bare endpoint**: double-clicking a free wire endpoint (no label yet) opens the same editor for that end (§6.4) — so no inspector trip is needed to add one. Connected (pin-locked) endpoints are not label targets.

**Mid-wire label.** `mid_label` is a text/math caption drawn **over** the wire — centred on the wire at the fractional arc-length position `mid_label_pos` ∈ [0, 1] (`wire_point_at_fraction`), with an **opaque (white) backdrop** so the line does not run through the text. Same LaTeX-fragment convention as the endpoint labels. Use for captioning a signal/bus mid-run. It is **draggable along the wire** on the canvas: pressing the rendered label and dragging projects the cursor onto the polyline (`wire_fraction_at_point`) and, on release, commits the new fractional position via `set_wire_mid_label_pos` (`SetWireMidLabelPosCommand`); the position is a fraction of arc-length, so it survives reshaping the wire. Double-clicking the label opens the same in-place editor (`begin_label_edit("mid")`). A mid-label is added through the inspector's **Middle** field (it appears at the midpoint), then dragged/edited on the canvas. In the LaTeX output it is a `\node[fill=white, inner sep=1pt] at (x,y) {…};` emitted after the wire draw so it paints on top.

`line_style` / `line_width` / `no_junction_dots` / `no_termination_dots` / `start_marker` / `end_marker` / `start_label` / `end_label` / `mid_label` / `mid_label_pos` are edited via the wire property inspector (§10.3) and the canvas, and are undoable (`SetWireLineStyleCommand` / `SetWireLineWidthCommand` / `SetWireNoJunctionDotsCommand` / `SetWireNoTerminationDotsCommand` / `SetWireStartMarkerCommand` / `SetWireEndMarkerCommand` / `SetWireStartLabelCommand` / `SetWireEndLabelCommand` / `SetWireMidLabelCommand` / `SetWireMidLabelPosCommand`). All are persisted only when non-default, so plain wires' JSON is unchanged; old files without them load as solid / 0.4 pt / no markers. On the canvas the pen width is proportional (`LINE_W × line_width/0.4`, so the 0.4 pt default keeps the existing 2 px appearance), and endpoint markers render at the wire ends as on-canvas approximations of their export tips (filled/concave/outlined triangles, or a bar). In the LaTeX output a wire that has a non-default style **or** an endpoint marker is emitted as its own `\draw[<spec>] (…) -- (…);` statement (default wires stay in the shared `\draw` path); the arrow spec (an `arrows.meta` form such as `-{Latex}`, `{Latex}-`, or `{Stealth}-{Latex}`) leads the option list, followed by any style options. See §8.

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
    version: str                     # Spec version this was created under, e.g. "0.1"
    name: str                        # User-visible schematic name
    components: list[Component]
    wires: list[Wire]
    metadata: dict[str, Any]         # Arbitrary key-value store for future use
```

### 4.5 Invariants

The following must hold at all times for a valid schematic:

1. All `Component.kind` values exist as keys in `REGISTRY`.
2. All `Component.rotation` values are in `{0, 90, 180, 270}`.
3. All `Wire.points` vertices lie on 0.25 GU boundaries (the minor grid, §3.1). Because every pin offset is a multiple of 0.25 and components snap to the 0.25 grid, a wire endpoint that follows a moved pin stays on grid.
4. All consecutive wire segment pairs are strictly horizontal or vertical (Manhattan constraint).
5. No two components share the same `id`; no two wires share the same `id`.

Note: distinct wires **may** share vertex coordinates — that is how connections
and multi-wire junctions are formed (see §6.4). Sharing a coordinate is a valid
connection, not an id collision.

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
LINE_W   = 2.0    # stroke width for component bodies and wires
PIN_R    = 3.0    # radius of pin indicator dot
LEAD_LEN = 15     # length of lead-in/lead-out wire stubs, in pixels
```

All `ComponentItem` subclasses import these constants, ensuring consistent proportions across all component types regardless of zoom level (Qt's `QGraphicsView` scales the painter automatically).

#### Symbol Source — SVG Reference Files

**All component symbols must be derived from CircuiTikZ SVG exports.** Hand-drawn symbols are prohibited — they will inevitably diverge from what CircuiTikZ actually renders and produce previews that don't match the canvas.

Symbols come from **CircuiTikZ SVG exports** produced by the single deterministic Python pipeline `tools/export_circuitikz_svgs.py`. One run (1) renders each component with `latex` + `dvisvgm` (`[american]` option) to a normalised `.svg` on disk, then (2) compiles those SVGs into a **self-contained** `tools/circuitikz_svgs/manifest.json`. **The application reads only the manifest at run time** (`app/canvas/svgsym.py`) — it never touches the `.svg` files; the SVGs are intermediate build artifacts. `python tools/export_circuitikz_svgs.py` rebuilds both; `--no-render` rebuilds just the manifest from the existing SVGs. Output is byte-stable (dvisvgm writes no timestamp), so re-running on the same toolchain reproduces identical files.

The pipeline supports three component categories, each with an output subdirectory:

| Category | Subdirectory | LaTeX template | Examples |
|----------|-------------|----------------|---------|
| `bipoles` | `bipoles/` | `\draw (0,0) to[kind] (2,0);` | R, C, L, D |
| `tripoles` | `tripoles/` | `\node[kind] (X) at (0,0) {}; <leads>` | op amp, nigfete |
| `nodes` | `nodes/` | `\draw (0,0) node[kind] {};` | ground, sground, cground, vcc, vdd, vee, vss |

The component set is exactly what the registry uses (defined in the `BIPOLES`/`NODES`/`TRIPOLES` tables in the script); multi-terminal parts carry per-component lead routing that extends each named terminal anchor to a grid-aligned coordinate.

**Manifest schema.** Each entry is keyed by component name and holds `kind`, `name`, `viewBox`, `width_pt`/`height_pt`, and two geometry lists (both in SVG point coordinates):

- **`paths`** — the stroked/filled body geometry: `{d, stroke_width, fill, stroke}`.
- **`glyphs`** — text marks (the `+`/`−` of sources). dvisvgm emits these as `<use>` references into `<defs>`; the pipeline **resolves them at build time** into `{d, matrix, stroke_width}`, where `matrix` is the composed affine (enclosing-group matrix ∘ `<use>` translation). This is why the manifest is self-contained — no `<use>`/`<defs>` indirection survives, so the app needs no SVG access. `svgsym.symbol_paths` paints each glyph as a filled body via `QTransform(*matrix)` then the component transform.

**Diode body scale.** CircuiTikZ's default diode body is visually large next to the other bipoles, so every diode-family symbol (`D`/`zD`/`sD`/`tD`/`zzD`/`leD` and their filled `*` variants) is rendered with `\ctikzset{diodes/scale=0.8}` and the code generator emits the **same** picture-scoped `\ctikzset{diodes/scale=0.8}` for any schematic containing a diode (see §7.2). `DIODE_SYMBOL_SCALE` in `app/codegen/circuitikz.py` and `DIODE_SCALE` in the export script are the two sources of truth and **must match**. The scale shrinks only the body (the 2-GU span and pin positions are unchanged — leads auto-extend), and it does not affect the MOSFET body-diode (a tripole shape), so the canvas and the rendered output stay in sync.

To add a new component: add it to the relevant table in `tools/export_circuitikz_svgs.py` and re-run it, then add the `Placement` anchor in `svgsym.py` (see §5.5 for the measurement procedure) and an `ITEM_CLASSES` entry in `items.py`.

To implement a new `ComponentItem`, look up the component in `manifest.json`, read the `paths` (and `glyphs`) arrays, and translate each path `d` string into `QPainterPath` calls:

| SVG command | `QPainterPath` equivalent |
|-------------|--------------------------|
| `M x y` | `path.moveTo(x, y)` |
| `L x y` | `path.lineTo(x, y)` |
| `H x` | `path.lineTo(x, path.currentPosition().y())` |
| `V y` | `path.lineTo(path.currentPosition().x(), y)` |
| `C x1 y1 x2 y2 x y` | `path.cubicTo(x1, y1, x2, y2, x, y)` |
| `Z` | `path.closeSubpath()` |

Coordinates are scaled from SVG pt units to `GRID_PX` by dividing by the SVG `viewBox` height and multiplying by the component's height in pixels. The SVG y-axis matches Qt's (y-down), so no axis flip is required.

**Fill rule.** dvisvgm emits a solid body (the filled diode triangle, transistor/LED arrowheads, …) as a **bare** `<path>` — no `stroke` and no `fill` attribute — which in SVG is the default **black fill**; the body's outline is a separate `fill='none'` stroke path. The export pipeline records a bare path's fill as `#000` (the SVG default), so `svgsym.symbol_paths` treats a path as **filled** unless its fill is the explicit `none`. (An earlier version recorded the absent attribute as `none`, which made `D` and `D*` — and every other solid body — render identically; the regression test is `test_filled_diode_body_is_filled`.) `stroke_width` values in the manifest are relative — thin strokes (≈0.4pt) map to `LINE_W`; thick strokes (≈0.8pt) map to `LINE_W * 2`. The `glyphs` list is always filled.

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

- **Display** — one `_SlotLabel` child per non-empty annotation slot, placed above/below the body and counter-rotated to stay upright. Slot labels are non-interactive (display only).
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

**Diodes** — all share pins `anode` (0,0) and `cathode` (2,0), default span (2,0), and label slots `l`, `l_`, `v`, `v^`, `i`, `i_`. Instantiated as `DiodeComponent`; when `DiodeComponent.filled` is `True` the canvas uses the `*` SVG and the codegen emits `KIND*`.

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

The **Bipole** (`bipole`) component appears last in the Bipoles group — see §7.7.

The LED bbox is slightly taller (y0=−0.75, y1=0.75) to accommodate the emission arrows. The resistor and inductor bboxes are tighter perpendicular to the leads (y0=−0.25, y1=0.25) — snug around the zigzag (±0.21 GU) / humps (≈0.20 GU) so their side labels sit close to the body (§5.8). The capacitor keeps ±0.5 (its plates reach ±0.42 GU).

The **Filled** checkbox appears in the Properties panel for any component that is an instance of `DiodeComponent`. It is backed by an undoable `SetFilledCommand`.

The **Body diode** checkbox appears in the Properties panel for any component that is an instance of `MosfetComponent` (nigfete, nigfetd, pigfete, pigfetd). When checked, the canvas uses the `*_bodydiode` SVG variant and the codegen emits `bodydiode` as an additional node option (e.g. `node[nigfete, bodydiode, xscale=1.0167, anchor=gate]`). It is backed by an undoable `SetBodyDiodeCommand`.

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

All four MOSFET variants share the same pin x-offset (0.9836 GU from gate, corrected to 1.0 GU via `xscale=1.0167`). N-channel symbols have `drain` at the top (`-1.0` Qt y) and `source` at the bottom (`+0.5` Qt y); P-channel symbols are mirrored with `source` at top (`-0.5` Qt y) and `drain` at bottom (`+1.0` Qt y). Enhancement mode = three channel dashes; depletion mode = solid channel line.

**BJTs:**

| Kind | Display Name | Pins | Label Slots |
|------|-------------|------|-------------|
| `npn` | NPN BJT | `base` (0,0), `collector` (1.0,−1.0), `emitter` (1.0,1.0) | `l` |
| `pnp` | PNP BJT | `base` (0,0), `emitter` (1.0,−1.0), `collector` (1.0,1.0) | `l` |

Both BJTs are placed with `anchor=B` (base pin) at `Component.position`. SVG symbols are exported with TRIPOLE_LEADS (`\draw (X.C) -- (0.0129,1)` etc.) that extend the collector/emitter leads to grid-aligned endpoints so the canvas preview is correct. For the LaTeX output, codegen applies `xscale=1.181, yscale=1.287` (same strategy as MOSFETs) to stretch the symbol so the C/E anchors land exactly on the (1.0, ±1.0) GU grid — no bridge lead wires needed. The scale factors are derived from the unextended CTikZ pin offsets: actual (0.847, 0.777) GU → snapped (1.0, 1.0) GU. For NPN: collector at top-right (Qt y = −1.0), emitter at bottom-right (Qt y = +1.0). For PNP: emitter at top-right, collector at bottom-right.

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

The `open` component renders a **translucent** (mostly-opaque) solid line between its two endpoints — drawn at `OPEN_ANNOTATION_OPACITY` (0.5) so it is visually distinct from both solid and dashed wires — with a voltage/current annotation from the options string. Unlike other components, its annotation labels are **centred over the middle of the line** rather than offset to a side, mirroring where CircuiTikZ draws the voltage/current arrow label (see §5.8). When selected, square drag handles appear at both endpoints; dragging the terminal handle resizes the span (updating `Component.span_override`). The resize is undoable via `ResizeCommand`. Connected wires follow the moved endpoint.

#### Drawing Annotations

Drawing annotations are non-circuit visual elements that appear in the palette under the **Drawing** category. They have no circuit pins and do not participate in connectivity, junction detection, or open-endpoint detection. They are emitted as standalone LaTeX commands after the main `\draw` block in the generated output.

| Kind | Display Name | Pins | Default Span | Resizable | Inspector Controls |
|------|-------------|------|-------------|-----------|-------------------|
| `text_node` | Text | none | (0,0) | No | Text content field, Font size spinbox (6–72 pt), Bold/Italic checkboxes, Font family combo, Z-order spinbox, Rotation buttons (0°/90°/180°/270°) |
| `rect` | Rectangle | none | (2,2) | Yes (corner drag) | Line style combo, Border width spinbox (pt), Fill color combo, Move to front/back buttons, Z-order spinbox |

#### Bipole Component (`bipole`)

The `bipole` kind is a generic labelled rectangular box representing an arbitrary two-terminal subsystem (named "Bipole" to the user; distinct from the **Bipoles** palette *category* it sits in). Wires connect to its left (`in`) and right (`out`) pins. Although two-terminal, it is **not** emitted via the CircuiTikZ `to[...]` path syntax — it is rendered as a standalone `\node` (see Code generation below), like the other `DrawingComponent` kinds.

| Kind | Display Name | Category | Pins | Default Span | Resizable |
|------|-------------|----------|------|-------------|-----------|
| `bipole` | Bipole | Bipoles | `in` (0,0), `out` (1,0) | (1,0) | Yes (right endpoint drag) |

**Model:** `BipoleComponent(FontedComponent, StyledComponent, DrawingComponent)` — composes both capability mixins (gains `font_*` for the label and `fill_color`/`border_width`/`line_style` for the box) over the `DrawingComponent` base (`z_order`). `options` holds a CircuiTikZ-style option string; the `t=` slot sets the label inside the box. Other slots (`l=`, `v=`, `i=`) are stored in options but not rendered in the LaTeX output (they don't apply to a standalone TikZ node).

**Canvas rendering (`BipoleItem`):** Extends both `_DrawingAnnotationBase` (for z-order) and `_ResizableTwoTerminalItem` (for span/resize). Draws a rectangle of half-height `_BIPOLE_HALF_H` (0.25 GU) centered on the connecting line, from the origin pin to the terminal pin, using the `fill_color`, `border_width`, and `line_style` from the StyledComponent fields. The pen style is resolved from `line_style` via the shared `_resolve_pen_style()` helper (same mapping as `RectItem`), so dashed/dotted borders render on the canvas. The `t=` label is drawn centered inside the rectangle. Pin dots appear at both endpoints. A square resize handle at the terminal (right) endpoint is shown when selected. The hit region is the full rectangle interior plus the resize handle.

**Resizing:** Dragging the right endpoint handle changes `span_override`. The resize directly controls the box width in both the canvas preview and the LaTeX output. Committed via `ResizeCommand`.

**Properties inspector:** The capability sections that apply to a bipole are `BipoleLabelSection` (label `t=` + other options), `FontSection`, `FillBorderSection` (line style, border width, fill), `TransformSection` (rotation + mirror), and `LayerSection` (front/back + z-order). See §10.3 for the section architecture. `line_style` is edited through the shared `FillBorderSection` (the same control rect uses), so bipoles support dashed/dotted borders.

**Inline label editing:** Double-clicking a `BipoleItem` activates an inline text editor centred inside the box showing only the `t=` label text (not the full options string). On commit the edited text is spliced back into `options` using `_replace_bipole_label`, preserving all other slots. The painted label is suppressed while the editor is active.

**Fill color, border width, line style** — carried by the shared `StyledComponent` mixin (same fields as rect): `fill_color: str` (default `""`, TikZ color e.g. `"yellow!20"`, empty = transparent; palette None/White/Light gray/Yellow/Blue/Green/Red), `border_width: float` (default `0.4` pt), and `line_style: str` (default `""` = solid, raw TikZ tokens e.g. `"dashed"`). Each is saved in JSON only when non-default and rendered on canvas (`_resolve_tikz_color` for fill; pixel-equivalent width for the border). Edited via per-field undoable commands (`SetFillColorCommand`, `SetBorderWidthCommand`, `SetLineStyleCommand`).

**Code generation:** Bipole is NOT in `_TWO_TERMINAL_KINDS`. It is handled in the same background/foreground drawing-annotation passes as `rect` and `text_node`, via `_bipole_node_line()`. Emits a standalone TikZ node whose dimensions are derived from `span_override` so the box exactly fills the pin-to-pin space (example with a 3 cm custom span):
```latex
\node[draw, minimum width=3cm, minimum height=0.5cm] at (1.5,0) {Processor};
% with fill and border width:
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
`RectComponent.position` is the first corner (top-left when span is positive). `RectComponent.span_override` (or `default_span` = (2,2) when not set) gives the offset `(dx, dy)` to the opposite corner. The draw style is carried by the shared `StyledComponent` fields — `line_style` (e.g. `dashed`, `dotted`, `dash dot`), `border_width` (pt), and `fill_color` — and composed into the `\draw[…]` argument by `compose_style_options()` (the same helper used for `bipole`). `RectComponent.options` is unused; legacy files that stored the style string in `options` are migrated into the fields on load (and `options` cleared) by `schematic/io.py`. The canvas item draws the rectangle with the selected style and shows a square drag handle at the far corner when selected (no circuit pin dots). Resizing via the corner handle is undoable via `ResizeCommand`; style edits via the per-field `SetFillColorCommand` / `SetBorderWidthCommand` / `SetLineStyleCommand`.

New rects default to `z_order = -10` (behind circuit elements). TikZ color strings in `fill=` (e.g. `yellow!20`, `gray!15`) are resolved to Qt colors using the `color!percent` mixing formula (percent% of the named color blended with white) before rendering on the Qt canvas. The hit region for selection is the full rectangle interior (not just a band along the diagonal), so clicking anywhere inside the rect selects it.

The `LayerSection` of the inspector — shared by all `DrawingComponent` kinds (text_node, rect, bipole) — shows **Move to front** and **Move to back** buttons: "Move to front" sets `z_order` to `max(all existing z_orders) + 1`; "Move to back" sets it to `min(all existing z_orders) - 1`. Both operations are undoable via `SetZOrderCommand` and update the Z-order spinbox. Code generation emits:
```latex
\draw[dashed, line width=1.5pt, fill=yellow!20] (x1,y1) rectangle (x2,y2);
% solid with no extra options:
\draw (x1,y1) rectangle (x2,y2);
```

**Z-order (`DrawingComponent.z_order`):** An integer field on `DrawingComponent` (default 0), stored in the JSON file (omitted when 0 for backward compat). Applies to `text_node` and `rect`. On the Qt canvas, maps to `QGraphicsItem.setZValue()`. In the LaTeX output, controls emission order:
- `z_order < 0` → emitted **before** the main `\draw` block (behind circuit elements in the PDF).
- `z_order ≥ 0` → emitted **after** the `\draw` block and junction/open-endpoint nodes (in front).

Changed via `SetZOrderCommand` (undoable) through `scene.set_component_z_order()`.

**SetFontSizeCommand:** An undoable command that sets `TextNodeComponent.font_size`.

**SetTextStyleCommand:** An undoable command that sets `font_bold`, `font_italic`, and `font_family` together on a `TextNodeComponent`. All three values are stored and restored atomically so a single undo reverts the entire style change.

**SetZOrderCommand:** An undoable command that sets `DrawingComponent.z_order`.

The palette category display order is: **Bipoles → Tripoles → Nodes → Annotations → Drawing**.

### 5.5 Multi-Terminal Pin Geometry — Alignment Procedure

CircuiTikZ multi-terminal nodes have internal pin anchor positions that do not
fall on the 0.5-GU canvas grid. This section documents the procedure for
aligning them when adding a new component.

#### Background: two independent lead/correction mechanisms

There are **two entirely separate mechanisms** that are easy to confuse. Both
may be needed for a single component, but they serve different purposes:

| Mechanism | Where | Purpose |
|-----------|-------|---------|
| **Tripole lead routing** in `tools/export_circuitikz_svgs.py` (the `TRIPOLES` table) | Canvas SVG export only | Extends the exported SVG paths so their endpoints (= the values read by `svgsym.py`) land on the grid. Has **no effect** on the LaTeX output. |
| **`_MULTI_TERMINAL_LEADS`** in `app/codegen/circuitikz.py` | LaTeX output only | Emits explicit `\draw (node_id.PIN) -- (grid_coord)` bridge wires in the generated LaTeX to bridge from a CTikZ anchor to the registry grid position. Has **no effect** on the canvas. |
| **`_MULTI_TERMINAL_EXTRA_OPTS`** (`xscale`/`yscale`) in `app/codegen/circuitikz.py` | LaTeX output only | Stretches the CTikZ symbol so its anchors land on the grid — **no bridge wires needed**. Also requires a matching scale in `svgsym.py` `Placement` when TRIPOLE_LEADS are NOT used for the canvas. |

**Critical invariant — scale correction and bridge wires are mutually exclusive
for a given pin.** If `_MULTI_TERMINAL_EXTRA_OPTS` contains an xscale/yscale
that moves a pin onto the grid, then `_MULTI_TERMINAL_LEADS` for that component
must be `[]` (empty). Adding both double-corrects the position and produces
misaligned symbols in the LaTeX output. Conversely, if bridge wires are used,
do not add a scale correction for the same axis.

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
re-exported `manifest.json` after adding TRIPOLE_LEADS (Step 4).

#### Step 2 — Choose registry pin positions (0.5-GU snap)

Round each measured pin position to the nearest 0.5 GU. These become the
`PinDef.offset` values in `REGISTRY`. The registry pins define where wires
connect on the canvas, so they must be on-grid.

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
`(0,0)`). Re-run the script to regenerate the SVGs and `manifest.json`.

After regeneration, read the lead endpoint SVG coordinates from `manifest.json`
to determine the correct `svgsym.py` anchor:

```python
anchor = (base_lead_svg_x, base_lead_svg_y)  # primary pin lead final point
```

Verify local pixel coordinates from anchor:
```python
local_x = (pin_svg_x - anchor_x) / 28.348  # should match registry offset
local_y = (pin_svg_y - anchor_y) / 28.348
```

#### Step 5 — Update codegen tables

Add entries to `app/codegen/circuitikz.py` (all four tables must have an entry
for every multi-terminal kind registered in `REGISTRY`):

| Table | Entry |
|-------|-------|
| `_MULTI_TERMINAL_KINDS` | add `"KIND"` to the frozenset |
| `_MULTI_TERMINAL_ANCHOR_PIN` | `"KIND": ("ctikz_anchor", "registry_pin_name")` |
| `_PIN_TO_CTIKZ_ANCHOR` | `"KIND": {"pin": "anchor", ...}` — every registry pin |
| `_MULTI_TERMINAL_EXTRA_OPTS` | scale correction string, or omit if Strategy B |
| `_MULTI_TERMINAL_LEADS` | `[]` if Strategy A (scale), or `[(pin, anchor), ...]` if Strategy B |

A startup validation check fires at import time and raises `RuntimeError` if
any registered multi-terminal kind is missing from `_PIN_TO_CTIKZ_ANCHOR` — this
prevents silent fallback to bare coordinates.

### 5.6 Extensibility

To add a new component type:

1. Add the component to the relevant table in `tools/export_circuitikz_svgs.py` and run it to render the SVG and rebuild the self-contained `manifest.json`.
2. Add a `ComponentDef` entry to `REGISTRY` in `app/components/registry.py`, with `bbox` and `pins` derived from the SVG `viewBox` dimensions and CircuiTikZ anchor positions.
3. Add a `ComponentItem` subclass to `app/canvas/items.py`, translating the manifest `paths` array to `QPainterPath` calls as described in §5.2.
4. Add the mapping entry to `ITEM_CLASSES` in `app/canvas/items.py`.
5. No changes to the schematic model, code generator, or UI layout are required.

### 5.7 Component Symbol Conventions

All canvas symbols follow **American/IEEE style**, matching the `[american]` CircuiTikZ option used to generate the SVG reference files. This ensures pixel-accurate visual correspondence between the canvas and the compiled LaTeX output.

#### General Drawing Rules

- All symbols are drawn as `QPainterPath` geometry translated from `tools/circuitikz_svgs/manifest.json`. No external image assets are used.
- Stroke width is `LINE_W` for normal strokes and `LINE_W * 2` for thick strokes (e.g. gate electrodes). At palette thumbnail scale (32×32px), stroke width is reduced to `LINE_W_THIN` to prevent fine detail from filling in.
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

### 5.8 On-Canvas Math Rendering (WYSIWYG labels)

Component labels, `text_node` content, and `bipole` box text are shown as
**typeset math**, rendered to vector by `app/preview/mathrender.py`. This reuses
the exact toolchain that produces the component symbols (§5.2): a LaTeX fragment
is wrapped in a `standalone` document and run through `latex → dvisvgm
--no-fonts → SVG`, and the SVG paths are parsed by the same
`svgsym.parse_path()` into a `QPainterPath`. No raster step is involved, so
labels stay crisp at every zoom.

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
  half-width for vertical) plus a gap; **current** (`i=`) labels instead hug the
  wire (a small `_CURRENT_GAP` off the lead axis), matching where CircuiTikZ
  draws them. Each `_SlotLabel` is counter-rotated to stay upright and stacks
  outward when several share a direction. This is a readable convention,
  **not** a pixel-exact reproduction of CircuiTikZ's voltage/current arrow
  rendering (no ± signs or direction arrows are drawn on the canvas).
    - **Centred placement.** `ComponentItem._labels_centered_on_axis()` (default
      `False`) lets a component pin its labels *over* the lead axis instead of
      beside it: when `True` the base clearance is zeroed and the label centre
      is placed at the component centre (siblings still stack along the offset
      direction). The `open` voltage annotation overrides it to `True` so its
      label sits over the middle of the line, matching where CircuiTikZ draws
      the arrow label. A centred label is painted over an opaque white backdrop
      padded by `_LABEL_BG_PAD` (3 px) so the annotation line does not appear to
      run into the text.
- **Hover association.** Hovering the component body *or* any of its slot labels
  highlights the whole group (body + all slot labels) in `COLOR_HOVER`, so it is
  clear the labels belong to that component. `_SlotLabel` forwards hover events
  to `ComponentItem._set_hovered()`, which repaints the body and every slot.
- **Caching.** Two tiers: an in-process `lru_cache` of parsed paths, and an
  on-disk cache of compiled SVG text keyed by a content hash (with a
  `_RENDER_VERSION` prefix so template changes invalidate it). A failed compile
  writes an empty sentinel so it is not retried. Reopening a file re-parses
  cached SVGs without invoking `latex`.
- **Async, non-blocking.** `render_async(fragment, on_done)` runs the compile on
  a bounded `QThreadPool` (2 workers) and delivers the result back on the UI
  thread via a queued signal. Until the path arrives — or if `latex`/`dvisvgm`
  are missing — items fall back to raw-text rendering, so the canvas never
  blocks and degrades gracefully. Queued callbacks guard with
  `shiboken6.isValid` so a render landing after its item was deleted is dropped.

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
2. A ghost (semi-transparent) rendering of the component follows the cursor, snapping to 0.25 GU.
3. Left-click places the component at the snapped position and records an undoable `PlaceCommand`.
4. Right-click or `Escape` cancels placement and returns to **Select** mode. `Escape` is registered as a **window-level shortcut** so it fires regardless of which widget (palette, canvas, etc.) currently holds keyboard focus — clicking a palette entry to start placement does not require a subsequent click on the canvas before Escape works.
5. After placement, the canvas remains in **Place** mode for rapid repeated placement of the same component type.

### 6.3 Component Selection and Movement

- Left-click a component to select it (deselects others). Pressing on a
  component's **body** selects/drags it; pressing on a free pin instead starts a
  wire (see §6.4 "Auto-enter wire mode").
- `Ctrl+click` adds to or removes from the selection.
- Rubber-band drag (drag on empty canvas) selects all components and wire segments within the rectangle.
- `Ctrl+A` selects all.
- Selected components can be dragged; the component item snaps to 0.25 GU **during** the drag (not only on release), so the visual position always lands on a grid point. Movement records a `MoveCommand`. Component drag/selection is enabled **only in Select mode** — in Place/Wire/Pan modes component items are non-movable and non-selectable so a stray press cannot desync an item from its model position.
- **Wires follow the components they connect to.** When a component moves (by drag or by arrow-key nudge), any wire endpoint coinciding with one of its pins moves by the same delta. A connected endpoint that would leave its adjacent segment diagonal gets an auto-elbow inserted to stay Manhattan; if both ends of a wire ride the same move, the whole polyline translates rigidly. **When all components in the schematic are moved together (select-all drag), every wire translates rigidly regardless of connectivity** — free (open-circle) endpoints move with the rest of the circuit instead of being left behind. **Explicitly-selected wires** (rubber-band selection includes wire items) are also translated rigidly as part of the drag — the scene passes the selected wire IDs to `MoveCommand` via the `wire_ids` parameter, and the preview treats those wires the same way. The reshape is part of the same `MoveCommand` and is fully reversed on undo. A live ghost of the reshaped, simplified wires is shown during the drag.
- `R` rotates the selection 90° CW around the bounding-box centroid of the selected component positions (snapped to 0.25 GU); records a `GroupRotateCommand`. When a single component is selected the centroid equals its own position, so it spins in place. Connected wires are reshaped or rigidly rotated according to whether their other endpoint is inside or outside the selection (see §6.6 `GroupRotateCommand` note). `Component.label_offset` is cleared for each rotated component so the label auto-repositions. In **Place** mode `R` cycles the ghost's rotation instead.
- Arrow keys nudge selected components by `NUDGE_GU` (0.25 GU, one minor-grid cell) per keypress, in any direction. Connected wires follow (§6.6) and stay grid-valid.
- `Delete` or `Backspace` deletes the current selection — components (and any wires connected to their pins) **and** any directly-selected wires; records a `DeleteCommand`.

### 6.4 Wire Routing

A wire is an ordered list of Manhattan-routed points (§4.3). The whole feature is
built on **one** routing primitive and **one** simplification pass, reused by
every code path (drawing preview, drawing commit, vertex drag, component
follow). Duplicating the corner/elbow math across paths is the historical source
of preview-vs-commit disagreement and accumulating vertices, and is prohibited.

#### The routing primitive

`route(a, b)` returns the Manhattan path between two points as either `[a, b]`
(when they already share an x or y) or `[a, corner, b]` (one auto-corner). The
corner orientation is **deterministic by dominant axis**: travel along the
longer axis first — horizontal-first when `|bx − ax| ≥ |by − ay|`, otherwise
vertical-first. There is **no** modifier key to flip it; the user controls
routing by dropping intermediate vertices instead. An "elbow" inserted to keep a
shifted segment Manhattan is simply `route(a, b)` with the corner orientation
the caller needs — the same function, never a re-implementation.

#### Drawing

A wire-in-progress is an ordered list of committed vertices. The first comes
from the starting click; the live **wire ghost** previews `route(last_vertex,
cursor)` from the last committed vertex to the snapped cursor and updates on
every move.

1. In **Wire** mode, left-click a pin, an existing wire, or any grid point to begin a wire (the first vertex).
2. Move cursor → the ghost shows the dominant-axis `route()` L from the last committed vertex to the cursor.
3. Left-click on an empty grid point **commits the previewed L** — both its auto-corner (if any) and the clicked point become committed vertices — and routing continues from there. This is how the user places intermediate corners and steers the path.
4. Left-click on a pin, an existing wire vertex, or a wire segment **finalizes** the wire (committing the previewed L to that target) and returns to **Select** mode (see "Finalizing").
5. Double-click on an empty grid point finalizes the wire at the cursor as a free (open) endpoint and **stays in Wire** mode for continued routing.
6. `Escape` discards the wire in progress (clearing the ghost) without leaving Wire mode.

On finalize the committed point list is passed through `simplify_points`; if it
collapses to fewer than two distinct points the wire is **discarded** (no
degenerate zero-length wire is created). A committed wire is recorded as a
`WireCommand` (or a `MacroCommand` when a split is also required).

#### Snapping

The snapped cursor target is resolved (within `PIN_SNAP_GU` = 0.125 GU) with this priority:

1. **Component pin** — connects to the pin.
2. **Existing wire vertex** — connects to that wire, forming a junction.
3. **Point on an existing wire segment** — connects mid-segment (see "Junctions and segment splitting").
4. Otherwise the bare **0.25 GU grid node** under the cursor.

Priorities 1–3 are **connectable** targets; the ghost's end marker distinguishes
a connectable snap (ring) from a plain grid-node anchor (dot), and connectable
targets are what finalize the wire.

#### Finalizing and mode transitions

- In **Select** mode, left-clicking an **unconnected** pin (a pin with no wire endpoint on it) auto-switches to **Wire** mode and begins a wire there. Clicking a connected pin, or a component body, does normal selection/drag instead. The auto-start uses a tight grab radius so a press near a component's centre still selects/drags the component.
- In **Select** mode, **double-clicking a wire's rendered label** — an endpoint label (`_WireEndLabel`) or the mid-wire label (`_WireMidLabel`) — opens its in-place text editor (§4.3) instead of routing. These checks run **before** the wire-body check so labels aren't shadowed by the "double-click wire → Wire mode" gesture. The **mid-wire label is also draggable**: a left-press on it (handled in the scene's `mousePressEvent`, ahead of vertex-drag/selection) starts a drag that slides it along the wire; `mouseMoveEvent` previews and `mouseReleaseEvent` commits the new fractional position. A no-movement press falls through to the double-click edit.
- In **Select** mode, **double-clicking a free wire endpoint** (a draggable first/last vertex, via `wire_vertex_at`) opens that endpoint's label editor (§4.3) too — so a label can be started even when none is set yet and there is no rendered label to click. Only *draggable* endpoints qualify: a pin-locked (connected) endpoint and any interior vertex are **not** returned, so they fall through to the wire-body routing gesture below. This check runs after the rendered-label check and before the wire-body check.
- In any mode, **`Tab` while the cursor hovers a wire** cycles styling at the cursor without selecting (handled in `SchematicView.event()`, ahead of Qt's focus navigation, via `SchematicScene.cycle_at`): over a **free endpoint** it cycles that endpoint's marker (`WIRE_MARKER_CYCLE`: none → arrow → stealth → open → bar → none); over a **wire body** (or an interior/connected vertex) it cycles the line style (`WIRE_LINE_STYLE_CYCLE`: solid → dashed → dotted → dash-dot → solid). **`Shift+Tab`** steps backward. Each step is an undoable `set_wire_*` command. When a label editor is focused, `Tab` is left to the editor. If the cursor is over nothing, `Tab` keeps its normal focus-navigation behaviour.
- In **Select** mode, **double-clicking on a wire** (segment body or interior vertex) auto-switches to **Wire** mode and begins routing from the clicked point. (To extend a *free endpoint* into a new leg — now that an endpoint double-click edits its label — double-click the segment just inside the endpoint; it snaps to the endpoint vertex.) The start point snaps to the nearest wire vertex (within `PIN_SNAP_GU`) or the nearest point on a wire segment (projected to the segment, grid-snapped to 0.25 GU). Any split needed by the connecting wire is applied automatically when the new wire is committed. The wire check takes priority over the component double-click check so that wires near or inside a component's bounding box remain reachable.
- A wire that terminates on a **connectable** target — a pin, an existing wire vertex, or a wire segment — finalizes and returns to **Select** mode.
- In **Select** mode, a **double-click on blank canvas** (no wire or component hit) also enters **Wire** mode, starting a free wire from the snapped 0.25 GU grid point.
- A **double-click** on an empty grid node finalizes the wire (its end becomes an open `ocirc` endpoint) but **stays in Wire** mode so the user can immediately draw another wire.

#### Junctions and segment splitting

- Where wires (and pins) meet, a solid **connection dot** is drawn and emitted as `\node[circ]` (see §7.6). The dot rule is based on the **degree** of a coordinate — the number of wire segment-ends meeting there (an endpoint counts 1, a pass-through/interior vertex counts 2) plus 1 for a coincident pin. **Degree ≥ 3 → dot.** A straight pass-through, a lone corner, two wires meeting end-to-end, and a pin with a single wire all have degree 2 and get no dot. (In this model coincident wire points are electrically joined; there is no non-connecting "hop" crossing.) A wire with `no_junction_dots=True` (§4.3) is **excluded from the degree count entirely**, so annotation leads do not create dots; other wires/pins at the coordinate are still counted normally.
- Wire endpoints that do not coincide with any component pin are drawn as **open circles** and emitted as `\node[ocirc]` (see §7.6). Only the first and last point of each wire are candidates; interior vertices are never open endpoints. A wire with `no_termination_dots=True` (§4.3) is **excluded from `open_endpoints()`**, so its free ends get no terminal — while it still counts as a connection for other wires ending on it. An end carrying a **custom marker** (`start_marker`/`end_marker`, §4.3) is likewise excluded at that specific end, so the marker (e.g. an arrowhead) replaces the automatic open-circle terminal there.
- When a wire connects to the **middle of another wire's segment** or to an existing wire's **intermediate (corner) vertex** — whether by drawing a new wire onto it, by dragging an existing wire vertex onto it, or by **placing or moving a component** such that one of its pins lands mid-segment — the target wire is **split into two independent wire objects** at the connection point so each half is separately selectable and deletable, and a junction dot is drawn. Connecting at an existing *endpoint* (first or last vertex) does not split. The split is bundled with the triggering command (`WireCommand`, `MoveWireVertexCommand`, `PlaceCommand`, or `MoveCommand`) inside a `MacroCommand` so it is one undoable action. Component operations that trigger splits: initial placement, drag-drop, arrow-key nudge, and paste.
- When a wire is **deleted** and the deletion dissolves a T-junction (a free endpoint now has exactly two remaining wire neighbors and is not a component pin), those two stubs are automatically **merged** into a single wire. The merge is bundled with the `DeleteCommand` inside a `MacroCommand` so delete + merge is one undoable action. Undoing restores the deleted wire and re-splits the merged wire back into its two halves.

Connectivity is **purely geometric and never stored**: two coordinates are
electrically joined precisely when they are equal. Junction dots, open-circle
ends, and which wires follow a moved component are all *derived* from point
coincidence whenever the schematic changes — so dragging an endpoint onto a pin
connects it and dragging it away disconnects it, with no bookkeeping.

#### Editing existing wires

- In **Select** mode a wire's draggable vertices (intermediate corners and free, non-pin endpoints) show grab handles. Dragging one moves that vertex, re-routing each adjacent segment through `route()` to stay Manhattan and simplifying afterward; recorded as a `MoveWireVertexCommand`. The **live drag preview** mirrors this exactly — segments are re-routed and `simplify_points` is applied on every mouse-move, so the ghost never shows redundant collinear vertices and no diagonal segments appear before the mouse is released. A dropped vertex snaps to a connectable target (pin / other wire) just like a drawn endpoint. Endpoints sitting on a component pin are locked (they are owned by component wire-following) and have no handle. If a drag collapses the wire to a single point (its simplified path drops below two vertices), the wire is **removed** rather than left as a degenerate single-point wire — and `undo` restores it. (This mirrors `MoveCommand`'s handling of a collapsed wire-following.)
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
| `MacroCommand` | Composite of the above (e.g. a split + add, or a multi-component move) |

Notes:

- `MoveCommand` also drags connected wire endpoints with the component and captures each affected wire's original points so undo restores them exactly (see §6.3).
- `SplitWireCommand` replaces a wire with two independent halves when another wire connects mid-segment; it is normally bundled with the triggering `WireCommand` / `MoveWireVertexCommand` in a `MacroCommand` so the connection is one undoable action (see §6.4).
- `MergeWireCommand` merges two wire stubs that share a free endpoint into one wire; it is bundled after a `DeleteCommand` inside a `MacroCommand` when the deletion dissolves a T-junction (see §6.4).
- `DeleteCommand` accepts both component ids and wire ids, removing components, the wires connected to their pins, and any directly-selected wires.
- `GroupRotateCommand` is used by `rotate_selected_cw()` for all rotations (single or multi-component). It rotates positions around the bounding-box centroid of the selection (snapped to the 0.25 GU grid), increments each component's `rotation` by 90°, clears `label_offset` (reset to auto), rotates all selected and internal wire vertices, and reshapes boundary wires (one endpoint on a selected pin, one not) using the same elbow logic as `MoveCommand`. If a boundary wire's reshape collapses it to a single point (its moving end folds onto its fixed end), the wire is **removed** rather than left as a degenerate single-point wire — and `undo` re-adds it (same guard `MoveCommand` applies to wire-following).

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
  `SchematicScene._remove_item(...)`. Callers pass `dict.pop(key)` directly, so
  the tracking-dict entry and the scene item are dropped together, and the item
  is always detached from the scene (`removeItem`) *before* its last reference
  dies. `removeItem` synchronously clears the scene's other internal pointers
  (selection, focus, mouse grabber, hover).
- **No lingering references.** Nothing retains a reference to an item after it
  has been removed; conversely, every live item is owned by exactly one tracking
  structure (`_comp_items`, `_wire_items`, `_junction_items`,
  `_open_circle_items`, `_ghost`, or `_wire_preview`).
- **`NoIndex` item method.** The scene uses `QGraphicsScene.NoIndex` rather than
  the default BSP tree. The BSP index *defers* item removal, which would let a
  freed item linger in the index until the next paint; `NoIndex` keeps the item
  list consistent synchronously with `removeItem`. (This class of frequently
  mutated scene gains nothing from the BSP index anyway.)

These invariants are guarded by `test_no_index_method` (deterministic) and by a
randomized paint-after-every-mutation fuzz test (probabilistic — a use-after-free
only faults nondeterministically, so it cannot be checked deterministically
without a native memory sanitizer). See §13.

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

#### Two-Terminal Components (R, C, L, D, sources)

Each two-terminal component with origin at `(x0, y0)` and terminal pin at `(x1, y1)` (after rotation) maps to:

```latex
(x0, y0) to[KIND, LABELS] (x1, y1)
```

Where `OPTIONS` is the component's options string, e.g.:

```latex
(0,0) to[R, l=$R_1$, v=$V_R$] (2,0)
```

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

**Diode scale.** When the schematic contains any diode-family component
(`D`/`zD`/`sD`/`tD`/`zzD`/`leD`), the generator emits a picture-scoped
`\ctikzset{diodes/scale=0.8}` as the first line inside `\begin{circuitikz}` (the
factor is `DIODE_SYMBOL_SCALE`). This shrinks CircuiTikZ's oversized default
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

Because CircuiTikZ's internal pin geometry does not align with the 0.5-GU
canvas grid, two strategies are used to make wire connections exact:

**op amp** — placed by center (`comp.position`). Short lead wires are drawn
from each named CTikZ anchor to the registry pin coordinate:

```latex
(node_id.+) -- (pin_plus_coord)
(node_id.-) -- (pin_minus_coord)
(node_id.out) -- (pin_out_coord)
```

This bridges the gap between CircuiTikZ's internal ±1.194 GU geometry and
the canvas's ±1.5 GU lead-stub positions.

**nigfete** — placed with `anchor=gate` at the gate pin coordinate. An
`xscale=1.0167` is applied to stretch the symbol horizontally so that
drain/source x aligns with the 1.0 GU grid position (CTikZ internal x is
0.984 GU from gate; 0.984 × 1.0167 = 1.0 GU). No lead wires are drawn for
drain/source because their CTikZ anchors are not rectilinearly aligned with
the registry pin positions.

#### Named Anchor References

Wire endpoints and two-terminal component terminals that coincide with a
multi-terminal pin are rendered as named anchor references instead of bare
coordinates:

```latex
(78.5,80) to[R] (node_abc123.gate)
(node_abc123.out) -- (node_def456.+)
```

This makes connections explicit and produces cleaner output.

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

### 7.7 Drawing Annotation Commands

After junction and open-endpoint nodes, the generator emits standalone commands for drawing annotations (`text_node`, `rect`). These produce nothing inside the `\draw` path block — `_component_lines()` returns `[]` for drawing kinds.

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
% when options is "" (solid):
\draw (x1,y1) rectangle (x2,y2);
```

`(x2,y2) = (x1+dx, y1+dy)` where `(dx,dy)` is `span_override` when set, or `default_span = (2,2)` otherwise. `STYLE` is the raw `Component.options` string passed verbatim. The `y_flip` transform applies to all four coordinates.

### 8.1 Full Schematic Preview

A rendered PDF preview of the complete schematic is produced by:

1. Wrapping the generated CircuiTikZ in a minimal `.tex` document.
2. Running `pdflatex` in a temporary directory via `subprocess`.
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

Compilation runs in a `QThread` (`PreviewWorker`). The main thread is never blocked. While compiling, a spinner is shown in the preview panel. If `pdflatex` returns a non-zero exit code, the error log is shown in the preview panel in place of the image.

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
\documentclass[border=4pt]{standalone}
\usepackage[american]{circuitikz}
\usetikzlibrary{arrows.meta}
\ctikzset{voltage=american, current=american, resistor=american}
\begin{document}
% CIRCUITIKZ_SOURCE
\end{document}
```

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
%   \ctikzset{voltage=american, current=american, resistor=american}
\begin{circuitikz}
  ...
\end{circuitikz}
```

The snippet is a bare `circuitikz` environment preceded by a comment listing the
preamble packages the host document must load — it deliberately omits
`\documentclass` and `\begin{document}` so it can be `\input` into an existing
document rather than compiled on its own. The source is generated with
`generate(schematic, y_flip=True)` (Y-up convention, like preview compilation in
§8.4) so the included figure renders in the same orientation as the canvas.

### 8.6 Export to PDF / EPS

**File → Export to PDF…** and **File → Export to EPS…** write a compiled image
of the schematic, suitable for `\includegraphics` in a LaTeX document (or any
other consumer). Both reuse the §8.1 compile pipeline:

1. `generate(schematic, y_flip=True)` → `build_tex()` → `compile_tex()` yields
   PDF bytes (run synchronously on the UI thread; the status bar shows
   "Compiling…").
2. **PDF export** writes those bytes directly.
3. **EPS export** converts them with `pdf_to_eps()`, which runs
   `pdftocairo -eps`. The `-eps` flag emits Encapsulated PostScript with a tight
   bounding box derived from the PDF crop box.

Unlike the §8.5 `.tex` snippet, these formats require `pdflatex` to be available
at export time (and `pdftocairo` for EPS), but the result is a self-contained
image that does not need the host document to load `circuitikz`. Compile or
conversion failures are reported in a dialog (the `pdflatex` log is included for
compile errors) and leave no file behind. For a `pdflatex`/`lualatex` workflow,
PDF is the natural choice; EPS is for `latex`+`dvips` PostScript workflows.

### 8.4 Dependencies

- `pdflatex` must be on the system `PATH`. Checked at startup (`check_dependencies`); a warning dialog is shown if not found. It is the only tool required for normal use.
- The PDF preview is rendered by the `QtPdf` module that ships with PySide6 — no external process and no Poppler. There is no `pdf2image`/Poppler dependency for the preview.
- `pdftocairo` (Poppler) is required **only** for EPS export (§8.6). It is checked on demand in `pdf_to_eps` (not at startup), so users who never export EPS are not warned about a missing Poppler.
- The `circuitikz` LaTeX package must be installed in the TeX distribution.

---

## 9. File Format

### 9.1 Save Format

Schematics are saved as UTF-8 JSON files (no byte-order mark) with the extension `.hv`.

Saving is **atomic**: the JSON is written to a sibling temporary file (`<name>.tmp`) and then renamed over the target via `os.replace`, so an interrupted or failed write never corrupts an existing file.

### 9.2 Schema

```json
{
  "version": "0.1",
  "name": "My Schematic",
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

### 9.3 Validation on Load

On file load, the application:

1. Validates JSON schema structure.
2. Verifies `version` field is a known spec version.
3. Validates all invariants listed in Section 4.5.
4. On validation failure, shows an error dialog with a description of the first failing check and does not load the file.

### 9.4 Versioning

The `version` field in the JSON corresponds to the spec version. Future spec versions must document migration rules for loading files from older versions.

### 9.5 Bundled Examples

Example schematics live under `examples/` as `.hv` files and are bundled into
the app (the PyInstaller spec adds `examples/*.hv`; the co-located `.pdf`/`.eps`
exports are regenerable and not bundled or tracked). **File → Open Example ▸**
lists each one (resolved via `resource_path("examples")`, so it works from a
source checkout and when frozen). Selecting an example loads it as a *template*:
it is opened and the view is fit to it (§5), but `_current_path` is left unset so
**Save** prompts for a new location rather than overwriting the read-only bundled
file. If no examples are present the submenu shows a disabled placeholder.

---

## 10. UI Layout

### 10.1 Main Window

```
┌─────────────────────────────────────────────────────────────┐
│  Menu Bar: File | Edit | View | Help                        │
├─────────────────────────────────────────────────────────────┤
│  Toolbar: New | Open | Save | | Undo | Redo | | Compile     │
├────┬─────────┬───────────────────────────┬──────────────────┤
│Tool│ Palette │                           │  Properties      │
│ ↖  │         │        Canvas             │  Panel           │
│ ⌁  │Bipoles  │   (QGraphicsView)         │                  │
│ ✋  │Amps     │                           │  (context-       │
│    │Sources  │                           │   sensitive)     │
│    │Tripoles │                           │                  │
│    │         │                           │                  │
├────┴─────────┴──────────────┬────────────┴──────────────────┤
│  Source Panel (CircuiTikZ)  │  LaTeX Preview (draggable)    │
├─────────────────────────────┴───────────────────────────────┤
│  Status bar: cursor coords | zoom level | compile status    │
└─────────────────────────────────────────────────────────────┘
```

**Window background.** The window background is white: `MainWindow` sets its
palette `Window` color to `#ffffff`, which child widgets inherit (central area,
panels, splitter gaps, status bar). The two **toolbars keep their gray** (`#ebebeb`,
set via their own stylesheets) and input controls are unaffected (they paint
with the `Base`/`Button` palette roles, not `Window`).

### 10.2 Component Palette

- Left panel, fixed width ~180px, white background.
- Components grouped by `category` in collapsible sections.
- Each entry shows a thumbnail rendered from the component's `ComponentItem` at 32×32px, alongside the `display_name`.
- Clicking an entry enters **Place** mode.
- A search field at the top filters by `display_name`.

### 10.3 Properties Panel

- Right panel, fixed width ~250px, header showing the `ComponentDef.display_name` and `kind`, followed by a vertical scroll area of **capability sections**.
- Empty when nothing is selected; shows a multi-select count (components + wires) when more than one item is selected.
- Selecting a single **wire** shows the wire inspector instead of the component sections (header "Wire").

**Architecture — capability sections.** The panel is composed of `InspectorSection` widgets rather than one monolithic panel per component type. Each section edits one capability and declares which components it `applies_to` (by `isinstance` against the model hierarchy and the `FontedComponent` / `StyledComponent` mixins). On selection the panel walks an ordered section list, `bind`-ing (showing) the sections that apply and `unbind`-ing (hiding) the rest; the first visible section's leading separator is suppressed. Adding a component type that combines existing capabilities needs no new panel — the sections compose. Section → applicability:

| Section | Applies to | Controls |
|---------|-----------|----------|
| `OptionsSection` | plain circuit (not `DrawingComponent`) | CircuiTikZ options field + slot hint |
| `TextContentSection` | `text_node` | text-content field (stored in `options`) |
| `BipoleLabelSection` | `bipole` | `t=` label field + other-options field + hint |
| `DiodeSection` | `DiodeComponent` | **Filled** checkbox |
| `MosfetSection` | `MosfetComponent` | **Body diode** checkbox |
| `FontSection` | `FontedComponent` (text_node, bipole) | size / bold / italic / family |
| `FillBorderSection` | `StyledComponent` (rect, bipole) | line style, border width, fill |
| `TransformSection` | all but `rect` (rect rotation is a codegen no-op) | rotation buttons; mirror checkbox (circuit + bipole only) |
| `LayerSection` | `DrawingComponent` (text_node, rect, bipole) | move front/back buttons + z-order spinbox |

`WireStyleSection` is a section for **wires** (not Components, so it is outside the component `applies_to` loop). When a single wire is selected, `PropertiesPanel.show_wire(wire_id)` unbinds the component sections and binds it via `bind_wire`; it offers **Line style** (solid/dashed/dotted/dash-dot), **Line width (pt)**, a **No junction dots** checkbox, a **No termination dots** checkbox, **Start endpoint** / **End endpoint** marker combos (None/Arrow/Stealth/Open arrow/Bar), and **Start** / **End** / **Middle** label text fields (text or `$math$`), writing through `set_wire_line_style` / `set_wire_line_width` / `set_wire_no_junction_dots` / `set_wire_no_termination_dots` / `set_wire_start_marker` / `set_wire_end_marker` / `set_wire_start_label` / `set_wire_end_label` / `set_wire_mid_label` (the line-style combo/width spinbox debounce 300 ms; the checkboxes and marker combos commit immediately; the label fields commit on `editingFinished` — Enter or focus-out — *not* per keystroke, so a re-bind can't jerk the cursor mid-edit, and `bind_wire` additionally skips a label field that currently has focus). The endpoint markers are independent of the automatic junction/termination dots and exist mainly to draw block diagrams (the arrowhead); the endpoint labels caption signal lines (an arrow terminating into text); the **Middle** field adds an over-the-wire mid-label (§4.3) that is then dragged/edited on the canvas. Selection routing (`MainWindow`) queries both `selected_component_ids()` and `selected_wire_ids()` to choose component / wire / multi-select / empty.

All section edits funnel through `SchematicScene` methods that push undoable commands. Text/options fields and the fill/border controls debounce commits 300 ms; checkboxes, rotation, mirror, and z-order commit immediately.

The bottom strip (height 260px) holds the source panel and preview panel side by
side in a horizontal `QSplitter`. Because the generated CircuiTikZ lines are
short, the preview gets the larger initial share of the width (initial sizes
≈ 440 / 840); the user can drag the handle to rebalance, and neither pane is
collapsible.

### 10.4 Source Panel

- Left pane of the bottom strip.
- Read-only `QPlainTextEdit` showing the current generated CircuiTikZ source.
- Updates live (debounced 300ms) as the schematic changes.
- Syntax is not highlighted in v1.

### 10.5 Preview Panel

- Right pane of the bottom strip, separated by a 1px border; resizable via the
  splitter (minimum width ~240px), and re-renders to fit on resize.
- Shows the rendered PDF preview image, scaled to fill the available area.
- Shows error text on compilation failure.
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
| Duplicate | `Ctrl+D` |
| Delete | `Delete` / `Backspace` |
| Select All | `Ctrl+A` |
| Preferences | `Ctrl+,` |
| Select mode | `S` |
| Wire mode | `W` |
| Pan mode (persistent) | `P` |
| Cycle wire endpoint marker / line style (while hovering) | `Tab` / `Shift+Tab` |
| Cancel / Select mode | `Escape` |
| Pan (transient) | `Space` + drag |
| Compile preview | `Ctrl+Return` |
| Fit to schematic | `Ctrl+0` |
| Zoom in / out | `Ctrl++` / `Ctrl+-` |

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
reflected immediately.

Current settings:

| Setting | Key | Default | Effect |
|---------|-----|---------|--------|
| Auto-export PDF on save | `export/auto_pdf_on_save` | off | After a successful save of `<name>.hv`, also write `<name>.pdf` to the same directory. |
| Auto-export EPS on save | `export/auto_eps_on_save` | off | After a successful save, also write `<name>.eps` to the same directory. |
| Mark unconnected component pins | `display/mark_unconnected_pins` | off | Draw an open circle at every component pin with no wire attached — on the **canvas**, and as `\node[ocirc]` in the preview, source panel, and exports (§7.6). |

When either is enabled, `_do_save()` calls `_auto_export()`, which compiles the
schematic **once** (reusing the §8.6 pipeline) and writes the requested sibling
file(s) — the single PDF is converted to EPS via `pdf_to_eps()` when both are
requested. This keeps an `\includegraphics{<name>.pdf}` (or `.eps`) in a LaTeX
document in sync with the schematic without a manual export step.

Auto-export never blocks or aborts the save: it runs only *after* the `.hv`
is written, and any failure (invalid schematic, missing `pdflatex`/`pdftocairo`,
or a `pdflatex` error) is reported in the status bar only — not as a modal
dialog, which would be intrusive on every save. The compile is synchronous, so
saving adds the compile latency when auto-export is enabled.

---

## 11. Project Structure

```
heaviside/
├── main.py                        # Entry point; constructs QApplication and MainWindow
├── heaviside.spec                 # PyInstaller build spec (see §11.1)
├── scripts/
│   ├── build_app.sh               # Clean PyInstaller build helper
│   └── make_icns.sh               # Regenerate assets/icon.icns from icon.png
├── examples/                      # Bundled example .hv schematics (File → Open Example, §9.5)
├── app/
│   ├── resources.py               # resource_path(): frozen-safe bundled-file resolution
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
│   │   ├── svgsym.py              # SVG-manifest → QPainterPath symbol geometry loader
│   │   ├── style.py               # GRID_PX, LINE_W, PIN_R, LEAD_LEN, colors, and constants
│   │   └── commands.py            # Undo/redo command classes
│   ├── components/
│   │   ├── model.py               # ComponentDef, PinDef dataclasses
│   │   └── registry.py            # REGISTRY dict and all ComponentDef entries
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
│   │   └── latex.py               # build_tex / build_snippet / pdf_to_eps, helpers
│   └── ui/
│       ├── mainwindow.py          # MainWindow(QMainWindow)
│       ├── palette.py             # ComponentPalette(QWidget)
│       ├── properties.py          # PropertiesPanel(QWidget)
│       ├── preferences.py         # Preferences (QSettings), PreferencesDialog
│       └── sourcepanel.py         # SourcePanel(QWidget)
└── tests/
    ├── test_model.py              # model + validation + geometry helpers (simplify,
    │                              #   junctions, splits)
    ├── test_codegen.py            # code generation incl. junction \node[circ]
    ├── test_io.py
    ├── test_registry.py
    ├── test_commands.py           # undo/redo for all command classes
    ├── test_geometry.py           # pure canvas geometry helpers (no Qt scene)
    ├── test_wiregeometry.py       # WireGeometry snapping / hit-testing (no Qt scene)
    ├── test_scene.py              # SchematicScene/SchematicView interaction (offscreen Qt)
    ├── test_preferences.py        # Preferences (QSettings) + dialog
    ├── test_preview_render.py     # QtPdf preview rendering (offscreen Qt + pdflatex)
    ├── test_mathrender.py         # on-canvas math vector rendering + slot parsing (offscreen Qt; render gated on latex/dvisvgm)
    └── test_svgsym.py             # symbol geometry incl. glyph (+/-) reconstruction
```

Note: the `assets/components/` directory has been removed. All component rendering is handled programmatically via `ComponentItem.paint()`.

`scratch_canvas.py` was a temporary development harness used during Phases 5–8 and has been removed now that the full UI shell (Phase 9) is in place.

### 11.1 Packaging (PyInstaller)

The app ships as a self-contained bundle built with PyInstaller from
[`heaviside.spec`](heaviside.spec) (`./scripts/build_app.sh`). Output is
`dist/Heaviside.app` on macOS (a proper `.app` bundle with the `.icns` icon and
a `.hv` document-type association) and `dist/Heaviside/` elsewhere. `build/` and
`dist/` are git-ignored.

**App icon.** The bundle icon is `assets/icon.icns`, regenerated from
`assets/icon.png` by `./scripts/make_icns.sh`. On macOS `build_app.sh` runs this
automatically when the `.icns` is missing or older than the PNG, so updating the
icon is just: replace `icon.png`, rebuild. The source PNG need not be square —
the script pads it onto a transparent square canvas before rendering the
iconset, so the icon is never distorted. (After replacing the icon you may need
to clear the macOS icon cache — e.g. relaunch the Dock — to see the change on an
already-seen bundle.)

**Runtime resources.** Two resources are read at runtime and must be bundled:
`assets/icon.png`, and `tools/circuitikz_svgs/manifest.json`. The manifest is
**self-contained** — it bakes in every symbol's geometry, including the resolved
`+`/`−` glyph marks (as `glyphs` entries with a baked affine matrix; see §5.3),
so `svgsym.py` reads only the manifest and never touches the `.svg` files. The
intermediate `.svg` files are build artifacts and are **not** bundled. Because a
frozen app cannot resolve `__file__`-relative paths the way a source checkout
does, all call sites (`main.py`, `app/ui/mainwindow.py`, `app/canvas/style.py`)
go through `resource_path()` in `app/resources.py`, which roots paths at
`sys._MEIPASS` when frozen and at the project root otherwise.

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
| `pdftocairo` *(optional)* | PDF→EPS conversion for **EPS export only** | Poppler (`poppler-utils` on Debian/Ubuntu) |

The PDF preview is rendered by the `QtPdf` module bundled with PySide6, so no Poppler is needed for normal use. The application checks for `pdflatex` on the system `PATH` at startup and shows a warning dialog if it is missing; `pdftocairo` is checked only when EPS export is invoked.

---

## 13. Test Specification and Acceptance Criteria

### 13.1 Philosophy

Tests are organized into three tiers: **unit tests** covering pure logic with no UI or filesystem dependencies, **integration tests** covering interactions between layers, and **acceptance criteria** defining the conditions under which v1 is considered complete. The UI layer (canvas painting, mouse interaction) is not unit-tested; it is covered by the acceptance criteria via manual verification.

### 13.2 Unit Tests

All unit tests live in `tests/` and are run with `pytest`. They must pass with no network access, no display server, and no LaTeX installation.

#### Model and Validation (`test_model.py`)

| Test | Description |
|------|-------------|
| `test_component_mixin_composition` | `BipoleComponent` is an instance of both `FontedComponent` and `StyledComponent` (and `DrawingComponent`); `rect` is `StyledComponent`-only, `text_node` is `FontedComponent`-only; bipole's `font_size` override is 7.0. Guards mixin base-ordering. |
| `test_component_valid` | A `Component` with a known `kind`, valid rotation, and valid position passes `validate()` with no errors. |
| `test_component_invalid_kind` | A `Component` with a `kind` not in `REGISTRY` produces a validation error. |
| `test_component_invalid_rotation` | A `Component` with rotation `45` produces a validation error. |
| `test_wire_valid` | A `Wire` with a valid Manhattan path on 0.25 GU boundaries passes validation. |
| `test_wire_off_grid` | A `Wire` with a vertex off the 0.25 GU grid (e.g. `(0.3, 0.0)`) produces a validation error. |
| `test_wire_on_quarter_grid_is_valid` | Vertices on the 0.25 GU grid (e.g. a 0.25-nudged pin at y=0.25) validate (§3.1). |
| `test_wire_diagonal` | A `Wire` with a diagonal segment produces a validation error. |
| `test_schematic_duplicate_ids` | A `Schematic` with two components sharing the same `id` produces a validation error. |
| `test_schematic_empty_valid` | An empty `Schematic` (no components, no wires) passes validation. |

#### Code Generation (`test_codegen.py`)

| Test | Description |
|------|-------------|
| `test_resistor_horizontal` | A single resistor at (0,0), rotation 0, no labels → produces `(0,0) to[R] (2,0)`. |
| `test_resistor_with_options` | A resistor with `options="l=$R_1$, v=$V$"` → produces `to[R, l=$R_1$, v=$V$]`. |
| `test_label_value_with_comma_is_brace_protected` / `test_comma_free_label_value_left_unwrapped` | A label value containing a comma is brace-wrapped (`v=$\phi(0,0^+)$` → `v={$\phi(0,0^+)$}`) so pgfkeys does not mis-split the option list; comma-free values are emitted verbatim (regression). |
| `test_protect_label_commas_unit` | `protect_label_commas()` wraps only comma-bearing values, leaves already-braced groups and comma-free flags untouched, and is a no-op on empty input. |
| `test_resistor_rotated_90` | A resistor at (0,0), rotation 90 → origin and terminal pins are correctly rotated; output uses correct coordinates. |
| `test_capacitor_horizontal` | A capacitor at (2,0), rotation 0 → produces `(2,0) to[C] (4,0)`. |
| `test_inductor_horizontal` | An inductor at (0,0), rotation 0 → produces `(0,0) to[L] (2,0)`. |
| `test_diode_horizontal` | A diode at (0,0), rotation 0 → produces `(0,0) to[D] (2,0)`. |
| `test_diode_filled` | A diode with `filled=True` → produces `(0,0) to[D*] (2,0)`. |
| `test_diode_emits_picture_scoped_scale` / `test_no_diode_scale_without_diodes` | A schematic with a diode emits `\ctikzset{diodes/scale=0.8}` as the first line inside `\begin{circuitikz}`; schematics without any diode omit it. |
| `test_zener_diode` | A `zD` component → produces `(0,0) to[zD] (2,0)`. |
| `test_zener_diode_filled` | A `zD` with `filled=True` → produces `(0,0) to[zD*] (2,0)`. |
| `test_led` | A `leD` component → produces `(0,0) to[leD] (2,0)`. |
| `test_voltage_source` | A voltage source at (0,0), rotation 0 → produces `(0,0) to[V] (0,2)`. |
| `test_opamp_node` | An op-amp produces `node[op amp]` syntax with correct anchor coordinates. |
| `test_nmos_node` | An NMOS (`nigfete`) produces `node[nigfete, xscale=1.0167, anchor=gate]` syntax (with its §7.2 geometry correction). |
| `test_nmos_depletion_node` | `nigfetd` uses the same `xscale=1.0167, anchor=gate` geometry correction as `nigfete`. |
| `test_pmos_node` | `pigfete` is placed with `xscale=1.0167, anchor=gate`. |
| `test_pmos_depletion_node` | `pigfetd` uses the same geometry correction as `pigfete`. |
| `test_nmos_bodydiode` | `nigfete` with `body_diode=True` emits `node[nigfete, bodydiode, xscale=1.0167, anchor=gate]`. |
| `test_nmos_no_bodydiode` | `nigfete` with `body_diode=False` omits the `bodydiode` option entirely. |
| `test_pmos_bodydiode` | `pigfete` with `body_diode=True` emits `node[pigfete, bodydiode, xscale=1.0167, anchor=gate]`. |
| `test_pmos_pin_offsets` | PMOS source is at Qt (1.0,-0.5) and drain at (1.0,+1.0) relative to gate — the y-mirror of NMOS. |
| `test_wire_straight` | A two-point wire `[(0,0),(2,0)]` → produces `(0,0) -- (2,0)`. |
| `test_wire_manhattan` | A three-point wire `[(0,0),(2,0),(2,2)]` → produces `(0,0) -- (2,0) -- (2,2)`. |
| `test_coordinate_formatting_integer` | Coordinate `2.0` is output as `2`, not `2.0` or `2.00`. |
| `test_coordinate_formatting_half` | Coordinate `1.5` is output as `1.5`, not `1.50`. |
| `test_empty_schematic` | An empty schematic produces a valid (empty) `circuitikz` environment. |
| `test_generate_is_pure` | Calling `generate()` twice on the same `Schematic` produces identical output. |
| `test_junction_emits_circ_node` / `test_no_junction_no_circ_node` / `test_junction_node_count_matches` | Junction coordinates emit `\node[circ]`; non-junctions do not; count matches the number of junction points. |
| `test_open_endpoint_emits_ocirc_node` | A wire with both ends free emits two `\node[ocirc]` nodes at those coordinates. |
| `test_pin_connected_endpoint_no_ocirc` | A wire endpoint coinciding with a component pin does not emit `\node[ocirc]`. |
| `test_voltage_annotation_endpoint_emits_ocirc` | A wire ending on a voltage annotation (`open`) pin still emits `\node[ocirc]` — the annotation is an open circuit, not a connection. |
| `test_degenerate_wire_skipped_but_endpoint_open` | A degenerate single-point wire emits no `\draw` coordinate and does not suppress the `\node[ocirc]` at a real wire endpoint sharing its coordinate. |
| `test_no_open_endpoints_no_ocirc` | A wire whose both ends land on component pins emits no `\node[ocirc]`. |
| `test_mark_unconnected_pins_off_by_default` / `test_mark_unconnected_pins_marks_dangling_pins` | With `mark_unconnected_pins=False` (default) a lone resistor emits no `ocirc`; with it `True`, both free pins get a `\node[ocirc]` (§7.6). |
| `test_mark_unconnected_pins_skips_wired_pin` / `test_mark_unconnected_pins_respects_y_flip` | A pin with a wire on it is never marked even when the option is on; marked pins honor the `y_flip` convention. |
| `test_mark_unconnected_pins_voltage_annotation_not_a_connection` | A voltage annotation (`open`) abutting a pin does not suppress that pin's `ocirc` (it is an open circuit, not a connection). |
| `test_text_node_basic` | A `text_node` at (2,3) with options "Hello" emits `\node at (2,3) {Hello};` outside the `\draw` block. |
| `test_text_node_with_font_size` | A `text_node` with `span_override=(14,0)` emits `\node[font=\fontsize{14}…\selectfont] at (…) {…};`. |
| `test_text_node_y_flip` | A `text_node` at (2,3) with `y_flip=True` emits `\node at (2,-3) {…};`. |
| `test_text_node_rotation` | A `text_node` with `rotation=90` emits `\node[rotate=270] at (…) {…};` (negated: CW-visual maps to TikZ CCW convention). |
| `test_text_node_rotation_with_font` | A `text_node` with `rotation=270` and `font_bold=True` emits `rotate=90` and `\bfseries` in the option list. |
| `test_rect_solid` | A `rect` with no options and `span_override=(5,1)` at (-0.5,-0.5) emits `\draw (-0.5,-0.5) rectangle (4.5,0.5);`. |
| `test_rect_dashed` | A `rect` with `line_style="dashed"` emits `\draw[dashed] … rectangle …;`. |
| `test_rect_uses_default_span_when_none` | A `rect` with `span_override=None` falls back to `default_span=(2,2)`. |
| `test_rect_line_style_and_fill_combined` | A `rect` with `line_style="dotted"` + `fill_color="cyan!15"` emits `\draw[dotted, fill=cyan!15] … rectangle …;`. |
| `test_drawing_kinds_not_in_draw_block` | `text_node` and `rect` produce nothing inside the main `\draw … ;` block. |
| `test_plain_wire_in_shared_draw` | A default-styled wire is emitted inside the shared `\draw` path (no per-wire `\draw[…]`). |
| `test_styled_wire_separate_draw` | A wire with `line_style="dashed"`, `line_width=0.8` emits its own `\draw[dashed, line width=0.8pt] (…) -- (…);`. |
| `test_styled_wire_line_width_only` | A non-default `line_width` alone triggers a styled `\draw[line width=…pt]` statement. |
| `test_no_junction_dots_wire_suppresses_circ` | A wire flagged `no_junction_dots` emits no `\node[circ]` at its T-junction (and the same topology unflagged does). |
| `test_no_termination_dots_wire_suppresses_ocirc` | A wire flagged `no_termination_dots` emits no `\node[ocirc]` at its free ends (and the same wire unflagged does). |
| `test_wire_end_marker_emits_arrow` / `test_wire_start_marker_emits_reverse_arrow` / `test_wire_both_markers_emit_double_arrow` | An `end_marker`/`start_marker`/both `="arrow"` wire emits `\draw[-{Latex}]` / `\draw[{Latex}-]` / `\draw[{Latex}-{Latex}]`. |
| `test_wire_marker_styles_map_to_arrows_meta_tips` | `stealth`/`open`/`bar` markers emit `-{Stealth}` / `-{Latex[open]}` / `-{Bar}`. |
| `test_wire_mixed_markers_emit_distinct_tips` | Different start/end kinds compose independently, e.g. `{Bar}-{Stealth}`. |
| `test_wire_marker_combines_with_style` | A marked + styled wire emits the arrow spec first: `\draw[-{Latex}, dashed, line width=0.8pt] …`. |
| `test_wire_marker_suppresses_ocirc_at_that_end` | A marked end emits no `\node[ocirc]`; the unmarked end still does. |
| `test_build_tex_loads_arrows_meta` / `test_build_snippet_lists_required_preamble` | The standalone template loads `\usetikzlibrary{arrows.meta}`; the snippet preamble documents it. |
| `test_wire_end_label_horizontal_anchor_west` / `test_wire_start_label_horizontal_anchor_east` | A horizontal-wire end/start label emits `\node[anchor=west/east]` just beyond the tip. |
| `test_wire_label_vertical_anchor_under_yflip` | Vertical-wire labels anchor by emitted-space (Y-flip-aware) direction: `anchor=south` above the top end, `anchor=north` below the bottom. |
| `test_wire_label_empty_emits_no_node` / `test_wire_label_degenerate_wire_skipped` | No label node for an unlabelled wire or a degenerate single-point wire. |
| `test_wire_label_coexists_with_arrow_marker` | An arrow marker and an end label render together (arrow into text). |
| `test_wire_mid_label_node_with_white_fill` / `test_wire_mid_label_respects_position` / `test_wire_mid_label_empty_emits_no_node` | A `mid_label` emits `\node[fill=white, inner sep=1pt]` at `wire_point_at_fraction(points, mid_label_pos)`; empty emits nothing. |
| `test_point_at_fraction_*` / `test_fraction_at_point_projects_onto_polyline` / `test_fraction_round_trips_with_point` | `wire_point_at_fraction` / `wire_fraction_at_point` map fractional arc-length ↔ point (straight + L-wire, clamp, degenerate, projection, round-trip). |
| `test_bipole_fill_color` | A `bipole` with `fill_color="yellow!20"` → emits `fill=yellow!20` in the `\node[…]` options. |
| `test_bipole_border_width` | A `bipole` with `border_width=1.5` → emits `line width=1.5pt` in the `\node[…]` options. |
| `test_bipole_default_border_width_omitted` | A `bipole` at default `border_width=0.4` does not emit any `line width` option. |
| `test_bipole_line_style` | A `bipole` with `line_style="dashed"` → emits `dashed` in the `\node[…]` options. |
| `test_build_snippet_wraps_environment` | `build_snippet()` keeps the `circuitikz` environment intact and includes the generated source verbatim (§8.5). |
| `test_build_snippet_lists_required_preamble` | The snippet documents the required `\usepackage[american]{circuitikz}` preamble and the `\input` usage. |
| `test_build_snippet_has_no_document_wrapper` | The snippet is includable, not standalone: it emits no `\documentclass` or `\begin{document}`. |
| `test_pdf_to_eps_missing_tool` | `pdf_to_eps()` raises `CompileError` mentioning `pdftocairo` when the tool is absent (§8.6). |
| `test_pdf_to_eps_roundtrip` | A compiled schematic PDF converts to a valid EPS (`%!PS-Adobe`, `EPSF`, `%%BoundingBox`). Skipped without `pdflatex`+`pdftocairo`. |

#### File I/O (`test_io.py`)

| Test | Description |
|------|-------------|
| `test_roundtrip_empty` | Save and reload an empty schematic → loaded schematic equals original. |
| `test_roundtrip_components` | Save and reload a schematic with one of each v1 component type → all fields preserved exactly. |
| `test_roundtrip_wires` | Save and reload a schematic containing wires → all wire points preserved exactly. |
| `test_roundtrip_options` | Save and reload a schematic with a LaTeX-containing options string → options preserved exactly. |
| `test_roundtrip_label_offset` | Save and reload a component with `label_offset=(12.5, -30.0)` → offset preserved exactly. |
| `test_label_offset_none_not_serialised` | When `label_offset` is `None` the `label_offset` key is absent from the JSON. |
| `test_label_offset_missing_loads_as_none` | Old files without `label_offset` field deserialise with `label_offset=None`. |
| `test_label_offset_bad_type_raises` | `label_offset` with wrong type (string instead of two-element array) raises `SchematicLoadError`. |
| `test_roundtrip_wire_style` | A wire's `line_style`/`line_width` round-trip through save+load. |
| `test_wire_default_style_not_serialised` | Default wire style fields are omitted from the JSON (back-compat). |
| `test_wire_missing_style_loads_defaults` | Old files without wire style fields load as solid / 0.4 pt. |
| `test_wire_bad_style_type_raises` | A non-numeric `line_width` raises `SchematicLoadError`. |
| `test_roundtrip_wire_no_junction_dots` | A wire's `no_junction_dots` flag round-trips through save+load. |
| `test_wire_no_junction_dots_default_omitted` | The default (`False`) is omitted from the JSON. |
| `test_wire_no_junction_dots_bad_type_raises` | A non-boolean `no_junction_dots` raises `SchematicLoadError`. |
| `test_roundtrip_wire_no_termination_dots` | A wire's `no_termination_dots` flag round-trips through save+load. |
| `test_wire_no_termination_dots_default_omitted` | The default (`False`) is omitted from the JSON. |
| `test_wire_no_termination_dots_bad_type_raises` | A non-boolean `no_termination_dots` raises `SchematicLoadError`. |
| `test_roundtrip_wire_markers` | A wire's `start_marker`/`end_marker` round-trip through save+load. |
| `test_wire_markers_default_omitted` | Empty markers are omitted from the JSON (back-compat). |
| `test_wire_marker_bad_type_raises` | A non-string `end_marker` raises `SchematicLoadError`. |
| `test_roundtrip_wire_labels` | A wire's `start_label`/`end_label` round-trip through save+load. |
| `test_wire_labels_default_omitted` | Empty labels are omitted from the JSON (back-compat). |
| `test_wire_label_bad_type_raises` | A non-string `start_label` raises `SchematicLoadError`. |
| `test_roundtrip_wire_mid_label` / `test_wire_mid_label_defaults_omitted` | A wire's `mid_label`/`mid_label_pos` round-trip; empty label and the default 0.5 position are omitted. |
| `test_wire_mid_label_pos_clamped_on_load` / `test_wire_mid_label_pos_bad_type_raises` | `mid_label_pos` is clamped to [0,1] on load; a non-numeric value raises `SchematicLoadError`. |
| `test_roundtrip_legacy_labels_migration` | Load a v0.1 file with a `labels` dict → migrated to an equivalent options string. |
| `test_load_unknown_version` | Loading a `.hv` file with an unrecognized `version` string raises a descriptive error. |
| `test_load_invalid_json` | Loading a malformed JSON file raises a descriptive error. |
| `test_load_missing_field` | Loading a JSON file missing a required field raises a descriptive error. |
| `test_load_invalid_invariant` | Loading a JSON file that violates an invariant (e.g., diagonal wire) raises a descriptive error. |
| `test_save_creates_file` | `save()` creates a file at the specified path. |
| `test_save_is_utf8` | Saved `.hv` files are valid UTF-8 and contain no byte-order marks. |
| `test_save_is_atomic_overwrite` | `save()` atomically replaces an existing file (latest write wins) and leaves no `.tmp` file behind. |
| `test_bipole_fill_color_roundtrip` | `BipoleComponent.fill_color` survives a save/load cycle. |
| `test_bipole_border_width_roundtrip` | `BipoleComponent.border_width` survives a save/load cycle. |
| `test_bipole_defaults_not_saved` | Default `fill_color=""` and `border_width=0.4` are omitted from the JSON. |
| `test_bipole_line_style_roundtrip` | `BipoleComponent.line_style="dashed"` survives a save/load cycle. |
| `test_rect_style_fields_roundtrip` | `RectComponent` `fill_color`/`border_width`/`line_style` survive a save/load cycle with empty `options`. |
| `test_rect_legacy_options_migrated_to_fields` | A legacy `rect` storing its style in `options` is migrated into the `StyledComponent` fields on load (and `options` cleared). |
| `test_styled_defaults_not_saved` | Default `fill_color`/`border_width`/`line_style` are omitted from the JSON. |
| `test_mosfet_body_diode_roundtrip` | `MosfetComponent.body_diode=True` survives a save/load cycle. |
| `test_mosfet_body_diode_false_not_saved` | Default `body_diode=False` is omitted from the JSON. |

#### Registry (`test_registry.py`)

| Test | Description |
|------|-------------|
| `test_all_kinds_have_item_class` | Every `kind` in `REGISTRY` has a corresponding entry in `ITEM_CLASSES`. |
| `test_all_pins_on_half_grid` | Every `PinDef.offset` in every `ComponentDef` lies on a 0.5 GU boundary. |
| `test_default_span_matches_terminal_pin` | For two-terminal components, `default_span` equals the offset of the terminal pin. |
| `test_no_duplicate_kinds` | No two `ComponentDef` entries share the same `kind`. |

#### Geometry Helpers (`test_model.py`)

| Test | Description |
|------|-------------|
| `simplify_points` / `test_simplify_u_turn_collapses_to_straight` | Collapses consecutive duplicates and redundant collinear interior vertices; preserves endpoints and genuine elbows; does not mutate its input. A second dedup pass after collinear collapse handles the A–B–A → A case where collapsing B (same-y) would otherwise leave a consecutive duplicate at A. |
| `test_junction_no_spurious_dot_after_u_turn_drag` | Dragging a wire endpoint so the auto-elbow lands on the adjacent pin coordinate must not produce a junction dot at that pin (regression: the U-turn path left a duplicate interior vertex with degree 2, combining with the pin's degree 1 to falsely reach the dot threshold). |
| `test_no_junction_dots_wire_excluded` | A wire flagged `no_junction_dots` does not contribute to junction degree (its T-junction gets no dot). |
| `test_no_junction_dots_does_not_remove_others` | A flagged wire does not suppress a dot that other wires/pins independently justify at the same coordinate. |
| `test_no_termination_dots_suppresses_open_endpoints` | A wire flagged `no_termination_dots` contributes no open endpoints. |
| `test_no_termination_dots_does_not_affect_other_wires` | A flagged wire still counts as a connection for another wire ending on it (only its own free ends lose terminals). |
| `test_custom_marker_suppresses_open_endpoint` / `test_custom_marker_start_and_end_suppress_both_endpoints` | An end bearing a `start_marker`/`end_marker` is excluded from `open_endpoints()`; the unmarked end keeps its terminal. |
| `test_custom_marker_does_not_affect_other_wires` | A marked end still counts as a connection for another wire ending on it. |
| `junction_points` | Returns a dot coordinate exactly where the degree (wire segment-ends + coincident pin) is ≥ 3: 3-/4-way meetings, T-splits, and pin-on-pass-through; no dot for straight pass-throughs, lone corners, end-to-end meetings, or pin + single wire. |
| `open_endpoints` (`test_open_endpoints_*`) | Returns the set of wire endpoints (first/last point only) not coinciding with any connecting component pin; interior vertices are excluded; both ends of an unconnected wire are returned; a real-pin-connected end is excluded; a wire ending on a voltage annotation (`open`) pin stays open (annotation does not connect); a degenerate single-point wire connects nothing, so it does not suppress a real endpoint at the same coordinate. |
| `unconnected_pins` (`test_unconnected_pins_*`) | Returns component pins with no wire vertex on them and no second connecting pin sharing the coordinate: a lone component's pins are all returned; a pin with a wire endpoint or interior-vertex on it is excluded; two abutting pins are excluded; no components → empty set. `NON_CONNECTING_KINDS` pins (voltage annotation `open`) neither suppress a real pin's marker nor get one themselves, while a current annotation `short` does connect; a degenerate single-point wire on a pin does not mark it connected. |
| `wire_splits_at` | Finds wires whose interior passes through a point (returns `(wire_id, insert_index)`); a point already at a vertex is not returned — use `wire_corner_splits_at` for that case. |
| `wire_corner_splits_at` | Finds wires that have a point as an intermediate (non-endpoint) vertex (returns `(wire_id, vertex_index)`); used to split L-wires at their elbow when a new wire connects there. |
| `component_pin_positions` | Returns absolute pin coordinates with the mirror-then-rotate transform applied. |

#### Canvas Geometry (`test_geometry.py`)

The pure, Qt-scene-free helpers in `app/canvas/geometry.py` are unit-tested directly: `snap_gu` rounding; `scene_to_gu`/`gu_to_scene` round-trip; `snap_point_gu`; the `local_span_to_world` / `world_delta_to_local` rotation mapping (round-trip across all four rotations and both mirror states, plus known clockwise/mirror values); `dist2_to_segment` interior-vs-endpoint and degenerate cases; and `wire_proximity_key` (empty polyline → None, interior hit outranks endpoint touch, intermediate-vertex hit promoted to rank 0).

#### Wire Geometry (`test_wiregeometry.py`)

`WireGeometry` (wire snapping / hit-testing over a `Schematic`, no Qt scene) is unit-tested directly: `nearest_pin` radius behavior; `all_pin_positions`; `wire_snap_target` priority (pin over wire), grid fallback, snap-onto-segment, and own-wire exclusion; `vertex_is_draggable` (endpoint-on-pin locked, intermediate always draggable); `wire_vertex_at`; `unconnected_pin_at` (free pin detected, connected pin skipped); and `click_select_wire_id` preferring a pass-through wire over the grabbed stub.

#### Commands (`test_commands.py`)

In addition to the undo/redo behaviors in §13.3, the pure (Qt-free) command layer is unit-tested directly, including: `MoveCommand` wire-following (endpoint follows, rigid translate when both ends ride, auto-elbow, exact undo; select-all rigid translate of free endpoints; explicit `wire_ids` rigid translate for selected free wires; partial-move leaves unselected free endpoints anchored); `SplitWireCommand` split-into-two / undo (two halves replace original, undo restores original); `MergeWireCommand` merge-two-halves / undo; `MoveWireVertexCommand` reshape + simplify + undo, plus collapse-to-a-point removes the wire (not a degenerate single-point wire) with undo/redo restoring it; `DeleteCommand` with component and wire ids; `MacroCommand` composing split + add (3 wires) as one undoable unit; `MoveOptionsLabelCommand` set/undo/redo/clear of `label_offset`; `SetWireLineStyleCommand` / `SetWireLineWidthCommand` / `SetWireNoJunctionDotsCommand` / `SetWireNoTerminationDotsCommand` do/undo/redo on a wire; and `GroupRotateCommand` (single-component spin-in-place, two-component centroid rotation, internal wire vertex rotation, boundary wire reshaping, undo/redo, plus a boundary wire that collapses to a point under the rotation is removed rather than left degenerate, with undo/redo restoring it).

#### Preview Worker (`test_worker.py`)

`PreviewWorker` thread lifecycle: `shutdown()` stops the background `QThread`; it is idempotent (safe to call from both `closeEvent` and `aboutToQuit`); and emitting `QApplication.aboutToQuit` stops the thread even when the window's `closeEvent` never fired.

#### Preview Render (`test_preview_render.py`)

`pdf_to_qimage` (QtPdf): a compiled schematic PDF renders to a non-null `QImage`; a higher DPI yields a proportionally larger raster (same source page); garbage input raises cleanly (`CompileError`/`RuntimeError`) rather than crashing. Requires `pdflatex`; no Poppler involved.

#### Math Render (`test_mathrender.py`)

On-canvas math rendering and option-slot parsing (§5.8). Pure-logic tests always run: `_split_top_level` ignores commas inside `$…$`/`{…}`; `slot_fragments` pairs side-slot keys with values and drops `t=`, flags, and empty values; `slot_side` maps `^`/`_`/family to above/below; `label_display_latex` extraction. Render tests are gated on `latex`+`dvisvgm`: a fragment renders to a non-empty baseline-normalised `QPainterPath` (left ink at x=0, baseline at y=0); different fragments share the baseline; empty input yields `None`; the compiled SVG is cached on disk; and `render_async` delivers its result through the Qt event loop.

#### Symbol Geometry (`test_svgsym.py`)

`symbol_paths` glyph reconstruction (from the **self-contained** manifest, §5.3): `test_manifest_is_self_contained_for_glyph_kind` — `cV`'s `+`/`−` marks are baked into the manifest's `glyphs` list (real path `d` + a 6-element affine `matrix`), so no `.svg` access is needed at run time; every path returned for `cV` has real geometry (no unresolved glyph-ref leaks through as an empty path); a glyph-bearing kind (`cV`) returns strictly more paths than a glyph-free one (`R`); and a plain symbol still renders its strokes. Guards against the `+`/`−` marks silently disappearing. **Fill rule** (§5.2): `test_filled_diode_body_is_filled` — the filled diode `D*` has a filled body path while plain `D` does not (so toggling the filled option visibly updates the canvas); `test_stroke_only_symbols_not_filled` — pure outline symbols (`L`/`C`/`R`) have no filled paths, guarding the rule against over-filling stroked bodies.

#### Preferences (`test_preferences.py`)

The `Preferences` wrapper and `PreferencesDialog` (§10.8), exercised against an
isolated `QSettings` backed by a temp INI file (never touching the real user
store): auto-export and mark-unconnected-pins defaults are off; PDF/EPS and the
display flag round-trip and persist across new `Preferences` instances over the
same backing file; `_to_bool` normalizes the string booleans `QSettings` may
return; the dialog persists all checkbox state on accept and discards it on
cancel.

### 13.3 Integration Tests

Integration tests run against `SchematicScene` / `SchematicView` (file `test_scene.py`). They require Qt with an offscreen platform (`QT_QPA_PLATFORM=offscreen`) but no LaTeX installation. In a headless environment Qt's platform plugin needs the system GL/EGL libraries present; if Qt cannot initialise, the scene tests skip rather than fail.

| Test | Description |
|------|-------------|
| `test_place_component_updates_model` | Simulating a placement action on `SchematicScene` results in the component appearing in the scene's internal `Schematic`. |
| `test_undo_place` | Placing a component then calling undo removes it from the model. |
| `test_undo_redo_place` | Place → undo → redo restores the component with identical field values. |
| `test_undo_move` | Moving a component then calling undo restores its original position. |
| `test_undo_delete` | Deleting a component then calling undo restores it and any connected wires. |
| `test_undo_edit_label` | Editing a label then calling undo restores the previous label value. |
| `test_undo_stack_depth` | Performing 20 sequential operations then undoing all 20 returns the schematic to its original empty state. |
| `test_source_reflects_scene` | After placing a component, `generate()` of the scene's model contains the expected CircuiTikZ keyword (proxy for the Phase-9 source panel). |
| `test_snap_to_grid` | A component placed/dragged between grid points snaps to the nearest 0.25 GU point. |
| `test_component_drag_snaps_to_grid_mid_drag` | Component item position is snapped to 0.25 GU on every mouse-move during a drag, not only on release (regression: previously the visual was unsnapped mid-drag). |
| `test_pin_snap` | Beginning a wire near a pin snaps the wire start point to the exact pin coordinates. |
| `test_component_survives_repeated_moves` | A component dragged multiple times in succession stays visible and position-synced (regression: item reconciliation, not destroy/recreate). |
| `test_items_movable_only_in_select_mode` | Component drag/selection is enabled only in Select mode. |
| `test_wire_ghost_appears_during_component_drag` | Dragging a connected component shows a live ghost of the reshaped wires; the model is untouched until release. |
| `test_wire_preview_*` | The in-progress wire ghost spawns, tracks the cursor, switches its snap marker for pin vs. grid node, and routes its corner by dominant axis (no modifier key flips it). |
| `test_route_manhattan` | `route()` returns a two-point path for axis-aligned targets and a single dominant-axis corner otherwise. |
| `test_grid_node_anchor_adds_vertex` | Clicking empty space mid-wire drops an intermediate anchor; a collinear anchor is simplified away. |
| `test_click_free_pin_enters_wire_mode` / `test_terminate_on_pin_returns_to_select` | Auto-enter on a free-pin click; auto-exit when ending on a pin; connected-pin clicks and empty-space double-clicks behave per §6.4. |
| `test_double_click_wire_body_enters_wire_mode` / `test_double_click_wire_commits_splits_on_add` / `test_double_click_wire_vertex_enters_wire_mode` / `test_double_click_empty_space_enters_wire_mode` | Double-clicking a wire, wire vertex, or blank canvas in SELECT mode auto-enters WIRE mode from the snapped grid point; routing away and finalizing splits any target wire as normal. |
| `test_double_click_wire_near_component_enters_wire_mode` | Wire double-click is detected even when the wire is inside a component's bounding box — the wire check runs before the component check (regression: component bbox previously swallowed the event). |
| `test_wire_label_inline_edit_commits` / `test_wire_label_inline_edit_cancel_leaves_model` | In-place editing of a wire endpoint label (§4.3): `begin_label_edit` pre-fills the editor with the raw fragment and hides the display; commit writes via `set_wire_*_label` and restores the display; Escape leaves the model unchanged. |
| `test_set_wire_mid_label_and_pos` / `test_mid_label_noop_when_unchanged` | Mid-label text/position setters are undoable and clamp position to [0,1]; unchanged values push no command. |
| `test_mid_label_inline_edit_commits` | Double-click editing of the mid-label (`begin_label_edit("mid")`) pre-fills/hides the display and commits via `set_wire_mid_label`, restoring the display. |
| `test_double_click_free_endpoint_opens_label_editor` | Double-clicking a free wire endpoint opens its label editor for the correct end (start/last) and stays in SELECT mode (§6.4). |
| `test_double_click_connected_endpoint_enters_wire_mode` | A wire endpoint on a component pin is not a label target — it falls through to WIRE-mode routing. |
| `test_tab_cycle_endpoint_marker` / `test_tab_cycle_start_vs_end_endpoint` | `cycle_at` on a free endpoint steps that end's marker through `WIRE_MARKER_CYCLE` (wraps; `backward` reverses; undoable); the cursor's endpoint picks start vs. end. |
| `test_tab_cycle_line_style_on_body` / `test_tab_cycle_interior_vertex_cycles_line_style` | `cycle_at` on a wire body (or interior vertex) steps the line style through `WIRE_LINE_STYLE_CYCLE` without touching endpoint markers. |
| `test_tab_cycle_empty_space_is_noop` | `cycle_at` off any wire changes nothing and returns False (so `Tab` keeps normal focus behaviour). |
| `test_drag_corner_reshapes_wire` / `test_drag_vertex_is_undoable` / `test_vertex_drag_preview_is_manhattan` / `test_vertex_drag_preview_is_simplified` | Dragging a draggable wire vertex reshapes the wire (Manhattan-preserving) and is undoable; the live drag preview is Manhattan and simplified throughout (no diagonal segments, no redundant collinear vertices until release); pin-locked endpoints are not draggable. |
| `test_ocirc_follows_dragged_endpoint` | Open-circle item tracks a free wire endpoint in real time as it is dragged — the stale position is removed and the new position appears before the drag is released (regression: ocirc previously stayed put until commit). |
| `test_pin_circles_absent_by_default` / `test_pin_circles_appear_when_enabled` / `test_pin_circles_toggle_off_removes_items` | Unconnected-pin circles (§10.5) are absent until `set_mark_unconnected_pins(True)`, then drawn at each free pin, and removed again when toggled off. |
| `test_pin_circle_removed_when_pin_gets_wired` | With the preference on, attaching a wire to a previously-free pin removes that pin's circle on the next rebuild while the still-free pin keeps its own. |
| `test_wire_shape_*` | Wire selection hit-area is the thin band along the segments, not the bounding rect, so a wire does not steal clicks from an overlapping component. |
| `test_drag_release_at_same_spot_is_noop` / `test_click_near_endpoint_selects_short_wire` / `test_click_on_segment_near_vertex_does_not_move_it` | A vertex grab is a drag only if the snapped cursor moves between press and release; a stationary click selects the wire (no command, no geometry change), so a short wire with an open-circle end is selectable/deletable near its ends and clicking a segment near a vertex never relocates the vertex or inserts a spurious junction (regression). |
| `test_click_at_t_junction_selects_through_wire_half` | With split-on-join, each half of a split through wire is a separate wire object; clicking on the body of each half selects that half (not the stub). |
| `test_junction_dot_item_appears_for_three_wires` / `…removed_when_wire_deleted` | Junction dot items appear/disappear as wire connectivity changes. |
| `test_snap_to_existing_wire_vertex` / `test_snap_onto_wire_segment` | Wire routing snaps to existing wire vertices and segment points (pin snap takes priority). |
| `test_connect_to_mid_segment_splits_target` / `test_drag_vertex_onto_segment_splits_target` | Connecting (by drawing or by dragging a vertex) onto a wire's mid-segment splits the target into two independent wire objects, forms a junction, and is one undoable action that restores the original single wire. |
| `test_connect_to_wire_corner_splits_l_wire` / `test_connect_to_wire_corner_split_is_one_undo` | Connecting a new wire at an L-wire's corner (intermediate vertex) splits the L-wire into two straight wires, forms a junction, and is one undoable action. Connecting at an existing endpoint leaves the wire unchanged. |
| `test_click_near_endpoint_selects_short_wire` | After split-on-join the stub is selectable/deletable; deleting it merges the through-wire halves back into one wire (regression + merge-on-delete behavior). |
| `test_delete_selected_wire` | A directly-selected wire is deleted and restored on undo. |
| `test_no_index_method` / `test_group_rotate_then_delete_then_paint_does_not_crash` | The scene uses `QGraphicsScene.NoIndex`; group-rotating a selection containing a junction dot and then deleting it, followed by a repaint, completes without crashing (regression: the default BSP index retained a dangling pointer to coordinate-keyed junction/open-circle dots freed during `_rebuild_items`, segfaulting on the next paint). Enforces the §6.8 memory-safety invariant. |
| `test_random_mutation_sequences_never_crash_paint` | Randomized sequences of place/wire/rotate/delete/nudge/undo/redo, painting through a real view (and `scene.render`) after each step, never crash. A probabilistic safety net for the §6.8 graphics-item-lifetime invariant (a use-after-free faults nondeterministically and cannot be checked deterministically without a native sanitizer). |
| `test_resolve_pen_style_mapping` | `_resolve_pen_style()` maps line-style tokens to Qt pen styles (case-insensitive; unknown/empty → solid), shared by rect and bipole items. |
| `test_bipole_line_style_changes_canvas_rendering` | Setting a bipole's `line_style` to `"dashed"` via the scene renders a different canvas image than solid. Regression: `BipoleItem` previously ignored `line_style` when building its pen. |

### 13.4 Acceptance Criteria

The following criteria define v1 completion. Each must be verified manually by the author against a working build on the development machine.

#### AC-1: Component Placement
- [ ] All 13 v1 component types appear in the palette, grouped by category.
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
- [ ] Double-clicking or clicking a second pin terminates the wire.
- [ ] The resulting wire is rendered correctly on the canvas.
- [ ] `Escape` cancels a wire in progress without adding it to the schematic.
- [ ] Clicking an empty grid point mid-wire drops an intermediate anchor and continues routing.
- [ ] Clicking a free pin in Select mode auto-enters Wire mode; terminating on a pin returns to Select mode.
- [ ] Wires snap to existing wire vertices and to points on existing wire segments.
- [ ] Connecting to the middle of a wire splits it and shows a solid junction dot; the same dot appears wherever 3+ wire ends (or a pin + a pass-through wire) meet.
- [ ] Dragging a wire's draggable vertex reshapes it while staying Manhattan; pin-locked endpoints have no handle.
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

## 14. AI-Assisted Implementation Guide

This section records recommended practices for implementing this project with Claude as a coding assistant. It is intended to be provided alongside this spec at the start of each implementation session.

### 14.1 Model Selection

| Task | Recommended Model |
|------|------------------|
| Registry entries, dataclass definitions | Claude Sonnet 4.6 |
| Unit test implementation | Claude Sonnet 4.6 |
| Code generation layer (`codegen/`) | Claude Sonnet 4.6 |
| File I/O and validation layer | Claude Sonnet 4.6 |
| Preview pipeline (`preview/`) | Claude Sonnet 4.6 |
| UI panels (`app/ui/`) | Claude Sonnet 4.6 |
| Canvas interaction state machine (`scene.py`, `view.py`) | Claude Opus 4.6 |
| Undo/redo command stack (`commands.py`) | Claude Opus 4.6 |
| Coordinate transform and rotation/mirror math | Claude Opus 4.6 |
| Debugging sessions with non-obvious root causes | Claude Opus 4.6 |

### 14.2 Extended Thinking

Enable extended thinking for:
- Designing the canvas interaction state machine (Place / Select / Wire / Pan modes and their transitions)
- Getting the rotation and mirror transform math consistent between `items.py` (canvas painting) and `codegen/circuitikz.py` (coordinate output) before either is implemented
- The `DeleteCommand` inverse logic (restoring wires connected to a deleted component)

Leave extended thinking off for all other tasks. The spec has already resolved the key design decisions; extended thinking adds latency and cost without benefit for tasks where the path is clear.

### 14.3 Session Setup

At the start of each implementation session, provide:
1. This spec file in full
2. The current contents of any files directly relevant to the session's task
3. A one-line statement of what the session should accomplish (e.g., "implement `app/codegen/circuitikz.py` and its unit tests")

The spec is the authoritative reference. If anything in the generated code contradicts the spec, the spec takes precedence and the discrepancy should be flagged.

**Keep the spec in sync (mandatory).** Per Section 0, every task that adds,
changes, or removes a feature MUST update this specification in the same change
set. Before finishing a task, re-read the sections your change touches and
confirm they describe the new behavior; update Section 13 with corresponding
test entries and bump the **Version** field for substantive changes. Treat "the
spec still matches the code" as part of the definition of done.

### 14.4 Recommended Implementation Order

Work bottom-up, layer by layer. Each layer is independently testable before the next begins.

| Phase | Files | Verification |
|-------|-------|-------------|
| 1 | `app/components/model.py`, `app/components/registry.py` | `test_registry.py` passes |
| 2 | `app/schematic/model.py`, `app/schematic/validate.py` | `test_model.py` passes |
| 3 | `app/schematic/io.py` | `test_io.py` passes |
| 4 | `app/codegen/circuitikz.py` | `test_codegen.py` passes |
| 5 | `app/canvas/style.py`, `app/canvas/items.py` | Visual inspection: each component renders correctly |
| 6 | `app/canvas/commands.py` | Integration undo/redo tests pass |
| 7 | `app/canvas/scene.py`, `app/canvas/view.py` | Remaining integration tests pass |
| 8 | `app/preview/latex.py`, `app/preview/worker.py` | Preview compiles and displays for a simple schematic |
| 9 ✓ | `app/ui/` (all panels), `main.py` | Acceptance criteria AC-1 through AC-9 |

Do not proceed to the next phase until the current phase's verification condition is met. This keeps debugging localized to the layer most recently added.

### 14.5 Cross-Layer Consistency Checks

Two pairs of files must stay in sync with each other. Flag any divergence immediately:

- **`app/components/registry.py` ↔ `app/canvas/items.py`**: Every `kind` in `REGISTRY` must have an entry in `ITEM_CLASSES`, and every `PinDef` offset in the registry must correspond to a pin indicator drawn at the same position in the matching `ComponentItem.paint()`.
- **`app/canvas/items.py` ↔ `app/codegen/circuitikz.py`**: Rotation and mirror transforms must produce identical coordinate results in both files. A component rotated 90° on the canvas must generate CircuiTikZ coordinates that match what is visually shown.

---

## 15. Out of Scope (v1)

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
