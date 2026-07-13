"""``company serve`` -- single-port production runtime.

Runs the app on ONE public port: uvicorn serves the REST API + WebSocket +
the built React SPA (via the ``SERVE_STATIC_CLIENT`` block in
``server/main.py``), plus the Node.js code-exec sidecar on its own internal
port. Used locally for a production-shaped run AND as the systemd
``ExecStart`` on a VM provisioned by ``company deploy``.

Unlike ``company start`` (which runs a separate static-client server + the
backend + temporal on multiple ports), ``serve`` is single-port and serves
the client from the backend itself. The Node sidecar is NOT launched by
``start``/``dev`` today, so ``serve`` adds it (the JS/TS executor nodes need
it).

The long-running uvicorn is invoked via the server venv's interpreter
directly (not ``uv run``) so the systemd service has no runtime dependency
on ``uv`` being on PATH.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from cli._common import preflight
from cli.buildenv import validate_build
from cli.colors import console
from cli.platform_ import IS_WINDOWS, server_dir, server_venv


def _venv_python(root: Path | None = None) -> str:
    """Absolute path to the server venv's Python interpreter."""
    venv = server_venv(root)
    rel = "Scripts/python.exe" if IS_WINDOWS else "bin/python"
    return str(venv / rel)


def serve_command(port: int | None = None) -> None:
    from cli.supervisor import Manager, ServiceSpec

    cfg, root = preflight()
    os.environ.setdefault("PYTHONUTF8", "1")
    validate_build(root, require_client_dist=True)

    # Public port: --port flag > $PORT (Cloud Run / systemd convention) >
    # PYTHON_BACKEND_PORT from the env files.
    bind_port = port or int(os.environ.get("PORT") or cfg.backend_port)

    # Free the ports we will bind (clears stale orphans; idempotent).
    from cli.ports import kill_port

    for p in {bind_port, cfg.nodejs_port}:
        kill_port(p)

    console.print()
    console.print("  [bold]OpenCompany[/] serve (single-port)")
    console.print(f"  App:     http://0.0.0.0:{bind_port}  (API + WebSocket + SPA)")
    console.print(f"  Sidecar: 127.0.0.1:{cfg.nodejs_port}  (JS/TS executor)")
    console.print()

    sidecar = server_dir(root) / "nodejs" / "dist" / "index.js"

    specs = [
        ServiceSpec(
            name="server",
            argv=[
                _venv_python(root),
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "0.0.0.0",
                "--port",
                str(bind_port),
                "--log-level",
                "warning",
            ],
            cwd=server_dir(root),
            env={"SERVE_STATIC_CLIENT": "1", "PORT": str(bind_port)},
            ready_port=bind_port,
        ),
        ServiceSpec(
            name="nodejs",
            argv=["node", str(sidecar)],
            cwd=server_dir(root) / "nodejs",
            env={"NODEJS_EXECUTOR_PORT": str(cfg.nodejs_port)},
            ready_port=cfg.nodejs_port,
        ),
    ]

    manager = Manager()
    manager.add_all(specs)
    rc = asyncio.run(manager.run())
    if rc != 0:
        raise typer.Exit(code=rc)
