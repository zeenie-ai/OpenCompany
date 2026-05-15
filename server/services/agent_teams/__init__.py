"""Agent teams WS handlers — Wave 13.4 extraction.

Side-effect import registers the 10 team-lifecycle handlers into
``ws_handler_registry``. The team-service business logic stays in the
existing ``services/agent_team.py`` module (intentional underscore
mismatch — service vs handlers).
"""

from __future__ import annotations

from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers

from .handlers import WS_HANDLERS as _AGENT_TEAMS_WS_HANDLERS

_register_ws_handlers(_AGENT_TEAMS_WS_HANDLERS)

__all__ = ["handlers"]
