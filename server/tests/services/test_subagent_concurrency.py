from __future__ import annotations

import asyncio
import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
async def concurrency_database():
    module_name = "tests._real_concurrency_database"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).resolve().parents[2] / "core" / "database.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = Path.cwd() / f".subagent-concurrency-{uuid.uuid4().hex}.db"
    database = module.Database(SimpleNamespace(
        database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
        database_echo=False, database_pool_size=5, database_max_overflow=5,
    ))
    await database.startup()
    try:
        yield database
    finally:
        await database.shutdown()
        sys.modules.pop(module_name, None)
        for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            candidate.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_root_limit_is_atomic_across_parallel_acquires(concurrency_database):
    results = await asyncio.gather(*(
        concurrency_database.acquire_subagent_permit("root-1", f"call-{i}", 3)
        for i in range(10)
    ))
    assert sum(results) == 3


@pytest.mark.asyncio
async def test_acquire_and_release_are_idempotent(concurrency_database):
    assert await concurrency_database.acquire_subagent_permit("root-1", "call-1", 1)
    assert await concurrency_database.acquire_subagent_permit("root-1", "call-1", 1)
    assert not await concurrency_database.acquire_subagent_permit("root-1", "call-2", 1)
    assert await concurrency_database.release_subagent_permit("root-1", "call-1")
    assert await concurrency_database.release_subagent_permit("root-1", "call-1")
    assert await concurrency_database.acquire_subagent_permit("root-1", "call-2", 1)

