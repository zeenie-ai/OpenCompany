"""``company daemon start`` -- spawn the backend as a detached process."""

from __future__ import annotations

import subprocess

from cli._common import preflight
from cli.colors import console
from cli.platform_ import server_dir
from cli.run import uv_run

from . import app
from ._state import detached_kwargs, log_file, pid_dir, pid_file, read_pid


@app.command("start")
def start_command() -> None:
    """Start the backend in the background; write PID to the project's
    user-data directory."""
    if (existing := read_pid()) is not None:
        console.print(f"[yellow]Already running (pid={existing}).[/]")
        return

    cfg, root = preflight()
    backend_port = cfg.backend_port

    pid_dir().mkdir(parents=True, exist_ok=True)
    log_path = log_file()
    log = log_path.open("ab")

    proc = subprocess.Popen(
        uv_run(
            "uvicorn",
            "main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(backend_port),
            "--log-level",
            "warning",
        ),
        cwd=str(server_dir(root)),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        **detached_kwargs(),
    )
    pid_file().write_text(str(proc.pid))
    console.print(f"[green]Started pid={proc.pid}[/]  (logs: {log_path})")
