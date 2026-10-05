"""Apply future run snapshots without Reset's destructive data clearing."""
from __future__ import annotations
from copy import deepcopy
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import uuid
from typing import Any
from sqlmodel import select
from models.employees import EmployeeApply, EmployeeJob
from models.database import AgentTeam, WorkflowControlExecution


async def _active(database, workflow_id):
    async with database.get_session() as session:
        team = (await session.execute(select(AgentTeam.id).where(AgentTeam.workflow_id == workflow_id, AgentTeam.status == "active"))).first()
        job = (await session.execute(select(EmployeeJob.id).where(EmployeeJob.workflow_id == workflow_id, EmployeeJob.state.in_(["running", "working", "delivering", "delivery_queued"])))).first()
    from core.container import container
    from services.chat.ledger import live_runs
    if team or job or await live_runs(database, workflow_id):
        return True
    if container.workflow_service().get_deployment_status(workflow_id).get("active_runs", 0):
        return True
    wrapper = container.temporal_client()
    if wrapper and wrapper.client:
        # Admission is already paused before this query. Exclude the controller
        # and listeners: detached execution roots carry EventWorkflowId too.
        async for execution in wrapper.client.list_workflows(query=f"EventWorkflowId='{workflow_id}' AND ExecutionStatus='Running'"):
            if execution.workflow_type not in {"WorkflowControlWorkflow", "TriggerListenerWorkflow", "PollingTriggerWorkflow", "WorkspaceTaskControllerWorkflow"}:
                return True
    return False


async def apply_saved_changes(database: Any, workflow_id: str, *, owner_id: str, key: str, stop_work: bool = False) -> dict:
    from core.container import container
    from services.deployment.handlers import _controller_handle, _set_cron_pause
    from services.workflow_validator import validate_workflow
    from services.deployment.control import _graph_hash
    from services.employees.events import employee_changed_now
    if not getattr(container.settings(), "employee_safe_apply_enabled", True):
        return {"success": False, "error": "safe_apply_disabled", "activation_state": "saved"}
    workflow = await database.get_workflow(workflow_id)
    if workflow is None or (workflow.data or {}).get("owner_id", "owner") != owner_id:
        return {"success": False, "error": "not_found"}
    control = await database.get_latest_workflow_control(workflow_id)
    if not control or control.status in {"ready", "failed", "completed", "reset"}:
        return {"success": True, "activation_state": "saved"}
    if control.status not in {"running", "paused"}:
        return {"success": False, "error": "conflict"}
    identifier = f"apply:{workflow_id}:{key}"
    snapshot = deepcopy(workflow.data)
    parameters = {node["id"]: await database.get_node_parameters(node["id"]) or {} for node in snapshot.get("nodes", [])}
    from services.workflow_sanitizer import sanitize_runtime_payload
    snapshot["parameters"] = sanitize_runtime_payload(parameters)
    report = await validate_workflow(nodes=snapshot["nodes"], edges=snapshot["edges"], parameters_by_id=parameters)
    if report.get("errors"):
        return {"success": False, "error": "invalid_changes", "validation_issues": report["errors"]}
    handle = _controller_handle(control)
    if handle is None and getattr(container.settings(), "temporal_enabled", True):
        # Legacy single-agent installations keep their operational snapshot;
        # callers can explicitly Stop/Start. Never use Reset to erase data.
        return {"success": False, "error": "safe_apply_runtime_required", "activation_state": "saved"}
    async with database.reserved_session() as session:
        request = await session.get(EmployeeApply, identifier)
        if request is None:
            waiting = (await session.execute(select(EmployeeApply).where(EmployeeApply.workflow_id == workflow_id, EmployeeApply.state.in_(["waiting", "applying"])))).first()
            if waiting:
                return {"success": False, "error": "conflict"}
            request = EmployeeApply(id=identifier, workflow_id=workflow_id, owner_id=owner_id,
                resume_after=control.status == "running", snapshot=snapshot, previous=deepcopy(control.graph_snapshot))
            session.add(request)
            await session.commit()
        elif request.state == "applied":
            return {"success": True, "activation_state": "running" if request.resume_after else "paused"}
        snapshot = deepcopy(request.snapshot)
    # Capture the owner's producer pause settings before the temporary
    # admission pause. Keep them durably across waiting and process restarts.
    client = container.temporal_client().client
    if not request.producer_states.get("captured"):
        original = {}
        if client:
            schedules = await client.list_schedules(query=f"EventWorkflowId='{workflow_id}' AND EventTriggerKind='cron'")
            async for entry in schedules:
                schedule = (await client.get_schedule_handle(entry.id).describe()).schedule
                original[entry.id] = {"paused": schedule.state.paused, "note": schedule.state.note}
        async with database.reserved_session() as session:
            saved = await session.get(EmployeeApply, identifier)
            if not saved.producer_states.get("captured"):
                saved.producer_states = {"captured": True, "schedules": original}
                await session.commit()
            request.producer_states = deepcopy(saved.producer_states)
    async def restore_producers():
        from temporalio.client import ScheduleUpdate
        for schedule_id, original in request.producer_states.get("schedules", {}).items():
            def restore(update, original=original):
                schedule = update.description.schedule
                return ScheduleUpdate(schedule=replace(schedule, state=replace(schedule.state,
                    paused=original["paused"] or not request.resume_after, note=original.get("note"))))
            await client.get_schedule_handle(schedule_id).update(restore)
    # Only stop admissions. This does not signal Pause to children, cancel
    # approvals, reset Context, or empty the controller's accepted queue.
    if handle:
        pause_key = identifier + ":pause" + (":" + uuid.uuid4().hex if request.state == "failed" else "")
        await handle.execute_update("pause_admissions", request.resume_after, id=pause_key)
    await _set_cron_pause(workflow_id, paused=True, strict=True)
    manager = container.workflow_service()._get_deployment_manager()
    manager.pause(workflow_id)
    if stop_work:
        wrapper = container.temporal_client()
        if wrapper and wrapper.client:
            async for execution in wrapper.client.list_workflows(query=f"EventWorkflowId='{workflow_id}' AND ExecutionStatus='Running'"):
                if execution.workflow_type not in {"WorkflowControlWorkflow", "TriggerListenerWorkflow", "PollingTriggerWorkflow", "WorkspaceTaskControllerWorkflow"}:
                    await wrapper.client.get_workflow_handle(execution.id).cancel()
        for task in manager._active_runs.get(workflow_id, {}).values():
            task.cancel()
        # Durable work records must reflect the explicitly requested stop;
        # otherwise recovery would wait forever for a cancelled execution.
        async with database.get_session() as session:
            teams = (await session.execute(select(AgentTeam).where(AgentTeam.workflow_id == workflow_id, AgentTeam.status == "active"))).scalars().all()
        for team in teams:
            for task in await database.get_team_tasks(team.id):
                if task.get("status") in {"pending", "blocked", "queued", "running"}:
                    await database.cancel_team_task(team.id, task["id"], "The owner stopped work to apply new abilities")
            await database.update_team_status(team.id, "dissolved")
        async with database.reserved_session() as session:
            jobs = (await session.execute(select(EmployeeJob).where(EmployeeJob.workflow_id == workflow_id, EmployeeJob.state.in_(["running", "working", "delivering", "delivery_queued"])))).scalars().all()
            for job in jobs:
                job.state = "cancelled"
                job.updated_at = datetime.now(timezone.utc)
            await session.commit()
    if await _active(database, workflow_id):
        return {"success": True, "activation_state": "waiting", "message": "Finishing current work before applying the new abilities."}
    claim = uuid.uuid4().hex
    async with database.reserved_session() as session:
        request = await session.get(EmployeeApply, identifier)
        until = request.lease_until
        if until and until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if request.state == "applying" and until and until > datetime.now(timezone.utc):
            return {"success": True, "activation_state": "starting"}
        request.state = "applying"
        request.claim_token = claim
        request.lease_until = datetime.now(timezone.utc) + timedelta(minutes=2)
        await session.commit()
    manager = container.workflow_service()._get_deployment_manager()
    old_state = manager._deployments.get(workflow_id)
    old_schedules = []
    async def renew():
        while True:
            await asyncio.sleep(30)
            async with database.reserved_session() as session:
                saved = await session.get(EmployeeApply, identifier)
                if saved.claim_token != claim:
                    return
                saved.lease_until = datetime.now(timezone.utc) + timedelta(minutes=2)
                await session.commit()
    renewal = asyncio.create_task(renew())
    async def verify_claim():
        async with database.get_session() as session:
            current = await session.get(EmployeeApply, identifier)
            until = current.lease_until
            if until and until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            if current.claim_token != claim or not until or until <= datetime.now(timezone.utc):
                raise RuntimeError("apply_claim_lost")
    try:
        # Refresh all cron action snapshots, preserving schedule specification,
        # pause state, owner edits and catch-up policies.
        from temporalio.client import ScheduleUpdate
        client = container.temporal_client().client
        async def schedules():
            if not client:
                return
            iterator = await client.list_schedules(query=f"EventWorkflowId='{workflow_id}' AND EventTriggerKind='cron'")
            async for description in iterator:
                yield description
        async for description in schedules():
            await verify_claim()
            old_schedules.append((description.id, (await client.get_schedule_handle(description.id).describe()).schedule))
            async def update_schedule(update):
                schedule = update.description.schedule
                action = schedule.action
                arguments = list(action.args)
                if arguments and not isinstance(arguments[0], dict):
                    from temporalio.api.common.v1 import Payload
                    if isinstance(arguments[0], Payload):
                        arguments = await client.data_converter.decode(arguments)
                if not arguments or not isinstance(arguments[0], dict):
                    raise RuntimeError("The schedule snapshot needs review")
                data = {**arguments[0], "nodes": snapshot["nodes"], "edges": snapshot["edges"],
                        "parameter_snapshot": snapshot.get("parameters", {})}
                return ScheduleUpdate(schedule=replace(schedule, action=replace(action, args=[data, *arguments[1:]])))
            await client.get_schedule_handle(description.id).update(update_schedule)
        if handle:
            await verify_claim()
            await handle.execute_update("replace_graph", snapshot, id=identifier + ":graph:" + claim)
        if old_state:
            await verify_claim()
            changes = {"nodes": snapshot["nodes"], "edges": snapshot["edges"]}
            if hasattr(old_state, "parameter_snapshot"):
                changes["parameter_snapshot"] = snapshot.get("parameters", {})
            manager._deployments[workflow_id] = replace(old_state, **changes)
            old_ids = {node["id"] for node in request.previous.get("nodes", [])}
            from services.plugin import TriggerNode
            from services.node_registry import get_node_class
            for node in snapshot["nodes"]:
                cls = get_node_class(node["type"])
                if handle and node["id"] not in old_ids and cls and issubclass(cls, TriggerNode) and node["type"] != "cronScheduler":
                    await manager._start_canary_listener(node, workflow_id, await database.get_node_parameters(node["id"]) or {})
        async with database.reserved_session() as session:
            current = await session.get(WorkflowControlExecution, control.id)
            saved = await session.get(EmployeeApply, identifier)
            if saved.claim_token != claim:
                raise RuntimeError("apply_claim_lost")
            if current.status not in {"running", "paused"}:
                raise RuntimeError("The employee changed while applying abilities")
            # A manual Pause during the drain takes precedence over the
            # original request to resume after the handoff.
            saved.resume_after = saved.resume_after and current.status == "running"
            request.resume_after = saved.resume_after
            current.graph_snapshot = snapshot
            current.graph_hash = _graph_hash(snapshot["nodes"], snapshot["edges"])
            current.revision += 1
            await session.commit()
        if request.resume_after:
            await verify_claim()
            if handle:
                await handle.execute_update("set_control_state", "running", id=identifier + ":resume:" + claim)
            await _set_cron_pause(workflow_id, paused=False, strict=True)
            await manager.resume(workflow_id)
        await restore_producers()
        await verify_claim()
        async with database.reserved_session() as session:
            saved = await session.get(EmployeeApply, identifier)
            if saved.claim_token != claim:
                raise RuntimeError("apply_claim_lost")
            saved.state = "applied"
            saved.claim_token = None
            saved.lease_until = None
            await session.commit()
        employee_changed_now(workflow_id)
        return {"success": True, "activation_state": "running" if request.resume_after else "paused"}
    except Exception:
        async with database.get_session() as session:
            current_request = await session.get(EmployeeApply, identifier)
            if current_request.claim_token != claim:
                return {"success": False, "error": "conflict", "activation_state": "starting"}
        # Retain the last operational graph if any part of handoff fails.
        if handle:
            await verify_claim()
            await handle.execute_update("pause_admissions", False, id=identifier + ":rollback-pause:" + claim)
            await handle.execute_update("replace_graph", request.previous, id=identifier + ":rollback:" + claim)
        for schedule_id, previous_schedule in old_schedules:
            await verify_claim()
            await client.get_schedule_handle(schedule_id).update(lambda _input, previous_schedule=previous_schedule: ScheduleUpdate(schedule=previous_schedule))
        if old_state:
            manager._deployments[workflow_id] = old_state
        if request.resume_after:
            await verify_claim()
            if handle:
                await handle.execute_update("set_control_state", "running", id=identifier + ":rollback-resume:" + claim)
            await _set_cron_pause(workflow_id, paused=False, strict=True)
            await manager.resume(workflow_id)
        await restore_producers()
        async with database.reserved_session() as session:
            failed = await session.get(EmployeeApply, identifier)
            if failed.claim_token != claim:
                return {"success": False, "error": "conflict", "activation_state": "starting"}
            current = await session.get(WorkflowControlExecution, control.id)
            current.graph_snapshot = request.previous
            current.graph_hash = _graph_hash(request.previous.get("nodes", []), request.previous.get("edges", []))
            failed.state = "failed"
            failed.claim_token = None
            failed.lease_until = None
            await session.commit()
        return {"success": False, "error": "apply_failed", "activation_state": "failed"}
    finally:
        renewal.cancel()
        await asyncio.gather(renewal, return_exceptions=True)


async def recover_applies(database: Any) -> None:
    async with database.get_session() as session:
        pending = (await session.execute(select(EmployeeApply).where(EmployeeApply.state.in_(["waiting", "applying"])))).scalars().all()
    for request in pending:
        await apply_saved_changes(database, request.workflow_id, owner_id=request.owner_id, key=request.id.split(":", 2)[2])
