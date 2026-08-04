"""Microsoft Graph OAuth 2.0 client (Work/School accounts).

Subclasses :class:`services.plugin.oauth.OAuth2PKCEClient` (the Twitter
pattern) — the base owns the PKCE state store, code-verifier generation,
code exchange, token refresh, and revocation. This file declares the
Microsoft-specific endpoints, scopes, ``fetch_user_info`` translation,
and one override: the Microsoft identity platform token endpoint expects
``client_id`` + ``client_secret`` as form-body parameters, NOT HTTP Basic
auth (which the base uses by default for confidential clients).

Endpoints + scopes are read from ``server/config/microsoft_apis.json``
(authority ``/organizations`` — organizational Work/School accounts only).
A hardcoded fallback keeps the module importable if the config is absent.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar, Dict, List, Optional, Tuple

import httpx

from core.logging import get_logger
from services.plugin.oauth import OAuth2PKCEClient, OAuthStateStore

logger = get_logger(__name__)

# Microsoft Graph user-info endpoint (the profile of the signed-in user).
USER_INFO_URL = "https://graph.microsoft.com/v1.0/me"

# Hardcoded fallbacks (authority /organizations). Overridden by config.
_DEFAULT_AUTHORIZATION_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
_DEFAULT_TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"

_DEFAULT_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",  # enables refresh tokens
    "User.Read",
    "Mail.Send",
    "Mail.ReadWrite",
    "Calendars.ReadWrite",
]


def _config_path() -> Path:
    # server/nodes/microsoft/_oauth.py -> parents[2] == server/
    return Path(__file__).resolve().parents[2] / "config" / "microsoft_apis.json"


def _load_config() -> Dict[str, Any]:
    try:
        with _config_path().open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug(f"[microsoft] microsoft_apis.json unavailable, using defaults: {exc}")
        return {}


def get_oauth_endpoints() -> Dict[str, str]:
    """Return ``{auth_uri, token_uri}`` from config (or defaults)."""
    oauth = _load_config().get("oauth", {})
    return {
        "auth_uri": oauth.get("auth_uri", _DEFAULT_AUTHORIZATION_URL),
        "token_uri": oauth.get("token_uri", _DEFAULT_TOKEN_URL),
    }


def get_all_scopes() -> List[str]:
    """Flatten every scope block in config into one deduplicated list."""
    scopes_cfg = _load_config().get("scopes")
    if not scopes_cfg:
        return list(_DEFAULT_SCOPES)
    seen: Dict[str, None] = {}
    for block in scopes_cfg.values():
        for scope in block:
            seen.setdefault(scope, None)
    return list(seen.keys()) if seen else list(_DEFAULT_SCOPES)


# Single source of truth for the credential scope union.
MICROSOFT_GRAPH_SCOPES: List[str] = get_all_scopes()

_ENDPOINTS = get_oauth_endpoints()


class MicrosoftOAuth(OAuth2PKCEClient):
    """Microsoft Graph OAuth 2.0 client (authorization-code + PKCE)."""

    provider = "microsoft"
    authorization_endpoint = _ENDPOINTS["auth_uri"]
    token_endpoint = _ENDPOINTS["token_uri"]
    # Microsoft has no OAuth token-revocation endpoint; logout clears the
    # stored tokens locally. revoke_token() is a no-op (base handles the
    # empty ``revocation_endpoint`` by returning {"success": True, "skipped"}).
    revocation_endpoint = ""

    # Plugin-scoped state store — isolated from Google's / Twitter's.
    state_store = OAuthStateStore()

    DEFAULT_SCOPES: ClassVar[List[str]] = MICROSOFT_GRAPH_SCOPES

    def __init__(
        self,
        client_id: str,
        redirect_uri: str,
        client_secret: Optional[str] = None,
        scopes: Optional[List[str]] = None,
    ) -> None:
        super().__init__(
            client_id=client_id,
            redirect_uri=redirect_uri,
            client_secret=client_secret,
            scopes=scopes,
        )

    def _token_request_auth(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """Microsoft identity platform expects the client credentials in
        the request BODY, not HTTP Basic auth. Override the base (which
        would send ``Authorization: Basic``) to put ``client_id`` +
        ``client_secret`` in the form body — the documented shape for the
        v2.0 token endpoint.

        See: https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow#redeem-a-code-for-an-access-token
        """
        body = {"client_id": self.client_id}
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return body, {}

    async def fetch_user_info(self, access_token: str) -> Dict[str, Any]:
        """Translate Graph ``/me`` into the unified user-info shape.

        Work/School accounts populate ``mail``; when null (some org
        mailboxes) fall back to ``userPrincipalName``.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    USER_INFO_URL,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            logger.error(f"[microsoft] HTTP error getting user info: {exc}")
            return {"success": False, "error": str(exc)}

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            err = error_data.get("error", {})
            message = err.get("message") if isinstance(err, dict) else str(err)
            return {"success": False, "error": message or "Failed to get user info"}

        user = response.json()
        email = user.get("mail") or user.get("userPrincipalName") or "Unknown"
        return {
            "success": True,
            "id": user.get("id"),
            "email": email,
            "name": user.get("displayName", ""),
        }


__all__ = [
    "MicrosoftOAuth",
    "MICROSOFT_GRAPH_SCOPES",
    "USER_INFO_URL",
    "get_oauth_endpoints",
    "get_all_scopes",
]
