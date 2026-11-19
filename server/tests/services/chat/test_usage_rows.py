"""Usage rows record the chat run they worked for (``token_usage_metrics.chat_run_id``).

Run against real SQLite: a row keeps its run, a call outside a chat run keeps
none, and a database created before the column gains it with its index.
"""

from sqlalchemy import text

from services.employees.llm import LLMChoice, record_llm_usage
from services.llm.protocol import LLMResponse, Usage

CHOICE = LLMChoice(provider="openrouter", model="anthropic/claude-sonnet-5.5", local=False)
INDEX_NAME = "ix_token_usage_metrics_chat_run_id"


def answer() -> LLMResponse:
    return LLMResponse(content="done", usage=Usage(input_tokens=50, output_tokens=7))


async def stored_run_ids(database) -> list:
    async with database.engine.begin() as conn:
        result = await conn.execute(text("SELECT chat_run_id FROM token_usage_metrics ORDER BY id"))
        return [row[0] for row in result.fetchall()]


async def column_names(database) -> set:
    async with database.engine.begin() as conn:
        result = await conn.execute(text("PRAGMA table_info(token_usage_metrics)"))
        return {row[1] for row in result.fetchall()}


async def index_names(database) -> set:
    async with database.engine.begin() as conn:
        result = await conn.execute(text("PRAGMA index_list(token_usage_metrics)"))
        return {row[1] for row in result.fetchall()}


async def test_a_row_keeps_the_chat_run_it_worked_for(database):
    await record_llm_usage(database, CHOICE, answer(), session_id="wf", chat_run_id="r_1")

    assert await stored_run_ids(database) == ["r_1"]


async def test_a_call_outside_a_chat_run_keeps_none(database):
    await record_llm_usage(database, CHOICE, answer(), session_id="wf")

    assert await stored_run_ids(database) == [None]


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
