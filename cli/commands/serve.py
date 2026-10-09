"""``company serve`` -- single-port production runtime.

Runs the app on ONE public port: uvicorn serves the REST API + WebSocket +
the built React SPA (via the ``SERVE_STATIC_CLIENT`` block in
``server/main.py``). Used locally for a production-shaped run AND as the
systemd ``ExecStart`` on a VM provisioned by ``company deploy``.

Optional daemons are backend-owned and spawn on demand — the JS/TS
code-exec sidecar on bun (``nodes/code/_runtime.py``), WhatsApp, and the
Temporal dev server all start from the backend, so ``serve`` supervises
exactly one process.

The long-running uvicorn is invoked via the server venv's interpreter
directly (not ``uv run``) so the systemd service has no runtime dependency
on ``uv`` being on PATH.
"""

from __future__ import annotations

import asyncio
import os

import typer

from cli._common import backend_shutdown_grace_seconds, build_backend_spec, preflight
from cli.buildenv import validate_build
from cli.colors import console


def serve_command(port: int | None = None) -> None:
    from cli.supervisor import Manager

    cfg, root = preflight()
    os.environ.setdefault("PYTHONUTF8", "1")
    validate_build(root)

    # Public port: --port flag > $PORT (Cloud Run / systemd convention) >
    # PYTHON_BACKEND_PORT from the env files.
    bind_port = port or int(os.environ.get("PORT") or cfg.backend_port)

    # Free the port we will bind (clears stale orphans; idempotent).
    from cli.ports import kill_port

    kill_port(bind_port, backend_graceful_timeout=backend_shutdown_grace_seconds(cfg))

    console.print()
    console.print("  [bold]OpenCompany[/] serve (single-port)")
    console.print(f"  App:     http://0.0.0.0:{bind_port}  (API + WebSocket + SPA)")
    console.print()

    specs = [
        build_backend_spec(
            cfg,
            host="0.0.0.0",
            root=root,
            port=bind_port,
            env={"SERVE_STATIC_CLIENT": "1", "PORT": str(bind_port)},
        ),
    ]

    manager = Manager()
    manager.add_all(specs)
    rc = asyncio.run(manager.run())
    if rc != 0:
        raise typer.Exit(code=rc)
