"""GitHub WebSocket handlers — the gh CLI owns its own auth (Stripe
CLI pattern).

``github_login`` spawns gh's official browser login::

    gh auth login --hostname github.com --git-protocol https --web

Source-verified behavior (gh ``internal/authflow/flow.go``): with no
TTY the flow takes the ``isInteractive=false`` branch — no "Press
Enter" block — and prints to **stderr**::

    ! First copy your one-time code: XXXX-XXXX
    Open this URL to continue in your web browser: https://github.com/login/device

then polls GitHub until the user authorises. The handler parses those
two lines, returns ``{success, url, verification_code}`` (the modal
opens the url and displays the code), and a background task awaits the
poll. Success gate: ``gh auth status`` exit 0. On success we run the
official ``gh auth setup-git`` bridge (configures git to use gh as
credential helper — the future git node needs zero auth code), fetch
the account identity via ``gh api user`` (so the modal shows
"Connected as <login>" through the catalogue's ``account_label``, same
as Claude Code), write the synthetic ``cli-managed`` marker OAuth row
(flips the catalogue's ``stored`` badge), and broadcast the generic
catalogue-invalidation event. Marker + broadcast plumbing is the
shared :mod:`services.cli_agent._cli_auth` module (claude/codex/github
all use it).

OpenCompany never stores or reads the actual token — it stays in gh's
system credential store.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

from fastapi import WebSocket

from core.logging import get_logger
from services.cli_agent._cli_auth import broadcast_credential_event, mark_logged_in, mark_logged_out
from services.events import run_cli_command

from ._install import ensure_gh_cli
from ._service import cli_logged_in, login_env

logger = get_logger(__name__)

_LOGIN_ARGS = ["auth", "login", "--hostname", "github.com", "--git-protocol", "https", "--web"]
_LOGIN_TIMEOUT_SECONDS = 600
# How long to wait for the one-time code / device URL on the CLI's
# output before giving up on this attempt.
_BANNER_DEADLINE_SECONDS = 20
# The frontend drops WS requests after 30s. A first-ever login pays a
# cold gh download inside this handler — answer within this budget no
# matter what and let the flow continue in the background.
_RESPONSE_BUDGET_SECONDS = 22

# Source-verified banner shapes (authflow/flow.go format strings).
_CODE_RE = re.compile(r"one-time code:\s*([A-Z0-9]{4}-[A-Z0-9]{4})")
_URL_RE = re.compile(r"https://\S*?/login/device\S*")


async def _fetch_account(binary: str) -> Tuple[Optional[str], Optional[str]]:
    """``(login, name)`` of the authenticated user via ``gh api user``
    — feeds the catalogue's ``account_label`` ("Connected as <login>",
    same surface Claude Code uses). Best-effort: ``(None, None)`` on
    any failure."""
    result = await run_cli_command(
        binary=binary,
        argv=["api", "user"],
        timeout=15.0,
        env=login_env(),
    )
    info = result.get("result")
    if not (result.get("success") and isinstance(info, dict)):
        logger.debug("[GitHub] account fetch failed (non-fatal): %s", result.get("error"))
        return None, None
    return info.get("login"), info.get("name")


# Strong refs for fire-and-forget tasks — asyncio holds only weak refs
# (the documented discard-set pattern from the asyncio docs).
_background_tasks: set = set()


def _spawn_background(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def parse_login_banner(text: str) -> Optional[tuple[str, Optional[str]]]:
    """``(device_url, one_time_code)`` once the URL has appeared in the
    accumulated output; ``None`` while still waiting. The code is
    printed BEFORE the URL (flow.go order), so when the URL is present
    the code — if any — is already in the buffer."""
    url_m = _URL_RE.search(text)
    if not url_m:
        return None
    code_m = _CODE_RE.search(text)
    return url_m.group(0).rstrip(".,;)'\""), code_m.group(1) if code_m else None


async def _read_login_banner(proc: asyncio.subprocess.Process) -> tuple[Optional[tuple[str, Optional[str]]], List[str]]:
    """Read the login process's early output until the device URL shows
    up (or deadline / EOF). Chunk-based, not ``readline()`` — spinner
    frames repaint via ``\\r`` with no newline. The pumps deliberately
    OUTLIVE this function: they keep draining both pipes for the
    process lifetime so gh never blocks on a full pipe buffer during
    its ~15-minute poll; only the first 32 KiB is retained for parsing."""
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

    parsed: Optional[tuple[str, Optional[str]]] = None
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _BANNER_DEADLINE_SECONDS
    while True:
        parsed = parse_login_banner("".join(buf))
        if parsed is not None or all(t.done() for t in pumps):
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
    return parsed, tail


async def _start_login_flow() -> Dict[str, Any]:
    """Install (if needed) + spawn gh's browser login + parse the
    banner + schedule completion. Never raises — returns the WS
    response dict."""
    try:
        try:
            binary = str(await ensure_gh_cli())
        except Exception as e:
            logger.warning("[GitHub] gh CLI install failed: %s", e)
            return {
                "success": False,
                "error": f"gh CLI install failed ({e}). Manual install: https://cli.github.com",
            }

        # stdin=PIPE left un-written: headless gh skips the Press-Enter
        # prompt entirely (isInteractive=false branch), but a pipe keeps
        # any stray stdin read from seeing instant EOF.
        proc = await asyncio.create_subprocess_exec(
            binary,
            *_LOGIN_ARGS,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=login_env(),
        )

        parsed, lines = await _read_login_banner(proc)
        if parsed is None:
            proc.kill()
            banner = " | ".join(lines[-5:]) or "(no output)"
            logger.warning("[GitHub] login banner had no device URL within %ss: %s", _BANNER_DEADLINE_SECONDS, banner)
            return {
                "success": False,
                "error": (
                    f"Could not start gh's browser login. CLI said: {banner} — "
                    "you can also run 'gh auth login' in a terminal on this machine."
                ),
            }

        url, code = parsed
        logger.info(
            "[GitHub] device-flow URL issued (code=%s) — opening on frontend; awaiting browser confirmation in background (timeout=%ss)",
            code,
            _LOGIN_TIMEOUT_SECONDS,
        )
        _spawn_background(_complete_login(proc))
        return {"success": True, "url": url, "verification_code": code}
    except Exception as e:
        logger.exception("[GitHub] login flow raised unexpectedly: %s", e)
        return {"success": False, "error": f"GitHub login failed: {e}"}


async def handle_github_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Answer within the frontend's request window no matter what
    (vercel's cold-install pending pattern)."""
    logger.info("[GitHub] login flow starting (gh auth login --web)")
    flow = _spawn_background(_start_login_flow())
    try:
        return await asyncio.wait_for(asyncio.shield(flow), timeout=_RESPONSE_BUDGET_SECONDS)
    except asyncio.TimeoutError:
        logger.info(
            "[GitHub] login still preparing after %ss (cold gh download) — continuing in background",
            _RESPONSE_BUDGET_SECONDS,
        )
        return {
            "success": True,
            "pending": True,
            "message": "gh CLI is being installed — click Login again in a few seconds.",
        }


async def _complete_login(proc: asyncio.subprocess.Process) -> None:
    """Await gh's device-flow poll; gate success on ``gh auth status``
    (exit codes are not trusted alone — Stripe precedent); then the
    official git bridge + marker + broadcast."""
    try:
        try:
            returncode = await asyncio.wait_for(proc.wait(), timeout=_LOGIN_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            logger.warning("[GitHub] login timed out after %ss — user never completed the browser flow", _LOGIN_TIMEOUT_SECONDS)
            return
    except Exception as e:
        logger.exception("[GitHub] login wait raised unexpectedly: %s", e)
        return

    if not await cli_logged_in():
        logger.warning(
            "[GitHub] login exited (code=%s) but 'gh auth status' reports no session — user likely closed the browser before authorising",
            returncode,
        )
        return

    logger.info("[GitHub] auth successful — gh holds the credential; configuring git bridge + catalogue marker")

    login = name = None
    from ._service import resolve_gh_light

    binary = resolve_gh_light()
    if binary:
        # Official one-shot: configures git to use gh as credential helper
        # for authenticated hosts. Best-effort — github ops work without it.
        setup = await run_cli_command(binary=binary, argv=["auth", "setup-git"], timeout=30.0, env=login_env())
        if setup.get("success"):
            logger.info("[GitHub] 'gh auth setup-git' configured git's credential helper")
        else:
            logger.warning("[GitHub] 'gh auth setup-git' failed (non-fatal): %s", setup.get("error"))
        login, name = await _fetch_account(binary)

    await mark_logged_in("github", email=login, name=name)
    logger.info("[GitHub] connected as %s — catalogue marker persisted", login or "<unknown>")
    await broadcast_credential_event("credential.oauth.connected", provider="github")


async def handle_github_logout(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """``gh auth logout`` (best-effort), drop the catalogue marker,
    broadcast so the modal flips immediately."""
    logger.info("[GitHub] logout starting")
    from ._service import resolve_gh_light

    binary = resolve_gh_light()
    result: Dict[str, Any] = {"success": True}
    if binary:
        result = await run_cli_command(
            binary=binary,
            argv=["auth", "logout", "--hostname", "github.com"],
            timeout=15.0,
            env=login_env(),
        )
        if not result.get("success"):
            logger.warning("[GitHub] 'gh auth logout' failed (marker still removed): %s", result.get("error"))
            result = {"success": True, "message": "gh reported no stored session; catalogue marker removed"}
    await mark_logged_out("github")
    await broadcast_credential_event("credential.oauth.disconnected", provider="github")
    logger.info("[GitHub] logout complete: marker removed + catalogue broadcast sent")
    return result


async def handle_github_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Session snapshot straight from the CLI — no side effects."""
    connected = await cli_logged_in()
    return {"success": True, "status": {"connected": connected, "logged_in": connected}}


WS_HANDLERS = {
    "github_login": handle_github_login,
    "github_logout": handle_github_logout,
    "github_status": handle_github_status,
}
