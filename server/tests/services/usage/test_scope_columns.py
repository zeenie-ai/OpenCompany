"""The columns of the two usage tables that say what a call worked for:
``chat_run_id`` and ``employee_job_id``.

A new database has each column and its index, and a database created before
a column gains both when it starts (``Database._migrate_user_settings``).
What a recorded call stores in them is tested with the recorders.
"""

import os

import pytest
from sqlalchemy import text

TABLES = ("token_usage_metrics", "api_usage_metrics")
COLUMNS = ("chat_run_id", "employee_job_id")

# The inline column adds, and the PRAGMA reads below, are SQLite's.
pytestmark = pytest.mark.skipif(bool(os.environ.get("TEST_POSTGRES_URL")), reason="SQLite schema checks")


def index_name(table: str, column: str) -> str:
    return f"ix_{table}_{column}"


async def column_names(database, table: str) -> set:
    async with database.engine.begin() as conn:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        return {row[1] for row in result.fetchall()}


async def index_names(database, table: str) -> set:
    async with database.engine.begin() as conn:
        result = await conn.execute(text(f"PRAGMA index_list({table})"))
        return {row[1] for row in result.fetchall()}


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("column", COLUMNS)
async def test_a_new_database_has_the_column_and_its_index(database, table, column):
    assert column in await column_names(database, table)
    assert index_name(table, column) in await index_names(database, table)


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("column", COLUMNS)
async def test_an_older_database_gains_the_column_and_its_index(database, table, column):
    async with database.engine.begin() as conn:
        await conn.execute(text(f"DROP INDEX {index_name(table, column)}"))
        await conn.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))

    await database._migrate_user_settings()

    assert column in await column_names(database, table)
    assert index_name(table, column) in await index_names(database, table)
