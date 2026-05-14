"""Wave 12 B5: CloudEvents factories + broadcaster wrappers for google.

Plugin-specific event emission — replaces:
  - ``event_waiter.dispatch("gmail_email_received", email_data)`` in
    ``gmail_receive/__init__.py``.

OAuth completion broadcasts route through the cross-cutting
``StatusBroadcaster.broadcast_credential_event("credential.oauth.connected", ...)``
path (RFC §6.4 cross-cutting tier) — NOT a plugin-named broadcast.
No migration needed here for that flow.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live
in the plugin folder.

Legacy ``event_type`` (``"gmail_email_received"``) is preserved so
the ``gmailReceive`` trigger node's ``event_type`` ClassVar still
matches without a coordinated registry-side rename.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


# Legacy event_type the event_waiter dispatches by; trigger nodes
# subscribe on this string (matches ``GmailReceiveNode.event_type``).
_LEGACY_EVENT_TYPE = "gmail_email_received"


# ---- Typed factory ---------------------------------------------------------


def gmail_message_received(email_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming Gmail message envelope. ``subject`` is the Gmail
    message_id (correlation key for the consumer-side ``markAsRead``
    + dedup paths)."""
    payload = dict(email_data)
    message_id = payload.get("message_id") or payload.get("id")
    return WorkflowEvent(
        source="machinaos://nodes/google",
        type="com.machinaos.gmail.message.received",
        subject=str(message_id) if message_id else None,
        data=payload,
    )


# ---- Dispatcher wrapper ----------------------------------------------------


def dispatch_gmail_received(email_data: Mapping[str, Any]) -> int:
    """Dispatch an incoming Gmail message to waiting ``gmailReceive``
    trigger nodes. Returns the count of resolved waiters."""
    from services import event_waiter

    return event_waiter.dispatch(_LEGACY_EVENT_TYPE, dict(email_data))


__all__ = [
    "dispatch_gmail_received",
    "gmail_message_received",
]
