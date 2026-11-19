"""Recording a call that was made and paid for.

A recorder never raises. The call it records already happened, and callers
act on an exception: an agent step would fail after its provider billed it.
A failed price or write is logged as a warning instead.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

from core.logging import get_logger
from models.database import TokenUsageMetric

logger = get_logger(__name__)

TOKEN_KINDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "reasoning_tokens",
)
NO_COST = {"input_cost": 0.0, "output_cost": 0.0, "cache_cost": 0.0, "reasoning_cost": 0.0, "total_cost": 0.0}


async def _save(row: Any, database: Any) -> None:
    if database is None:
        from core.container import container  # looked up when called: plugins import this package

        database = container.database()
    async with database.get_session() as session:
        session.add(row)
        await session.commit()


async def record_llm_usage(
    *,
    provider: str,
    model: str,
    usage: Mapping[str, int],
    session_id: str,
    node_id: str,
    iteration: int = 1,
    workflow_id: Optional[str] = None,
    execution_id: Optional[str] = None,
    chat_run_id: Optional[str] = None,
    database: Any = None,
) -> Dict[str, float]:
    """Book one model call in ``token_usage_metrics`` and return its cost.

    ``usage`` maps the six token kinds to their counts; a missing kind is 0.
    The cost is the price service's breakdown (``input_cost``,
    ``output_cost``, ``cache_cost``, ``reasoning_cost``, ``total_cost``):
    all zeros when the price lookup failed, the real price when only the
    write failed. ``chat_run_id`` is the chat run the call worked for, when
    it worked for one. With no ``database`` the container's is used.
    """
    cost = dict(NO_COST)
    try:
        from services.pricing import get_pricing_service

        tokens = {kind: usage.get(kind, 0) for kind in TOKEN_KINDS}
        cost = get_pricing_service().calculate_cost(provider=provider, model=model, **tokens)
        row = TokenUsageMetric(
            session_id=session_id,
            node_id=node_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            chat_run_id=chat_run_id,
            provider=provider,
            model=model,
            iteration=iteration,
            **tokens,
            input_cost=cost["input_cost"],
            output_cost=cost["output_cost"],
            cache_cost=cost["cache_cost"],
            total_cost=cost["total_cost"],
            created_at=datetime.now(timezone.utc),
        )
        await _save(row, database)
    except Exception:
        logger.warning("LLM usage not recorded", provider=provider, model=model, exc_info=True)
    return cost
