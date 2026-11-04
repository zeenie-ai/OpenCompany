"""workflow.db runs in SQLite's WAL mode with an explicit busy timeout
(RFC-0005 N9). With the default rollback journal, a reader that kept its
transaction open made every commit wait out the busy timeout and fail with
"database is locked"; in WAL mode the reader keeps its snapshot and the
commit goes through."""

from __future__ import annotations

import pytest


@pytest.fixture
async def sqlite_database(real_database):
    if real_database.engine.dialect.name != "sqlite":
        pytest.skip("WAL is SQLite's; TEST_POSTGRES_URL runs these on PostgreSQL")
    return real_database


async def test_every_connection_runs_in_wal_with_a_busy_timeout(sqlite_database):
    async with sqlite_database.engine.connect() as first, sqlite_database.engine.connect() as second:
        for connection in (first, second):
            assert (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar() == "wal"
            assert (await connection.exec_driver_sql("PRAGMA busy_timeout")).scalar() == 5000
            assert (await connection.exec_driver_sql("PRAGMA synchronous")).scalar() == 1  # NORMAL


async def test_a_commit_goes_through_while_a_reader_keeps_its_transaction(sqlite_database):
    async with sqlite_database.engine.connect() as reader:
        await reader.exec_driver_sql("BEGIN")
        before = (await reader.exec_driver_sql("SELECT count(*) FROM identity_sequences")).scalar()

        assert await sqlite_database.allocate_workflow_id()

        # The reader still sees the snapshot its transaction started with.
        assert (await reader.exec_driver_sql("SELECT count(*) FROM identity_sequences")).scalar() == before
        await reader.rollback()
