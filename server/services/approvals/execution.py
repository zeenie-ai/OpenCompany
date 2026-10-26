"""Sending a held tool call once the owner pressed Send.

``start_send(row)`` starts ``ApprovedToolCallWorkflow`` (services/temporal/
approved_tool_call_workflow.py) for the row's current revision. The workflow
sleeps until ``grace_until`` (Undo works until then), claims the row
(``approvals.claim_send``: approved -> sending, only at the revision it was
started for, so an Undo, a newer Send or a cancel ends it quietly), runs the
node's own activity (``node.<type>.v<n>``) once, with no retry, and records
how it went (``approvals.record_outcome``): sent, not sent, or ``unknown``
when the call broke off and may have gone out. The employee learns it at the
start of its next turn in the chat (an ``[update]`` note), the card through
``approval_lifecycle``.

The node runs with the call's arguments over its own settings, as the
employee made it (with the owner's edits); what it sends as (its locked
fields) comes from its settings. Its activity checks that the row it
carries is the one being sent (``tool_calls.check``).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from core.logging import get_logger
from services.approvals import store
from services.approvals.listeners import change_of, notify_approval_changed

logger = get_logger(__name__)

WORKFLOW_NAME = "ApprovedToolCallWorkflow"
PAYLOAD_VERSION = 1


class SendUnavailable(RuntimeError):
    """The workflow engine is not there to send it."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def send_workflow_id(row: Any) -> str:
    return f"approval-send-{row.id}-r{row.revision}"


def send_payload(row: Any) -> Dict[str, Any]:
    grace = store.aware(row.grace_until)
    return {
        "payload_version": PAYLOAD_VERSION,
        "approval_id": row.id,
        "revision": row.revision,
        "grace_until": grace.isoformat() if grace is not None else None,
    }


async def start_send(row: Any) -> None:
    """Start the workflow that sends ``row`` after its grace."""
    from core.container import container

    wrapper = container.temporal_client()
    if wrapper is None or getattr(wrapper, "client", None) is None:
        raise SendUnavailable("send_unavailable")
    await wrapper.client.start_workflow(
        WORKFLOW_NAME,
        args=[send_payload(row)],
        id=send_workflow_id(row),
        task_queue=container.settings().temporal_task_queue,
    )


def node_activity_name(node_type: str) -> Optional[str]:
    from services.node_registry import get_node_class

    node_cls = get_node_class(node_type)
    if node_cls is None:
        return None
    return f"node.{node_cls.type}.v{getattr(node_cls, 'version', 1)}"


def node_context(row: Any, token: str) -> Dict[str, Any]:
    """What the node's activity runs with. A graph node's card (no agent)
    keeps what it sent with its templates already resolved: it runs with no
    graph, so nothing resolves again and blanks it, and with no tool
    arguments, so a tool node keeps the saved operation."""
    graph = not row.agent_node_id
    return {
        "node_id": row.tool_node_id or row.node_id,
        "node_type": row.node_type,
        "node_data": dict(row.node_data or {}),
        **({} if graph else {"tool_args": dict(row.args or {})}),
        "inputs": {},
        "nodes": None if graph else [],
        "edges": None if graph else [],
        "workflow_id": row.workflow_id,
        "session_id": row.workflow_id,
        "execution_id": row.execution_id,
        "generation": row.generation,
        "user_id": row.owner_id,
        "approval_execution": {"approval_id": row.id, "claim_token": token},
    }


async def claim_send(database: Any, approval_id: str, revision: int, token: str) -> Dict[str, Any]:
    """Take the row on for sending: ``{claimed, reason?, activity?,
    context?}``. A retry with the same token finds its own claim."""
    row = await store.get(database, approval_id)
    if row is None:
        return {"claimed": False, "reason": "not_found"}
    if row.status == "sending" and row.claim_token == token:
        activity_name = node_activity_name(row.node_type or "")
        return {"claimed": True, "activity": activity_name, "context": node_context(row, token)}
    if (row.kind or "gate") != "tool_call" or row.status != "approved" or row.revision != revision:
        return {"claimed": False, "reason": "changed"}
    activity_name = node_activity_name(row.node_type or "")
    if activity_name is None:
        failed = await _fail_unclaimed(database, row, "The tool it uses is no longer installed.")
        return {"claimed": False, "reason": "unknown_tool", "status": getattr(failed, "status", None)}
    try:
        claimed = await store.transition(
            database,
            row.id,
            expected_revision=row.revision,
            from_statuses=("approved",),
            status="sending",
            values={"claim_token": token, "consumed_at": _utcnow()},
        )
    except store.ApprovalConflict:
        return {"claimed": False, "reason": "changed"}
    await notify_approval_changed(change_of(claimed, "sending"))
    return {"claimed": True, "activity": activity_name, "context": node_context(claimed, token)}


async def _fail_unclaimed(database: Any, row: Any, error: str) -> Any:
    try:
        failed = await store.transition(
            database,
            row.id,
            expected_revision=row.revision,
            from_statuses=("approved",),
            status="failed",
            values={"outcome": "not_sent", "outcome_error": error[:1000], "outcome_at": _utcnow()},
        )
    except store.ApprovalConflict:
        return None
    await notify_approval_changed(change_of(failed, "failed"))
    await tell_employee(database, failed)
    return failed


def outcome_of(result: Any, error: Optional[str]) -> Dict[str, Any]:
    """``{outcome, error?}`` from what the node's activity returned (or the
    error it broke off with)."""
    if error is not None:
        return {"outcome": "unknown", "error": error}
    if isinstance(result, dict) and result.get("success") is False:
        return {"outcome": "not_sent", "error": str(result.get("error") or "It did not go out.")}
    return {"outcome": "sent"}


async def record_outcome(database: Any, approval_id: str, token: str, outcome: str, error: Optional[str]) -> Optional[Any]:
    """Record how the send went. None when the row is not this claim's."""
    row = await store.get(database, approval_id)
    if row is None or row.status != "sending" or row.claim_token != token:
        return None
    status = "sent" if outcome == "sent" else "failed"
    try:
        settled = await store.transition(
            database,
            row.id,
            expected_revision=row.revision,
            from_statuses=("sending",),
            status=status,
            values={
                "outcome": outcome,
                "outcome_error": (error or None) and str(error)[:1000],
                "outcome_at": _utcnow(),
            },
        )
    except store.ApprovalConflict:
        return None
    logger.info("Sent a held call" if status == "sent" else "A held call did not go out", approval_id=row.id, outcome=outcome)
    await notify_approval_changed(change_of(settled, status))
    await tell_employee(database, settled)
    return settled


def update_note(row: Any) -> Optional[str]:
    """The ``[update]`` line the employee reads about one of its drafts."""
    maybe = "may or may not have happened" if row.kind == "effect" else "may or may not have gone out"
    result = {
        "sent": "sent",
        "failed": "not sent" if row.outcome == "not_sent" else maybe,
        "discarded": "discarded by the owner",
        "expired": "expired unsent",
        "cancelled": "cancelled unsent",
    }.get(row.status)
    if result is None:
        return None
    payload: Dict[str, Any] = {
        "approval_id": row.id,
        "what": row.action or f"{row.channel} message",
        "to": row.recipient_label or row.recipient or None,
        "result": result,
    }
    if row.edited and row.status == "sent":
        payload["edited_by_owner"] = True
    if row.status == "failed" and row.outcome_error:
        payload["error"] = row.outcome_error[:300]
    if row.status == "discarded" and row.note:
        payload["owner_note"] = row.note
    return f"[update]{json.dumps(payload, ensure_ascii=False, separators=(', ', ': '))}[/update]"


async def tell_employee(database: Any, row: Any) -> None:
    """Leave the outcome for the employee's next turn in the chat (its
    session is the workflow's). A call its turn waits on hears it there."""
    text = update_note(row)
    if text is None or not row.workflow_id or row.runtime == "turn":
        return
    try:
        from services.chat.notes import upsert_note

        await upsert_note(database, session_id=row.workflow_id, key=f"approval:{row.id}", kind="update", text=text)
    except Exception:  # noqa: BLE001 - the card shows it either way
        logger.warning("Could not leave the employee a note about a draft", approval_id=row.id, exc_info=True)


__all__ = [
    "PAYLOAD_VERSION",
    "SendUnavailable",
    "WORKFLOW_NAME",
    "claim_send",
    "node_activity_name",
    "node_context",
    "outcome_of",
    "record_outcome",
    "send_payload",
    "send_workflow_id",
    "start_send",
    "tell_employee",
    "update_note",
]
