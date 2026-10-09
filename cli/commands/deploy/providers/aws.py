"""aws CLI adapter -- Stage 1 of ``company deploy --provider aws``.

Uses the operator's installed + authenticated ``aws`` CLI (v2) for auth and
region; Terraform's ``aws`` provider then creates the resources with the same
identity, exported from the CLI's credential chain. The module
(``cli/terraform/aws``) installs a published release with ``install.sh``, so
``release`` is the only install source.
"""

from __future__ import annotations

import os

import typer

from cli._common import error_block
from cli.colors import console
from cli.run import capture

_LOGIN_HINT = "Run: aws login (or aws sso login / aws configure)"


class AwsCli:
    name = "aws"
    default_machine_type = "t3.micro"
    sources = ("release",)

    def check(self) -> None:
        if capture(["aws", "--version"]) is None:
            error_block(
                "The aws CLI was not found on PATH.",
                ["Install AWS CLI v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"],
            )
            raise typer.Exit(code=1)

    def authed_account(self) -> str | None:
        return capture(
            ["aws", "sts", "get-caller-identity", "--query", "Arn", "--output", "text", "--no-cli-pager"]
        )

    def resolve_context(self, *, region, zone, project) -> dict:
        account = self.authed_account()
        if not account:
            error_block("The aws CLI is not logged in, or its session expired.", [_LOGIN_HINT])
            raise typer.Exit(code=1)

        # The AWS CLI's own order: the environment, then the profile's config.
        reg = (
            region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
            or capture(["aws", "configure", "get", "region", "--no-cli-pager"])
            or "us-east-1"
        )

        console.print(f"  aws identity: {account}")
        console.print(f"  region={reg}")
        return {"region": reg}

    def ensure_terraform_auth(self) -> None:
        # Terraform's aws provider reads the standard credential chain but not
        # every source the CLI supports (an `aws login` console session, for
        # one). Export the credentials the CLI resolved into this process's
        # environment, so the terraform child process uses the same identity.
        exported = capture(
            ["aws", "configure", "export-credentials", "--format", "env-no-export", "--no-cli-pager"]
        )
        if exported is None:
            error_block("Terraform cannot get credentials from the aws CLI.", [_LOGIN_HINT])
            raise typer.Exit(code=1)
        for line in exported.splitlines():
            key, sep, value = line.partition("=")
            if sep and key.startswith("AWS_"):
                os.environ[key] = value

    def enable_apis(self, ctx: dict) -> None:
        # AWS has no per-service API switch. The module's one account
        # prerequisite is a default VPC in the region: it names no subnet.
        region = ctx["region"]
        vpc = capture(
            [
                "aws", "ec2", "describe-vpcs",
                "--region", region,
                "--filters", "Name=is-default,Values=true",
                "--query", "Vpcs[0].VpcId",
                "--output", "text",
                "--no-cli-pager",
            ]
        )
        if not vpc or vpc == "None":
            error_block(
                f"Region {region} has no default VPC.",
                [f"Create one: aws ec2 create-default-vpc --region {region}"],
            )
            raise typer.Exit(code=1)

    def tfvars_extra(self, ctx: dict) -> dict:
        return {"region": ctx["region"]}
