# Heaviside — Claude Instructions

## After every code change

After adding, changing, or removing any feature or behavior, always check:

1. **Tests** — Do any tests in `tests/` need to be added, updated, or removed to reflect the change? Add regression tests for bug fixes. Update tests whose expected behavior has changed. Remove tests for deleted behavior.

2. **Spec** — Does `PROJECT_SPEC.md` need to be updated? The spec is a living document (see its §0) and must stay in sync with the implementation at all times. Update prose, invariants, commands, keyboard shortcuts, acceptance criteria, and the §13 test table as needed.

3. **Changelog** — Does the change warrant an entry in `CHANGELOG.md`? Add user-facing additions, changes, fixes, and removals under the `## [Unreleased]` heading (create it if absent). Internal-only refactors that change no behavior may be skipped.

A change is not complete until the tests, the spec, and (where applicable) the changelog reflect it.
