"""What was recorded, added up for the usage panels.

A cost is model calls plus paid tool calls: the rows of
``token_usage_metrics`` and of ``api_usage_metrics``.
"""

from datetime import datetime, timezone
from operator import itemgetter
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func
from sqlmodel import select

from core.logging import get_logger
from models.database import APIUsageMetric, TokenUsageMetric, Workflow
from models.employees import EmployeeJob

logger = get_logger(__name__)


def month_start(now: datetime, zone_name: Optional[str]) -> datetime:
    """Midnight on the 1st of this month in the owner's time zone, the month
    Settings > Billing counts tasks over. UTC when the zone is unset or
    unknown."""
    try:
        zone = ZoneInfo(zone_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return now.astimezone(zone).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def chat_run_cost(database: Any, chat_run_id: str) -> float:
    """USD one chat run has cost so far. Errors propagate."""
    async with database.get_session() as session:
        model_calls = await session.execute(
            select(func.sum(TokenUsageMetric.total_cost)).where(TokenUsageMetric.chat_run_id == chat_run_id)
        )
        tool_calls = await session.execute(
            select(func.sum(APIUsageMetric.cost)).where(APIUsageMetric.chat_run_id == chat_run_id)
        )
        return float(model_calls.scalar() or 0) + float(tool_calls.scalar() or 0)


async def chat_run_costs(database: Any, workflow_id: str) -> Dict[str, float]:
    """USD per chat run of one workflow: ``{run_id: cost}``.

    A run's cost is its own rows plus the rows of the employee jobs its reply
    started (``EmployeeJob.chat_run_id``), so the figure under a reply covers
    the team it handed work to. A job's row that carries a chat run of its own
    counts for that run only. A run with no booked call is absent. Errors
    propagate, so a caller shows that the costs are unavailable instead of a
    zero.
    """
    async with database.get_session() as session:
        model_calls = await session.execute(
            select(TokenUsageMetric.chat_run_id, func.sum(TokenUsageMetric.total_cost))
            .where(TokenUsageMetric.workflow_id == workflow_id, TokenUsageMetric.chat_run_id.is_not(None))
            .group_by(TokenUsageMetric.chat_run_id)
        )
        tool_calls = await session.execute(
            select(APIUsageMetric.chat_run_id, func.sum(APIUsageMetric.cost))
            .where(APIUsageMetric.workflow_id == workflow_id, APIUsageMetric.chat_run_id.is_not(None))
            .group_by(APIUsageMetric.chat_run_id)
        )
        started_by_a_reply = (EmployeeJob.workflow_id == workflow_id, EmployeeJob.chat_run_id.is_not(None))
        job_model_calls = await session.execute(
            select(EmployeeJob.chat_run_id, func.sum(TokenUsageMetric.total_cost))
            .join_from(TokenUsageMetric, EmployeeJob, EmployeeJob.id == TokenUsageMetric.employee_job_id)
            .where(*started_by_a_reply, TokenUsageMetric.chat_run_id.is_(None))
            .group_by(EmployeeJob.chat_run_id)
        )
        job_tool_calls = await session.execute(
            select(EmployeeJob.chat_run_id, func.sum(APIUsageMetric.cost))
            .join_from(APIUsageMetric, EmployeeJob, EmployeeJob.id == APIUsageMetric.employee_job_id)
            .where(*started_by_a_reply, APIUsageMetric.chat_run_id.is_(None))
            .group_by(EmployeeJob.chat_run_id)
        )
        rows = [*model_calls.all(), *tool_calls.all(), *job_model_calls.all(), *job_tool_calls.all()]

    costs: Dict[str, float] = {}
    for run_id, cost in rows:
        costs[run_id] = costs.get(run_id, 0.0) + float(cost or 0)
    return {run_id: round(cost, 6) for run_id, cost in costs.items()}


async def workflow_cost_since(database: Any, workflow_id: str, since: datetime) -> float:
    """USD one workflow has cost since ``since``: its model calls plus its
    paid tool calls. Errors propagate."""
    since_utc = since.astimezone(timezone.utc)
    async with database.get_session() as session:
        # token_usage_metrics.created_at holds UTC without a zone;
        # api_usage_metrics.timestamp is declared with one.
        model_calls = await session.execute(
            select(func.sum(TokenUsageMetric.total_cost)).where(
                TokenUsageMetric.workflow_id == workflow_id,
                TokenUsageMetric.created_at >= since_utc.replace(tzinfo=None),
            )
        )
        tool_calls = await session.execute(
            select(func.sum(APIUsageMetric.cost)).where(
                APIUsageMetric.workflow_id == workflow_id,
                APIUsageMetric.timestamp >= since_utc,
            )
        )
        return float(model_calls.scalar() or 0) + float(tool_calls.scalar() or 0)


async def workflow_costs(database: Any, since: datetime) -> List[Dict[str, Any]]:
    """USD per workflow since ``since``: ``[{workflow_id, name, total_cost}]``.

    Workflows come highest cost first. Calls booked with no workflow (a hire
    screen's setup call), or for a workflow deleted since, add up in one last
    row whose ``workflow_id`` and ``name`` are ``None``. Errors propagate.
    """
    since_utc = since.astimezone(timezone.utc)
    async with database.get_session() as session:
        # token_usage_metrics.created_at holds UTC without a zone;
        # api_usage_metrics.timestamp is declared with one.
        model_calls = await session.execute(
            select(TokenUsageMetric.workflow_id, func.sum(TokenUsageMetric.total_cost))
            .where(TokenUsageMetric.created_at >= since_utc.replace(tzinfo=None))
            .group_by(TokenUsageMetric.workflow_id)
        )
        tool_calls = await session.execute(
            select(APIUsageMetric.workflow_id, func.sum(APIUsageMetric.cost))
            .where(APIUsageMetric.timestamp >= since_utc)
            .group_by(APIUsageMetric.workflow_id)
        )
        rows = [*model_calls.all(), *tool_calls.all()]
        names = dict((await session.execute(select(Workflow.id, Workflow.name))).all())

    costs: Dict[Optional[str], float] = {}
    for workflow_id, cost in rows:
        key = workflow_id if workflow_id in names else None
        costs[key] = costs.get(key, 0.0) + float(cost or 0)

    workflows = [
        {"workflow_id": workflow_id, "name": names[workflow_id], "total_cost": round(cost, 6)}
        for workflow_id, cost in costs.items()
        if workflow_id is not None
    ]
    workflows.sort(key=itemgetter("total_cost"), reverse=True)
    if None in costs:
        workflows.append({"workflow_id": None, "name": None, "total_cost": round(costs[None], 6)})
    return workflows


async def provider_usage_summary(database: Any) -> List[Dict[str, Any]]:
    """Token usage and cost by provider, each with its models.

    One dict per provider: ``provider``, ``total_input_tokens``,
    ``total_output_tokens``, ``total_tokens``, ``total_input_cost``,
    ``total_output_cost``, ``total_cache_cost``, ``total_cost`` (USD),
    ``execution_count`` and ``models``, the same figures per model. A failed
    read answers an empty list.
    """
    try:
        async with database.get_session() as session:
            model_stmt = (
                select(
                    TokenUsageMetric.provider,
                    TokenUsageMetric.model,
                    func.sum(TokenUsageMetric.input_tokens).label("input_tokens"),
                    func.sum(TokenUsageMetric.output_tokens).label("output_tokens"),
                    func.sum(TokenUsageMetric.total_tokens).label("total_tokens"),
                    func.sum(TokenUsageMetric.input_cost).label("input_cost"),
                    func.sum(TokenUsageMetric.output_cost).label("output_cost"),
                    func.sum(TokenUsageMetric.cache_cost).label("cache_cost"),
                    func.sum(TokenUsageMetric.total_cost).label("total_cost"),
                    func.count().label("execution_count"),
                )
                .group_by(TokenUsageMetric.provider, TokenUsageMetric.model)
                .order_by(TokenUsageMetric.provider, TokenUsageMetric.model)
            )
            result = await session.execute(model_stmt)
            rows = result.all()

            providers: Dict[str, Dict] = {}
            for row in rows:
                provider = row.provider or "unknown"
                if provider not in providers:
                    providers[provider] = {
                        "provider": provider,
                        "total_input_tokens": 0,
                        "total_output_tokens": 0,
                        "total_tokens": 0,
                        "total_input_cost": 0.0,
                        "total_output_cost": 0.0,
                        "total_cache_cost": 0.0,
                        "total_cost": 0.0,
                        "execution_count": 0,
                        "models": [],
                    }

                p = providers[provider]
                p["total_input_tokens"] += row.input_tokens or 0
                p["total_output_tokens"] += row.output_tokens or 0
                p["total_tokens"] += row.total_tokens or 0
                p["total_input_cost"] += float(row.input_cost or 0)
                p["total_output_cost"] += float(row.output_cost or 0)
                p["total_cache_cost"] += float(row.cache_cost or 0)
                p["total_cost"] += float(row.total_cost or 0)
                p["execution_count"] += row.execution_count or 0

                p["models"].append(
                    {
                        "model": row.model,
                        "input_tokens": row.input_tokens or 0,
                        "output_tokens": row.output_tokens or 0,
                        "total_tokens": row.total_tokens or 0,
                        "input_cost": round(float(row.input_cost or 0), 6),
                        "output_cost": round(float(row.output_cost or 0), 6),
                        "cache_cost": round(float(row.cache_cost or 0), 6),
                        "total_cost": round(float(row.total_cost or 0), 6),
                        "execution_count": row.execution_count or 0,
                    }
                )

            for p in providers.values():
                p["total_input_cost"] = round(p["total_input_cost"], 6)
                p["total_output_cost"] = round(p["total_output_cost"], 6)
                p["total_cache_cost"] = round(p["total_cache_cost"], 6)
                p["total_cost"] = round(p["total_cost"], 6)

            return list(providers.values())

    except Exception as e:
        logger.error("Failed to get provider usage summary", error=str(e))
        return []


async def api_usage_summary(database: Any, service: Optional[str] = None) -> List[Dict[str, Any]]:
    """API usage and cost by service, each with its operations.

    One dict per service: ``service``, ``total_resources``, ``total_cost``
    (USD), ``execution_count`` and ``operations``, the same figures per
    operation. ``service`` keeps one service only. A failed read answers an
    empty list.
    """
    try:
        async with database.get_session() as session:
            query = select(
                APIUsageMetric.service,
                APIUsageMetric.operation,
                func.sum(APIUsageMetric.resource_count).label("resource_count"),
                func.sum(APIUsageMetric.cost).label("cost"),
                func.count().label("execution_count"),
            ).group_by(APIUsageMetric.service, APIUsageMetric.operation)

            if service:
                query = query.where(APIUsageMetric.service == service)

            query = query.order_by(APIUsageMetric.service, APIUsageMetric.operation)
            result = await session.execute(query)
            rows = result.all()

            services: Dict[str, Dict] = {}
            for row in rows:
                svc = row.service or "unknown"
                if svc not in services:
                    services[svc] = {"service": svc, "total_resources": 0, "total_cost": 0.0, "execution_count": 0, "operations": []}

                s = services[svc]
                s["total_resources"] += row.resource_count or 0
                s["total_cost"] += float(row.cost or 0)
                s["execution_count"] += row.execution_count or 0

                s["operations"].append(
                    {
                        "operation": row.operation,
                        "resource_count": row.resource_count or 0,
                        "total_cost": round(float(row.cost or 0), 6),
                        "execution_count": row.execution_count or 0,
                    }
                )

            for s in services.values():
                s["total_cost"] = round(s["total_cost"], 6)

            return list(services.values())

    except Exception as e:
        logger.error("Failed to get API usage summary", error=str(e))
        return []
