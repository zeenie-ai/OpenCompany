"""Cross-platform process-tree control.

Two cooperating mechanisms guarantee supervised children die when we
want them to (gracefully) AND die when the supervisor dies (forcibly):

POSIX:
  - ``setsid`` makes each child a process-group leader so ``killpg``
    reaches the whole descendant tree.

Windows:
  - ``CREATE_NEW_PROCESS_GROUP`` (per-child) so the supervisor can
    target each child with ``CTRL_BREAK_EVENT`` for graceful shutdown
    without the signal also reaching the supervisor itself
    (https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NEW_PROCESS_GROUP).
    Required because ``proc.terminate()`` on Windows is
    ``TerminateProcess()`` — an instant hard kill that leaves no time
    for cleanup and forces exit code 1.
  - A single Job Object with ``JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`` for
    automatic atomic tree-kill if the supervisor dies abnormally.
    Children created by ``CreateProcess`` auto-inherit the job, so
    grandchildren are reaped without manual tree walks
    (https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).

References:
- https://docs.python.org/3/library/subprocess.html#subprocess.CREATE_NEW_PROCESS_GROUP
- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
- https://nikhilism.com/post/2017/windows-job-objects-process-tree-management/
- microsoft/node-pty (uses Job Objects in production)
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Optional

import psutil


# ---------------------------------------------------------------- POSIX / Windows spawn kwargs


def new_session_kwargs() -> dict:
    """``Popen``/``open_process`` kwargs to spawn the child in its own group.

    POSIX: ``start_new_session=True`` (``setsid``) makes the child a
    process-group leader so ``killpg`` reaches the whole tree.

    Windows: ``creationflags=CREATE_NEW_PROCESS_GROUP`` so the child can
    receive ``CTRL_BREAK_EVENT`` from the supervisor without the console
    Ctrl+C also reaching it. Without this flag, ``proc.terminate()`` on
    Windows falls back to ``TerminateProcess()`` (instant hard kill,
    exit 1, no cleanup chance) — what we're trying to avoid for daemons
    like temporal that buffer state.
    """
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def signal_group(pid: int, sig: signal.Signals = signal.SIGTERM) -> None:
    """Send ``sig`` to the process group led by ``pid`` (POSIX only)."""
    if sys.platform == "win32":
        return
    try:
        os.killpg(os.getpgid(pid), sig)
    except ProcessLookupError:
        pass


# ---------------------------------------------------------------- Windows Job Object


class _JobObject:
    """Lazy-imported wrapper around a Windows Job Object.

    Failure modes are surfaced to stderr instead of swallowed. When this
    fails the supervisor cannot tree-kill children if it itself dies
    abnormally (SIGKILL, BSOD, console close), so silent failure leads
    directly to orphan-process accumulation on Windows.
    """

    # Win10/11 supports nested jobs, but the parent must allow it. When
    # the supervisor was launched from another process that already put
    # us inside a Job Object (npm/pnpm/conhost wrappers occasionally do
    # this), AssignProcessToJobObject succeeds at the API level but the
    # child silently lands in the OUTER job, not ours. We detect that
    # via IsProcessInJob in ``add()`` and warn explicitly.
    def __init__(self) -> None:
        self._handle = None
        self._win32job = None
        if sys.platform != "win32":
            return
        try:
            import win32job  # type: ignore[import-not-found]
        except ImportError as e:
            sys.stderr.write(
                f"[supervisor] WARN: pywin32 import failed ({e}). "
                f"Children will NOT be tree-killed if the supervisor dies "
                f"abnormally on Windows. Reinstall via: pip install --force-reinstall pywin32\n"
            )
            return
        try:
            self._win32job = win32job
            self._handle = win32job.CreateJobObject(None, "")
            info = win32job.QueryInformationJobObject(
                self._handle, win32job.JobObjectExtendedLimitInformation
            )
            info["BasicLimitInformation"]["LimitFlags"] |= (
                win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            )
            win32job.SetInformationJobObject(
                self._handle, win32job.JobObjectExtendedLimitInformation, info
            )
        except Exception as e:
            sys.stderr.write(
                f"[supervisor] WARN: Job Object init failed: {e!r}. "
                f"Tree-kill on abnormal supervisor exit will not work.\n"
            )
            self._handle = None
            self._win32job = None

    def add(self, pid: int) -> bool:
        """Enroll ``pid`` in the job. Returns False when enrollment did
        not actually take effect.

        ``AssignProcessToJobObject`` can return success even when the
        child remains in a different job (inherited from an outer
        wrapper that doesn't allow nesting). We verify with
        ``IsProcessInJob`` so the caller learns the truth.
        """
        if self._handle is None or self._win32job is None:
            return False
        try:
            import win32api  # type: ignore[import-not-found]
        except ImportError as e:
            sys.stderr.write(f"[supervisor] WARN: win32api import failed ({e})\n")
            return False
        try:
            # PROCESS_ALL_ACCESS = 0x1F0FFF
            handle = win32api.OpenProcess(0x1F0FFF, False, pid)
            self._win32job.AssignProcessToJobObject(self._handle, handle)
            in_our_job = self._win32job.IsProcessInJob(handle, self._handle)
            if not in_our_job:
                in_some_job = self._win32job.IsProcessInJob(handle, None)
                sys.stderr.write(
                    f"[supervisor] WARN: pid={pid} did not enter our Job Object "
                    f"(already in another job: {in_some_job}). Likely cause: "
                    f"this supervisor was launched from a wrapper (npm/pnpm/conhost) "
                    f"whose own Job Object disallows nesting. Tree-kill on abnormal "
                    f"supervisor exit will not reach this child.\n"
                )
                return False
            return True
        except Exception as e:
            sys.stderr.write(
                f"[supervisor] WARN: AssignProcessToJobObject(pid={pid}) failed: {e!r}\n"
            )
            return False


_JOB: Optional[_JobObject] = None


def get_job() -> _JobObject:
    global _JOB
    if _JOB is None:
        _JOB = _JobObject()
    return _JOB


def add_to_job(pid: int) -> bool:
    """Best-effort enrollment of ``pid`` in the supervisor's Job Object."""
    if sys.platform != "win32":
        return False
    return get_job().add(pid)


# --------------------------------------------------------------- Tree kill


def kill_tree(pid: int) -> None:
    """Cross-platform tree-kill via psutil. Defensive against races."""
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    try:
        children = parent.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    for child in children:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    try:
        parent.kill()
    except psutil.NoSuchProcess:
        pass
