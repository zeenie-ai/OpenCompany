"""Stable admission and reviewed delivery for owner-submitted team jobs."""
from datetime import timedelta
import asyncio
from typing import Any
from temporalio import workflow, activity
from temporalio.common import RetryPolicy
from temporalio.exceptions import is_cancelled_exception
from services.temporal.execution_control import ExecutionControl


@activity.defn(name="employee.job.deliver")
async def deliver_employee_job(context: dict[str, Any]) -> dict:
    from core.container import container
    from services.employees.jobs import deliver_job
    from services.plugin import NodeContext
    ctx = NodeContext.from_legacy(context["node_id"], "employeeJob", context)
    task = asyncio.create_task(deliver_job(container.database(), ctx, lead_node_id=context["node_id"]))
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=30)
            if not done:
                activity.heartbeat("Waiting for reviewed delivery")
        return await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@activity.defn(name="employee.job.failed")
async def fail_employee_job(context: dict[str, Any]) -> dict:
    from core.container import container
    from models.employees import EmployeeJob
    async with container.database().reserved_session() as session:
        job = await session.get(EmployeeJob, str(context.get("employee_job_id")))
        if job is None and context.get("runtime_admission"):
            from sqlmodel import select
            found = await session.execute(select(EmployeeJob).where(EmployeeJob.workflow_id == context.get("workflow_id"),
                EmployeeJob.lead_node_id == context.get("node_id"), EmployeeJob.origin_execution_id == context.get("execution_id")))
            job = found.scalar_one_or_none()
        if job and job.state not in {"delivered", "delivering"}:
            job.state = "cancelled" if context.get("cancelled") else "failed"
            await session.commit()
    return {"saved": True}


@workflow.defn(name="EmployeeJobWorkflow", sandboxed=False)
class EmployeeJobWorkflow:
    @workflow.init
    def __init__(self, context: dict[str, Any] | None = None):
        self._execution_control = ExecutionControl()
        if context is not None:
            self._execution_control.bind(context)

    @workflow.update
    async def set_control_state(self, payload: dict) -> dict:
        return await self._execution_control.set_control_state(payload)

    @set_control_state.validator
    def validate_set_control_state(self, payload: dict) -> None:
        self._execution_control.validate_control(payload)

    @workflow.update
    async def wait_for_checkpoint(self, payload: dict) -> dict:
        return await self._execution_control.wait_for_checkpoint(payload)

    @wait_for_checkpoint.validator
    def validate_wait_for_checkpoint(self, payload: dict) -> None:
        self._execution_control.validate_control(payload)

    @workflow.query
    def execution_control_status(self) -> dict:
        return self._execution_control.status()

    @workflow.run
    async def run(self, context: dict[str, Any] | None = None) -> dict:
        context = context or {}
        self._execution_control.bind(context)
        if not self._execution_control.enabled:
            return await self._run_job(context)
        await self._execution_control.register_root()
        try:
            return await self._run_job(context)
        finally:
            await self._execution_control.unregister_root()
            await workflow.wait_condition(workflow.all_handlers_finished)

    async def _run_job(self, context: dict[str, Any]) -> dict:
        try:
            if self._execution_control.enabled:
                async with self._execution_control.child_start():
                    handle = await workflow.start_child_workflow("AgentWorkflow", context, id=workflow.info().workflow_id + ":lead")
                result = await handle
            else:
                result = await workflow.execute_child_workflow("AgentWorkflow", context, id=workflow.info().workflow_id + ":lead")
        except BaseException as exc:
            # Cancellation must leave a durable terminal job record too.
            # Shield this short cleanup from the parent's cancellation.
            await asyncio.shield(workflow.execute_activity("employee.job.failed", {**context, "cancelled": isinstance(exc, asyncio.CancelledError) or is_cancelled_exception(exc)},
                start_to_close_timeout=timedelta(seconds=60), retry_policy=RetryPolicy(maximum_attempts=3)))
            raise
        if not result.get("success"):
            await workflow.execute_activity("employee.job.failed", context,
                start_to_close_timeout=timedelta(seconds=60), retry_policy=RetryPolicy(maximum_attempts=3))
            return result
        delivery = {**context, "outputs": {**context.get("outputs", {}), context["node_id"]: result.get("result", {})}}
        if self._execution_control.enabled:
            async with self._execution_control.action():
                return await workflow.execute_activity("employee.job.deliver", delivery,
                    start_to_close_timeout=timedelta(days=31), heartbeat_timeout=timedelta(minutes=2),
                    retry_policy=RetryPolicy(maximum_attempts=3))
        return await workflow.execute_activity("employee.job.deliver", delivery,
            start_to_close_timeout=timedelta(days=31), heartbeat_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3))


@activity.defn(name="employee.job.resolve_delivery")
async def resolve_employee_job_delivery(request: dict[str, Any]) -> dict:
    from core.container import container
    from services.employees.delivery_resolution import continue_delivery
    return await continue_delivery(container.database(), request["job_id"], request["resolution_id"])


@workflow.defn(name="EmployeeJobDeliveryWorkflow", sandboxed=False)
class EmployeeJobDeliveryWorkflow:
    """Stable continuation after the owner reviews an uncertain send."""

    @workflow.run
    async def run(self, request: dict[str, Any]) -> dict:
        return await workflow.execute_activity("employee.job.resolve_delivery", request,
            start_to_close_timeout=timedelta(days=31), heartbeat_timeout=timedelta(minutes=2),
            retry_policy=RetryPolicy(maximum_attempts=3))
