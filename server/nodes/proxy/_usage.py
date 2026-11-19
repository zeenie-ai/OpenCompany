"""Shared proxy-usage tracker.

``track_proxy_usage`` is used by the ``proxyRequest`` plugin. It computes the
cost from ``pricing.json:proxy.<provider>.cost_per_gb`` and books one
``api_usage_metrics`` row through the usage package.
"""

from __future__ import annotations

from typing import Optional


async def track_proxy_usage(
    node_id: str,
    provider_name: str,
    bytes_transferred: int,
    *,
    workflow_id: Optional[str] = None,
    session_id: str = "default",
) -> float:
    """Record one proxied request; returns its USD cost."""
    from services.pricing import get_pricing_service
    from services.usage import record_api_usage

    pricing = get_pricing_service()
    proxy_pricing = pricing._config.get("proxy", {})
    provider_pricing = proxy_pricing.get(provider_name, {})
    cost_per_gb = provider_pricing.get("cost_per_gb", 0.0)

    gb = bytes_transferred / (1024**3)
    total_cost = round(gb * cost_per_gb, 8)

    return await record_api_usage(
        f"proxy_{provider_name}" if provider_name else "proxy",
        "proxy_request",
        node_id=node_id,
        session_id=session_id,
        workflow_id=workflow_id,
        endpoint="proxy",
        cost=total_cost,
    )
