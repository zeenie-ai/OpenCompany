"""Durable single-node execution with the complete saved graph as context."""

from __future__ import annotations
import asyncio
from datetime import timedelta
from typing import Any
from temporalio import workflow
from temporalio.common import RetryPolicy


# The parent Temporal package registers services on import. Match the other
# framework workflows: orchestration remains deterministic, without re-importing
# that package and its logging/DI dependencies inside the workflow sandbox.
@workflow.defn(sandboxed=False)
class NodeInvocationWorkflow:
    def __init__(self) -> None:
        self.metadata: dict[str, Any] = {}
        self.status = "queued"

    @workflow.query
    def describe(self) -> dict:
        return {**self.metadata, "status": self.status}

    @workflow.run
    async def run(self, payload: dict) -> dict:
        self.metadata = {k: payload[k] for k in ("principal", "workflow_id", "node_id", "fingerprint")}
        self.status = "running"
        result: dict = {}
        try:
            if payload.get("history_version") == 1:
                await self._record_status(payload)
            if payload.get("dispatch_version") == 1 and payload.get("dispatch_kind") == "native_agent":
                result = await workflow.execute_child_workflow(
                    "AgentWorkflow", payload["context"],
                    id=f"{workflow.info().workflow_id}:agent",
                    cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
                    parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
                )
            else:
                result = await workflow.execute_activity(
                    payload["activity"],
                    payload["context"],
                    task_queue=payload["task_queue"],
                    start_to_close_timeout=timedelta(seconds=payload["timeout_s"]),
                    heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=1),
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
            self.status = "completed" if result.get("success") else "failed"
            return result
        except asyncio.CancelledError:
            self.status = "cancelled"
            raise
        except Exception as exc:
            if payload.get("dispatch_version") == 1:
                from temporalio.exceptions import is_cancelled_exception
                if is_cancelled_exception(exc):
                    self.status = "cancelled"
                    raise asyncio.CancelledError() from exc
                cause = exc
                for _ in range(8):
                    if getattr(cause, "cause", None) is None:
                        break
                    cause = cause.cause
                error_type = "BrowserBusy" if str(getattr(cause, "message", "")).startswith("BrowserBusy") else "BrowserTaskFailed"
                self.metadata["error_type"] = error_type
                self.metadata["detail"] = ("This browser is assigned to another task. Wait for that task to finish."
                                           if error_type == "BrowserBusy" else "The browser task failed. Check browser status and credentials.")
                result = {"error_type": error_type}
            self.status = "failed"
            raise
        finally:

            if payload.get("history_version") == 1:
                # Cancellation waits for the attached child and its owner cleanup
                # before this terminal projection is persisted.
                try:
                    await asyncio.shield(asyncio.create_task(self._record_status(payload, result)))
                except Exception as exc:
                    workflow.logger.warning("Workspace task projection failed: %s", type(exc).__name__)

            async def record() -> None:
                await workflow.execute_activity(
                    "workflow_runs.record_completion",
                    {
                        "workflow_id": payload["workflow_id"],
                        "run_id": f"{workflow.info().workflow_id}:{workflow.info().run_id}",
                        "generation": payload["context"].get("generation", 0),
                        "status": "success" if self.status == "completed" else "failed",
                    },
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                )

            try:
                await asyncio.shield(asyncio.create_task(record()))
            except Exception as exc:
                # A summary outage must not convert a completed device task into
                # a failure that a user may replay. Temporal remains authoritative.
                workflow.logger.warning("Workspace run summary failed: %s", type(exc).__name__)

    async def _record_status(self, payload: dict, result: dict | None = None) -> None:
        await workflow.execute_activity(
            "workspace_tasks.update_record",
            {"invocation_id": workflow.info().workflow_id, "status": self.status, "result": result},
            start_to_close_timeout=timedelta(seconds=30),
            # The UI reads this projection. Keep an idempotent write pending
            # through a database outage rather than leave a task permanently
            # running after its authoritative Temporal execution has ended.
            retry_policy=RetryPolicy(maximum_attempts=0),
        )
