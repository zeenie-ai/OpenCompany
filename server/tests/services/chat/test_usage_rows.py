"""The ``chat_run_id`` column of ``token_usage_metrics``.

Run against real SQLite: a new database has the column and its index, and a
database created before the column gains both. What a recorded call stores in
it is tested with the recorder (``tests/services/usage/test_recording_llm.py``).
"""

from sqlalchemy import text

INDEX_NAME = "ix_token_usage_metrics_chat_run_id"


async def column_names(database) -> set:
    async with database.engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(token_usage_metrics)"))
        return {row[1] for row in result.fetchall()}


async def index_names(database) -> set:
    async with database.engine.begin() as conn:
        result = await conn.execute(text("PRAGMA index_list(token_usage_metrics)"))
        return {row[1] for row in result.fetchall()}


async def test_a_new_database_has_the_column_and_its_index(database):
    assert "chat_run_id" in await column_names(database)
    assert INDEX_NAME in await index_names(database)


async def test_an_older_database_gains_the_column_and_its_index(database):
    async with database.engine.begin() as conn:
        await conn.execute(text(f"DROP INDEX {INDEX_NAME}"))
        await conn.execute(text("ALTER TABLE token_usage_metrics DROP COLUMN chat_run_id"))

    await database._migrate_user_settings()

    assert "chat_run_id" in await column_names(database)
    assert INDEX_NAME in await index_names(database)
