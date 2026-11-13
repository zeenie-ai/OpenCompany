"""Install the pinned treg CLI as an isolated uv tool.

treg's command-line tool is the PyPI package ``tools-registry`` (command
``treg``). ``uv tool install tools-registry==<pin>`` puts it in
``UV_TOOL_DIR`` / ``UV_TOOL_BIN_DIR`` when the desktop app sets both (next
to the Python it bundles, so uninstalling the app cannot orphan the tool),
and otherwise in ``<DATA_DIR>/packages/treg/{tools,bin}``. Never in uv's
default folders, so a ``treg`` the owner installed in a terminal is never
replaced.

Adapted from ``nodes/browser/_install_bu.py`` (a plugin does not import
another plugin's folder). The install runs as an async subprocess with a
timeout, never on the event loop; concurrent callers share one install; a
failure is retried after a cooldown. ``opencompany-treg.json`` in the tool
folder records what was installed, and a different pin reinstalls.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, Optional

from core.logging import get_logger
from services.plugin.base import NodeUserError
from services.process_environment import without_onepassword_environment

logger = get_logger(__name__)

#: The treg CLI OpenCompany runs. Bump it by hand, after checking the new
#: release's ``--json`` output and sign-in against ``_service`` and
#: ``_handlers``.
SPEC = "tools-registry==0.30.0"

_INSTALL_TIMEOUT_SECONDS = 600.0
_FAILURE_COOLDOWN_SECONDS = 60.0


def _tool_dirs() -> tuple[Path, Path]:
    tool_dir = os.environ.get("UV_TOOL_DIR", "").strip()
    bin_dir = os.environ.get("UV_TOOL_BIN_DIR", "").strip()
    if tool_dir and bin_dir:
        return Path(tool_dir), Path(bin_dir)
    from core.paths import package_dir

    root = package_dir("treg")
    return root / "tools", root / "bin"


def _python() -> str:
    """The interpreter uv installs the CLI for: the one behind the backend's
    venv when the CLI supports it (it needs Python 3.12 or 3.13), else that
    range, for uv to find one."""
    if (3, 12) <= sys.version_info[:2] < (3, 14):
        return getattr(sys, "_base_executable", None) or sys.executable
    return ">=3.12,<3.14"


class TregInstaller:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.phase = "idle"
        self.error: Optional[str] = None
        self.failed_at = 0.0

    def paths(self) -> Dict[str, Path]:
        tool_dir, bin_dir = _tool_dirs()
        return {
            "tool_dir": tool_dir,
            "bin_dir": bin_dir,
            "cli": bin_dir / ("treg.exe" if sys.platform == "win32" else "treg"),
            "stamp": tool_dir / "opencompany-treg.json",
        }

    def installed(self) -> Optional[Path]:
        """The CLI's path when the pinned version is installed, else None."""
        paths = self.paths()
        if not paths["cli"].exists():
            return None
        try:
            stamp = json.loads(paths["stamp"].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return paths["cli"] if stamp.get("spec") == SPEC else None

    async def ensure(self, *, wait: float) -> Path:
        """The pinned CLI, installing it first when needed. Waits at most
        ``wait`` seconds for an install; the install itself carries on."""
        found = self.installed()
        if found is not None:
            return found
        task = await self._start()
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, wait))
        except asyncio.TimeoutError:
            raise NodeUserError("Installing the treg CLI. This happens once; try again in a minute.") from None

    async def _start(self) -> asyncio.Task:
        async with self._lock:
            if self._task is not None and not self._task.done():
                return self._task
            if self.phase == "failed":
                remaining = self.failed_at + _FAILURE_COOLDOWN_SECONDS - time.monotonic()
                if remaining > 0:
                    raise NodeUserError(f"The treg CLI failed to install ({self.error}). Retrying in {int(remaining) + 1} s.")
            self._task = asyncio.create_task(self._run(), name="treg-cli-install")
            self._task.add_done_callback(lambda t: t.cancelled() or t.exception())
            return self._task

    async def _run(self) -> Path:
        uv = os.environ.get("OPENCOMPANY_UV_BIN", "").strip() or shutil.which("uv")
        if not uv:
            self._fail("uv is not installed")
            raise NodeUserError(
                "treg's CLI is installed with uv (https://docs.astral.sh/uv/), which this machine does not have. "
                "Install uv and try again.",
                requires_user_action=True,
            )
        paths = self.paths()
        paths["tool_dir"].mkdir(parents=True, exist_ok=True)
        paths["bin_dir"].mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "UV_TOOL_DIR": str(paths["tool_dir"]), "UV_TOOL_BIN_DIR": str(paths["bin_dir"])}
        # A leftover VIRTUAL_ENV (the backend's) makes uv warn and can pick the wrong interpreter.
        env.pop("VIRTUAL_ENV", None)
        self.phase, self.error = "installing", None
        argv = [uv, "tool", "install", "--reinstall", "--python", _python(), SPEC]
        logger.info("[treg] installing %s", SPEC)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, env=without_onepassword_environment(env), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=_INSTALL_TIMEOUT_SECONDS)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                self._fail(f"timed out after {int(_INSTALL_TIMEOUT_SECONDS)} s")
                raise NodeUserError(
                    f"Installing the treg CLI timed out after {int(_INSTALL_TIMEOUT_SECONDS)} s; it will be retried."
                ) from None
        except OSError as exc:
            self._fail(str(exc))
            raise NodeUserError(f"The treg CLI could not be installed: {exc}") from exc
        if proc.returncode != 0 or not paths["cli"].exists():
            message = (stderr or b"").decode("utf-8", errors="replace").strip()[-500:] or f"uv exited {proc.returncode}"
            self._fail(message)
            logger.warning("[treg] treg CLI install failed: %s", message)
            raise NodeUserError(f"The treg CLI could not be installed: {message}")
        paths["stamp"].write_text(json.dumps({"spec": SPEC, "python": _python()}), encoding="utf-8")
        self.phase = "ready"
        logger.info("[treg] %s installed at %s", SPEC, paths["cli"])
        return paths["cli"]

    def _fail(self, message: str) -> None:
        self.phase, self.error, self.failed_at = "failed", message, time.monotonic()


_installer: Optional[TregInstaller] = None


def get_installer() -> TregInstaller:
    global _installer
    if _installer is None:
        _installer = TregInstaller()
    return _installer


__all__ = ["SPEC", "TregInstaller", "get_installer"]
