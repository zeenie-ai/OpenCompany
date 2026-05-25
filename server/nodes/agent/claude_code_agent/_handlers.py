"""WebSocket handlers for claude code CLI-managed OAuth.

Self-registered into ``services.ws_handler_registry`` from this
plugin's ``__init__.py``. Wire format matches the prior unified
handlers (``claude_code_login`` / ``claude_code_logout``) — frontend
dispatches with an empty payload.

Claude flow uses the documented CLI subcommands from
https://code.claude.com/docs/en/cli-reference (``claude auth login`` /
``status`` / ``logout``). Every subprocess invocation goes through
``services.events.cli.run_cli_command`` (Stripe precedent — see
``nodes/stripe/_handlers.py``); the CLI owns its own credentials file
and we never touch it.

Login lifecycle:

1. ``handle_claude_code_login`` schedules ``run_claude_login`` as an
   ``asyncio.create_task`` (mirrors Stripe's ``stripe login --complete``)
   and returns immediately. The CLI opens the user's browser; up to
   10 minutes are allowed for the flow to complete.
2. When the task resolves, ``_finalize_claude_login`` reads
   ``claude auth status``, stores the synthetic ``"cli-managed"``
   marker along with the user's ``email``/``orgName`` via
   ``auth_service.store_oauth_tokens()``, and fires
   ``broadcast_credential_event("credential.oauth.connected", ...)`` —
   the frontend's ``WebSocketContext`` re-fetches the catalogue.

Logout runs ``claude auth logout`` (CLI clears its own credentials),
drops the catalogue marker, and broadcasts ``.disconnected``.

Shared cli-managed-marker helpers (``_mark_logged_in`` /
``_mark_logged_out`` / ``_broadcast_credential_event``) live in
:mod:`services.cli_agent._cli_auth` so codex's own handlers can
reuse them — they're CLI-agnostic plumbing, not claude-specific.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Dict

from fastapi import WebSocket

from core.logging import get_logger

from services.cli_agent._cli_auth import (
    broadcast_credential_event,
    mark_logged_in,
    mark_logged_out,
)

from ._oauth import (
    claude_auth_logout,
    claude_auth_status_info,
    run_claude_login,
)

logger = get_logger(__name__)


_CATALOGUE_KEY = "claude_code"


async def _finalize_claude_login() -> None:
    """Run ``claude auth login`` to completion, then store user info +
    broadcast on success."""
    try:
        envelope = await run_claude_login()
        if not envelope.get("success"):
            logger.warning(
                "[claude_code_login] CLI exited unsuccessfully: %s",
                envelope.get("error") or envelope.get("stderr"),
            )
            return

        info = await claude_auth_status_info()
        if not info.get("loggedIn"):
            logger.warning(
                "[claude_code_login] CLI exited cleanly but auth status " "reports not logged in: %s",
                info,
            )
            return

        email = info.get("email")
        org_name = info.get("orgName")
        await mark_logged_in(_CATALOGUE_KEY, email=email, name=org_name)
        await broadcast_credential_event(
            "credential.oauth.connected",
            provider=_CATALOGUE_KEY,
        )
        logger.info(
            "[claude_code_login] connected as %s (%s · %s)",
            email or "unknown",
            org_name or "unknown org",
            info.get("subscriptionType") or "unknown plan",
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("[claude_code_login] finalize failed: %s", exc)


async def handle_claude_code_login(
    data: Dict[str, Any],  # noqa: ARG001 — frontend sends {}
    websocket: WebSocket,  # noqa: ARG001 — registry signature
) -> Dict[str, Any]:
    """Spawn ``claude auth login`` in the background; let the CLI open
    the user's browser. Idempotent re-click syncs the marker without
    re-running the flow."""
    info = await claude_auth_status_info()
    if info.get("loggedIn"):
        try:
            await mark_logged_in(
                _CATALOGUE_KEY,
                email=info.get("email"),
                name=info.get("orgName"),
            )
            await broadcast_credential_event(
                "credential.oauth.connected",
                provider=_CATALOGUE_KEY,
            )
        except Exception as exc:
            logger.warning("[claude_code_login] mark/broadcast failed: %s", exc)
        return {
            "success": True,
            "already_logged_in": True,
            "email": info.get("email"),
            "org_name": info.get("orgName"),
            "subscription_type": info.get("subscriptionType"),
            "message": "Already authenticated; refreshed status.",
        }

    asyncio.create_task(_finalize_claude_login(), name="claude_code_login")
    return {
        "success": True,
        "message": "Claude is opening your browser to authenticate.",
    }


async def handle_claude_code_logout(
    data: Dict[str, Any],  # noqa: ARG001
    websocket: WebSocket,  # noqa: ARG001
) -> Dict[str, Any]:
    """Run ``claude auth logout`` (CLI clears its own credentials), drop
    the catalogue marker, broadcast ``.disconnected``."""
    try:
        await claude_auth_logout()
        await mark_logged_out(_CATALOGUE_KEY)
        await broadcast_credential_event(
            "credential.oauth.disconnected",
            provider=_CATALOGUE_KEY,
        )
    except Exception as exc:
        logger.warning("[claude_code_logout] failed: %s", exc)
        return {"success": False, "error": str(exc)}
    return {"success": True}


# ---------------------------------------------------------------------------
# Registry payload — plugin __init__.py registers these into
# services.ws_handler_registry on package import.
# ---------------------------------------------------------------------------

WSHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]

WS_HANDLERS: Dict[str, WSHandler] = {
    "claude_code_login": handle_claude_code_login,
    "claude_code_logout": handle_claude_code_logout,
}
