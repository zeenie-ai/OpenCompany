"""Cross-platform port + process killing.

Lifts the helpers from ``scripts/port_kill.py`` so the CLI uses the
same battle-tested ``psutil`` paths that are already in production.
"""

from __future__ import annotations

import os
import sys
import subprocess
from dataclasses import dataclass

import psutil


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
    """Terminate ``pid`` gracefully, then force-kill on timeout."""
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        try:
            proc.wait(timeout=graceful_timeout)
        except psutil.TimeoutExpired:
            proc.kill()
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False


def kill_port(port: int) -> KillResult:
    """Kill anything listening on ``port`` and report whether the port is free."""
    my_pid = os.getpid()
    killed: list[int] = []
    for pid in find_pids_by_port(port):
        if pid == my_pid:
            continue
        if kill_pid(pid):
            killed.append(pid)
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
