"""What was recorded, added up for the usage panels."""

from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlmodel import select

from core.logging import get_logger
from models.database import APIUsageMetric, TokenUsageMetric

logger = get_logger(__name__)


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
