"""The ``chat_run_id`` column of the two usage tables.

A new database has the column and its index, and a database created before
the column gains both when it starts (``Database._migrate_user_settings``).
What a recorded call stores in the column is tested with the recorders.
"""

import os

import pytest
from sqlalchemy import text

TABLES = ("token_usage_metrics", "api_usage_metrics")

# The inline column adds, and the PRAGMA reads below, are SQLite's.
pytestmark = pytest.mark.skipif(bool(os.environ.get("TEST_POSTGRES_URL")), reason="SQLite schema checks")


def index_name(table: str) -> str:
    return f"ix_{table}_chat_run_id"


async def column_names(database, table: str) -> set:
    async with database.engine.begin() as conn:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        return {row[1] for row in result.fetchall()}


async def index_names(database, table: str) -> set:
    async with database.engine.begin() as conn:
        result = await conn.execute(text(f"PRAGMA index_list({table})"))
        return {row[1] for row in result.fetchall()}


@pytest.mark.parametrize("table", TABLES)
async def test_a_new_database_has_the_column_and_its_index(database, table):
    assert "chat_run_id" in await column_names(database, table)
    assert index_name(table) in await index_names(database, table)


@pytest.mark.parametrize("table", TABLES)
async def test_an_older_database_gains_the_column_and_its_index(database, table):
    async with database.engine.begin() as conn:
        await conn.execute(text(f"DROP INDEX {index_name(table)}"))
        await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN chat_run_id"))

    await database._migrate_user_settings()

    assert "chat_run_id" in await column_names(database, table)
    assert index_name(table) in await index_names(database, table)
