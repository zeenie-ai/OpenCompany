"""End what nothing waits for any more, and finish sends nothing finished.

A gate's pending row outlives its wait when:

- it came from an in-process run (``runtime == "local"``) and the process
  that ran it has since stopped: in-process runs are not resumed;
- its generation was reset or failed: the run it belonged to is over.

Temporal waits survive restarts (the activity retries and finds its row),
so a Temporal gate row is kept while its generation is live. A held tool
call waits on nothing (its run already went on), so only expiry or a cancel
ends it.

Sends, every round of the chat watchdog (:func:`recover_sends`): an
approved held call whose send never started (the server stopped between the
decision and the workflow), or whose claim refused because its employee was
stopped, is started again once its employee runs; the claim at its revision
makes a second start harmless. One whose generation ended is cancelled, and
the employee told (``execution.send_state``). A send claimed long ago that
never reported broke off somewhere: it ends ``failed`` with an unknown
outcome, since it may have gone out.

Orphaned gate rows are ended once per process, the first time the drafts
are listed; local rows created by this process are never touched.

Expiry: a held call waits on nothing, so the chat watchdog ends one past
its expiry every round (:func:`expire_due`). Rows are kept until their
workflow is deleted.

Waits: a decided row a turn waits on is nudged again from the same round
when its wait has not read it (:func:`renudge_turns`), in case the nudge
the decision sent was lost (services/approvals/waiting.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from core.logging import get_logger
from services.approvals import execution, store
from services.approvals.listeners import change_of, notify_approval_changed

logger = get_logger(__name__)

PROCESS_STARTED = datetime.now(timezone.utc)
#: How long after its grace an approved send may still be on its way.
_START_GRACE = timedelta(seconds=60)
#: How long a claimed send may run before it counts as broken off.
_SEND_DEADLINE = timedelta(minutes=30)
#: How long a decision's own nudge may take before the round nudges again.
_RENUDGE_AFTER = timedelta(seconds=10)
_done = False
#: Approved rows whose wait had ended: their turn takes them on itself.
_ended: set = set()


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
            orphaned = control is None or control.generation != row.generation or control.status not in execution.LIVE_STATES
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
    """Start sends that never started, or that waited for Resume, once their
    employee runs; cancel those of a generation that ended; end sends that
    never reported."""
    now = now or datetime.now(timezone.utc)
    touched: List[str] = []
    controls: Dict[str, Any] = {}
    for row in await store.list_unfinished_sends(database):
        if row.status == "approved" and row.consumed_at is None:
            grace = store.aware(row.grace_until)
            if grace is None or now - grace < _START_GRACE:
                continue
            if row.workflow_id not in controls:
                controls[row.workflow_id] = await database.get_latest_workflow_control(row.workflow_id)
            state = execution.send_state(row, controls[row.workflow_id])
            if state == "wait":
                continue
            if state == "ended":
                if await execution.cancel_ended(database, row) is not None:
                    touched.append(row.id)
                continue
            try:
                await execution.start_send(row)
                touched.append(row.id)
            except execution.SendUnavailable:
                pass  # no workflow engine: the next round tries again
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
    except Exception:
        _done = False
        logger.warning("Could not reconcile waiting drafts", exc_info=True)


async def renudge_turns(database: Any, *, now: datetime | None = None) -> List[str]:
    """Nudge again the waits whose decided rows they have not read, while
    the row's generation runs (a stopped employee's turns go on after
    Resume). A row whose wait has ended is left: one nothing will read is
    marked read, an approved one is its turn's to take on."""
    from services.approvals import waiting

    client = waiting.engine()
    if client is None:
        return []
    now = now or datetime.now(timezone.utc)
    nudged: List[str] = []
    for row in await store.list_unread_waits(database):
        decided = store.aware(row.decided_at) or store.aware(row.updated_at)
        if row.id in _ended or decided is None or now - decided < _RENUDGE_AFTER:
            continue
        control = await database.get_latest_workflow_control(row.workflow_id)
        if control is None or control.generation != row.generation or control.status != "running":
            continue
        try:
            reached = await waiting.nudge(row.id, client)
        except Exception:
            logger.warning("Could not nudge an approval wait", approval_id=row.id, exc_info=True)
            continue
        if reached:
            nudged.append(row.id)
        elif row.status == "approved":
            _ended.add(row.id)
        else:
            await waiting.mark_read(database, row.id)
    if nudged:
        logger.info("Nudged approval waits again", count=len(nudged))
    return nudged


def reset_for_tests() -> None:
    global _done
    _done = False
    _ended.clear()


__all__ = ["PROCESS_STARTED", "cancel_orphaned_pending", "expire_due", "reconcile_once", "recover_sends", "renudge_turns"]
