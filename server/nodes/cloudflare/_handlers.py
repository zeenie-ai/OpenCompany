"""Cloudflare WebSocket handlers — the cf CLI owns its own auth (gh /
Stripe CLI pattern).

``cloudflare_login`` spawns cf's official browser login::

    cf auth login

Source-verified behavior (cf 0.2.0 ``dist/auth-*.mjs`` + live headless
capture): the flow runs a PKCE OAuth against
``dash.cloudflare.com/oauth2/auth`` with a loopback callback server on
``localhost:8877``, self-opens the browser, and prints to **stderr**
(status stream; stdout is reserved for JSON)::

    Attempting to login via OAuth...
    Opening a link in your default browser: https://dash.cloudflare.com/oauth2/auth?...

(or ``Visit this link to authenticate: <url>`` when it can't open a
browser). There is NO device code — the loopback callback completes the
flow, so the handler returns ``{success, url}`` only and the modal just
opens the url. Success gate: ``cf auth whoami`` reporting
``authenticated: true`` (cf exits 0 in both states, so exit codes are
never trusted). On success we write the synthetic ``cli-managed``
marker OAuth row (flips the catalogue's ``stored`` badge, with the
whoami email as the account label) and broadcast the generic
catalogue-invalidation event. Marker + broadcast plumbing is the shared
:mod:`services.cli_agent._cli_auth` module (claude/codex/github all use
it).

cf delta vs gh: when a session already exists, ``cf auth login``
(without ``--force``) exits without printing an authorize URL — the
handler detects the early exit, confirms via whoami, and reports
success immediately.

OpenCompany never stores or reads the actual token — it stays in cf's
user-level config (``auth.jsonc`` / OS keyring).
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.cli_agent._cli_auth import broadcast_credential_event, mark_logged_in, mark_logged_out
from services.events import run_cli_command

from ._install import ensure_cf_cli
from ._service import extract_login_url, login_env, whoami_snapshot

logger = get_logger(__name__)

_LOGIN_ARGS = ["auth", "login"]
_LOGIN_TIMEOUT_SECONDS = 600
# How long to wait for the authorize URL on the CLI's output before
# giving up on this attempt.
_BANNER_DEADLINE_SECONDS = 20
# The frontend drops WS requests after 30s. A first-ever login pays a
# cold npm install inside this handler — answer within this budget no
# matter what and let the flow continue in the background.
_RESPONSE_BUDGET_SECONDS = 22


# Strong refs for fire-and-forget tasks — asyncio holds only weak refs
# (the documented discard-set pattern from the asyncio docs).
_background_tasks: set = set()


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _read_login_banner(proc: asyncio.subprocess.Process) -> tuple[Optional[str], List[str]]:
    """Read the login process's early output until the authorize URL
    shows up (or deadline / EOF). Chunk-based, not ``readline()`` —
    spinner frames repaint via ``\\r`` with no newline. The pumps
    deliberately OUTLIVE this function: they keep draining both pipes
    for the process lifetime so cf never blocks on a full pipe buffer
    while its callback server waits (~10 min); only the first 32 KiB is
    retained for parsing."""
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
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _BANNER_DEADLINE_SECONDS
    while True:
        url = extract_login_url("".join(buf))
        if url is not None or all(t.done() for t in pumps):
            break
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        new_data.clear()
        try:
            await asyncio.wait_for(new_data.wait(), timeout=remaining)
        except asyncio.TimeoutError:
            break

    tail = [ln.strip() for ln in "".join(buf).splitlines() if ln.strip()]
    return url, tail


async def _start_login_flow() -> Dict[str, Any]:
    """Install (if needed) + spawn cf's browser login + parse the
    banner + schedule completion. Never raises — returns the WS
    response dict."""
    try:
        try:
            binary = str(await ensure_cf_cli())
        except Exception as e:
            logger.warning("[Cloudflare] cf CLI install failed: %s", e)
            return {
                "success": False,
                "error": f"cf CLI install failed ({e}). Manual install: npm i -g cf",
            }

        # stdin=PIPE left un-written: cf's prompts auto-disable when
        # stdio is not a TTY, and a pipe keeps any stray stdin read
        # from seeing instant EOF.
        proc = await asyncio.create_subprocess_exec(
            binary,
            *_LOGIN_ARGS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=login_env(),
        )

        url, lines = await _read_login_banner(proc)
        if url is None:
            # cf delta: with a live session, `cf auth login` exits
            # without printing an authorize URL — confirm via whoami
            # and mark immediately instead of treating it as a failure.
            if proc.returncode is not None:
                info = await whoami_snapshot()
                if info:
                    email = info.get("email")
                    await mark_logged_in("cloudflare", email=email)
                    await broadcast_credential_event("credential.oauth.connected", provider="cloudflare")
                    logger.info("[Cloudflare] already logged in as %s — marker refreshed", email or "<unknown>")
                    return {"success": True, "message": f"Already logged in{f' as {email}' if email else ''}."}
            proc.kill()
            banner = " | ".join(lines[-5:]) or "(no output)"
            logger.warning("[Cloudflare] login banner had no authorize URL within %ss: %s", _BANNER_DEADLINE_SECONDS, banner)
            return {
                "success": False,
                "error": (
                    f"Could not start cf's browser login. CLI said: {banner} — "
                    "you can also run 'cf auth login' in a terminal on this machine."
                ),
            }

        logger.info(
            "[Cloudflare] OAuth authorize URL issued — opening on frontend; awaiting loopback callback in background (timeout=%ss)",
            _LOGIN_TIMEOUT_SECONDS,
        )
        _spawn_background(_complete_login(proc))
        return {"success": True, "url": url}
    except Exception as e:
        logger.exception("[Cloudflare] login flow raised unexpectedly: %s", e)
        return {"success": False, "error": f"Cloudflare login failed: {e}"}


async def handle_cloudflare_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Answer within the frontend's request window no matter what
    (vercel's cold-install pending pattern)."""
    logger.info("[Cloudflare] login flow starting (cf auth login)")
    flow = _spawn_background(_start_login_flow())
    try:
        return await asyncio.wait_for(asyncio.shield(flow), timeout=_RESPONSE_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        logger.info(
            "[Cloudflare] login still preparing after %ss (cold cf install) — continuing in background",
            _RESPONSE_BUDGET_SECONDS,
        )
        return {
            "success": True,
            "pending": True,
            "message": "cf CLI is being installed — click Login again in a few seconds.",
        }


async def _complete_login(proc: asyncio.subprocess.Process) -> None:
    """Await cf's loopback-callback flow; gate success on ``cf auth
    whoami`` (exit codes are not trusted — cf exits 0 either way); then
    the marker + broadcast."""
    try:
        try:
            returncode = await asyncio.wait_for(proc.wait(), timeout=_LOGIN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("[Cloudflare] login timed out after %ss — user never completed the browser flow", _LOGIN_TIMEOUT_SECONDS)
            return
    except Exception as e:
        logger.exception("[Cloudflare] login wait raised unexpectedly: %s", e)
        return

    info = await whoami_snapshot()
    if not info:
        logger.warning(
            "[Cloudflare] login exited (code=%s) but 'cf auth whoami' reports no session — user likely closed the browser before authorising",
            returncode,
        )
        return

    email = info.get("email")
    await mark_logged_in("cloudflare", email=email)
    logger.info("[Cloudflare] connected as %s — catalogue marker persisted", email or "<unknown>")
    await broadcast_credential_event("credential.oauth.connected", provider="cloudflare")


async def handle_cloudflare_logout(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """``cf auth logout`` (best-effort, revokes the OAuth token), drop
    the catalogue marker, broadcast so the modal flips immediately."""
    logger.info("[Cloudflare] logout starting")
    from ._service import resolve_cf_light

    binary = resolve_cf_light()
    result: Dict[str, Any] = {"success": True}
    if binary:
        result = await run_cli_command(
            binary=binary,
            argv=["auth", "logout"],
            timeout=15.0,
            env=login_env(),
        )
        if not result.get("success"):
            logger.warning("[Cloudflare] 'cf auth logout' failed (marker still removed): %s", result.get("error"))
            result = {"success": True, "message": "cf reported no stored session; catalogue marker removed"}
    await mark_logged_out("cloudflare")
    await broadcast_credential_event("credential.oauth.disconnected", provider="cloudflare")
    logger.info("[Cloudflare] logout complete: marker removed + catalogue broadcast sent")
    return result


async def handle_cloudflare_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Session snapshot straight from the CLI — no side effects."""
    info = await whoami_snapshot()
    connected = info is not None
    status: Dict[str, Any] = {"connected": connected, "logged_in": connected}
    if info and info.get("email"):
        status["email"] = info["email"]
    return {"success": True, "status": status}


WS_HANDLERS = {
    "cloudflare_login": handle_cloudflare_login,
    "cloudflare_logout": handle_cloudflare_logout,
    "cloudflare_status": handle_cloudflare_status,
}
