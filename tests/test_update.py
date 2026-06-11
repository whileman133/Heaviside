"""
Tests for app/update — the opt-out update notifier (spec §11.2).

The version logic and release selection are pure and injectable (no network):
``check_for_update`` takes a ``fetch`` callable. ``check_async`` is exercised
against an offscreen Qt event loop.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtCore", reason="PySide6 not importable")

from app import update  # noqa: E402
from app.update import UpdateInfo, check_for_update, is_newer  # noqa: E402


# ---------------------------------------------------------------------------
# Version comparison
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("remote,local,expected", [
    ("0.3.0", "0.2.0", True),
    ("0.2.0", "0.2.0", False),
    ("0.2.0", "0.3.0", False),
    ("0.10.0", "0.9.0", True),      # numeric, not lexical
    ("1.0.0", "1.0.0-alpha", True),  # final beats its own pre-release
    ("1.0.0-alpha", "1.0.0", False),
    ("0.3.0-alpha", "0.2.0", True),  # pre-release of a higher version still wins
    ("0.2.0-beta", "0.2.0-alpha", True),
    ("v0.3.0", "0.2.0", True),       # leading v tolerated
    ("", "0.2.0", False),            # unparseable remote is never "newer"
])
def test_is_newer(remote, local, expected):
    assert is_newer(remote, local) is expected


# ---------------------------------------------------------------------------
# Release selection
# ---------------------------------------------------------------------------

def _rel(tag, *, prerelease=False, draft=False, body="notes", url=None):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": draft,
        "body": body,
        "html_url": (f"https://github.com/whileman133/Heaviside/releases/tag/{tag}"
                     if url is None else url),
    }


def _fetch(releases):
    return lambda timeout=5.0: releases


def test_picks_newest_including_prereleases():
    info = check_for_update("0.2.0", fetch=_fetch([
        _rel("v0.2.5"),
        _rel("v0.3.0", prerelease=True),
        _rel("v0.9.9", draft=True),   # drafts always ignored
    ]))
    assert isinstance(info, UpdateInfo)
    assert info.version == "0.3.0" and info.prerelease is True
    assert info.url == "https://github.com/whileman133/Heaviside/releases/tag/v0.3.0"


def test_excludes_prereleases_when_asked():
    info = check_for_update("0.2.0", include_prereleases=False, fetch=_fetch([
        _rel("v0.3.0", prerelease=True),
        _rel("v0.2.5"),
    ]))
    assert info is not None and info.version == "0.2.5"


def test_none_when_up_to_date():
    assert check_for_update("0.3.0", fetch=_fetch([_rel("v0.3.0")])) is None


def test_none_when_only_older():
    assert check_for_update("0.5.0", fetch=_fetch([_rel("v0.2.0")])) is None


def test_none_on_network_failure():
    assert check_for_update("0.2.0", fetch=lambda timeout=5.0: None) is None


def test_none_on_empty_list():
    assert check_for_update("0.2.0", fetch=_fetch([])) is None


def test_skips_malformed_entries():
    info = check_for_update("0.2.0", fetch=_fetch([
        "not-a-dict",
        {"no_tag": True},
        _rel("v0.4.0"),
    ]))
    assert info is not None and info.version == "0.4.0"


def test_notes_truncated():
    info = check_for_update("0.2.0", fetch=_fetch([_rel("v0.3.0", body="x" * 5000)]))
    assert info is not None and len(info.notes) <= 2000


# ---------------------------------------------------------------------------
# Release-URL validation (the UI opens info.url unprompted-by-content; a
# tampered API response must not be able to point it anywhere dangerous)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://github.com/whileman133/Heaviside/releases/tag/v9.0.0",
    "https://www.github.com/whileman133/Heaviside/releases",   # subdomain ok
    "https://GITHUB.COM/whileman133/Heaviside/releases",       # case-insensitive host
])
def test_release_url_https_github_passes(url):
    info = check_for_update("0.2.0", fetch=_fetch([_rel("v9.0.0", url=url)]))
    assert info is not None and info.url == url


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "javascript:alert(1)",
    "http://github.com/whileman133/Heaviside/releases",   # not https
    "https://evil.com/releases",
    "https://evilgithub.com/releases",                    # suffix-spoof, not a subdomain
    "https://github.com.evil.com/releases",               # prefix-spoof
    "",                                                    # missing html_url
    "not a url",
])
def test_release_url_unsafe_falls_back_to_releases_page(url):
    info = check_for_update("0.2.0", fetch=_fetch([_rel("v9.0.0", url=url)]))
    assert info is not None
    assert info.url == update.RELEASES_PAGE_URL


@pytest.mark.parametrize("url,ok", [
    ("https://github.com/x", True),
    ("https://api.github.com/x", True),
    ("file:///tmp/x", False),
    ("javascript:alert(1)", False),
    ("http://github.com/x", False),
    ("https://evil.com/x", False),
    ("https://github.com@evil.com/x", False),   # userinfo trick: host is evil.com
    ("", False),
])
def test_safe_release_url_unit(url, ok):
    got = update._safe_release_url(url)
    assert got == (url if ok else update.RELEASES_PAGE_URL)


# ---------------------------------------------------------------------------
# Network fetch error handling (mock urlopen)
# ---------------------------------------------------------------------------

def test_fetch_returns_none_on_urlopen_error(monkeypatch):
    def _boom(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(update.urllib.request, "urlopen", _boom)
    assert update._fetch_releases() is None


def test_fetch_returns_none_on_bad_json(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"<<not json>>"
    monkeypatch.setattr(update.urllib.request, "urlopen", lambda *a, **k: _Resp())
    assert update._fetch_releases() is None


# ---------------------------------------------------------------------------
# Async delivery
# ---------------------------------------------------------------------------

def test_check_async_delivers_on_event_loop(qtbot, monkeypatch):
    sentinel = UpdateInfo("9.9.9", "v9.9.9", "https://example/9.9.9", "n", False)
    monkeypatch.setattr(update, "check_for_update", lambda current: sentinel)
    got: list = []
    update.check_async("0.0.0", got.append)
    qtbot.waitUntil(lambda: len(got) == 1, timeout=3000)
    assert got[0] is sentinel
