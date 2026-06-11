"""
Lightweight, opt-out update *notifier* (spec §11.2).

Heaviside does not self-update. On startup (when enabled in Preferences) it makes
a single read-only HTTPS request to the GitHub Releases API, compares the newest
published release to the running version, and — if a newer one exists — points
the user at the download page. Nothing is downloaded or installed automatically.

Design notes:

* **No third-party dependency.** Uses ``urllib`` + ``json`` from the stdlib and a
  small hand-rolled version comparison, so nothing extra has to be bundled.
* **Pre-releases are considered.** While the project is in alpha its releases are
  marked *pre-release* on GitHub, and the ``/releases/latest`` endpoint silently
  skips those. We therefore query the releases *list* and pick the newest by
  version, including pre-releases (drafts are always ignored).
* **Fail-silent.** Any network/parse error returns ``None`` (no update found),
  so an offline or rate-limited launch is a no-op, never an error dialog.
* **Qt-free core.** :func:`check_for_update` and the version helpers are pure and
  unit-tested without an event loop; :func:`check_async` adds the threading.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlparse

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

#: GitHub repository in ``owner/name`` form.
REPO = "whileman133/Heaviside"
#: Releases list (newest first). Unlike ``/releases/latest`` this includes
#: pre-releases, which alpha builds are tagged as.
RELEASES_API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"
#: Human-facing page the notifier sends the user to.
RELEASES_PAGE_URL = f"https://github.com/{REPO}/releases/latest"

_USER_AGENT = "Heaviside-update-check"
_DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True)
class UpdateInfo:
    """A newer release than the one running."""

    version: str   # bare version, e.g. "0.3.0"
    tag: str       # the git tag, e.g. "v0.3.0"
    url: str       # release page (html_url)
    notes: str     # release body (markdown), truncated
    prerelease: bool


# ---------------------------------------------------------------------------
# Version comparison (no `packaging` dependency)
# ---------------------------------------------------------------------------

def _parse_version(text: str) -> tuple[tuple[int, ...], int, str]:
    """Parse ``"v1.2.3-alpha.1"`` into a sortable key.

    Returns ``(release_numbers, prerelease_rank, prerelease_label)`` where
    ``prerelease_rank`` is ``1`` for a final release and ``0`` for a pre-release
    (so ``1.0.0`` sorts above ``1.0.0-alpha``), and ``prerelease_label`` breaks
    ties between pre-releases (``alpha`` < ``beta`` < ``rc`` happens to sort
    correctly lexicographically). Unparseable input yields a zero key.
    """
    text = (text or "").strip().lstrip("vV")
    if not text:
        return ((0,), 1, "")
    # Split the numeric release core (1.2.3) from any pre-release/build suffix.
    m = re.match(r"(\d+(?:\.\d+)*)(?:[-+](.*))?$", text)
    core, suffix = (m.group(1), m.group(2) or "") if m else (text, "")
    numbers = tuple(int(p) for p in re.findall(r"\d+", core)) or (0,)
    is_final = 0 if suffix else 1
    return (numbers, is_final, suffix.lower())


def is_newer(remote: str, local: str) -> bool:
    """True if release version *remote* is strictly newer than *local*."""
    return _parse_version(remote) > _parse_version(local)


def _safe_release_url(url: str) -> str:
    """Validate a release URL from the API response before the UI opens it.

    The ``html_url`` field comes from a network response; the notifier dialog
    hands it straight to the OS URL opener, so a tampered/spoofed response must
    not be able to launch ``file:``/``javascript:`` or an arbitrary site.  Only
    ``https`` URLs on ``github.com`` (or a subdomain) pass through; anything
    else falls back to the hardcoded releases page.
    """
    try:
        parts = urlparse(url or "")
    except ValueError:
        return RELEASES_PAGE_URL
    host = (parts.hostname or "").lower()
    if parts.scheme == "https" and (host == "github.com" or host.endswith(".github.com")):
        return url
    return RELEASES_PAGE_URL


# ---------------------------------------------------------------------------
# Network + selection (pure; inject `fetch` in tests)
# ---------------------------------------------------------------------------

def _fetch_releases(*, timeout: float = _DEFAULT_TIMEOUT) -> list | None:
    """GET the releases list. Returns the parsed JSON array, or ``None`` on any
    network/parse failure (offline, rate-limited, malformed)."""
    req = urllib.request.Request(
        RELEASES_API_URL,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - https only
            raw = resp.read()
    except Exception:  # noqa: BLE001 - any failure => "no update found"
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, list) else None


def check_for_update(
    current: str,
    *,
    include_prereleases: bool = True,
    timeout: float = _DEFAULT_TIMEOUT,
    fetch=_fetch_releases,
) -> UpdateInfo | None:
    """Return the newest release strictly newer than *current*, else ``None``.

    Drafts are always skipped. Pre-releases are included by default (alpha
    builds are tagged as pre-releases). ``fetch`` is injectable for testing.
    """
    releases = fetch(timeout=timeout)
    if not releases:
        return None

    best: UpdateInfo | None = None
    for rel in releases:
        if not isinstance(rel, dict) or rel.get("draft"):
            continue
        if rel.get("prerelease") and not include_prereleases:
            continue
        tag = str(rel.get("tag_name") or "")
        version = tag.lstrip("vV")
        if not version or not is_newer(version, current):
            continue
        if best is not None and not is_newer(version, best.version):
            continue
        best = UpdateInfo(
            version=version,
            tag=tag,
            url=_safe_release_url(str(rel.get("html_url") or "")),
            notes=str(rel.get("body") or "")[:2000],
            prerelease=bool(rel.get("prerelease")),
        )
    return best


# ---------------------------------------------------------------------------
# Async wrapper (delivers the result on the calling/UI thread)
# ---------------------------------------------------------------------------

class _ProbeSignals(QObject):
    done = Signal(object)  # UpdateInfo | None


class _ProbeTask(QRunnable):
    def __init__(self, current: str, signals: "_ProbeSignals") -> None:
        super().__init__()
        self._current = current
        self._signals = signals

    def run(self) -> None:  # noqa: D401 - QRunnable hook
        try:
            info = check_for_update(self._current)
        except Exception:  # noqa: BLE001 - never let a probe crash the pool
            info = None
        self._signals.done.emit(info)


# Keep signal objects alive until their task fires (else GC'd mid-flight).
_live_probes: set[_ProbeSignals] = set()


def check_async(current: str, on_done) -> None:  # noqa: ANN001
    """Check for an update on a worker thread; call ``on_done(UpdateInfo|None)``
    on the calling thread when done. Fail-silent (delivers ``None`` on error)."""
    signals = _ProbeSignals()

    def _relay(info) -> None:  # noqa: ANN001
        _live_probes.discard(signals)
        on_done(info)

    signals.done.connect(_relay)
    _live_probes.add(signals)
    QThreadPool.globalInstance().start(_ProbeTask(current, signals))
