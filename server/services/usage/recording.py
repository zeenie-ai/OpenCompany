"""Recording a call that was made and paid for.

A recorder never raises. The call it records already happened, and callers
act on an exception: an agent step would fail after its provider billed it,
the proxy node would send a paid request again, a retried tweet could post
twice, and a mail trigger would drop the email it just read. A failed price
or write is logged as a warning instead.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional

import structlog

from core.logging import get_logger
from models.database import APIUsageMetric, TokenUsageMetric

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
    cost: Optional[float] = None,
    database: Any = None,
) -> Dict[str, float]:
    """Book one model call in ``token_usage_metrics`` and return its cost.

    ``usage`` maps the six token kinds to their counts; a missing kind is 0.
    The cost is the price service's breakdown (``input_cost``,
    ``output_cost``, ``cache_cost``, ``reasoning_cost``, ``total_cost``):
    all zeros when the price lookup failed, the real price when only the
    write failed. ``chat_run_id`` is the chat run the call worked for, when
    it worked for one. With no ``database`` the container's is used.

    A provider that reports its own charge for the call has it passed as
    ``cost`` (``LLMResponse.reported_cost``). It is the ``total_cost`` booked
    and returned; the input, output and cache costs stay the price service's
    estimates, so they need not add up to it.

    The row also takes the employee job bound in the log context: the model
    step and the summarizer bind it for a call made for a job.
    """
    priced = dict(NO_COST)
    try:
        from services.pricing import get_pricing_service

        tokens = {kind: usage.get(kind, 0) for kind in TOKEN_KINDS}
        priced = get_pricing_service().calculate_cost(provider=provider, model=model, **tokens)
        if cost is not None:
            priced = {**priced, "total_cost": cost}
        row = TokenUsageMetric(
            session_id=session_id,
            node_id=node_id,
            workflow_id=workflow_id,
            execution_id=execution_id,
            chat_run_id=chat_run_id,
            employee_job_id=structlog.contextvars.get_contextvars().get("employee_job_id"),
            provider=provider,
            model=model,
            iteration=iteration,
            **tokens,
            input_cost=priced["input_cost"],
            output_cost=priced["output_cost"],
            cache_cost=priced["cache_cost"],
            total_cost=priced["total_cost"],
            created_at=datetime.now(timezone.utc),
        )
        await _save(row, database)
    except Exception:
        logger.warning("LLM usage not recorded", provider=provider, model=model, exc_info=True)
    return priced


async def record_api_usage(
    service: str,
    action: str,
    count: float = 1,
    *,
    node_id: str,
    session_id: str = "default",
    workflow_id: Optional[str] = None,
    endpoint: Optional[str] = None,
    cost: Optional[float] = None,
    database: Any = None,
) -> float:
    """Book one paid API call in ``api_usage_metrics`` and return its USD cost.

    The price service prices ``count`` units of ``action`` for ``service``,
    and the row's operation is the one it priced. A plugin whose provider
    reports its own charge passes it as ``cost``; nothing is looked up then
    and the operation is ``action``. ``endpoint`` says what was called and is
    ``action`` unless given. The cost returned is 0.0 when the price lookup
    failed and the real cost when only the write failed. With no
    ``database`` the container's is used.

    The row also takes the chat run and the employee job bound in the log
    context. ``BaseNode.execute`` binds them for every plugin operation
    (``services/plugin/base.py``), so a tool call an agent makes while it
    answers a chat run counts for that run, one it makes for a job counts for
    that job, and no plugin has to pass either. A call outside both stores none.
    """
    if endpoint is None:
        endpoint = action
    operation = action
    total = 0.0
    try:
        if cost is None:
            from services.pricing import get_pricing_service

            price = get_pricing_service().calculate_api_cost(service, action, count)
            operation = price["operation"]
            cost = price["total_cost"]
        total = cost
        bound = structlog.contextvars.get_contextvars()
        row = APIUsageMetric(
            session_id=session_id,
            node_id=node_id,
            workflow_id=workflow_id,
            chat_run_id=bound.get("chat_run_id"),
            employee_job_id=bound.get("employee_job_id"),
            service=service,
            operation=operation,
            endpoint=endpoint,
            resource_count=count,
            cost=cost,
        )
        await _save(row, database)
    except Exception:
        logger.warning("API usage not recorded", service=service, action=action, exc_info=True)
    return total
