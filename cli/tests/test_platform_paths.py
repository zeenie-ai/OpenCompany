"""Compatibility tests for OpenCompany user-state path selection."""

from pathlib import Path

from cli import platform_


def _state_roots(tmp_path: Path) -> tuple[Path, Path]:
    current = tmp_path / ".opencompany"
    legacy = tmp_path / ".machina"
    current.mkdir()
    legacy.mkdir()
    return current, legacy


def test_seed_only_current_root_does_not_mask_legacy_state(
    tmp_path: Path, monkeypatch
) -> None:
    current, legacy = _state_roots(tmp_path)
    (current / "workflows").mkdir()
    (current / "workflows" / "example.json").write_text("{}", encoding="utf-8")
    (legacy / "credentials.db").write_text("legacy", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(current))

    assert platform_.user_data_dir() == legacy


def test_current_runtime_state_wins_over_legacy_state(
    tmp_path: Path, monkeypatch
) -> None:
    current, legacy = _state_roots(tmp_path)
    (current / "workflows").mkdir()
    (current / "workflow.db").write_text("current", encoding="utf-8")
    (legacy / "workflow.db").write_text("legacy", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(current))

    assert platform_.user_data_dir() == current


def test_custom_data_dir_is_never_rewritten(tmp_path: Path, monkeypatch) -> None:
    configured = tmp_path / "operator-selected"
    configured.mkdir()
    (tmp_path / ".machina").mkdir()
    (tmp_path / ".machina" / "workflow.db").write_text("legacy", encoding="utf-8")
    monkeypatch.setenv("DATA_DIR", str(configured))

    assert platform_.user_data_dir() == configured
