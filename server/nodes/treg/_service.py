"""How OpenCompany runs the treg CLI.

Every run gets the environment :func:`treg_env` builds:

- ``TREG_CONFIG`` points at ``<DATA_DIR>/treg/config.json``. The CLI keeps
  its sign-in there, apart from the ``~/.treg`` of a ``treg`` the owner
  runs in a terminal.
- Every inherited ``TREG_*`` variable is removed: an inherited ``TREG_TOKEN``
  would override that sign-in, and ``TREG_URL`` / ``TREG_ORG`` would redirect
  it.
- ``TREG_TELEMETRY=0`` and ``DO_NOT_TRACK=1`` turn the CLI's usage analytics
  off.
- ``TREG_CLIENT`` names OpenCompany as the caller. treg otherwise labels a
  call by the agent it detects in the environment ("claude-code" when the
  backend was started from a Claude Code terminal), else "cli".
- ``BROWSER`` is a command that opens nothing. ``treg login`` opens its
  sign-in page with Python's ``webbrowser``, which runs a ``BROWSER``
  command line containing ``%s`` with the URL and stops at its exit code 0.
  The Credentials modal opens the page instead: one tab, and it works when
  OpenCompany runs on another machine.

stdin is a pipe that is closed at once. The CLI then never prompts: on
Windows an inherited ``NUL`` counts as a terminal, which starts treg's
first-run questions after a sign-in.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.events import run_cli_command
from services.process_environment import without_onepassword_environment

#: The caller label treg records for OpenCompany's calls.
CLIENT_LABEL = "opencompany"


def treg_dir() -> Path:
    """``<DATA_DIR>/treg``: the CLI's config and its working directory."""
    from core.paths import data_path

    path = data_path("treg")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _no_browser() -> str:
    """A ``BROWSER`` command line that opens nothing: this Python, told to do nothing with the URL."""
    return f'"{Path(sys.executable).as_posix()}" -c pass %s'


def treg_env() -> Dict[str, str]:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("TREG_")}
    env.update(
        TREG_CONFIG=str(treg_dir() / "config.json"),
        TREG_TELEMETRY="0",
        DO_NOT_TRACK="1",
        TREG_CLIENT=CLIENT_LABEL,
        BROWSER=_no_browser(),
    )
    return without_onepassword_environment(env)


async def run_treg(binary: Path, argv: List[str], *, timeout: float, body: Optional[bytes] = None) -> Dict[str, Any]:
    """``treg --json <argv>`` once, as :func:`services.events.run_cli_command`
    answers it. ``body`` is written to stdin (``--data=@-``); stdin is closed
    after it, or at once."""
    return await run_cli_command(
        binary=str(binary),
        argv=["--json", *argv],
        timeout=timeout,
        env=treg_env(),
        stdin=asyncio.subprocess.PIPE,
        input=body,
        cwd=str(treg_dir()),
    )


async def spawn_treg(binary: Path, argv: List[str]) -> asyncio.subprocess.Process:
    """A long-running ``treg <argv>`` (the sign-in), its stderr merged into stdout."""
    proc = await asyncio.create_subprocess_exec(
        str(binary),
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=treg_env(),
        cwd=str(treg_dir()),
    )
    proc.stdin.close()
    return proc


async def signed_in_account(binary: Path) -> Optional[Dict[str, str]]:
    """``{email, team}`` when the CLI holds a sign-in, else None.

    ``treg config`` only reads the CLI's config file: no request is made, so
    it answers the same with treg.to unreachable. Whether treg still accepts
    the key shows on the next call."""
    result = await run_treg(binary, ["config"], timeout=30.0)
    info = result.get("result")
    if not result.get("success") or not isinstance(info, dict) or not info.get("logged_in"):
        return None
    return {"email": str(info.get("email") or ""), "team": str(info.get("active_org") or "")}


async def verified_account(binary: Path) -> Optional[Dict[str, str]]:
    """``{email, team, role}`` when treg accepts the CLI's sign-in and it names
    a team, else None. ``treg whoami`` makes two free requests."""
    result = await run_treg(binary, ["whoami"], timeout=30.0)
    info = result.get("result")
    if not result.get("success") or not isinstance(info, dict) or not info.get("email") or not info.get("team"):
        return None
    return {"email": str(info["email"]), "team": str(info["team"]), "role": str(info.get("role") or "")}


__all__ = [
    "CLIENT_LABEL",
    "run_treg",
    "signed_in_account",
    "spawn_treg",
    "treg_dir",
    "treg_env",
    "verified_account",
]
