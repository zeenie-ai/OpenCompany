"""Shared proxy-usage tracker.

``track_proxy_usage`` is used by the ``proxyRequest`` plugin. It books one
``api_usage_metrics`` row through the usage package, at the cost the caller
passes: the request's bytes at the provider's own price per GB
(``ProxyService.request_cost``).
"""

from __future__ import annotations

from typing import Optional


async def track_proxy_usage(
    node_id: str,
    provider_name: str,
    cost: float,
    *,
    workflow_id: Optional[str] = None,
    session_id: str = "default",
) -> float:
    """Record one proxied request at ``cost`` USD; returns that cost."""
    from services.usage import record_api_usage

    return await record_api_usage(
        f"proxy_{provider_name}" if provider_name else "proxy",
        "proxy_request",
        node_id=node_id,
        session_id=session_id,
        workflow_id=workflow_id,
        endpoint="proxy",
        cost=cost,
    )
