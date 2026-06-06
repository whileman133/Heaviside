# AI-Assisted Implementation Guide

This document records the recommended practices used to implement Heaviside with
Claude as a coding assistant. It is **supplementary process documentation**, not
part of the behavior specification — the authoritative description of what the
software does lives in [`PROJECT_SPEC.md`](../PROJECT_SPEC.md). This guide was
historically Section 14 of that spec; it was extracted once the project reached a
stable version so the spec stays focused on behavior.

It is intended to be provided alongside the spec at the start of an
implementation session.

## Model Selection

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

## Extended Thinking

Enable extended thinking for:
- Designing the canvas interaction state machine (Place / Select / Wire / Pan modes and their transitions)
- Getting the rotation and mirror transform math consistent between `items.py` (canvas painting) and `codegen/circuitikz.py` (coordinate output) before either is implemented
- The `DeleteCommand` inverse logic (restoring wires connected to a deleted component)

Leave extended thinking off for all other tasks. The spec has already resolved the key design decisions; extended thinking adds latency and cost without benefit for tasks where the path is clear.

## Session Setup

At the start of each implementation session, provide:
1. The spec file in full
2. The current contents of any files directly relevant to the session's task
3. A one-line statement of what the session should accomplish (e.g., "implement `app/codegen/circuitikz.py` and its unit tests")

The spec is the authoritative reference. If anything in the generated code contradicts the spec, the spec takes precedence and the discrepancy should be flagged.

**Keep the spec in sync (mandatory).** Per `PROJECT_SPEC.md` §0, every task that
adds, changes, or removes a feature MUST update the specification in the same
change set. Before finishing a task, re-read the sections your change touches and
confirm they describe the new behavior; update the Section 13 test table with
corresponding test entries and bump the **Version** field for substantive
changes. Treat "the spec still matches the code" as part of the definition of
done.

## Recommended Implementation Order

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
| 9 | `app/ui/` (all panels), `main.py` | Acceptance criteria AC-1 through AC-9 |

Do not proceed to the next phase until the current phase's verification condition is met. This keeps debugging localized to the layer most recently added.

## Cross-Layer Consistency Checks

Two pairs of files must stay in sync with each other. Flag any divergence immediately:

- **`app/components/registry.py` ↔ `app/canvas/items.py`**: Every `kind` in `REGISTRY` must have an entry in `ITEM_CLASSES`, and every `PinDef` offset in the registry must correspond to a pin indicator drawn at the same position in the matching `ComponentItem.paint()`.
- **`app/canvas/items.py` ↔ `app/codegen/circuitikz.py`**: Rotation and mirror transforms must produce identical coordinate results in both files. A component rotated 90° on the canvas must generate CircuiTikZ coordinates that match what is visually shown.
