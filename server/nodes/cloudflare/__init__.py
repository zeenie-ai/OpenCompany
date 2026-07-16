"""Plugin for the 'deployment' palette group — Cloudflare via the
official cf CLI.

Self-contained CLI-managed-auth plugin (Stripe/gh pattern): the cf CLI
owns its auth end-to-end — ``cf auth login`` (PKCE OAuth with a
loopback callback on localhost:8877) driven from the credentials modal
(or the user's own terminal), token in cf's user-level config, a
synthetic ``cli-managed`` marker OAuth row for the catalogue badge.
OpenCompany never stores or injects a token. Headless alternative: an
ambient ``CLOUDFLARE_API_TOKEN`` env var (cf's documented first-priority
credential source) works for ops without any login.
"""

from __future__ import annotations

from services.node_output_schemas import register_output_schema
from services.ws_handler_registry import register_ws_handlers

from ._credentials import CloudflareCredential
from ._handlers import WS_HANDLERS
from .cloudflare_action import CloudflareActionNode, CloudflareActionOutput

register_ws_handlers(WS_HANDLERS)
register_output_schema("cloudflareAction", CloudflareActionOutput)

__all__ = [
    "CloudflareCredential",
    "CloudflareActionNode",
    "WS_HANDLERS",
]
