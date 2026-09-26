"""Install the pinned browser-use CLI as an isolated uv tool.

``uv tool install browser-use==<pin>`` into ``UV_TOOL_DIR`` /
``UV_TOOL_BIN_DIR``. The desktop app already sets both (next to the Python
it bundles, so uninstalling the app cannot orphan the tool); anywhere else
they default to ``<DATA_DIR>/packages/browser-use/{tools,bin}``. The install
runs as an async subprocess with a timeout, never on the event loop, and a
failure is retried after a cooldown. ``install.json`` records what was
installed; a different pin reinstalls.
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

from ._config import BrowserUsePin, get_config

logger = get_logger(__name__)

_FAILURE_COOLDOWN_SECONDS = 60.0


def _tool_dirs() -> tuple[Path, Path]:
    tool_dir = os.environ.get("UV_TOOL_DIR", "").strip()
    bin_dir = os.environ.get("UV_TOOL_BIN_DIR", "").strip()
    if tool_dir and bin_dir:
        return Path(tool_dir), Path(bin_dir)
    from core.paths import package_dir

    root = package_dir("browser-use")
    return root / "tools", root / "bin"


def _exe_name(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def _base_python() -> str:
    """The interpreter behind the backend's venv (the desktop's bundled one)."""
    return getattr(sys, "_base_executable", None) or sys.executable


class BrowserUseInstaller:
    def __init__(self, pin: Optional[BrowserUsePin] = None) -> None:
        self._pin = pin
        self._task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()
        self.phase = "idle"
        self.error: Optional[str] = None
        self.failed_at = 0.0

    @property
    def pin(self) -> BrowserUsePin:
        return self._pin or get_config().browser_use

    def paths(self) -> Dict[str, Path]:
        tool_dir, bin_dir = _tool_dirs()
        venv = tool_dir / "browser-use"
        python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        return {
            "tool_dir": tool_dir,
            "bin_dir": bin_dir,
            "cli": bin_dir / _exe_name("browser-use"),
            "python": python,
            "stamp": tool_dir / "opencompany-browser-use.json",
        }

    def installed(self) -> Optional[Dict[str, Path]]:
        paths = self.paths()
        if not paths["cli"].exists():
            return None
        try:
            stamp = json.loads(paths["stamp"].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if stamp.get("spec") != self.pin.spec:
            return None
        return paths

    def status(self) -> dict:
        return {"phase": "ready" if self.installed() else self.phase, "version": self.pin.version, "error": self.error}

    async def ensure(self, *, wait: float) -> Dict[str, Path]:
        found = self.installed()
        if found is not None:
            return found
        task = await self._start()
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=max(0.0, wait))
        except asyncio.TimeoutError:
            raise NodeUserError("Installing the browser tools. This happens once; try again in a minute.") from None

    async def _start(self) -> asyncio.Task:
        async with self._lock:
            if self._task is not None and not self._task.done():
                return self._task
            if self.phase == "failed":
                remaining = self.failed_at + _FAILURE_COOLDOWN_SECONDS - time.monotonic()
                if remaining > 0:
                    raise NodeUserError(f"The browser tools failed to install ({self.error}). Retrying in {int(remaining) + 1} s.")
            self._task = asyncio.create_task(self._run(), name="browser-use-install")
            self._task.add_done_callback(lambda t: t.cancelled() or t.exception())
            return self._task

    async def _run(self) -> Dict[str, Path]:
        from core.container import container

        timeout = float(getattr(container.settings(), "browser_install_timeout_seconds", 900) or 900)
        uv = os.environ.get("OPENCOMPANY_UV_BIN", "").strip() or shutil.which("uv")
        if not uv:
            self._fail("uv is not installed")
            raise NodeUserError("The browser tools need uv (https://docs.astral.sh/uv/). Install it and try again.")
        paths = self.paths()
        paths["tool_dir"].mkdir(parents=True, exist_ok=True)
        paths["bin_dir"].mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "UV_TOOL_DIR": str(paths["tool_dir"]), "UV_TOOL_BIN_DIR": str(paths["bin_dir"])}
        # A leftover VIRTUAL_ENV (the backend's) makes uv warn and can pick the wrong interpreter.
        env.pop("VIRTUAL_ENV", None)
        self.phase, self.error = "installing", None
        argv = [uv, "tool", "install", "--reinstall", "--python", _base_python(), self.pin.spec]
        logger.info("[browser] installing %s", self.pin.spec)
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                self._fail(f"timed out after {int(timeout)} s")
                raise NodeUserError(f"Installing the browser tools timed out after {int(timeout)} s; it will be retried.") from None
        except OSError as exc:
            self._fail(str(exc))
            raise NodeUserError(f"The browser tools could not be installed: {exc}") from exc
        if proc.returncode != 0 or not paths["cli"].exists():
            message = (stderr or b"").decode("utf-8", errors="replace").strip()[-500:] or f"uv exited {proc.returncode}"
            self._fail(message)
            logger.warning("[browser] browser-use install failed: %s", message)
            raise NodeUserError(f"The browser tools could not be installed: {message}")
        paths["stamp"].write_text(json.dumps({"spec": self.pin.spec, "python": _base_python()}), encoding="utf-8")
        self.phase = "ready"
        logger.info("[browser] %s installed at %s", self.pin.spec, paths["cli"])
        return paths

    def _fail(self, message: str) -> None:
        self.phase, self.error, self.failed_at = "failed", message, time.monotonic()


_installer: Optional[BrowserUseInstaller] = None


def get_browser_use_installer() -> BrowserUseInstaller:
    global _installer
    if _installer is None:
        _installer = BrowserUseInstaller()
    return _installer


__all__ = ["BrowserUseInstaller", "get_browser_use_installer"]
