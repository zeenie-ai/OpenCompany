"""Provider CLI adapters -- Stage 1 of ``company deploy``.

Each adapter wraps the operator's already-installed cloud CLI (gcloud / aws)
to verify it is present + authenticated, resolve the project/region/zone,
ensure Terraform can authenticate with the same credentials, and enable the
required cloud APIs. Adapters create NO cloud resources -- Terraform (Stage 2)
does that, inheriting the CLI's credentials.
"""

from __future__ import annotations

import typer

from ._base import ProviderCli


def get_provider(name: str) -> ProviderCli:
    """Resolve the CLI adapter for ``name`` (lazy-imports the impl)."""
    if name == "gcp":
        from .gcp import GcpCli

        return GcpCli()
    if name == "aws":
        from .aws import AwsCli

        return AwsCli()

    from cli._common import error_block

    error_block(f"Unknown provider {name!r}.", ["Supported: gcp (aws is a follow-on)."])
    raise typer.Exit(code=1)


__all__ = ["ProviderCli", "get_provider"]
