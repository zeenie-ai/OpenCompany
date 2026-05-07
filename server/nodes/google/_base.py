"""Shared helpers for Google Workspace plugins (Wave 11.D.4).

Every Google plugin follows the same pattern:

    creds -> googleapiclient.discovery.build(name, version) -> .execute()
    track usage via pricing service -> return result

These two helpers capture that boilerplate so each ``@Operation`` in a
plugin shrinks to the API-specific call + argument shaping.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from googleapiclient.discovery import build

from core.logging import get_logger
from ._credentials import GoogleCredential
from services.pricing import get_pricing_service

logger = get_logger(__name__)


async def build_google_service(
    api_name: str,
    api_version: str,
    parameters: Dict[str, Any],
    context: Dict[str, Any],
):
    """Build an authenticated Google API client (runs in executor).

    Args:
        api_name: Google API short name (``gmail`` / ``calendar`` / ``drive`` /
            ``sheets`` / ``tasks`` / ``people``).
        api_version: API version (e.g. ``v1``, ``v3``).
        parameters: Node parameters (forwarded to ``GoogleCredential.build_credentials``).
        context: Execution context (forwarded).
    """
    creds = await GoogleCredential.build_credentials(parameters, context)
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: build(api_name, api_version, credentials=creds),
    )


async def track_google_usage(
    service: str,
    node_id: str,
    action: str,
    resource_count: int,
    context: Dict[str, Any],
) -> Dict[str, float]:
    """Record a Google API call in ``api_usage_metrics``.

    ``service`` must match a pricing-config key: ``gmail`` /
    ``google_calendar`` / ``google_drive`` / ``google_sheets`` /
    ``google_tasks`` / ``google_contacts``. Google APIs are free at our
    tier — this is analytics bookkeeping, cost is $0.
    """
    from services.plugin.deps import get_database

    pricing = get_pricing_service()
    cost_data = pricing.calculate_api_cost(service, action, resource_count)

    db = get_database()
    await db.save_api_usage_metric({
        'session_id': context.get('session_id', 'default'),
        'node_id': node_id,
        'workflow_id': context.get('workflow_id'),
        'service': service,
        'operation': cost_data.get('operation', action),
        'endpoint': action,
        'resource_count': resource_count,
        'cost': cost_data.get('total_cost', 0.0),
    })
    return cost_data


async def run_sync(fn):
    """Run a blocking googleapiclient call in the default executor."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, fn)
