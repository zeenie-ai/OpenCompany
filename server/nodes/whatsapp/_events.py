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

Wire format preserved from the pre-migration shape (so FE consumers
keep working). Each wrapper dual-emits:
  - Legacy raw frame: ``{type: <legacy_key>, data: <raw payload>}``
  - Typed CloudEvents sibling: ``{type: <typed_wire_key>, data:
    <WorkflowEvent envelope>}``

Status events use the existing ``plugin_connection_status`` typed
channel (matches Wave 11.I X4 + the B1 android migration); message /
newsletter / history events use new dedicated typed channels.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Literal, Mapping, Optional

from services.events.envelope import WorkflowEvent


# ---- Wire-routing keys (outer ``type`` field the FE switches on) -----------
#
# Unchanged from the pre-B2 shape per RFC §11 (FE still routes on the
# legacy keys). Only the INNER ``data.type`` gains the ``com.machinaos.``
# reverse-DNS prefix via the typed CloudEvents factories below.

_STATUS_LEGACY_WIRE_KEY = "whatsapp_status"
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
        source="machinaos://nodes/whatsapp",
        type=(
            "com.machinaos.whatsapp.connection.opened"
            if connected
            else "com.machinaos.whatsapp.connection.closed"
        ),
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
    subject = (
        payload.get("chat_id")
        or payload.get("sender")
        or payload.get("from")
    )
    return WorkflowEvent(
        source="machinaos://nodes/whatsapp",
        type=f"com.machinaos.whatsapp.message.{direction}",
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
        source="machinaos://nodes/whatsapp",
        type=f"com.machinaos.whatsapp.newsletter.{verb}",
        subject=str(subject) if subject else None,
        data=payload,
    )


def whatsapp_history_synced(params: Mapping[str, Any]) -> WorkflowEvent:
    """History-sync-complete envelope. Subject is the device_id when
    available in the params payload."""
    payload = dict(params)
    subject = payload.get("device_id")
    return WorkflowEvent(
        source="machinaos://nodes/whatsapp",
        type="com.machinaos.whatsapp.history.synced",
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
    """Update the whatsapp status cache + emit both the legacy raw
    ``whatsapp_status`` frame AND the typed
    ``plugin_connection_status`` CloudEvents sibling.

    Replaces ``StatusBroadcaster.update_whatsapp_status``.
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

    # Legacy raw frame (FE back-compat).
    await broadcaster.broadcast({
        "type": _STATUS_LEGACY_WIRE_KEY,
        "data": payload,
    })

    # Typed CloudEvents sibling — shared `plugin_connection_status` channel.
    event = whatsapp_connection_status(
        connected=connected,
        has_session=has_session,
        running=running,
        pairing=pairing,
        device_id=device_id,
        qr=qr,
    )
    await broadcaster.broadcast({
        "type": _STATUS_TYPED_WIRE_KEY,
        "data": event.model_dump(mode="json"),
    })


async def broadcast_whatsapp_message(
    direction: Literal["sent", "received"],
    params: Mapping[str, Any],
) -> None:
    """Emit a message event (sent or received). Wire-routing key
    preserves the legacy ``whatsapp_message_<direction>`` channel.

    For inbound (``received``) messages, also fans out via
    :func:`services.events.dispatch.emit` so Wave 12 C1 canary
    :class:`TriggerListenerWorkflow` consumers can pick up the
    envelope (no-op when ``Settings.event_framework_enabled`` is off,
    so the legacy WS path stays default).
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    wire_key = (
        _MESSAGE_SENT_WIRE_KEY if direction == "sent"
        else _MESSAGE_RECEIVED_WIRE_KEY
    )
    payload = dict(params)

    # Legacy raw frame.
    await broadcaster.broadcast({
        "type": wire_key,
        "data": payload,
    })

    # Typed CloudEvents sibling — same outer wire key (the inner
    # envelope is what carries the typed contract).
    event = whatsapp_message_event(direction, payload)
    await broadcaster.broadcast({
        "type": wire_key,
        "data": event.model_dump(mode="json"),
    })

    # Wave 12 C1 rollout #4: Temporal-durable canary fan-out (received
    # only; outbound message events are pure observation, no trigger
    # node consumes them). emit() no-ops when the feature flag is off.
    if direction == "received":
        from services.events.dispatch import emit

        await emit(event, wire_routing_key=wire_key)


async def broadcast_whatsapp_newsletter(
    verb: Literal["joined", "left", "muted", "live_updated"],
    params: Mapping[str, Any],
) -> None:
    """Emit a newsletter-lifecycle event. ``verb`` selects the
    legacy wire key from :data:`_NEWSLETTER_WIRE_KEYS`."""
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    wire_key = _NEWSLETTER_WIRE_KEYS[verb]

    await broadcaster.broadcast({
        "type": wire_key,
        "data": dict(params),
    })

    event = whatsapp_newsletter_event(verb, params)
    await broadcaster.broadcast({
        "type": wire_key,
        "data": event.model_dump(mode="json"),
    })


async def broadcast_whatsapp_history_synced(params: Mapping[str, Any]) -> None:
    """Emit a history-sync-complete event."""
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()

    await broadcaster.broadcast({
        "type": _HISTORY_SYNCED_WIRE_KEY,
        "data": dict(params),
    })

    event = whatsapp_history_synced(params)
    await broadcaster.broadcast({
        "type": _HISTORY_SYNCED_WIRE_KEY,
        "data": event.model_dump(mode="json"),
    })


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
