"""Plugins for the 'twitter' palette group.

Self-contained plugin folder (Wave 11.H pattern). Owns:

- Action / trigger nodes — ``twitter_send.py``, ``twitter_search.py``,
  ``twitter_user.py``, ``twitter_receive.py`` (auto-registered via
  ``BaseNode.__init_subclass__``).
- ``_credentials.py`` — :class:`TwitterCredential` (OAuth2).
- ``_oauth.py`` — OAuth 2.0 PKCE flow client (formerly
  ``services/twitter_oauth.py``).
- ``_handlers.py`` — 3 WebSocket handlers
  (``twitter_oauth_login`` / ``twitter_oauth_status`` /
  ``twitter_logout``).
- ``_router.py`` — HTTP OAuth callback (``/api/twitter/callback``).

Two self-registration calls below — the central WS dispatcher and the
FastAPI app pick up the plugin's surface without ever importing this
module by name.
"""

from services.status_broadcaster import register_service_refresh
from services.ws_handler_registry import register_router, register_ws_handlers

from . import _router
from ._handlers import WS_HANDLERS
from ._refresh import refresh_twitter_status

# WebSocket handlers (3 message types) and the OAuth-callback HTTP
# router self-register so ``routers/websocket.py`` and ``main.py``
# stay free of per-plugin imports. Plus the service-status refresh
# callback (Wave 11.I, milestone J) -- the broadcaster fans out to
# this on lifespan startup instead of hardcoding the call.
register_ws_handlers(WS_HANDLERS)
register_router(_router.router, name="twitter")
register_service_refresh(refresh_twitter_status)
