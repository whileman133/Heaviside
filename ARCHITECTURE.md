# Heaviside — Software Architecture

## Layer Overview

```mermaid
graph TB
    subgraph Entry["Entry Point"]
        main["main.py\nQApplication + MainWindow"]
    end

    subgraph UI["UI Layer  (app/ui/)"]
        MW["MainWindow\n─────────────\nmenus, toolbar,\nfile I/O, status bar"]
        PAL["ComponentPalette\n─────────────\nthumb tiles, search\n→ start_placement()"]
        PROP["PropertiesPanel\n─────────────\noptions text field\nrotate / mirror btns"]
        SRC["SourcePanel\n─────────────\nread-only source\n300 ms debounce"]
    end

    subgraph Canvas["Canvas Layer  (app/canvas/)"]
        SCENE["SchematicScene\n─────────────\nmode FSM, undo stack\nmouse events → commands"]
        VIEW["SchematicView\n─────────────\nzoom / pan\nkeyboard dispatch"]
        ITEMS["Graphics Items\n─────────────\nComponentItem subtypes\nWireItem, JunctionItem\nOpenCircleItem, PreviewItem"]
        CMDS["UndoStack + Commands\n─────────────\nPlace/Delete/Move/Wire\nEdit/Rotate/Mirror\nSplit/Merge/MoveVertex\nMacro  (12 total)"]
    end

    subgraph Model["Model Layer  (app/schematic/ + app/components/)"]
        SCH["Schematic\n─────────────\nComponent[ ]\nWire[ ]\nmetadata{ }"]
        COMPS["ComponentDef / PinDef\n─────────────\nkind, bbox, pins\ndisplay_name (frozen)"]
        REG["REGISTRY\n─────────────\ndict[kind → ComponentDef]\nITEM_CLASSES dict"]
        VAL["validate()"]
        IO["save() / load()\nJSON ↔ Schematic"]
    end

    subgraph Codegen["Code Generation  (app/codegen/)"]
        GEN["generate()\n─────────────\nSchematic → LaTeX string\npure function"]
    end

    subgraph Preview["Preview Pipeline  (app/preview/)"]
        WORKER["PreviewWorker\n─────────────\nQThread + debounce\n5 s schematic / 0.5 s eq"]
        LATEX["latex.py\n─────────────\nbuild_tex()\ncompile_tex()  ← pdflatex\npdf_to_qimage() ← pdf2image"]
    end

    main --> MW
    MW --> SCENE
    MW --> VIEW
    MW --> PAL
    MW --> PROP
    MW --> SRC
    MW --> WORKER

    PAL --> SCENE
    PROP --> SCENE
    SRC --> GEN
    VIEW --> SCENE

    SCENE --> CMDS
    SCENE --> ITEMS
    CMDS --> SCH
    ITEMS --> REG

    SCH --> COMPS
    COMPS --> REG

    GEN --> SCH
    GEN --> VAL

    IO --> SCH
    IO --> VAL

    WORKER --> GEN
    WORKER --> LATEX
```

---

## SchematicScene — State Variables

```mermaid
graph LR
    subgraph Persistent["Persistent Document State"]
        S1["_schematic: Schematic\n(live model)"]
        S2["_stack: UndoStack\n(undo/redo history)"]
    end

    subgraph Mode["Interaction Mode FSM"]
        S3["_mode: Mode\n{SELECT, PLACE, WIRE, PAN}"]
        S4["_panning: bool"]
    end

    subgraph ItemMaps["Scene Item Maps  (rebuilt on every command)"]
        S5["_comp_items: dict[id → ComponentItem]"]
        S6["_wire_items: dict[id → WireItem]"]
        S7["_junction_items: dict[xy → JunctionItem]"]
        S8["_open_circle_items: dict[xy → OpenCircleItem]"]
    end

    subgraph PlaceState["Placement Ghost State"]
        S9["_place_kind: str | None"]
        S10["_place_rotation: int  {0/90/180/270}"]
        S11["_place_mirror: bool"]
        S12["_ghost: ComponentItem | None"]
    end

    subgraph WireState["Wire-Drawing State"]
        S13["_wire_pts: list[xy]\n(anchored vertices so far)"]
        S14["_wire_preview: WirePreviewItem | None"]
    end

    subgraph DragState["Drag-Move State"]
        S15["_drag_start: dict[id → xy]\n(positions at drag start)"]
        S16["_drag_wire_ids: set[id]\n(wires selected at drag start)"]
        S17["_previewed_wire_ids: set[id]\n(showing live-drag preview)"]
    end

    subgraph VertexState["Wire-Vertex Drag State"]
        S18["_vertex_drag: (wire_id, index, orig_xy) | None"]
        S19["_vertex_press_gu: xy | None\n(press position for click/drag disambiguation)"]
    end

    subgraph Clipboard["Clipboard"]
        S20["_clipboard_components: list[Component]"]
        S21["_clipboard_wires: list[Wire]"]
    end
```

---

## Signals & Data Flow

```mermaid
sequenceDiagram
    actor User

    participant View as SchematicView
    participant Scene as SchematicScene
    participant Cmd as UndoStack / Command
    participant Model as Schematic (model)
    participant MW as MainWindow
    participant Src as SourcePanel
    participant Prop as PropertiesPanel
    participant Wkr as PreviewWorker

    User->>View: mouse / keyboard
    View->>Scene: mousePressEvent / keyPressEvent

    Scene->>Cmd: _push(command)
    Cmd->>Model: command.do(schematic)
    Cmd->>Scene: _rebuild_items()
    Scene-->>MW: schematic_changed  ──────────────────────►
    Scene-->>MW: selection_changed_gu(comp_ids)  ─────────►
    Scene-->>MW: cursor_moved(x, y)  ────────────────────►
    Scene-->>MW: mode_changed(mode)  ────────────────────►
    Scene-->>MW: component_double_clicked(id)  ──────────►

    MW->>Prop: show_component(id)
    MW->>Src: (schematic_changed triggers _refresh)
    Src->>Src: generate(schematic)  → text

    MW->>Wkr: request_compile(source)
    Wkr->>Wkr: debounce 5 s
    Wkr-->>MW: compile_started
    Wkr-->>MW: preview_ready(QImage)
    View-->>MW: zoom_changed(factor)
```

---

## Command Taxonomy & Inverses

```mermaid
graph TD
    subgraph Atomic["Atomic Commands"]
        PC["PlaceCommand\n+ append component\n− remove by id"]
        DC["DeleteCommand\n+ remove comps + connected wires\n− restore at original indices"]
        MC["MoveCommand\n+ shift positions + reshape wires\n− shift back + restore wire points"]
        WC["WireCommand\n+ append wire\n− remove by id"]
        SWC["SplitWireCommand\n+ replace 1 wire → 2 halves\n− remove halves, restore original"]
        MWC["MergeWireCommand\n+ replace 2 wires → 1 merged\n− remove merged, restore originals"]
        MVC["MoveWireVertexCommand\n+ shift vertex + insert elbows\n− restore original points"]
        EC["EditCommand\n+ set new options string\n− restore old options string"]
        MOC["MoveOptionsLabelCommand\n+ set label_offset\n− restore old label_offset"]
        RC["RotateCommand\n+ set new rotation\n− restore old rotation"]
        MIC["MirrorCommand\n+ set new mirror bool\n− restore old mirror bool"]
    end

    subgraph Composite["Composite"]
        MAC["MacroCommand\n+ run children in order\n− run children in reverse\n\nUsed for:\n• Wire draw (Wire + Split×n)\n• Delete (Delete + Merge×n)\n• Paste (Place×n + Wire×n)\n• Rotate-all / Mirror-all"]
    end

    MAC --> PC
    MAC --> DC
    MAC --> WC
    MAC --> SWC
    MAC --> MWC
    MAC --> RC
    MAC --> MIC
```

---

## Module Dependency Graph

```mermaid
graph BT
    style Entry fill:#e8f4e8
    style Canvas fill:#e8f0ff
    style Model fill:#fff8e0
    style Preview fill:#fce8e8
    style UI fill:#f0f8ff

    subgraph Entry
        MAIN["main.py"]
    end
    subgraph UI
        MW2["ui.mainwindow"]
        PAL2["ui.palette"]
        PROP2["ui.properties"]
        SRC2["ui.sourcepanel"]
    end
    subgraph Canvas
        SCENE2["canvas.scene"]
        VIEW2["canvas.view"]
        ITEMS2["canvas.items"]
        CMDS2["canvas.commands"]
        STYLE["canvas.style"]
        SVGSYM["canvas.svgsym"]
    end
    subgraph Model
        SMODEL["schematic.model"]
        SIO["schematic.io"]
        SVAL["schematic.validate"]
        CMODEL["components.model"]
        CREG["components.registry"]
    end
    subgraph Preview
        LATEX2["preview.latex"]
        WORKER2["preview.worker"]
        GEN2["codegen.circuitikz"]
    end

    MAIN --> MW2

    MW2 --> SCENE2
    MW2 --> VIEW2
    MW2 --> PAL2
    MW2 --> PROP2
    MW2 --> SRC2
    MW2 --> WORKER2
    MW2 --> GEN2
    MW2 --> SIO
    MW2 --> SMODEL

    PAL2 --> SCENE2
    PAL2 --> ITEMS2
    PAL2 --> CREG
    PAL2 --> STYLE

    PROP2 --> SCENE2
    PROP2 --> CREG

    SRC2 --> SCENE2
    SRC2 --> GEN2

    VIEW2 --> SCENE2
    VIEW2 --> ITEMS2
    VIEW2 --> STYLE

    SCENE2 --> CMDS2
    SCENE2 --> ITEMS2
    SCENE2 --> STYLE
    SCENE2 --> CREG
    SCENE2 --> SMODEL

    ITEMS2 --> STYLE
    ITEMS2 --> SVGSYM
    ITEMS2 --> CREG

    CMDS2 --> SMODEL

    SVGSYM --> STYLE

    GEN2 --> SMODEL
    GEN2 --> CREG
    GEN2 --> SVAL

    SIO --> SMODEL
    SIO --> SVAL

    SVAL --> SMODEL
    SVAL --> CREG

    SMODEL --> CREG

    CREG --> CMODEL

    WORKER2 --> LATEX2
    WORKER2 --> GEN2
```
