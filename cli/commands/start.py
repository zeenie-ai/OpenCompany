"""``machina start`` -- replaces ``scripts/start.js``.

Production launcher: validates the build exists, runs the sqlalchemy
preflight probe (Windows Defender workaround), frees configured ports,
then spawns the static client + uvicorn + temporal-server (unless
already running) under ``Manager.run()``.

WhatsApp's edgymeow Go binary is supervised by the Python backend
(``server/nodes/whatsapp/_runtime.py``) so it does NOT appear here.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import typer

from cli._common import build_backend_spec, error_block, free_all_ports, preflight
from cli.colors import console
from cli.platform_ import (
    IS_WINDOWS,
    IS_WSL,
    platform_name,
    server_venv,
    static_client_script,
)
from cli.buildenv import validate_build
from cli.run import uv_run
from cli.supervisor import Manager, ServiceSpec
from cli.commands._temporal_specs import temporal_specs


def _sqlalchemy_preflight(root: Path) -> None:
    """Time-boxed sqlalchemy import probe.

    On Windows, Defender's minifilter driver (MpFilter.sys) sometimes
    caches stale "pending scan" entries that block .pyd LoadLibrary
    calls even after exclusions are added. Catching it here gives a
    clear remediation message instead of letting uvicorn hang silently.
    See ``docs-internal/errors.md`` #1 / #1a.

    Runs the probe inside the workspace ``.venv`` through ``uv run``
    (via :func:`cli.run.uv_run`) -- same environment the supervised
    services will use, no path-to-interpreter logic on this side.
    """
    started = time.monotonic()
    try:
        subprocess.run(
            uv_run("python", "-c", "import sqlalchemy"),
            cwd=str(root),
            timeout=15,
            check=True,
            capture_output=True,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        elapsed = time.monotonic() - started
        error_block(
            f"Python venv health check failed ({elapsed:.1f}s).",
            [
                "sqlalchemy import hung or crashed.",
                "Likely cause: Windows Defender scan cache or stale kernel state.",
                "Fix options:",
                "  1. Restart-Service WinDefend  (admin PowerShell)",
                "  2. Reboot the machine",
                f"  3. Add {server_venv(root)} to Defender exclusions",
                "See docs-internal/errors.md #1 / #1a for details.",
            ],
        )
        raise typer.Exit(code=1)
    elapsed = time.monotonic() - started
    if elapsed > 5.0:
        console.print(
            f"[yellow]Warning: sqlalchemy import took {elapsed:.1f}s "
            "(expected <1s). See docs-internal/errors.md #1.[/]"
        )


def _temporal_running(cfg) -> bool:
    """Check whether a Temporal frontend is already listening on the
    configured gRPC port. TCP probe instead of spawning a CLI -- works
    without the legacy ``temporal-server`` npm wrapper (we install our
    own binary via pooch at first boot of the supervised
    ``TemporalServerRuntime``)."""
    from cli.tcp import probe_tcp_port_sync
    return probe_tcp_port_sync(cfg.temporal_port)


def _read_version(root: Path) -> str:
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        return pkg.get("version", "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def _build_specs(root: Path, cfg, *, temporal_running: bool) -> list[ServiceSpec]:
    # Match the existing behaviour: production uses python:start
    # (bind 127.0.0.1) on Windows/WSL, python:daemon (bind 0.0.0.0)
    # on POSIX.
    backend_host = "127.0.0.1" if (IS_WINDOWS or IS_WSL) else "0.0.0.0"

    specs: list[ServiceSpec] = [
        ServiceSpec(
            name="client",
            argv=["node", str(static_client_script(root))],
            cwd=root,
            ready_port=cfg.client_port,
        ),
        build_backend_spec(cfg, host=backend_host, root=root),
    ]
    if not temporal_running:
        specs.extend(temporal_specs(root, cfg))
    return specs


def start_command() -> None:
    cfg, root = preflight()
    os.environ.setdefault("PYTHONUTF8", "1")

    validate_build(root, require_client_dist=True)
    _sqlalchemy_preflight(root)

    temporal_running = _temporal_running(cfg)
    if temporal_running:
        console.print("[dim]Temporal already running, skipping[/]")

    console.log("Freeing ports...")
    free_all_ports(cfg)
    console.log("Ports ready")

    version = _read_version(root)
    console.print()
    console.print(f"  [bold]MachinaOS[/] v{version}")
    console.print(f"  Frontend:  http://localhost:{cfg.client_port}")
    console.print(f"  Backend:   http://localhost:{cfg.backend_port}")
    console.print(f"  Platform:  {platform_name()}")
    console.print()

    manager = Manager()
    manager.add_all(_build_specs(root, cfg, temporal_running=temporal_running))
    rc = asyncio.run(manager.run())
    if rc != 0:
        raise typer.Exit(code=rc)
