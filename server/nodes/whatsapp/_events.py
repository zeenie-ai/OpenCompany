"""Wave 12 B2: CloudEvents factories + broadcaster wrappers for whatsapp.

Plugin-specific event emission — replaces:
  - 10 ``broadcaster.update_whatsapp_status(...)`` callsites (event
    handlers + status / start / restart WS handlers)
  - 7 ``broadcaster.send_custom_event("whatsapp_*", ...)`` callsites
    (message_sent / message_received / 4 newsletter events /
    history_sync_complete)

…all of which previously emitted raw dicts and lived as a tangle in
``_service.py``. After B2 every whatsapp wire frame originates from
exactly one wrapper in this module — single source of truth for shape.

Per RFC plugin_authoring_rfc.md §6.4:
  - Plugin-specific factories live in the plugin folder (NOT central
    ``services/events/envelope.py``).
  - Cross-cutting factories (``credential``, ``oauth_completed``, …)
    stay in central envelope.

Wire format (Wave 12 D4 — legacy ``whatsapp_status`` raw frame retired):
  - Status: typed CloudEvents envelope on ``plugin_connection_status``
    (FE routes by ``envelope.source``).
  - Message / newsletter / history: still dual-emit on their legacy
    wire keys until the matching FE handlers migrate to envelope-aware
    readers (follow-up D4 round).
"""

from __future__ import annotations

import time
from typing import Any, Dict, Literal, Mapping, Optional

from services.events.envelope import WorkflowEvent


# ---- Wire-routing keys (outer ``type`` field the FE switches on) -----------
#
# Unchanged from the pre-B2 shape per RFC §11 (FE still routes on the
# legacy keys). Only the INNER ``data.type`` gains the ``com.opencompany.``
# reverse-DNS prefix via the typed CloudEvents factories below.

_STATUS_TYPED_WIRE_KEY = "plugin_connection_status"  # shared cross-plugin channel

_MESSAGE_SENT_WIRE_KEY = "whatsapp_message_sent"
_MESSAGE_RECEIVED_WIRE_KEY = "whatsapp_message_received"

_NEWSLETTER_WIRE_KEYS: Dict[str, str] = {
    "joined": "whatsapp_newsletter_join",
    "left": "whatsapp_newsletter_leave",
    "muted": "whatsapp_newsletter_mute_change",
    "live_updated": "whatsapp_newsletter_live_update",
}

_HISTORY_SYNCED_WIRE_KEY = "whatsapp_history_sync_complete"


# ---- Typed factories (plugin-specific per RFC §6.4) ------------------------


def whatsapp_connection_status(
    *,
    connected: bool,
    has_session: bool = False,
    running: bool = False,
    pairing: bool = False,
    device_id: Optional[str] = None,
    qr: Optional[str] = None,
) -> WorkflowEvent:
    """Connection-state envelope. ``subject`` is the device_id so the
    FE can route per-device updates."""
    return WorkflowEvent(
        source="opencompany://nodes/whatsapp",
        type=("com.opencompany.whatsapp.connection.opened" if connected else "com.opencompany.whatsapp.connection.closed"),
        subject=device_id,
        data={
            "connected": connected,
            "has_session": has_session,
            "running": running,
            "pairing": pairing,
            "device_id": device_id,
            "qr": qr,
        },
    )


def whatsapp_message_event(
    direction: Literal["sent", "received"],
    params: Mapping[str, Any],
) -> WorkflowEvent:
    """Message envelope (sent or received). Subject auto-extracted
    from the params payload (chat_id / sender / from)."""
    payload = dict(params)
    subject = payload.get("chat_id") or payload.get("sender") or payload.get("from")
    return WorkflowEvent(
        source="opencompany://nodes/whatsapp",
        type=f"com.opencompany.whatsapp.message.{direction}",
        subject=str(subject) if subject else None,
        data=payload,
    )


def whatsapp_newsletter_event(
    verb: Literal["joined", "left", "muted", "live_updated"],
    params: Mapping[str, Any],
) -> WorkflowEvent:
    """Newsletter-lifecycle envelope. Subject auto-extracted from
    ``newsletter_jid`` if present in params."""
    payload = dict(params)
    subject = payload.get("newsletter_jid") or payload.get("jid")
    return WorkflowEvent(
        source="opencompany://nodes/whatsapp",
        type=f"com.opencompany.whatsapp.newsletter.{verb}",
        subject=str(subject) if subject else None,
        data=payload,
    )


def whatsapp_history_synced(params: Mapping[str, Any]) -> WorkflowEvent:
    """History-sync-complete envelope. Subject is the device_id when
    available in the params payload."""
    payload = dict(params)
    subject = payload.get("device_id")
    return WorkflowEvent(
        source="opencompany://nodes/whatsapp",
        type="com.opencompany.whatsapp.history.synced",
        subject=str(subject) if subject else None,
        data=payload,
    )


# ---- Broadcaster wrappers --------------------------------------------------


async def broadcast_whatsapp_status(
    *,
    connected: bool,
    has_session: bool = False,
    running: bool = False,
    pairing: bool = False,
    device_id: Optional[str] = None,
    qr: Optional[str] = None,
) -> None:
    """Update the whatsapp status cache + emit the typed
    ``plugin_connection_status`` CloudEvents envelope.

    Replaces ``StatusBroadcaster.update_whatsapp_status``. Legacy raw
    ``whatsapp_status`` frame retired in Wave 12 D4 — FE consumes via
    the envelope-aware ``plugin_connection_status`` case.
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()

    payload: Dict[str, Any] = {
        "connected": connected,
        "has_session": has_session,
        "running": running,
        "pairing": pairing,
        "device_id": device_id,
        "qr": qr,
        "timestamp": time.time(),
    }
    broadcaster._status["whatsapp"] = payload

    event = whatsapp_connection_status(
        connected=connected,
        has_session=has_session,
        running=running,
        pairing=pairing,
        device_id=device_id,
        qr=qr,
    )
    await broadcaster.broadcast(
        {
            "type": _STATUS_TYPED_WIRE_KEY,
            "data": event.model_dump(mode="json"),
        }
    )


async def broadcast_whatsapp_message(
    direction: Literal["sent", "received"],
    params: Mapping[str, Any],
) -> None:
    """Emit a message event (sent or received).

    Inbound (``received``) routes via :func:`services.events.dispatch.emit`
    — the canary CloudEvents path Signals running
    :class:`TriggerListenerWorkflow` consumers AND broadcasts the
    envelope on the ``whatsapp_message_received`` wire key.

    Outbound (``sent``) is observation-only: no trigger consumer
    exists, so it bypasses ``dispatch.emit`` and goes out as a raw
    legacy frame on ``whatsapp_message_sent`` for the FE message-list
    UI.
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    payload = dict(params)

    if direction == "received":
        # Canary path — single ``emit`` call delivers to Temporal
        # listeners + FE WS. The FE message-list handler at
        # WebSocketContext.tsx still reads ``data.*`` for back-compat,
        # so we also broadcast the legacy raw frame until that handler
        # migrates to envelope-shape (Wave 12 D4 follow-up).
        from services.events.dispatch import emit

        await broadcaster.broadcast(
            {
                "type": _MESSAGE_RECEIVED_WIRE_KEY,
                "data": payload,
            }
        )
        await emit(
            whatsapp_message_event("received", payload),
            wire_routing_key=_MESSAGE_RECEIVED_WIRE_KEY,
        )
    else:
        # Outbound observation only — direct legacy raw broadcast.
        await broadcaster.broadcast(
            {
                "type": _MESSAGE_SENT_WIRE_KEY,
                "data": payload,
            }
        )


async def broadcast_whatsapp_newsletter(
    verb: Literal["joined", "left", "muted", "live_updated"],
    params: Mapping[str, Any],
) -> None:
    """Emit a newsletter-lifecycle event for FE observation.

    Not a trigger event — no canary consumer. Direct raw broadcast on
    the legacy wire key until the FE newsletter UI migrates to read
    the envelope shape, at which point this becomes a ``dispatch.emit``
    call like :func:`broadcast_whatsapp_message`.
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    await broadcaster.broadcast(
        {
            "type": _NEWSLETTER_WIRE_KEYS[verb],
            "data": dict(params),
        }
    )


async def broadcast_whatsapp_history_synced(params: Mapping[str, Any]) -> None:
    """Emit a history-sync-complete event for FE observation.

    Not a trigger event — no canary consumer. Direct raw broadcast.
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    await broadcaster.broadcast(
        {
            "type": _HISTORY_SYNCED_WIRE_KEY,
            "data": dict(params),
        }
    )


__all__ = [
    "broadcast_whatsapp_history_synced",
    "broadcast_whatsapp_message",
    "broadcast_whatsapp_newsletter",
    "broadcast_whatsapp_status",
    "whatsapp_connection_status",
    "whatsapp_history_synced",
    "whatsapp_message_event",
    "whatsapp_newsletter_event",
]
