"""Stable admission and reviewed delivery for owner-submitted team jobs."""
from datetime import timedelta
import asyncio
from typing import Any
from temporalio import workflow, activity
from temporalio.common import RetryPolicy
from temporalio.exceptions import is_cancelled_exception


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
        if job and job.state not in {"delivered", "delivering"}:
            job.state = "cancelled" if context.get("cancelled") else "failed"
            await session.commit()
    return {"saved": True}


@workflow.defn(name="EmployeeJobWorkflow", sandboxed=False)
class EmployeeJobWorkflow:
    @workflow.run
    async def run(self, context: dict[str, Any]) -> dict:
        try:
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
