"""VSCode-style IDE lockfile writer + remover + stale-PID sweep.

Each spawned CLI session writes a discovery lockfile that the CLI reads
to auto-connect to MachinaOs's MCP server. We mirror the format the
official VSCode Claude Code extension writes
(``~/.claude/ide/<pid>.lock`` containing
``{port, url, authToken, workspaceFolders, ideName, transport, pid}``)
and the path convention Gemini's VSCode companion uses
(``<tmpdir>/gemini/ide/gemini-ide-server-<pid>-<port>.json``).

The startup sweep (mirroring VSCode's own behaviour) removes lockfiles
whose PIDs are no longer alive — see ``main.py`` lifespan.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def _lockfile_path(
    *,
    ide_lockfile_dir: Path,
    pid: int,
    port: int,
    ide_name: str,
) -> Path:
    if ide_name == "gemini":
        return ide_lockfile_dir / f"gemini-ide-server-{pid}-{port}.json"
    return ide_lockfile_dir / f"{pid}.lock"


# Keep `lockfile_path` as a public alias for tests that exercise the
# path-shape contract.
lockfile_path = _lockfile_path


def write_ide_lockfile(
    *,
    ide_lockfile_dir: Path,
    pid: int,
    port: int,
    token: str,
    workspace_dir: Path,
    ide_name: str,
    url: Optional[str] = None,
    transport: str = "http",
) -> Path:
    """Write a VSCode-style IDE lockfile (mode 0600 on POSIX)."""
    ide_lockfile_dir.mkdir(parents=True, exist_ok=True)
    path = _lockfile_path(
        ide_lockfile_dir=ide_lockfile_dir,
        pid=pid,
        port=port,
        ide_name=ide_name,
    )

    # FastMCP's ``streamable_http_app()`` registers the JSON-RPC route at
    # ``/mcp`` of the sub-app; ``main.py`` mounts the sub-app at
    # ``/mcp/ide``. The absolute endpoint the claude CLI POSTs to via
    # ``--ide`` lockfile discovery is therefore ``/mcp/ide/mcp``. Older
    # builds advertised ``/mcp/ide`` here and the CLI silently 404'd
    # before reaching the bearer-token middleware — see ``[CC-Agent MCP
    # auth]`` log lines.
    payload = {
        "port": port,
        "url": url or f"http://127.0.0.1:{port}/mcp/ide/mcp",
        "authToken": token,
        "workspaceFolders": [str(workspace_dir)],
        "ideName": ide_name,
        "transport": transport,
        "pid": pid,
    }

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    if sys.platform != "win32":
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    tmp.replace(path)
    return path


def remove_ide_lockfile(path: Optional[Path]) -> None:
    """Best-effort lockfile removal. Never raises."""
    if not path:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:
        logger.debug("Failed to remove lockfile %s: %s", path, exc)


def sweep_stale_lockfiles(ide_lockfile_dir: Optional[Path]) -> int:
    """Remove lockfiles whose PIDs are no longer alive.

    Mirrors VSCode's startup behaviour. Safe on missing dirs. Never raises.
    """
    if not ide_lockfile_dir or not ide_lockfile_dir.exists():
        return 0

    try:
        import psutil
    except ImportError:
        return 0

    removed = 0
    for entry in ide_lockfile_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            data = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        pid = data.get("pid")
        if not isinstance(pid, int):
            continue
        try:
            alive = psutil.pid_exists(pid)
        except Exception:
            alive = True
        if alive:
            continue
        try:
            entry.unlink()
            removed += 1
        except OSError as exc:
            logger.debug("Failed to remove stale lockfile %s: %s", entry, exc)

    if removed:
        logger.info("Swept %d stale CLI lockfile(s) from %s", removed, ide_lockfile_dir)
    return removed


def list_active_lockfiles(ide_lockfile_dir: Optional[Path]) -> List[Path]:
    """Diagnostic helper: list lockfiles in the discovery dir."""
    if not ide_lockfile_dir or not ide_lockfile_dir.exists():
        return []
    return [p for p in ide_lockfile_dir.iterdir() if p.is_file()]
