"""Build-environment helpers shared by ``start`` / ``dev`` / ``daemon``.

``validate_build`` refuses to launch when ``machina build`` hasn't been
run. Layout knowledge lives in :mod:`cli.platform_` -- this file only
composes the documented prefixes (workspace venv, node_modules, client
dist) into a boolean check.

Subprocess callers (start / dev / daemon / temporal specs) build their
argv via :func:`cli.run.uv_run`, which runs the command inside the uv
workspace ``.venv`` per https://docs.astral.sh/uv/reference/cli/#uv-run.
There is no path-to-interpreter logic on the CLI side -- uv owns that.
"""

from __future__ import annotations

from pathlib import Path

import typer

from cli.colors import console
from cli.platform_ import (
    client_dist_entry,
    node_modules_dir,
    server_venv,
)


def validate_build(root: Path, *, require_client_dist: bool = False) -> None:
    """Refuse to launch if ``machina build`` hasn't been run.

    Raises ``typer.Exit(1)`` with a remediation hint. ``dev`` allows a
    missing ``client/dist`` (Vite serves the source); ``start`` opts in
    via ``require_client_dist=True``.
    """
    if not node_modules_dir(root).exists() or not server_venv(root).exists():
        console.print('[red]Error: Project not built. Run "machina build" first.[/]')
        raise typer.Exit(code=1)
    if require_client_dist and not client_dist_entry(root).exists():
        console.print('[red]Error: Client not built. Run "machina build" first.[/]')
        raise typer.Exit(code=1)
