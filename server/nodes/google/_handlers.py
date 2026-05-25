"""Google Workspace WebSocket handlers — factory-built (Wave 11.I, S.2).

The 3 handlers (``google_oauth_login`` / ``google_oauth_status`` /
``google_logout``) come from
:func:`services.events.oauth_lifecycle.make_oauth_lifecycle_handlers`.
``legacy_status_broadcast="google_status"`` keeps the pre-S frontend
listeners happy by emitting the legacy wire-frame alongside the
unified credential-event broadcast.

The factory pulls ``google_client_id`` + ``google_client_secret`` from
``auth_service`` inside the async :func:`_google_oauth_factory` helper
and constructs a :class:`GoogleOAuth` per call.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.events.oauth_lifecycle import make_oauth_lifecycle_handlers

from ._oauth import GoogleOAuth


async def _google_oauth_factory(
    *,
    redirect_uri: Optional[str] = None,
    **_kwargs,
) -> GoogleOAuth:
    """Build a :class:`GoogleOAuth` from stored client credentials."""
    from services.plugin.deps import get_auth_service

    auth_service = get_auth_service()
    client_id = await auth_service.get_api_key("google_client_id") or ""
    client_secret = await auth_service.get_api_key("google_client_secret") or ""
    return GoogleOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri or "",
    )


def _user_info_to_email(info: Dict[str, Any]) -> str:
    return info.get("email", "Unknown") or "Unknown"


WS_HANDLERS = make_oauth_lifecycle_handlers(
    provider="google",
    oauth_factory=_google_oauth_factory,
    user_info_to_subject=_user_info_to_email,
    legacy_status_broadcast="google_status",
)


# Module-level aliases so the contract tests in
# ``tests/credentials/test_websocket_handlers.py`` can import the
# handlers by name.
handle_google_oauth_login = WS_HANDLERS["google_oauth_login"]
handle_google_oauth_status = WS_HANDLERS["google_oauth_status"]
handle_google_logout = WS_HANDLERS["google_logout"]
