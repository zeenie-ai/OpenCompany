"""Lock the ordering of ``BaseProcessSupervisor._do_start``.

The contract: ``_pre_spawn`` must run before ``binary_path``'s existence
check. Subclasses that download or materialise their binary inside
``_pre_spawn`` (notably ``TemporalServerRuntime``, which pooch-fetches
its binary on first call) depend on this — checking existence first
fails against an unresolved placeholder and the subprocess never starts.

Locking the order via test makes a future regression of this lifecycle
contract a loud test failure instead of a silent
``FileNotFoundError: <bin> not found`` at runtime.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Ensure server/ on sys.path (mirrors conftest.py)
SERVER_DIR = Path(__file__).resolve().parents[2]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from services._supervisor import BaseProcessSupervisor  # noqa: E402


class _DownloadOnPreSpawn(BaseProcessSupervisor):
    """Models the Temporal pattern: binary is materialised by _pre_spawn."""

    name = "fake-download"

    def __init__(self, tmp_path: Path) -> None:
        super().__init__()
        self._tmp = tmp_path
        self._binary: Path | None = None
        self.pre_spawn_calls = 0

    async def _pre_spawn(self) -> None:
        self.pre_spawn_calls += 1
        # Materialise the binary on disk — mirrors pooch fetching the
        # Temporal release tarball into the cache.
        target = self._tmp / "fake_binary"
        target.write_text("#!/bin/sh\nexit 0\n")
        target.chmod(0o755)
        self._binary = target

    def binary_path(self) -> Path:
        # Pre-``_pre_spawn`` placeholder; real path appears after.
        return self._binary if self._binary is not None else Path("/nonexistent")

    def argv(self) -> list[str]:
        return [str(self.binary_path())]


class _NoMaterialise(BaseProcessSupervisor):
    """Models a misconfigured subclass: binary missing, _pre_spawn is no-op."""

    name = "fake-broken"

    def binary_path(self) -> Path:
        return Path("/definitely/not/a/real/binary")

    def argv(self) -> list[str]:
        return [str(self.binary_path())]


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Per-class singleton storage on ``BaseSupervisor`` leaks across tests."""
    for klass in (BaseProcessSupervisor, _DownloadOnPreSpawn, _NoMaterialise):
        klass._instance = None
    yield
    for klass in (BaseProcessSupervisor, _DownloadOnPreSpawn, _NoMaterialise):
        klass._instance = None


@pytest.mark.asyncio
async def test_pre_spawn_runs_before_binary_existence_check(tmp_path):
    """The Temporal-failure scenario: binary materialised inside _pre_spawn.

    With the correct ordering, _do_start should:
      1. call _pre_spawn (which writes the binary to disk)
      2. then check binary_path().exists() — finds the freshly written file
      3. then spawn the subprocess (we mock anyio.open_process to avoid
         actually executing the shell stub).
    """
    sub = _DownloadOnPreSpawn(tmp_path)

    fake_proc = AsyncMock()
    fake_proc.pid = 4242
    fake_proc.returncode = None
    # ``_do_stop`` tree-kills the pid via psutil; bypass that for the
    # fake pid so test teardown doesn't reach the real OS process table.
    with (
        patch(
            "services._supervisor.process.anyio.open_process",
            new=AsyncMock(return_value=fake_proc),
        ),
        patch(
            "services._supervisor.util.kill_tree",
        ),
    ):
        await sub.start()

        assert sub.pre_spawn_calls == 1, "_pre_spawn must run before existence check"
        assert sub._binary is not None and sub._binary.exists()
        assert sub.is_running()

        # Mark the proc as exited so ``is_running`` flips false and the
        # supervisor's stop path short-circuits cleanly.
        fake_proc.returncode = 0
        await sub.stop()


@pytest.mark.asyncio
async def test_existence_check_still_raises_when_pre_spawn_doesnt_provide_binary():
    """Safety net: a misconfigured subclass still gets a clear error.

    Reordering _pre_spawn before the existence check must not remove the
    check itself — when nothing materialises the binary, the supervisor
    should still raise FileNotFoundError pointing at the missing path.
    """
    sub = _NoMaterialise()
    with pytest.raises(FileNotFoundError, match="fake-broken"):
        await sub.start()
    assert not sub.is_running()
