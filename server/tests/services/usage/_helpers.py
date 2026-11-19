"""Usage rows for the tests, written straight into the two tables and read back."""

from sqlmodel import select

from models.database import APIUsageMetric, TokenUsageMetric


def unavailable():
    raise RuntimeError("database down")


async def _add(database, row) -> None:
    async with database.get_session() as session:
        session.add(row)
        await session.commit()


async def model_rows(database) -> list:
    """Every ``token_usage_metrics`` row, oldest first."""
    async with database.get_session() as session:
        result = await session.execute(select(TokenUsageMetric).order_by(TokenUsageMetric.id))
        return result.scalars().all()


async def api_rows(database) -> list:
    """Every ``api_usage_metrics`` row, oldest first."""
    async with database.get_session() as session:
        result = await session.execute(select(APIUsageMetric).order_by(APIUsageMetric.id))
        return result.scalars().all()


async def add_model_call(database, **fields) -> None:
    """One ``token_usage_metrics`` row; ``fields`` override the defaults."""
    values = {"session_id": "wf", "node_id": "agent", "provider": "openai", "model": "gpt-a"}
    values.update(fields)
    await _add(database, TokenUsageMetric(**values))


async def add_api_call(database, **fields) -> None:
    """One ``api_usage_metrics`` row; ``fields`` override the defaults."""
    values = {"session_id": "wf", "node_id": "node", "service": "twitter", "operation": "content_create", "endpoint": "tweet"}
    values.update(fields)
    await _add(database, APIUsageMetric(**values))
