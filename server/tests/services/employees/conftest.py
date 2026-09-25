"""Fixtures for the Normal-mode employees package tests."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
async def real_database(tmp_path: Path):
    """A real ``core.database.Database`` on a throwaway SQLite file.

    The root conftest stubs ``core.database`` for fast plugin tests, so the
    real module is loaded privately (tests/services/agent_context does the
    same)."""
    module_name = f"tests._employees_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[3] / "core" / "database.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    db_path = tmp_path / f"employees-{uuid.uuid4().hex}.db"
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
