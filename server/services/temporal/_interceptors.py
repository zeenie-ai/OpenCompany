"""Wave 17.3: Temporal SDK worker interceptors for retry observability.

On a local-PC deployment the worker dies without SIGTERM (laptop sleep,
hard kill, battery). Temporal re-dispatches the interrupted activity on
the next poll — invisibly, unless something logs the attempt number.
These interceptors give the operator log a structured
``activity_retry`` line (WARN at the first retry, at the attempt limit
and every 10th attempt after it; DEBUG otherwise) so "first attempt" vs
"re-dispatch after worker restart" is distinguishable without opening
the Temporal Web UI, and an activity still retrying past its limit shows
on Home as an attention item (services/attention.py).

Registration: both Worker constructions in ``worker.py`` pass
``interceptors=[ObservabilityWorkerInterceptor()]``.

Replay-safety contract (per
https://docs.temporal.io/develop/python/workers/interceptors):
workflow inbound interceptor methods also execute during Event-History
replay. Side effects there would diverge the replay from the recorded
history, so ``WorkflowObservabilityInterceptor`` guards every log call
with ``workflow.unsafe.is_replaying()``. Activity interceptors run only
on live executions — no guard needed.
"""

from __future__ import annotations

from typing import Any, Optional, Type

from temporalio import activity, workflow
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    ExecuteWorkflowInput,
    Interceptor,
    WorkflowInboundInterceptor,
    WorkflowInterceptorClassInput,
)

from core.logging import get_logger, log_context

logger = get_logger(__name__)

#: Retries past these attempts mean an activity is stuck: an LLM call
#: (unlimited retries through an outage) sooner than the rest.
RETRY_LIMITS = {"agent.execute_llm_step": 8, "agent.compact_context": 8}
DEFAULT_RETRY_LIMIT = 10
#: Activities that wait by retrying, or whose attempts end on a timer: their
#: attempts are no sign of trouble (names or prefixes).
PLANNED_WAITS = ("agent.acquire_subagent_permit", "node.approvalGate.", "coding.pi_drive_turn")


def retry_limit(activity_type: str) -> int:
    return RETRY_LIMITS.get(activity_type, DEFAULT_RETRY_LIMIT)


def retry_warns(attempt: int, limit: int) -> bool:
    """A retry is WARN-worthy at the first one, at the limit and every 10th
    attempt after it; DEBUG otherwise."""
    return attempt == 2 or attempt == limit or (attempt > limit and attempt % 10 == 0)


class ActivityObservabilityInterceptor(ActivityInboundInterceptor):
    """Log start / retry / end (with outcome) for every activity.

    Also binds the Temporal identifiers to the log context for the whole
    activity, under their own keys (``workflow_id`` elsewhere is the graph
    id). The LLM gateway's attribution reads them (services.gateway).

    An attempt past its retry limit touches a ``stuck_retry`` attention
    item (one line on Home), and its success resolves it. Planned waits
    never count. The store calls are best effort.
    """

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()
        extra = {
            "activity_name": info.activity_type,
            "activity_id": info.activity_id,
            "workflow_id": info.workflow_id,
            "attempt": info.attempt,
        }
        planned = info.activity_type.startswith(PLANNED_WAITS)
        limit = retry_limit(info.activity_type)
        stuck = not planned and info.attempt > limit
        if info.attempt > 1 and not planned and retry_warns(info.attempt, limit):
            # Re-dispatch: prior attempt died (worker crash / laptop
            # sleep / timeout) or failed retryably. WARN so ops can
            # count these without DEBUG noise.
            logger.warning("activity_retry", limit=limit, **extra)
        else:
            logger.debug("activity_retry" if info.attempt > 1 else "activity_start", **extra)
        if stuck:
            from services import attention

            await attention.touch(attention.STUCK_RETRY, f"{info.workflow_id}:{info.activity_id}")
        bound = {
            "temporal_workflow_id": info.workflow_id,
            "temporal_run_id": info.workflow_run_id,
            "temporal_activity_id": info.activity_id,
            "temporal_attempt": info.attempt,
        }
        try:
            async with log_context(**bound):
                result = await self.next.execute_activity(input)
        except Exception as exc:
            logger.warning(
                "activity_end",
                outcome="failure",
                error=type(exc).__name__,
                **extra,
            )
            raise
        logger.debug("activity_end", outcome="success", **extra)
        if stuck:
            from services import attention

            await attention.resolve(attention.STUCK_RETRY, f"{info.workflow_id}:{info.activity_id}")
        return result


class WorkflowObservabilityInterceptor(WorkflowInboundInterceptor):
    """Log workflow starts — guarded so replays stay deterministic."""

    async def execute_workflow(self, input: ExecuteWorkflowInput) -> Any:
        if not workflow.unsafe.is_replaying():
            wf_info = workflow.info()
            logger.info(
                "workflow_start",
                workflow_type=wf_info.workflow_type,
                workflow_id=wf_info.workflow_id,
                run_id=wf_info.run_id,
                attempt=wf_info.attempt,
            )
        return await self.next.execute_workflow(input)


class ObservabilityWorkerInterceptor(Interceptor):
    """Worker-level wiring: one instance shared by all Workers."""

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return ActivityObservabilityInterceptor(next)

    def workflow_interceptor_class(self, input: WorkflowInterceptorClassInput) -> Optional[Type[WorkflowInboundInterceptor]]:
        return WorkflowObservabilityInterceptor
