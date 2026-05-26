"""Cross-platform port + process killing.

Lifts the helpers from ``scripts/port_kill.py`` so the CLI uses the
same battle-tested ``psutil`` paths that are already in production.
"""

from __future__ import annotations

import os
import signal
import sys
import subprocess
import time
from dataclasses import dataclass

import psutil


# Post-kill grace before re-checking the port. Windows can lag a few
# hundred ms releasing the listener socket after the bound process
# dies — without this, ``kill_port`` reports the port still in use
# even though the kill succeeded.
_POST_KILL_RECHECK_DELAY = 0.5


@dataclass
class KillResult:
    port: int
    killed_pids: list[int]
    port_free: bool


def find_pids_by_port(port: int) -> set[int]:
    """Find PIDs listening on ``port`` via psutil's native APIs."""
    pids: set[int] = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.pid:
                pids.add(conn.pid)
    except psutil.AccessDenied:
        # macOS: net_connections() requires root, fall back to lsof.
        if sys.platform == "darwin":
            try:
                output = subprocess.check_output(
                    ["lsof", "-ti", f":{port}"],
                    text=True,
                    stderr=subprocess.DEVNULL,
                )
                for line in output.strip().splitlines():
                    try:
                        pids.add(int(line.strip()))
                    except ValueError:
                        pass
            except (subprocess.CalledProcessError, FileNotFoundError):
                pass
    except OSError:
        pass
    return pids


def kill_pid(pid: int, *, graceful_timeout: float = 3.0) -> bool:
    """Terminate ``pid`` gracefully, then force-kill on timeout.

    Windows: send ``CTRL_BREAK_EVENT`` first so daemons spawned with
    ``CREATE_NEW_PROCESS_GROUP`` (the supervisor's children — see
    ``cli/tree.py:new_session_kwargs``) get a real shutdown signal and
    can release listener sockets cleanly. ``proc.terminate()``
    (= ``TerminateProcess``) is the fallback for processes that weren't
    spawned with a process group — equivalent to SIGKILL, leaves the
    OS holding sockets briefly. Same pattern as
    ``cli/supervisor.py:_stop_proc``.

    POSIX: plain ``proc.terminate()`` (SIGTERM).
    """
    try:
        proc = psutil.Process(pid)
        if sys.platform == "win32":
            try:
                os.kill(pid, signal.CTRL_BREAK_EVENT)
            except (OSError, ProcessLookupError, SystemError):
                # Fall back to TerminateProcess via psutil.terminate.
                #   * ``OSError`` -- target wasn't in our process group.
                #   * ``SystemError`` -- CPython issue #106148: on
                #     Windows, ``os.kill`` returns success AND sets an
                #     ``OSError`` when ``GenerateConsoleCtrlEvent``
                #     fails with ERROR_INVALID_PARAMETER (e.g. the
                #     target process isn't in the caller's console
                #     group). CPython detects the inconsistency and
                #     raises ``SystemError`` instead of propagating
                #     the underlying OSError. ``kill_pid`` is used to
                #     clean up stale state (port squatters, orphans
                #     from a prior session) -- none of them are in
                #     our console group, so this path fires every
                #     time on Windows and used to crash
                #     ``machina stop``.
                proc.terminate()
        else:
            proc.terminate()
        try:
            proc.wait(timeout=graceful_timeout)
        except psutil.TimeoutExpired:
            proc.kill()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def kill_port(port: int) -> KillResult:
    """Kill anything listening on ``port`` and report whether the port is free.

    Post-kill recheck sleeps ``_POST_KILL_RECHECK_DELAY`` because Windows
    can lag a few hundred ms releasing the listener socket after the
    bound process dies — without this, ``temporal server start-dev``'s
    UI port (8080) frequently re-reports as in-use immediately after the
    gRPC port kill, even though the kill on temporal.exe (one process
    binds both ports — per docs.temporal.io/cli/server) succeeded.
    """
    my_pid = os.getpid()
    killed: list[int] = []
    for pid in find_pids_by_port(port):
        if pid == my_pid:
            continue
        if kill_pid(pid):
            killed.append(pid)
    if killed:
        time.sleep(_POST_KILL_RECHECK_DELAY)
    port_free = not find_pids_by_port(port)
    return KillResult(port=port, killed_pids=killed, port_free=port_free)


def kill_by_pattern(pattern: str, *, root_dir: str | None = None) -> list[int]:
    """Kill processes whose name OR command line matches ``pattern``.

    When ``root_dir`` is supplied, only processes whose command line also
    references that path are killed (so unrelated tools that happen to
    share a substring are left alone).
    """
    pattern_lower = pattern.lower()
    root_norm = root_dir.lower().replace("\\", "/") if root_dir else None
    my_pid = os.getpid()
    killed: list[int] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmd = " ".join(proc.info.get("cmdline") or []).lower().replace("\\", "/")
            if pattern_lower not in name and pattern_lower not in cmd:
                continue
            if root_norm and root_norm not in cmd:
                continue
            if proc.pid == my_pid:
                continue
            proc.kill()
            killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed


def kill_orphaned_machina_processes(
    root_dir: str, *, exclude_substring: str | None = None
) -> list[int]:
    """Kill stray python/node processes whose cmdline references the project root."""
    root_norm = root_dir.lower().replace("\\", "/")
    target_names = {"python", "python3", "python.exe", "node", "node.exe"}
    my_pid = os.getpid()
    killed: list[int] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name not in target_names:
                continue
            cmd = " ".join(proc.info.get("cmdline") or []).lower().replace("\\", "/")
            if root_norm not in cmd:
                continue
            if exclude_substring and exclude_substring.lower() in cmd:
                continue
            if proc.pid == my_pid:
                continue
            if kill_pid(proc.pid, graceful_timeout=2.0):
                killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed
