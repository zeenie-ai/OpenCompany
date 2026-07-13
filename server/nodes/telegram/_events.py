"""Wave 12 B3: CloudEvents factories + broadcaster wrappers for telegram.

Plugin-specific event emission — replaces:
  - ``broadcaster.update_telegram_status(...)`` call inside the
    telegram service's ``_broadcast_status`` method
  - ``event_waiter.dispatch("telegram_message_received", event_data)``
    inside the message-receive handler

After B3 every telegram wire frame originates from one wrapper here —
single source of truth for shape. The cross-plugin
``_emit_connection_typed`` helper on ``StatusBroadcaster`` retires in
the same commit since android (B1) + whatsapp (B2) + telegram (B3) are
the only callers.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder.

Wire format (Wave 12 D4 — legacy ``telegram_status`` raw frame retired):
  - Status: typed CloudEvent on ``plugin_connection_status`` (FE routes
    by ``envelope.source``).
  - Message: typed CloudEvent dispatched to ``event_waiter`` so
    ``telegramReceive`` trigger nodes match it; legacy wire key
    ``telegram_message_received`` preserved for FE.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Mapping, Optional

from services.events.envelope import WorkflowEvent


# ---- Wire-routing keys -----------------------------------------------------

_STATUS_TYPED_WIRE_KEY = "plugin_connection_status"

# Legacy event_type the event_waiter dispatches by; trigger nodes
# subscribe on this string. Keep until Phase B11 FE migration.
_MESSAGE_LEGACY_EVENT_TYPE = "telegram_message_received"


# ---- Typed factories (plugin-specific per RFC §6.4) ------------------------


def telegram_connection_status(
    *,
    connected: bool,
    bot_id: Optional[int] = None,
    bot_username: Optional[str] = None,
    bot_name: Optional[str] = None,
    owner_chat_id: Optional[int] = None,
    has_stored_token: Optional[bool] = None,
) -> WorkflowEvent:
    """Bot connection-state envelope. ``subject`` is the bot username
    so the FE can route per-bot updates.

    ``has_stored_token`` distinguishes "not connected, no token stored"
    (user hasn't added a token yet) from "not connected, token stored
    but bot offline" (network failure / token revoked). Used by the
    auto-reconnect refresh callback (see :mod:`._refresh`).
    """
    data: Dict[str, Any] = {
        "connected": connected,
        "bot_id": bot_id,
        "bot_username": bot_username,
        "bot_name": bot_name,
        "owner_chat_id": owner_chat_id,
    }
    if has_stored_token is not None:
        data["has_stored_token"] = has_stored_token
    return WorkflowEvent(
        source="opencompany://nodes/telegram",
        type=("com.opencompany.telegram.connection.opened" if connected else "com.opencompany.telegram.connection.closed"),
        subject=bot_username,
        data=data,
    )


def telegram_message_received(event_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming Telegram message envelope. ``subject`` is the chat_id
    (cast to str — Telegram chat IDs are numeric, the envelope spec
    requires a string subject)."""
    payload = dict(event_data)
    chat_id = payload.get("chat_id")
    return WorkflowEvent(
        source="opencompany://nodes/telegram",
        type="com.opencompany.telegram.message.received",
        subject=str(chat_id) if chat_id is not None else None,
        data=payload,
    )


# ---- Broadcaster wrappers --------------------------------------------------


async def broadcast_telegram_status(
    *,
    connected: bool,
    bot_id: Optional[int] = None,
    bot_username: Optional[str] = None,
    bot_name: Optional[str] = None,
    owner_chat_id: Optional[int] = None,
    has_stored_token: Optional[bool] = None,
) -> None:
    """Update the telegram status cache + emit the typed
    ``plugin_connection_status`` CloudEvents envelope.

    Replaces ``StatusBroadcaster.update_telegram_status``. Legacy raw
    ``telegram_status`` frame retired in Wave 12 D4 — FE consumes via
    the envelope-aware ``plugin_connection_status`` case.
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()

    payload: Dict[str, Any] = {
        "connected": connected,
        "bot_id": bot_id,
        "bot_username": bot_username,
        "bot_name": bot_name,
        "owner_chat_id": owner_chat_id,
        "timestamp": time.time(),
    }
    if has_stored_token is not None:
        payload["has_stored_token"] = has_stored_token
    broadcaster._status["telegram"] = payload

    event = telegram_connection_status(
        connected=connected,
        bot_id=bot_id,
        bot_username=bot_username,
        bot_name=bot_name,
        owner_chat_id=owner_chat_id,
        has_stored_token=has_stored_token,
    )
    await broadcaster.broadcast(
        {
            "type": _STATUS_TYPED_WIRE_KEY,
            "data": event.model_dump(mode="json"),
        }
    )


async def dispatch_telegram_message_received(event_data: Mapping[str, Any]) -> None:
    """Dispatch an incoming Telegram message via the canary CloudEvents path.

    Single delivery: :func:`services.events.dispatch.emit` Signals running
    :class:`TriggerListenerWorkflow` consumers via Temporal Visibility AND
    broadcasts the envelope to FE on the ``telegram_message_received``
    wire key. telegramReceive is canary-registered so no legacy
    ``event_waiter`` waiter is ever registered for it.
    """
    from services.events.dispatch import emit

    await emit(
        telegram_message_received(dict(event_data)),
        wire_routing_key=_MESSAGE_LEGACY_EVENT_TYPE,
    )


__all__ = [
    "broadcast_telegram_status",
    "dispatch_telegram_message_received",
    "telegram_connection_status",
    "telegram_message_received",
]
