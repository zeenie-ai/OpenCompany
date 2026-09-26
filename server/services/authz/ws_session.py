"""Handshake checks for the app's WebSocket routes.

A browser lets any web page open a WebSocket to any host, ``localhost``
included, and attaches the page's ``Origin`` to the handshake. With login
off (the local default) there is no session cookie to check, and with login
on the cookie still rides along on a same-site request, which covers every
page served from ``localhost`` on another port. So without an ``Origin``
check any page open on the machine, including one the agent's own browser
loaded, could drive the app through its sockets.

Two admission helpers, both run before ``accept()``:

- :func:`authenticate_ws` for the sockets a page of the app opens
  (``/ws/status``, ``/ws/browser``): the ``Origin`` check, then the session
  cookie unless login is disabled. Returns the principal.
- :func:`admit_internal_ws` for the worker socket (``/ws/internal``): the
  ``Origin`` check, then the worker token. No page can set that header.

A handshake without ``Origin`` comes from a non-browser client (the
Temporal worker, a script) and passes the ``Origin`` check.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Optional, Tuple
from urllib.parse import urlsplit

from core.logging import get_logger
from services.authz.ws_surface import is_internal_caller

logger = get_logger(__name__)

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _host_key(netloc: str, scheme: str) -> Optional[Tuple[str, int]]:
    """``(hostname, port)`` of ``netloc``, with the scheme's default port filled in."""
    try:
        parts = urlsplit(f"//{netloc}")
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    return hostname.lower(), port if port is not None else _DEFAULT_PORTS.get(scheme, 0)


def _origin_key(origin: str) -> Optional[Tuple[str, str, int]]:
    """``(scheme, hostname, port)`` of an http(s) origin, else ``None``.

    ``null`` (sandboxed frames, ``file:``), extension and other schemes
    are never a page of the app, so they have no key and are refused.
    """
    try:
        parts = urlsplit(origin.strip())
    except ValueError:
        return None
    scheme = (parts.scheme or "").lower()
    if scheme not in _DEFAULT_PORTS:
        return None
    host = _host_key(parts.netloc, scheme)
    if host is None:
        return None
    return (scheme, *host)


def is_allowed_ws_origin(origin: Optional[str], host: Optional[str], allowed_origins: Iterable[str]) -> bool:
    """Whether a handshake's ``Origin`` belongs to the app.

    Allowed: no ``Origin`` (not a browser); an origin on the same host and
    port as the request's ``Host`` header (the SPA served by this backend,
    or through a proxy that keeps ``Host``, as the Vite dev proxy does); or
    an origin listed in ``CORS_ORIGINS``. ``*`` there allows every origin,
    mirroring what it already means for HTTP CORS.
    """
    if origin is None:
        return True
    key = _origin_key(origin)
    if key is None:
        return False
    allowed = list(allowed_origins or ())
    if "*" in allowed:
        return True
    if any(_origin_key(candidate) == key for candidate in allowed if isinstance(candidate, str)):
        return True
    if host:
        return _host_key(host.strip(), key[0]) == key[1:]
    return False


def _origin_refused(websocket: Any, settings: Any) -> bool:
    headers = websocket.headers
    origin = headers.get("origin")
    if is_allowed_ws_origin(origin, headers.get("host"), getattr(settings, "cors_origins", None) or ()):
        return False
    path = str((getattr(websocket, "scope", None) or {}).get("path") or "")
    logger.warning("[WebSocket] Refused a handshake from a foreign origin", path=path, origin=origin)
    return True


def _auth_disabled(settings: Any) -> bool:
    value = getattr(settings, "vite_auth_enabled", None)
    return bool(value) and str(value).lower() == "false"


async def authenticate_ws(websocket: Any, *, settings: Any, user_auth_service: Callable[[], Any]) -> Optional[str]:
    """Admit a browser-facing socket and return its principal.

    Returns ``None`` after closing the socket: ``4003`` for a foreign
    origin, ``4001`` for a missing or invalid session. With login disabled
    every same-origin caller is the owner.
    """
    if _origin_refused(websocket, settings):
        await websocket.close(code=4003, reason="Origin not allowed")
        return None

    if _auth_disabled(settings):
        from constants import OWNER_PRINCIPAL_ID

        return OWNER_PRINCIPAL_ID

    from core.auth_cookies import get_session_token

    token = get_session_token(websocket.cookies, settings)
    if not token:
        await websocket.close(code=4001, reason="Not authenticated")
        return None

    payload = user_auth_service().verify_token(token)
    if not payload:
        await websocket.close(code=4001, reason="Invalid or expired session")
        return None

    principal = str(payload.get("sub") or "")
    if not principal:
        await websocket.close(code=4001, reason="Invalid session subject")
        return None
    return principal


async def admit_internal_ws(websocket: Any, *, settings: Any) -> bool:
    """Admit the worker socket, or close it and return ``False``."""
    if _origin_refused(websocket, settings):
        await websocket.close(code=4003, reason="Origin not allowed")
        return False
    if not is_internal_caller(websocket.headers, settings.secret_key):
        client = websocket.client.host if getattr(websocket, "client", None) else "unknown"
        logger.warning("[WebSocket Internal] Refused a connection without the worker token", client=client)
        await websocket.close(code=4001, reason="Not authenticated")
        return False
    return True


__all__ = ["admit_internal_ws", "authenticate_ws", "is_allowed_ws_origin"]
