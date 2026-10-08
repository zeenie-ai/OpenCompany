"""Durable admission and cooperative Reset for one workflow's Workspace tasks.

Child-start acknowledgement is part of admission. Reset fences new submissions
before waiting for that acknowledgement, then waits for every admitted child to
close. No process-local registry or visibility query is the source of truth.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError

from services.temporal.node_invocation import NodeInvocationWorkflow

MAX_ACTIVE_TASKS = 32
MAX_REMEMBERED_SUBMISSIONS = 128
MAX_REMEMBERED_RESETS = 128
_OPERATIONS_PER_RUN = 256
_HISTORY_SOFT_CAP = 10_000


def _reject(message: str, error_type: str) -> None:
    raise ApplicationError(message, type=error_type, non_retryable=True)


def _bounded_remember(records: dict, key: str, value: Any, limit: int) -> None:
    records[key] = value
    while len(records) > limit:
        del records[next(iter(records))]


@workflow.defn(sandboxed=False)
class WorkspaceTaskControllerWorkflow:
    @workflow.init
    def __init__(self, payload: dict) -> None:
        # Update-With-Start can invoke a handler before run(), so its input
        # must already be available here.
        self._workflow_id = str(payload["workflow_id"])
        self._epoch = int(payload.get("epoch", 0))
        self._submissions: dict[str, str] = dict(list((payload.get("submissions") or {}).items())[-MAX_REMEMBERED_SUBMISSIONS:])
        self._resets: dict[str, dict] = dict(list((payload.get("resets") or {}).items())[-MAX_REMEMBERED_RESETS:])
        self._active: dict[str, tuple[str, Any]] = {}
        self._starting: str | None = None
        self._resetting: str | None = None
        self._reset_running = False
        self._reset_cancelled: set[str] = set()
        self._admission_lock = asyncio.Lock()
        self._operations = 0

    @workflow.query
    def describe(self) -> dict:
        return {
            "epoch": self._epoch,
            "resetting": self._resetting is not None,
            "reset_request_id": self._resetting,
            "last_reset_request_id": next(reversed(self._resets), None),
            "active_count": len(self._active) + int(self._starting is not None),
        }

    def _check_admission(self, payload: dict) -> tuple[str, str]:
        if self._resetting is not None:
            _reject("Workspace tasks are resetting", "WorkspaceResetInProgress")
        epoch = payload.get("admission_epoch", 0)
        if isinstance(epoch, bool) or not isinstance(epoch, int) or epoch != self._epoch:
            _reject("Workspace admission changed; submit a fresh task", "WorkspaceAdmissionChanged")
        context = payload.get("context") or {}
        if payload.get("workflow_id") != self._workflow_id or context.get("workflow_id") != self._workflow_id:
            _reject("Workspace task belongs to a different workflow", "WorkspaceScopeMismatch")
        run_id = context.get("execution_id")
        fingerprint = payload.get("fingerprint")
        if not isinstance(run_id, str) or not run_id or len(run_id) > 256:
            _reject("Workspace task needs a bounded execution ID", "WorkspaceInvalidSubmission")
        if not isinstance(fingerprint, str) or not fingerprint or len(fingerprint) > 128:
            _reject("Workspace task needs a bounded fingerprint", "WorkspaceInvalidSubmission")
        return run_id, fingerprint

    @workflow.update
    async def submit(self, payload: dict) -> dict:
        self._operations += 1
        self._check_admission(payload)
        async with self._admission_lock:
            # Reset can begin while this handler waits behind another start.
            run_id, fingerprint = self._check_admission(payload)
            known = self._active.get(run_id)
            known_fingerprint = known[0] if known else self._submissions.get(run_id)
            if known_fingerprint is not None:
                if known_fingerprint != fingerprint:
                    _reject("Submission ID was already used for a different task", "WorkspaceSubmissionConflict")
                return {"run_id": run_id, "status": "accepted"}
            if len(self._active) >= MAX_ACTIVE_TASKS:
                _reject("The Workspace task queue is full", "WorkspaceTaskQueueFull")

            self._starting = run_id
            try:
                # Only new server-versioned submissions produce this command;
                # histories recorded before the projection retain their commands.
                if payload.get("history_version") == 1:
                    admitted_record = await workflow.execute_activity(
                        "workspace_tasks.admit_record", payload,
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(maximum_attempts=3),
                    )
                    if isinstance(admitted_record, dict) and admitted_record.get("status") in ("completed", "failed", "cancelled"):
                        # The projection can outlive Temporal retention and
                        # this controller's bounded dedup window. A terminal
                        # stable submission must never produce new effects.
                        _bounded_remember(self._submissions, run_id, fingerprint, MAX_REMEMBERED_SUBMISSIONS)
                        return {"run_id": run_id, "status": "duplicate"}
                child = await workflow.start_child_workflow(
                    NodeInvocationWorkflow.run,
                    payload,
                    id=run_id,
                    id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                    cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
                    parent_close_policy=workflow.ParentClosePolicy.REQUEST_CANCEL,
                )
            except WorkflowAlreadyStartedError:
                # This can be a pre-controller root, or an evicted completed
                # record. Only the service can query that execution to verify
                # its fingerprint; never trust/cache the newly supplied one.
                return {"run_id": run_id, "status": "duplicate"}
            finally:
                self._starting = None
            self._active[run_id] = (fingerprint, child)
            child.add_done_callback(lambda completed: self._child_closed(run_id, fingerprint, completed))
            return {"run_id": run_id, "status": "accepted"}

    def _child_closed(self, run_id: str, fingerprint: str, child: Any) -> None:
        # Consume failures even when the caller only waited for admission.
        # Completion, failure, and cancellation all release the same slot.
        try:
            child.exception()
        except asyncio.CancelledError:
            pass
        if self._active.get(run_id, (None, None))[1] is child:
            del self._active[run_id]
            _bounded_remember(self._submissions, run_id, fingerprint, MAX_REMEMBERED_SUBMISSIONS)

    @workflow.update
    async def reset(self, request_id: str) -> dict:
        self._operations += 1
        if not isinstance(request_id, str) or not request_id or len(request_id) > 256:
            _reject("Reset needs a bounded request ID", "WorkspaceInvalidReset")
        while True:
            if request_id in self._resets:
                return dict(self._resets[request_id])
            if self._resetting is not None and self._resetting != request_id:
                _reject("Workspace tasks are already resetting", "WorkspaceResetInProgress")
            if not self._reset_running:
                break
            await workflow.wait_condition(lambda: not self._reset_running)

        if self._resetting is None:
            # Fence late network requests immediately, including while a previous
            # submission is awaiting ChildWorkflowExecutionStarted under the lock.
            self._resetting = request_id
            self._epoch += 1
            self._reset_cancelled.clear()
        # A failed cleanup leaves the fence latched. The same request can retry
        # without advancing the epoch or forgetting the children it cancelled.
        self._reset_running = True
        try:
            async with self._admission_lock:
                children = [(run_id, child) for run_id, (_, child) in self._active.items() if not child.done()]
                for run_id, child in children:
                    self._reset_cancelled.add(run_id)
                    child.cancel()
                # WAIT_CANCELLATION_COMPLETED on each handle means this waits
                # for activity cleanup and child close, not just cancel ack.
                await asyncio.gather(*(child for _, child in children), return_exceptions=True)
                # Local plugin runtimes may predate this controller, including
                # work in a phone-only graph with no deployment control row.
                runtime_result = await workflow.execute_activity(
                    "workspace_tasks.reset_runtime",
                    {"workflow_id": self._workflow_id},
                    start_to_close_timeout=timedelta(seconds=120),
                    heartbeat_timeout=timedelta(seconds=30),
                    retry_policy=RetryPolicy(maximum_attempts=3),
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                result = {"cancelled": len(self._reset_cancelled)}
                if isinstance(runtime_result, dict) and "reset_nodes" in runtime_result:
                    result["reset_nodes"] = runtime_result["reset_nodes"]
                _bounded_remember(self._resets, request_id, result, MAX_REMEMBERED_RESETS)
                self._resetting = None
                return dict(result)
        finally:
            self._reset_running = False

    def _ready_to_roll_over(self) -> bool:
        if self._active or self._starting or self._resetting or self._admission_lock.locked():
            return False
        if not workflow.all_handlers_finished():
            return False
        info = workflow.info()
        return (
            self._operations >= _OPERATIONS_PER_RUN
            or info.is_continue_as_new_suggested()
            or info.get_current_history_length() >= _HISTORY_SOFT_CAP
        )

    @workflow.run
    async def run(self, payload: dict) -> None:
        await workflow.wait_condition(self._ready_to_roll_over)
        workflow.continue_as_new({
            "workflow_id": self._workflow_id,
            "epoch": self._epoch,
            "submissions": self._submissions,
            "resets": self._resets,
        })
