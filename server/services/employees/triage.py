"""Triage: an app event that the employee's "Wake me when" rule says is not
for it is dropped before any model runs.

A team employee (recipe v3) admits every app, webhook and polling event as a
job, and a job runs the Orchestrator, a specialist, a review and Chat even
when the event needs nothing. When the owner has set a rule, one judgment
(``services/judgment``) decides first: the noul "this event calls for the
employee's work", answered below ``DROP_BELOW``, drops the event. No job is
admitted, no instance starts, no model runs, and the run ends
``event_not_matched`` (``agent.instance.bootstrap``).

The rule is the team plan's ``wake_rule``. It is drafted at hire for an
employee an app event starts, and the owner edits it in the employee's
settings. ``admit_team_plan`` copies the plan into each generation at Start
and Apply, so a generation keeps the rule it was admitted with, and an edit
waits for Apply like any other change to the team. A plan without one (an
older employee, a cleared rule) admits every event, as before.

Every doubt admits the event:
- no rule;
- not the bootstrap of the plan's lead for a fresh event (a review, a
  delegated task, a job already admitted);
- a job already admitted for this run, so a retried prepare never drops
  what it admitted;
- no judgment available (nothing connected, a timeout, an error);
- a judgment at or above ``DROP_BELOW``.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from core.logging import get_logger
from services.employees.hire_request import WAKE_RULE_MAX as RULE_MAX

logger = get_logger(__name__)

#: Below this probability that the event calls for work, it is dropped.
DROP_BELOW = 0.2
#: The judgment's own time limit, well inside the bootstrap activity's
#: (``agent_workflow.PERSIST_TURN_TIMEOUT`` x 2). Past it the event is admitted.
JUDGE_TIMEOUT_SECONDS = 15.0
#: Triggers that start work on a schedule: a scheduled run carries the same
#: mission every time, so a rule about events would say nothing about it.
_SCHEDULE_TRIGGERS = frozenset({"cronScheduler"})
_QUESTION = "wake"


def wake_rule(plan: Optional[Mapping[str, Any]]) -> str:
    """The plan's rule, as one line; "" when it has none."""
    return " ".join(str((plan or {}).get("wake_rule") or "").split())[:RULE_MAX]


def takes_wake_rule(plan: Optional[Mapping[str, Any]], nodes: Sequence[Mapping[str, Any]]) -> bool:
    """A team employee whose jobs an app event starts: a v3 plan naming the
    trigger that admits its jobs, when that trigger is not a schedule."""
    if not plan or plan.get("version") != 3 or not plan.get("intake_trigger_node_id"):
        return False
    trigger = next((node for node in nodes if node.get("id") == plan["intake_trigger_node_id"]), None)
    return trigger is not None and trigger.get("type") not in _SCHEDULE_TRIGGERS


def wake_question(rule: str) -> Dict[str, Dict[str, str]]:
    """The one question triage asks about an event."""
    return {
        _QUESTION: {
            "type": "noul",
            "description": f"This event calls for the employee's work. The employee works on: {rule}",
        }
    }


async def dropped_event(
    database: Any,
    context: Mapping[str, Any],
    plan: Optional[Mapping[str, Any]],
    mission: Any,
    review: Any,
) -> Optional[Dict[str, float]]:
    """``{"probability": p}`` when this event should be dropped, else None.

    Called by the agent's prepare step right before it admits the event as a
    job (``admit_employee_runtime_job``), with the same arguments.
    """
    if not context.get("_agent_instance_bootstrap") or review:
        return None
    if not plan or plan.get("version") != 3 or context.get("node_id") != plan.get("lead_node_id"):
        return None
    if context.get("employee_job_id") or context.get("parent_node_id"):
        return None
    rule = wake_rule(plan)
    text = str(mission or "").strip()
    if not rule or not text:
        return None
    from services.employees.jobs import employee_job_mission

    admitted = await employee_job_mission(
        database,
        workflow_id=str(context.get("workflow_id")),
        lead_node_id=str(plan["lead_node_id"]),
        execution_id=str(context.get("execution_id")),
    )
    if admitted is not None:
        return None

    from services.judgment import judge

    workflow_id = context.get("workflow_id")
    judgment = await judge(
        text,
        wake_question(rule),
        node_id=str(context.get("node_id")),
        workflow_id=str(workflow_id) if workflow_id else None,
        session_id=str(context.get("session_id") or "default"),
        principal=context.get("user_id"),
        timeout=JUDGE_TIMEOUT_SECONDS,
    )
    if not judgment.available:
        logger.info("Event admitted without triage", workflow_id=workflow_id, reason=judgment.reason)
        return None
    probability = judgment.answers[_QUESTION].probability
    if probability is None or probability >= DROP_BELOW:
        return None
    logger.info(
        "Event dropped by the wake rule",
        workflow_id=workflow_id,
        probability=round(probability, 3),
        provider=judgment.provider,
    )
    return {"probability": probability}


__all__ = ["DROP_BELOW", "JUDGE_TIMEOUT_SECONDS", "RULE_MAX", "dropped_event", "takes_wake_rule", "wake_question", "wake_rule"]
