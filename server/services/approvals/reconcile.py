"""End what nothing waits for any more, and finish sends nothing finished.

A gate's pending row outlives its wait when:

- it came from an in-process run (``runtime == "local"``) and the process
  that ran it has since stopped: in-process runs are not resumed;
- its generation was reset or failed: the run it belonged to is over.

Temporal waits survive restarts (the activity retries and finds its row),
so a Temporal gate row is kept while its generation is live. A held tool
call waits on nothing (its run already went on), so only expiry or a cancel
ends it.

Sends: an approved held call whose send never started (the server stopped
between the decision and the workflow) is started again; the claim at its
revision makes a second start harmless. A send claimed long ago that never
reported broke off somewhere: it ends ``failed`` with an unknown outcome,
since it may have gone out.

Runs once per process, the first time the drafts are listed; local rows
created by this process are never touched.

Expiry: a held call waits on nothing, so the chat watchdog ends one past
its expiry every round (:func:`expire_due`). Rows are kept until their
workflow is deleted.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, List

from core.logging import get_logger
from services.approvals import store
from services.approvals.listeners import change_of, notify_approval_changed

logger = get_logger(__name__)

PROCESS_STARTED = datetime.now(timezone.utc)
_LIVE_STATUSES = frozenset({"starting", "running", "pausing", "paused", "resuming"})
#: How long after its grace an approved send may still be on its way.
_START_GRACE = timedelta(seconds=60)
#: How long a claimed send may run before it counts as broken off.
_SEND_DEADLINE = timedelta(minutes=30)
_done = False


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def cancel_orphaned_pending(database: Any, *, started_at: datetime = PROCESS_STARTED) -> List[str]:
    cancelled: List[str] = []
    for row in await store.list_pending(database):
        if (row.kind or "gate") != "gate":
            continue
        orphaned = False
        if row.runtime == "local":
            orphaned = _aware(row.created_at) < started_at
        else:
            control = await database.get_latest_workflow_control(row.workflow_id)
            orphaned = control is None or control.generation != row.generation or control.status not in _LIVE_STATUSES
        if not orphaned:
            continue
        try:
            settled = await store.settle(database, row.id, expected_revision=row.revision, status="cancelled")
        except store.ApprovalConflict:
            continue
        cancelled.append(settled.id)
        await notify_approval_changed(change_of(settled, "cancelled"))
    if cancelled:
        logger.info("Cancelled drafts nothing was waiting for", count=len(cancelled))
    return cancelled


async def recover_sends(database: Any, *, now: datetime | None = None) -> List[str]:
    """Restart sends that never started; end sends that never reported."""
    from services.approvals import execution

    now = now or datetime.now(timezone.utc)
    touched: List[str] = []
    for row in await store.list_unfinished_sends(database):
        if row.status == "approved" and row.consumed_at is None:
            grace = store.aware(row.grace_until)
            if grace is None or now - grace < _START_GRACE:
                continue
            try:
                await execution.start_send(row)
                touched.append(row.id)
            except Exception:
                logger.warning("Could not restart a send", approval_id=row.id, exc_info=True)
        elif row.status == "sending":
            claimed = store.aware(row.consumed_at) or store.aware(row.updated_at)
            if claimed is None or now - claimed < _SEND_DEADLINE:
                continue
            try:
                failed = await store.transition(
                    database,
                    row.id,
                    expected_revision=row.revision,
                    from_statuses=("sending",),
                    status="failed",
                    values={"outcome": "unknown", "outcome_error": "The send never reported back.", "outcome_at": now},
                )
            except store.ApprovalConflict:
                continue
            touched.append(failed.id)
            await notify_approval_changed(change_of(failed, "failed"))
            await execution.tell_employee(database, failed)
    return touched


async def expire_due(database: Any, *, now: datetime | None = None) -> List[str]:
    """End the held calls past their expiry (``decisions.expire_if_due``,
    which announces each). A gate's row is its own node's to end."""
    from services.approvals.decisions import expire_if_due

    now = now or datetime.now(timezone.utc)
    expired: List[str] = []
    for row in await store.list_pending(database):
        if row.kind != "tool_call":
            continue
        settled = await expire_if_due(database, row, now)
        if settled.status == "expired":
            expired.append(settled.id)
    if expired:
        logger.info("Ended held drafts past their expiry", count=len(expired))
    return expired


async def reconcile_once(database: Any) -> None:
    global _done
    if _done:
        return
    _done = True
    try:
        await cancel_orphaned_pending(database)
        await recover_sends(database)
    except Exception:
        _done = False
        logger.warning("Could not reconcile waiting drafts", exc_info=True)


def reset_for_tests() -> None:
    global _done
    _done = False


__all__ = ["PROCESS_STARTED", "cancel_orphaned_pending", "expire_due", "reconcile_once", "recover_sends"]
