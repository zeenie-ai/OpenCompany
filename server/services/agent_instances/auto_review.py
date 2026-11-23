"""Review auto-accept: a teammate's submitted work is accepted without
waking its reviewer when a calibrated judgment is sure enough.

A delegated task wakes its reviewer (the Orchestrator, or an AI Employee
that manages specialists) for a model turn whose usual outcome is
``accept_task``. When the owner has set "Skip reviews when sure"
(``UserSettings.review_auto_accept_percent``, 0 = off), the claim activity
asks first, for each submitted task the run was woken to review, the noul
"The result fully completes these instructions." Only a calibrated judgment
counts (Jev, never the chat-model fallback). When every task is judged at
least that sure, they are accepted (marked as accepted automatically), the
reviewer's transcript gets one line saying so, and the run completes with
the accepted work, with no model turn
(``AgentInstanceService.settle_auto_review``). The review gate then submits
the reviewer's own task as it would after a model's review.

Chat's review of the final answer is never skipped: a run of the job's lead
is never settled here. Anything else leaves the review to the reviewer's
model turn: the setting off, a batch holding anything but fresh review
notices of submitted work, no judgment available, or any task judged below
the setting.

The judgment's cost counts toward the job, and so toward the reply that
started it and the employee's monthly budget: it runs inside the job's log
context (``services/usage/recording.py`` reads ``employee_job_id`` from it).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from core.logging import get_logger

logger = get_logger(__name__)

#: The judgment's own time limit, inside the claim activity's 60 s.
JUDGE_TIMEOUT_SECONDS = 15.0
_NAME = "complete"
QUESTION = {_NAME: {"type": "noul", "description": "The result fully completes these instructions."}}


async def auto_accept_percent(database: Any) -> int:
    """The owner's "Skip reviews when sure" setting, 0 to 100; 0 when off or
    unreadable."""
    try:
        settings = await database.get_user_settings() or {}
        value = int(settings.get("review_auto_accept_percent") or 0)
    except Exception:
        logger.warning("Could not read the review auto-accept setting", exc_info=True)
        return 0
    return max(0, min(100, value))


def result_text(result: Any) -> str:
    """A submitted task's result as the text a judgment reads."""
    if isinstance(result, dict):
        result = result.get("response", result)
    if isinstance(result, str):
        return result
    import json

    return json.dumps(result, default=str)


async def settle(store: Any, claim: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Accept what the claimed run was woken to review, when the owner's
    setting lets a calibrated judgment do it. Returns the settled run's
    receipt (``AgentInstanceService.settle_auto_review``), or None to leave the
    review to the reviewer's model turn."""
    instance = claim.get("instance") or {}
    if instance.get("role") == "lead" or claim.get("status") not in (None, "running") or not claim.get("deliveries"):
        return None
    percent = await auto_accept_percent(store.database)
    if percent <= 0:
        return None
    candidates = await store.review_candidates(instance["id"], claim["run_id"], claim["deliveries"])
    if not candidates:
        return None

    from core.logging import log_context
    from services.judgment import judge

    context = claim.get("context") or {}
    workflow_id = context.get("workflow_id")
    accepted = []
    async with log_context(employee_job_id=context.get("employee_job_id")):
        for candidate in candidates:
            judgment = await judge(
                {"instructions": candidate["instructions"], "result": result_text(candidate["result"])},
                QUESTION,
                node_id=str(context.get("node_id") or instance.get("template_node_id") or "agent_review"),
                workflow_id=str(workflow_id) if workflow_id else None,
                session_id=str(context.get("session_id") or "default"),
                principal=context.get("user_id"),
                calibrated_only=True,
                timeout=JUDGE_TIMEOUT_SECONDS,
            )
            probability = judgment.answers[_NAME].probability if judgment.available else None
            if probability is None or probability * 100 < percent:
                logger.info(
                    "Review left to the reviewer",
                    task_id=candidate["task_id"],
                    reason=judgment.reason or "below the setting",
                    probability=None if probability is None else round(probability, 3),
                )
                return None
            accepted.append({**candidate, "confidence": probability})
    settled = await store.settle_auto_review(claim, accepted)
    logger.info("Review accepted automatically", run_id=claim["run_id"], tasks=len(accepted))
    return settled


__all__ = ["JUDGE_TIMEOUT_SECONDS", "QUESTION", "auto_accept_percent", "result_text", "settle"]
