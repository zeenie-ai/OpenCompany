"""``machina deploy destroy`` -- tear down the deployment's cloud resources."""

from __future__ import annotations

import shutil

import typer

from cli._common import preflight
from cli.colors import console

from . import _state, _terraform


def destroy_command(*, keep_state: bool = False) -> None:
    # Establish the same DATA_DIR context as `deploy up` so workdir() matches.
    preflight()
    meta = _state.read_meta()
    if meta is None:
        console.print("[yellow]No 'machinaos' deployment found.[/]")
        raise typer.Exit(code=1)

    _terraform.ensure_terraform()
    wd = _state.workdir()

    console.log("terraform destroy (machinaos)...")
    _terraform.tf(wd, "destroy", "-auto-approve", "-input=false")

    if keep_state:
        console.print(f"  Destroyed. State kept at {wd}")
        return

    shutil.rmtree(wd, ignore_errors=True)
    console.print("  [green]Destroyed[/] and removed local state for 'machinaos'.")
