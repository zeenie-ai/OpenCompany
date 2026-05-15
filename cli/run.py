"""Shared subprocess helpers used across ``cli.commands``.

Two patterns covering ~95% of subprocess use in this CLI:

* :func:`run` -- inherit-stdio fire-and-forget; raises ``typer.Exit`` on
  non-zero exit unless ``check=False`` is passed (matches the existing
  build.py / start.py / dev.py call shape).
* :func:`capture` -- run silently, return stdout (stripped) on success
  or ``None`` on missing binary / non-zero exit. Tolerates failure by
  design; the caller branches on truthiness.

Centralising these two functions removes the per-file ``_run`` /
``_capture`` / ``_git_describe`` duplicates that previously diverged
in subtle ways (one had inverted ``ignore_error`` semantics, another
swallowed stderr, etc.).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import typer

from cli.colors import console


def which_argv(argv: list[str]) -> list[str]:
    """Resolve argv[0] via PATH (PATHEXT-aware on Windows for .cmd/.bat).

    ``subprocess.run`` / ``anyio.open_process`` with ``shell=False`` do not
    consult PATHEXT, so on Windows ``["npm", ...]`` raises FileNotFoundError
    because the launcher is ``npm.cmd``. ``shutil.which`` mirrors what the
    shell would resolve. Shared by ``run`` / ``capture`` and the supervisor
    so all subprocess entry points behave identically.
    """
    if not argv:
        return argv
    return [shutil.which(argv[0]) or argv[0], *argv[1:]]


def run(
    argv: list[str],
    *,
    cwd: Path | str | None = None,
    check: bool = True,
) -> int:
    """Inherit-stdio run; raises :class:`typer.Exit` on non-zero when ``check``."""
    resolved = which_argv(argv)
    try:
        proc = subprocess.run(resolved, cwd=str(cwd) if cwd else None)
    except FileNotFoundError:
        console.print(f"[red]Command not found:[/] {argv[0] if argv else ''}")
        if check:
            raise typer.Exit(code=127)
        return 127
    if check and proc.returncode != 0:
        console.print(f"[red]Command failed:[/] {' '.join(argv)}")
        raise typer.Exit(code=proc.returncode)
    return proc.returncode


def capture(argv: list[str], *, cwd: Path | str | None = None) -> str | None:
    """Capture stdout (or stderr fallback); ``None`` if binary missing or fails.

    Used for "is this tool installed and what version" queries -- always
    tolerant, never raises.
    """
    try:
        result = subprocess.run(
            which_argv(argv),
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
        )
        return result.stdout.strip() or result.stderr.strip() or None
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
