"""Vercel WebSocket handlers.

``vercel login`` (2026 CLI) is an OAuth **device flow**: the CLI prints
a verification URL (+ user code) and then blocks, polling Vercel until
the user authorises in a browser. Unlike Stripe there is no two-step
``--non-interactive`` / ``--complete <url>`` pair, so the login handler
cannot use :func:`run_cli_command` (it buffers output via
``communicate()`` until process exit — the URL is needed mid-run).
Instead it spawns the CLI directly, reads stdout/stderr incrementally
until the URL appears, returns ``{success, url}`` to the frontend
(which ``window.open``s the url — it never renders a separate code, so
the url must be the code-embedding link), and lets a background task
await process exit.

Success gate mirrors Stripe's exit-code-distrust idiom: the pinned
``auth.json`` mtime must advance past its pre-login snapshot AND
:func:`is_logged_in` must hold. On success a synthetic marker OAuth
token flips the catalogue's ``stored`` flag (``_cli_base`` pattern) and
the generic catalogue-invalidation broadcast fires.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.events import run_cli_command

from ._install import ensure_vercel_cli, vercel_cli_path
from ._service import (
    extract_login_url,
    extract_verification_code,
    global_argv,
    is_logged_in,
    stored_token,
    strip_ansi,
    vercel_auth_path,
    vercel_env,
)

logger = get_logger(__name__)

_LOGIN_TIMEOUT_SECONDS = 600
# How long to wait for the device-flow URL to appear on the CLI's
# output before giving up on this login attempt.
_URL_DEADLINE_SECONDS = 20
# The frontend drops WS requests after 30s (REQUEST_TIMEOUT in
# WebSocketContext). The first-ever login pays a cold `npm install
# vercel` inside this handler, which can blow well past that — so the
# handler answers within this budget no matter what, and the login
# keeps going in the background (the CLI auto-opens the browser on
# this machine when it gets to the device-flow prompt).
_RESPONSE_BUDGET_SECONDS = 22
_MARKER_TOKEN = "cli-managed"


async def _resolved_binary() -> Optional[str]:
    """Resolve the vercel binary path, installing on first use.
    Returns None on install failure (caller surfaces the error)."""
    try:
        return str(await ensure_vercel_cli())
    except Exception as e:
        logger.warning("[Vercel] CLI install failed: %s", e)
        return None


async def _mark_logged_in() -> None:
    from services.plugin.deps import get_auth_service

    await get_auth_service().store_oauth_tokens(
        provider="vercel",
        access_token=_MARKER_TOKEN,
        refresh_token=_MARKER_TOKEN,
    )


async def _mark_logged_out() -> None:
    from services.plugin.deps import get_auth_service

    await get_auth_service().remove_oauth_tokens("vercel")


async def _broadcast_credential_event(event_type: str) -> None:
    from services.status_broadcaster import get_status_broadcaster

    await get_status_broadcaster().broadcast_credential_event(
        event_type,
        provider="vercel",
    )


# Strong refs for fire-and-forget tasks — asyncio only holds weak refs,
# so an un-referenced running task can be garbage-collected mid-flight.
_background_tasks: set = set()


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _read_login_banner(proc: asyncio.subprocess.Process) -> tuple[Optional[str], Optional[str], List[str]]:
    """Read the login process's early output until a URL shows up (or
    the deadline / EOF). Returns ``(url, verification_code, tail_lines)``.

    Chunk-based, not ``readline()`` — device-flow spinners repaint via
    ``\\r`` frames with no newline, which would overrun the
    StreamReader line limit. The pumps deliberately OUTLIVE this
    function: they keep draining both pipes for the process lifetime so
    the CLI never blocks on a full pipe buffer during its ~10-minute
    poll; only the first 32 KiB is retained for parsing.
    """
    buf: List[str] = []
    buf_len = 0
    new_data = asyncio.Event()

    async def pump(stream: Optional[asyncio.StreamReader]) -> None:
        nonlocal buf_len
        if stream is None:
            new_data.set()
            return
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            if buf_len < 32768:
                text = chunk.decode(errors="replace")
                buf.append(text)
                buf_len += len(text)
            new_data.set()
        new_data.set()

    pumps = [_spawn_background(pump(proc.stdout)), _spawn_background(pump(proc.stderr))]

    url: Optional[str] = None
    code: Optional[str] = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _URL_DEADLINE_SECONDS
    while True:
        blob = strip_ansi("".join(buf))
        url = url or extract_login_url(blob)
        code = code or extract_verification_code(blob)
        if url or all(t.done() for t in pumps):
            break
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        new_data.clear()
        try:
            await asyncio.wait_for(new_data.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            break

    tail = [ln.strip() for ln in strip_ansi("".join(buf)).splitlines() if ln.strip()]
    return url, code, tail


async def _start_login_flow() -> Dict[str, Any]:
    """Install (if needed) + spawn the device-flow login + parse the
    banner + schedule completion. Never raises — returns the WS
    response dict."""
    try:
        binary = await _resolved_binary()
        if not binary:
            return {
                "success": False,
                "error": "Vercel CLI install failed. Manual install: npm i -g vercel (https://vercel.com/docs/cli)",
            }

        auth = vercel_auth_path()
        pre_mtime = auth.stat().st_mtime if auth.exists() else 0.0

        # stdin=PIPE left un-written — under the FastAPI daemon (no TTY)
        # an inherited closed stdin makes the CLI see EOF and abort the
        # device-flow poll (claude auth login precedent, see
        # run_cli_command's stdin doc).
        proc = await asyncio.create_subprocess_exec(
            binary,
            *global_argv(["login"]),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=vercel_env(),
        )

        url, code, lines = await _read_login_banner(proc)
        if not url:
            proc.kill()
            banner = " | ".join(lines[-5:]) or "(no output)"
            logger.warning("[Vercel] login banner had no URL within %ss: %s", _URL_DEADLINE_SECONDS, banner)
            return {
                "success": False,
                "error": f"Could not extract the login URL from 'vercel login' output. CLI said: {banner}",
            }

        logger.info(
            "[Vercel] device-flow URL issued (code=%s) — opening on frontend; awaiting browser confirmation in background (timeout=%ss)",
            code,
            _LOGIN_TIMEOUT_SECONDS,
        )
        _spawn_background(_complete_login(proc, pre_mtime))
        # The frontend only opens `url`; `verification_code` rides along for
        # forward-compat (never rendered today — keep the URL code-embedding).
        return {"success": True, "url": url, "verification_code": code}
    except Exception as e:
        logger.exception("[Vercel] login flow raised unexpectedly: %s", e)
        return {"success": False, "error": f"Vercel login failed: {e}"}


async def handle_vercel_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Answer within the frontend's request window no matter what.

    Fast path (CLI already installed): the device-flow URL is on the
    CLI's output within a few seconds — return it so the frontend opens
    the browser. Cold path (first run pays ``npm install vercel``): the
    budget expires first; return a pending success immediately and let
    the flow finish in the background — ``vercel login`` opens the
    browser on this machine itself once it reaches the device-flow
    prompt, and the connected-state broadcast flips the modal when the
    user authorises."""
    logger.info("[Vercel] login flow starting (device flow)")
    flow = _spawn_background(_start_login_flow())
    try:
        return await asyncio.wait_for(asyncio.shield(flow), timeout=_RESPONSE_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        logger.info(
            "[Vercel] login still preparing after %ss (cold CLI install) — continuing in background; the CLI will open the browser itself",
            _RESPONSE_BUDGET_SECONDS,
        )
        return {
            "success": True,
            "pending": True,
            "message": "Vercel CLI is being installed — your browser will open shortly to complete the login.",
        }


async def _complete_login(proc: asyncio.subprocess.Process, pre_mtime: float) -> None:
    """Await the device-flow poll. Success = ``auth.json`` mtime advanced
    past the pre-login snapshot AND the sniff holds — the exit code alone
    is not trusted (Stripe precedent: CLIs exit non-zero after a
    successful credential write)."""
    try:
        try:
            returncode = await asyncio.wait_for(proc.wait(), timeout=_LOGIN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("[Vercel] login timed out after %ss — user never completed the browser flow", _LOGIN_TIMEOUT_SECONDS)
            return
    except Exception as e:
        # asyncio.create_task swallows exceptions silently — log them.
        logger.exception("[Vercel] login wait raised unexpectedly: %s", e)
        return

    auth = vercel_auth_path()
    post_mtime = auth.stat().st_mtime if auth.exists() else 0.0
    fresh_credentials_written = post_mtime > pre_mtime and is_logged_in()

    if not fresh_credentials_written:
        logger.warning(
            "[Vercel] login exited (code=%s) but no fresh credentials written to %s (pre=%.3f post=%.3f) — user likely closed the browser before authorising",
            returncode,
            auth,
            pre_mtime,
            post_mtime,
        )
        return

    if returncode != 0:
        logger.debug("[Vercel] login exited non-zero (%s) but credentials WERE written — treating as success", returncode)

    logger.info("[Vercel] auth successful — credentials written to %s; persisting catalogue marker", auth)
    await _mark_logged_in()
    await _broadcast_credential_event("credential.oauth.connected")


async def handle_vercel_logout(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """``vercel logout`` against the pinned config dir, then drop the
    catalogue marker + broadcast so the modal flips immediately."""
    logger.info("[Vercel] logout starting")
    cached = vercel_cli_path()
    if cached is None:
        auth = vercel_auth_path()
        auth.unlink(missing_ok=True)
        result: Dict[str, Any] = {"success": True, "message": "Logged out (CLI not yet installed; cleared auth file)"}
        logger.info("[Vercel] logout fallback: CLI not installed; deleted %s", auth)
    else:
        result = await run_cli_command(
            binary=str(cached),
            argv=global_argv(["logout"]),
            timeout=15.0,
            env=vercel_env(),
        )
        # Belt-and-braces: the sniff must not report logged-in afterwards.
        if is_logged_in():
            vercel_auth_path().unlink(missing_ok=True)
    await _mark_logged_out()
    await _broadcast_credential_event("credential.oauth.disconnected")
    logger.info("[Vercel] logout complete: marker token removed + catalogue broadcast sent")
    return result


async def handle_vercel_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Login-state snapshot: connected when EITHER auth path is live."""
    token = await stored_token()
    logged_in = is_logged_in()
    return {
        "success": True,
        "status": {
            "logged_in": logged_in,
            "token_stored": bool(token),
            "connected": logged_in or bool(token),
        },
    }


WS_HANDLERS = {
    "vercel_login": handle_vercel_login,
    "vercel_logout": handle_vercel_logout,
    "vercel_status": handle_vercel_status,
}
