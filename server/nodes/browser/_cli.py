"""Running one Browser operation through the browser-use CLI.

Each call spawns ``browser-use`` with the operation's script on stdin. The
CLI hands the work to a daemon that stays up per profile and holds its own
CDP connection to the profile's Chrome (``BU_CDP_URL``), so a warm call costs
about 0.3 s. A timeout or interruption stops the CLI and its daemon so an
abandoned script cannot keep issuing input. Already dispatched actions may
still have happened; the caller must inspect the page before acting again.

The environment is built from an allowlist, never inherited, because the CLI
executes scripts and loads plugins from its surroundings:

- no ``.env`` secret, API key or cloud credential reaches it
  (``BROWSER_USE_API_KEY`` would let it start a billed cloud browser);
- telemetry is off (browser-harness otherwise uploads every script and its
  output), the PyPI update check is off, and the tab-title marker is off;
- its home, config, runtime and workspace directories are per profile under
  ``<DATA_DIR>/daemons/browser-use/<profile>/``, so it never reads the user's
  own Chrome profiles, a ``.env`` in the backend's directory, or an
  ``agent_helpers.py`` it did not get from us.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from core.logging import get_logger
from services.plugin.base import NodeUserError

from ._scripts import MARKER, build_script

logger = get_logger(__name__)

_MAX_OUTPUT_BYTES = 4 * 1024 * 1024
#: Environment the CLI may inherit from the backend. Everything else is set
#: explicitly in :meth:`BrowserUseCli.env`.
_PASSTHROUGH = ("PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "TZ")
#: Errors the agent can correct itself.
USER_ERROR_TYPES = frozenset({"stale_ref", "not_found", "script", "policy", "timeout", "cdp"})


class BrowserUnavailable(RuntimeError):
    """The CLI could not reach the profile's Chrome."""


class BrowserStepOutcomeUnknown(NodeUserError):
    """The command's outcome is unknown; it must not be replayed blindly."""


class BrowserStepTimeout(BrowserStepOutcomeUnknown):
    """The command did not return a confirmed result before its deadline."""


@dataclass
class CliResult:
    ok: bool
    value: Any = None
    error_type: Optional[str] = None
    error: Optional[str] = None
    page: Optional[Dict[str, Any]] = None
    output: str = ""
    stderr: str = ""
    exit_code: int = 0
    extra: Dict[str, Any] = field(default_factory=dict)


def parse_output(stdout: str, stderr: str, exit_code: int, nonce: str) -> CliResult:
    """The result line for ``nonce``; the rest of stdout is kept as output."""
    tag = f"{MARKER}:{nonce}:"
    payload = None
    other_lines = []
    for line in stdout.splitlines():
        if line.startswith(tag):
            payload = line[len(tag) :]
        else:
            other_lines.append(line)
    output = "\n".join(other_lines).strip()
    if payload is not None:
        try:
            data = json.loads(payload)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("ok"), bool) and (data["ok"] or isinstance(data.get("error"), dict)):
            error = data.get("error") or {}
            cursor = data.get("cursor")
            return CliResult(
                ok=bool(data.get("ok")),
                value=data.get("value"),
                error_type=error.get("type") if not data.get("ok") else None,
                error=error.get("message") if not data.get("ok") else None,
                page=data.get("page"),
                output=output,
                stderr=stderr,
                exit_code=exit_code,
                # Where an action happened (_scripts.py), for the live view.
                extra={"cursor": cursor} if data.get("ok") and isinstance(cursor, dict) else {},
            )
    low = stderr.lower()
    if payload is None and ("unreachable" in low or "fatal:" in low or "connection refused" in low or "devtools" in low):
        raise BrowserUnavailable(f"the browser could not be reached: {stderr.strip()[-400:]}")
    raise BrowserStepOutcomeUnknown(
        f"The browser step exited {exit_code} without a valid result. Its action may already have happened. "
        f"Take a snapshot before another action. {(stderr or output).strip()[-800:]}"
    )


class BrowserUseCli:
    """The CLI bound to one profile's Chrome."""

    def __init__(self, *, cli_path: Path, python_path: Path, profile_id: str, cdp_http_url: str) -> None:
        self.cli_path = cli_path
        self.python_path = python_path
        self.profile_id = profile_id
        self.cdp_http_url = cdp_http_url
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._interrupt_version = 0

    # -- environment -----------------------------------------------------------

    def dirs(self) -> Dict[str, Path]:
        from core.paths import daemons_dir, safe_path_component

        root = daemons_dir() / "browser-use" / safe_path_component(self.profile_id, fallback="profile")
        from services.browser_owners import settings, replica_id, RUNTIME_EPOCH
        if getattr(settings(), "distributed_mode", False) is True:
            import tempfile
            root = Path(tempfile.gettempdir()) / "opencompany-browser-runtime" / safe_path_component(replica_id()) / RUNTIME_EPOCH / safe_path_component(self.profile_id)
        found = {name: root / name for name in ("home", "runtime", "tmp", "workspace", "config")}
        for path in found.values():
            path.mkdir(parents=True, exist_ok=True)
        return found

    def env(self) -> Dict[str, str]:
        d = self.dirs()
        env = {k: v for k, v in os.environ.items() if k.upper() in _PASSTHROUGH}
        home = str(d["home"])
        env.update(
            {
                "BU_CDP_URL": self.cdp_http_url,
                "BU_NAME": "default",
                "BH_HOME": home,
                "BROWSER_HARNESS_HOME": home,
                "BH_CONFIG_DIR": str(d["config"]),
                "BH_RUNTIME_DIR": str(d["runtime"]),
                "BH_TMP_DIR": str(d["tmp"]),
                "BH_AGENT_WORKSPACE": str(d["workspace"]),
                "BH_TELEMETRY": "0",
                "BROWSER_HARNESS_TELEMETRY": "0",
                "ANONYMIZED_TELEMETRY": "false",
                "BH_UPDATE_CHECK": "0",
                "BH_TAB_MARKER": "0",
                "BH_RECORD": "0",
                "BH_DOMAIN_SKILLS": "0",
                "BROWSER_USE_SETUP_LOGGING": "false",
                "HOME": home,
                "USERPROFILE": home,
                "XDG_CONFIG_HOME": str(d["config"]),
                "XDG_CACHE_HOME": str(d["home"] / "cache"),
                "TEMP": str(d["tmp"]),
                "TMP": str(d["tmp"]),
                "TMPDIR": str(d["tmp"]),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUTF8": "1",
            }
        )
        if sys.platform == "win32":
            # Local-mode fallbacks read the user's Chrome profiles from here.
            env["LOCALAPPDATA"] = home
            env["APPDATA"] = home
        return env

    # -- running -----------------------------------------------------------------

    async def run(self, op: str, args: Dict[str, Any], *, timeout: float) -> CliResult:
        nonce = secrets.token_hex(12)
        script = build_script(op, args, nonce)
        stdout, stderr, code = await self._exec([str(self.cli_path)], script, timeout=timeout)
        result = parse_output(stdout, stderr, code, nonce)
        if not result.ok and result.error_type is None:
            result.error_type = "script"
        return result

    async def _exec(self, argv: list[str], stdin: Optional[str], *, timeout: float) -> tuple[str, str, int]:
        interrupt_version = self._interrupt_version
        d = self.dirs()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(d["workspace"]),
                env=self.env(),
            )
        except FileNotFoundError as exc:
            raise BrowserUnavailable("the browser-use CLI is missing; it reinstalls on the next step") from exc
        self._proc = proc
        try:
            # A takeover can happen while process creation is in flight.
            if interrupt_version != self._interrupt_version:
                await self._kill(proc)
                self.stop_daemon()
                raise BrowserStepOutcomeUnknown("The browser step was interrupted. Its action may already have happened. Take a snapshot before another action.")
            out, err = await asyncio.wait_for(proc.communicate(stdin.encode("utf-8") if stdin is not None else None), timeout=timeout)
            if interrupt_version != self._interrupt_version:
                raise BrowserStepOutcomeUnknown("The browser step was interrupted. Its action may already have happened. Take a snapshot before another action.")
        except asyncio.TimeoutError:
            await self._kill(proc)
            self.stop_daemon()
            raise BrowserStepTimeout(f"The browser step exceeded {int(timeout)} s. Its action may already have happened. Take a snapshot before another action.") from None
        except asyncio.CancelledError:
            await self._kill(proc)
            self.stop_daemon()
            raise
        finally:
            if self._proc is proc:
                self._proc = None
        return (
            out[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"),
            err[-_MAX_OUTPUT_BYTES:].decode("utf-8", errors="replace"),
            proc.returncode if proc.returncode is not None else -1,
        )

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        # The CLI only, not its tree: the daemon it may have spawned is a
        # child too, and outlives every call by design.
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    async def interrupt(self) -> None:
        """Stop the running step (the owner took control)."""
        self._interrupt_version += 1
        proc = self._proc
        if proc is not None:
            await self._kill(proc)
            self.stop_daemon()

    async def suspend_for_credentials(self) -> bool:
        """Confirm the harness is gone before plaintext enters private CDP.

        Never accept an unreadable or unrelated PID as proof of suspension.
        This path receives no secrets and returns no daemon diagnostics.
        """
        import psutil
        if self._proc is not None:
            await self._kill(self._proc)
            if self._proc.returncode is None:
                return False
        pidfile = self.dirs()["runtime"] / "bu.pid"
        if not pidfile.exists():
            return True
        try:
            pid = int(pidfile.read_text().strip())
            process = psutil.Process(pid)
            if "browser_harness" not in " ".join(process.cmdline()).lower():
                return False
            daemon_home = process.environ().get("BH_HOME", "")
            if Path(daemon_home).resolve() != self.dirs()["home"].resolve():
                return False
        except psutil.NoSuchProcess:
            self.stop_daemon()
            return True
        except (OSError, ValueError, psutil.Error):
            return False
        self.stop_daemon()
        try:
            await asyncio.to_thread(process.wait, timeout=5)
        except psutil.NoSuchProcess:
            pass
        except psutil.Error:
            return False
        return not process.is_running()

    async def suspend_prior_epochs(self) -> bool:
        """Retire this machine's old profile daemons before starting new Chrome."""
        from services.browser_owners import settings, RUNTIME_EPOCH
        if getattr(settings(), "distributed_mode", False) is not True:
            return True
        current = self.dirs()["home"].parent
        for epoch in current.parent.parent.iterdir():
            if epoch.name == RUNTIME_EPOCH or len(epoch.name) != 32 or any(c not in "0123456789abcdef" for c in epoch.name):
                continue
            previous = epoch / current.name
            if not (previous / "runtime" / "bu.pid").is_file():
                continue
            old = BrowserUseCli(cli_path=self.cli_path, python_path=self.python_path, profile_id=self.profile_id, cdp_http_url="")
            old.dirs = lambda root=previous: {name: root / name for name in ("home", "runtime", "tmp", "workspace", "config")}
            if not await old.suspend_for_credentials():
                return False
        return True

    async def doctor(self) -> Dict[str, Any]:
        """browser-harness's own health report (``doctor --json``)."""
        stdout, stderr, code = await self._exec(
            [str(self.python_path), "-m", "browser_harness.run", "doctor", "--json", "--require-existing-daemon"], None, timeout=30
        )
        try:
            return json.loads(stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"healthy": False, "exit_code": code, "output": (stdout or stderr).strip()[-800:]}

    def stop_daemon(self) -> None:
        """Kill the profile's daemon (Chrome stopped or restarted under it)."""
        from services._supervisor.util import kill_tree

        pidfile = self.dirs()["runtime"] / "bu.pid"
        try:
            pid = int(pidfile.read_text().strip())
        except (OSError, ValueError):
            return
        # The pid file carries no start time, so make sure the pid still
        # names a browser-harness daemon before killing anything.
        try:
            import psutil

            cmdline = " ".join(psutil.Process(pid).cmdline()).lower()
        except Exception:  # noqa: BLE001 - gone or unreadable
            cmdline = ""
        if "browser_harness" in cmdline:
            kill_tree(pid)
        for name in ("bu.pid", "bu.port"):
            try:
                (self.dirs()["runtime"] / name).unlink()
            except OSError:
                pass


__all__ = ["BrowserStepOutcomeUnknown", "BrowserStepTimeout", "BrowserUnavailable", "BrowserUseCli", "CliResult", "USER_ERROR_TYPES", "parse_output"]
