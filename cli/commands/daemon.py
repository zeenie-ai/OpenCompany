"""``machina daemon`` -- run the backend as a detached process.

Pure-Python, all-platforms. No NSSM / systemd / launchd integration.
Spawns ``uvicorn main:app`` in a new session (POSIX) or detached
process group (Windows), writes the PID under ``~/.machina/`` so the
other verbs can find it, and uses ``psutil`` for tree-kill on stop.

For boot-time auto-start, configure your OS service manager
separately (`systemctl`, `launchctl`, Task Scheduler) -- this CLI does
not register itself with the system.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import psutil
import typer

from cli.buildenv import venv_python
from cli.colors import console
from cli.platform_ import IS_WINDOWS, project_root

app = typer.Typer(
    name="daemon",
    help="Run the MachinaOs backend as a detached process.",
    no_args_is_help=True,
    add_completion=False,
)


# ---------------------------------------------------------------- helpers

_PID_DIR = Path.home() / ".machina"
_PID_FILE = _PID_DIR / "machina-backend.pid"


def _detached_kwargs() -> dict:
    """Cross-platform "spawn detached, survive parent exit" kwargs."""
    if IS_WINDOWS:
        # CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT later;
        # DETACHED_PROCESS releases the console handle.
        return {
            "creationflags": (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            ),
        }
    # POSIX: setsid puts the child in its own process group / session,
    # so killing the parent (this CLI) doesn't take the daemon down.
    return {"start_new_session": True}


def _read_pid() -> int | None:
    if not _PID_FILE.exists():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        return None
    return pid if psutil.pid_exists(pid) else None


def _kill_tree(pid: int) -> None:
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


# ---------------------------------------------------------------- verbs


@app.command("start")
def start_command() -> None:
    """Start the backend in the background; write PID to ~/.machina/."""
    if (existing := _read_pid()) is not None:
        console.print(f"[yellow]Already running (pid={existing}).[/]")
        return

    root = project_root()
    server = root / "server"
    py = venv_python(root)
    if py is None:
        console.print(f'[red]Python venv not found at {py}.[/] Run "machina build" first.')
        raise typer.Exit(code=1)

    _PID_DIR.mkdir(parents=True, exist_ok=True)
    log_file = _PID_DIR / "backend.log"
    log = log_file.open("ab")

    proc = subprocess.Popen(
        [str(py), "-m", "uvicorn", "main:app",
         "--host", "0.0.0.0", "--port", "3010", "--log-level", "warning"],
        cwd=str(server),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        **_detached_kwargs(),
    )
    _PID_FILE.write_text(str(proc.pid))
    console.print(f"[green]Started pid={proc.pid}[/]  (logs: {log_file})")


@app.command("stop")
def stop_command() -> None:
    """Stop the backend if running; clear PID file."""
    pid = _read_pid()
    if pid is None:
        console.print("Not running.")
        _PID_FILE.unlink(missing_ok=True)
        return
    _kill_tree(pid)
    _PID_FILE.unlink(missing_ok=True)
    console.print(f"[green]Stopped pid={pid}[/]")


@app.command("status")
def status_command() -> None:
    """Report whether the backend is running."""
    pid = _read_pid()
    if pid is None:
        console.print("Not running.")
        raise typer.Exit(code=1)
    console.print(f"[green]Running pid={pid}[/]")


@app.command("restart")
def restart_command() -> None:
    """Stop then start; convenience wrapper."""
    stop_command()
    start_command()
