"""Build-environment helpers shared by ``start`` / ``serve`` / ``dev``.

``validate_build`` refuses to launch when ``company build`` hasn't been
run. Layout knowledge lives in :mod:`cli.platform_` -- this file only
composes the documented prefixes (workspace venv, node_modules, client
dist) into a boolean check.

The long-running backend (``start`` / ``serve`` / ``dev`` / ``daemon``)
runs the server venv's interpreter directly via
:func:`cli.platform_.server_venv_python`, not ``uv run``, so no resident
``uv`` parent sits above it. :func:`cli.run.uv_run` remains for the
one-shot steps of ``company build``.
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


def validate_build(root: Path, *, dev: bool = False) -> None:
    """Refuse to launch if ``company build`` hasn't been run.

    Raises ``typer.Exit(1)`` with a remediation hint. Every verb needs the
    server venv. ``dev`` serves the client source through Vite, so it needs
    ``node_modules``; ``start`` and ``serve`` need the built client instead.
    A ``bun add -g`` install has no ``node_modules`` next to the package (bun
    keeps a global package's dependencies in its own global tree), so asking
    ``start`` / ``serve`` for it stopped every registry install, VMs included
    (docs-internal/errors.md #25).
    """
    if not server_venv(root).exists() or (dev and not node_modules_dir(root).exists()):
        console.print('[red]Error: Project not built. Run "company build" first.[/]')
        raise typer.Exit(code=1)
    if not dev and not client_dist_entry(root).exists():
        console.print('[red]Error: Client not built. Run "company build" first.[/]')
        raise typer.Exit(code=1)
