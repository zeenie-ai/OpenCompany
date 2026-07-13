"""``company deploy status`` -- show the deployment's URL + health."""

from __future__ import annotations

import urllib.error
import urllib.request

import typer

from cli._common import preflight
from cli.colors import console

from . import _state, _terraform


def status_command() -> None:
    # Establish the same DATA_DIR context as `deploy up` (load_config sets it)
    # so workdir() resolves to the directory `up` created.
    preflight()
    meta = _state.read_meta()
    if meta is None:
        console.print("[yellow]No OpenCompany deployment found.[/]")
        raise typer.Exit(code=1)

    wd = _state.workdir()
    ip = _terraform.tf_output(wd, "external_ip")
    url = _terraform.tf_output(wd, "url") or (
        f"http://{ip}:{meta.get('port', 8080)}" if ip else None
    )

    console.print("  Deployment:  OpenCompany")
    console.print(f"  Resource ID: {_state.resource_name()}")
    console.print(f"  Provider:    {meta.get('provider', '?')}")
    console.print(f"  External IP: {ip or '(unknown / destroyed)'}")
    console.print(f"  URL:         {url or '(unknown)'}")
    console.print(f"  Login email: {meta.get('owner_email', '?')}")

    if not url:
        raise typer.Exit(code=1)

    healthy = False
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=5) as resp:  # noqa: S310
            healthy = resp.status == 200
    except (urllib.error.URLError, OSError):
        healthy = False
    console.print(f"  Health:      {'[green]OK[/]' if healthy else '[yellow]not ready[/]'}")
    if not healthy:
        raise typer.Exit(code=1)
