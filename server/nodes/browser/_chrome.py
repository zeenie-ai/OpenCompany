"""Launching and stopping one profile's Chrome.

Chrome runs ``--headless=new`` (full Chrome, not the old headless shell)
with its own ``--user-data-dir``, a debugging port chosen by the OS
(``--remote-debugging-port=0``; the real port is read back from the
``DevToolsActivePort`` file), and every request routed through the egress
proxy. The user sees it only through the in-app live view.

Never passed: ``--remote-allow-origins=*`` (it would let web pages talk to
the debugging port), ``--enable-automation`` (the "controlled by automated
software" banner and ``navigator.webdriver``), or any debugging address other
than 127.0.0.1.

The process is supervised by :class:`ChromeProcess`. Only one Chrome may
open a profile: a lock file held for the process lifetime keeps a second
backend (the desktop app and a CLI install sharing a data folder) out, and a
pidfile with the process start time lets the next start remove a Chrome
orphaned by a crash without ever killing an unrelated process that reused
the pid. Stopping asks Chrome to close over CDP first so it flushes cookies
to disk, and only then terminates it.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.logging import get_logger
from services._supervisor import BaseProcessSupervisor, register_supervisor
from services._supervisor.util import kill_tree
from services.plugin.base import NodeUserError

from ._cdp import CDPConnection, CDPDisconnected, CDPError, read_devtools_active_port

logger = get_logger(__name__)

#: The fixed page viewport. The agent's coordinates and the live view's
#: coordinate mapping both assume it, so a viewer resizing never re-lays-out
#: the page under the agent.
VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 800

#: Chrome features switched on for every profile (WebMCP's CDP domain).
ENABLED_FEATURES = ("WebMCP",)

_READY_TIMEOUT = 25.0
_EARLY_EXIT_SECONDS = 3.0

#: Environment Chrome may inherit. Everything else (API keys from .env,
#: SECRET_KEY, cloud credentials) stays out of the browser's processes.
_ENV_ALLOWLIST = (
    "PATH",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "TZ",
    "DISPLAY",
    "XDG_RUNTIME_DIR",
    "FONTCONFIG_PATH",
    "FONTCONFIG_FILE",
)


def user_agent(major: int, platform: Optional[str] = None) -> str:
    """The reduced user-agent a normal Chrome of this version sends.

    Headless Chrome announces itself as ``HeadlessChrome``, which many sites
    block outright.
    """
    platform = platform or sys.platform
    if platform == "win32":
        os_part = "Windows NT 10.0; Win64; x64"
    elif platform == "darwin":
        os_part = "Macintosh; Intel Mac OS X 10_15_7"
    else:
        os_part = "X11; Linux x86_64"
    return f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"


def build_chrome_argv(
    exe: Path,
    *,
    user_data_dir: Path,
    proxy_port: int,
    major: int,
    no_sandbox: bool,
    small_shm: bool,
    platform: Optional[str] = None,
    downloads_dir: Optional[Path] = None,
) -> List[str]:
    platform = platform or sys.platform
    argv = [
        str(exe),
        f"--user-data-dir={user_data_dir}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=0",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-sync",
        "--disable-component-update",
        "--no-pings",
        "--hide-crash-restore-bubble",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        "--disable-background-timer-throttling",
        f"--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}",
        f"--user-agent={user_agent(major, platform)}",
        f"--proxy-server=http://127.0.0.1:{proxy_port}",
        # Chrome sends loopback requests direct by default; this removes that
        # rule so localhost goes through the egress proxy's policy too.
        "--proxy-bypass-list=<-loopback>",
        # No path around the proxy: QUIC and WebRTC's UDP are not proxied.
        "--disable-quic",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
        "--enable-features=" + ",".join(ENABLED_FEATURES),
    ]
    if platform.startswith("linux"):
        argv.append("--password-store=basic")
    elif platform == "darwin":
        argv.append("--use-mock-keychain")
    if no_sandbox:
        argv.append("--no-sandbox")
    if small_shm:
        argv.append("--disable-dev-shm-usage")
    argv.append("about:blank")
    return argv


def chrome_env() -> Dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() in _ENV_ALLOWLIST}


class ProfileLock:
    """An OS-level exclusive lock on ``<profile>/oc.lock``.

    Released by the OS if the process dies, so a crash never leaves a
    profile locked.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._fh: Any = None

    def acquire(self) -> bool:
        fh = open(self.path, "a+b")
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            fh.close()


def _read_pidfile(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sweep_orphan(pidfile: Path) -> bool:
    """Kill a Chrome a previous backend left running, if it is still that one.

    Returns ``True`` if something was killed. A pid is only trusted when the
    running process's start time matches the recorded one.
    """
    data = _read_pidfile(pidfile)
    if not data:
        return False
    try:
        import psutil

        pid = int(data.get("pid") or 0)
        recorded = float(data.get("create_time") or 0)
        proc = psutil.Process(pid)
        if abs(proc.create_time() - recorded) > 1.0:
            return False
    except Exception:  # noqa: BLE001 - gone, reused or unreadable: nothing to do
        try:
            pidfile.unlink()
        except OSError:
            pass
        return False
    kill_tree(pid)
    try:
        pidfile.unlink()
    except OSError:
        pass
    logger.info("[browser] removed a Chrome left running by an earlier backend (pid %s)", pid)
    return True


class ChromeProcess(BaseProcessSupervisor):
    """One profile's Chrome. Constructed per profile, never a singleton."""

    pipe_streams = True
    terminate_grace_seconds = 5.0

    def __init__(
        self,
        *,
        profile_id: str,
        exe: Path,
        profile_root: Path,
        user_data_dir: Path,
        proxy_port: int,
        major: int,
        no_sandbox: bool,
        small_shm: bool,
    ) -> None:
        super().__init__()
        self.name = f"browser-chrome:{profile_id}"
        self.profile_id = profile_id
        self._exe = exe
        self._root = profile_root
        self._user_data_dir = user_data_dir
        self._proxy_port = proxy_port
        self._major = major
        self._no_sandbox = no_sandbox
        self._small_shm = small_shm
        self._profile_lock = ProfileLock(profile_root / "oc.lock")
        self._locked = False
        self.port: Optional[int] = None
        self.ws_url: Optional[str] = None
        self.connection: Optional[CDPConnection] = None

    @property
    def pidfile(self) -> Path:
        return self._root / "oc-chrome.json"

    # -- BaseProcessSupervisor surface ---------------------------------------

    def binary_path(self) -> Path:
        return self._exe

    def argv(self) -> List[str]:
        return build_chrome_argv(
            self._exe,
            user_data_dir=self._user_data_dir,
            proxy_port=self._proxy_port,
            major=self._major,
            no_sandbox=self._no_sandbox,
            small_shm=self._small_shm,
        )

    def env(self) -> Dict[str, str]:
        return chrome_env()

    def stdout_log(self, line: str) -> None:
        self._logger.debug(line)

    def stderr_log(self, line: str) -> None:
        # Chrome is chatty on stderr (GPU, dbus, updater); none of it is an error of ours.
        self._logger.debug(line)

    async def _pre_spawn(self) -> None:
        sweep_orphan(self.pidfile)
        if not self._profile_lock.acquire():
            raise NodeUserError(
                "This browser profile is open in another OpenCompany (for example the desktop app and a terminal "
                "install sharing a data folder). Close it there, or use another profile."
            )
        self._locked = True
        try:
            (self._user_data_dir / "DevToolsActivePort").unlink()
        except OSError:
            pass

    # -- lifecycle -------------------------------------------------------------

    async def launch(self) -> CDPConnection:
        """Start Chrome, wait for its debugging port, connect over CDP."""
        register_supervisor(self)
        try:
            await self.start()
            await self._wait_ready()
        except BaseException:
            await self._abort()
            raise
        self._write_pidfile()
        return self.connection  # type: ignore[return-value]

    async def _wait_ready(self) -> None:
        started = time.monotonic()
        deadline = started + _READY_TIMEOUT
        while time.monotonic() < deadline:
            if not self.is_running():
                elapsed = time.monotonic() - started
                hint = " Another Chrome may have this profile open." if elapsed < _EARLY_EXIT_SECONDS else ""
                raise NodeUserError(f"Chrome exited while starting.{hint} Run the browser 'diagnose' operation for details.")
            found = read_devtools_active_port(self._user_data_dir)
            if found:
                self.port, path = found
                self.ws_url = f"ws://127.0.0.1:{self.port}{path}"
                try:
                    self.connection = await CDPConnection.connect(self.ws_url)
                    return
                except OSError:
                    pass
            await asyncio.sleep(0.1)
        raise NodeUserError(f"Chrome did not open its debugging port within {int(_READY_TIMEOUT)} s.")

    def _write_pidfile(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            import psutil

            create_time = psutil.Process(proc.pid).create_time()
        except Exception:  # noqa: BLE001
            create_time = 0.0
        try:
            self.pidfile.write_text(json.dumps({"pid": proc.pid, "create_time": create_time, "port": self.port}), encoding="utf-8")
        except OSError:
            pass

    async def _abort(self) -> None:
        try:
            await self.stop()
        finally:
            self._release()

    def _release(self) -> None:
        if self._locked:
            self._profile_lock.release()
            self._locked = False

    async def shutdown(self, *, grace: float = 3.0) -> None:
        """Ask Chrome to close (flushing cookies), then make sure it is gone."""
        connection = self.connection
        self.connection = None
        if connection is not None and not connection.is_closed:
            try:
                await connection.send("Browser.close", timeout=grace)
            except (CDPError, CDPDisconnected, TimeoutError, OSError):
                pass
            proc = self._proc
            if proc is not None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=grace)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass
            await connection.close()
        try:
            await self.stop()
        finally:
            try:
                self.pidfile.unlink()
            except OSError:
                pass
            self._release()

    async def version_info(self) -> Dict[str, Any]:
        if self.connection is None:
            return {}
        return await self.connection.send("Browser.getVersion")


__all__ = [
    "ChromeProcess",
    "ENABLED_FEATURES",
    "ProfileLock",
    "VIEWPORT_HEIGHT",
    "VIEWPORT_WIDTH",
    "build_chrome_argv",
    "chrome_env",
    "sweep_orphan",
    "user_agent",
]
