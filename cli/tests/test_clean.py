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
        "client/dist",
        "client/.vite",
        "server/data",
        "server/.venv",
        ".venv",
    ]


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
