"""Wave 17.3: Temporal SDK worker interceptors for retry observability.

On a local-PC deployment the worker dies without SIGTERM (laptop sleep,
hard kill, battery). Temporal re-dispatches the interrupted activity on
the next poll — invisibly, unless something logs the attempt number.
These interceptors give the operator log a structured
``activity_retry`` line whenever ``activity.info().attempt > 1`` so
"first attempt" vs "re-dispatch after worker restart" is
distinguishable without opening the Temporal Web UI.

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

from core.logging import get_logger

logger = get_logger(__name__)


class ActivityObservabilityInterceptor(ActivityInboundInterceptor):
    """Log start / retry / end (with outcome) for every activity."""

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()
        extra = {
            "activity_name": info.activity_type,
            "activity_id": info.activity_id,
            "workflow_id": info.workflow_id,
            "attempt": info.attempt,
        }
        if info.attempt > 1:
            # Re-dispatch: prior attempt died (worker crash / laptop
            # sleep / timeout) or failed retryably. WARN so ops can
            # count these without DEBUG noise.
            logger.warning("activity_retry", **extra)
        else:
            logger.debug("activity_start", **extra)
        try:
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
