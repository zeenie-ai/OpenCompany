"""WS-connect status broadcast for the Temporal stack.

Registered via ``status_broadcaster.register_service_refresh`` so the
frontend health indicator stays current — every WebSocket client
connect triggers ``_refresh_all_services()`` which fans out to every
registered callback. Same idiom :mod:`nodes.telegram._refresh` uses.

Also exposes :func:`temporal_status_snapshot` — the single source of
truth for the ``{temporal}`` status shape consumed by both this refresh
callback and ``_handlers.py``'s WS commands.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.logging import get_logger

if TYPE_CHECKING:
    from services.status_broadcaster import StatusBroadcaster

logger = get_logger(__name__)


def temporal_status_snapshot() -> dict[str, Any]:
    """Return ``{"temporal": {...}}`` snapshot.

    Exposes the uniform ``{name, running, started_at, last_error,
    ...extras}`` shape every ``BaseSupervisor`` subclass returns.
    Shared by the WS-refresh callback below and the
    ``temporal_status`` / ``_start`` / ``_stop`` handlers in
    :mod:`services.temporal._handlers`.
    """
    from services.temporal._runtime import get_temporal_server_runtime

    return {"temporal": get_temporal_server_runtime().status_snapshot()}


async def refresh_temporal_status(broadcaster: "StatusBroadcaster") -> None:
    """Broadcast the Temporal status snapshot on WS connect.

    Signature matches the ``register_service_refresh`` callback contract
    — :meth:`StatusBroadcaster._refresh_all_services` invokes every
    registered callback as ``callback(self)`` (passes the broadcaster
    as the sole positional argument). Without the parameter the
    framework's ``TaskGroup`` swallows a ``TypeError: takes 0 positional
    arguments but 1 was given`` on every server boot.
    """
    await broadcaster.broadcast({
        "type": "temporal_status",
        "data": temporal_status_snapshot(),
    })


__all__ = ["refresh_temporal_status", "temporal_status_snapshot"]
