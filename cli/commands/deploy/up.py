"""``machina deploy up`` -- provision + run MachinaOs on a fresh cloud VM.

Two stages:
  STAGE 1 (cloud CLI): the provider adapter verifies the CLI is installed +
    authenticated, resolves project/region/zone, ensures Terraform auth, and
    enables the required cloud APIs.
  STAGE 2 (Terraform): generate secrets + owner creds -> (local source) npm
    pack -> write tfvars -> ``terraform init`` + ``apply`` -> read outputs ->
    poll ``/health`` -> print URL + credentials.

The VM instance is always named ``machinaos`` (one deployment per project).
"""

from __future__ import annotations

import time
import urllib.error
import urllib.request
from pathlib import Path

import typer

from cli._common import error_block, preflight
from cli.colors import console
from cli.run import capture

from . import _secrets, _state, _terraform
from .providers import get_provider


def _npm_pack(root: Path) -> str:
    """Run ``npm pack`` in the repo root; return the abs path to the tarball."""
    console.log("Packaging local build (npm pack)...")
    out = capture(["npm", "pack"], cwd=root)
    if not out:
        error_block(
            "`npm pack` produced no output.",
            ["Ensure Node/npm are installed and you are in a MachinaOs checkout."],
        )
        raise typer.Exit(code=1)
    tarball = out.strip().splitlines()[-1].strip()
    path = (root / tarball).resolve()
    if not path.is_file():
        error_block(f"npm pack reported {tarball!r} but it is not on disk.", [str(path)])
        raise typer.Exit(code=1)
    return str(path)


def _poll_health(url: str, *, attempts: int = 40, delay: float = 15.0) -> bool:
    """Poll ``<url>/health`` until 200 or attempts exhausted (~10 min)."""
    health = url.rstrip("/") + "/health"
    for i in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(health, timeout=5) as resp:  # noqa: S310
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        console.log(f"waiting for the VM to finish provisioning... ({i}/{attempts})")
        time.sleep(delay)
    return False


def up_command(
    *,
    provider: str,
    region: str | None,
    zone: str | None,
    machine_type: str,
    port: int,
    owner_email: str,
    owner_password: str | None,
    source: str,
    version: str,
    allow_cidr: str,
    project: str | None,
) -> None:
    _, root = preflight()

    # --- STAGE 1: cloud CLI (auth + context + APIs) ------------------------
    cli = get_provider(provider)
    cli.check()
    _terraform.ensure_terraform()
    ctx = cli.resolve_context(region=region, zone=zone, project=project)
    cli.ensure_terraform_auth()
    cli.enable_apis(ctx)

    if _state.exists() and (_state.workdir() / "terraform.tfstate").exists():
        console.print(
            "[yellow]A 'machinaos' deployment already exists. Re-running will "
            "re-apply Terraform (safe), or run `machina deploy destroy` first.[/]"
        )

    # --- STAGE 2: secrets + source + Terraform -----------------------------
    pw = owner_password or _secrets.new_password()
    pw_generated = owner_password is None
    app_env = _secrets.build_app_env(owner_email=owner_email, owner_password=pw, port=port)

    pack_tarball = _npm_pack(root) if source == "local" else ""

    tfvars: dict = {
        "machine_type": machine_type,
        "port": port,
        "allow_cidr": allow_cidr,
        "app_env": app_env,
        "source_mode": source,
        "machinaos_version": version,
        "pack_tarball": pack_tarball,
    }
    tfvars.update(cli.tfvars_extra(ctx))

    wd = _state.workdir()
    _terraform.prepare_workdir(wd, provider)
    _terraform.write_tfvars(wd, tfvars)
    _state.write_meta({"provider": provider, "port": port, "owner_email": owner_email})

    console.log("terraform init...")
    _terraform.tf(wd, "init", "-input=false")
    console.log("terraform apply (creating cloud resources)...")
    _terraform.tf(wd, "apply", "-auto-approve", "-input=false")

    ip = _terraform.tf_output(wd, "external_ip")
    url = _terraform.tf_output(wd, "url") or (f"http://{ip}:{port}" if ip else None)

    console.print()
    console.print("  [bold green]VM 'machinaos' provisioned.[/]")
    if ip:
        console.print(f"  External IP: {ip}")
    if url:
        console.print(f"  URL:         {url}")
    console.print(f"  Login email: {owner_email}")
    if pw_generated:
        console.print(f"  Login password (save this now): [bold]{pw}[/]")
    else:
        console.print("  Login password: (the one you provided)")
    console.print()

    if url:
        console.log("The VM is installing MachinaOs (Node + npm + build); this takes a few minutes.")
        if _poll_health(url):
            console.print(f"  [bold green]Ready.[/] Open {url} and log in.")
        else:
            console.print(
                "  [yellow]Still provisioning.[/] Check again with "
                "`machina deploy status` in a few minutes."
            )
