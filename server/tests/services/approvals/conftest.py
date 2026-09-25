"""Fixtures for the approval-step tests: a real database on a throwaway
file, and the container, broadcaster and wait timing pointed at it."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
async def real_database(tmp_path: Path):
    """A real ``core.database.Database`` (the root conftest stubs the module,
    so the real one is loaded privately, as tests/services/employees does)."""
    module_name = f"tests._approvals_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, Path(__file__).resolve().parents[3] / "core" / "database.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = tmp_path / f"approvals-{uuid.uuid4().hex}.db"
    settings = SimpleNamespace(
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        database_echo=False,
        database_pool_size=5,
        database_max_overflow=5,
    )
    database = module.Database(settings)
    await database.startup()
    try:
        yield database
    finally:
        await database.shutdown()
        sys.modules.pop(module_name, None)
        for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            candidate.unlink(missing_ok=True)


class FakeBroadcaster:
    def __init__(self):
        self.frames = []
        self.statuses = []

    async def broadcast(self, message):
        self.frames.append(message)

    async def update_node_status(self, node_id, status, data=None, workflow_id=None):
        self.statuses.append((node_id, status, data, workflow_id))


@pytest.fixture
def harness(monkeypatch, real_database):
    import core.container as container_module
    import services.status_broadcaster as status_broadcaster
    from services.approvals import reconcile, waiter

    import nodes.workflow.approval_gate as gate

    broadcaster = FakeBroadcaster()
    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: real_database))
    monkeypatch.setattr(status_broadcaster, "get_status_broadcaster", lambda: broadcaster)
    monkeypatch.setattr(gate, "POLL_SECONDS", 0.02)
    waiter.reset_for_tests()
    reconcile.reset_for_tests()
    yield SimpleNamespace(database=real_database, broadcaster=broadcaster, gate=gate)
    waiter.reset_for_tests()
