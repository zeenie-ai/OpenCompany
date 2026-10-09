"""``company deploy destroy`` -- tear down the deployment's cloud resources."""

from __future__ import annotations

import shutil

import typer

from cli._common import preflight
from cli.colors import console

from . import _state, _terraform
from .providers import get_provider


def destroy_command(*, name: str = _state.NAME, keep_state: bool = False) -> None:
    # Establish the same DATA_DIR context as `deploy up` so workdir() matches.
    preflight()
    meta = _state.read_meta(name)
    if meta is None:
        console.print("[yellow]No OpenCompany deployment found.[/]")
        raise typer.Exit(code=1)

    # Terraform needs the provider's credentials to delete what it created.
    cli = get_provider(meta["provider"])
    cli.check()
    _terraform.ensure_terraform()
    cli.ensure_terraform_auth()
    wd = _state.workdir(name)

    console.log(f"terraform destroy ({_state.resource_name(name)})...")
    _terraform.tf(wd, "destroy", "-auto-approve", "-input=false")

    if keep_state:
        console.print(f"  Destroyed. State kept at {wd}")
        return

    shutil.rmtree(wd, ignore_errors=True)
    console.print("  [green]Destroyed[/] and removed local OpenCompany deployment state.")
