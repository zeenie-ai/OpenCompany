"""The aws adapter of ``company deploy`` (Stage 1), with the aws CLI faked."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
import typer

from cli.commands.deploy.providers import aws
from cli.commands.deploy.providers.aws import AwsCli


def _fake_capture(responses: dict[tuple[str, ...], str | None]):
    """``capture`` stand-in answering by argv prefix."""

    def capture(argv, cwd=None):
        for prefix, out in responses.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return out
        raise AssertionError(f"unexpected command: {argv}")

    return capture


_LOGGED_IN = {("aws", "sts", "get-caller-identity"): "arn:aws:iam::123456789012:user/ops"}


@pytest.mark.parametrize(
    ("flag", "env", "config", "expected"),
    [
        ("eu-west-1", {"AWS_REGION": "ap-south-1"}, "us-west-2", "eu-west-1"),
        (None, {"AWS_REGION": "ap-south-1", "AWS_DEFAULT_REGION": "ca-central-1"}, "us-west-2", "ap-south-1"),
        (None, {"AWS_DEFAULT_REGION": "ca-central-1"}, "us-west-2", "ca-central-1"),
        (None, {}, "us-west-2", "us-west-2"),
        (None, {}, None, "us-east-1"),
    ],
)
def test_region_follows_the_aws_cli_order(monkeypatch, flag, env, config, expected) -> None:
    for key in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    responses = {**_LOGGED_IN, ("aws", "configure", "get", "region"): config}
    with patch.object(aws, "capture", side_effect=_fake_capture(responses)):
        ctx = AwsCli().resolve_context(region=flag, zone=None, project=None)
    assert ctx == {"region": expected}
    assert AwsCli().tfvars_extra(ctx) == {"region": expected}


def test_not_logged_in_stops_before_terraform() -> None:
    responses = {("aws", "sts", "get-caller-identity"): None}
    with patch.object(aws, "capture", side_effect=_fake_capture(responses)), pytest.raises(typer.Exit) as exc:
        AwsCli().resolve_context(region="us-east-1", zone=None, project=None)
    assert exc.value.exit_code == 1


def test_terraform_gets_the_credentials_the_cli_resolved() -> None:
    exported = (
        "AWS_ACCESS_KEY_ID=ASIAEXAMPLE\n"
        "AWS_SECRET_ACCESS_KEY=abc/def+ghi=\n"
        "AWS_SESSION_TOKEN=token==\n"
        "AWS_CREDENTIAL_EXPIRATION=2026-10-09T12:00:00+00:00"
    )
    responses = {("aws", "configure", "export-credentials"): exported}
    with patch.dict(os.environ), patch.object(aws, "capture", side_effect=_fake_capture(responses)):
        AwsCli().ensure_terraform_auth()
        assert os.environ["AWS_ACCESS_KEY_ID"] == "ASIAEXAMPLE"
        assert os.environ["AWS_SECRET_ACCESS_KEY"] == "abc/def+ghi="
        assert os.environ["AWS_SESSION_TOKEN"] == "token=="


def test_no_exportable_credentials_stops_before_terraform() -> None:
    responses = {("aws", "configure", "export-credentials"): None}
    with patch.object(aws, "capture", side_effect=_fake_capture(responses)), pytest.raises(typer.Exit):
        AwsCli().ensure_terraform_auth()


@pytest.mark.parametrize(("vpc", "ok"), [("vpc-0abc", True), ("None", False), (None, False)])
def test_a_default_vpc_is_required(vpc, ok) -> None:
    responses = {("aws", "ec2", "describe-vpcs"): vpc}
    with patch.object(aws, "capture", side_effect=_fake_capture(responses)):
        if ok:
            AwsCli().enable_apis({"region": "us-east-1"})
        else:
            with pytest.raises(typer.Exit):
                AwsCli().enable_apis({"region": "us-east-1"})


def test_missing_cli_is_reported() -> None:
    with patch.object(aws, "capture", return_value=None), pytest.raises(typer.Exit):
        AwsCli().check()
