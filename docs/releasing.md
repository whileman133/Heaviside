# Releasing Heaviside

This is the maintainer runbook for cutting a release. CI (`.github/workflows/ci.yml`)
runs the test suite on every push and PR; the release workflow
(`.github/workflows/release.yml`) builds and attaches the standalone binaries
when a version tag is pushed.

## Version numbers — keep these in sync

The project version appears in **four** places. A release bumps all of them to
the same value (and the git tag matches, prefixed with `v`):

| File | Field |
|------|-------|
| `pyproject.toml` | `version = "X.Y.Z"` |
| `heaviside.spec` | `version=`, `CFBundleShortVersionString`, `CFBundleVersion` (macOS bundle) |
| `PROJECT_SPEC.md` | `**Version:**` header field |
| `CHANGELOG.md` | the `## [X.Y.Z]` release heading |

If these drift, the built `.app` reports the wrong version in Finder's *Get Info*
and *About* dialogs. Grep for the old number before tagging to be sure:
`grep -rn "0\.4\.0" pyproject.toml heaviside.spec PROJECT_SPEC.md CHANGELOG.md`.

## Release steps

1. **Finalize the changelog.** Move the entries under `## [Unreleased]` in
   `CHANGELOG.md` into a new `## [X.Y.Z] - YYYY-MM-DD` section, and update the
   link reference at the bottom of the file.

2. **Bump the version** in all four files above to `X.Y.Z`.

3. **Verify locally.** Run the suite headless:
   `QT_QPA_PLATFORM=offscreen uv run pytest`. It must be green.

4. **Commit and push** the version bump and changelog on `main`.

5. **Tag and push the tag:**
   ```sh
   git tag vX.Y.Z
   git push --tags
   ```
   This triggers `release.yml`, which builds the Apple-Silicon `.app` and the
   Windows bundle, attaches them with `.sha256` checksums, and opens a **draft**
   GitHub Release.

6. **Publish the draft.** On GitHub → *Releases*, open the draft for `vX.Y.Z`:
   - Confirm the macOS `.zip`, Windows `.zip`, and their `.sha256` files are attached.
   - Paste the first-launch notice from
     [`release-notes-macos-snippet.md`](release-notes-macos-snippet.md) into the
     release description (so downloaders see the macOS Gatekeeper / Windows
     SmartScreen instructions).
   - **Publish release.**

7. **Verify the bundle's license folder.** Download the published macOS `.zip`
   and confirm `Heaviside.app/Contents/Resources/licenses/` contains
   `THIRD_PARTY_LICENSES.md`, `LGPL-3.0.txt`, and `GPL-3.0.txt` — the LGPLv3
   notice must physically ship inside the bundle (see the License section of the
   README and `licenses/THIRD_PARTY_LICENSES.md`).

## Testing the build without cutting a release

To exercise the build/package pipeline without consuming a version tag or
creating a GitHub Release, trigger the workflow manually: **Actions → Release →
Run workflow**. A manual (`workflow_dispatch`) run builds the macOS and Windows
bundles and uploads them as **run artifacts** (downloadable from the run summary
page, auto-expiring) but skips the release-publishing job. Download the macOS
artifact and confirm the `.app` launches and reports the correct version before
doing a real tagged release.

## Notes

- The macOS build is **Apple Silicon (arm64) only** and **ad-hoc signed** (not
  notarized); the first-launch instructions cover the resulting Gatekeeper
  prompt. Adding Developer ID signing/notarization would require an Apple
  Developer certificate stored as a GitHub Actions secret.
- The release workflow runs free on **public** repos. On a private repo it works
  but bills macOS minutes at a 10× multiplier against the Actions quota.
