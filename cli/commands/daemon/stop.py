"""``company daemon stop`` -- terminate the detached backend."""

from __future__ import annotations

from cli.colors import console

from . import app
from ._state import kill_tree, legacy_pid_file, pid_file, read_pid


@app.command("stop")
def stop_command() -> None:
    """Stop the backend if running; clear PID file either way."""
    pid = read_pid()
    pid_paths = (pid_file(), legacy_pid_file())
    if pid is None:
        console.print("Not running.")
        for path in pid_paths:
            path.unlink(missing_ok=True)
        return
    kill_tree(pid)
    for path in pid_paths:
        path.unlink(missing_ok=True)
    console.print(f"[green]Stopped pid={pid}[/]")
