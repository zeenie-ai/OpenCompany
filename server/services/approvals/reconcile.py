"""Cancel drafts nothing is waiting for any more.

A pending row outlives its wait when:

- it came from an in-process run (``runtime == "local"``) and the process
  that ran it has since stopped: in-process runs are not resumed;
- its generation was reset or failed: the run it belonged to is over.

Temporal waits survive restarts (the activity retries and finds its row),
so a Temporal row is kept while its generation is live. Runs once per
process, the first time the drafts are listed; local rows created by this
process are never touched.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List

from core.logging import get_logger
from services.approvals import store
from services.approvals.listeners import change_of, notify_approval_changed

logger = get_logger(__name__)

PROCESS_STARTED = datetime.now(timezone.utc)
_LIVE_STATUSES = frozenset({"starting", "running", "pausing", "paused", "resuming"})
_done = False


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


async def cancel_orphaned_pending(database: Any, *, started_at: datetime = PROCESS_STARTED) -> List[str]:
    cancelled: List[str] = []
    for row in await store.list_pending(database):
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


def reset_for_tests() -> None:
    global _done
    _done = False


__all__ = ["PROCESS_STARTED", "cancel_orphaned_pending", "reconcile_once"]
