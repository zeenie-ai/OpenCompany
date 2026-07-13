"""AI CLI agent framework — multi-provider, multi-instance, VSCode-pattern.

Spawns N parallel Claude Code / Codex CLI sessions per workflow node,
each isolated in its own git worktree, with a shared MCP server hosting
OpenCompany tools (``mcp__opencompany__*``) discovered via VSCode-style
lockfile.

Self-registers per-provider WebSocket handlers
(``claude_code_login``, ``claude_code_logout``, ``codex_cli_login``,
``codex_cli_logout``) into ``services.ws_handler_registry`` on import,
mirroring the telegram / whatsapp / stripe plugin folder pattern. The
router does not need to know about us by name.
"""

from __future__ import annotations

import logging as _logging

# Pin every `[CC-Agent ...]` logger in this package at INFO so live
# debugging surfaces without flipping the global LOG_LEVEL. Override with
# ``logging.getLogger("services.cli_agent").setLevel(...)`` for quieter
# runs.
_logging.getLogger("services.cli_agent").setLevel(_logging.INFO)

from services.cli_agent.factory import (
    create_cli_provider,
    is_supported,
    register_provider,
    registered_provider_names,
)
from services.cli_agent.protocol import (
    AICliProvider,
    BatchResult,
    CanonicalUsage,
    SessionResult,
)
from services.cli_agent.types import (
    AICliTaskSpec,
    BaseAICliTaskSpec,
    BatchResultModel,
    BatchSummary,
    ClaudeTaskSpec,
    CodexTaskSpec,
    GeminiTaskSpec,
    SessionResultModel,
    session_result_to_model,
)

# --- self-registration on import -------------------------------------------
#
# Framework-side: register codex (still lives under
# ``services/cli_agent/providers/`` because the codex_agent plugin hasn't
# been migrated to the plugin-folder layout yet — separate scope). Claude
# is registered by its plugin folder
# (``nodes/agent/claude_code_agent/__init__.py``), which imports + calls
# ``register_provider("claude", AnthropicClaudeProvider)`` on its own
# module load. We don't import the plugin here; it imports us.
from services.ws_handler_registry import register_ws_handlers
from services.cli_agent._handlers import WS_HANDLERS

register_ws_handlers(WS_HANDLERS)


def _register_builtin_providers() -> None:
    """Bind providers that still live under ``services/cli_agent/providers/``.

    Plugins that have been migrated to the per-folder layout
    (e.g. ``claude_code_agent``) self-register from their own
    ``__init__.py``. This function only covers the ones that haven't
    moved yet.
    """
    from services.cli_agent.providers.openai_codex import OpenAICodexProvider

    register_provider("codex", OpenAICodexProvider)


_register_builtin_providers()


__all__ = [
    # Factory
    "create_cli_provider",
    "is_supported",
    "register_provider",
    "registered_provider_names",
    # Protocol + dataclasses
    "AICliProvider",
    "CanonicalUsage",
    "SessionResult",
    "BatchResult",
    # Pydantic specs
    "BaseAICliTaskSpec",
    "ClaudeTaskSpec",
    "CodexTaskSpec",
    "GeminiTaskSpec",
    "AICliTaskSpec",
    # Pydantic result models
    "SessionResultModel",
    "BatchSummary",
    "BatchResultModel",
    "session_result_to_model",
]
