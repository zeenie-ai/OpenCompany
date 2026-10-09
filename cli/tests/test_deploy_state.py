"""Persisted deployment state: named deployments and rebrand compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.commands.deploy import _state


def _isolate_roots(monkeypatch, tmp_path: Path) -> Path:
    data = tmp_path / "data"
    monkeypatch.setattr(_state, "user_data_dir", lambda: data)
    monkeypatch.setattr(_state, "project_root", lambda: tmp_path / "repo")
    return data


def test_existing_legacy_state_is_reused(monkeypatch, tmp_path: Path) -> None:
    data = _isolate_roots(monkeypatch, tmp_path)
    legacy = data / "deploy" / "machinaos"
    legacy.mkdir(parents=True)
    (legacy / "deploy-meta.json").write_text(
        json.dumps({"provider": "gcp"}), encoding="utf-8"
    )

    assert _state.workdir() == legacy
    assert _state.resource_name() == "machinaos"


def test_new_deployments_use_opencompany(monkeypatch, tmp_path: Path) -> None:
    data = _isolate_roots(monkeypatch, tmp_path)
    current = data / "deploy" / "opencompany"
    current.mkdir(parents=True)
    (current / "deploy-meta.json").write_text(
        json.dumps({"provider": "gcp", "resource_name": "opencompany"}),
        encoding="utf-8",
    )

    assert _state.workdir() == current
    assert _state.resource_name() == "opencompany"


def test_named_deployments_keep_their_own_state(monkeypatch, tmp_path: Path) -> None:
    data = _isolate_roots(monkeypatch, tmp_path)

    _state.write_meta({"provider": "aws"}, "acme-corp")

    assert _state.workdir("acme-corp") == data / "deploy" / "acme-corp"
    assert _state.resource_name("acme-corp") == "acme-corp"
    assert _state.read_meta("acme-corp") == {"provider": "aws"}
    assert _state.read_meta() is None


def test_named_deployments_never_adopt_legacy_state(monkeypatch, tmp_path: Path) -> None:
    data = _isolate_roots(monkeypatch, tmp_path)
    legacy = data / "deploy" / "machinaos"
    legacy.mkdir(parents=True)
    (legacy / "deploy-meta.json").write_text(json.dumps({"provider": "gcp"}), encoding="utf-8")

    assert _state.workdir("acme-corp") == data / "deploy" / "acme-corp"
    assert _state.workdir() == legacy


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("opencompany", True),
        ("machinaos", True),
        ("acme-corp-01", True),
        ("abc", False),
        ("Acme-corp", False),
        ("acme-", False),
        ("1acme", False),
        ("acme_corp", False),
        ("a" * 31, False),
    ],
)
def test_names_must_be_valid_cloud_ids(name: str, valid: bool) -> None:
    assert bool(_state.NAME_PATTERN.fullmatch(name)) is valid
