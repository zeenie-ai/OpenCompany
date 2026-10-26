"""The Temporal activities ``ApprovedToolCallWorkflow`` sends a held tool call
through (services/approvals/execution.py), and the one ``ApprovalWorkflow``
reads the row a turn waits on with (services/approvals/waiting.py).

All are safe to retry: a claim retried by the same workflow run finds its
own claim (the token is the run's), an outcome is recorded once (only a
row still ``sending`` under that claim moves), and a read moves a row only
from pending.
"""

from __future__ import annotations

from typing import Any, Dict

from temporalio import activity


@activity.defn(name="approvals.claim_send")
async def claim_send_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Payload ``{approval_id, revision, claim_token}``. Returns ``{claimed,
    reason?, activity?, context?}``: the node activity to run and its
    input."""
    from core.container import container
    from services.approvals.execution import claim_send

    return await claim_send(
        container.database(),
        str(payload.get("approval_id") or ""),
        int(payload.get("revision") or 0),
        str(payload.get("claim_token") or ""),
    )


@activity.defn(name="approvals.record_outcome")
async def record_outcome_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Payload ``{approval_id, claim_token, success?, error?, broke_off?}``.
    Returns ``{recorded, status, outcome}``."""
    from core.container import container
    from services.approvals.execution import outcome_of, record_outcome

    error = str(payload.get("error") or "It broke off.") if payload.get("broke_off") else None
    result = {"success": payload.get("success", True) is not False, "error": payload.get("error")}
    outcome = outcome_of(result, error)
    settled = await record_outcome(
        container.database(),
        str(payload.get("approval_id") or ""),
        str(payload.get("claim_token") or ""),
        outcome["outcome"],
        outcome.get("error"),
    )
    return {"recorded": settled is not None, "status": getattr(settled, "status", None), "outcome": outcome["outcome"]}


@activity.defn(name="approvals.read")
async def read_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Payload ``{approval_id, cancel?}``. Returns ``{status, revision,
    expires_at?, note?}`` (``waiting.read``)."""
    from core.container import container
    from services.approvals.waiting import read

    return await read(container.database(), str(payload.get("approval_id") or ""), cancel=payload.get("cancel") is True)


APPROVAL_ACTIVITIES = [claim_send_activity, record_outcome_activity, read_activity]

__all__ = ["APPROVAL_ACTIVITIES", "claim_send_activity", "read_activity", "record_outcome_activity"]
