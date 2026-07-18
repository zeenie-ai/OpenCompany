"""Cloudflare credential — thin marker (stripe idiom, vercel shape).

Two independent auth paths, either is sufficient:

* **CLI login** — ``cf auth login`` (browser OAuth, loopback callback)
  driven by the ``cloudflare_login`` WS handler; cf stores its own auth
  state in its user-level config, and a synthetic marker OAuth token
  flips the catalogue's ``stored`` flag. The OAuth grant carries a
  FIXED 86-scope set (only ``dns_analytics:read`` for analytics — no
  Web Analytics/RUM or zone-analytics scopes, and no way to request
  more).
* **API token** — the optional ``cloudflare_api_token`` api-key row,
  pasted in the credentials modal and injected as the
  ``CLOUDFLARE_API_TOKEN`` env var on every CLI invocation (token takes
  precedence over CLI login, per cf's documented resolution order).
  This is the only path to endpoints outside the OAuth scope set
  (Web Analytics/RUM, GraphQL Analytics, zone analytics).
"""

from __future__ import annotations

from typing import Any, Dict

import httpx

from services.plugin.credential import Credential, ProbeResult

from ._service import ACCOUNT_TOKEN_PREFIX, GLOBAL_KEY_PREFIX, stored_email

# Cloudflare's official token-validation endpoint ("Verify Token"):
# returns {"success": true, "result": {"id": ..., "status": "active"}}
# for a live user token.
# https://developers.cloudflare.com/fundamentals/api/how-to/verify-token/
_VERIFY_URL = "https://api.cloudflare.com/client/v4/user/tokens/verify"
_ACCOUNTS_URL = "https://api.cloudflare.com/client/v4/accounts"
_USER_URL = "https://api.cloudflare.com/client/v4/user"


class CloudflareCredential(Credential):
    id = "cloudflare"
    display_name = "Cloudflare"
    category = "Deployment"
    auth = "custom"
    docs_url = "https://developers.cloudflare.com"

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        """Return the optional credential rows: the ``apiKey`` field
        (an API token OR a cfk_ Global API Key, stored under the
        provider id) plus the companion account email. There is no
        api_key for the CLI-login path — that state lives in cf's own
        config."""
        from services.plugin.deps import get_auth_service

        auth = get_auth_service()
        secrets: Dict[str, Any] = {}
        token = await auth.get_api_key(cls.id)
        if token:
            secrets["cloudflare_api_token"] = token
        email = await stored_email()
        if email:
            secrets["cloudflare_email"] = email
        return secrets

    @classmethod
    async def _probe(cls, api_key: str) -> ProbeResult:
        """Validate the credential, routing by Cloudflare's documented
        prefix (Global API Key vs account token vs user token). The
        base ``Credential.validate`` scaffold does the rest (store
        under ``cls.id`` on success, broadcast, envelope); httpx errors
        propagate for its typed classification. Reads the stored
        companion email for cfk_ keys — a read, not a side effect."""
        if api_key.startswith(GLOBAL_KEY_PREFIX):
            email = await stored_email()
            if not email:
                return ProbeResult(
                    valid=False,
                    message=(
                        "Global API Keys (cfk_...) authenticate with your account email — "
                        "fill the Account Email field below, Save Credentials, then validate "
                        "again. (Scoped API tokens from dash.cloudflare.com/profile/api-tokens "
                        "work without it.)"
                    ),
                )
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(_USER_URL, headers={"X-Auth-Email": email, "X-Auth-Key": api_key})
            resp.raise_for_status()
            if resp.json().get("success"):
                return ProbeResult(valid=True, message=f"Global API Key is valid for {email}")
            return ProbeResult(valid=False, message="Cloudflare rejected the Global API Key + email pair")

        if api_key.startswith(ACCOUNT_TOKEN_PREFIX):
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    _ACCOUNTS_URL,
                    params={"per_page": 1},
                    headers={"Authorization": f"Bearer {api_key}"},
                )
            resp.raise_for_status()
            if resp.json().get("success"):
                return ProbeResult(valid=True, message="Cloudflare account API token is valid")
            return ProbeResult(valid=False, message="Cloudflare rejected the account API token")

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(_VERIFY_URL, headers={"Authorization": f"Bearer {api_key}"})
        resp.raise_for_status()
        body = resp.json()
        status = str((body.get("result") or {}).get("status") or "").lower()
        if body.get("success") and status == "active":
            return ProbeResult(valid=True, message="Cloudflare API token is valid and active")
        return ProbeResult(
            valid=False,
            message=f"Cloudflare reports the token as '{status or 'unknown'}' — check it at dash.cloudflare.com/profile/api-tokens",
        )
