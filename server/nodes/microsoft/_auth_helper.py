"""Shared Microsoft Graph token helper.

Microsoft Graph access tokens expire in ~1 hour, and
``AuthService.get_oauth_tokens()`` returns the STORED (possibly expired)
access token without refreshing — the generic :class:`Connection` facade
only retries once on 401/403 with the *same* token, so a stale token
would fail twice. This helper proactively refreshes and persists the
access token before a node makes Graph calls (mirroring
``nodes.google._auth_helper.get_google_credentials``).

The AuthService storage layer does not plumb ``token_expiry``, so we
track expiry in-process (``_TOKEN_EXPIRY``). On an unknown / expired
entry (e.g. the first call after a server restart) we refresh once —
a cheap, safe over-refresh — then rely on the cached expiry to skip
refresh on subsequent calls within the token lifetime.
"""

from __future__ import annotations

import time
from typing import Dict, Optional

from core.logging import get_logger

logger = get_logger(__name__)

# user_id -> epoch seconds at which the currently-stored access token expires.
_TOKEN_EXPIRY: Dict[str, float] = {}

# Refresh this many seconds before actual expiry to avoid edge-of-expiry races.
_EXPIRY_MARGIN_SECONDS = 120


def _needs_refresh(user_id: str) -> bool:
    expiry = _TOKEN_EXPIRY.get(user_id)
    if expiry is None:
        return True  # unknown (fresh process / never refreshed here)
    return time.time() >= (expiry - _EXPIRY_MARGIN_SECONDS)


async def ensure_fresh_microsoft_token(user_id: str = "owner") -> str:
    """Guarantee the stored Microsoft access token is fresh; return it.

    Raises:
        PermissionError: if Microsoft Graph is not connected (no tokens),
            annotated so ``BaseNode.execute`` surfaces the provider and
            emits a ``credential.oauth.runtime_failed`` event.
    """
    from services.plugin.deps import get_auth_service

    auth_service = get_auth_service()
    tokens = await auth_service.get_oauth_tokens("microsoft", customer_id=user_id)
    if not tokens or not tokens.get("access_token"):
        err = PermissionError("Microsoft Graph not connected. Connect via the Credentials modal.")
        err.provider = "microsoft"
        err.reason = "missing"
        err.auth = "oauth2"
        raise err

    if not _needs_refresh(user_id):
        return tokens["access_token"]

    refreshed = await _refresh_and_persist(user_id, tokens)
    return refreshed or tokens["access_token"]


async def _refresh_and_persist(user_id: str, current_tokens: Dict) -> Optional[str]:
    """Attempt a token refresh; persist the new access + rotated refresh.

    Returns the new access token, or ``None`` if refresh was not possible
    (no refresh token / no client credentials / upstream failure) — the
    caller falls back to the existing (stored) access token in that case.
    """
    from services.plugin.deps import get_auth_service

    from ._oauth import MicrosoftOAuth

    auth_service = get_auth_service()

    refresh_token = await auth_service.get_oauth_refresh_token("microsoft", customer_id=user_id)
    if not refresh_token:
        return None

    client_id = await auth_service.get_api_key("microsoft_client_id") or ""
    client_secret = await auth_service.get_api_key("microsoft_client_secret") or ""
    if not client_id:
        return None

    oauth = MicrosoftOAuth(client_id=client_id, redirect_uri="", client_secret=client_secret or None)
    result = await oauth.refresh_access_token(refresh_token)
    if not result.get("success") or not result.get("access_token"):
        logger.debug(f"[microsoft] token refresh failed: {result.get('error')}")
        return None

    new_access = result["access_token"]
    new_refresh = result.get("refresh_token") or refresh_token
    await auth_service.store_oauth_tokens(
        provider="microsoft",
        access_token=new_access,
        refresh_token=new_refresh,
        email=current_tokens.get("email"),
        name=current_tokens.get("name"),
        scopes=current_tokens.get("scopes"),
        customer_id=user_id,
    )

    expires_in = result.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        _TOKEN_EXPIRY[user_id] = time.time() + float(expires_in)
    else:
        # Microsoft access tokens default to ~3600s; assume that.
        _TOKEN_EXPIRY[user_id] = time.time() + 3600.0

    logger.debug("[microsoft] refreshed and persisted access token")
    return new_access
