"""Cloudflare WebSocket handlers — the cf CLI owns its own auth (gh /
Stripe CLI pattern), including the browser interaction.

``cloudflare_login`` spawns cf's official login and lets the CLI drive
the whole flow itself::

    cf auth login --force

Source-verified behavior (cf 0.2.0 ``dist/auth-*.mjs`` + live capture):
the flow runs a PKCE OAuth against ``dash.cloudflare.com/oauth2/auth``
with a loopback callback server on the FIXED port ``localhost:8877``,
and cf OPENS THE DEFAULT BROWSER ITSELF (``start`` / ``open`` /
``xdg-open``). The handler deliberately does NOT parse or proxy the
authorize URL to the frontend — no custom login UI; the modal just gets
``{success, message}`` and the connected badge flips when the
background completion broadcasts. Headless/remote deployments use
``CLOUDFLARE_API_TOKEN`` instead (cf's documented first-priority
credential source).

Two hazards this module guards against (both observed live):

* **Fixed callback port** — two concurrent ``cf auth login`` processes
  collide on 8877 and the second exits instantly with "Port already in
  use". A module-level single-flight guard makes repeat Login clicks
  return "already in progress" instead of spawning again.
* **Windows shim orphaning** — the installed binary is an npm ``.cmd``
  shim; killing it terminates only the cmd.exe wrapper and orphans the
  node child, which keeps holding port 8877 and breaks every later
  login. The completion watcher therefore NEVER kills the process — cf
  enforces its own login timeout and exits by itself.

Success gate: ``cf auth whoami`` reporting ``authenticated: true`` (cf
exits 0 in both auth states, so exit codes are never trusted). On
success we write the synthetic ``cli-managed`` marker OAuth row (flips
the catalogue's ``stored`` badge, with the whoami email as the account
label) and broadcast the generic catalogue-invalidation event. Marker +
broadcast plumbing is the shared :mod:`services.cli_agent._cli_auth`
module (claude/codex/github all use it).

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
from ._service import login_env, whoami_snapshot

logger = get_logger(__name__)

# --force: we only spawn when whoami reports no live session, so this
# never discards a healthy login — it exists to push PAST a stale or
# invalid stored token that would otherwise short-circuit the flow.
_LOGIN_ARGS = ["auth", "login", "--force"]
_LOGIN_TIMEOUT_SECONDS = 600
# The frontend drops WS requests after 30s. A first-ever login pays a
# cold npm install inside this handler — answer within this budget no
# matter what and let the flow continue in the background.
_RESPONSE_BUDGET_SECONDS = 22
# Retained head of the CLI's output, used only for the failure log line.
_OUTPUT_CAP_BYTES = 8192


# Strong refs for fire-and-forget tasks — asyncio holds only weak refs
# (the documented discard-set pattern from the asyncio docs).
_background_tasks: set = set()

# Single-flight state: at most one login flow at a time (fixed callback
# port). `task` covers the pre-spawn window (install + whoami probe),
# `proc` covers the browser-flow window until cf exits on its own.
_active_login: Dict[str, Any] = {"task": None, "proc": None}


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _login_in_progress() -> bool:
    task = _active_login["task"]
    if task is not None and not task.done():
        return True
    proc = _active_login["proc"]
    return proc is not None and proc.returncode is None


async def _mark_connected(email: Optional[str]) -> None:
    await mark_logged_in("cloudflare", email=email)
    logger.info("[Cloudflare] connected as %s — catalogue marker persisted", email or "<unknown>")
    await broadcast_credential_event("credential.oauth.connected", provider="cloudflare")


async def _start_login_flow() -> Dict[str, Any]:
    """Install (if needed) + spawn cf's own browser login. Never raises
    — returns the WS response dict. The CLI owns the interaction from
    here: it opens the browser, serves the loopback callback, and exits
    when done; we only watch for the exit in the background."""
    try:
        try:
            binary = str(await ensure_cf_cli())
        except Exception as e:
            logger.warning("[Cloudflare] cf CLI install failed: %s", e)
            return {
                "success": False,
                "error": f"cf CLI install failed ({e}). Manual install: npm i -g cf",
            }

        # Fast path: a live session already exists (user logged in via a
        # terminal, or a previous flow completed after the modal closed).
        info = await whoami_snapshot()
        if info:
            email = info.get("email")
            await _mark_connected(email)
            return {"success": True, "message": f"Already logged in{f' as {email}' if email else ''}."}

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
        _active_login["proc"] = proc

        # Drain both pipes for the process lifetime so cf never blocks
        # on a full pipe buffer while its callback server waits. Only a
        # small head is retained — solely for the failure log line.
        output: List[str] = []

        async def drain(stream: Optional[asyncio.StreamReader]) -> None:
            if stream is None:
                return
            kept = 0
            while True:
                chunk = await stream.read(4096)
                if not chunk:
                    break
                if kept < _OUTPUT_CAP_BYTES:
                    text = chunk.decode(errors="replace")
                    output.append(text)
                    kept += len(text)

        _spawn_background(drain(proc.stdout))
        _spawn_background(drain(proc.stderr))
        _spawn_background(_complete_login(proc, output))

        logger.info(
            "[Cloudflare] cf auth login spawned (pid=%s) — cf opens the browser itself; awaiting completion in background (timeout=%ss)",
            proc.pid,
            _LOGIN_TIMEOUT_SECONDS,
        )
        return {
            "success": True,
            "message": "cf is opening your default browser — complete the Cloudflare login there.",
        }
    except Exception as e:
        logger.exception("[Cloudflare] login flow raised unexpectedly: %s", e)
        return {"success": False, "error": f"Cloudflare login failed: {e}"}


async def handle_cloudflare_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Single-flight + answer within the frontend's request window no
    matter what (vercel's cold-install pending pattern)."""
    if _login_in_progress():
        logger.info("[Cloudflare] login request ignored — a flow is already in progress")
        return {
            "success": True,
            "pending": True,
            "message": (
                "A Cloudflare login is already in progress — complete it in the browser "
                "window cf opened (it may still be preparing)."
            ),
        }

    logger.info("[Cloudflare] login flow starting (cf auth login)")
    flow = _spawn_background(_start_login_flow())
    _active_login["task"] = flow
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
            "message": "cf CLI is being installed — the browser will open automatically when it is ready.",
        }


async def _complete_login(proc: asyncio.subprocess.Process, output: List[str]) -> None:
    """Await cf's loopback-callback flow; gate success on ``cf auth
    whoami`` (exit codes are not trusted — cf exits 0 either way); then
    the marker + broadcast.

    Never kills the process: the binary is an npm ``.cmd`` shim on
    Windows, and killing the wrapper orphans the node child, which
    keeps holding callback port 8877 and breaks every later login. cf
    enforces its own login timeout and exits by itself.
    """
    try:
        try:
            returncode = await asyncio.wait_for(proc.wait(), timeout=_LOGIN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning(
                "[Cloudflare] login still running after %ss — leaving it to cf's own timeout (killing the shim would orphan the callback server)",
                _LOGIN_TIMEOUT_SECONDS,
            )
            return

        info = await whoami_snapshot()
        if not info:
            tail = "".join(output).strip().splitlines()
            banner = " | ".join(ln.strip() for ln in tail[-5:] if ln.strip()) or "(no output)"
            logger.warning(
                "[Cloudflare] login exited (code=%s) but 'cf auth whoami' reports no session. CLI said: %s",
                returncode,
                banner,
            )
            return

        await _mark_connected(info.get("email"))
    except Exception as e:
        logger.exception("[Cloudflare] login completion raised unexpectedly: %s", e)
    finally:
        if _active_login["proc"] is proc:
            _active_login["proc"] = None


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
