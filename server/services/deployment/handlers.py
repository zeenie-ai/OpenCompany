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
from typing import Any, Dict, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.ws_handler_registry import ws_handler

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
        parameters_by_id=data.get("parameters_by_id"),
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


WS_HANDLERS: Dict[str, Any] = {
    "deploy_workflow": handle_deploy_workflow,
    "cancel_deployment": handle_cancel_deployment,
    "get_deployment_status": handle_get_deployment_status,
    "get_workflow_lock": handle_get_workflow_lock,
    "update_deployment_settings": handle_update_deployment_settings,
}


__all__ = [
    "WS_HANDLERS",
    "handle_cancel_deployment",
    "handle_deploy_workflow",
    "handle_get_deployment_status",
    "handle_get_workflow_lock",
    "handle_update_deployment_settings",
]
