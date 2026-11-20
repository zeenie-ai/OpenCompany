"""Explicit owner recovery of a halted job lead, without repeating worker work."""
from __future__ import annotations

from models.employees import EmployeeJob


async def retry_review(database, *, workflow_id: str, job_id: str, owner_id: str, request_key: str) -> dict:
    if not request_key or len(request_key) > 200:
        return {"success": False, "error": "invalid_request"}
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, job_id)
    if not job or job.workflow_id != workflow_id or (job.source or {}).get("user_id") != owner_id:
        return {"success": False, "error": "not_found"}
    from services.agent_instances.service import AgentInstanceService, InstanceError
    scope = {
        "workflow_id": workflow_id, "job_id": job_id, "team_id": job.team_id,
        "lead_node_id": job.lead_node_id, "generation": (job.source or {}).get("generation", 0),
        "user_id": owner_id, "owner_id": owner_id,
    }
    try:
        result = await AgentInstanceService(database).retry_review(scope=scope, request_id=request_key)
    except InstanceError as exc:
        return {"success": False, "error": exc.code, "message": str(exc)}
    from services.agent_instances.runtime import wake_instances
    await wake_instances(result)
    from services.employees.events import employee_changed_now
    employee_changed_now(workflow_id)
    return {**result, "success": True, "message": "Review resumed. Previously completed work is saved."}
