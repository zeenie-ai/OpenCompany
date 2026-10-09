"""``company deploy up`` wiring: option defaults per provider, and the
contract between the tfvars ``up`` writes and each provider module."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

from cli.commands.deploy import up
from cli.commands.deploy.providers import get_provider
from cli.platform_ import project_root

_CONTEXT = {
    "gcp": {"project": "demo-project", "region": "us-central1", "zone": "us-central1-a"},
    "aws": {"region": "us-east-1"},
}


def _module_variables(provider: str) -> dict[str, bool]:
    """``{name: has_default}`` for every variable the provider module declares."""
    text = (project_root() / "cli" / "terraform" / provider / "variables.tf").read_text(encoding="utf-8")
    blocks = re.split(r'^variable "', text, flags=re.MULTILINE)[1:]
    return {block.split('"', 1)[0]: re.search(r"^\s*default\s*=", block, re.MULTILINE) is not None for block in blocks}


def _module_outputs(provider: str) -> set[str]:
    text = (project_root() / "cli" / "terraform" / provider / "outputs.tf").read_text(encoding="utf-8")
    return set(re.findall(r'^output "([^"]+)"', text, re.MULTILINE))


def _up(provider: str, tmp_path: Path, written: dict, **overrides) -> None:
    adapter = get_provider(provider)
    kwargs = {
        "name": "opencompany",
        "provider": provider,
        "region": None,
        "zone": None,
        "machine_type": None,
        "port": 4321,
        "owner_email": "owner@example.com",
        "owner_password": "correct-horse-battery",
        "source": None,
        "version": "latest",
        "allow_cidr": "203.0.113.4/32",
        "project": None,
        **overrides,
    }
    with (
        patch.object(up, "preflight", return_value=(None, tmp_path)),
        patch.object(up, "get_provider", return_value=adapter),
        patch.object(type(adapter), "check"),
        patch.object(type(adapter), "resolve_context", return_value=_CONTEXT[provider]),
        patch.object(type(adapter), "ensure_terraform_auth"),
        patch.object(type(adapter), "enable_apis"),
        patch.object(up._state, "read_meta", return_value=None),
        patch.object(up._state, "exists", return_value=False),
        patch.object(up._state, "workdir", return_value=tmp_path),
        patch.object(up._state, "write_meta"),
        patch.object(up._terraform, "ensure_terraform"),
        patch.object(up._terraform, "prepare_workdir"),
        patch.object(up._terraform, "write_tfvars", side_effect=lambda _wd, tfvars: written.update(tfvars)),
        patch.object(up._terraform, "tf"),
        patch.object(up._terraform, "tf_output", return_value=None),
        patch.object(up, "_npm_pack", return_value=str(tmp_path / "pkg.tgz")),
        patch.object(up.console, "print"),
        patch.object(up.console, "log"),
    ):
        up.up_command(**kwargs)


@pytest.mark.parametrize("provider", ["gcp", "aws"])
def test_tfvars_match_the_provider_module(provider: str, tmp_path: Path) -> None:
    """Every key ``up`` writes is a variable the module declares, and every
    variable without a default is written. The aws module used to diverge
    (``instance_type`` / ``instance_name``) and Terraform only warns about
    undeclared tfvars, so the mismatch was silent."""
    written: dict = {}
    _up(provider, tmp_path, written)
    declared = _module_variables(provider)
    assert set(written) <= set(declared)
    assert {name for name, has_default in declared.items() if not has_default} <= set(written)


@pytest.mark.parametrize("provider", ["gcp", "aws"])
def test_module_exports_the_outputs_up_and_status_read(provider: str) -> None:
    assert {"external_ip", "url"} <= _module_outputs(provider)


@pytest.mark.parametrize("provider", ["gcp", "aws"])
def test_defaults_come_from_the_provider(provider: str, tmp_path: Path) -> None:
    written: dict = {}
    _up(provider, tmp_path, written)
    adapter = get_provider(provider)
    assert written["machine_type"] == adapter.default_machine_type
    assert written["source_mode"] == adapter.sources[0]
    assert written["resource_name"] == "opencompany"


def test_aws_refuses_local_source_before_any_cloud_call(tmp_path: Path) -> None:
    adapter = get_provider("aws")
    with (
        patch.object(up, "preflight", return_value=(None, tmp_path)),
        patch.object(up._state, "read_meta", return_value=None),
        patch.object(up, "get_provider", return_value=adapter),
        patch.object(type(adapter), "check") as check,
        pytest.raises(typer.Exit) as exc,
    ):
        up.up_command(
            name="opencompany", provider="aws", region=None, zone=None, machine_type=None, port=4321,
            owner_email="owner@example.com", owner_password=None, source="local", version="latest",
            allow_cidr="0.0.0.0/0", project=None,
        )
    assert exc.value.exit_code == 1
    check.assert_not_called()


def test_a_name_belongs_to_one_provider(tmp_path: Path) -> None:
    """Applying aws's module in a gcp deployment's working dir would destroy
    the gcp resources, so ``up`` refuses before touching any cloud."""
    with (
        patch.object(up, "preflight", return_value=(None, tmp_path)),
        patch.object(up._state, "read_meta", return_value={"provider": "gcp"}),
        patch.object(up, "get_provider") as get,
        pytest.raises(typer.Exit),
    ):
        up.up_command(
            name="opencompany", provider="aws", region=None, zone=None, machine_type=None, port=4321,
            owner_email="owner@example.com", owner_password=None, source=None, version="latest",
            allow_cidr="0.0.0.0/0", project=None,
        )
    get.assert_not_called()


@pytest.mark.parametrize("name", ["abc", "Acme-corp", "acme-", "1acme", "a" * 31, "acme_corp"])
def test_invalid_names_are_refused(name: str, tmp_path: Path) -> None:
    with (
        patch.object(up, "preflight", return_value=(None, tmp_path)),
        patch.object(up, "get_provider") as get,
        pytest.raises(typer.Exit),
    ):
        up.up_command(
            name=name, provider="aws", region=None, zone=None, machine_type=None, port=4321,
            owner_email="owner@example.com", owner_password=None, source=None, version="latest",
            allow_cidr="0.0.0.0/0", project=None,
        )
    get.assert_not_called()
