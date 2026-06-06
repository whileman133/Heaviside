# Heaviside Specifications

This directory holds focused, per-feature specifications that keep the root
[`PROJECT_SPEC.md`](../PROJECT_SPEC.md) — the master behavior specification —
from growing unwieldy. `PROJECT_SPEC.md` stays the authoritative top-level
document and links into the specs here; each document below governs one feature
in depth.

All specs follow the living-document rule in `PROJECT_SPEC.md` §0: a feature
change is not complete until its spec is updated in the same change set.

| Spec | Status | Summary |
|------|--------|---------|
| [`component-editor.md`](component-editor.md) | Draft — measurement tool + data file built | Generate grid-aligned CircuiTikZ components without hand-stored magic numbers: a tool that **measures** a symbol's pin anchors via `latex`/`dvisvgm`, plus one flat data file (`components/components.json`) holding pins/bbox/alignment/metadata — replacing the hand-maintained numbers from `PROJECT_SPEC.md` §5.5. |
