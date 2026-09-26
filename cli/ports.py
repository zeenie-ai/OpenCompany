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


def _ancestor_pids() -> set[int]:
    """Return PIDs of the current process + every ancestor up the tree.

    Used by the pattern-matching kill functions below so we never
    terminate our own parent / grandparent. When invoked through the
    global bin shim the chain is ``powershell -> bun.exe (bin/cli.js)
    -> python.exe (-m cli ...)`` -- if ``kill_orphaned_opencompany_processes``
    matches ``bun.exe`` (its cmdline carries the install path),
    killing it tears down stdio mid-execution and the Python child
    exits with whatever garbage code Windows assigns to an
    abruptly-orphaned process (observed: 58). Walk up the tree once
    and exclude every ancestor PID.

    Bounded loop (20 hops) defends against process-tree cycles
    (theoretically impossible but cheap insurance).
    """
    pids: set[int] = set()
    try:
        cur: psutil.Process | None = psutil.Process(os.getpid())
        for _ in range(20):
            if cur is None:
                break
            pids.add(cur.pid)
            try:
                cur = cur.parent()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pids.add(os.getpid())
    return pids


def find_pids_by_port(port: int) -> set[int]:
    """Find PIDs with a LISTENING socket on ``port`` via psutil's native APIs.

    Only listeners block a fresh ``bind()``. Half-closed connections left
    by a browser tab after the server dies (``CLOSE_WAIT`` on a dead PID)
    must not count, or ``company stop`` reports a bindable port as in use.
    """
    pids: set[int] = set()
    try:
        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.laddr
                and conn.laddr.port == port
                and conn.pid
                and conn.status == psutil.CONN_LISTEN
            ):
                pids.add(conn.pid)
    except psutil.AccessDenied:
        # macOS: net_connections() requires root, fall back to lsof.
        if sys.platform == "darwin":
            try:
                output = subprocess.check_output(
                    ["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"],
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
                #     ``company stop``.
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
    UI port frequently re-reports as in-use immediately after the
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
    safe_pids = _ancestor_pids()
    killed: list[int] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            cmd = " ".join(proc.info.get("cmdline") or []).lower().replace("\\", "/")
            if pattern_lower not in name and pattern_lower not in cmd:
                continue
            if root_norm and root_norm not in cmd:
                continue
            if proc.pid in safe_pids:
                continue
            proc.kill()
            killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed


def _names_this_checkout(cmd: str, root_norm: str) -> bool:
    """Whether a normalized cmdline names a path inside the checkout at ``root_norm``.

    A plain substring match also caught other checkouts: a sibling folder
    that only shares the prefix (``opencompany-worktrees/...``) and the
    worktrees nested in the checkout (``<root>/.claude/worktrees/...``), so a
    ``company stop`` in one checkout killed a dev server or test run in another.
    The root must be followed by a separator or the end of an argument, and a
    path under the nested worktrees does not count.
    """
    root = root_norm.rstrip("/")
    if not root:
        return False
    nested_worktrees = root + "/.claude/worktrees/"
    start = 0
    while True:
        index = cmd.find(root, start)
        if index < 0:
            return False
        following = cmd[index + len(root) : index + len(root) + 1]
        if following in ("", "/", " ", '"', "'") and not cmd.startswith(nested_worktrees, index):
            return True
        start = index + 1


def kill_orphaned_opencompany_processes(
    root_dir: str, *, exclude_substring: str | None = None
) -> list[int]:
    """Kill stray python/bun processes whose cmdline references the project root.

    Only this checkout's processes: see :func:`_names_this_checkout`.
    """
    root_norm = root_dir.lower().replace("\\", "/")
    # bun runs the JS executor sidecar and the company shim; node is kept
    # so a sidecar left over from a pre-bun install is still reaped.
    target_names = {"python", "python3", "python.exe", "bun", "bun.exe", "node", "node.exe"}
    safe_pids = _ancestor_pids()
    killed: list[int] = []

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (proc.info["name"] or "").lower()
            if name not in target_names:
                continue
            cmd = " ".join(proc.info.get("cmdline") or []).lower().replace("\\", "/")
            if not _names_this_checkout(cmd, root_norm):
                continue
            if exclude_substring and exclude_substring.lower() in cmd:
                continue
            if proc.pid in safe_pids:
                continue
            if kill_pid(proc.pid, graceful_timeout=2.0):
                killed.append(proc.pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return killed


# Deprecated import alias for third-party scripts that imported the old helper.
kill_orphaned_machina_processes = kill_orphaned_opencompany_processes
