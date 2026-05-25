"""Twitter / X OAuth 2.0 PKCE client.

Wave 11.I, milestone S: subclasses :class:`OAuth2PKCEClient` from
:mod:`services.plugin.oauth`. The base class owns the PKCE state
store, code-verifier generation, code exchange, token refresh, and
revocation. This file declares X-specific endpoints + scopes +
``fetch_user_info`` translation.

Pre-S the file hand-rolled all of that (~410 LOC). The only Twitter-
specific behaviour now lives in this small subclass.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx

from core.logging import get_logger
from services.plugin.oauth import OAuth2PKCEClient, OAuthStateStore

logger = get_logger(__name__)

# X API OAuth 2.0 endpoints. Updated URLs per latest docs. Public so
# the contract tests in tests/credentials/test_twitter_oauth.py can
# pin them via respx mocks.
AUTHORIZATION_URL = "https://x.com/i/oauth2/authorize"
TOKEN_URL = "https://api.x.com/2/oauth2/token"
REVOKE_URL = "https://api.x.com/2/oauth2/revoke"
USER_INFO_URL = "https://api.x.com/2/users/me"

# Required scopes for full Twitter integration.
# See: https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code
_DEFAULT_SCOPES = [
    "tweet.read",
    "tweet.write",
    "users.read",
    "follows.read",
    "like.read",
    "like.write",
    "offline.access",  # enables refresh tokens
]


class TwitterOAuth(OAuth2PKCEClient):
    """X (Twitter) OAuth 2.0 PKCE client."""

    provider = "twitter"
    authorization_endpoint = AUTHORIZATION_URL
    token_endpoint = TOKEN_URL
    revocation_endpoint = REVOKE_URL

    # Plugin-scoped state store -- isolated from Google's instance.
    state_store = OAuthStateStore()

    DEFAULT_SCOPES = _DEFAULT_SCOPES

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

    # Back-compat alias for the contract tests in
    # tests/credentials/test_twitter_oauth.py -- the unified protocol
    # method is :meth:`fetch_user_info`.
    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        return await self.fetch_user_info(access_token)

    async def fetch_user_info(self, access_token: str) -> Dict[str, Any]:
        """Translate X's ``/users/me`` response into the unified shape."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    USER_INFO_URL,
                    params={
                        "user.fields": "id,name,username,profile_image_url,verified",
                    },
                    headers={"Authorization": f"Bearer {access_token}"},
                )
        except httpx.HTTPError as exc:
            logger.error(f"[twitter] HTTP error getting user info: {exc}")
            return {"success": False, "error": str(exc)}

        if response.status_code != 200:
            error_data = response.json() if response.text else {}
            return {
                "success": False,
                "error": error_data.get("detail") or error_data.get("title", "Failed to get user info"),
            }

        user = response.json().get("data", {})
        return {
            "success": True,
            "id": user.get("id"),
            "username": user.get("username"),
            "name": user.get("name"),
            "profile_image_url": user.get("profile_image_url"),
            "verified": user.get("verified", False),
        }


# Module-level alias for the contract tests' ``_oauth_states.clear()``
# pattern -- this is the same dict the class-level state store wraps,
# so clearing one clears the other.
_oauth_states = TwitterOAuth.state_store._states


__all__ = [
    "TwitterOAuth",
    "AUTHORIZATION_URL",
    "TOKEN_URL",
    "REVOKE_URL",
    "USER_INFO_URL",
]
