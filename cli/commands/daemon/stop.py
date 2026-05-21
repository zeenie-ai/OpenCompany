"""``machina daemon stop`` -- terminate the detached backend."""

from __future__ import annotations

from cli.colors import console

from . import app
from ._state import kill_tree, pid_file, read_pid


@app.command("stop")
def stop_command() -> None:
    """Stop the backend if running; clear PID file either way."""
    pid = read_pid()
    pf = pid_file()
    if pid is None:
        console.print("Not running.")
        pf.unlink(missing_ok=True)
        return
    kill_tree(pid)
    pf.unlink(missing_ok=True)
    console.print(f"[green]Stopped pid={pid}[/]")
