"""Terraform driver for ``machina deploy``.

Thin helpers over the ``terraform`` CLI (via ``cli.run``): preflight that it
exists, copy the chosen provider module into the deployment working dir, write
``terraform.tfvars.json``, and run init/apply/output/destroy. The CLI never
talks to a cloud API directly -- Terraform's providers do, using the same
credentials the cloud CLIs use (gcloud ADC / AWS default chain).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import typer

from cli._common import error_block
from cli.platform_ import project_root
from cli.run import capture, run


def ensure_terraform() -> None:
    """Abort with guidance if the ``terraform`` binary is not on PATH."""
    if capture(["terraform", "version"]) is None:
        error_block(
            "Terraform is required but was not found on PATH.",
            [
                "Install it: https://developer.hashicorp.com/terraform/install",
                "Then re-run `machina deploy up`.",
            ],
        )
        raise typer.Exit(code=1)


def module_src(provider: str) -> Path:
    """Path to the shipped HCL module for ``provider``."""
    return project_root() / "cli" / "terraform" / provider


def prepare_workdir(workdir: Path, provider: str) -> None:
    """Copy the provider module's files into ``workdir`` (preserving state)."""
    src = module_src(provider)
    if not src.is_dir():
        error_block(
            f"No Terraform module for provider {provider!r}.",
            [f"Expected at {src}", "Supported today: gcp (aws is a follow-on)."],
        )
        raise typer.Exit(code=1)
    workdir.mkdir(parents=True, exist_ok=True)
    # Copy module sources (*.tf, *.tftpl). Never touch tfstate / tfvars / meta.
    for f in src.iterdir():
        if f.is_file() and f.suffix in (".tf", ".tftpl"):
            shutil.copy2(f, workdir / f.name)


def write_tfvars(workdir: Path, variables: dict) -> None:
    (workdir / "terraform.tfvars.json").write_text(
        json.dumps(variables, indent=2), encoding="utf-8"
    )


def tf(workdir: Path, *args: str, check: bool = True) -> int:
    return run(["terraform", *args], cwd=workdir, check=check)


def tf_output(workdir: Path, name: str) -> str | None:
    return capture(["terraform", "output", "-raw", name], cwd=workdir)
