"""Owner-reviewed reconciliation of uncertain external delivery.

An expired send intent is never automatically resent. The owner confirms its
outcome, and a durable request continues the original reviewed job using its
saved approval gates, recipients and delivery identities.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlmodel import select
from models.employees import EmployeeJob


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired(value: str | None) -> bool:
    if not value:
        return True
    try:
        lease = datetime.fromisoformat(value)
        return lease.replace(tzinfo=timezone.utc) <= _now() if lease.tzinfo is None else lease <= _now()
    except ValueError:
        return False


async def resolve_delivery(database: Any, *, workflow_id: str, job_id: str, owner_id: str,
                           decision: str, request_key: str) -> dict:
    if decision not in {"arrived", "retry"} or not request_key or len(request_key) > 200:
        return {"success": False, "error": "invalid_request"}
    identifier = "employee-delivery:" + hashlib.sha256(json.dumps([workflow_id, job_id, request_key]).encode()).hexdigest()
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, job_id)
    if not job or job.workflow_id != workflow_id or job.source.get("user_id") != owner_id:
        return {"success": False, "error": "not_found"}
    tasks = await database.get_team_tasks(job.team_id) if job.team_id else []
    async with database.reserved_session() as session:
        current = await session.get(EmployeeJob, job_id)
        if not current or current.workflow_id != workflow_id or current.source.get("user_id") != owner_id:
            return {"success": False, "error": "not_found"}
        delivery = dict(current.delivery)
        resolutions = dict(delivery.get("resolutions") or {})
        earlier = resolutions.get(identifier)
        if earlier:
            if earlier["decision"] != decision:
                return {"success": False, "error": "request_conflict"}
            return {"success": True, "request_id": identifier, "state": earlier["state"], "message": "Your delivery decision is saved."}
        if not tasks or any(task.get("status") != "accepted" for task in tasks) or current.team_id != job.team_id or not current.result or current.result.strip() == "NO_REPLY":
            return {"success": False, "error": "result_not_reviewed", "message": "The team must finish checking this result before it can be sent."}
        if any(item.get("state") in {"queued", "started"} for item in resolutions.values()):
            return {"success": False, "error": "busy", "message": "Your previous delivery decision is still being applied."}
        inflight = delivery.get("inflight")
        types = {node["id"]: node["type"] for node in current.source.get("nodes", [])}
        if not inflight or inflight not in delivery.get("node_ids", []) or types.get(inflight) in {None, "approvalGate", "chatReply"} or not _expired(delivery.get("lease_until")):
            return {"success": False, "error": "not_waiting_for_review", "message": "This delivery is already being handled. Check its progress."}
        completed = list(delivery.get("completed") or [])
        if decision == "arrived" and inflight not in completed:
            completed.append(inflight)
        complete = set(delivery.get("node_ids") or []) <= set(completed)
        resolutions[identifier] = {"decision": decision, "sink": inflight, "state": "complete" if complete else "queued", "owner_id": owner_id,
                                   "created_at": _now().isoformat()}
        if complete:
            resolutions[identifier]["result"] = {"delivered": True, "state": "delivered", "job_id": job_id}
        current.delivery = {**delivery, "completed": completed, "inflight": None, "claim_token": None,
                            "lease_until": None, "resolutions": resolutions}
        current.state = "delivered" if complete else "delivery_queued"
        current.updated_at = _now()
        await session.commit()
    from services.employees.events import employee_changed_now
    employee_changed_now(workflow_id)
    return {"success": True, "request_id": identifier, "state": "complete" if complete else "queued",
            "message": "Delivery confirmed." if complete else "Your decision is saved. The team will finish delivering their checked result."}


async def dispatch_resolution(database: Any, job_id: str, resolution_id: str) -> bool:
    from core.container import container
    from services.employees.team_runtime import team_runtime_error
    if team_runtime_error():
        return False
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, job_id)
    record = (job.delivery.get("resolutions") or {}).get(resolution_id) if job else None
    if not record or record.get("state") != "queued":
        return False
    from temporalio.common import WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError
    from services.temporal.trigger_listener_workflow import event_workflow_search_attributes
    try:
        await container.temporal_client().client.start_workflow("EmployeeJobDeliveryWorkflow",
            {"job_id": job_id, "resolution_id": resolution_id}, id=resolution_id,
            task_queue=container.settings().temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            search_attributes=event_workflow_search_attributes(job.workflow_id))
    except WorkflowAlreadyStartedError:
        pass
    async with database.reserved_session() as session:
        current = await session.get(EmployeeJob, job_id)
        records = dict(current.delivery.get("resolutions") or {})
        record = records.get(resolution_id)
        if record and record.get("state") == "queued":
            records[resolution_id] = {**record, "state": "started"}
            current.delivery = {**current.delivery, "resolutions": records}
            await session.commit()
    return True


async def recover_delivery_resolutions(database: Any) -> None:
    async with database.get_session() as session:
        jobs = (await session.execute(select(EmployeeJob).where(EmployeeJob.state == "delivery_queued"))).scalars().all()
    for job in jobs:
        for identifier, record in (job.delivery.get("resolutions") or {}).items():
            if record.get("state") == "queued":
                await dispatch_resolution(database, job.id, identifier)


async def continue_delivery(database: Any, job_id: str, resolution_id: str) -> dict:
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, job_id)
    resolution = (job.delivery.get("resolutions") or {}).get(resolution_id) if job else None
    if not resolution:
        return {"delivered": False, "state": "not_found"}
    if resolution.get("state") == "complete":
        return resolution.get("result") or {"delivered": job.state == "delivered", "state": job.state}
    from services.temporal.employee_job_workflow import deliver_employee_job
    # Only the stored reviewed answer is reconstructed; the owner request
    # cannot supply a response, a recipient, a graph, or approval parameters.
    source = job.source
    context = {"node_id": job.lead_node_id, "workflow_id": job.workflow_id, "execution_id": job.origin_execution_id,
        "user_id": source["user_id"], "session_id": source["session_id"], "employee_job_id": job.id,
        "team_id": job.team_id, "outputs": {**source.get("outputs", {}), job.lead_node_id:
            {"response": job.result, "team_id": job.team_id, "execution_id": job.origin_execution_id}}}
    try:
        result = await deliver_employee_job(context)
    except Exception:
        # A failed external activity may already have sent. Its durable
        # inflight intent is retained for another explicit owner decision.
        result = {"delivered": False, "state": "delivery_needs_review", "job_id": job_id}
    async with database.reserved_session() as session:
        current = await session.get(EmployeeJob, job_id)
        records = dict(current.delivery.get("resolutions") or {})
        records[resolution_id] = {**records[resolution_id], "state": "complete", "result": result}
        current.delivery = {**current.delivery, "resolutions": records}
        if result.get("delivered"):
            current.state = "delivered"
            current.delivery = {**current.delivery, "claim_token": None, "lease_until": None}
        elif result.get("state") == "delivery_needs_review":
            current.state = "delivering"
        current.updated_at = _now()
        await session.commit()
    from services.employees.events import employee_changed_now
    employee_changed_now(job.workflow_id)
    return result
