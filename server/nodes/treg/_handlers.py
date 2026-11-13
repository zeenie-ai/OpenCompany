"""treg sign-in, relayed from the treg CLI to the Credentials modal.

The treg CLI owns its sign-in (the GitHub, Cloudflare and Codex pattern).
``treg login`` asks treg.to for a one-time sign-in and prints a link whose
``#code=`` fragment is the code the sign-in page shows. It then polls for
about three minutes until the owner approves in a browser, on this machine
or any other, and saves the team key in its own config file, which
OpenCompany pins under its data folder (``_service.treg_env``). OpenCompany
relays the link and the code, and stores only the ``cli-managed`` marker
behind the modal's connected badge (``services/cli_agent/_cli_auth.py``).

- ``treg_login`` starts one sign-in at a time and answers within a few
  seconds; a repeat click gets the sign-in already running. First use
  installs the CLI before anything else. When the CLI already holds a key
  treg accepts, no sign-in is started.
- ``treg_status`` answers the running sign-in's state, else what the CLI's
  config says (``treg config``, no request), and keeps the marker in step.
- ``treg_logout`` cancels a running sign-in, signs the CLI out and drops the
  marker. treg's ``logout`` forgets the key on this machine only; the key
  itself stays valid until it is revoked on treg.to.

Replies carry ``connected`` and ``pending`` at the top level: the modal polls
the status while ``pending`` is true and reloads the catalogue when
``connected`` is set.

The link is never logged: whoever polls treg.to first after the owner
approves receives the key.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import suppress
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlsplit

from fastapi import WebSocket

from core.logging import get_logger
from services._supervisor.util import kill_tree
from services.cli_agent._cli_auth import broadcast_credential_event, mark_logged_in, mark_logged_out
from services.plugin.base import NodeUserError
from services.plugin.shutdown_hooks import register_shutdown_hook

from ._install import get_installer
from ._service import run_treg, signed_in_account, spawn_treg, verified_account

logger = get_logger(__name__)

PROVIDER = "treg"

#: How long a Login click waits for the link before answering "pending".
_RESPONSE_WAIT_SECONDS = 15.0
#: How long the CLI may take to print its link (it first asks treg.to).
_LINK_WAIT_SECONDS = 30.0
#: The CLI polls for about three minutes; a sign-in nobody finished ends here.
_SIGN_IN_DEADLINE_SECONDS = 240.0
#: A first install; the installer's own timeout ends a stuck one sooner.
_INSTALL_WAIT_SECONDS = 900.0
#: How long a killed CLI may take to exit.
_REAP_SECONDS = 5.0

_LINK_RE = re.compile(r"https?://\S+/login\?\S+")

_login_task: Optional[asyncio.Task] = None
_login_state: Dict[str, Any] = {}


def _in_progress() -> bool:
    return _login_task is not None and not _login_task.done()


def _response() -> Dict[str, Any]:
    return {"success": not _login_state.get("error"), "connected": False, "pending": _in_progress(), **_login_state}


def _signed_in_message(account: Dict[str, str]) -> str:
    team = f" (team {account['team']})" if account.get("team") else ""
    return f"Signed in to treg as {account['email']}{team}."


async def _marked() -> bool:
    from core.container import container

    return await container.auth_service().get_oauth_tokens(PROVIDER) is not None


async def _connect(account: Dict[str, str]) -> None:
    await mark_logged_in(PROVIDER, email=account.get("email") or None, name=account.get("team") or None)
    await broadcast_credential_event("credential.oauth.connected", provider=PROVIDER)


async def _disconnect() -> None:
    await mark_logged_out(PROVIDER)
    await broadcast_credential_event("credential.oauth.disconnected", provider=PROVIDER)


def _code_of(link: str) -> str:
    """The code in the link's ``#code=`` fragment; empty when treg could not
    start a sign-in (the CLI then prints a link without one)."""
    return (parse_qs(urlsplit(link).fragment).get("code") or [""])[0]


async def _read_link(proc: asyncio.subprocess.Process) -> Optional[str]:
    """The sign-in link from the CLI's output; None if it exits without one."""
    while True:
        line = await proc.stdout.readline()
        if not line:
            return None
        found = _LINK_RE.search(line.decode("utf-8", errors="replace"))
        if found:
            return found.group(0)


async def _last_line(proc: asyncio.subprocess.Process) -> str:
    """Read the rest of the output; its last non-empty line."""
    last = ""
    while True:
        line = await proc.stdout.readline()
        if not line:
            return last
        text = line.decode("utf-8", errors="replace").strip()
        if text:
            last = text


async def _browser_sign_in(binary, ready: asyncio.Event) -> Dict[str, str]:
    """Run ``treg login``, relay its link and code, wait for the owner."""
    proc = await spawn_treg(binary, ["login"])
    try:
        try:
            link = await asyncio.wait_for(_read_link(proc), timeout=_LINK_WAIT_SECONDS)
        except asyncio.TimeoutError:
            link = None
        code = _code_of(link) if link else ""
        if not code:
            raise NodeUserError("treg could not start a sign-in. Check that this machine can reach treg.to, then click Login again.")
        _login_state.update(
            url=link,
            verification_code=code,
            message="Sign in on the treg page that opened and check that it shows this code.",
        )
        ready.set()
        try:
            last = await asyncio.wait_for(_last_line(proc), timeout=_SIGN_IN_DEADLINE_SECONDS)
            returncode = await asyncio.wait_for(proc.wait(), timeout=_LINK_WAIT_SECONDS)
        except asyncio.TimeoutError:
            raise NodeUserError("The treg sign-in was not finished in time. Click Login to get a new link.") from None
    finally:
        if proc.returncode is None:
            kill_tree(proc.pid)
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=_REAP_SECONDS)
    if returncode != 0:
        raise NodeUserError(f"treg did not finish the sign-in: {last or f'exit {returncode}'}")
    account = await signed_in_account(binary)
    if not account or not account["email"]:
        raise NodeUserError("treg finished without a sign-in. Click Login to try again.")
    return account


async def _sign_in(ready: asyncio.Event) -> None:
    try:
        installer = get_installer()
        if installer.installed() is None:
            _login_state["message"] = "Installing the treg CLI. This happens once."
        binary = await installer.ensure(wait=_INSTALL_WAIT_SECONDS)
        account = await verified_account(binary) or await _browser_sign_in(binary, ready)
        await _connect(account)
        _login_state.clear()
        _login_state.update(connected=True, message=_signed_in_message(account))
    except asyncio.CancelledError:
        raise
    except NodeUserError as exc:
        _login_state.clear()
        _login_state["error"] = str(exc)
    except Exception as exc:  # noqa: BLE001 - the modal shows a plain error instead
        # Never the exception text: it could carry the CLI's output and with it the link.
        logger.warning("[treg] sign-in failed", error_type=type(exc).__name__)
        _login_state.clear()
        _login_state["error"] = "The treg sign-in could not be completed. Try again."
    finally:
        ready.set()


async def handle_treg_login(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:  # noqa: ARG001
    global _login_task
    if _in_progress():
        return _response()
    _login_state.clear()
    _login_state["message"] = "Preparing the treg sign-in."
    ready = asyncio.Event()
    _login_task = asyncio.create_task(_sign_in(ready), name="treg_login")
    with suppress(asyncio.TimeoutError):
        await asyncio.wait_for(ready.wait(), timeout=_RESPONSE_WAIT_SECONDS)
    return _response()


async def handle_treg_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:  # noqa: ARG001
    if _in_progress() or (_login_state.get("error") and not data.get("refresh")):
        return _response()
    # Any installed version can read its own config: a newer pin, not yet
    # installed, does not sign the owner out.
    binary = get_installer().paths()["cli"]
    account = await signed_in_account(binary) if binary.exists() else None
    marked = await _marked()
    if account and not marked:
        await _connect(account)
    elif not account and marked:
        await _disconnect()
    _login_state.clear()
    if account:
        _login_state.update(connected=True, message=_signed_in_message(account))
    return _response()


async def handle_treg_logout(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:  # noqa: ARG001
    await shutdown_login()
    binary = get_installer().paths()["cli"]
    if binary.exists():
        result = await run_treg(binary, ["logout"], timeout=30.0)
        if not result.get("success"):
            return {"success": False, "connected": False, "pending": False, "error": "treg could not sign out. Try again."}
    await _disconnect()
    return {"success": True, "connected": False, "pending": False}


async def shutdown_login() -> None:
    """Cancel a running sign-in; ending it stops the CLI."""
    global _login_task
    task, _login_task = _login_task, None
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    _login_state.clear()


WS_HANDLERS = {
    "treg_login": handle_treg_login,
    "treg_status": handle_treg_status,
    "treg_logout": handle_treg_logout,
}

register_shutdown_hook("treg_login", shutdown_login)

__all__ = ["WS_HANDLERS", "handle_treg_login", "handle_treg_logout", "handle_treg_status", "shutdown_login"]
