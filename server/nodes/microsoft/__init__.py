"""Plugins for the 'microsoft' palette group (Microsoft Graph).

Self-contained plugin folder (Wave 11.H pattern). One folder owns the
Microsoft Graph Email + Calendar surface:

- 2 action plugins — ``mail/`` (``msMail``) and ``calendar/`` (``msCalendar``),
  both usable as AI tools.
- ``_credentials.py`` — :class:`MicrosoftCredential` (OAuth2). Both plugins
  reference this single credential class.
- ``_oauth.py`` — :class:`MicrosoftOAuth` (OAuth2 auth-code + PKCE,
  authority ``/organizations`` — Work/School accounts only).
- ``_auth_helper.py`` — proactive access-token refresh (Graph tokens
  expire ~1h and ``get_oauth_tokens`` does not refresh).
- ``_base.py`` — ``graph_request`` helper + usage tracking.
- ``_handlers.py`` — 3 WebSocket handlers
  (``microsoft_oauth_login`` / ``microsoft_oauth_status`` / ``microsoft_logout``).
- ``_router.py`` — HTTP OAuth callback (``/api/microsoft/callback``).

Self-registration below — the central WS dispatcher, the FastAPI app,
and the status broadcaster pick up the plugin's surface without ever
importing this module by name.
"""

from services.deployment.canary_registry import register_canary_trigger_type
from services.status_broadcaster import register_service_refresh
from services.ws_handler_registry import (
    register_oauth_callback_path,
    register_router,
    register_ws_handlers,
)

from . import _router
from ._handlers import WS_HANDLERS
from ._refresh import refresh_microsoft_status

register_ws_handlers(WS_HANDLERS)
register_router(_router.router, name="microsoft")
register_oauth_callback_path("microsoft", "/api/microsoft/callback")
register_service_refresh(refresh_microsoft_status)

# Opt msMailReceive into the PollingTriggerWorkflow consumer path. The
# per-cycle Temporal activity is emitted by MailReceiveNode.as_poll_activity()
# (auto-collected by the worker); the CloudEvents type must match the
# producer so DeploymentManager's EventType Search Attribute lines up.
register_canary_trigger_type("msMailReceive", "com.opencompany.msmail.message.received")
