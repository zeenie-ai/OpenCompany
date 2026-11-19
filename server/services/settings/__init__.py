"""Settings domain — Wave 13.3 extraction from ``routers/websocket.py``.

Side-effect import registers the settings handlers listed in
``handlers.WS_HANDLERS`` (user_settings + provider_defaults +
validated_ai_providers + global_model + compaction stats/config) into
``ws_handler_registry``.
"""

from __future__ import annotations

from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from .handlers import WS_HANDLERS as _SETTINGS_WS_HANDLERS

_register_ws_handlers(_SETTINGS_WS_HANDLERS)

__all__ = ["handlers"]
