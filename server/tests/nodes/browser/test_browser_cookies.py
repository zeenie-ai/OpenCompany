"""Session-file parsing (nodes/browser/_cookies.py)."""

from __future__ import annotations

import json

import pytest

from nodes.browser._cookies import (
    CookieFormatError,
    filter_domains,
    from_cdp_cookies,
    parse_session_file,
    to_cookie_param,
    to_storage_state,
)

NOW = 1_800_000_000.0
FUTURE = NOW + 86_400


def test_playwright_storage_state():
    data = {
        "cookies": [
            {"name": "sid", "value": "s1", "domain": ".example.com", "path": "/", "expires": FUTURE, "httpOnly": True, "secure": True, "sameSite": "Lax"},
            {"name": "host", "value": "h", "domain": "app.example.com", "path": "/", "expires": -1, "httpOnly": False, "secure": False, "sameSite": "Strict"},
            {"name": "old", "value": "x", "domain": "example.com", "path": "/", "expires": NOW - 5},
        ],
        "origins": [{"origin": "https://app.example.com", "localStorage": [{"name": "token", "value": "t"}]}],
    }
    jar = parse_session_file(json.dumps(data).encode(), now=NOW)
    assert jar.format == "playwright"
    assert [c.name for c in jar.cookies] == ["sid", "host"]
    assert jar.skipped == 1  # the expired one
    sid, host = jar.cookies
    assert (sid.domain, sid.host_only, sid.expires) == ("example.com", False, FUTURE)
    assert (host.host_only, host.expires) == (True, None)
    assert jar.local_storage == {"https://app.example.com": [("token", "t")]}
    assert jar.domains() == [{"domain": "app.example.com", "cookie_count": 1}, {"domain": "example.com", "cookie_count": 1}]


def test_cookie_editor_export():
    data = [
        {"domain": ".github.com", "hostOnly": False, "name": "user_session", "value": "v", "path": "/", "secure": True, "httpOnly": True, "sameSite": "lax", "expirationDate": FUTURE, "session": False},
        {"domain": "github.com", "hostOnly": True, "name": "__Host-token", "value": "v", "path": "/x", "secure": False, "httpOnly": True, "sameSite": "no_restriction", "session": True},
        {"nonsense": True},
    ]
    jar = parse_session_file(json.dumps(data).encode(), now=NOW)
    assert jar.format == "cookie_editor"
    assert jar.skipped == 1
    session, host = jar.cookies
    assert session.same_site == "Lax" and not session.host_only
    # __Host- forces host-only, path / and secure; SameSite=None forces secure.
    assert (host.host_only, host.path, host.secure, host.same_site, host.expires) == (True, "/", True, "None", None)


def test_netscape_cookies_txt():
    text = (
        "# Netscape HTTP Cookie File\n"
        f".example.com\tTRUE\t/\tTRUE\t{int(FUTURE)}\tsid\tabc\n"
        f"#HttpOnly_shop.example.com\tFALSE\t/cart\tFALSE\t0\tcart\t42\n"
        "broken line\n"
    )
    jar = parse_session_file(text.encode(), now=NOW)
    assert jar.format == "netscape"
    sid, cart = jar.cookies
    assert (sid.domain, sid.host_only, sid.secure) == ("example.com", False, True)
    assert (cart.domain, cart.host_only, cart.http_only, cart.path, cart.expires) == ("shop.example.com", True, True, "/cart", None)
    assert jar.skipped == 1


@pytest.mark.parametrize(
    "content",
    [b"just some text", b"{\"hello\": 1}", b"{not json", b"[]", b"x" * (5 * 1024 * 1024 + 1)],
    ids=["plain-text", "unknown-json", "bad-json", "empty-array", "oversize"],
)
def test_unusable_files_are_refused(content):
    with pytest.raises(CookieFormatError):
        parse_session_file(content, now=NOW)


def test_domain_filter_matches_on_label_boundaries():
    jar = from_cdp_cookies(
        [
            {"name": "a", "value": "1", "domain": ".example.com", "path": "/"},
            {"name": "b", "value": "2", "domain": "badexample.com", "path": "/"},
            {"name": "c", "value": "3", "domain": "mail.example.com", "path": "/"},
        ]
    )
    kept = filter_domains(jar, ["example.com"])
    assert sorted(c.name for c in kept.cookies) == ["a", "c"]


def test_cookie_params_for_storage_set_cookies():
    jar = parse_session_file(
        json.dumps(
            [
                {"domain": ".example.com", "hostOnly": False, "name": "d", "value": "1", "path": "/", "secure": True, "expirationDate": FUTURE},
                {"domain": "example.com", "hostOnly": True, "name": "h", "value": "2", "path": "/app", "secure": True, "session": True},
            ]
        ).encode(),
        now=NOW,
    )
    domain_param, host_param = (to_cookie_param(c) for c in jar.cookies)
    assert domain_param["domain"] == ".example.com" and "url" not in domain_param
    assert domain_param["expires"] == FUTURE
    assert host_param["url"] == "https://example.com/app" and "domain" not in host_param
    assert "expires" not in host_param


def test_summaries_and_exports_never_carry_values_in_summary():
    jar = parse_session_file(json.dumps([{"domain": "a.test", "name": "n", "value": "SECRET"}]).encode(), now=NOW)
    assert "SECRET" not in json.dumps(jar.domains())
    state = to_storage_state(jar)
    assert state["cookies"][0]["value"] == "SECRET"  # the owner-only export keeps it
