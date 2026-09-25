"""``approval_lifecycle``: a draft was requested, decided, expired or cancelled.

Type ``com.opencompany.approval.{requested|decided|expired|cancelled}``,
subject = the approval id. Identity only: never the message, never who it
goes to. The client refetches the drafts it shows (``list_approvals``)
through the authorized handler.

Broadcast directly: no Temporal consumer listens for these.
"""

from __future__ import annotations

from services.approvals.listeners import ApprovalChange
from services.events.envelope import WorkflowEvent

WIRE_KEY = "approval_lifecycle"
SOURCE = "opencompany://nodes/approval_gate"


def approval_lifecycle_event(change: ApprovalChange) -> WorkflowEvent:
    return WorkflowEvent(
        source=SOURCE,
        type=f"com.opencompany.approval.{change.stage}",
        subject=change.approval_id,
        data={
            "approval_id": change.approval_id,
            "workflow_id": change.workflow_id,
            "status": change.status,
            "revision": change.revision,
        },
    )


async def broadcast_approval_change(change: ApprovalChange) -> None:
    from services.status_broadcaster import get_status_broadcaster

    event = approval_lifecycle_event(change)
    await get_status_broadcaster().broadcast({"type": WIRE_KEY, "data": event.model_dump(mode="json", exclude_none=True)})


__all__ = ["WIRE_KEY", "approval_lifecycle_event", "broadcast_approval_change"]
