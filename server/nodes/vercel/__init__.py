"""Plugins for the 'deployment' palette group — Vercel.

Self-contained CLI-managed-auth plugin (stripe shape, minus the
daemon/webhook surface — the Vercel CLI has no ``listen`` equivalent).
This package contributes only the Vercel-specific shapes: the action
node with its argv builders, the device-flow login driver, the config
pinning helpers, and the credential class.
"""

from __future__ import annotations

from services.node_output_schemas import register_output_schema
from services.ws_handler_registry import register_ws_handlers

from ._credentials import VercelCredential
from ._handlers import WS_HANDLERS
from .vercel_action import VercelActionNode, VercelActionOutput

register_ws_handlers(WS_HANDLERS)
register_output_schema("vercelAction", VercelActionOutput)

__all__ = [
    "VercelCredential",
    "VercelActionNode",
    "WS_HANDLERS",
]
