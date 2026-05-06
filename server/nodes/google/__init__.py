"""Plugins for the 'google' palette group.

Self-contained plugin folder (Wave 11.H pattern). One folder owns
the entire Google Workspace surface:

- 6 service plugins — ``gmail.py``, ``calendar.py``, ``drive.py``,
  ``sheets.py``, ``tasks.py``, ``contacts.py``, plus
  ``gmail_receive.py`` for the polling trigger.
- ``_credentials.py`` — :class:`GoogleCredential` (OAuth2). The
  6 service plugins all reference this single credential class.
- ``_oauth.py`` — OAuth 2.0 client (formerly
  ``services/google_oauth.py``).
- ``_auth_helper.py`` — shared credential builder + proactive
  refresh (formerly ``services/handlers/google_auth.py``). Used by
  every Workspace plugin to obtain a refresh-aware ``Credentials``
  object.
- ``_handlers.py`` — 3 WebSocket handlers
  (``google_oauth_login`` / ``google_oauth_status`` /
  ``google_logout``).
- ``_router.py`` — HTTP OAuth callback (``/api/google/callback``).

Two self-registration calls below — the central WS dispatcher and
the FastAPI app pick up the plugin's surface without ever importing
this module by name.
"""

from services.status_broadcaster import register_service_refresh
from services.ws_handler_registry import register_router, register_ws_handlers

from . import _router
from ._handlers import WS_HANDLERS
from ._refresh import refresh_google_status

register_ws_handlers(WS_HANDLERS)
register_router(_router.router, name="google")
register_service_refresh(refresh_google_status)
