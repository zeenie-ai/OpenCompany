"""CloudEvents factory for gmail.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder.

No dispatcher wrapper: the canary delivery path for ``googleGmailReceive``
is :class:`services.temporal.polling_trigger_workflow.PollingTriggerWorkflow`
(not Signal-based). The polling activity (built by
:meth:`services.plugin.polling.PollingTriggerNode.as_poll_activity`)
returns the raw email dict back to the workflow which spawns the child
MachinaWorkflow directly — no envelope-on-the-wire step is needed.

The factory below is kept for parity with the other plugin ``_events.py``
modules (and for any future ad-hoc emit usage); the legacy
``dispatch_gmail_received`` shim was deleted in Wave 13 because it
dispatched to ``event_waiter`` waiters that were never registered in
canary-on mode (the polling workflow owns delivery).

OAuth completion broadcasts route through the cross-cutting
``StatusBroadcaster.broadcast_credential_event("credential.oauth.connected", ...)``
path (RFC §6.4 cross-cutting tier) — NOT a plugin-named broadcast.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.events.envelope import WorkflowEvent


def gmail_message_received(email_data: Mapping[str, Any]) -> WorkflowEvent:
    """Incoming Gmail message envelope. ``subject`` is the Gmail
    message_id (correlation key for the consumer-side ``markAsRead``
    + dedup paths)."""
    payload = dict(email_data)
    message_id = payload.get("message_id") or payload.get("id")
    return WorkflowEvent(
        source="opencompany://nodes/google",
        type="com.opencompany.gmail.message.received",
        subject=str(message_id) if message_id else None,
        data=payload,
    )


__all__ = [
    "gmail_message_received",
]
