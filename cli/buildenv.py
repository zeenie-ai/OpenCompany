"""Build-environment helpers shared by ``start`` / ``dev`` / ``daemon``.

Three commands had near-duplicate copies of "find the venv python" and
"the build artifacts must exist or refuse to start". Lifted here so a
new launcher gets both for free.
"""

from __future__ import annotations

from pathlib import Path

import typer

from cli.colors import console


def venv_python(root: Path) -> Path | None:
    """Return the project's venv python path, or ``None`` if absent.

    Tries the Windows layout first (``Scripts/python.exe``) then POSIX
    (``bin/python``); the first existing path wins.
    """
    for candidate in (
        root / "server" / ".venv" / "Scripts" / "python.exe",
        root / "server" / ".venv" / "bin" / "python",
    ):
        if candidate.exists():
            return candidate
    return None


def validate_build(root: Path, *, require_client_dist: bool = False) -> None:
    """Refuse to launch if ``machina build`` hasn't been run.

    Raises ``typer.Exit(1)`` with a remediation hint. ``dev`` allows a
    missing ``client/dist`` (Vite serves the source); ``start`` opts in
    via ``require_client_dist=True``.
    """
    if not (root / "node_modules").exists() or not (root / "server" / ".venv").exists():
        console.print('[red]Error: Project not built. Run "machina build" first.[/]')
        raise typer.Exit(code=1)
    if require_client_dist and not (root / "client" / "dist" / "index.html").exists():
        console.print('[red]Error: Client not built. Run "machina build" first.[/]')
        raise typer.Exit(code=1)
