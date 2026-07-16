"""Cloudflare credential — thin marker (Stripe idiom, github shape).

The cf CLI manages its own auth state: ``cf auth login`` (driven by the
modal's Login button or run by the user in a terminal) stores the OAuth
token in cf's user-level config (``auth.jsonc``, or the OS keyring when
``CLOUDFLARE_AUTH_USE_KEYRING`` is set) and ``cf auth logout`` revokes
it. OpenCompany stores no token — the credentials modal's connected
badge is driven by the synthetic ``cli-managed`` marker OAuth row
written by ``_handlers.py`` after a successful login.
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
        """Nothing to resolve — auth lives in the cf CLI's own config
        (or an ambient ``CLOUDFLARE_API_TOKEN`` env var, per cf's own
        documented precedence)."""
        return {}
