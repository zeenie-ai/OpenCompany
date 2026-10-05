"""Admission requirements for employees with an explicit durable team plan."""
from __future__ import annotations
from typing import Any, Optional


def team_runtime_error(*, settings: Any = None, temporal_client: Any = None, worker_manager: Any = None) -> Optional[str]:
    if settings is None or temporal_client is None:
        from core.container import container
        settings = settings if settings is not None else container.settings()
        temporal_client = temporal_client if temporal_client is not None else container.temporal_client()
    if not getattr(settings, "temporal_enabled", False):
        return "team_temporal_required"
    if not getattr(settings, "temporal_agent_workflow_enabled", False):
        return "team_agent_workflow_required"
    if not getattr(temporal_client, "is_connected", False):
        return "team_runtime_not_ready"
    if worker_manager is None and hasattr(temporal_client, "worker_manager"):
        worker_manager = temporal_client.worker_manager
        if worker_manager is None:
            return "team_runtime_not_ready"
    if worker_manager is not None and not getattr(worker_manager, "is_running", False):
        return "team_runtime_not_ready"
    return None


async def employee_job_mission(database: Any, context: dict) -> Optional[str]:
    """Read an admitted lead's mission from its durable job, never user params.

    Child agents inherit job identity for traceability but keep their own
    invocation. Task-trigger review runs likewise retain their review prompt.
    """
    job_id = context.get("employee_job_id")
    if not job_id:
        return None
    from models.employees import EmployeeJob
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, str(job_id))
    if job is None or job.workflow_id != context.get("workflow_id"):
        raise ValueError("The employee job does not belong to this workflow")
    if job.lead_node_id != context.get("node_id"):
        return None
    if job.origin_execution_id != context.get("execution_id"):
        return None
    return job.mission


async def employee_job_parameters(database: Any, context: dict) -> dict:
    if not context.get("employee_job_id"):
        return context.get("parameter_snapshot") or {}
    from models.employees import EmployeeJob
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, str(context["employee_job_id"]))
    if job is None or job.workflow_id != context.get("workflow_id"):
        raise ValueError("The employee job does not belong to this workflow")
    return dict(job.source.get("parameters") or {})
def validate_managed_assignment(args, scope):
    """Require reviewable assignments only in server-admitted employee jobs."""
    if not scope.get("employee_job_id"):
        return
    if not isinstance(args.get("mission", args.get("task")), str) or not args.get("mission", args.get("task", "")).strip():
        raise ValueError("Employee job assignment requires a nonempty mission")
    if not isinstance(args.get("context"), dict):
        raise ValueError("Employee job assignment requires context as an object")
    if not isinstance(args.get("acceptance_criteria"), dict) or not args["acceptance_criteria"]:
        raise ValueError("Employee job assignment requires nonempty acceptance_criteria; specify how submitted work will be reviewed")
    dependencies = args.get("depends_on", [])
    if dependencies is None:
        dependencies = []
    if not isinstance(dependencies, list) or any(not isinstance(item, str) or not item.strip() for item in dependencies):
        raise ValueError("Employee job assignment depends_on must be a list of resolved task IDs")
