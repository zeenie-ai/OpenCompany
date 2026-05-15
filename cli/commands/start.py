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
import sys
import time
from pathlib import Path

import typer

from cli.colors import console
from cli.config import load_config
from cli.platform_ import IS_WINDOWS, IS_WSL, platform_name, project_root
from cli.buildenv import validate_build, venv_python
from cli.ports import kill_port
from cli.run import which_argv
from cli.supervisor import Manager, RestartPolicy, ServiceSpec
from cli.commands._temporal_specs import temporal_specs


def _sqlalchemy_preflight(root: Path) -> None:
    """Time-boxed sqlalchemy import probe.

    On Windows, Defender's minifilter driver (MpFilter.sys) sometimes
    caches stale "pending scan" entries that block .pyd LoadLibrary
    calls even after exclusions are added. Catching it here gives a
    clear remediation message instead of letting uvicorn hang silently.
    See ``docs-internal/errors.md`` #1 / #1a.
    """
    py = venv_python(root)
    if py is None:
        return
    started = time.monotonic()
    try:
        subprocess.run(
            [str(py), "-c", "import sqlalchemy"],
            timeout=15,
            check=True,
            capture_output=True,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        elapsed = time.monotonic() - started
        console.print(f"[red]Error: Python venv health check failed ({elapsed:.1f}s).[/]")
        console.print("  sqlalchemy import hung or crashed.")
        console.print("  Likely cause: Windows Defender scan cache or stale kernel state.")
        console.print("  Fix options:")
        console.print("    1. Restart-Service WinDefend  (admin PowerShell)")
        console.print("    2. Reboot the machine")
        console.print(f"    3. Add {root / 'server' / '.venv'} to Defender exclusions")
        console.print("  See docs-internal/errors.md #1 / #1a for details.")
        raise typer.Exit(code=1)
    elapsed = time.monotonic() - started
    if elapsed > 5.0:
        console.print(
            f"[yellow]Warning: sqlalchemy import took {elapsed:.1f}s "
            "(expected <1s). See docs-internal/errors.md #1.[/]"
        )


def _temporal_running() -> bool:
    """Check whether the temporal server is already up via the ``temporal`` CLI."""
    try:
        out = subprocess.run(
            which_argv(["temporal", "status"]),
            capture_output=True,
            text=True,
            timeout=5,
        )
        return any(token in out.stdout for token in ("running", "UP", "up"))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _read_version(root: Path) -> str:
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        return pkg.get("version", "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def _build_specs(root: Path, cfg, *, temporal_running: bool) -> list[ServiceSpec]:
    static_client = root / "scripts" / "serve-client.js"
    server_dir = root / "server"

    # Match the existing behaviour: production uses python:start
    # (bind 127.0.0.1) on Windows/WSL, python:daemon (bind 0.0.0.0)
    # on POSIX.
    backend_host = "127.0.0.1" if (IS_WINDOWS or IS_WSL) else "0.0.0.0"
    backend_argv = [
        "uv", "run", "uvicorn", "main:app",
        "--host", backend_host,
        "--port", str(cfg.backend_port),
        "--log-level", "warning",
    ]

    specs: list[ServiceSpec] = [
        ServiceSpec(
            name="client",
            argv=["node", str(static_client)],
            cwd=root,
            ready_port=cfg.client_port,
        ),
        ServiceSpec(
            name="server",
            argv=backend_argv,
            cwd=server_dir,
            ready_port=cfg.backend_port,
        ),
    ]
    if not temporal_running:
        specs.extend(temporal_specs(root, cfg))
    return specs


def start_command() -> None:
    root = project_root()
    cfg = load_config()
    os.environ.setdefault("PYTHONUTF8", "1")

    validate_build(root, require_client_dist=True)
    _sqlalchemy_preflight(root)

    temporal_running = _temporal_running()
    if temporal_running:
        console.print("[dim]Temporal already running, skipping[/]")

    console.log("Freeing ports...")
    for port in cfg.all_ports:
        kill_port(port)
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
