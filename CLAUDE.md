# Heaviside — Claude Instructions

## After every code change

After adding, changing, or removing any feature or behavior, always check:

1. **Tests** — Do any tests in `tests/` need to be added, updated, or removed to reflect the change? Add regression tests for bug fixes. Update tests whose expected behavior has changed. Remove tests for deleted behavior.

2. **Spec** — Does `PROJECT_SPEC.md` need to be updated? The spec is a living document (see its §0) and must stay in sync with the implementation at all times. Update prose, invariants, commands, keyboard shortcuts, acceptance criteria, and the §13 test table as needed.

3. **Changelog** — Does the change warrant an entry in `CHANGELOG.md`? Add user-facing additions, changes, fixes, and removals under the `## [Unreleased]` heading (create it if absent). Internal-only refactors that change no behavior may be skipped.

A change is not complete until the tests, the spec, and (where applicable) the changelog reflect it.

## When the `.hv` file format changes

If you change `_FORMAT_VERSION` or the set of accepted versions (`_KNOWN_VERSIONS`) in `app/schematic/io.py`, you **must also re-save every bundled example** under `examples/` so they declare the new version and stay loadable in the app. The simplest way is to load and re-save each through `app.schematic.io` (which normalises the version on save):

```python
from pathlib import Path
from app.schematic import io
import json
for p in Path("examples").glob("*.hv"):
    data = json.loads(p.read_text(encoding="utf-8"))
    data["version"] = io._FORMAT_VERSION          # relabel to current
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    io.save(io.load(p), p)                          # validate + normalise
```

`tests/test_examples.py` enforces this — it fails if any example does not load or does not declare `_FORMAT_VERSION`. Run the suite after a format change.
