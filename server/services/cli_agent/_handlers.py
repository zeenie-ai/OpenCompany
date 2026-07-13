"""WebSocket handlers for CLI providers that still live under the
generic framework.

Self-registered into ``services.ws_handler_registry`` from
``services/cli_agent/__init__.py``. Currently only codex stays here —
claude's handlers moved to ``nodes/agent/claude_code_agent/_handlers.py``
per the canonical plugin-folder pattern. Once ``codex_agent`` adopts
the per-folder layout this whole module goes with it.

Shared cli-managed-marker plumbing (``mark_logged_in`` /
``mark_logged_out`` / ``broadcast_credential_event``) lives in
:mod:`services.cli_agent._cli_auth` and is CLI-agnostic — both
claude and codex handlers consume it.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from fastapi import WebSocket

from core.logging import get_logger

from services.cli_agent._cli_auth import (
    broadcast_credential_event,
    mark_logged_out,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Codex — login flow not yet wired; logout works for marker cleanup
# ---------------------------------------------------------------------------


async def handle_codex_cli_login(
    data: Dict[str, Any],  # noqa: ARG001
    websocket: WebSocket,  # noqa: ARG001
) -> Dict[str, Any]:
    return {
        "success": False,
        "error": (
            "Codex login is not yet wired in OpenCompany. "
            "Install with `npm install -g @openai/codex` and run "
            "`codex login` in your terminal — then click Login again "
            "to mark connected."
        ),
    }


async def handle_codex_cli_logout(
    data: Dict[str, Any],  # noqa: ARG001
    websocket: WebSocket,  # noqa: ARG001
) -> Dict[str, Any]:
    try:
        await mark_logged_out("codex_cli")
        await broadcast_credential_event(
            "credential.oauth.disconnected",
            provider="codex_cli",
        )
    except Exception as exc:
        logger.warning("[codex_cli_logout] failed: %s", exc)
        return {"success": False, "error": str(exc)}
    return {"success": True}


# ---------------------------------------------------------------------------
# Registry payload — ``services/cli_agent/__init__.py`` registers these
# into ``services.ws_handler_registry`` on package import.
# ---------------------------------------------------------------------------

WSHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]

WS_HANDLERS: Dict[str, WSHandler] = {
    "codex_cli_login": handle_codex_cli_login,
    "codex_cli_logout": handle_codex_cli_logout,
}
