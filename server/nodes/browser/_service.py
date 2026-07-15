"""Thin wrapper around agent-browser CLI.

Stateless client — finds the binary via :mod:`nodes.browser._install`,
runs commands via subprocess, parses JSON output. The agent-browser
daemon manages its own lifecycle (auto-starts, persists between
commands).

Invocation strategy
-------------------
agent-browser is a OpenCompany-managed local install (see
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
import os
import queue
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional
from weakref import WeakValueDictionary

from core.logging import get_logger
from nodes.browser._install import agent_browser_binary_path
from services._supervisor.util import kill_tree

logger = get_logger(__name__)

_MAX_OUTPUT = 100_000


async def _to_thread_until_complete(func: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """Run sync work without abandoning it when the waiter is cancelled.

    ``asyncio.to_thread`` cannot stop its worker thread.  If the awaiting task
    is cancelled while it owns a resource lock, releasing that lock would let
    another command overlap the still-running subprocess.  Defer cancellation
    until the worker has finished (the subprocess itself has a hard timeout).
    """
    worker = asyncio.create_task(asyncio.to_thread(func, *args, **kwargs))
    cancellation: Optional[asyncio.CancelledError] = None

    while not worker.done():
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
        except BaseException:  # The worker failed; retrieve it below.
            break

    if cancellation is not None:
        # Retrieve a worker failure so asyncio does not report it as an
        # unhandled task exception; the caller's cancellation remains primary.
        if not worker.cancelled():
            try:
                worker.result()
            except BaseException:
                pass
        raise cancellation

    return worker.result()


def _max_instances() -> int:
    """Concurrent browser-session cap. Canonical value lives in
    .env.template (BROWSER_MAX_INSTANCES); Settings read is wrapped
    defensively because tests stub ``core.config.Settings`` (same
    pattern as ``_default_max_iterations`` in temporal/agent_workflow.py).
    """
    try:
        from core.config import Settings

        value = Settings().browser_max_instances
        # isinstance guard: tests stub Settings with MagicMock, and
        # int(MagicMock()) silently returns 1 instead of raising.
        if isinstance(value, int):
            return value
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(os.environ.get("BROWSER_MAX_INSTANCES") or 3)
    except (TypeError, ValueError):
        return 3


def _idle_timeout_ms() -> int:
    """agent-browser daemon idle auto-shutdown (ms); 0 disables.

    Official mechanism per the agent-browser README ("Architecture"):
    when AGENT_BROWSER_IDLE_TIMEOUT_MS is set, the daemon closes the
    browser and exits after receiving no commands for that duration.
    """
    try:
        from core.config import Settings

        value = Settings().browser_idle_timeout_ms
        if isinstance(value, int):
            return value
    except Exception:  # noqa: BLE001
        pass
    try:
        return int(os.environ.get("BROWSER_IDLE_TIMEOUT_MS") or 600_000)
    except (TypeError, ValueError):
        return 600_000


def _spawn_env() -> Optional[Dict[str, str]]:
    """Env for agent-browser spawns. The daemon inherits this on
    auto-start, so the idle timeout must ride every command that could
    be the one that starts it. None -> inherit unchanged."""
    idle = _idle_timeout_ms()
    if idle <= 0:
        return None
    return {**os.environ, "AGENT_BROWSER_IDLE_TIMEOUT_MS": str(idle)}


class BrowserService:
    """Subprocess wrapper for the agent-browser CLI.

    Holds a frozen argv prefix (typically ``[npx_path, --no-install,
    agent-browser]``) plus the logic to spawn the daemon, read its first
    JSON line, and kill the tree.
    """

    def __init__(self, argv_prefix: List[str]) -> None:
        self._prefix = list(argv_prefix)
        # Sessions already gated by this process — fast-path so the
        # daemon's ``session list`` is queried once per new session, not
        # on every command. The daemon registry stays the authority.
        self._gated_sessions: set = set()
        self._gate_lock = asyncio.Lock()
        # Commands targeting one browser session share mutable tabs, refs and
        # navigation state. Serialize only that session; unrelated sessions
        # continue to execute in parallel.
        self._session_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()

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
        action_delay: float = 0,
    ) -> Dict[str, Any]:
        """Execute an agent-browser command and return parsed JSON output.

        agent-browser outputs JSON on the first stdout line then keeps the
        daemon process alive. We read just the first line via Popen in a
        thread, then kill the process — never wait for exit.
        """
        session_lock = self._session_locks.get(session)
        if session_lock is None:
            session_lock = asyncio.Lock()
            self._session_locks[session] = session_lock
        async with session_lock:
            if action_delay > 0:
                await self._run_locked(
                    args=["wait", str(action_delay)],
                    session=session,
                    timeout=timeout,
                    stdin=None,
                    headed=headed,
                    user_agent=user_agent,
                    proxy=proxy,
                    executable_path=executable_path,
                    auto_connect=auto_connect,
                    chrome_profile=chrome_profile,
                    new_window=new_window,
                )
            return await self._run_locked(
                args=args,
                session=session,
                timeout=timeout,
                stdin=stdin,
                headed=headed,
                user_agent=user_agent,
                proxy=proxy,
                executable_path=executable_path,
                auto_connect=auto_connect,
                chrome_profile=chrome_profile,
                new_window=new_window,
            )

    async def _run_locked(
        self,
        *,
        args: List[str],
        session: str,
        timeout: int,
        stdin: Optional[bytes],
        headed: bool,
        user_agent: Optional[str],
        proxy: Optional[str],
        executable_path: Optional[str],
        auto_connect: bool,
        chrome_profile: Optional[str],
        new_window: bool,
    ) -> Dict[str, Any]:
        """Execute one command while the caller holds ``session``'s lock."""
        await self._enforce_instance_cap(session)

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

        raw = await _to_thread_until_complete(self._run_sync, argv, timeout, stdin)

        if len(raw) > _MAX_OUTPUT:
            raw = raw[:_MAX_OUTPUT] + "\n...(truncated)"

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"output": raw}

    async def _enforce_instance_cap(self, session: str) -> None:
        """Cap concurrent browser instances via agent-browser's own
        primitives: ``session list --json`` (the daemon's authoritative
        registry) and per-session ``close``. One ``--session`` name =
        one browser instance (README "Sessions"); the CLI ships no
        built-in cap, so admission is gated here before a NEW session
        would exceed BROWSER_MAX_INSTANCES.
        """
        if session in self._gated_sessions:
            return
        async with self._gate_lock:
            if session in self._gated_sessions:
                return
            listing = await self._session_cmd(["session", "list"])
            names = [str(s) for s in (listing.get("data") or {}).get("sessions") or []]
            if session not in names:
                cap = _max_instances()
                required = max(0, len(names) - cap + 1)
                closed = 0
                for stale in names:
                    if closed >= required:
                        break
                    stale_lock = self._session_locks.get(stale)
                    if stale_lock is not None and stale_lock.locked():
                        logger.info(
                            "[Browser] instance cap reached; preserving active session %s",
                            stale,
                        )
                        continue
                    logger.info("[Browser] instance cap %d reached; closing session %s", cap, stale)
                    await self._session_cmd(["close", "--session", stale])
                    self._gated_sessions.discard(stale)
                    closed += 1
                if closed < required:
                    logger.warning(
                        "[Browser] instance cap %d temporarily exceeded; "
                        "%d session(s) are active",
                        cap,
                        required - closed,
                    )
            self._gated_sessions.add(session)

    async def _session_cmd(self, args: List[str]) -> Dict[str, Any]:
        """Run a session-management command through the standard
        ``_run_sync`` pipeline. Fails open ({}) — gating must never
        block an actual browser operation.
        """
        try:
            raw = await _to_thread_until_complete(
                self._run_sync,
                [*self._prefix, *args, "--json"],
                15,
                None,
            )
            return json.loads(raw)
        except Exception as e:  # noqa: BLE001
            logger.debug("agent-browser session command failed: args=%s error=%s", args, e)
            return {}

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
            # The daemon inherits this env on auto-start — carries
            # AGENT_BROWSER_IDLE_TIMEOUT_MS (official idle auto-shutdown).
            env=_spawn_env(),
        )

        first_line: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def _read_first_line() -> None:
            try:
                if proc.stdout is None:
                    raise RuntimeError("agent-browser stdout pipe was not created")
                first_line.put((True, proc.stdout.readline()))
            except BaseException as exc:  # Hand the reader failure to caller thread.
                first_line.put((False, exc))

        reader = threading.Thread(
            target=_read_first_line,
            name=f"agent-browser-read-{proc.pid}",
            daemon=True,
        )
        reader.start()

        try:
            if stdin_data and proc.stdin:
                proc.stdin.write(stdin_data)
                proc.stdin.close()

            # Read the first line — that's the JSON result.
            # The daemon keeps the process alive after this, so we must
            # not call communicate() or wait() before killing it.
            try:
                ok, value = first_line.get(timeout=max(float(timeout), 0.001))
            except queue.Empty as exc:
                raise TimeoutError(f"agent-browser timed out after {timeout}s") from exc

            if not ok:
                raise value
            line = value.decode(errors="replace").strip()

            if not line:
                # No output — check stderr for errors.
                # ``read`` is safe only after process exit; otherwise stderr
                # may also be held open by the daemon and would defeat the
                # command timeout we just enforced.
                err = ""
                if proc.poll() is not None and proc.stderr is not None:
                    err = proc.stderr.read().decode(errors="replace").strip()
                raise RuntimeError(err or "agent-browser returned empty output")

            return line
        finally:
            # npx -> node -> agent-browser daemon -> Chromium. Killing only
            # proc.pid leaves the daemon orphaned. psutil.children(recursive=True)
            # walks the tree natively on every platform.
            try:
                kill_tree(proc.pid)
            except Exception as exc:  # Ensure the direct child is still reaped.
                logger.debug("agent-browser tree cleanup failed for pid=%s: %s", proc.pid, exc)
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except OSError:
                    pass
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    logger.warning("agent-browser process pid=%s did not exit after kill", proc.pid)
            reader.join(timeout=1)


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
        svc._gated_sessions.clear()
        logger.info("agent-browser daemon shut down")
    except Exception as e:
        logger.debug("agent-browser shutdown skipped: %s", e)
