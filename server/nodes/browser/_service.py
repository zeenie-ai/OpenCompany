"""Thin wrapper around agent-browser CLI.

Stateless client — finds the binary via :mod:`nodes.browser._install`,
runs commands via subprocess, parses JSON output. The agent-browser
daemon manages its own lifecycle (auto-starts, persists between
commands).

Invocation strategy
-------------------
agent-browser is a MachinaOs-managed local install (see
``nodes/browser/_install.py``) — same pattern as Claude Code's
project-local CLI. The binary lives at
``<package_dir("browser")>/npm/node_modules/.bin/agent-browser[.cmd]``
and is installed via ``npm install agent-browser --prefix <...>`` on
first use. No dependency on the workspace ``package.json`` /
``node_modules`` / pnpm lockfile.

All subprocess calls use ``shell=False`` with list argv — Python handles
Windows .CMD files natively via ``CreateProcessW``, avoiding the BatBadBut
(CVE-2024-1874) argument-escaping vulnerabilities that plague shell=True.
"""

import asyncio
import json
import subprocess
from typing import Any, Dict, List, Optional

from core.logging import get_logger
from nodes.browser._install import agent_browser_binary_path
from services._supervisor.util import kill_tree

logger = get_logger(__name__)

_MAX_OUTPUT = 100_000


class BrowserService:
    """Subprocess wrapper for the agent-browser CLI.

    Holds a frozen argv prefix (typically ``[npx_path, --no-install,
    agent-browser]``) plus the logic to spawn the daemon, read its first
    JSON line, and kill the tree.
    """

    def __init__(self, argv_prefix: List[str]) -> None:
        self._prefix = list(argv_prefix)

    async def run(
        self,
        args: List[str],
        session: str,
        timeout: int = 30,
        stdin: Optional[bytes] = None,
        headed: bool = False,
        user_agent: Optional[str] = None,
        proxy: Optional[str] = None,
        executable_path: Optional[str] = None,
        auto_connect: bool = False,
        chrome_profile: Optional[str] = None,
        new_window: bool = False,
    ) -> Dict[str, Any]:
        """Execute an agent-browser command and return parsed JSON output.

        agent-browser outputs JSON on the first stdout line then keeps the
        daemon process alive. We read just the first line via Popen in a
        thread, then kill the process — never wait for exit.
        """
        argv = [
            *self._prefix,
            "--session",
            session,
            "--json",
        ]
        if headed:
            argv.append("--headed")
        if auto_connect:
            argv.append("--auto-connect")
        if executable_path:
            argv.extend(["--executable-path", executable_path])
        if new_window:
            argv.extend(["--args", "--new-window"])
        if chrome_profile:
            argv.extend(["--profile", chrome_profile])
        if user_agent:
            argv.extend(["--user-agent", user_agent])
        if proxy:
            argv.extend(["--proxy", proxy])
        argv.extend(args)

        logger.debug("agent-browser exec", argv=argv)

        raw = await asyncio.to_thread(self._run_sync, argv, timeout, stdin)

        if len(raw) > _MAX_OUTPUT:
            raw = raw[:_MAX_OUTPUT] + "\n...(truncated)"

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"output": raw}

    @staticmethod
    def _run_sync(
        argv: List[str],
        timeout: int,
        stdin_data: Optional[bytes],
    ) -> str:
        """Spawn agent-browser, read first JSON line, kill the process tree.

        The daemon holds stdout open after emitting its result, so we cannot
        use communicate() or wait() — either would hang forever. Instead we
        readline() and then force-kill the tree.

        Uses ``shell=False`` unconditionally with a list argv. This is safe
        on every platform including .CMD files on Windows (handled natively
        by CreateProcessW since Python 3.7, hardened against BatBadBut in 3.12+).
        """
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin_data else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )

        try:
            if stdin_data and proc.stdin:
                proc.stdin.write(stdin_data)
                proc.stdin.close()

            # Read the first line — that's the JSON result.
            # The daemon keeps the process alive after this, so we must
            # not call communicate() or wait() before killing it.
            line = proc.stdout.readline().decode(errors="replace").strip()

            if not line:
                # No output — check stderr for errors.
                err = proc.stderr.read().decode(errors="replace").strip()
                raise RuntimeError(err or "agent-browser returned empty output")

            return line
        finally:
            # npx -> node -> agent-browser daemon -> Chromium. Killing only
            # proc.pid leaves the daemon orphaned. psutil.children(recursive=True)
            # walks the tree natively on every platform.
            kill_tree(proc.pid)
            proc.wait()


# -- Module-level lazy singleton (NodeJSClient pattern) -----------------

_instance: Optional[BrowserService] = None


def _find_agent_browser_cmd() -> Optional[List[str]]:
    """Resolve agent-browser via the local-install helper.

    Calls :func:`nodes.browser._install.agent_browser_binary_path`,
    which installs the npm package into :func:`core.paths.package_dir`
    on first use (mirroring the claude_code_agent precedent). Returns
    None when ``npm`` is unavailable (Node toolchain missing).
    """
    binary = agent_browser_binary_path()
    return [binary] if binary else None


def get_browser_service() -> Optional[BrowserService]:
    """Return the BrowserService singleton, or None if agent-browser cannot be located."""
    global _instance
    if _instance is None:
        cmd = _find_agent_browser_cmd()
        if cmd:
            _instance = BrowserService(cmd)
    return _instance


async def shutdown_browser_service() -> None:
    """Close all browser sessions via `agent-browser close --all`.

    Called during FastAPI lifespan shutdown. This is the daemon's intended
    cleanup API -- it stops the background process and releases file locks.
    """
    svc = get_browser_service()
    if not svc:
        return
    try:
        await asyncio.to_thread(
            subprocess.run,
            [*svc._prefix, "close", "--all"],
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        logger.info("agent-browser daemon shut down")
    except Exception as e:
        logger.debug("agent-browser shutdown skipped: %s", e)
