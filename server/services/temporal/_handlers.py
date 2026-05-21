"""WebSocket handlers for the Temporal lifecycle commands.

Wire keys registered in :mod:`services.ws_handler_registry`:

  - ``temporal_status``  → status snapshot of the runtime
  - ``temporal_start``   → idempotent start
  - ``temporal_stop``    → idempotent stop

All three share the snapshot shape from
:func:`services.temporal._refresh.temporal_status_snapshot` — the
single source of truth for the ``{temporal}`` payload that the
WS-connect refresh callback also emits.
"""
from __future__ import annotations

from typing import Any

from fastapi import WebSocket

from core.logging import get_logger
from services.temporal._refresh import temporal_status_snapshot

logger = get_logger(__name__)


async def handle_temporal_status(
    data: dict[str, Any], websocket: WebSocket,
) -> dict[str, Any]:
    """Return the status snapshot for the Temporal runtime."""
    return temporal_status_snapshot()


async def handle_temporal_start(
    data: dict[str, Any], websocket: WebSocket,
) -> dict[str, Any]:
    """Start Temporal. Idempotent — ``.start()`` returns immediately if
    the runtime is already running."""
    from services.temporal._runtime import get_temporal_server_runtime

    await get_temporal_server_runtime().start()
    return {"ok": True, **temporal_status_snapshot()}


async def handle_temporal_stop(
    data: dict[str, Any], websocket: WebSocket,
) -> dict[str, Any]:
    """Stop Temporal. Idempotent."""
    from services.temporal._runtime import get_temporal_server_runtime

    await get_temporal_server_runtime().stop()
    return {"ok": True, **temporal_status_snapshot()}


WS_HANDLERS: dict[str, Any] = {
    "temporal_status": handle_temporal_status,
    "temporal_start": handle_temporal_start,
    "temporal_stop": handle_temporal_stop,
}


__all__ = ["WS_HANDLERS"]
