#!/usr/bin/env python3
"""
Sign, notarize, and staple the macOS Heaviside.app — locally.

This reproduces what .github/workflows/release.yml does on the macOS runner, so
you can verify the whole Developer ID + notarization chain on your own machine
before cutting a release. macOS only.

    dist/Heaviside.app  ->  signed + notarized + stapled, ready to distribute

Prerequisites
-------------
1. A built app at dist/Heaviside.app (run scripts/build.py first, or pass
   --build to do it here).
2. Your **Developer ID Application** certificate imported into your login
   Keychain (double-click the .p12). codesign finds it automatically — the
   script auto-detects the identity, or set MACOS_SIGN_IDENTITY to pick one.
3. For notarization, App Store Connect API credentials. The cleanest way is to
   store them once as a notarytool keychain profile (no secrets on the command
   line or in env):

       xcrun notarytool store-credentials heaviside-notary \\
           --key ~/.appstoreconnect/private_keys/AuthKey_XXXXXXXXXX.p8 \\
           --key-id XXXXXXXXXX --issuer <issuer-uuid>

   The script uses the profile named by NOTARY_PROFILE (default
   "heaviside-notary"). Alternatively, point it at the key directly with
   NOTARY_KEY (path to the .p8), NOTARY_KEY_ID, and NOTARY_ISSUER.

Usage
-----
    python scripts/sign_macos.py                # sign + notarize + staple + verify
    python scripts/sign_macos.py --build        # build first, then the above
    python scripts/sign_macos.py --no-notarize  # sign + verify only (fast check)
    python scripts/sign_macos.py --zip          # also emit the distributable zip
    uv run python scripts/sign_macos.py

Notarization needs a network connection and usually takes a few minutes (the
script waits for Apple's verdict). --no-notarize is handy for a quick local
"does it sign and validate" loop.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_APP = _ROOT / "dist" / "Heaviside.app"
# Hardened-runtime relaxations a PyInstaller bundle needs under notarization:
# allow-jit + allow-unsigned-executable-memory (CPython/Qt allocate W+X memory)
# and disable-library-validation (load the bundled PySide6/Qt dylibs). The plist
# itself must stay comment-free — codesign's AMFI entitlements parser rejects XML
# comments ("AMFIUnserializeXML: syntax error").
_ENTITLEMENTS = _ROOT / "packaging" / "entitlements.plist"
_DEFAULT_PROFILE = "heaviside-notary"


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    """Echo and run a command, raising on failure (unless check=False)."""
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kw)


def _fail(msg: str) -> "int":
    print(f"\nERROR: {msg}", file=sys.stderr)
    return 1


def resolve_identity() -> str:
    """Return the Developer ID Application identity to sign with.

    Honors MACOS_SIGN_IDENTITY; otherwise auto-detects the sole "Developer ID
    Application" identity in the login keychain. Errors if there are none, or
    several and the choice is ambiguous.
    """
    explicit = os.environ.get("MACOS_SIGN_IDENTITY", "").strip()
    if explicit:
        return explicit

    out = subprocess.run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True, text=True,
    ).stdout
    names = re.findall(r'"(Developer ID Application:[^"]+)"', out)
    # De-duplicate while preserving order (a cert can appear under several lines).
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    if not seen:
        raise SystemExit(
            "No 'Developer ID Application' identity found in your Keychain.\n"
            "Import your .p12 (double-click it) and confirm with:\n"
            "    security find-identity -v -p codesigning"
        )
    if len(seen) > 1:
        joined = "\n  ".join(seen)
        raise SystemExit(
            "Multiple Developer ID Application identities found — set "
            "MACOS_SIGN_IDENTITY to choose one:\n  " + joined
        )
    return seen[0]


def build() -> None:
    print("==> Building the app (scripts/build.py)…")
    _run([sys.executable, str(_ROOT / "scripts" / "build.py")])


def sign(identity: str) -> None:
    print(f"==> Signing {_APP.name} as: {identity}")
    if not _ENTITLEMENTS.exists():
        raise SystemExit(f"Entitlements file missing: {_ENTITLEMENTS}")
    _run([
        "codesign", "--force", "--deep", "--timestamp", "--options", "runtime",
        "--entitlements", str(_ENTITLEMENTS),
        "--sign", identity,
        str(_APP),
    ])
    print("==> Verifying signature…")
    _run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(_APP)])


def _notary_credential_args() -> list[str]:
    """notarytool authentication flags: a stored keychain profile, or a direct
    API key trio. Prefers the profile."""
    key = os.environ.get("NOTARY_KEY", "").strip()
    key_id = os.environ.get("NOTARY_KEY_ID", "").strip()
    issuer = os.environ.get("NOTARY_ISSUER", "").strip()
    if key and key_id and issuer:
        return ["--key", key, "--key-id", key_id, "--issuer", issuer]
    profile = os.environ.get("NOTARY_PROFILE", _DEFAULT_PROFILE)
    return ["--keychain-profile", profile]


def notarize_and_staple() -> None:
    zip_path = _ROOT / "dist" / "Heaviside-notarize.zip"
    print("==> Zipping the app for notarization (ditto)…")
    _run(["ditto", "-c", "-k", "--keepParent", str(_APP), str(zip_path)])
    try:
        print("==> Submitting to Apple notary service (this can take a few minutes)…")
        _run(["xcrun", "notarytool", "submit", str(zip_path),
              *_notary_credential_args(), "--wait"])
        print("==> Stapling the notarization ticket…")
        _run(["xcrun", "stapler", "staple", str(_APP)])
    finally:
        zip_path.unlink(missing_ok=True)


def verify_gatekeeper() -> None:
    print("==> Validating the staple…")
    _run(["xcrun", "stapler", "validate", str(_APP)])
    print("==> Gatekeeper assessment (spctl)…")
    # Non-fatal echo of the verdict; a clean run prints "accepted ... Notarized".
    _run(["spctl", "-a", "-vvv", "--type", "exec", str(_APP)], check=False)


def make_zip() -> None:
    out = _ROOT / "dist" / "Heaviside-macos-arm64.zip"
    print(f"==> Packaging {out.name} (ditto)…")
    _run(["ditto", "-c", "-k", "--keepParent", str(_APP), str(out)])
    sha = subprocess.run(["shasum", "-a", "256", out.name],
                         cwd=out.parent, capture_output=True, text=True).stdout
    (out.parent / (out.name + ".sha256")).write_text(sha, encoding="utf-8")
    print(f"    -> dist/{out.name}  (+ .sha256)")


def main() -> int:
    global _APP
    parser = argparse.ArgumentParser(description="Sign/notarize/staple Heaviside.app (macOS).")
    parser.add_argument("--build", action="store_true",
                        help="run scripts/build.py first")
    parser.add_argument("--no-notarize", action="store_true",
                        help="sign and verify only; skip notarization/stapling")
    parser.add_argument("--zip", action="store_true",
                        help="also emit dist/Heaviside-macos-arm64.zip (+ .sha256)")
    parser.add_argument("--app", default=str(_APP),
                        help="path to the .app bundle (default: dist/Heaviside.app)")
    args = parser.parse_args()

    if sys.platform != "darwin":
        return _fail("macOS only — signing/notarization need codesign + notarytool.")

    _APP = Path(args.app).resolve()

    if args.build:
        build()
    if not _APP.exists():
        return _fail(f"{_APP} not found. Build it first (scripts/build.py) or pass --build.")

    try:
        identity = resolve_identity()
        sign(identity)
        if args.no_notarize:
            print("\n--no-notarize: skipped notarization. The app is signed but NOT "
                  "notarized, so a downloaded copy will still be quarantined.")
        else:
            notarize_and_staple()
            verify_gatekeeper()
        if args.zip:
            make_zip()
    except SystemExit as e:               # our own resolve_identity() guidance
        return _fail(str(e))
    except subprocess.CalledProcessError as e:
        return _fail(f"command failed (exit {e.returncode}): {' '.join(e.cmd)}")

    print("\nDone." + ("" if args.no_notarize else
          "  Heaviside.app is signed, notarized, and stapled."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
