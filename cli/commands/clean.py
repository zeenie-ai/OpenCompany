"""``machina clean`` -- reset the repo to a fresh-checkout state.

Recovery verb: must work even when the env is partially broken
(missing ``rich`` / ``psutil`` / ``platformdirs`` wheels, half-wiped
``server/.venv``, etc.). Everything at module load is stdlib + first-party
helpers (``cli._common`` / ``cli.config`` / ``cli.platform_``). The
process-killing step (which needs ``psutil`` via :mod:`cli.ports`) is
lazy-imported inside :func:`clean_command` and degrades to a warning
if the wheel is missing -- file removal continues regardless.

Stops every process listening on the configured ports + orphaned
project processes, waits for file locks to release, then removes build
artefacts and venvs.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from cli._common import free_all_ports, preflight


# Removed each run -- order doesn't matter, ``shutil.rmtree`` is recursive.
# Only project-local artefacts. Home-rooted state (``~/.machina/``,
# ``~/.claude/``, etc.) is the user's; we never touch it from
# ``machina clean``.
_TARGETS = [
    "node_modules",
    "client/node_modules",
    "client/dist",
    "client/.vite",
    # Python venvs. ``server/.venv`` is uv's default location for the
    # server's standalone project. ``.venv`` at the repo root is a
    # leftover from the brief workspace-layout experiment -- harmless
    # to wipe alongside if present.
    "server/.venv",
    ".venv",
]


# Children of ``<repo>/.machina/`` to wipe selectively. The bare
# ``.machina`` entry can't go in ``_TARGETS`` anymore because the
# ``workflows/`` subtree holds shipped example seeds (git-tracked,
# imported on first launch by ``services.example_loader``) -- wiping
# it would force the operator to re-clone to recover. ``deploy/``
# holds ``machina deploy``'s Terraform working dirs + state files:
# deleting state for LIVE cloud resources orphans the VM/firewall
# (``machina deploy destroy`` could no longer find them) -- only
# ``deploy destroy`` removes that tree. Anything else under
# ``.machina/`` (claude state, workspaces, credentials.db, ...) is
# transient runtime state and is fair game.
_MACHINA_KEEP = frozenset({"workflows", "deploy"})


def _rmtree_with_retry(path: Path, *, attempts: int = 3, delay: float = 0.1) -> bool:
    """``shutil.rmtree`` with Windows-friendly retry on file-lock errors."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, ignore_errors=False)
            return True
        except OSError as exc:
            if attempt == attempts - 1:
                print(f"  warning: could not remove {path.name}: {exc}")
                return False
            time.sleep(delay)
    return False


def _kill_running_processes(cfg, root: Path) -> bool:
    """Best-effort: kill anything on configured ports + orphaned project
    processes. Returns True if anything was killed (so the caller knows
    to wait for file locks to release before deleting directories).

    The process-killing path needs ``psutil`` (via :mod:`cli.ports`). If
    it isn't importable -- partially broken env -- we print a warning
    and skip; the directory-removal step still runs.
    """
    try:
        results = free_all_ports(cfg)
        # ``free_all_ports`` itself lazy-imports ``cli.ports`` -- the
        # outer try/except catches the ImportError if psutil is gone.
        from cli.ports import kill_orphaned_machina_processes
    except ImportError as exc:
        print(f"  warning: psutil unavailable ({exc.name}); skipping process cleanup")
        return False

    killed_ports = 0
    for port, result in zip(cfg.all_ports, results):
        if result.killed_pids:
            print(f"  port {port}: killed {len(result.killed_pids)} process(es)")
            killed_ports += len(result.killed_pids)

    orphaned = kill_orphaned_machina_processes(str(root), exclude_substring="-m cli")
    if orphaned:
        print(f"  orphaned: killed {len(orphaned)} process(es)")

    return bool(killed_ports or orphaned)


def clean_command() -> None:
    cfg, root = preflight()

    print("Cleaning MachinaOS...")

    # Step 1+2: kill running processes (best-effort, may degrade)
    print("Stopping running processes...")
    killed_any = _kill_running_processes(cfg, root)
    if killed_any:
        print("  waiting for processes to release file locks...")
        time.sleep(1.0)
    else:
        print("  no running processes found.")

    # Step 3: remove top-level artefact directories
    print()
    print("Removing directories...")
    for target in _TARGETS:
        path = root / target
        if path.exists():
            print(f"  removing: {target}")
            _rmtree_with_retry(path)

    # Step 4: selectively clean ``<repo>/.machina/`` -- preserve
    # ``workflows/`` (shipped example seeds, see ``_MACHINA_KEEP``);
    # wipe everything else (claude/, workspaces/, *.db, ...) so the
    # repo-local DATA_DIR opt-out gets the same fresh-state treatment.
    machina_dir = root / ".machina"
    if machina_dir.is_dir():
        for child in machina_dir.iterdir():
            if child.name in _MACHINA_KEEP:
                continue
            rel = f".machina/{child.name}"
            print(f"  removing: {rel}")
            if child.is_dir():
                _rmtree_with_retry(child)
            else:
                try:
                    child.unlink()
                except OSError as exc:
                    print(f"  warning: could not remove {rel}: {exc}")

    print()
    print("Done.")
