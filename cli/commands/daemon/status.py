"""``machina daemon status`` -- report whether the backend is running."""

from __future__ import annotations

import typer

from cli.colors import console

from . import app
from ._state import read_pid


@app.command("status")
def status_command() -> None:
    """Exit 0 if running (and print PID); exit 1 otherwise."""
    pid = read_pid()
    if pid is None:
        console.print("Not running.")
        raise typer.Exit(code=1)
    console.print(f"[green]Running pid={pid}[/]")
