"""CloudEvents factory + dispatch wrapper for chat_trigger.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder.

Delivery: single ``dispatch.emit`` call routes events to running
:class:`TriggerListenerWorkflow` consumers via Temporal Visibility AND
broadcasts the envelope to FE on the ``chat_message_received`` wire key.
chatTrigger is canary-registered (see ``nodes/trigger/chat_trigger/__init__.py``)
so the deployment manager skips ``setup_event_trigger`` and the legacy
``event_waiter.dispatch`` path has zero consumers — removed.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


# Outer wire-routing key. Matches ``ChatTriggerNode.event_type`` and the
# FE WS channel; the inner envelope carries
# ``com.opencompany.chat.message.received``.
_WIRE_ROUTING_KEY = "chat_message_received"


def chat_message_received(event_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming chat message envelope. ``subject`` is the session_id so
    consumers can route per-conversation."""
    payload = dict(event_data)
    session_id = payload.get("session_id")
    return WorkflowEvent(
        source="opencompany://nodes/chat_trigger",
        type="com.opencompany.chat.message.received",
        subject=str(session_id) if session_id else None,
        data=payload,
    )


async def dispatch_chat_message_received(event_data: Mapping[str, Any]) -> None:
    """Dispatch an incoming chat message via the canary CloudEvents path."""
    from services.events.dispatch import emit

    await emit(
        chat_message_received(dict(event_data)),
        wire_routing_key=_WIRE_ROUTING_KEY,
    )


__all__ = [
    "chat_message_received",
    "dispatch_chat_message_received",
]
