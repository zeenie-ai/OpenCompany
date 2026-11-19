"""The WebSocket commands that read usage.

- ``get_provider_usage_summary {}`` answers ``{providers}``: token usage and
  cost by provider, for an AI provider's "Usage & Costs" in Credentials.
- ``get_api_usage_summary {service?}`` answers ``{services}``: paid API calls
  by service, for a connector's "API Usage & Costs" in Credentials.
- ``get_chat_run_costs {workflow_id}`` answers ``{runs: {run_id: usd}}``: what
  each chat run of one workflow cost, for the figure under a reply.
- ``get_workflow_costs {}`` answers ``{since, total_cost, workflows:
  [{workflow_id, name, total_cost}]}``: this month's cost by employee, for
  the cost panel. ``since`` is the 1st of the month in the owner's time zone,
  as YYYY-MM-DD.

A cost counts model calls and paid tool calls. A failed read of the two cost
commands answers ``success: false``, so a screen shows that the figure is
unavailable instead of a zero.

``main.py`` imports this module, and the import registers the commands. They
answer the owner's sockets only: the internal worker socket reaches no
command outside its allowlist.
"""

from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import WebSocket

from services.usage.summaries import (
    api_usage_summary,
    chat_run_costs,
    month_start,
    provider_usage_summary,
    workflow_costs,
)
from services.ws_handler_registry import register_ws_handlers, ws_handler


def _database() -> Any:
    from core.container import container  # looked up when a command runs

    return container.database()


@ws_handler()
async def handle_get_provider_usage_summary(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return {"providers": await provider_usage_summary(_database())}


@ws_handler()
async def handle_get_api_usage_summary(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return {"services": await api_usage_summary(_database(), data.get("service"))}


@ws_handler("workflow_id")
async def handle_get_chat_run_costs(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return {"runs": await chat_run_costs(_database(), data["workflow_id"])}


@ws_handler()
async def handle_get_workflow_costs(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    database = _database()
    settings = await database.get_user_settings() or {}
    since = month_start(datetime.now(timezone.utc), settings.get("profile_timezone"))
    workflows = await workflow_costs(database, since)
    total = sum(workflow["total_cost"] for workflow in workflows)
    return {"since": since.date().isoformat(), "total_cost": round(total, 6), "workflows": workflows}


WS_HANDLERS = {
    "get_provider_usage_summary": handle_get_provider_usage_summary,
    "get_api_usage_summary": handle_get_api_usage_summary,
    "get_chat_run_costs": handle_get_chat_run_costs,
    "get_workflow_costs": handle_get_workflow_costs,
}

register_ws_handlers(WS_HANDLERS)
