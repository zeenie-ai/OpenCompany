"""Wait for the owner's answer to a held tool call its turn waits on
(RFC-0007 §4.6; services/approvals/waiting.py).

AgentWorkflow starts one per waiting call, id ``approval-<approval_id>``,
and awaits it outside any admitted action, so Stop completes while it
waits. It reads the row (``approvals.read``, which ends it ``expired`` past
its expiry) and, while the row is pending, waits for a ``nudge`` Signal (the
row changed) or the row's expiry, then reads again. It returns the row's
answer: ``{status, revision, note?}``. Cancelled, it ends a still pending
row ``cancelled`` before the cancel goes on.

It admits no actions and answers no control Updates, so Stop's tree walk
leaves it out (services/deployment/execution_control.py). Unsandboxed, like
every type in this package (its ``__init__`` has import side effects).

``payload_version`` 1: ``{approval_id}``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Dict

from temporalio import workflow

from services.temporal.approved_tool_call_workflow import BOOKKEEPING_RETRY, BOOKKEEPING_TIMEOUT

#: The shortest wait between two reads.
MIN_WAIT = timedelta(seconds=1)


@workflow.defn(name="ApprovalWorkflow", sandboxed=False)
class ApprovalWorkflow:
    def __init__(self) -> None:
        self._nudged = False

    @workflow.signal
    def nudge(self) -> None:
        self._nudged = True

    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(payload.get("approval_id") or "")
        try:
            while True:
                # Before the read, so a nudge during it is not lost.
                self._nudged = False
                row = await workflow.execute_activity(
                    "approvals.read",
                    {"approval_id": approval_id},
                    start_to_close_timeout=BOOKKEEPING_TIMEOUT,
                    retry_policy=BOOKKEEPING_RETRY,
                )
                if row.get("status") != "pending":
                    return row
                due = datetime.fromisoformat(str(row["expires_at"]))
                try:
                    await workflow.wait_condition(lambda: self._nudged, timeout=max(due - workflow.now(), MIN_WAIT))
                except asyncio.TimeoutError:
                    pass  # the next read ends it expired, unless a decision got there first
        except asyncio.CancelledError:
            await asyncio.shield(
                workflow.execute_activity(
                    "approvals.read",
                    {"approval_id": approval_id, "cancel": True},
                    start_to_close_timeout=BOOKKEEPING_TIMEOUT,
                    retry_policy=BOOKKEEPING_RETRY,
                )
            )
            raise


__all__ = ["ApprovalWorkflow"]
