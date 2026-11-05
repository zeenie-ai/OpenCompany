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
from services.cli_agent._codex_auth import codex_auth_logout, codex_auth_status_info, codex_binary_path, run_codex_login

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Codex — the CLI owns credentials; only a verified marker is stored here.
# ---------------------------------------------------------------------------


_login_task: asyncio.Task[None] | None = None
_login_lock = asyncio.Lock()
_login_state: Dict[str, Any] = {}
_login_native_started = False
_auth_revision = 0
LOGIN_RESPONSE_WAIT_SECONDS = 5.0


def _login_in_progress() -> bool:
    return _login_task is not None and not _login_task.done()


def _response() -> Dict[str, Any]:
    return {"success": not bool(_login_state.get("error")), "connected": False, "pending": _login_in_progress(), **_login_state}


async def _connected() -> None:
    await mark_logged_in("codex_cli")
    _login_state.clear()
    _login_state.update(connected=True, pending=False)
    await broadcast_credential_event("credential.oauth.connected", provider="codex_cli")


async def _finalize_codex_login(ready: asyncio.Event, *, device_auth: bool) -> None:
    global _login_native_started
    owner = asyncio.current_task()
    preparing_cli = True

    def prompt_received(prompt: Dict[str, str]) -> None:
        if _login_task is not owner:
            return
        _login_state.update(prompt)
        if _login_state.get("url") and (not device_auth or _login_state.get("verification_code")):
            ready.set()

    try:
        # Installation and the native status probe can be slow on first use.
        # Keep both in the owned task so Login, Cancel and polling stay usable.
        await asyncio.to_thread(codex_binary_path)
        preparing_cli = False
        info = await codex_auth_status_info()
        if info.get("logged_in") or info.get("error"):
            async with _login_lock:
                if owner is not _login_task:
                    return
                if info.get("logged_in"):
                    await _connected()
                    _login_state["already_logged_in"] = True
                else:
                    _login_state.clear()
                    _login_state.update(pending=False, error=info["error"])
            return
        _login_native_started = True
        result = await run_codex_login(on_prompt=prompt_received, device_auth=device_auth)
        info = await codex_auth_status_info() if result.get("success") else {}
        async with _login_lock:
            if asyncio.current_task() is not _login_task:
                return
            if result.get("success") and info.get("logged_in"):
                await _connected()
            else:
                _login_state.clear()
                _login_state.update(
                    pending=False, error=result.get("error") or "Codex sign-in completed without authenticated credentials. Try again."
                )
    except asyncio.CancelledError:
        raise
    except Exception:
        # Exceptions may carry OAuth URLs; never log or return raw auth output.
        if owner is _login_task:
            _login_state.clear()
            error = (
                "The managed Codex CLI could not be prepared. Check Bun and registry connectivity, then try Login again."
                if preparing_cli
                else "Codex sign-in could not be completed. Try again."
            )
            _login_state.update(pending=False, error=error)
        logger.warning("[codex_cli_login] authentication failed")
    finally:
        ready.set()


async def handle_codex_cli_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:  # noqa: ARG001
    global _login_task, _login_native_started, _auth_revision
    async with _login_lock:
        if _login_in_progress():
            return _response()
        _auth_revision += 1
        _login_state.clear()
        _login_native_started = False
        _login_state.update(
            pending=True, message="Complete Codex sign-in in your browser. Use a device code when OpenCompany runs on a remote server."
        )
        ready = asyncio.Event()
        _login_task = asyncio.create_task(_finalize_codex_login(ready, device_auth=data.get("device_auth") is True), name="codex_cli_login")
    # Return the URL/code before the CLI finishes. Slow cold installs continue
    # in the background, and the status endpoint supplies instructions later.
    with suppress(TimeoutError):
        await asyncio.wait_for(ready.wait(), timeout=LOGIN_RESPONSE_WAIT_SECONDS)
    return _response()


async def handle_codex_cli_device_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return await handle_codex_cli_login({**data, "device_auth": True}, websocket)


async def handle_codex_cli_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:  # noqa: ARG001
    global _auth_revision
    async with _login_lock:
        if _login_in_progress() or (_login_state.get("error") and not data.get("refresh")):
            return _response()
        _auth_revision += 1
        revision = _auth_revision

    # A status probe never installs the CLI and must not block Login/Cancel.
    # Only the latest probe can reconcile; Login/Logout also invalidate it.
    info = await codex_auth_status_info()
    async with _login_lock:
        if revision != _auth_revision or _login_in_progress():
            return _response()
        if info.get("error"):
            return {"success": False, "error": info["error"]}
        if info.get("logged_in"):
            await _connected()
        else:
            await mark_logged_out("codex_cli")
            _login_state.clear()
            # The durable marker can predate this backend process. Invalidate
            # every client even when no connected state was held in memory.
            await broadcast_credential_event("credential.oauth.disconnected", provider="codex_cli")
        return _response()


async def shutdown_codex_login() -> None:
    """Reap a pending native login during backend lifespan shutdown."""
    global _login_task, _auth_revision
    async with _login_lock:
        _auth_revision += 1
        task, _login_task = _login_task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        _login_state.clear()


async def handle_codex_cli_logout(
    data: Dict[str, Any],  # noqa: ARG001
    websocket: WebSocket,  # noqa: ARG001
) -> Dict[str, Any]:
    global _login_task, _auth_revision
    async with _login_lock:
        _auth_revision += 1
        # Cancel during a cold install/status check without starting another
        # installer just to run logout: no native login was launched yet.
        cancel_check = _login_in_progress() and not _login_native_started
        if _login_in_progress():
            _login_task.cancel()
            with suppress(asyncio.CancelledError):
                await _login_task
        _login_task = None
        _login_state.clear()
        try:
            if not cancel_check and not await codex_auth_logout():
                return {"success": False, "error": "Codex could not sign out. Try again."}
            await mark_logged_out("codex_cli")
            await broadcast_credential_event("credential.oauth.disconnected", provider="codex_cli")
        except Exception:
            logger.warning("[codex_cli_logout] sign-out failed")
            return {"success": False, "error": "Codex could not sign out. Try again."}
        return {"success": True, "connected": False, "pending": False}


# ---------------------------------------------------------------------------
# Registry payload — ``services/cli_agent/__init__.py`` registers these
# into ``services.ws_handler_registry`` on package import.
# ---------------------------------------------------------------------------

WSHandler = Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]

WS_HANDLERS: Dict[str, WSHandler] = {
    "codex_cli_login": handle_codex_cli_login,
    "codex_cli_device_login": handle_codex_cli_device_login,
    "codex_cli_status": handle_codex_cli_status,
    "codex_cli_logout": handle_codex_cli_logout,
}

from services.plugin.shutdown_hooks import register_shutdown_hook

register_shutdown_hook("codex_cli_login", shutdown_codex_login)
