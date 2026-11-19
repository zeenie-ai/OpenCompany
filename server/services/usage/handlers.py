"""The WebSocket commands that read usage.

- ``get_provider_usage_summary {}`` answers ``{providers}``: token usage and
  cost by provider, for an AI provider's "Usage & Costs" in Credentials.
- ``get_api_usage_summary {service?}`` answers ``{services}``: paid API calls
  by service, for a connector's "API Usage & Costs" in Credentials.

``main.py`` imports this module, and the import registers the commands. They
answer the owner's sockets only: the internal worker socket reaches no
command outside its allowlist.
"""

from typing import Any, Dict

from fastapi import WebSocket

from services.usage.summaries import api_usage_summary, provider_usage_summary
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


WS_HANDLERS = {
    "get_provider_usage_summary": handle_get_provider_usage_summary,
    "get_api_usage_summary": handle_get_api_usage_summary,
}

register_ws_handlers(WS_HANDLERS)
