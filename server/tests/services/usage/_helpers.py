"""Usage rows for the tests, written straight into the two tables."""

from models.database import APIUsageMetric, TokenUsageMetric


async def _add(database, row) -> None:
    async with database.get_session() as session:
        session.add(row)
        await session.commit()


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
