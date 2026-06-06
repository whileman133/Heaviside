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
| [`component-editor.md`](component-editor.md) | Draft (design — not yet implemented) | A developer-first editor that imports a CircuiTikZ symbol from its generating command, aligns it to the grid, captures pins/labels, and emits one declarative **Component Definition** that becomes the single source of truth — replacing the five hand-maintained files described in `PROJECT_SPEC.md` §5.5. |
