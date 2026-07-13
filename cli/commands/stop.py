"""``company stop`` -- replaces ``scripts/stop.js``.

Kills configured ports + orphaned OpenCompany processes + the Temporal
binary. Same exit-code semantics as the JS version: 0 on full success,
1 if any port is still in use afterward.
"""

from __future__ import annotations

import time

import typer

from cli._common import free_all_ports, preflight
from cli.colors import console
from cli.platform_ import platform_name
from cli.ports import (
    kill_by_pattern,
    kill_orphaned_opencompany_processes,
)


def stop_command() -> None:
    cfg, root = preflight()

    console.print()
    console.print("[bold]Stopping OpenCompany services...[/]")
    console.print(f"Platform: {platform_name()}")
    console.print(f"Ports:    {', '.join(str(p) for p in cfg.all_ports)}")
    console.print("Temporal: enabled" if cfg.temporal_enabled else "Temporal: disabled")
    console.print()

    all_stopped = True
    for port, result in zip(cfg.all_ports, free_all_ports(cfg)):
        status = "[green]\\[OK][/]" if result.port_free else "[red]\\[!!][/]"
        if result.port_free:
            if result.killed_pids:
                msg = f"Killed {len(result.killed_pids)} process(es)"
            else:
                msg = "Free"
        else:
            msg = "Warning: Port still in use"
            all_stopped = False
        console.print(f"{status} Port {port}: {msg}")
        if result.killed_pids:
            console.print(f"    PIDs: {', '.join(str(p) for p in result.killed_pids)}")

    temporal_pids = kill_by_pattern("temporal")
    if temporal_pids:
        console.print(
            f"[green]\\[OK][/] Temporal: Killed {len(temporal_pids)} process(es)"
        )

    orphaned_pids = kill_orphaned_opencompany_processes(str(root))
    if orphaned_pids:
        time.sleep(0.2)  # let DB locks release
        console.print(
            f"[green]\\[OK][/] Orphaned: Killed {len(orphaned_pids)} OpenCompany process(es)"
        )
        console.print(f"    PIDs: {', '.join(str(p) for p in orphaned_pids)}")

    console.print()
    if all_stopped:
        console.print("[green]All services stopped.[/]")
    else:
        console.print("[yellow]Warning: Some ports may still be in use.[/]")
        raise typer.Exit(code=1)
