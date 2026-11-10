"""WebSocket handlers for the Claude Code CLI's own sign-in.

Self-registered into ``services.ws_handler_registry`` from this
plugin's ``__init__.py``; the Claude Code card names them in its ``ws``
block in ``config/credential_providers.json``.

The CLI owns the sign-in (``claude auth login`` / ``status`` / ``logout``
from https://code.claude.com/docs/en/cli-reference) and its credentials
file. OpenCompany only relays it, so the sign-in also works from a
browser on another computer, such as for a headless server (GitHub #129):

1. ``claude_code_login`` starts ``run_claude_login`` as a task, one at a
   time, and answers with the URL of the CLI's sign-in page once the CLI
   prints it (within ``LOGIN_RESPONSE_WAIT_SECONDS``; the status poll
   carries it after that). The card opens it in the owner's browser.
2. After the owner signs in, that page shows a code, and
   ``claude_code_submit_code`` writes it to the waiting CLI. Where the CLI
   could open a browser itself, that window finishes without a code.
3. ``claude_code_status`` reports the sign-in while the card polls it.
   When the CLI exits, ``claude auth status`` decides: signed in, the
   synthetic ``"cli-managed"`` marker is stored with the account's
   ``email``/``orgName`` through ``auth_service.store_oauth_tokens()`` and
   ``credential.oauth.connected`` is broadcast (the frontend's
   ``WebSocketContext`` re-fetches the catalogue); otherwise the card
   shows why.
4. ``claude_code_logout`` cancels a sign-in that is still waiting, which
   stops its CLI. Otherwise it runs ``claude auth logout`` (the CLI clears
   its own credentials), drops the marker and broadcasts ``.disconnected``.

Shared cli-managed-marker helpers (``mark_logged_in`` /
``mark_logged_out`` / ``broadcast_credential_event``) live in
:mod:`services.cli_agent._cli_auth` so codex's own handlers can
reuse them — they're CLI-agnostic plumbing, not claude-specific.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import Any, Awaitable, Callable, Dict

from fastapi import WebSocket

from core.logging import get_logger

from services.cli_agent._cli_auth import (
    broadcast_credential_event,
    mark_logged_in,
    mark_logged_out,
)

from ._oauth import (
    LOGIN_FAILED,
    claude_auth_logout,
    claude_auth_status_info,
    run_claude_login,
)

logger = get_logger(__name__)


_CATALOGUE_KEY = "claude_code"
#: How long Login waits for the CLI to print its sign-in URL before it
#: answers; after that the status poll carries the URL.
LOGIN_RESPONSE_WAIT_SECONDS = 5.0
_SIGN_IN_MESSAGE = "Sign in to Claude in your browser. If the sign-in page then shows a code, paste it here."

# The in-flight login task. Single-flight: a second click while a login
# is running must not spawn a second ``claude auth login`` (each one waits
# up to LOGIN_TIMEOUT_SECONDS, and on a headless box they simply piled
# up — GitHub issue #129). Holding the reference here also keeps the task
# alive; a bare ``create_task`` result is eligible for garbage collection
# mid-flight.
_login_task: asyncio.Task[None] | None = None
# Codes the owner pastes, written to the running login's CLI.
_login_codes: asyncio.Queue[str] | None = None
# What the card shows: pending, url and message, then connected or error.
_login_state: Dict[str, Any] = {}


def _login_in_progress() -> bool:
    return _login_task is not None and not _login_task.done()


def _response() -> Dict[str, Any]:
    return {"success": not _login_state.get("error"), "connected": False, "pending": _login_in_progress(), **_login_state}


async def _connected(info: Dict[str, Any]) -> None:
    """Store the marker for the account ``claude auth status`` reported."""
    await mark_logged_in(_CATALOGUE_KEY, email=info.get("email"), name=info.get("orgName"))
    await broadcast_credential_event("credential.oauth.connected", provider=_CATALOGUE_KEY)


async def _finalize_claude_login(ready: asyncio.Event, codes: asyncio.Queue[str]) -> None:
    """Run ``claude auth login`` to the end, then record what it left."""
    owner = asyncio.current_task()

    def prompt_received(prompt: Dict[str, str]) -> None:
        if _login_task is owner:
            _login_state.update(prompt)
            ready.set()

    try:
        result = await run_claude_login(on_prompt=prompt_received, codes=codes)
        info = await claude_auth_status_info() if result.get("success") else {}
        if _login_task is not owner:
            return
        if info.get("loggedIn"):
            await _connected(info)
            outcome: Dict[str, Any] = {"connected": True, "pending": False}
            logger.info(
                "[claude_code_login] connected as %s (%s, %s)",
                info.get("email") or "unknown",
                info.get("orgName") or "unknown org",
                info.get("subscriptionType") or "unknown plan",
            )
        else:
            outcome = {"pending": False, "error": result.get("error") or LOGIN_FAILED}
            logger.warning("[claude_code_login] sign-in did not complete")
        _login_state.clear()
        _login_state.update(outcome)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        if _login_task is owner:
            _login_state.clear()
            _login_state.update(pending=False, error=LOGIN_FAILED)
        logger.warning("[claude_code_login] sign-in failed: %s", type(exc).__name__)
    finally:
        ready.set()


async def handle_claude_code_login(
    data: Dict[str, Any],  # noqa: ARG001 — frontend sends {}
    websocket: WebSocket,  # noqa: ARG001 — registry signature
) -> Dict[str, Any]:
    """Start the CLI's sign-in and answer with its sign-in page. A click
    while one is running answers with that one; when the CLI is already
    signed in, the marker is refreshed instead."""
    global _login_task, _login_codes
    info = await claude_auth_status_info()
    if info.get("loggedIn"):
        _login_state.clear()
        try:
            await _connected(info)
        except Exception as exc:
            logger.warning("[claude_code_login] mark/broadcast failed: %s", exc)
        return {
            "success": True,
            "connected": True,
            "pending": False,
            "already_logged_in": True,
            "email": info.get("email"),
            "org_name": info.get("orgName"),
            "subscription_type": info.get("subscriptionType"),
            "message": "Already authenticated; refreshed status.",
        }

    if _login_in_progress():
        return _response()

    _login_state.clear()
    _login_state.update(pending=True, message=_SIGN_IN_MESSAGE)
    _login_codes = asyncio.Queue()
    ready = asyncio.Event()
    _login_task = asyncio.create_task(_finalize_claude_login(ready, _login_codes), name="claude_code_login")
    with suppress(TimeoutError):
        await asyncio.wait_for(ready.wait(), timeout=LOGIN_RESPONSE_WAIT_SECONDS)
    return _response()


async def handle_claude_code_submit_code(
    data: Dict[str, Any],
    websocket: WebSocket,  # noqa: ARG001 — registry signature
) -> Dict[str, Any]:
    """Hand the code from the sign-in page to the waiting CLI."""
    if not _login_in_progress() or _login_codes is None:
        return {
            "success": False,
            "connected": False,
            "pending": False,
            "error": "No Claude sign-in is waiting for a code. Click Login to start one.",
        }
    # A code holds no whitespace: drop what a copy across wrapped lines adds.
    code = "".join(str(data.get("code") or "").split())
    # The CLI takes the code as "<code>#<state>" and ignores anything else, so
    # an incomplete paste is answered here and the sign-in keeps waiting.
    first, _, second = code.partition("#")
    if first and second:
        _login_codes.put_nowait(code)
        _login_state["message"] = "Code sent. Claude Code is finishing the sign-in."
    else:
        _login_state["message"] = "That code is incomplete. Copy the whole code from the sign-in page, including the part after #."
    return _response()


async def handle_claude_code_status(
    data: Dict[str, Any],
    websocket: WebSocket,  # noqa: ARG001 — registry signature
) -> Dict[str, Any]:
    """The sign-in's state, which the card polls while it waits. Refresh
    clears the error a finished sign-in left."""
    if data.get("refresh") and not _login_in_progress():
        _login_state.clear()
    return _response()


async def handle_claude_code_logout(
    data: Dict[str, Any],  # noqa: ARG001
    websocket: WebSocket,  # noqa: ARG001
) -> Dict[str, Any]:
    """Cancel a sign-in that is still waiting, which stops its CLI.
    Otherwise run ``claude auth logout`` (CLI clears its own credentials),
    drop the catalogue marker, broadcast ``.disconnected``."""
    global _login_task, _login_codes
    _login_state.clear()
    if _login_in_progress():
        task, _login_task, _login_codes = _login_task, None, None
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return {"success": True, "connected": False, "pending": False}
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


async def shutdown_claude_login() -> None:
    """Stop a sign-in still waiting when the backend shuts down."""
    global _login_task, _login_codes
    task, _login_task, _login_codes = _login_task, None, None
    _login_state.clear()
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ---------------------------------------------------------------------------
# Registry payload — plugin __init__.py registers these into
# services.ws_handler_registry on package import.
# ---------------------------------------------------------------------------

WSHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]

WS_HANDLERS: Dict[str, WSHandler] = {
    "claude_code_login": handle_claude_code_login,
    "claude_code_logout": handle_claude_code_logout,
    "claude_code_status": handle_claude_code_status,
    "claude_code_submit_code": handle_claude_code_submit_code,
}
