"""Twitter / X WebSocket handlers — factory-built (Wave 11.I, S).

The 3 handlers (``twitter_oauth_login`` / ``twitter_oauth_status`` /
``twitter_logout``) come from
:func:`services.events.oauth_lifecycle.make_oauth_lifecycle_handlers`.
The factory takes care of credential loading, redirect-URI derivation,
silent token refresh on status, revoke + remove + broadcast on
logout. Plugin-specific bits supplied via kwargs:

* ``oauth_factory`` -- async builder that constructs
  :class:`TwitterOAuth` per call, pulling stored client_id /
  client_secret from ``auth_service``.
* ``user_info_to_subject`` -- ``f"@{username}"`` (X has no email).
* ``extra_logout`` -- drops legacy API-key entries left behind by the
  pre-OAuth layout.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from services.events.oauth_lifecycle import make_oauth_lifecycle_handlers

from ._oauth import TwitterOAuth


async def _twitter_oauth_factory(
    *, redirect_uri: Optional[str] = None, **_kwargs,
) -> TwitterOAuth:
    """Build a :class:`TwitterOAuth` from stored client credentials."""
    from services.plugin.deps import get_auth_service

    auth_service = get_auth_service()
    client_id = await auth_service.get_api_key("twitter_client_id") or ""
    client_secret = await auth_service.get_api_key("twitter_client_secret")
    return TwitterOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri or "",
    )


def _user_info_to_handle(info: Dict[str, Any]) -> str:
    return f"@{info.get('username', 'unknown')}"


async def _drop_legacy_api_key_entries() -> None:
    """Remove stale API-key entries from the pre-OAuth layout.

    Pre-Wave-11.I some flows mistakenly stored access / refresh tokens
    as API keys. The OAuth tokens table is the canonical home now;
    these orphans are cleaned on every logout for safety.
    """
    from services.plugin.deps import get_auth_service

    auth_service = get_auth_service()
    for key in ("twitter_access_token", "twitter_refresh_token", "twitter_user_info"):
        try:
            await auth_service.remove_api_key(key)
        except Exception:
            pass


WS_HANDLERS = make_oauth_lifecycle_handlers(
    provider="twitter",
    oauth_factory=_twitter_oauth_factory,
    user_info_to_subject=_user_info_to_handle,
    extra_logout=_drop_legacy_api_key_entries,
)

# Module-level aliases so the contract tests in
# ``tests/credentials/test_websocket_handlers.py`` can import the
# handlers by name. The dispatch table itself is what
# ``register_ws_handlers`` consumes.
handle_twitter_oauth_login = WS_HANDLERS["twitter_oauth_login"]
handle_twitter_oauth_status = WS_HANDLERS["twitter_oauth_status"]
handle_twitter_logout = WS_HANDLERS["twitter_logout"]
