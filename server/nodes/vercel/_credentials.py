"""Vercel credential — thin marker (stripe idiom).

Two independent auth paths, either is sufficient:

* **CLI login** — ``vercel login`` (browser device flow) driven by the
  ``vercel_login`` WS handler; the CLI stores its own auth state in the
  OpenCompany-pinned ``--global-config`` dir (``<DATA_DIR>/vercel/``),
  and a synthetic marker OAuth token flips the catalogue's ``stored``
  flag.
* **Access token** — the optional ``vercel_token`` api-key row, pasted
  in the credentials modal and injected as the ``VERCEL_TOKEN`` env var
  on every CLI invocation (token takes precedence over CLI login).
"""

from __future__ import annotations

from typing import Any, Dict

from services.plugin.credential import Credential


class VercelCredential(Credential):
    id = "vercel"
    display_name = "Vercel"
    category = "Deployment"
    auth = "custom"
    docs_url = "https://vercel.com/docs/cli"

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        """Return the optional access token. There is no api_key — CLI
        login state lives in the pinned ``--global-config`` dir."""
        from services.plugin.deps import get_auth_service

        token = await get_auth_service().get_api_key("vercel_token")
        return {"vercel_token": token} if token else {}
