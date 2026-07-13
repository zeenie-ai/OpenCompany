"""CloudEvents factory + broadcaster wrapper for webhook_trigger.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder.

Delivery: single ``dispatch.emit`` call routes events to running
:class:`TriggerListenerWorkflow` consumers via Temporal Visibility AND
broadcasts the envelope to FE on the ``webhook_received`` wire key.
webhookTrigger is canary-registered (see ``nodes/trigger/webhook_trigger/__init__.py``)
so the deployment manager skips ``setup_event_trigger`` and the legacy
``broadcaster.send_custom_event`` path (which dispatched to
``event_waiter``) has zero consumers — removed.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


# Outer wire-routing key. Matches ``WebhookTriggerNode.event_type`` and
# the FE WS channel; the inner envelope carries
# ``com.opencompany.webhook.received``.
_WIRE_ROUTING_KEY = "webhook_received"


def webhook_received(webhook_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming webhook envelope. ``subject`` is the path so consumers
    can route per-endpoint."""
    payload = dict(webhook_data)
    path = payload.get("path")
    return WorkflowEvent(
        source="opencompany://nodes/webhook_trigger",
        type="com.opencompany.webhook.received",
        subject=str(path) if path else None,
        data=payload,
    )


async def broadcast_webhook_received(webhook_data: Mapping[str, Any]) -> None:
    """Broadcast an incoming webhook via the canary CloudEvents path."""
    from services.events.dispatch import emit

    await emit(
        webhook_received(dict(webhook_data)),
        wire_routing_key=_WIRE_ROUTING_KEY,
    )


__all__ = [
    "broadcast_webhook_received",
    "webhook_received",
]
