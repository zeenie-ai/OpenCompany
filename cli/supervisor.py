"""Supervisor for long-running services.

VS Code-tier process control built on stdlib + ``anyio`` + ``psutil``:

- One ``ServiceSpec`` per child; ``Manager.run()`` supervises them all
  in a single ``anyio.create_task_group`` (structured concurrency)
- Bounded restart with sliding window (LSP rule: 5 crashes / 3 min)
- Exponential backoff with jitter between restarts (1s -> 30s cap)
- Two-phase shutdown: SIGTERM -> grace -> SIGKILL via tree-walk
- Job Object on Windows / ``setsid`` on POSIX so children die with us
- Per-service color/prefix output via ``rich``

Phase A leaves ``start`` / ``dev`` migrations for later -- but the
supervisor is fully functional now and unit-tested.
"""

from __future__ import annotations

import enum
import os
import random
import signal
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import anyio

from cli.colors import emit, next_color
from cli.run import which_argv
from cli.tcp import wait_for_tcp_port
from cli.tree import add_to_job, kill_tree, new_session_kwargs


class RestartPolicy(enum.Enum):
    NEVER = "never"
    ON_CRASH = "on_crash"   # restart only on non-healthy exit
    ALWAYS = "always"       # restart on any exit


@dataclass
class ServiceSpec:
    name: str
    argv: list[str]
    cwd: Path | None = None
    env: dict[str, str] = field(default_factory=dict)
    ready_port: int | None = None
    ready_timeout: float = 30.0
    restart: RestartPolicy = RestartPolicy.ON_CRASH
    color: str | None = None
    healthy_exit_codes: set[int] = field(default_factory=lambda: {0})
    crash_window_seconds: float = 180.0
    crash_window_max: int = 5
    terminate_grace_seconds: float = 5.0


def _full_env(spec_env: dict[str, str]) -> dict[str, str]:
    """Inherit parent env + force-color so child output stays readable."""
    return {
        **os.environ,
        "FORCE_COLOR": "1",
        "PYTHONUNBUFFERED": "1",
        **spec_env,
    }


class Manager:
    """Supervise multiple ``ServiceSpec`` instances in one TaskGroup."""

    def __init__(self) -> None:
        self._specs: list[ServiceSpec] = []
        self._fatal: list[str] = []  # names of services that crash-looped
        self._active = 0
        # name -> (Process, color). Populated by _spawn_once for the
        # lifetime of each child. Used by _wait_for_signal so a second
        # Ctrl-C can force-kill PIDs synchronously without waiting on the
        # cancel-scope unwind.
        self._procs: dict[str, tuple[anyio.abc.Process, str]] = {}
        self._shutting_down = False

    def add(self, spec: ServiceSpec) -> None:
        if spec.color is None:
            spec.color = next_color()
        self._specs.append(spec)

    def add_all(self, specs: Iterable[ServiceSpec]) -> None:
        for spec in specs:
            self.add(spec)

    async def run(self) -> int:
        """Run all services concurrently. Return 0 on clean shutdown, 1 on fatal."""
        if not self._specs:
            return 0
        self._active = len(self._specs)
        unhandled = False
        try:
            async with anyio.create_task_group() as tg:
                for spec in self._specs:
                    tg.start_soon(self._supervise_wrapped, spec, tg.cancel_scope)
                tg.start_soon(self._wait_for_signal, tg.cancel_scope)
        except* Exception as eg:  # type: ignore[syntax]  # py311+
            for exc in eg.exceptions:
                emit("manager", "red", f"unexpected error: {exc}", stream="stderr")
            unhandled = True
        finally:
            # Runs even on ``CancelledError`` (which is ``BaseException``
            # in py311+ and so escapes ``except* Exception``). Without the
            # finally, the shutdown-complete marker is silently skipped on
            # the Ctrl-C path -- the user's actual exit case.
            if self._shutting_down:
                emit("manager", "green", "shutdown complete")
        if unhandled:
            return 1
        return 1 if self._fatal else 0

    async def _supervise_wrapped(
        self, spec: ServiceSpec, cancel_scope: anyio.CancelScope
    ) -> None:
        """Wrap ``_supervise`` so the manager exits when the last service does."""
        try:
            await self._supervise(spec, cancel_scope)
        finally:
            self._active -= 1
            if self._active == 0:
                # All supervised services have finished — wake the signal
                # waiter and let the TaskGroup unwind cleanly.
                cancel_scope.cancel()

    async def _wait_for_signal(self, cancel_scope: anyio.CancelScope) -> None:
        """Two-stage Ctrl-C / SIGTERM handler.

        First signal: emit a "shutting down" notice and cancel the task
        group so each ``_spawn_once`` unwinds via :meth:`_stop_proc`
        (SIGTERM -> grace -> tree-kill).

        Second signal: skip the grace period entirely -- iterate the
        ``self._procs`` registry and tree-kill every still-running PID
        synchronously. This matches the typical "Ctrl-C, then Ctrl-C
        again to force" UX from systemd / VS Code / docker compose.

        Windows only delivers ``SIGINT`` / ``SIGBREAK`` to a Python
        process; passing ``SIGTERM`` raises ``NotImplementedError``
        from anyio, so we subscribe to whichever subset is available.
        """
        if sys.platform == "win32":
            signals = (signal.SIGINT, getattr(signal, "SIGBREAK", signal.SIGINT))
        else:
            signals = (signal.SIGINT, signal.SIGTERM)
        try:
            with anyio.open_signal_receiver(*signals) as receiver:
                async for sig in receiver:
                    if not self._shutting_down:
                        self._shutting_down = True
                        emit(
                            "manager",
                            "yellow",
                            f"received {sig.name} -- shutting down "
                            "(Ctrl-C again to force-kill)",
                            stream="stderr",
                        )
                        cancel_scope.cancel()
                    else:
                        emit(
                            "manager",
                            "red",
                            f"received {sig.name} again -- force-killing",
                            stream="stderr",
                        )
                        for name, (proc, color) in list(self._procs.items()):
                            if proc.returncode is None:
                                kill_tree(proc.pid)
                                emit(name, color, "force-killed", stream="stderr")
                        return
        except NotImplementedError:
            # Some environments (eg. asyncio on Windows without
            # ProactorEventLoop, or signal handling from a non-main
            # thread) don't support open_signal_receiver. Fall back to
            # sleeping forever -- the supervised tasks themselves wake
            # the cancel scope when they finish.
            await anyio.sleep_forever()

    async def _stop_proc(self, proc: anyio.abc.Process, spec: ServiceSpec) -> None:
        """SIGTERM (POSIX) / TerminateProcess (Win) -> wait grace -> tree-kill.

        Wrapped in ``CancelScope(shield=True)`` -- this is the anyio-
        documented idiom for cleanup that must run to completion during
        an outer cancellation. Without the shield, ``_stop_proc`` is
        invoked from inside an already-cancelled scope (the supervisor
        signal handler cancelled the task group), so the very first
        ``await proc.wait()`` re-raises ``CancelledError`` and the grace
        period never elapses.
        """
        if proc.returncode is not None:
            return
        color = spec.color or "white"
        emit(spec.name, color, "stopping", stream="stderr")
        with anyio.CancelScope(shield=True):
            try:
                proc.terminate()
            except ProcessLookupError:
                return
            with anyio.move_on_after(spec.terminate_grace_seconds):
                await proc.wait()
            if proc.returncode is None:
                emit(
                    spec.name,
                    "red",
                    f"did not exit in {spec.terminate_grace_seconds:.0f}s, "
                    "force-killing process tree",
                    stream="stderr",
                )
                kill_tree(proc.pid)
                with anyio.move_on_after(2.0):
                    await proc.wait()
            emit(
                spec.name,
                color,
                f"stopped (exit {proc.returncode})",
                stream="stderr",
            )

    async def _supervise(
        self, spec: ServiceSpec, cancel_scope: anyio.CancelScope
    ) -> None:
        crashes: deque[float] = deque(maxlen=spec.crash_window_max)
        backoff_seconds = 1.0
        attempt = 0

        while True:
            attempt += 1
            exit_code = await self._spawn_once(spec)
            healthy = exit_code in spec.healthy_exit_codes

            if spec.restart is RestartPolicy.NEVER:
                return
            if spec.restart is RestartPolicy.ON_CRASH and healthy:
                emit(spec.name, spec.color or "white", f"exited cleanly ({exit_code})")
                return

            # Sliding-window crash detection (LSP rule).
            crashes.append(time.monotonic())
            if (
                len(crashes) == spec.crash_window_max
                and (crashes[-1] - crashes[0]) < spec.crash_window_seconds
            ):
                emit(
                    spec.name,
                    "red",
                    f"crash-loop: {spec.crash_window_max} deaths in "
                    f"{spec.crash_window_seconds:.0f}s -- giving up",
                    stream="stderr",
                )
                self._fatal.append(spec.name)
                return

            wait = backoff_seconds + random.uniform(0, backoff_seconds * 0.1)
            emit(
                spec.name,
                "yellow",
                f"exited with {exit_code}, restarting in {wait:.1f}s (attempt #{attempt})",
                stream="stderr",
            )
            await anyio.sleep(wait)
            backoff_seconds = min(backoff_seconds * 2, 30.0)

    async def _spawn_once(self, spec: ServiceSpec) -> int:
        """Spawn the service, drain its output, return exit code.

        ``async with`` on the ``anyio.Process`` runs ``aclose`` on exit,
        which closes stdin/stdout/stderr and awaits the child. Without
        this, asyncio's Windows ProactorEventLoop leaves the underlying
        ``BaseSubprocessTransport`` un-closed and ``__del__`` later logs
        ``ResourceWarning: unclosed transport`` / ``ValueError: I/O
        operation on closed pipe`` at GC time.
        """
        env = _full_env(spec.env)
        spawn_kwargs = new_session_kwargs()
        async with await anyio.open_process(
            which_argv(spec.argv),
            cwd=str(spec.cwd) if spec.cwd else None,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **spawn_kwargs,
        ) as proc:
            color = spec.color or "white"
            if sys.platform == "win32":
                # Best-effort enrollment in the supervisor's Job Object so
                # the OS tree-kills the child if the supervisor itself
                # dies abnormally. Loud warning (from tree.py) on failure.
                if not add_to_job(proc.pid):
                    emit(
                        spec.name,
                        "yellow",
                        f"WARN: pid={proc.pid} not enrolled in Job Object -- "
                        f"orphan risk if supervisor crashes",
                        stream="stderr",
                    )
            emit(spec.name, color, f"started pid={proc.pid}")
            # Register for force-kill access on second Ctrl-C; deregister on
            # exit so a restarted-then-died service doesn't leave a stale entry.
            self._procs[spec.name] = (proc, color)
            try:
                async with anyio.create_task_group() as tg:
                    tg.start_soon(self._drain, proc.stdout, spec, "stdout")
                    tg.start_soon(self._drain, proc.stderr, spec, "stderr")
                    if spec.ready_port:
                        tg.start_soon(self._announce_ready, spec)
                    exit_code = await proc.wait()
                    tg.cancel_scope.cancel()
            except anyio.get_cancelled_exc_class():
                await self._stop_proc(proc, spec)
                raise
            finally:
                self._procs.pop(spec.name, None)
        return exit_code

    async def _drain(
        self,
        stream: Optional[anyio.abc.ByteReceiveStream],
        spec: ServiceSpec,
        which: str,
    ) -> None:
        if stream is None:
            return
        buf = b""
        try:
            async for chunk in stream:
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    text = line.decode("utf-8", errors="replace").rstrip("\r")
                    if text:
                        emit(spec.name, spec.color or "white", text, stream=which)
            if buf:
                text = buf.decode("utf-8", errors="replace").rstrip("\r")
                if text:
                    emit(spec.name, spec.color or "white", text, stream=which)
        except (anyio.ClosedResourceError, anyio.EndOfStream):
            pass

    async def _announce_ready(self, spec: ServiceSpec) -> None:
        ok = await wait_for_tcp_port(
            spec.ready_port,  # type: ignore[arg-type]
            timeout=spec.ready_timeout,
        )
        msg = f"ready on port {spec.ready_port}" if ok else f"timed out waiting for port {spec.ready_port}"
        emit(spec.name, spec.color or "white", msg)


