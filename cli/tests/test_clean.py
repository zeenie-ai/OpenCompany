"""Smoke tests for ``cli.commands.clean``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cli.commands import clean


def test_targets_list_is_stable():
    """Pin the cleanup target list so accidental edits surface in review."""
    assert clean._TARGETS == [
        "node_modules",
        "client/node_modules",
        "server/nodejs/node_modules",
        "client/dist",
        "client/.vite",
        "server/.venv",
        ".venv",
    ]


def test_opencompany_keep_preserves_workflows_deploy_and_packages():
    """``workflows/`` (shipped example seeds), ``deploy/`` (``company
    deploy``'s Terraform working dirs + state for LIVE cloud resources —
    wiping it orphans the VM/firewall; only ``deploy destroy`` removes it)
    and ``packages/`` (OpenCompany-managed binaries: Temporal CLI ~114 MB,
    Stripe CLI, shared npm tree — re-fetchable but expensive; wiping it
    forced a full Temporal re-download on every clean+build cycle) live
    under either supported state root; ``clean`` must never wipe any of them. Pin the
    keep list so accidental additions/removals surface in review."""
    assert clean._OPENCOMPANY_KEEP == frozenset({"workflows", "deploy", "packages"})
    assert clean._STATE_DIRS == (".opencompany", ".machina")


def test_clean_removes_each_database_with_its_wal_shm_and_journal_files(tmp_path: Path):
    """``workflow.db`` and ``temporal.db`` run in SQLite's WAL mode, and a
    ``-wal`` file left behind is applied to the next copy of the database put
    in its place. ``clean`` removes every file in the state folders, so each
    database goes with its ``-wal``, ``-shm`` and ``-journal`` files."""
    state = tmp_path / ".opencompany"
    for kept in ("workflows", "deploy", "packages"):
        (state / kept).mkdir(parents=True)
    for name in ("workflow.db", "workflow.db-wal", "workflow.db-shm", "credentials.db", "credentials.db-journal"):
        (state / name).write_bytes(b"x")

    with (
        patch.object(clean, "preflight", return_value=(None, tmp_path)),
        patch.object(clean, "_kill_running_processes", return_value=False),
    ):
        clean.clean_command()

    assert sorted(child.name for child in state.iterdir()) == ["deploy", "packages", "workflows"]


def test_rmtree_with_retry_handles_oserror(tmp_path: Path):
    """If rmtree fails 3 times, the helper returns False without raising."""
    target = tmp_path / "doesnotmatter"
    target.mkdir()
    with patch.object(clean.shutil, "rmtree", side_effect=OSError("locked")):
        assert clean._rmtree_with_retry(target, attempts=3, delay=0) is False


def test_rmtree_with_retry_succeeds_on_first_try(tmp_path: Path):
    """Happy path -- a present directory is removed cleanly."""
    target = tmp_path / "doomed"
    target.mkdir()
    (target / "file.txt").write_text("x")
    assert clean._rmtree_with_retry(target) is True
    assert not target.exists()
