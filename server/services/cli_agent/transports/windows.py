"""Windows ``PtyTransport`` backed by ``pywinpty>=3.0.3``.

``pywinpty`` v3.x is Rust-backed via PyO3 + winpty-rs + Maturin. Mirrors
ptyprocess's high-level API closely (``PtyProcess.spawn(argv, cwd=,
env=, dimensions=)``, ``write``, ``isalive``, ``kill``, ``close``) so
this file's shape mirrors :mod:`.posix`. The pywinpty maintainers ship
prebuilt wheels for Python 3.9-3.14 including free-threading, so
end-users don't need a C++ build toolchain.

Used in production by Jupyter Lab terminals and Spyder's IPython
console — same production maturity bar as ptyprocess on POSIX. See
the upgrade-path note in :mod:`services.cli_agent.transports` for the
out-of-process pty-host fallback if pywinpty stability ever degrades.
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

# Match POSIX backend's grace window so the two lifecycle layers agree.
_TERMINATE_GRACE_SECONDS = 5.0


class WindowsPtyHandle:
    """pywinpty-backed handle for one live ``claude`` subprocess."""

    __slots__ = ("_proc", "_exited")

    def __init__(self, proc: object) -> None:
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
        if not data:
            return
        # pywinpty's PtyProcess.write accepts bytes; defer the actual
        # WriteFile to a worker thread because ConPTY can briefly block
        # under back-pressure (https://github.com/microsoft/node-pty/issues/388).
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, self._proc.write, data,  # type: ignore[attr-defined]
            )
        except OSError as exc:
            raise ConnectionError(
                f"PTY write failed (pid={self.pid}): {exc}"
            ) from exc

    async def kill(self, signal_: int = signal.SIGTERM) -> None:
        """First-stage WM_CLOSE / SIGTERM equivalent, wait briefly,
        escalate to TerminateProcess if still alive.

        Windows doesn't have POSIX signals; pywinpty maps the
        ``kill(signal_)`` call to the closest analogue (TerminateProcess
        with the exit code, or ``CTRL_BREAK_EVENT`` when the process
        was spawned into its own console group). ``force=True`` is the
        unconditional escalation.
        """
        if self._exited:
            return

        loop = asyncio.get_running_loop()

        try:
            await loop.run_in_executor(
                None, self._proc.kill, signal_,  # type: ignore[attr-defined]
            )
        except (ProcessLookupError, OSError) as exc:
            logger.debug("ConPTY first-stage signal raced exit: %s", exc)
            self._exited = True
            return

        deadline = time.monotonic() + _TERMINATE_GRACE_SECONDS
        while time.monotonic() < deadline:
            if not self.is_alive():
                self._exited = True
                return
            await asyncio.sleep(0.05)

        # Escalate. ``close(force=True)`` calls TerminateProcess on the
        # child and frees the ConPTY handle.
        try:
            await loop.run_in_executor(None, self._proc.close, True)  # type: ignore[attr-defined]
        except (ProcessLookupError, OSError):
            pass
        self._exited = True


class WindowsPtyTransport(PtyTransport):
    """Spawn factory using ``pywinpty>=3.0.3``.

    Lazy-imports ``winpty`` (the importable package name for pywinpty)
    so test environments missing the dep can still import this module
    on non-Windows; the factory in
    :mod:`services.cli_agent.transports` only loads this backend when
    ``sys.platform == 'win32'``.
    """

    def __init__(self) -> None:
        self._pty_process_cls: Optional[object] = None

    def _load_winpty(self) -> object:
        if self._pty_process_cls is not None:
            return self._pty_process_cls
        try:
            from winpty import PtyProcess  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "WindowsPtyTransport requires 'pywinpty>=3.0.3'. "
                "Install it (e.g. `pip install pywinpty>=3.0.3`) — it's "
                "declared in server/pyproject.toml with the marker "
                "`sys_platform == 'win32'`. Prebuilt wheels are "
                "available for Python 3.9-3.14; no C++ toolchain "
                "required."
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
            raise ValueError("WindowsPtyTransport.spawn: empty argv")

        PtyProcess = self._load_winpty()  # noqa: N806

        binary = argv[0]
        if not os.path.exists(binary):
            raise FileNotFoundError(f"PTY binary not found: {binary}")

        loop = asyncio.get_running_loop()
        proc = await loop.run_in_executor(
            None,
            lambda: PtyProcess.spawn(  # type: ignore[attr-defined]
                argv,
                cwd=str(cwd),
                env=env,
                dimensions=(24, 80),
            ),
        )
        logger.info(
            "[PtyTransport windows] spawned pid=%s cwd=%s argv0=%s",
            getattr(proc, "pid", "?"), cwd, binary,
        )
        return WindowsPtyHandle(proc)
