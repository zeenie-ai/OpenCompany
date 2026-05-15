"""``machina stop`` -- replaces ``scripts/stop.js``.

Kills configured ports + orphaned MachinaOS processes + the Temporal
binary. Same exit-code semantics as the JS version: 0 on full success,
1 if any port is still in use afterward.
"""

from __future__ import annotations

import time

import typer

from cli.colors import console
from cli.config import load_config
from cli.platform_ import platform_name, project_root
from cli.ports import (
    kill_by_pattern,
    kill_orphaned_machina_processes,
    kill_port,
)


def stop_command() -> None:
    cfg = load_config()
    root = project_root()

    console.print()
    console.print("[bold]Stopping MachinaOS services...[/]")
    console.print(f"Platform: {platform_name()}")
    console.print(f"Ports:    {', '.join(str(p) for p in cfg.all_ports)}")
    console.print(f"Temporal: enabled" if cfg.temporal_enabled else "Temporal: disabled")
    console.print()

    all_stopped = True
    for port in cfg.all_ports:
        result = kill_port(port)
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

    orphaned_pids = kill_orphaned_machina_processes(str(root))
    if orphaned_pids:
        time.sleep(0.2)  # let DB locks release
        console.print(
            f"[green]\\[OK][/] Orphaned: Killed {len(orphaned_pids)} MachinaOS process(es)"
        )
        console.print(f"    PIDs: {', '.join(str(p) for p in orphaned_pids)}")

    console.print()
    if all_stopped:
        console.print("[green]All services stopped.[/]")
    else:
        console.print("[yellow]Warning: Some ports may still be in use.[/]")
        raise typer.Exit(code=1)
