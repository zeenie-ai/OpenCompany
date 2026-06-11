"""gcloud adapter -- Stage 1 of ``machina deploy --provider gcp``.

Uses the operator's installed + authenticated ``gcloud`` CLI for auth/context/
API-enablement; Terraform's ``google`` provider then reuses the same
Application Default Credentials to create the resources.
"""

from __future__ import annotations

import typer

from cli._common import error_block
from cli.colors import console
from cli.run import capture, run

_REQUIRED_APIS = [
    "compute.googleapis.com",
    "storage.googleapis.com",
    "iam.googleapis.com",
]


def _clean(value: str | None) -> str | None:
    """Normalise gcloud config reads (``(unset)`` / empty -> None)."""
    if not value:
        return None
    v = value.strip()
    return None if v in ("", "(unset)") else v


class GcpCli:
    name = "gcp"

    def check(self) -> None:
        if capture(["gcloud", "version"]) is None:
            error_block(
                "The gcloud CLI was not found on PATH.",
                ["Install the Google Cloud SDK: https://cloud.google.com/sdk/docs/install"],
            )
            raise typer.Exit(code=1)

    def authed_account(self) -> str | None:
        return _clean(
            capture(
                ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"]
            )
        )

    def resolve_context(self, *, region, zone, project) -> dict:
        account = self.authed_account()
        if not account:
            error_block(
                "gcloud is not logged in.",
                ["Run: gcloud auth login"],
            )
            raise typer.Exit(code=1)

        proj = project or _clean(capture(["gcloud", "config", "get-value", "project"]))
        if not proj:
            error_block(
                "No GCP project is set.",
                ["Pass --project <id>, or run: gcloud config set project <id>"],
            )
            raise typer.Exit(code=1)

        reg = region or _clean(capture(["gcloud", "config", "get-value", "compute/region"])) or "us-central1"
        zn = zone or _clean(capture(["gcloud", "config", "get-value", "compute/zone"])) or "us-central1-a"

        console.print(f"  gcloud account: {account}")
        console.print(f"  project={proj}  region={reg}  zone={zn}")
        return {"project": proj, "region": reg, "zone": zn}

    def ensure_terraform_auth(self) -> None:
        # Terraform's google provider authenticates via Application Default
        # Credentials. print-access-token exits non-zero (capture -> None) when
        # ADC is not configured.
        if capture(["gcloud", "auth", "application-default", "print-access-token"]) is None:
            error_block(
                "Terraform needs Application Default Credentials.",
                ["Run: gcloud auth application-default login"],
            )
            raise typer.Exit(code=1)

    def enable_apis(self, ctx: dict) -> None:
        console.log("Enabling required GCP APIs (compute, storage, iam)...")
        run(["gcloud", "services", "enable", *_REQUIRED_APIS, "--project", ctx["project"]])

    def tfvars_extra(self, ctx: dict) -> dict:
        return {"project": ctx["project"], "region": ctx["region"], "zone": ctx["zone"]}
