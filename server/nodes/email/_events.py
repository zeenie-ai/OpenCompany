"""Wave 12 B4: CloudEvents factories + broadcaster wrappers for email.

Plugin-specific event emission — replaces:
  - ``event_waiter.dispatch("email_received", email_data)`` in
    ``email_receive/__init__.py``

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder.

Legacy ``event_type`` (``"email_received"``) is preserved on the
dispatch path so the ``emailReceive`` trigger node's ``event_type``
ClassVar still matches without a coordinated registry-side rename
(Phase B11 / C1 will swap to the typed
``com.machinaos.email.message.received`` form together).
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


# Legacy event_type the event_waiter dispatches by; trigger nodes
# subscribe on this string (matches ``EmailReceiveNode.event_type``).
_LEGACY_EVENT_TYPE = "email_received"


# ---- Typed factory ---------------------------------------------------------


def email_message_received(email_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming email envelope. ``subject`` is the ``message_id`` so
    consumers can dedup and correlate (subject of the MAIL message
    itself goes in ``data.subject``)."""
    payload = dict(email_data)
    message_id = payload.get("message_id") or payload.get("id")
    return WorkflowEvent(
        source="machinaos://nodes/email",
        type="com.machinaos.email.message.received",
        subject=str(message_id) if message_id else None,
        data=payload,
    )


# ---- Dispatcher wrapper ----------------------------------------------------


def dispatch_email_received(email_data: Mapping[str, Any]) -> int:
    """Dispatch an incoming email to waiting ``emailReceive`` trigger
    nodes. Returns the count of resolved waiters.

    Replaces the direct ``event_waiter.dispatch("email_received", ...)``
    call in ``email_receive/__init__.py``.
    """
    from services import event_waiter

    return event_waiter.dispatch(_LEGACY_EVENT_TYPE, dict(email_data))


__all__ = [
    "dispatch_email_received",
    "email_message_received",
]
