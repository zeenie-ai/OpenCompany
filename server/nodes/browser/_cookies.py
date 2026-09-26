"""Parse and apply saved browser logins. Pure functions, no I/O.

Three upload formats, plus the cookies Chrome itself reports:

- **Playwright storageState** JSON: ``{"cookies": [...], "origins": [{"origin",
  "localStorage": [{"name", "value"}]}]}``;
- **Cookie-Editor** JSON (the browser extension's export): an array of
  ``{domain, hostOnly, name, value, path, secure, httpOnly, sameSite,
  expirationDate, session}``;
- **Netscape cookies.txt**: tab-separated lines, ``#HttpOnly_`` prefix for
  HTTP-only cookies;
- **CDP** ``Network.Cookie`` objects, from ``Storage.getCookies`` on the
  user's own Chrome during an import.

Everything becomes :class:`NormalizedCookie`, and leaves as CDP
``CookieParam`` for ``Storage.setCookies`` on a profile's Chrome. A cookie
value never appears in a summary, a log line or a response: summaries carry
domains and counts only.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_COOKIES = 5000

_SAME_SITE = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}


class CookieFormatError(ValueError):
    """The upload is not a session file this parser understands."""


@dataclass(frozen=True)
class NormalizedCookie:
    name: str
    value: str
    domain: str  # without a leading dot
    host_only: bool
    path: str = "/"
    secure: bool = False
    http_only: bool = False
    same_site: Optional[str] = None  # Strict | Lax | None
    expires: Optional[float] = None  # unix seconds; None = session cookie


@dataclass
class CookieJar:
    format: str
    cookies: List[NormalizedCookie] = field(default_factory=list)
    #: origin -> [(name, value)] for localStorage (Playwright storageState only).
    local_storage: Dict[str, List[Tuple[str, str]]] = field(default_factory=dict)
    skipped: int = 0

    def domains(self) -> List[Dict[str, Any]]:
        """``[{"domain", "cookie_count"}]``, sorted, never any value."""
        counts: Dict[str, int] = {}
        for cookie in self.cookies:
            counts[cookie.domain] = counts.get(cookie.domain, 0) + 1
        for origin, items in self.local_storage.items():
            host = urlsplit(origin).hostname or origin
            counts.setdefault(host, 0)
        return [{"domain": d, "cookie_count": counts[d]} for d in sorted(counts)]


def _clean_domain(value: Any) -> str:
    return str(value or "").strip().lower().lstrip(".").rstrip(".")


def _expires(value: Any, *, session: bool = False) -> Optional[float]:
    if session or value in (None, "", -1, "-1"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return None
    # Some exporters write milliseconds.
    if number > 1e11:
        number /= 1000.0
    return number


def _normalize_same_site(value: Any) -> Optional[str]:
    return _SAME_SITE.get(str(value or "").strip().lower())


def _finish(cookie: NormalizedCookie, now: float) -> Optional[NormalizedCookie]:
    """Apply the cookie-prefix and SameSite rules; drop what cannot be set."""
    if not cookie.name or not cookie.domain:
        return None
    if cookie.expires is not None and cookie.expires < now:
        return None
    if cookie.name.startswith("__Host-"):
        cookie = replace(cookie, host_only=True, path="/", secure=True)
    elif cookie.name.startswith("__Secure-"):
        cookie = replace(cookie, secure=True)
    if cookie.same_site == "None" and not cookie.secure:
        cookie = replace(cookie, secure=True)
    return cookie


def parse_playwright(data: Dict[str, Any]) -> CookieJar:
    jar = CookieJar(format="playwright")
    for raw in data.get("cookies") or []:
        if not isinstance(raw, dict):
            jar.skipped += 1
            continue
        domain_raw = str(raw.get("domain") or "")
        jar.cookies.append(
            NormalizedCookie(
                name=str(raw.get("name") or ""),
                value=str(raw.get("value") or ""),
                domain=_clean_domain(domain_raw),
                host_only=not domain_raw.startswith("."),
                path=str(raw.get("path") or "/"),
                secure=bool(raw.get("secure")),
                http_only=bool(raw.get("httpOnly")),
                same_site=_normalize_same_site(raw.get("sameSite")),
                expires=_expires(raw.get("expires")),
            )
        )
    for origin in data.get("origins") or []:
        if not isinstance(origin, dict):
            continue
        url = str(origin.get("origin") or "")
        if urlsplit(url).scheme not in ("http", "https"):
            continue
        items = [
            (str(item.get("name")), str(item.get("value") or ""))
            for item in origin.get("localStorage") or []
            if isinstance(item, dict) and item.get("name") is not None
        ]
        if items:
            jar.local_storage[url.rstrip("/")] = items
    return jar


def parse_cookie_editor(data: List[Any]) -> CookieJar:
    jar = CookieJar(format="cookie_editor")
    for raw in data:
        if not isinstance(raw, dict) or "name" not in raw or "domain" not in raw:
            jar.skipped += 1
            continue
        domain_raw = str(raw.get("domain") or "")
        host_only = bool(raw.get("hostOnly")) if "hostOnly" in raw else not domain_raw.startswith(".")
        jar.cookies.append(
            NormalizedCookie(
                name=str(raw.get("name") or ""),
                value=str(raw.get("value") or ""),
                domain=_clean_domain(domain_raw),
                host_only=host_only,
                path=str(raw.get("path") or "/"),
                secure=bool(raw.get("secure")),
                http_only=bool(raw.get("httpOnly")),
                same_site=_normalize_same_site(raw.get("sameSite")),
                expires=_expires(raw.get("expirationDate"), session=bool(raw.get("session"))),
            )
        )
    return jar


def parse_netscape(text: str) -> CookieJar:
    jar = CookieJar(format="netscape")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        http_only = False
        if stripped.startswith("#HttpOnly_"):
            http_only = True
            stripped = stripped[len("#HttpOnly_") :]
        elif stripped.startswith("#"):
            continue
        fields = stripped.split("\t")
        if len(fields) != 7:
            jar.skipped += 1
            continue
        domain_raw, include_subdomains, path, secure, expiry, name, value = fields
        jar.cookies.append(
            NormalizedCookie(
                name=name,
                value=value,
                domain=_clean_domain(domain_raw),
                host_only=include_subdomains.upper() != "TRUE" and not domain_raw.startswith("."),
                path=path or "/",
                secure=secure.upper() == "TRUE",
                http_only=http_only,
                expires=_expires(expiry),
            )
        )
    return jar


def from_cdp_cookies(cookies: Iterable[Dict[str, Any]]) -> CookieJar:
    """Cookies as ``Storage.getCookies`` reports them."""
    jar = CookieJar(format="chrome")
    for raw in cookies:
        domain_raw = str(raw.get("domain") or "")
        jar.cookies.append(
            NormalizedCookie(
                name=str(raw.get("name") or ""),
                value=str(raw.get("value") or ""),
                domain=_clean_domain(domain_raw),
                host_only=not domain_raw.startswith("."),
                path=str(raw.get("path") or "/"),
                secure=bool(raw.get("secure")),
                http_only=bool(raw.get("httpOnly")),
                same_site=_normalize_same_site(raw.get("sameSite")),
                expires=_expires(raw.get("expires"), session=bool(raw.get("session"))),
            )
        )
    return jar


def parse_session_file(content: bytes, *, now: Optional[float] = None) -> CookieJar:
    """Detect the format of an uploaded session file and parse it."""
    if len(content) > MAX_UPLOAD_BYTES:
        raise CookieFormatError(f"the file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    text = content.decode("utf-8-sig", errors="replace")
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise CookieFormatError("the file looks like JSON but does not parse") from exc
        if isinstance(data, dict) and ("cookies" in data or "origins" in data):
            jar = parse_playwright(data)
        elif isinstance(data, list):
            jar = parse_cookie_editor(data)
        else:
            raise CookieFormatError("JSON session files must be a Playwright storageState object or a Cookie-Editor array")
    elif "\t" in text:
        jar = parse_netscape(text)
    else:
        raise CookieFormatError("unrecognized format; upload Playwright storageState JSON, Cookie-Editor JSON or cookies.txt")
    return finalize(jar, now=now)


def finalize(jar: CookieJar, *, now: Optional[float] = None) -> CookieJar:
    now = time.time() if now is None else now
    kept: List[NormalizedCookie] = []
    for cookie in jar.cookies:
        finished = _finish(cookie, now)
        if finished is None:
            jar.skipped += 1
        else:
            kept.append(finished)
    if len(kept) > MAX_COOKIES:
        jar.skipped += len(kept) - MAX_COOKIES
        kept = kept[:MAX_COOKIES]
    jar.cookies = kept
    if not jar.cookies and not jar.local_storage:
        raise CookieFormatError("the file holds no cookies that can be imported")
    return jar


def _domain_matches(domain: str, chosen: Iterable[str]) -> bool:
    return any(domain == c or domain.endswith("." + c) for c in chosen)


def filter_domains(jar: CookieJar, domains: Iterable[str]) -> CookieJar:
    """Keep only the chosen sites (a domain keeps its subdomains)."""
    chosen = [_clean_domain(d) for d in domains if _clean_domain(d)]
    return CookieJar(
        format=jar.format,
        cookies=[c for c in jar.cookies if _domain_matches(c.domain, chosen)],
        local_storage={o: items for o, items in jar.local_storage.items() if _domain_matches(urlsplit(o).hostname or "", chosen)},
        skipped=jar.skipped,
    )


def to_cookie_param(cookie: NormalizedCookie) -> Dict[str, Any]:
    """CDP ``Network.CookieParam`` for ``Storage.setCookies``.

    A host-only cookie is set by ``url`` (no ``domain``), which is also the
    only way ``__Host-`` cookies are accepted.
    """
    param: Dict[str, Any] = {
        "name": cookie.name,
        "value": cookie.value,
        "path": cookie.path or "/",
        "secure": cookie.secure,
        "httpOnly": cookie.http_only,
    }
    if cookie.host_only:
        scheme = "https" if cookie.secure else "http"
        param["url"] = f"{scheme}://{cookie.domain}{cookie.path or '/'}"
    else:
        param["domain"] = "." + cookie.domain
    if cookie.same_site:
        param["sameSite"] = cookie.same_site
    if cookie.expires is not None:
        param["expires"] = cookie.expires
    return param


def to_storage_state(jar: CookieJar) -> Dict[str, Any]:
    """Playwright storageState, for exporting a profile's logins."""
    cookies = []
    for c in jar.cookies:
        cookies.append(
            {
                "name": c.name,
                "value": c.value,
                "domain": c.domain if c.host_only else "." + c.domain,
                "path": c.path,
                "expires": c.expires if c.expires is not None else -1,
                "httpOnly": c.http_only,
                "secure": c.secure,
                "sameSite": c.same_site or "Lax",
            }
        )
    origins = [
        {"origin": origin, "localStorage": [{"name": n, "value": v} for n, v in items]} for origin, items in jar.local_storage.items()
    ]
    return {"cookies": cookies, "origins": origins}


__all__ = [
    "CookieFormatError",
    "CookieJar",
    "MAX_COOKIES",
    "MAX_UPLOAD_BYTES",
    "NormalizedCookie",
    "filter_domains",
    "finalize",
    "from_cdp_cookies",
    "parse_cookie_editor",
    "parse_netscape",
    "parse_playwright",
    "parse_session_file",
    "to_cookie_param",
    "to_storage_state",
]
