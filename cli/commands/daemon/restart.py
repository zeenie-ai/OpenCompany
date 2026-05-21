"""``machina daemon restart`` -- stop then start."""

from __future__ import annotations

from . import app
from .start import start_command
from .stop import stop_command


@app.command("restart")
def restart_command() -> None:
    """Stop the backend then start it again; convenience wrapper."""
    stop_command()
    start_command()
