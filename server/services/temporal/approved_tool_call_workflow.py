"""Send a tool call the owner approved (services/approvals/execution.py).

One run per Send (its id names the approval and the revision Send left it
at). It sleeps until the Undo window closes, claims the row at that
revision (an Undo, a newer Send or a cancel moved it, and the run ends with
nothing sent), runs the node's own activity once with no retry, since a
message must never go twice, and records how it went: ``sent``, ``not_sent``
(the node said it did not go out) or ``unknown`` (the activity broke off,
so it may have).

The claim, the run and the record are :func:`run_approved_call`, which an
agent's turn that waited for the owner uses too (RFC-0007 §4.6).

``payload_version`` 1: ``{approval_id, revision, grace_until}``.
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, is_cancelled_exception

from services.temporal._retry_policies import BOOKKEEPING_RETRY

BOOKKEEPING_TIMEOUT = timedelta(seconds=30)
SEND_TIMEOUT = timedelta(minutes=10)
SEND_HEARTBEAT = timedelta(minutes=2)
#: Never retried: a retry could send it twice.
SEND_ONCE = RetryPolicy(maximum_attempts=1)
#: How the node's own activity runs when the caller names nothing else.
SEND_OPTIONS: Dict[str, Any] = {"start_to_close_timeout": SEND_TIMEOUT, "heartbeat_timeout": SEND_HEARTBEAT, "retry_policy": SEND_ONCE}


async def run_approved_call(
    approval_id: str,
    revision: int,
    token: str,
    *,
    activity: Optional[str] = None,
    overlay: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    options: Optional[Dict[str, Any]] = None,
    ids: Tuple[Optional[str], Optional[str], Optional[str]] = (None, None, None),
) -> Dict[str, Any]:
    """Claim the row at ``revision``, run the call once, record how it went.

    Returns ``{claimed: False, reason}`` when the claim was refused (the row
    moved), else ``{claimed: True, result, outcome, error?, cancelled?}``:
    the node's own result, ``sent`` / ``not_sent`` / ``unknown``, and, when
    the call broke off, why and whether a cancel broke it. The record is
    shielded, so a cancel never leaves the row sending.

    By default it runs the activity and input the claim names
    (``ApprovedToolCallWorkflow``). An agent's turn passes its tool's own
    ``activity``, an ``overlay`` that lays the claim's input over the call's
    payload, its tool ``options`` with one attempt, and ``ids`` (claim, run,
    record) that name the approval."""
    claim_id, run_id, record_id = ids
    claim = await workflow.execute_activity(
        "approvals.claim_send",
        {"approval_id": approval_id, "revision": revision, "claim_token": token},
        activity_id=claim_id,
        start_to_close_timeout=BOOKKEEPING_TIMEOUT,
        retry_policy=BOOKKEEPING_RETRY,
    )
    if not claim.get("claimed"):
        return {"claimed": False, "reason": claim.get("reason")}

    report: Dict[str, Any] = {"approval_id": approval_id, "claim_token": token}
    answer: Dict[str, Any] = {"claimed": True, "result": None}
    try:
        result = await workflow.execute_activity(
            activity or str(claim["activity"]),
            overlay(claim["context"]) if overlay is not None else claim["context"],
            activity_id=run_id,
            **(options or SEND_OPTIONS),
        )
        answer["result"] = result
        if isinstance(result, dict) and result.get("success") is False:
            report.update(success=False, error=str(result.get("error") or "It did not go out."))
        else:
            report.update(success=True)
    except ActivityError as exc:
        cause = exc.cause if exc.cause is not None else exc
        report.update(broke_off=True, error=f"{type(cause).__name__}: {cause}")
        answer.update(error=report["error"], cancelled=is_cancelled_exception(exc))

    recorded = await asyncio.shield(
        workflow.execute_activity(
            "approvals.record_outcome",
            report,
            activity_id=record_id,
            start_to_close_timeout=BOOKKEEPING_TIMEOUT,
            retry_policy=BOOKKEEPING_RETRY,
        )
    )
    return {**answer, "status": recorded.get("status"), "outcome": recorded.get("outcome")}


@workflow.defn(name="ApprovedToolCallWorkflow", sandboxed=False)
class ApprovedToolCallWorkflow:
    @workflow.run
    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        approval_id = str(payload.get("approval_id") or "")
        info = workflow.info()
        token = hashlib.sha256(f"{info.workflow_id}:{info.run_id}".encode()).hexdigest()[:32]

        grace_until = payload.get("grace_until")
        if grace_until:
            due = datetime.fromisoformat(str(grace_until))
            wait = (due - workflow.now()).total_seconds()
            if wait > 0:
                await workflow.sleep(timedelta(seconds=wait))

        sent = await run_approved_call(approval_id, int(payload.get("revision") or 0), token)
        if not sent["claimed"]:
            return {"sent": False, "reason": sent["reason"]}
        return {"sent": sent["status"] == "sent", "outcome": sent["outcome"]}


__all__ = ["ApprovedToolCallWorkflow", "run_approved_call"]
