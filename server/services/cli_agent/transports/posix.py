"""POSIX ``PtyTransport`` backed by ``ptyprocess``.

``ptyprocess.PtyProcess.spawn(...)`` wraps ``os.forkpty()`` and returns
a PtyProcess that owns the master fd. The child runs in its own
session (``os.setsid()`` is the default), so SIGTERM-then-SIGKILL on the
child pid reaps the whole TUI process group. We bridge ptyprocess's sync
API to asyncio with ``loop.run_in_executor`` only at the spawn/kill
boundary — writes are single fast syscalls and run on the event-loop
thread.

We do NOT register an ``loop.add_reader`` on the master fd: in the new
architecture the on-disk session JSONL is the protocol surface; PTY
stdout is rendered TUI we never look at. Keeping the read side empty
means no buffering races between fd-readiness and JSONL-watcher events.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time
from pathlib import Path
from typing import Dict, List, Optional

from core.logging import get_logger

from .base import PtyHandle, PtyTransport

logger = get_logger(__name__)

# How long to wait between SIGTERM and SIGKILL when killing a child.
# Matches ``BaseProcessSupervisor.terminate_grace_seconds`` so the two
# lifecycle layers agree on grace.
_TERMINATE_GRACE_SECONDS = 5.0


class PosixPtyHandle:
    """ptyprocess-backed handle for one live ``claude`` subprocess."""

    __slots__ = ("_proc", "_exited")

    def __init__(self, proc: object) -> None:
        # ``proc`` is a ``ptyprocess.PtyProcess`` but we keep it typed
        # as ``object`` so the import remains lazy in :meth:`spawn`.
        self._proc = proc
        self._exited = False

    @property
    def pid(self) -> int:
        return int(getattr(self._proc, "pid", -1))

    def is_alive(self) -> bool:
        if self._exited:
            return False
        try:
            alive = bool(self._proc.isalive())  # type: ignore[attr-defined]
        except Exception:
            return False
        if not alive:
            self._exited = True
        return alive

    async def write(self, data: bytes) -> None:
        # ptyprocess's write() is a single ``os.write`` call; fast
        # enough to run on the event-loop thread.
        if not data:
            return
        try:
            self._proc.write(data)  # type: ignore[attr-defined]
        except OSError as exc:
            # EIO = the slave end closed (process exited). Surface as a
            # clean ConnectionError so callers can distinguish from a
            # genuine permission / FD-leak bug.
            raise ConnectionError(
                f"PTY write failed (pid={self.pid}): {exc}"
            ) from exc

    async def kill(self, signal_: int = signal.SIGTERM) -> None:
        """SIGTERM, wait briefly, escalate to SIGKILL if still alive.

        Matches ``BaseProcessSupervisor.terminate_then_kill`` behaviour
        and the grace window from the existing process-tree supervisor.
        """
        if self._exited:
            return

        loop = asyncio.get_running_loop()

        # First-stage signal. ptyprocess exposes terminate(force=False)
        # for SIGTERM and terminate(force=True) for SIGKILL. We want the
        # SIGTERM-then-SIGKILL cascade with our own grace window so we
        # call signal helpers directly.
        try:
            await loop.run_in_executor(
                None, self._proc.kill, signal_,  # type: ignore[attr-defined]
            )
        except (ProcessLookupError, OSError) as exc:
            logger.debug("PTY first-stage signal raced exit: %s", exc)
            self._exited = True
            return

        # Wait up to grace for graceful exit.
        deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
        while time.monotonic() < deadline:
            if not self.is_alive():
                self._exited = True
                return
            await asyncio.sleep(0.05)

        # Escalate. ``kill(SIGKILL)`` is the documented force-quit path.
        try:
            await loop.run_in_executor(
                None, self._proc.kill, signal.SIGKILL,  # type: ignore[attr-defined]
            )
        except (ProcessLookupError, OSError):
            pass
        self._exited = True

        # Drain the ptyprocess so the OS-level zombie is reaped on next
        # alive-check. No-op if already reaped.
        try:
            await loop.run_in_executor(None, self._proc.close, True)  # type: ignore[attr-defined]
        except Exception:
            pass


class PosixPtyTransport(PtyTransport):
    """Spawn factory using ``ptyprocess``.

    Lazy-imports ``ptyprocess`` so test environments missing the dep
    can still import :mod:`services.cli_agent.transports` (the factory
    only loads this backend on POSIX). Raises ``RuntimeError`` if the
    dep is genuinely missing at spawn time.
    """

    def __init__(self) -> None:
        # Cache the imported class on first use so we don't repeat the
        # try/except on every spawn.
        self._pty_process_cls: Optional[object] = None

    def _load_ptyprocess(self) -> object:
        if self._pty_process_cls is not None:
            return self._pty_process_cls
        try:
            from ptyprocess import PtyProcess  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "PosixPtyTransport requires the 'ptyprocess' package. "
                "Install it (e.g. `pip install ptyprocess>=0.7.0`) — it's "
                "declared in server/pyproject.toml with the marker "
                "`sys_platform != 'win32'`."
            ) from exc
        self._pty_process_cls = PtyProcess
        return PtyProcess

    async def spawn(
        self,
        argv: List[str],
        *,
        cwd: Path,
        env: Dict[str, str],
    ) -> PtyHandle:
        if not argv:
            raise ValueError("PosixPtyTransport.spawn: empty argv")

        PtyProcess = self._load_ptyprocess()  # noqa: N806 — class name

        binary = argv[0]
        if not os.path.exists(binary):
            # Mirror BaseProcessSupervisor's pre-spawn check so the
            # error matches the existing operator-log shape.
            raise FileNotFoundError(f"PTY binary not found: {binary}")

        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: PtyProcess.spawn(  # type: ignore[attr-defined]
                argv,
                cwd=str(cwd),
                env=env,
                # 80x24 default matches what Ink/most TUI libraries
                # expect on a non-terminal-attached spawn. We don't
                # render anywhere, so the dimensions are cosmetic.
                dimensions=(24, 80),
            ),
        )
        logger.info(
            "[PtyTransport posix] spawned pid=%s cwd=%s argv0=%s",
            getattr(proc, "pid", "?"), cwd, binary,
        )
        return PosixPtyHandle(proc)
