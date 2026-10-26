"""A held tool call its agent's turn waits on (``runtime: turn``, RFC-0007
§4.6).

The turn waits in ``ApprovalWorkflow`` (services/temporal/approval_workflow.py),
one per call, id ``approval-<approval_id>``. It reads the row
(:func:`read`, the ``approvals.read`` activity) and, while the row is
pending, waits for a ``nudge`` Signal or the row's expiry, then reads again.

- :func:`nudge_on_change`, an approval listener: every change to a turn row
  (a decision, an expiry, a cancel) nudges its wait.
- ``reconcile.renudge_turns``, from the chat watchdog's round: a decided
  row its wait has not read yet is nudged again, in case the first nudge
  was lost.
- :func:`decline_with_message`: a message the owner sends while the turn
  waits declines its calls, the message their note (RFC-0007 D3), and the
  turn reads that the owner wrote instead of answering.
- :func:`run_waited`: the chat watchdog does not count the wait toward the
  run's time, and the owner's answer starts the run's window again (D2).
- :func:`cancel_waits`: a Reset, or a run whose workflow closed, ends the
  rows nothing waits on any more.

A nudge to a wait that has ended, or with no workflow engine, is dropped:
the wait reads the row at its expiry in any case. Never imports ``nodes/``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import update

from core.logging import get_logger
from models.approvals import ApprovalRequest
from services.approvals import store
from services.approvals.contract import DEFAULT_TIMEOUT_HOURS
from services.approvals.listeners import ApprovalChange, change_of, notify_approval_changed

logger = get_logger(__name__)

WORKFLOW_NAME = "ApprovalWorkflow"
#: The row's ``runtime`` when its turn waits on it.
TURN = "turn"
#: The decision key prefix of a message that declined a waiting call.
MESSAGE_KEY = "chat:"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def wait_workflow_id(approval_id: str) -> str:
    return f"approval-{approval_id}"


def engine() -> Any:
    """The connected Temporal client, or None."""
    from core.container import container

    wrapper = container.temporal_client()
    return getattr(wrapper, "client", None) if wrapper is not None else None


async def nudge(approval_id: str, client: Any) -> bool:
    """Wake the wait on ``approval_id`` to read its row again. False when
    that wait has ended."""
    from temporalio.service import RPCError, RPCStatusCode

    try:
        await client.get_workflow_handle(wait_workflow_id(approval_id)).signal("nudge")
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        logger.debug("The approval wait had ended", approval_id=approval_id)
        return False
    return True


async def nudge_on_change(change: ApprovalChange) -> None:
    if change.runtime != TURN or change.status == "pending":
        return
    client = engine()
    if client is not None:
        await nudge(change.approval_id, client)


async def cancel_waits(
    database: Any, workflow_id: str, generation: Optional[int] = None, *, run_ids: Optional[Sequence[str]] = None
) -> int:
    """End the open rows turns waited on when nothing waits on them any
    more: a Reset's generation (it terminated those turns, and their waits
    with them), or chat runs whose workflow closed (``run_ids``). The
    approval gate's own rows are its node's to end."""
    cancelled = await store.cancel_open(database, workflow_id=workflow_id, generation=generation, run_ids=run_ids, runtime=TURN)
    for row in cancelled:
        await notify_approval_changed(change_of(row, "cancelled"))
    if cancelled:
        logger.info("Cancelled the calls ended turns waited on", workflow_id=workflow_id, count=len(cancelled))
    return len(cancelled)


async def run_waited(database: Any, run_id: str, *, since: datetime) -> bool:
    """Whether a chat run's turn waits on the owner's answer to a call, or
    has had their answer since ``since``."""
    for row in await store.list_approvals(database, run_id=run_id, kind="tool_call", limit=100):
        decided = store.aware(row.decided_at)
        if row.runtime == TURN and (row.status == "pending" or (decided is not None and decided > since)):
            return True
    return False


def message_key(message_uid: str) -> str:
    """The decision key a message declines waiting calls under: its id, so
    the same message sent again finds them (:func:`declined_with`)."""
    return f"{MESSAGE_KEY}{message_uid}"


async def decline_with_message(database: Any, *, run_id: str, owner: str, message: str, decision_key: str) -> List[ApprovalRequest]:
    """Decline the calls a chat run's turn waits on, because the owner sent
    a message instead of answering: the message is their note, on the card
    and in what the turn reads. A row another decision settled first is left
    as it is."""
    from services.approvals.decisions import DecisionRefused, apply

    declined: List[ApprovalRequest] = []
    for row in await store.list_approvals(database, owner_id=owner, run_id=run_id, status="pending", kind="tool_call", limit=100):
        if row.runtime != TURN:
            continue
        try:
            moved, _ = await apply(database, row, decision="discard", decision_key=decision_key, actor=owner, note=message)
        except DecisionRefused:
            continue
        await notify_approval_changed(change_of(moved, "decided"))
        declined.append(moved)
    return declined


async def declined_with(database: Any, decision_key: str) -> List[ApprovalRequest]:
    """The calls a message declined (:func:`decline_with_message`)."""
    return await store.list_approvals(database, decision_key=decision_key, status="discarded")


async def mark_read(database: Any, approval_id: str) -> None:
    """The wait took the row's answer, or nothing waits for it any more:
    nothing nudges it again."""
    async with database.get_session() as session:
        await session.execute(
            update(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id, ApprovalRequest.consumed_at.is_(None))
            .values(consumed_at=_utcnow())
        )
        await session.commit()


async def read(database: Any, approval_id: str, *, cancel: bool = False) -> Dict[str, Any]:
    """The answer a wait gets: ``{status, revision, expires_at}`` while the
    row is pending, else ``{status, revision, note?, by_message?}``
    (``by_message``: a message the owner sent declined it). A row past its
    expiry ends ``expired`` first; with ``cancel`` (the wait was cancelled)
    a pending row ends ``cancelled``. A row that is gone reads as cancelled.
    An answer other than ``approved`` marks the row read; a claim marks an
    approved one when its call runs."""
    from services.approvals.decisions import expire_if_due

    row = await store.get(database, approval_id)
    if row is None:
        return {"status": "cancelled", "revision": 0}
    if row.status == "pending" and cancel:
        try:
            row = await store.settle(database, row.id, expected_revision=row.revision, status="cancelled")
            await notify_approval_changed(change_of(row, "cancelled"))
        except store.ApprovalConflict:
            row = await store.get(database, approval_id) or row
    elif row.status == "pending":
        row = await expire_if_due(database, row, _utcnow())
    if row.status == "pending":
        expires = store.aware(row.expires_at) or store.aware(row.created_at) + timedelta(hours=DEFAULT_TIMEOUT_HOURS)
        return {"status": "pending", "revision": row.revision, "expires_at": expires.isoformat()}
    if row.status != "approved":
        await mark_read(database, row.id)
    answer = {"status": row.status, "revision": row.revision, **({"note": row.note} if row.note else {})}
    if row.status == "discarded" and (row.decision_key or "").startswith(MESSAGE_KEY):
        answer["by_message"] = True
    return answer


__all__ = [
    "MESSAGE_KEY",
    "TURN",
    "WORKFLOW_NAME",
    "cancel_waits",
    "decline_with_message",
    "declined_with",
    "engine",
    "mark_read",
    "message_key",
    "nudge",
    "nudge_on_change",
    "read",
    "run_waited",
    "wait_workflow_id",
]
