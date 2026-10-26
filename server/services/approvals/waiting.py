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

A nudge to a wait that has ended, or with no workflow engine, is dropped:
the wait reads the row at its expiry in any case. Never imports ``nodes/``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

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


async def cancel_waits(database: Any, workflow_id: str, generation: int) -> int:
    """End the open rows a generation's turns wait on: a Reset terminated
    those turns, and their waits with them (the approval gate's own rows
    are its node's to end)."""
    cancelled = await store.cancel_open(database, workflow_id=workflow_id, generation=generation, runtime=TURN)
    for row in cancelled:
        await notify_approval_changed(change_of(row, "cancelled"))
    if cancelled:
        logger.info("Cancelled the calls a reset turn waited on", workflow_id=workflow_id, count=len(cancelled))
    return len(cancelled)


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
    row is pending, else ``{status, revision, note?}``. A row past its expiry
    ends ``expired`` first; with ``cancel`` (the wait was cancelled) a
    pending row ends ``cancelled``. A row that is gone reads as cancelled.
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
    return {"status": row.status, "revision": row.revision, **({"note": row.note} if row.note else {})}


__all__ = ["TURN", "WORKFLOW_NAME", "cancel_waits", "engine", "mark_read", "nudge", "nudge_on_change", "read", "wait_workflow_id"]
