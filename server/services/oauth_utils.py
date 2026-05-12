"""Derive OAuth redirect URIs from request context -- no hardcoded ports.

WebSocket handlers call get_redirect_uri(websocket, "google") to build the full
callback URL at runtime.  The callback *path* comes from
:func:`services.ws_handler_registry.get_oauth_callback_path` (plugin-registered
via ``register_oauth_callback_path("<provider>", "/api/<provider>/callback")``
from each plugin's ``__init__.py``); the base URL (scheme + host + port) comes
from the connection itself.

    ws://localhost:3010/ws/status    -> http://localhost:3010/api/google/callback
    wss://flow.zeenie.xyz/ws/status  -> https://flow.zeenie.xyz/api/google/callback
    http://localhost:3010/api/google -> http://localhost:3010/api/google/callback
"""

from urllib.parse import urlparse

from services.ws_handler_registry import get_oauth_callback_path


def get_base_url(connection) -> str:
    """Derive HTTP base URL from a Starlette WebSocket or Request.

    Works with both ``WebSocket.base_url`` and ``Request.base_url``.
    Strips the path and converts ws(s) to http(s).
    """
    raw = str(connection.base_url).rstrip("/")
    parsed = urlparse(raw)

    # Convert ws(s) scheme to http(s)
    scheme = parsed.scheme.replace("ws", "http") if "ws" in parsed.scheme else parsed.scheme

    # netloc includes host:port
    return f"{scheme}://{parsed.netloc}"


def get_redirect_uri(connection, provider: str) -> str:
    """Build full OAuth redirect URI from request context + plugin-registered path.

    Args:
        connection: Starlette ``WebSocket`` or ``Request`` (anything with ``base_url``).
        provider: Lowercase plugin id (``"google"``, ``"twitter"``, ...).

    Returns:
        Full redirect URI, e.g. ``http://localhost:3010/api/google/callback``.
        Falls back to ``/api/<provider>/callback`` if the plugin hasn't
        registered an explicit path (the default for the existing 8+ plugins).
    """
    path = get_oauth_callback_path(provider)
    return get_base_url(connection) + path
