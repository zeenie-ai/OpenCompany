"""``machina clean`` — reset the repo to a fresh-checkout state.

Stops every process listening on the configured ports + orphaned project
processes, waits for file locks to release, then removes build artefacts
and venvs.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from cli.colors import console
from cli.config import load_config
from cli.platform_ import project_root
from cli.ports import kill_orphaned_machina_processes, kill_port


# Removed each run -- order doesn't matter, ``shutil.rmtree`` is recursive.
# Only project-local artefacts. Home-rooted state (``~/.machina/``,
# ``~/.claude/``, etc.) is the user's; we never touch it from
# ``machina clean``.
_TARGETS = [
    "node_modules",
    "client/node_modules",
    "client/dist",
    "client/.vite",
    # Repo-local DATA_DIR target. Only present when the operator opts
    # out of the user-home default by setting ``DATA_DIR=.machina``
    # in ``.env`` (per the resolution rules in ``cli/config.py``).
    ".machina",
    # Python venvs
    "server/.venv",
    ".venv",             # stale root venv (should not exist)
]


def _rmtree_with_retry(path: Path, *, attempts: int = 3, delay: float = 0.1) -> bool:
    """``shutil.rmtree`` with Windows-friendly retry on file-lock errors."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return True
        except OSError as exc:
            if attempt == attempts - 1:
                console.print(f"  [yellow]Warning: Could not remove {path.name}: {exc}[/]")
                return False
            time.sleep(delay)
    return False


def clean_command() -> None:
    cfg = load_config()
    root = project_root()

    console.print("[bold]Cleaning MachinaOS...[/]\n")

    # Step 1: kill anything on configured ports
    console.print("Stopping running processes...")
    killed_ports = 0
    for port in cfg.all_ports:
        result = kill_port(port)
        if result.killed_pids:
            console.print(f"  Port {port}: Killed {len(result.killed_pids)} process(es)")
            killed_ports += len(result.killed_pids)

    # Step 2: kill orphaned project processes (may hold .venv file locks).
    # ``exclude_substring="-m cli"`` skips the supervisor running this
    # very ``clean`` command — its cmdline contains the entry-point
    # token ``-m cli``.
    orphaned = kill_orphaned_machina_processes(str(root), exclude_substring="-m cli")
    if orphaned:
        console.print(f"  Orphaned: Killed {len(orphaned)} process(es)")

    if killed_ports or orphaned:
        console.print("  Waiting for processes to release file locks...")
        time.sleep(1.0)
    else:
        console.print("  No running processes found.")

    # Step 3: remove directories
    console.print("\nRemoving directories...")
    for target in _TARGETS:
        path = root / target
        if path.exists():
            console.print(f"  Removing: {target}")
            _rmtree_with_retry(path)

    console.print("\n[green]Done.[/]")
