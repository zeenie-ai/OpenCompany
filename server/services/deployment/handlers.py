"""Deployment domain WebSocket handlers.

Extracted from ``routers/websocket.py`` (Wave 13.2). The 5 handlers
below cover the deployment lifecycle:

  - ``deploy_workflow`` — start a continuously-running workflow with
    triggers + per-workflow locking.
  - ``cancel_deployment`` — cancel a running deployment, drain its
    listeners, unlock the workflow.
  - ``get_deployment_status`` — snapshot of in-flight deployments.
  - ``get_workflow_lock`` — current lock state.
  - ``update_deployment_settings`` — mutate runtime settings without
    re-deploying.

All handlers preserve their pre-Wave-13 wire shape. The module-level
``_deployment_tasks`` dict (workflow_id -> asyncio.Task) moves here
too; it was process-local in ``routers/websocket.py`` and stays the
same shape — only the import path changes.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.ws_handler_registry import ws_handler
from services.deployment.control import WorkflowControlService, serialize_control

# ``core.container`` and ``services.status_broadcaster`` are lazy-imported
# inside each handler body. This module is imported transitively via
# ``services.workflow`` during ``core.container`` initialization (the
# container wires ``WorkflowService`` which imports ``services.workflow``
# which imports ``services.deployment``). Eager imports at module scope
# would deadlock the partially-initialized container module.

logger = get_logger(__name__)


# Per-workflow deployment tasks for proper cancellation (Temporal/n8n pattern).
# Maps workflow_id -> asyncio.Task for parallel workflow deployments.
_deployment_tasks: Dict[str, asyncio.Task] = {}


@ws_handler()
async def handle_deploy_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Deploy workflow to run continuously until cancelled.

    Expects:
        workflow_id: Workflow identifier (required for locking)
        nodes: List of workflow nodes with {id, type, data}
        edges: List of edges with {id, source, target}
        session_id: Optional session identifier
        delay_between_runs: Optional delay in seconds between iterations (default: 1.0)

    Returns:
        Deployment start confirmation (deployment runs in background)
    """
    global _deployment_tasks
    from core.container import container
    from services.status_broadcaster import get_status_broadcaster

    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    workflow_id = data.get("workflow_id")
    nodes = data.get("nodes", [])
    edges = data.get("edges", [])
    from services.workflow_migrations import normalize_legacy_android_toolkit

    nodes, edges, normalized_parameters, migration_warnings = normalize_legacy_android_toolkit(
        nodes, edges, data.get("parameters_by_id")
    )
    if migration_warnings:
        logger.warning("[Deploy] %s", "; ".join(migration_warnings))
    session_id = data.get("session_id", "default")

    logger.debug(f"[Deploy] Received {len(edges)} edges for workflow {workflow_id}")
    for e in edges:
        target_handle = e.get("targetHandle")
        if target_handle and target_handle.startswith("input-") and target_handle != "input-main":
            logger.debug(f"[Deploy] Config edge: {e.get('source')} -> {e.get('target')} (handle={target_handle})")

    tool_edges = [e for e in edges if e.get("targetHandle") == "input-tools"]
    if tool_edges:
        logger.debug(f"[Deploy] Tool edges found: {len(tool_edges)}")
        for te in tool_edges:
            logger.debug(f"[Deploy] Tool edge: source={te.get('source')} -> target={te.get('target')}")
    else:
        logger.debug("[Deploy] No input-tools edges found")

    if not nodes:
        return {"success": False, "error": "No nodes provided"}

    if not workflow_id:
        return {"success": False, "error": "workflow_id is required for deployment"}

    # Pre-deploy validation gate. Deploy never honors a force-override —
    # a broken workflow running on a schedule is far worse than a failed
    # one-shot manual run.
    from services.workflow_validator import validate_workflow

    deploy_report = await validate_workflow(
        nodes=nodes,
        edges=edges,
        parameters_by_id=normalized_parameters,
    )
    if deploy_report["errors"]:
        return {
            "success": False,
            "error": "validation_failed",
            "report": deploy_report,
        }

    if workflow_service.is_workflow_deployed(workflow_id):
        status = workflow_service.get_deployment_status(workflow_id)
        return {
            "success": False,
            "error": f"Workflow {workflow_id} is already deployed. Cancel it first.",
            "workflow_id": workflow_id,
            "is_running": True,
            "run_counter": status.get("run_counter", 0),
        }

    lock_acquired = await broadcaster.lock_workflow(workflow_id, reason="deployment")
    if not lock_acquired:
        lock_info = broadcaster.get_workflow_lock(workflow_id)
        return {
            "success": False,
            "error": f"Workflow {workflow_id} is already locked for {lock_info.get('reason', 'deployment')}",
            "locked_by": lock_info.get("workflow_id"),
            "locked_at": lock_info.get("locked_at"),
        }

    await broadcaster.update_workflow_status(executing=True, current_node=None, progress=0)
    await broadcaster.update_deployment_status(
        is_running=True,
        status="starting",
        active_runs=0,
        workflow_id=workflow_id,
    )

    async def status_callback(node_id: str, status: str, node_data: Optional[Dict] = None):
        if node_id == "__deployment__":
            active_runs = node_data.get("active_runs", 0) if node_data else 0
            await broadcaster.update_deployment_status(
                is_running=True,
                status=status,
                active_runs=active_runs,
                workflow_id=workflow_id,
                data=node_data,
            )
        else:
            await broadcaster.update_node_status(node_id, status, node_data, workflow_id=workflow_id)
            if status == "executing":
                position = node_data.get("position", 0) if node_data else 0
                total = node_data.get("total", 1) if node_data else 1
                progress = int((position / total) * 100) if total > 0 else 0
                await broadcaster.update_workflow_status(executing=True, current_node=node_id, progress=progress)

    async def run_deployment():
        try:
            result = await workflow_service.deploy_workflow(
                nodes=nodes,
                edges=edges,
                session_id=session_id,
                status_callback=status_callback,
                workflow_id=workflow_id,
            )

            if not result.get("success"):
                logger.error("Deployment setup failed", error=result.get("error"), workflow_id=workflow_id)
                await broadcaster.update_deployment_status(
                    is_running=False,
                    status="error",
                    active_runs=0,
                    workflow_id=workflow_id,
                    error=result.get("error"),
                )
                await broadcaster.unlock_workflow(workflow_id)
                _deployment_tasks.pop(workflow_id, None)
            else:
                await broadcaster.update_deployment_status(
                    is_running=True,
                    status="running",
                    active_runs=0,
                    workflow_id=workflow_id,
                    data={
                        "triggers_setup": result.get("triggers_setup", []),
                        "deployment_id": result.get("deployment_id"),
                    },
                )
                logger.info(
                    "[Deployment] Event-driven deployment active",
                    deployment_id=result.get("deployment_id"),
                    workflow_id=workflow_id,
                    triggers=len(result.get("triggers_setup", [])),
                )

        except Exception as e:
            logger.error("Deployment task error", workflow_id=workflow_id, error=str(e))
            await broadcaster.update_deployment_status(
                is_running=False,
                status="error",
                active_runs=0,
                workflow_id=workflow_id,
                error=str(e),
            )
            await broadcaster.unlock_workflow(workflow_id)
            _deployment_tasks.pop(workflow_id, None)

    _deployment_tasks[workflow_id] = asyncio.create_task(run_deployment())

    return {
        "success": True,
        "message": "Deployment started",
        "workflow_id": workflow_id,
        "is_running": True,
        "locked": True,
        "timestamp": time.time(),
    }


@ws_handler()
async def handle_cancel_deployment(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Cancel running deployment for a specific workflow (Temporal/n8n pattern).

    Expects:
        workflow_id: Workflow to cancel (required).

    Also cancels any active event waiters (trigger nodes) and unlocks the workflow.

    Returns:
        Cancellation result with iterations completed
    """
    global _deployment_tasks
    from core.container import container
    from services.status_broadcaster import get_status_broadcaster

    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    workflow_id = data.get("workflow_id")

    if not workflow_id:
        return {"success": False, "error": "workflow_id is required for cancellation"}

    result = await workflow_service.cancel_deployment(workflow_id)

    cancelled_waiters = 0
    if result.get("success"):
        cancelled_waiters = result.get("waiters_cancelled", 0)

    task = _deployment_tasks.pop(workflow_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            logger.info("[Deployment] Deployment task cancelled", workflow_id=workflow_id)

    if workflow_id:
        await broadcaster.unlock_workflow(workflow_id)

    if result.get("success"):
        for node_id in result.get("cancelled_listener_node_ids", []):
            await broadcaster.clear_node_status(node_id)

        await broadcaster.update_workflow_status(executing=False, current_node=None, progress=0)
        await broadcaster.update_deployment_status(
            is_running=False,
            status="cancelled",
            active_runs=0,
            workflow_id=workflow_id,
            data={
                "iterations_completed": result.get("iterations_completed", 0),
            },
        )

    return {
        "success": result.get("success", False),
        "message": result.get("message", result.get("error")),
        "workflow_id": workflow_id,
        "was_running": result.get("was_running", False),
        "iterations_completed": result.get("iterations_completed", 0),
        "cancelled_waiters": cancelled_waiters,
        "unlocked": workflow_id is not None,
        "timestamp": time.time(),
    }


@ws_handler()
async def handle_get_deployment_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get current deployment status including workflow lock info."""
    from core.container import container
    from services.status_broadcaster import get_status_broadcaster

    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    workflow_id = data.get("workflow_id")
    status = workflow_service.get_deployment_status(workflow_id)

    return {
        "is_running": workflow_service.is_deployment_running(workflow_id),
        "run_counter": status.get("run_counter", 0),
        "active_runs": status.get("active_runs", 0),
        "settings": workflow_service.get_deployment_settings(),
        "workflow_id": workflow_id or status.get("workflow_id"),
        "deployed_workflows": status.get("deployed_workflows", []),
        "lock": broadcaster.get_workflow_lock(),
        "timestamp": time.time(),
    }


@ws_handler()
async def handle_get_workflow_lock(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get current workflow lock status."""
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()

    return {
        "lock": broadcaster.get_workflow_lock(),
        "timestamp": time.time(),
    }


@ws_handler()
async def handle_update_deployment_settings(
    data: Dict[str, Any],
    websocket: WebSocket,
) -> Dict[str, Any]:
    """Update deployment settings (can be called during active deployment)."""
    from core.container import container
    from services.status_broadcaster import get_status_broadcaster

    workflow_service = container.workflow_service()
    broadcaster = get_status_broadcaster()

    settings_to_update = {}
    if "delay_between_runs" in data:
        settings_to_update["delay_between_runs"] = data["delay_between_runs"]
    if "stop_on_error" in data:
        settings_to_update["stop_on_error"] = data["stop_on_error"]
    if "max_iterations" in data:
        settings_to_update["max_iterations"] = data["max_iterations"]

    updated_settings = await workflow_service.update_deployment_settings(settings_to_update)

    status = workflow_service.get_deployment_status()
    await broadcaster.broadcast(
        {
            "type": "deployment_settings_updated",
            "settings": updated_settings,
            "is_running": workflow_service.is_deployment_running(),
            "run_counter": status.get("run_counter", 0),
        }
    )

    return {
        "success": True,
        "settings": updated_settings,
        "is_running": workflow_service.is_deployment_running(),
        "run_counter": status.get("run_counter", 0),
        "active_runs": status.get("active_runs", 0),
        "timestamp": time.time(),
    }


def _control_service():
    from core.container import container

    return WorkflowControlService(container.database())


async def _start_controller(control) -> Optional[str]:
    """Start the durable Temporal controller when Temporal is available."""
    from core.container import container
    from temporalio.common import SearchAttributeKey, SearchAttributePair, TypedSearchAttributes

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        return None
    handle = await wrapper.client.start_workflow(
        "WorkflowControlWorkflow",
        args=[{"generation": control.generation, "state": "running"}],
        id=control.controller_workflow_id,
        task_queue="machina-tasks",
        search_attributes=TypedSearchAttributes([
            SearchAttributePair(
                SearchAttributeKey.for_keyword("EventWorkflowId"), control.workflow_id,
            )
        ]),
    )
    return getattr(handle, "result_run_id", None) or getattr(handle, "first_execution_run_id", None)


async def _signal_controller(control, signal_name: str) -> None:
    from core.container import container

    wrapper = container.temporal_client()
    if wrapper is not None and wrapper.client is not None and control.controller_workflow_id:
        await wrapper.client.get_workflow_handle(
            control.controller_workflow_id, run_id=control.controller_run_id
        ).signal(signal_name)


async def _signal_generation_workflows(workflow_id: str, signal_name: str) -> int:
    """Best-effort cooperative fan-out to this deployment's live executions.

    Visibility is discovery only; durable control state remains authoritative.
    Every matching workflow receives an idempotent pause/resume flag mutation.
    """
    from core.container import container

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        return 0
    query = (
        f"EventWorkflowId='{workflow_id}' "
        "AND ExecutionStatus='Running'"
    )
    signalled = 0
    try:
        async for execution in wrapper.client.list_workflows(query=query):
            try:
                await wrapper.client.get_workflow_handle(
                    execution.id, run_id=execution.run_id
                ).signal(signal_name)
                signalled += 1
            except Exception as exc:
                logger.warning(
                    "Workflow control signal failed",
                    workflow_id=workflow_id,
                    temporal_workflow_id=execution.id,
                    signal=signal_name,
                    error=str(exc),
                )
    except Exception as exc:
        logger.warning(
            "Workflow control visibility fan-out failed",
            workflow_id=workflow_id,
            signal=signal_name,
            error=str(exc),
        )
    return signalled


async def _terminate_generation_workflows(workflow_id: str) -> int:
    """Immediately terminate every visible execution in one application tree."""
    from core.container import container

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        return 0
    terminated = 0
    try:
        async for execution in wrapper.client.list_workflows(
            query=f"EventWorkflowId='{workflow_id}' AND ExecutionStatus='Running'"
        ):
            try:
                await wrapper.client.get_workflow_handle(
                    execution.id, run_id=execution.run_id
                ).terminate(reason="workflow_reset")
                terminated += 1
            except Exception as exc:
                logger.warning(
                    "Workflow reset termination failed",
                    workflow_id=workflow_id,
                    temporal_workflow_id=execution.id,
                    error=str(exc),
                )
    except Exception as exc:
        logger.warning(
            "Workflow reset visibility scan failed",
            workflow_id=workflow_id,
            error=str(exc),
        )
    return terminated


def _expected_revision(data: Dict[str, Any], control) -> int:
    supplied = data.get("expected_revision")
    return control.revision if supplied is None else int(supplied)


async def _set_cron_pause(workflow_id: str, *, paused: bool) -> int:
    from core.container import container
    from services.temporal.schedules import set_cron_schedules_paused

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        return 0
    return await set_cron_schedules_paused(wrapper.client, workflow_id, paused=paused)


async def _with_runtime_counts(payload: Dict[str, Any], workflow_id: str) -> Dict[str, Any]:
    from core.container import container

    status = container.workflow_service().get_deployment_status(workflow_id)
    return {
        **payload,
        "active_count": status.get("active_runs", 0),
        "in_flight_count": status.get("active_runs", 0),
        "queued_count": 0,
    }


@ws_handler("workflow_id")
async def handle_get_workflow_control_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return await _with_runtime_counts(await _control_service().get_status(data["workflow_id"]), data["workflow_id"])


@ws_handler("workflow_id")
async def handle_start_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Create generation one and retain deploy_workflow wire compatibility."""
    workflow_id = data["workflow_id"]
    key = data.get("idempotency_key") or f"start:{workflow_id}:{uuid.uuid4().hex}"
    service = _control_service()
    control, created = await service.begin_generation(
        workflow_id=workflow_id, nodes=data.get("nodes", []), edges=data.get("edges", []),
        session_id=data.get("session_id", "default"), idempotency_key=key,
    )
    if not created:
        return await _with_runtime_counts({"success": True, "idempotent": True, **serialize_control(control)}, workflow_id)
    try:
        run_id = await _start_controller(control)
        if run_id:
            control = await service.transition(
                control, expected_revision=control.revision, from_statuses={"starting"}, status="starting",
                values={"controller_run_id": run_id},
            )
        deployed = await handle_deploy_workflow(data, websocket)
        if not deployed.get("success"):
            await service.fail(control, str(deployed.get("error", "deployment_failed")))
            return deployed
        control = await service.transition(control, expected_revision=control.revision, from_statuses={"starting"}, status="running")
        return await _with_runtime_counts({"success": True, **serialize_control(control)}, workflow_id)
    except Exception as exc:
        await service.fail(control, str(exc))
        raise


@ws_handler("workflow_id")
async def handle_pause_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    workflow_id = data["workflow_id"]
    service = _control_service()
    control = await service.database.get_latest_workflow_control(workflow_id)
    if control is None:
        return {"success": False, "error": "workflow_never_started"}
    control = await service.transition(
        control, expected_revision=_expected_revision(data, control), from_statuses={"running"}, status="pausing"
    )
    container.workflow_service().pause_deployment(workflow_id)
    await _signal_controller(control, "pause")
    paused_schedules = await _set_cron_pause(workflow_id, paused=True)
    paused_triggers = await container.workflow_service().update_trigger_pause_status(workflow_id, paused=True)
    signalled = await _signal_generation_workflows(workflow_id, "pause")
    control = await service.transition(control, expected_revision=control.revision, from_statuses={"pausing"}, status="paused")
    return await _with_runtime_counts({
        "success": True, "signalled_executions": signalled,
        "paused_schedules": paused_schedules, "paused_triggers": paused_triggers,
        **serialize_control(control),
    }, workflow_id)


@ws_handler("workflow_id")
async def handle_resume_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    workflow_id = data["workflow_id"]
    service = _control_service()
    control = await service.database.get_latest_workflow_control(workflow_id)
    if control is None:
        return {"success": False, "error": "workflow_never_started"}
    control = await service.transition(
        control, expected_revision=_expected_revision(data, control), from_statuses={"paused"}, status="resuming"
    )
    await _signal_controller(control, "resume")
    resumed_schedules = await _set_cron_pause(workflow_id, paused=False)
    signalled = await _signal_generation_workflows(workflow_id, "resume")
    queued = await container.workflow_service().resume_deployment(workflow_id)
    resumed_triggers = await container.workflow_service().update_trigger_pause_status(workflow_id, paused=False)
    control = await service.transition(control, expected_revision=control.revision, from_statuses={"resuming"}, status="running")
    return await _with_runtime_counts({
        "success": True, "resumed_queued_events": queued, "signalled_executions": signalled,
        "resumed_schedules": resumed_schedules, "resumed_triggers": resumed_triggers,
        **serialize_control(control),
    }, workflow_id)


@ws_handler("workflow_id")
async def handle_reset_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id = data["workflow_id"]
    service = _control_service()
    current = await service.database.get_latest_workflow_control(workflow_id)
    if current is not None:
        current = await service.transition(
            current, expected_revision=_expected_revision(data, current),
            from_statuses={"starting", "running", "pausing", "paused", "resuming", "failed"}, status="resetting",
            values={"terminal_reason": "workflow_reset", "completed_at": datetime.now(timezone.utc)},
        )
        try:
            await _signal_controller(current, "reset")
        finally:
            terminated = await _terminate_generation_workflows(workflow_id)
            await handle_cancel_deployment({"workflow_id": workflow_id}, websocket)
    else:
        terminated = 0
    if current is None:
        return await _with_runtime_counts(await service.get_status(workflow_id), workflow_id)
    current = await service.transition(
        current, expected_revision=current.revision, from_statuses={"resetting"}, status="reset",
        values={"terminal_reason": "workflow_reset", "completed_at": datetime.now(timezone.utc)},
    )
    return await _with_runtime_counts(
        {"success": True, "terminated_executions": terminated, **serialize_control(current)},
        workflow_id,
    )


WS_HANDLERS: Dict[str, Any] = {
    "deploy_workflow": handle_deploy_workflow,
    "cancel_deployment": handle_cancel_deployment,
    "get_deployment_status": handle_get_deployment_status,
    "get_workflow_lock": handle_get_workflow_lock,
    "update_deployment_settings": handle_update_deployment_settings,
    "start_workflow": handle_start_workflow,
    "pause_workflow": handle_pause_workflow,
    "resume_workflow": handle_resume_workflow,
    "reset_workflow": handle_reset_workflow,
    "get_workflow_control_status": handle_get_workflow_control_status,
}


__all__ = [
    "WS_HANDLERS",
    "handle_cancel_deployment",
    "handle_deploy_workflow",
    "handle_get_deployment_status",
    "handle_get_workflow_lock",
    "handle_update_deployment_settings",
]
