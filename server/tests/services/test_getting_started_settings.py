"""Real-SQLite tests for the Get Started checklist settings columns.

Uses the file-loaded real Database (same fixture pattern as
test_subagent_concurrency.py) so schema creation, the ALTER TABLE
migration, and the save/get round-trip run against actual SQLite.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text

GETTING_STARTED_COLUMNS = [
    "getting_started_dismissed",
    "getting_started_added_key",
    "getting_started_ran_example",
    "getting_started_built_workflow",
    "getting_started_tried_theme",
]


@pytest.fixture
async def settings_database():
    module_name = "tests._real_settings_database"
    spec = importlib.util.spec_from_file_location(
        module_name, Path(__file__).resolve().parents[2] / "core" / "database.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    db_path = Path.cwd() / f".getting-started-{uuid.uuid4().hex}.db"
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
async def test_fresh_database_has_getting_started_columns(settings_database):
    async with settings_database.engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(user_settings)"))
        columns = {row[1] for row in result.fetchall()}
    for column in GETTING_STARTED_COLUMNS:
        assert column in columns


@pytest.mark.asyncio
async def test_save_get_round_trip_returns_getting_started_keys(settings_database):
    assert await settings_database.save_user_settings(
        {"getting_started_ran_example": True}, user_id="default"
    )
    settings = await settings_database.get_user_settings("default")
    assert settings is not None
    assert settings["getting_started_ran_example"] is True
    assert settings["getting_started_dismissed"] is False


@pytest.mark.asyncio
async def test_migration_backfills_dismissed_for_completed_onboarding(settings_database):
    # Simulate a pre-feature database: rows exist, new columns absent.
    assert await settings_database.save_user_settings(
        {"onboarding_completed": True}, user_id="veteran"
    )
    assert await settings_database.save_user_settings(
        {"onboarding_completed": False}, user_id="newbie"
    )
    async with settings_database.engine.begin() as conn:
        for column in GETTING_STARTED_COLUMNS:
            await conn.execute(text(f"ALTER TABLE user_settings DROP COLUMN {column}"))

    await settings_database._migrate_user_settings()

    async with settings_database.engine.begin() as conn:
        rows = await conn.execute(text(
            "SELECT user_id, getting_started_dismissed FROM user_settings"
        ))
        dismissed_by_user = dict(rows.fetchall())
    assert dismissed_by_user["veteran"] == 1
    assert dismissed_by_user["newbie"] == 0
