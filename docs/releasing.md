# Releasing Heaviside

This is the maintainer runbook for cutting a release. CI (`.github/workflows/ci.yml`)
runs the test suite on every push and PR; the release workflow
(`.github/workflows/release.yml`) builds and attaches the standalone binaries
when a version tag is pushed.

## Version numbers

The project version has a **single source of truth**: the `version` field in
`pyproject.toml`. `heaviside.spec` reads it at build time (so the macOS bundle's
`CFBundleShortVersionString` / `CFBundleVersion` stay in sync automatically — no
manual edit there). A release therefore bumps the version in **one** place:

1. `pyproject.toml` → `version = "X.Y.Z"`

Then record the release in `CHANGELOG.md` (move `[Unreleased]` into a new
`## [X.Y.Z]` heading) and tag with `vX.Y.Z`.

Two other "versions" are intentionally **separate** and not bumped with the app
version: the spec document's own `**Version:**` header in `PROJECT_SPEC.md`
(tracks the specification, not the app), and the `.hv` **file-format** version
(`_FORMAT_VERSION` in `app/schematic/io.py`, currently `0.1`).

## Release steps

1. **Finalize the changelog.** Move the entries under `## [Unreleased]` in
   `CHANGELOG.md` into a new `## [X.Y.Z] - YYYY-MM-DD` section, and update the
   link reference at the bottom of the file.

2. **Bump the version** in `pyproject.toml` to `X.Y.Z` (the single source of
   truth; `heaviside.spec` picks it up automatically).

3. **Verify locally.** Run the suite headless:
   `QT_QPA_PLATFORM=offscreen uv run pytest`. It must be green.

4. **Commit and push** the version bump and changelog on `main`.

5. **Tag and push the tag:**
   ```sh
   git tag vX.Y.Z
   git push --tags
   ```
   This triggers `release.yml`, which builds the Apple-Silicon `.dmg` (a
   drag-to-Applications disk image), the Windows **installer**
   (`Heaviside-windows-x64-setup.exe`, an Inno Setup setup program) *and* the
   portable Windows `.zip`, attaches them with `.sha256` checksums, and opens a
   **draft** GitHub Release.

   **Windows code signing (optional).** The installer and app `.exe` are
   Authenticode-signed when the `WINDOWS_CERT_PFX` (base64 of a `.pfx`) and
   `WINDOWS_CERT_PASSWORD` repository secrets are set; without them the build is
   produced UNSIGNED (a SmartScreen warning on first run), exactly mirroring the
   conditional macOS signing. Signing removes the SmartScreen warning (instantly
   with an EV certificate; over time as download reputation accrues with an OV
   certificate).

   While the project is in its **alpha** phase (pre-1.0, unstable file format),
   mark the GitHub Release as a **pre-release** when publishing so downloaders
   see the "not yet stable" signal. A plain `vX.Y.Z` tag is fine — the
   pre-release *flag* (set in step 6) is what conveys alpha status; an explicit
   `-alpha`/`-alpha.N` tag suffix is optional and only needed when you want
   several pre-releases under one version.

6. **Publish the draft.** On GitHub → *Releases*, open the draft for `vX.Y.Z`:
   - Confirm the macOS `.dmg`, Windows installer `…-setup.exe`, Windows `.zip`, and their `.sha256` files are attached.
   - Paste the first-launch notice from
     [`release-notes-macos-snippet.md`](release-notes-macos-snippet.md) into the
     release description (so downloaders see the macOS Gatekeeper / Windows
     SmartScreen instructions).
   - **Publish release.**

7. **Verify the bundle's license folder.** Download and open the published macOS
   `.dmg` and confirm `Heaviside.app/Contents/Resources/licenses/` contains
   `THIRD_PARTY_LICENSES.md`, `LGPL-3.0.txt`, and `GPL-3.0.txt` — the LGPLv3
   notice must physically ship inside the bundle (see the License section of the
   README and `licenses/THIRD_PARTY_LICENSES.md`).

## Testing the build without cutting a release

To exercise the build/package pipeline without consuming a version tag or
creating a GitHub Release, trigger the workflow manually: **Actions → Release →
Run workflow**. A manual (`workflow_dispatch`) run builds the macOS, Windows, and
Linux bundles and uploads them as **run artifacts** (downloadable from the run
summary page, auto-expiring) but skips the release-publishing job. Download the
macOS artifact and confirm the `.dmg` mounts, the app drags to Applications,
launches, and reports the correct version; download the Linux
`Heaviside-linux-x86_64.AppImage`, `chmod +x` it, and confirm it launches — before
doing a real tagged release.

## Notes

- The macOS build is **Apple Silicon (arm64) only**, shipped as a
  drag-to-Applications **`.dmg`**. When the Developer ID signing secrets are
  configured (see the header of `release.yml`), the workflow signs the app,
  builds the `.dmg`, then signs, notarizes, and staples the `.dmg` so it opens
  cleanly. Without those secrets it produces an **unsigned** `.dmg` (still a
  valid build for testing); the first-launch instructions cover the resulting
  Gatekeeper prompt.
- The Linux build ships as a self-contained **AppImage**
  (`Heaviside-linux-x86_64.AppImage`) alongside the portable `.tar.gz`. The
  workflow fetches `appimagetool` ad-hoc and builds the image via
  `scripts/make_appimage.py`; there is no Linux code-signing. The AppImage needs
  FUSE to run, or `--appimage-extract-and-run` on systems without it.
- The release workflow runs free on **public** repos. On a private repo it works
  but bills macOS minutes at a 10× multiplier against the Actions quota.
