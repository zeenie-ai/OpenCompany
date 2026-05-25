"""Shared proxy-usage tracker.

``_track_proxy_usage`` is used by both the ``proxyRequest`` plugin
and (soon) the http_request + scraper plugins when ``useProxy=True``.
Computes cost from ``pricing.json:proxy.<provider>.cost_per_gb`` and
persists an ``APIUsageMetric`` row.
"""

from __future__ import annotations

from typing import Dict, Optional

from core.logging import get_logger

logger = get_logger(__name__)


async def track_proxy_usage(
    node_id: str,
    provider_name: str,
    bytes_transferred: int,
    *,
    workflow_id: Optional[str] = None,
    session_id: str = "default",
) -> Dict[str, float]:
    from services.plugin.deps import get_database
    from services.pricing import get_pricing_service

    pricing = get_pricing_service()
    proxy_pricing = pricing._config.get("proxy", {})
    provider_pricing = proxy_pricing.get(provider_name, {})
    cost_per_gb = provider_pricing.get("cost_per_gb", 0.0)

    gb = bytes_transferred / (1024**3)
    total_cost = round(gb * cost_per_gb, 8)

    db = get_database()
    await db.save_api_usage_metric(
        {
            "session_id": session_id,
            "node_id": node_id,
            "workflow_id": workflow_id,
            "service": f"proxy_{provider_name}" if provider_name else "proxy",
            "operation": "proxy_request",
            "endpoint": "proxy",
            "resource_count": 1,
            "cost": total_cost,
        }
    )
    logger.debug(f"[Proxy] Tracked {provider_name} usage: {bytes_transferred} bytes " f"= ${total_cost:.8f}")
    return {"cost_per_gb": cost_per_gb, "bytes": bytes_transferred, "total_cost": total_cost}
