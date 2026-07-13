"""CloudEvents factory + dispatcher for email.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder.

Delivery: single ``dispatch.emit`` call routes events to running
:class:`TriggerListenerWorkflow` consumers via Temporal Visibility AND
broadcasts the envelope to FE on the ``email_received`` wire key.
emailReceive is canary-registered (see ``nodes/email/__init__.py``) so
the deployment manager skips ``setup_event_trigger`` and the legacy
``event_waiter.dispatch`` path has zero consumers — removed.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


# Outer wire-routing key. Matches ``EmailReceiveNode.event_type`` and
# the FE WS channel; the inner envelope carries
# ``com.opencompany.email.message.received``.
_WIRE_ROUTING_KEY = "email_received"


def email_message_received(email_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming email envelope. ``subject`` is the ``message_id`` so
    consumers can dedup and correlate (subject of the MAIL message
    itself goes in ``data.subject``)."""
    payload = dict(email_data)
    message_id = payload.get("message_id") or payload.get("id")
    return WorkflowEvent(
        source="opencompany://nodes/email",
        type="com.opencompany.email.message.received",
        subject=str(message_id) if message_id else None,
        data=payload,
    )


async def dispatch_email_received(email_data: Mapping[str, Any]) -> None:
    """Dispatch an incoming email via the canary CloudEvents path."""
    from services.events.dispatch import emit

    await emit(
        email_message_received(dict(email_data)),
        wire_routing_key=_WIRE_ROUTING_KEY,
    )


__all__ = [
    "dispatch_email_received",
    "email_message_received",
]
