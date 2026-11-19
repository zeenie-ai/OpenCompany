"""Pricing-domain WS handlers — Wave 13.8 extraction.

Sibling module to ``services/pricing.py`` (the PricingService singleton);
kept flat instead of a subpackage to avoid renaming the existing
``services.pricing`` import path used across handlers.

Side-effect import (from ``main.py``) registers the 2 handlers below
into ``ws_handler_registry``:
  - ``get_pricing_config`` — return full pricing config for the
    Settings panel.
  - ``save_pricing_config`` — persist edits.

The usage summaries (``get_provider_usage_summary`` /
``get_api_usage_summary``) are served by ``services/usage/handlers.py``.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import WebSocket

from core.logging import get_logger
from services.ws_handler_registry import register_ws_handlers, ws_handler

logger = get_logger(__name__)


@ws_handler()
async def handle_get_pricing_config(
    data: Dict[str, Any],
    websocket: WebSocket,
) -> Dict[str, Any]:
    """Get full pricing configuration for display/editing."""
    from services.pricing import get_pricing_service

    pricing = get_pricing_service()
    return {"success": True, "config": pricing.get_config()}


@ws_handler()
async def handle_save_pricing_config(
    data: Dict[str, Any],
    websocket: WebSocket,
) -> Dict[str, Any]:
    """Save updated pricing configuration."""
    from services.pricing import get_pricing_service

    config = data.get("config")
    if not config:
        return {"success": False, "error": "No config provided"}

    pricing = get_pricing_service()
    success = pricing.save_config(config)
    return {"success": success}


WS_HANDLERS: Dict[str, Any] = {
    "get_pricing_config": handle_get_pricing_config,
    "save_pricing_config": handle_save_pricing_config,
}

# Self-register on import — ``main.py`` triggers via
# ``import services.pricing_handlers``.
register_ws_handlers(WS_HANDLERS)


__all__ = [
    "WS_HANDLERS",
    "handle_get_pricing_config",
    "handle_save_pricing_config",
]
