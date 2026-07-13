"""Shared state for the ``company daemon`` verbs.

PID-file location, detached-spawn kwargs, and the ``psutil``-backed
tree-kill helper that ``start`` / ``stop`` / ``status`` / ``restart``
all need. Lives in its own module so each verb file imports only what
it uses -- no module-level side effects (the PID-dir resolution
happens inside :func:`pid_dir`, not at import time, so importing this
module is cheap and doesn't depend on ``platformdirs`` being available
until the verb actually runs).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import psutil

from cli.platform_ import IS_WINDOWS, user_data_dir


_PID_FILENAME = "opencompany-backend.pid"
_LEGACY_PID_FILENAME = "machina-backend.pid"
_LOG_FILENAME = "backend.log"


def pid_dir() -> Path:
    """Project's user-data directory -- created on demand by callers."""
    return user_data_dir()


def pid_file() -> Path:
    """Path to the daemon's PID file."""
    return pid_dir() / _PID_FILENAME


def legacy_pid_file() -> Path:
    """Pre-rebrand PID path, read and cleared for upgrade compatibility."""
    return pid_dir() / _LEGACY_PID_FILENAME


def log_file() -> Path:
    """Path to the daemon's stdout/stderr log file."""
    return pid_dir() / _LOG_FILENAME


def detached_kwargs() -> dict:
    """Cross-platform "spawn detached, survive parent exit" kwargs.

    Windows: ``CREATE_NEW_PROCESS_GROUP`` enables later ``CTRL_BREAK_EVENT``;
    ``DETACHED_PROCESS`` releases the console handle.
    POSIX: ``start_new_session`` puts the child in its own session, so
    killing the parent (this CLI) doesn't take the daemon down.
    """
    if IS_WINDOWS:
        return {
            "creationflags": (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            ),
        }
    return {"start_new_session": True}


def read_pid() -> int | None:
    """Return the live PID from the PID file, or ``None`` if absent /
    corrupt / process no longer exists."""
    for pf in (pid_file(), legacy_pid_file()):
        if not pf.exists():
            continue
        try:
            pid = int(pf.read_text().strip())
        except (ValueError, OSError):
            continue
        if psutil.pid_exists(pid):
            return pid
    return None


def kill_tree(pid: int) -> None:
    """Terminate ``pid`` and all of its descendants. Best-effort.

    Children are killed first, then the parent is terminated with a
    5-second grace period; falls back to SIGKILL if the parent doesn't
    exit in time. Matches the supervisor's two-phase shutdown
    semantics.
    """
    try:
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    for child in proc.children(recursive=True):
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except psutil.NoSuchProcess:
        return
    except psutil.TimeoutExpired:
        try:
            proc.kill()
        except psutil.NoSuchProcess:
            pass
