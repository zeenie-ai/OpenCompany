"""Microsoft Graph WebSocket handlers — factory-built.

The 3 handlers (``microsoft_oauth_login`` / ``microsoft_oauth_status`` /
``microsoft_logout``) come from
:func:`services.events.oauth_lifecycle.make_oauth_lifecycle_handlers`.
No ``legacy_status_broadcast`` — Microsoft is a new provider with no
pre-existing frontend frame; the modal reads status via the unified
``credential_catalogue_updated`` envelope + ``config.stored``.

The factory pulls ``microsoft_client_id`` + ``microsoft_client_secret``
from ``auth_service`` inside :func:`_microsoft_oauth_factory` and
constructs a :class:`MicrosoftOAuth` per call.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.events.oauth_lifecycle import make_oauth_lifecycle_handlers

from ._oauth import MicrosoftOAuth


async def _microsoft_oauth_factory(
    *,
    redirect_uri: Optional[str] = None,
    **_kwargs,
) -> MicrosoftOAuth:
    """Build a :class:`MicrosoftOAuth` from stored client credentials."""
    from services.plugin.deps import get_auth_service

    auth_service = get_auth_service()
    client_id = await auth_service.get_api_key("microsoft_client_id") or ""
    client_secret = await auth_service.get_api_key("microsoft_client_secret") or ""
    return MicrosoftOAuth(
        client_id=client_id,
        client_secret=client_secret or None,
        redirect_uri=redirect_uri or "",
    )


def _user_info_to_email(info: Dict[str, Any]) -> str:
    return info.get("email", "Unknown") or "Unknown"


WS_HANDLERS = make_oauth_lifecycle_handlers(
    provider="microsoft",
    oauth_factory=_microsoft_oauth_factory,
    user_info_to_subject=_user_info_to_email,
)


# Module-level aliases so contract tests can import handlers by name.
handle_microsoft_oauth_login = WS_HANDLERS["microsoft_oauth_login"]
handle_microsoft_oauth_status = WS_HANDLERS["microsoft_oauth_status"]
handle_microsoft_logout = WS_HANDLERS["microsoft_logout"]
