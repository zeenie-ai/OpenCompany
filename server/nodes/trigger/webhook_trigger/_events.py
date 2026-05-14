"""Wave 12 B9: CloudEvents factory + dispatch wrapper for webhook_trigger.

Plugin-specific event emission — replaces:
  - ``broadcaster.send_custom_event("webhook_received", webhook_data)``
    in ``routers/webhook.py`` (unmapped-path fallback).

The webhook router previously emitted the event itself, hardcoding the
plugin-specific event_type string in framework code. Phase B9 inverts
this: the webhook_trigger plugin owns its dispatch primitive.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live
in the plugin folder.

Wire-key behaviour preserved: still goes through
``broadcaster.send_custom_event(...)`` which (a) does the legacy raw
WS broadcast AND (b) dispatches the event into ``event_waiter`` so
``webhookTrigger`` nodes match it.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


# Legacy wire-routing key. The frontend has no listener for it today
# (webhook receivers don't have a status panel); the consumer is the
# ``webhookTrigger`` node via the event_waiter dispatch.
_LEGACY_EVENT_TYPE = "webhook_received"


# ---- Typed factory ---------------------------------------------------------


def webhook_received(webhook_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming webhook envelope. ``subject`` is the path so consumers
    can route per-endpoint."""
    payload = dict(webhook_data)
    path = payload.get("path")
    return WorkflowEvent(
        source="machinaos://nodes/webhook_trigger",
        type="com.machinaos.webhook.received",
        subject=str(path) if path else None,
        data=payload,
    )


# ---- Broadcaster wrapper ---------------------------------------------------


async def broadcast_webhook_received(webhook_data: Mapping[str, Any]) -> None:
    """Broadcast an incoming webhook to ``webhookTrigger`` nodes.

    Routes through ``broadcaster.send_custom_event`` (the existing
    transport that does both the WS legacy broadcast AND the
    ``event_waiter`` dispatch). Wire shape unchanged from pre-B9.

    The typed envelope is constructed for future log/audit use even
    though no FE listener consumes it today.
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    # Build the typed envelope (currently unused on the wire, but
    # available for any future audit log / DLQ replay).
    _ = webhook_received(webhook_data)
    await broadcaster.send_custom_event(_LEGACY_EVENT_TYPE, dict(webhook_data))


__all__ = [
    "broadcast_webhook_received",
    "webhook_received",
]
