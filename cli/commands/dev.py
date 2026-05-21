"""``machina dev`` -- replaces ``scripts/dev.js``.

Development launcher: validates the build, frees ports, clears the
Vite dep cache (fixes "Outdated Optimize Dep" errors), then spawns
Vite + uvicorn + temporal-server under ``Manager.run()``.

uvicorn ``--reload``-style restarts (exit code 1) used to cascade-kill
the frontend under ``concurrently --kill-others``. Our supervisor
treats each service independently, so a backend reload doesn't touch
the Vite process.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

import typer

from cli._common import build_backend_spec, free_all_ports, preflight
from cli.colors import console
from cli.platform_ import (
    node_modules_dir,
    platform_name,
    static_client_script,
)
from cli.buildenv import validate_build
from cli.supervisor import Manager, ServiceSpec
from cli.commands._temporal_specs import temporal_specs


def _has_vite(root: Path) -> bool:
    return (
        (node_modules_dir(root) / "vite").exists()
        or (root / "client" / "node_modules" / "vite").exists()
    )


def _clear_vite_cache(root: Path) -> None:
    cache = root / "client" / "node_modules" / ".vite"
    if not cache.exists():
        return
    try:
        shutil.rmtree(cache)
        console.print("Cleared Vite cache")
    except OSError as exc:
        console.print(f"[yellow]Warning: Could not clear Vite cache: {exc}[/]")


def _build_specs(root: Path, cfg, *, daemon: bool, use_vite: bool) -> list[ServiceSpec]:
    if use_vite:
        client_spec = ServiceSpec(
            name="client",
            argv=["pnpm", "run", "client:start"],
            cwd=root,
            ready_port=cfg.client_port,
            ready_timeout=60.0,
        )
    else:
        client_spec = ServiceSpec(
            name="client",
            argv=["node", str(static_client_script(root))],
            cwd=root,
            ready_port=cfg.client_port,
        )

    backend_host = "0.0.0.0" if daemon else "127.0.0.1"
    server_spec = build_backend_spec(cfg, host=backend_host, root=root)
    return [client_spec, server_spec, *temporal_specs(root, cfg)]


def dev_command(
    daemon: bool = typer.Option(
        False, "--daemon", help="Bind backend to 0.0.0.0 instead of 127.0.0.1.",
    ),
) -> None:
    cfg, root = preflight()
    os.environ.setdefault("PYTHONUTF8", "1")

    validate_build(root)

    console.print("\n[bold]=== MachinaOS Starting ===[/]\n")
    console.print(f"Platform: {platform_name()}")
    console.print(f"Mode:     {'Daemon (uvicorn)' if daemon else 'Development (uvicorn)'}")
    console.print(f"Ports:    {', '.join(str(p) for p in cfg.all_ports)}")

    console.log("Freeing ports...")
    free_all_ports(cfg)
    console.log("Ports ready")

    _clear_vite_cache(root)

    use_vite = _has_vite(root)
    console.print(f"Client:   {'Vite dev server' if use_vite else 'Static server'}")
    console.print()

    manager = Manager()
    manager.add_all(_build_specs(root, cfg, daemon=daemon, use_vite=use_vite))
    rc = asyncio.run(manager.run())
    if rc != 0:
        raise typer.Exit(code=rc)
