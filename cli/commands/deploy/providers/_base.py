"""The provider-CLI adapter contract (Stage 1 of ``machina deploy``)."""

from __future__ import annotations

from typing import Protocol


class ProviderCli(Protocol):
    """Auth + context + API-enablement over a cloud provider's own CLI.

    All methods shell out via ``cli.run``; failures abort with ``typer.Exit``
    and ``error_block`` remediation. None of these create cloud resources --
    Terraform (Stage 2) owns that, using the credentials these methods verify.
    """

    name: str

    def check(self) -> None:
        """Abort unless the provider CLI binary is on PATH."""
        ...

    def authed_account(self) -> str | None:
        """Return the logged-in identity, or ``None`` if not authenticated."""
        ...

    def resolve_context(
        self, *, region: str | None, zone: str | None, project: str | None
    ) -> dict:
        """Resolve + validate ``{project, region, zone, ...}`` from CLI config + flags.

        Aborts (with guidance) if the CLI is not logged in or required context
        (e.g. a GCP project) is missing.
        """
        ...

    def ensure_terraform_auth(self) -> None:
        """Ensure Terraform's provider can authenticate (e.g. gcloud ADC)."""
        ...

    def enable_apis(self, ctx: dict) -> None:
        """Enable the cloud APIs Terraform will need."""
        ...

    def tfvars_extra(self, ctx: dict) -> dict:
        """Provider-specific tfvars keys merged into the deployment's tfvars."""
        ...
