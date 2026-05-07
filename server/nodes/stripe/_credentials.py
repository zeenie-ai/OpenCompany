"""Stripe credential — thin marker.

The Stripe CLI manages its own auth state at
``~/.config/stripe/config.toml`` (populated by ``stripe login`` and
cleared by ``stripe logout``). MachinaOs doesn't store an API key
itself — only the captured webhook signing secret rides along as an
extra field so :class:`StripeWebhookSource` can verify
``Stripe-Signature`` on incoming events.
"""

from __future__ import annotations

from typing import Any, Dict

from services.plugin.credential import Credential


class StripeCredential(Credential):
    id = "stripe"
    display_name = "Stripe"
    category = "Payments"
    icon = "asset:stripe"
    auth = "custom"
    docs_url = "https://stripe.com/docs/cli"

    @classmethod
    async def resolve(cls, *, user_id: str = "owner") -> Dict[str, Any]:
        """Return only the captured webhook signing secret. There is
        no api_key — auth lives in the Stripe CLI's config file."""
        from services.plugin.deps import get_auth_service

        secret = await get_auth_service().get_api_key("stripe_webhook_secret")
        return {"stripe_webhook_secret": secret} if secret else {}
