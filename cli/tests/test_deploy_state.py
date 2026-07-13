"""Rebrand compatibility tests for persisted deployment state."""

from __future__ import annotations

import json
from pathlib import Path

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
