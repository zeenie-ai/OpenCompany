"""``company start`` -- replaces ``scripts/start.js``.

Production launcher: validates the build exists, runs the sqlalchemy
preflight probe (Windows Defender workaround), frees configured ports,
then spawns uvicorn under ``Manager.run()``. The backend serves the
built SPA itself (the ``SERVE_STATIC_CLIENT`` block in
``server/main.py``, on by default), so there is no separate static
client process.

Optional daemons are supervised by the Python backend, not here:
WhatsApp's edgymeow binary (``server/nodes/whatsapp/_runtime.py``) and
the Temporal dev server (``server/services/temporal/_runtime.py``,
started from the backend lifespan when ``TEMPORAL_ENABLED`` and the
configured address is loopback).
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
    platform_name,
    server_dir,
    server_venv,
    server_venv_python,
)
from cli.buildenv import validate_build
from cli.supervisor import Manager, ServiceSpec


def _sqlalchemy_preflight(root: Path) -> None:
    """Time-boxed sqlalchemy import probe.

    On Windows, Defender's minifilter driver (MpFilter.sys) sometimes
    caches stale "pending scan" entries that block .pyd LoadLibrary
    calls even after exclusions are added. Catching it here gives a
    clear remediation message instead of letting uvicorn hang silently.
    See ``docs-internal/errors.md`` #1 / #1a.

    Runs the probe with the server venv's interpreter -- the same one
    the supervised backend spec uses.
    """
    started = time.monotonic()
    try:
        subprocess.run(
            [str(server_venv_python(root)), "-c", "import sqlalchemy"],
            cwd=str(server_dir(root)),
            timeout=15,
            check=True,
            capture_output=True,
        )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as exc:
        elapsed = time.monotonic() - started
        stderr_tail = ""
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            stderr_bytes = (
                exc.stderr if isinstance(exc.stderr, bytes) else exc.stderr.encode()
            )
            stderr_tail = stderr_bytes.decode(errors="replace").strip()
        details: list[str] = ["sqlalchemy import hung or crashed."]
        if stderr_tail:
            details.append(f"subprocess stderr: {stderr_tail}")
        if IS_WINDOWS:
            details.extend(
                [
                    "Likely cause on Windows: Defender scan cache or stale kernel state.",
                    "Fix options:",
                    "  1. Restart-Service WinDefend  (admin PowerShell)",
                    "  2. Reboot the machine",
                    f"  3. Add {server_venv(root)} to Defender exclusions",
                ]
            )
        else:
            details.extend(
                [
                    f"Check that {server_venv(root)} exists and is populated.",
                    "Run `company build` to recreate the server venv.",
                ]
            )
        details.append("See docs-internal/errors.md #1 / #1a for details.")
        error_block(
            f"Python venv health check failed ({elapsed:.1f}s).",
            details,
        )
        raise typer.Exit(code=1)
    elapsed = time.monotonic() - started
    if elapsed > 5.0:
        console.print(
            f"[yellow]Warning: sqlalchemy import took {elapsed:.1f}s "
            "(expected <1s). See docs-internal/errors.md #1.[/]"
        )


def _read_version(root: Path) -> str:
    try:
        pkg = json.loads((root / "package.json").read_text(encoding="utf-8"))
        return pkg.get("version", "0.0.0")
    except (OSError, json.JSONDecodeError):
        return "0.0.0"


def _build_specs(root: Path, cfg) -> list[ServiceSpec]:
    # Bind host: ``OPENCOMPANY_BIND_HOST`` overrides the auto-pick. The
    # pre-rebrand ``MACHINA_BIND_HOST`` spelling remains a compatibility
    # hatch when the platform detection is wrong or the operator wants
    # a specific interface. By default, native Windows stays private on
    # 127.0.0.1; WSL + POSIX bind 0.0.0.0 so the service is reachable
    # both via in-VM ``localhost`` AND via the WSL VM IP from the
    # Windows host, regardless of WSL2's localhostForwarding state.
    backend_host = (
        os.environ.get("OPENCOMPANY_BIND_HOST")
        or os.environ.get("MACHINA_BIND_HOST")
        or ("127.0.0.1" if IS_WINDOWS else "0.0.0.0")
    )

    return [build_backend_spec(cfg, host=backend_host, root=root)]


def start_command() -> None:
    cfg, root = preflight()
    os.environ.setdefault("PYTHONUTF8", "1")

    validate_build(root)
    _sqlalchemy_preflight(root)

    console.log("Freeing ports...")
    free_all_ports(cfg)
    console.log("Ports ready")

    version = _read_version(root)
    console.print()
    console.print(f"  [bold]OpenCompany[/] v{version}")
    console.print(f"  App:       http://localhost:{cfg.backend_port}  (API + WebSocket + SPA)")
    console.print(f"  Platform:  {platform_name()}")
    console.print()

    manager = Manager()
    manager.add_all(_build_specs(root, cfg))
    rc = asyncio.run(manager.run())
    if rc != 0:
        raise typer.Exit(code=rc)
