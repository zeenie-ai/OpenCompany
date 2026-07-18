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

from services.plugin.credential import Credential


class CloudflareCredential(Credential):
    id = "cloudflare"
    display_name = "Cloudflare"
    category = "Deployment"
    auth = "custom"
    docs_url = "https://developers.cloudflare.com"

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        """Return the optional API token. There is no api_key for the
        CLI-login path — that state lives in cf's own config."""
        from services.plugin.deps import get_auth_service

        token = await get_auth_service().get_api_key("cloudflare_api_token")
        return {"cloudflare_api_token": token} if token else {}
