"""``company dev`` -- replaces ``scripts/dev.js``.

Development launcher: validates the build, frees ports, then spawns
Vite + uvicorn + temporal-server under ``Manager.run()``.

The Vite dep cache (``client/node_modules/.vite``) is preserved across
boots -- Vite self-invalidates it via the lockfile/config hashes in
``.vite/deps/_metadata.json``, and an unconditional wipe forced a full
esbuild re-optimize (minutes on Windows) on every first page load.
``--force`` maps to Vite's own force-re-bundle mechanism
(``optimizeDeps.force`` via the ``VITE_FORCE`` env var, read in
``client/vite.config.js``) -- the documented recovery for an
"Outdated Optimize Dep" error. Env var rather than argv because the
client spec runs ``pnpm run client:start`` -> ``npm run start`` and a
``--force`` suffix does not survive the double ``npm run`` indirection.

uvicorn ``--reload``-style restarts (exit code 1) used to cascade-kill
the frontend under ``concurrently --kill-others``. Our supervisor
treats each service independently, so a backend reload doesn't touch
the Vite process.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer

from cli._common import build_backend_spec, free_all_ports, preflight
from cli.colors import console
from cli.config import load_dev_overrides
from cli.platform_ import (
    node_modules_dir,
    platform_name,
    static_client_script,
)
from cli.buildenv import validate_build
from cli.supervisor import Manager, ServiceSpec
from cli.commands._temporal_specs import temporal_specs


def _has_vite(root: Path) -> bool:
    return (node_modules_dir(root) / "vite").exists() or (
        root / "client" / "node_modules" / "vite"
    ).exists()


def _build_specs(
    root: Path, cfg, *, daemon: bool, use_vite: bool, force: bool = False
) -> list[ServiceSpec]:
    if use_vite:
        client_spec = ServiceSpec(
            name="client",
            argv=["pnpm", "run", "client:start"],
            cwd=root,
            ready_port=cfg.client_port,
            ready_timeout=60.0,
            env={"VITE_FORCE": "1"} if force else {},
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
        False,
        "--daemon",
        help="Bind backend to 0.0.0.0 instead of 127.0.0.1.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Force Vite to re-bundle dependencies (recovers 'Outdated Optimize Dep' errors).",
    ),
) -> None:
    # Layer ``.env.dev`` (committed) on top of ``.env.template`` + ``.env``
    # so dev state lands at ``<repo>/.opencompany/`` (per-checkout) instead
    # of ``~/.opencompany/`` (user home). Called BEFORE ``preflight()`` so
    # ``load_config``'s ``setdefault`` pass sees the dev values already
    # in ``os.environ``. ``company start`` / ``company daemon`` skip
    # this hook, falling through to the ``.env.template`` defaults.
    load_dev_overrides()
    cfg, root = preflight()
    os.environ.setdefault("PYTHONUTF8", "1")

    validate_build(root)

    console.print("\n[bold]=== OpenCompany Starting ===[/]\n")
    console.print(f"Platform: {platform_name()}")
    console.print(
        f"Mode:     {'Daemon (uvicorn)' if daemon else 'Development (uvicorn)'}"
    )
    console.print(f"Ports:    {', '.join(str(p) for p in cfg.all_ports)}")

    console.log("Freeing ports...")
    free_all_ports(cfg)
    console.log("Ports ready")

    use_vite = _has_vite(root)
    console.print(f"Client:   {'Vite dev server' if use_vite else 'Static server'}")
    if force:
        console.print("Vite:     forced dependency re-bundle (--force)")
    console.print()

    manager = Manager()
    manager.add_all(
        _build_specs(root, cfg, daemon=daemon, use_vite=use_vite, force=force)
    )
    rc = asyncio.run(manager.run())
    if rc != 0:
        raise typer.Exit(code=rc)
