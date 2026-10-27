"""Durable employee jobs: origin and recipients survive asynchronous review.

Only this boundary publishes team results. Task submissions and assignment
receipts are deliberately ineligible for delivery.
"""
from __future__ import annotations

import hashlib
import json
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import select
from models.employees import EmployeeJob
from models.database import AgentTeam, TeamTask


def _now():
    return datetime.now(timezone.utc)


NO_ASSIGNMENTS_CODE = "lead_no_assignments"
NO_ASSIGNMENTS_MESSAGE = "The lead completed without assigning work to a teammate."


def no_assignments_failed(job: EmployeeJob) -> bool:
    return job.state == "failed" and (job.delivery.get("failure") or {}).get("code") == NO_ASSIGNMENTS_CODE


def protected_job_outcome(job: EmployeeJob) -> bool:
    """A finalizer must never replace an approval or a possible external send."""
    return job.state in {"cancelled", "delivered", "delivering", "delivery_queued", "delivery_needs_review"} or bool(
        job.delivery.get("inflight") or job.delivery.get("completed") or job.delivery.get("resolution_pending")
    )


async def job_has_approval(session: Any, job: EmployeeJob) -> bool:
    from models.approvals import ApprovalRequest
    return (await session.execute(select(ApprovalRequest.id).where(
        ApprovalRequest.workflow_id == job.workflow_id, ApprovalRequest.execution_id == job.id,
    ).limit(1))).first() is not None


async def managed_job_for_team(session: Any, team_id: str, payload: dict | None = None) -> EmployeeJob | None:
    """Select only the job bound to this exact durable team, never latest."""
    payload = payload or {}
    job = (await session.execute(select(EmployeeJob).where(EmployeeJob.team_id == team_id))).scalar_one_or_none()
    if job and any(payload.get(key) and str(payload[key]) != str(value) for key, value in (
        ("employee_job_id", job.id), ("workflow_id", job.workflow_id), ("node_id", job.lead_node_id),
    )):
        raise ValueError("The employee job does not belong to this lead and team")
    return job


async def check_lead_assignments(database: Any, payload: dict) -> dict:
    """Fail closed on database errors; queued assignments already count as progress."""
    team_id = str(payload["team_id"])
    async with database.reserved_session() as session:
        job = await managed_job_for_team(session, team_id, payload)
        if not job:
            return {"team_id": team_id, "managed": False}
        result = {"team_id": team_id, "managed": True, "job_id": job.id, "status": "active", "state": job.state}
        if no_assignments_failed(job) or protected_job_outcome(job) or await job_has_approval(session, job):
            return {**result, "protected": True, "status": "failed" if no_assignments_failed(job) else "active"}
        assigned = (await session.execute(select(TeamTask.id).where(TeamTask.team_id == team_id).limit(1))).first()
        if not assigned:
            team = await session.get(AgentTeam, team_id)
            if team and team.status == "completed":
                team.status, team.completed_at = "active", None
                await session.commit()
            count = max(int(payload.get("correction_count") or 0), int((job.delivery.get("failure") or {}).get("correction_count") or 0))
            return {**result, "requires_delegation": True, "correction_count": count}
        return result


async def fail_no_assignments(database: Any, payload: dict) -> dict:
    """Serialize failure with task insertion and preserve every prior send intent."""
    changed = False
    async with database.reserved_session() as session:
        job = await session.get(EmployeeJob, str(payload.get("employee_job_id") or ""))
        if not job and payload.get("team_id"):
            job = await managed_job_for_team(session, str(payload["team_id"]), payload)
        if not job:
            return {"saved": False, "reason": "job_not_found"}
        if any(payload.get(key) and str(payload[key]) != str(value) for key, value in (
            ("workflow_id", job.workflow_id), ("node_id", job.lead_node_id), ("team_id", job.team_id),
        )):
            raise ValueError("The employee job does not belong to this lead and team")
        if no_assignments_failed(job) or protected_job_outcome(job) or await job_has_approval(session, job):
            return {"saved": False, "job_id": job.id, "state": job.state, "reason": "protected_outcome"}
        assigned = (await session.execute(select(TeamTask.id).where(TeamTask.team_id == job.team_id).limit(1))).first() if job.team_id else None
        if assigned:
            return {"saved": False, "job_id": job.id, "state": job.state, "reason": "assignments_exist"}
        job.state = "failed"
        job.delivery = {**job.delivery, "failure": {"code": NO_ASSIGNMENTS_CODE, "message": NO_ASSIGNMENTS_MESSAGE,
            "correction_count": int(payload.get("correction_count") or 0)}}
        job.updated_at = _now()
        team = await session.get(AgentTeam, job.team_id) if job.team_id else None
        if team:
            team.status, team.completed_at = "failed", _now()
        await session.commit()
        changed = True
    if changed:
        from services.employees.events import employee_changed_now
        employee_changed_now(job.workflow_id)
    return {"saved": changed, "job_id": job.id, "state": job.state}


async def employee_job_mission(database: Any, *, workflow_id: str, lead_node_id: str, execution_id: str) -> str | None:
    async with database.get_session() as session:
        found = await session.execute(select(EmployeeJob).where(EmployeeJob.workflow_id == workflow_id,
            EmployeeJob.lead_node_id == lead_node_id, EmployeeJob.origin_execution_id == execution_id))
        row = found.scalar_one_or_none()
        return row.mission if row else None


def _identity(ctx: Any, mission: str) -> str:
    # Tool retries have a stable call id; intake retries have an activity id.
    token = ctx.raw.get("tool_call_id") or ctx.raw.get("mutation_id") or ctx.execution_id
    if not token:
        raise ValueError("A job requires a durable originating request")
    body = [ctx.workflow_id, ctx.node_id, token, mission]
    return "employee-job:" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


async def create_job(database: Any, ctx: Any, *, mission: str, lead_node_id: str, delivery_node_ids: list[str], dispatch: bool) -> EmployeeJob:
    from services.employees.store import get_by_workflow
    employee = await get_by_workflow(database, str(ctx.workflow_id))
    from services.employees.team_runtime import admitted_graph, team_plan_in
    admitted = await admitted_graph(database, ctx.raw)
    captured_plan = team_plan_in(admitted, lead_node_id) if admitted else None
    plan = captured_plan or (employee.team_plan if employee else None)
    if not employee or not plan or plan.get("lead_node_id") != lead_node_id:
        raise ValueError("This employee does not have an approved team")
    graph = await database.get_workflow(str(ctx.workflow_id))
    allowed_destinations = set(plan.get("delivery_node_ids", []) + plan.get("talk_delivery_node_ids", [])) if captured_plan else set((employee.node_roles or {}).values())
    if not delivery_node_ids or any(node not in allowed_destinations for node in delivery_node_ids):
        raise ValueError("The job destination is not approved")
    email_input = ctx.raw.get("email_input")
    original_mission = email_input.get("original_mission", mission) if isinstance(email_input, dict) else mission
    if ctx.node_type == "employeeJob":
        original_mission = original_mission.strip()
    if len(mission) > 20000:
        from services.employees.email_input import EmployeeEmailInputLimit
        raise EmployeeEmailInputLimit()
    identifier = _identity(ctx, original_mission)
    # Persist the original graph/parameters rather than consulting mutable
    # recipients or templates when the review completes later. A team job
    # keeps its generation's whole admitted graph: the run that admits it
    # reaches neither the delivery nodes nor, from Talk, the lead.
    if captured_plan:
        nodes, edges, snapshot = admitted["nodes"], admitted["edges"], admitted.get("parameters") or {}
    else:
        nodes = ctx.nodes or (graph.data or {}).get("nodes", [])
        edges = ctx.edges or (graph.data or {}).get("edges", [])
        snapshot = ctx.raw.get("parameter_snapshot") or {}
    parameters = {str(node["id"]): snapshot[str(node["id"])] if str(node["id"]) in snapshot else await database.get_node_parameters(str(node["id"])) or {} for node in nodes}
    from services.workflow_sanitizer import sanitize_runtime_payload
    parameters = sanitize_runtime_payload(parameters)
    source = {"nodes": nodes, "edges": edges,
              "parameters": parameters, "outputs": ctx.outputs, "user_id": employee.owner_id,
              "session_id": ctx.session_id, "generation": ctx.raw.get("generation", 0),
              "data_scope_id": ctx.raw.get("data_scope_id"), "graphVersion": (graph.data or {}).get("graphVersion", 2), "team_plan": plan}
    source.update({key: ctx.raw[key] for key in ("execution_control_version", "controller_workflow_id", "pi_harness_version",
                  "pi_local_control_version", "pi_local_control_state", "pi_local_control_revision") if key in ctx.raw})
    if isinstance(email_input, dict):
        source["email_input"] = dict(email_input)
    if ctx.node_type == "employeeJob" and not dispatch:
        source["intake_node_id"] = ctx.node_id
    from services.employees.team_runtime import temporal_job_runtime
    runtime = temporal_job_runtime(ctx.raw)
    if runtime:
        source["runtime"] = runtime
    async with database.reserved_session() as session:
        row = await session.get(EmployeeJob, identifier)
        if row is not None:
            return row
        row = EmployeeJob(id=identifier, workflow_id=str(ctx.workflow_id),
            origin_execution_id=identifier if dispatch else str(ctx.execution_id),
            lead_node_id=lead_node_id, mission=mission, source=source,
            state="queued" if dispatch else "running", delivery={"node_ids": delivery_node_ids, "completed": [], "inflight": None})
        session.add(row)
        await session.commit()
        return row


async def bind_job_team(database: Any, *, workflow_id: str, lead_node_id: str, execution_id: str, team_id: str, employee_job_id: str | None = None) -> None:
    async with database.reserved_session() as session:
        criteria = EmployeeJob.id == employee_job_id if employee_job_id else EmployeeJob.origin_execution_id == execution_id
        found = await session.execute(select(EmployeeJob).where(EmployeeJob.workflow_id == workflow_id,
            EmployeeJob.lead_node_id == lead_node_id, criteria))
        job = found.scalar_one_or_none()
        if job:
            team = await session.get(AgentTeam, team_id)
            if team and (team.workflow_id != workflow_id or team.team_lead_node_id != lead_node_id):
                raise ValueError("The team does not belong to this employee lead")
            if job.team_id and job.team_id != team_id:
                raise RuntimeError("The job already belongs to another team")
            job.team_id = team_id
            if not no_assignments_failed(job) and not protected_job_outcome(job) and not await job_has_approval(session, job):
                job.state = "working"
            job.updated_at = _now()
            await session.commit()


async def dispatch_job(database: Any, job_id: str) -> bool:
    """Start a stable Temporal identity. Restart/retry reattaches to that run."""
    from core.container import container
    from services.employees.team_runtime import team_runtime_error
    if team_runtime_error():
        return False
    async with database.get_session() as session:
        job = await session.get(EmployeeJob, job_id)
        from models.employees import EmployeeApply
        if job and (await session.execute(select(EmployeeApply.id).where(EmployeeApply.workflow_id == job.workflow_id,
                    EmployeeApply.state.in_(["waiting", "applying"])))).first():
            return False
    if not job or job.state != "queued":
        return False
    if await database.get_workflow(job.workflow_id) is None:
        return False
    from temporalio.exceptions import WorkflowAlreadyStartedError
    from temporalio.common import WorkflowIDReusePolicy
    from services.temporal.trigger_listener_workflow import event_workflow_search_attributes
    nodes = job.source["nodes"]
    lead = next(node for node in nodes if node["id"] == job.lead_node_id)
    context = {"node_id": job.lead_node_id, "node_type": lead["type"], "node_data": lead.get("data", {}),
        "parameters": {**job.source["parameters"].get(job.lead_node_id, {}), "prompt": job.mission},
        "nodes": nodes, "edges": job.source["edges"], "outputs": job.source.get("outputs", {}),
        "workflow_id": job.workflow_id, "execution_id": job.id, "root_execution_id": job.id,
        "session_id": job.source["session_id"], "user_id": job.source["user_id"],
        "generation": job.source.get("generation", 0), "graphVersion": job.source.get("graphVersion", 2),
        "data_scope_id": job.source.get("data_scope_id"), "employee_job_id": job.id,
        "parameter_snapshot": job.source["parameters"]}
    context.update({key: job.source[key] for key in ("execution_control_version", "controller_workflow_id", "pi_harness_version",
                   "pi_local_control_version", "pi_local_control_state", "pi_local_control_revision") if key in job.source})
    try:
        await container.temporal_client().client.start_workflow("EmployeeJobWorkflow", context,
            id=job.id, task_queue=container.settings().temporal_task_queue,
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            search_attributes=event_workflow_search_attributes(job.workflow_id))
    except WorkflowAlreadyStartedError:
        pass
    async with database.reserved_session() as session:
        row = await session.get(EmployeeJob, job.id)
        if row and row.state == "queued":
            row.state = "running"
            row.updated_at = _now()
            await session.commit()
    return True


async def deliver_job(database: Any, ctx: Any, *, lead_node_id: str) -> dict:
    """Review eligibility and deliver to the origin's configured sinks once."""
    outputs = ctx.outputs or {}
    lead_output = outputs.get(lead_node_id) or {}
    team_id = str(lead_output.get("team_id") or ctx.raw.get("team_id") or "")
    execution_id = str(lead_output.get("execution_id") or ctx.execution_id or "")
    claim = uuid.uuid4().hex
    # The activity that claims the delivery holds its lease, so its own
    # retry (a restart in the middle of an approval wait) re-attaches
    # instead of answering "delivering" and leaving the draft stranded.
    from temporalio import activity
    info = activity.info() if activity.in_activity() else None
    holder = f"{info.workflow_id}:{info.activity_id}" if info else None
    async with database.reserved_session() as session:
        criteria = EmployeeJob.team_id == team_id if team_id else EmployeeJob.origin_execution_id == execution_id
        found = await session.execute(select(EmployeeJob).where(EmployeeJob.workflow_id == ctx.workflow_id,
            EmployeeJob.lead_node_id == lead_node_id, criteria))
        job = found.scalar_one_or_none()
        if not job:
            return {"delivered": False, "state": "waiting"}
        if no_assignments_failed(job):
            return {"delivered": False, "state": "failed", "job_id": job.id, "failure": job.delivery["failure"]}
        if job.state == "delivered":
            return {"delivered": True, "state": "delivered", "job_id": job.id}
        if job.state == "failed":
            # Never deliver a failed job again (a refused send ends it so).
            return {"delivered": False, "state": "failed", "job_id": job.id}
        lease = job.delivery.get("lease_until")
        if lease and datetime.fromisoformat(lease) > _now() and not (holder and job.delivery.get("lease_holder") == holder):
            return {"delivered": False, "state": "delivering", "job_id": job.id}
        inflight = job.delivery.get("inflight")
        node_types = {node["id"]: node["type"] for node in job.source["nodes"]}
        if inflight and node_types.get(inflight) not in {"approvalGate", "chatReply"}:
            # A send may have succeeded before cancellation or worker failure.
            # Preserve its intent until the owner confirms its outcome.
            return {"delivered": False, "state": "delivery_needs_review", "job_id": job.id}
        if job.state == "cancelled":
            return {"delivered": False, "state": "cancelled", "job_id": job.id}
        if job.state == "delivery_needs_review":
            return {"delivered": False, "state": job.state, "job_id": job.id}
        # Task submissions are not reviewed work. Requiring at least one
        # accepted task also prevents an un-delegated acknowledgement leaking.
        tasks = list((await session.execute(select(TeamTask).where(TeamTask.team_id == job.team_id))).scalars()) if job.team_id else []
        if tasks and any(task.status in {"failed", "cancelled"} for task in tasks):
            if protected_job_outcome(job) or await job_has_approval(session, job):
                return {"delivered": False, "state": job.state, "job_id": job.id}
            job.state = "failed" if any(task.status == "failed" for task in tasks) else "cancelled"
            job.updated_at = _now()
            await session.commit()
            return {"delivered": False, "state": job.state, "job_id": job.id}
        if not tasks:
            if protected_job_outcome(job) or await job_has_approval(session, job):
                return {"delivered": False, "state": job.state, "job_id": job.id}
            job.state = "failed"
            job.delivery = {**job.delivery, "failure": {"code": NO_ASSIGNMENTS_CODE, "message": NO_ASSIGNMENTS_MESSAGE,
                "correction_count": int(ctx.raw.get("correction_count") or 0)}}
            job.updated_at = _now()
            team = await session.get(AgentTeam, job.team_id) if job.team_id else None
            if team:
                team.status, team.completed_at = "failed", _now()
            await session.commit()
            from services.employees.events import employee_changed_now
            employee_changed_now(job.workflow_id)
            return {"delivered": False, "state": "failed", "job_id": job.id, "failure": job.delivery["failure"]}
        if any(task.status != "accepted" for task in tasks):
            return {"delivered": False, "state": "working", "job_id": job.id}
        response = str(lead_output.get("response") or "").strip()
        if not response or response == "NO_REPLY":
            return {"delivered": False, "state": "reviewing", "job_id": job.id}
        # Approval identities and chat message IDs are durable and idempotent;
        # reattach after a crashed worker rather than treating a waiting gate
        # as an uncertain external send.
        job.result = job.result or response
        job.delivery = {**job.delivery, "inflight": None, "claim_token": claim, "lease_holder": holder,
                        "lease_until": (_now() + timedelta(minutes=2)).isoformat()}
        job.state = "delivering"
        job.updated_at = _now()
        await session.commit()

    from core.container import container
    from services.chat_thread import record_chat_message
    source = job.source
    node_index = {node["id"]: node for node in source["nodes"]}
    restored = {**source.get("outputs", {}), lead_node_id: {**lead_output, "response": job.result}}
    completed = list(job.delivery.get("completed", []))
    pending = list(job.delivery["node_ids"])
    # Gate first, then its reply. Never replace the server-captured recipient.
    pending.sort(key=lambda node_id: 0 if node_index[node_id]["type"] == "approvalGate" else 1)
    for node_id in pending:
        if node_id in completed:
            if isinstance(job.delivery.get("outputs"), dict) and node_id in job.delivery["outputs"]:
                restored[node_id] = job.delivery["outputs"][node_id]
            continue
        node = node_index[node_id]
        from services.execution.conditions import evaluate_edge_condition
        conditional = [edge for edge in source["edges"] if edge.get("target") == node_id and (edge.get("data") or {}).get("condition")]
        if conditional and not any(evaluate_edge_condition(edge["data"]["condition"], envelope={"success": True, "result": restored.get(edge["source"], {})}, inner=restored.get(edge["source"], {})) for edge in conditional):
            # Discarded or expired approval is a completed no-send outcome.
            completed.append(node_id)
            async with database.reserved_session() as session:
                current = await session.get(EmployeeJob, job.id)
                current.delivery = {**current.delivery, "completed": completed}
                current.state = "delivered" if len(completed) == len(pending) else "delivering"
                await session.commit()
            continue
        async with database.reserved_session() as session:
            current = await session.get(EmployeeJob, job.id)
            # Only one delivery invocation claims this sink.
            if current.delivery.get("claim_token") != claim or current.delivery.get("inflight") or node_id in current.delivery.get("completed", []):
                return {"delivered": False, "state": "delivery_needs_review", "job_id": job.id}
            current.delivery = {**current.delivery, "inflight": node_id}
            await session.commit()
        async def renew():
            while True:
                await asyncio.sleep(30)
                async with database.reserved_session() as session:
                    current = await session.get(EmployeeJob, job.id)
                    if current.delivery.get("claim_token") != claim:
                        return
                    current.delivery = {**current.delivery, "lease_until": (_now() + timedelta(minutes=2)).isoformat()}
                    await session.commit()
        renewal = asyncio.create_task(renew())
        try:
            if node["type"] == "chatReply":
                uid = hashlib.sha256(f"{job.id}:{node_id}".encode()).hexdigest()[:32]
                saved = await record_chat_message(database, job.workflow_id, "assistant", job.result, uid=uid)
                result = {"success": bool(saved), "result": {"posted": bool(saved)}}
            else:
                # Fixed snapshot parameters, original outputs, stable per-job
                # execution identity keep approval and recipient scoping intact.
                result = await container.workflow_service().execute_node(node_id=node_id, node_type=node["type"],
                    parameters=source["parameters"][node_id], nodes=source["nodes"], edges=source["edges"],
                    session_id=source["session_id"], execution_id=job.id, workflow_id=job.workflow_id,
                    outputs=restored, user_id=source["user_id"], extras={"employee_job_id": job.id,
                        "generation": source.get("generation", 0), "data_scope_id": source.get("data_scope_id"),
                        "root_execution_id": job.id, "parameter_snapshot": source["parameters"]})
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
        if not result.get("success"):
            from services.plugin.base import NODE_WAIT_INTERRUPTED, NodeWaitInterrupted

            if result.get("error_type") == NODE_WAIT_INTERRUPTED:
                # The worker stopped in the middle of a wait: the retry
                # re-attaches under this lease, and nothing is recorded.
                raise NodeWaitInterrupted(str(result.get("error") or "The delivery's wait was interrupted"))
            # A clean refusal ends the job failed, unless a Reset or an Apply
            # ended it first.
            async with database.reserved_session() as session:
                current = await session.get(EmployeeJob, job.id)
                if current.delivery.get("claim_token") == claim and current.state == "delivering":
                    current.delivery = {**current.delivery, "inflight": None, "claim_token": None, "lease_until": None}
                    current.state = "failed"
                    current.updated_at = _now()
                    await session.commit()
                state = current.state
            from services.employees.events import employee_changed_now
            employee_changed_now(job.workflow_id)
            return {"delivered": False, "state": state, "job_id": job.id}
        restored[node_id] = result.get("result", {})
        completed.append(node_id)
        async with database.reserved_session() as session:
            current = await session.get(EmployeeJob, job.id)
            if current.delivery.get("claim_token") != claim:
                raise RuntimeError("The delivery claim changed while sending the result")
            current.delivery = {**current.delivery, "inflight": None, "completed": completed,
                "outputs": {**current.delivery.get("outputs", {}), node_id: restored[node_id]}}
            current.state = "delivered" if len(completed) == len(pending) else "delivering"
            if current.state == "delivered":
                current.delivery = {**current.delivery, "claim_token": None, "lease_until": None}
            current.updated_at = _now()
            await session.commit()
    return {"delivered": True, "state": "delivered", "job_id": job.id}


async def job_progress(database: Any, workflow_id: str) -> dict | None:
    async with database.get_session() as session:
        job = (await session.execute(select(EmployeeJob).where(EmployeeJob.workflow_id == workflow_id).order_by(EmployeeJob.updated_at.desc()).limit(1))).scalar_one_or_none()
    if not job or job.state == "delivered":
        return None
    inflight = job.delivery.get("inflight")
    node_types = {node["id"]: node["type"] for node in job.source.get("nodes", [])}
    lease = job.delivery.get("lease_until")
    expired = not lease or datetime.fromisoformat(lease) <= _now()
    state = "delivery_needs_review" if inflight and expired and node_types.get(inflight) not in {"approvalGate", "chatReply"} else job.state
    if inflight and node_types.get(inflight) == "approvalGate":
        return {"request_id": job.id, "state": "waiting_for_approval", "message": "Their result is ready for you to check before sending."}
    messages = {"queued": "Your request is saved. The team will start when they are ready.",
                "running": "The team is starting your request.", "working": "The team is working on your request and checking the result.",
                "delivering": "Their checked result is ready to send.", "failed": "The team could not finish this request. Ask them to try again.",
                "cancelled": "This request was stopped.", "delivery_needs_review": "The send was interrupted. Check whether it arrived before trying again."}
    if no_assignments_failed(job):
        messages["failed"] = "The lead did not assign work to a teammate, so this request could not finish."
    return {"request_id": job.id, "state": state, "message": messages.get(state, "The team is checking your request.")}
