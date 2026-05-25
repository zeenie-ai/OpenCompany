"""Subprocess-owning supervisor.

Subclass for any service that spawns and supervises a long-lived child
process (Go binary, Node sidecar, etc.). One-shot CLI wrappers should
inherit from :class:`BaseSupervisor` directly and use the helpers in
``_supervisor.util`` instead.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from abc import abstractmethod
from pathlib import Path
from typing import Any, Optional

import anyio

from .base import BaseSupervisor
from .util import drain_stream, terminate_then_kill


class BaseProcessSupervisor(BaseSupervisor):
    """A :class:`BaseSupervisor` that owns one ``anyio.abc.Process``."""

    # Inherit stdio (False) is the simplest and matches the WhatsApp
    # default — the child's logs go straight to the backend console.
    # Set True when the subclass wants line-by-line drain into the
    # Python logger (e.g. process_service for log files + broadcast).
    pipe_streams: bool = False

    # Time budget between graceful-stop and forceful tree-kill.
    terminate_grace_seconds: float = 5.0

    # Windows-only: spawn the child in a new process group so we can
    # send CTRL_BREAK_EVENT on shutdown. No-op on POSIX.
    graceful_shutdown: bool = False

    def __init__(self) -> None:
        super().__init__()
        self._proc: Optional[anyio.abc.Process] = None
        self._drain_tasks: list[asyncio.Task] = []

    # ---- subclass surface ------------------------------------------------

    @abstractmethod
    def binary_path(self) -> Path:
        """Locate the binary on disk (shutil.which / env override / etc.)."""

    @abstractmethod
    def argv(self) -> list[str]:
        """Full argv for the child process (binary + flags)."""

    def cwd(self) -> Optional[Path]:
        """Override to set the child's working directory."""
        return None

    def env(self) -> dict[str, str]:
        """Override to add to the child's environment. Defaults to inherit."""
        return {**os.environ}

    async def _pre_spawn(self) -> None:
        """Hook before ``anyio.open_process`` (e.g., write a config file)."""

    # ---- lifecycle implementation ---------------------------------------

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def _do_start(self) -> None:
        # Subclasses that download or otherwise materialise their binary
        # do so in ``_pre_spawn`` (e.g. Temporal's pooch fetch populates
        # ``self._binaries``). Run that first so ``binary_path`` can
        # return the real on-disk location before the existence check.
        await self._pre_spawn()

        binary = self.binary_path()
        if not binary.exists():
            raise FileNotFoundError(f"{self.label} binary not found at {binary}")

        argv = self.argv()
        kwargs: dict[str, Any] = {
            "cwd": str(self.cwd()) if self.cwd() else None,
            "env": self.env(),
        }
        if self.pipe_streams:
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE

        # Windows-only: opt into a new process group for graceful CTRL_BREAK.
        if sys.platform == "win32" and self.graceful_shutdown:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        self._proc = await anyio.open_process(argv, **kwargs)
        self._logger.info(
            "[%s] spawned pid=%s binary=%s",
            self.label,
            self._proc.pid,
            binary,
        )

        if self.pipe_streams:
            self._drain_tasks = [
                asyncio.create_task(
                    drain_stream(self._proc.stdout, self._logger.info, prefix=f"[{self.label}] "),
                ),
                asyncio.create_task(
                    drain_stream(self._proc.stderr, self._logger.error, prefix=f"[{self.label}] "),
                ),
            ]

    async def _do_stop(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        pid = proc.pid
        await terminate_then_kill(
            proc,
            grace=self.terminate_grace_seconds,
            use_ctrl_break=self.graceful_shutdown,
        )
        for task in self._drain_tasks:
            task.cancel()
        self._drain_tasks = []
        self._logger.info("[%s] stopped pid=%s", self.label, pid)

    def _extra_status(self) -> dict[str, Any]:
        return {"pid": self._proc.pid if self._proc else None}
