"""Wave 12 B7: CloudEvents factory + dispatch wrapper for chat_trigger.

Plugin-specific event emission — replaces:
  - ``event_waiter.dispatch("chat_message_received", event_data)`` in
    ``routers/websocket.py:handle_send_chat_message``.

The Chat console message-send handler in the WS router previously
dispatched the event itself, hardcoding the plugin-specific event_type
string in framework code. Phase B7 inverts this: the chat_trigger
plugin owns its own dispatch primitive, the WS handler imports it.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live
in the plugin folder.

Legacy ``event_type`` (``"chat_message_received"``) is preserved so
the ``ChatTriggerNode.event_type`` ClassVar still matches.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


# Legacy event_type the event_waiter dispatches by; trigger nodes
# subscribe on this string (matches ``ChatTriggerNode.event_type``).
_LEGACY_EVENT_TYPE = "chat_message_received"


# ---- Typed factory ---------------------------------------------------------


def chat_message_received(event_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming chat message envelope. ``subject`` is the session_id so
    consumers can route per-conversation."""
    payload = dict(event_data)
    session_id = payload.get("session_id")
    return WorkflowEvent(
        source="machinaos://nodes/chat_trigger",
        type="com.machinaos.chat.message.received",
        subject=str(session_id) if session_id else None,
        data=payload,
    )


# ---- Dispatcher wrapper ----------------------------------------------------


def dispatch_chat_message_received(event_data: Mapping[str, Any]) -> int:
    """Dispatch an incoming chat message to waiting ``chatTrigger``
    nodes. Returns the count of resolved waiters."""
    from services import event_waiter

    return event_waiter.dispatch(_LEGACY_EVENT_TYPE, dict(event_data))


__all__ = [
    "chat_message_received",
    "dispatch_chat_message_received",
]
