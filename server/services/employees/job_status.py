"""Bounded read-only job status shared by Talk, receipts and Home."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from sqlmodel import select
from models.database import TeamTask
from models.employees import EmployeeJob
from services.employees.failures import FAILURE_MESSAGES, NO_ASSIGNMENTS_CODE


def _job_progress(job: EmployeeJob) -> dict:
    inflight = job.delivery.get("inflight")
    node_types = {node["id"]: node["type"] for node in job.source.get("nodes", [])}
    lease = job.delivery.get("lease_until")
    expired = not lease or datetime.fromisoformat(lease) <= datetime.now(timezone.utc)
    state = "delivery_needs_review" if inflight and expired and node_types.get(inflight) not in {"approvalGate", "chatReply"} else job.state
    # A terminal job answers its own state even with a gate mark left behind.
    if inflight and node_types.get(inflight) == "approvalGate" and job.state not in {"failed", "cancelled"}:
        return {"request_id": job.id, "state": "waiting_for_approval", "message": "Their result is ready for you to check before sending."}
    messages = {"queued": "Your request is saved. The team will start when they are ready.",
                "running": "The team is starting your request.", "working": "The team is working on your request and checking the result.",
                "delivering": "Their checked result is ready to send.", "failed": "The team could not finish this request. Ask them to try again.",
                "cancelled": "This request was stopped.", "delivery_needs_review": "The send was interrupted. Check whether it arrived before trying again.",
                "review_blocked": "The lead needs another review turn. Completed work is saved.",
                "delivered": "The reviewed result was delivered."}
    if job.state == "failed" and (job.delivery.get("failure") or {}).get("code") == NO_ASSIGNMENTS_CODE:
        messages["failed"] = "The lead did not assign work to a teammate, so this request could not finish."
    failure_code = (job.delivery.get("failure") or {}).get("code")
    if job.state == "failed" and failure_code in FAILURE_MESSAGES:
        messages["failed"] = FAILURE_MESSAGES[failure_code]
    return {"request_id": job.id, "state": state, "message": messages.get(state, "The team is checking your request."),
            **({"failure_code": failure_code} if job.state == "failed" and failure_code in {NO_ASSIGNMENTS_CODE, *FAILURE_MESSAGES} else {}),
            **({"revision": job.updated_at.isoformat()} if state == "review_blocked" else {})}


async def get_job_status(database: Any, *, workflow_id: str, lead_node_id: str, job_id: str | None = None) -> dict:
    """Read only the authorized employee's job; never expose mission or results."""
    from sqlalchemy import func
    async with database.get_session() as session:
        query = select(EmployeeJob).where(EmployeeJob.workflow_id == workflow_id, EmployeeJob.lead_node_id == lead_node_id)
        if job_id:
            query = query.where(EmployeeJob.id == job_id)
        job = (await session.execute(query.order_by(EmployeeJob.updated_at.desc(), EmployeeJob.id).limit(1))).scalar_one_or_none()
        if job is None:
            return {"success": True, "operation": "get_job_status", "job": None,
                    "message": "No matching job was found for this employee."}
        counts = (await session.execute(select(TeamTask.status, func.count(TeamTask.id)).where(
            TeamTask.team_id == job.team_id,
            (TeamTask.workflow_id == workflow_id) | TeamTask.workflow_id.is_(None),
        ).group_by(TeamTask.status))).all() if job.team_id else []
        known = {"blocked", "queued", "running", "submitted", "accepted", "failed", "cancelled", "skipped"}
        task_counts = {status: count for status, count in counts if status in known}
        total = sum(count for _, count in counts)
        progress = _job_progress(job)
        if not total and job.state in {"running", "working"}:
            progress["message"] = "The request was admitted, but no teammate task has been assigned yet."
        return {"success": True, "operation": "get_job_status", "job": {
            "job_id": job.id, "state": progress["state"], "message": progress["message"],
            "updated_at": job.updated_at.isoformat(), "task_count": total, "tasks_by_status": task_counts,
            **({"failure_code": progress["failure_code"]} if progress.get("failure_code") else {}),
        }}
