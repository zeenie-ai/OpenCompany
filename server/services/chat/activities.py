"""The Temporal activities a chat run starts and finishes through.

MachinaWorkflow schedules them, behind the ``machina-chat-run-v1`` patch,
for a run that the owner's chat message spawned (services/temporal/
workflow.py). Both are safe to retry: the ledger accepts a repeat from the
workflow that claimed the run and publishes nothing twice. A failure raises,
so Temporal retries; when the retries run out the workflow carries on and
the watchdog ends the run.
"""

from __future__ import annotations

from typing import Any, Dict

from temporalio import activity


@activity.defn(name="chat_run.start")
async def start_chat_run_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Claim the run for the calling workflow.

    Payload ``{run_id, temporal_workflow_id, temporal_run_id}``. Returns
    ``{claimed, session_id?}``: False when another workflow claimed it first
    (several chat triggers in one graph) or it can no longer start (it was
    stopped, or the watchdog ended it); that workflow then runs without
    tracking. ``session_id`` is the chat's session, from the run's row: the
    run's nodes carry it in ``run_scope``.
    """
    from core.container import container
    from services.chat import ledger

    run = await ledger.start_run(
        container.database(),
        run_id=str(payload.get("run_id") or ""),
        temporal_workflow_id=str(payload.get("temporal_workflow_id") or ""),
        temporal_run_id=payload.get("temporal_run_id"),
    )
    if run is None:
        return {"claimed": False}
    return {"claimed": True, "session_id": run.session_id}


@activity.defn(name="chat_run.finish")
async def finish_chat_run_activity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """End the run the calling workflow claimed.

    Payload ``{run_id, temporal_workflow_id, temporal_run_id, success,
    error?, code?, hint?, requires_user_action?}``. Returns ``{state}``, the
    run's state afterwards, or None when the caller does not hold it.
    """
    from core.container import container
    from services.chat import ledger

    run = await ledger.finish_run(
        container.database(),
        run_id=str(payload.get("run_id") or ""),
        temporal_workflow_id=str(payload.get("temporal_workflow_id") or ""),
        temporal_run_id=payload.get("temporal_run_id"),
        success=bool(payload.get("success")),
        error=payload.get("error"),
        code=payload.get("code"),
        hint=payload.get("hint"),
        requires_user_action=payload.get("requires_user_action"),
    )
    return {"state": run.state if run is not None else None}


CHAT_RUN_ACTIVITIES = (start_chat_run_activity, finish_chat_run_activity)

__all__ = ["CHAT_RUN_ACTIVITIES", "finish_chat_run_activity", "start_chat_run_activity"]
