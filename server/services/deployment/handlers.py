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
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import WebSocket

from core.logging import get_logger
from services.ws_handler_registry import ws_handler
from services.deployment.control import (
    ACTIVE_STATES,
    CLEAR_PAUSE_REASON,
    PAUSE_REASON_CONTROLLER_MISSING,
    PAUSE_REASON_FAILURES,
    PAUSE_REASON_RECOVERY,
    WorkflowControlService,
    serialize_control,
)

# ``core.container`` and ``services.status_broadcaster`` are lazy-imported
# inside each handler body. This module is imported transitively via
# ``services.workflow`` during ``core.container`` initialization (the
# container wires ``WorkflowService`` which imports ``services.workflow``
# which imports ``services.deployment``). Eager imports at module scope
# would deadlock the partially-initialized container module.

logger = get_logger(__name__)


class TemporalControlUnavailable(RuntimeError):
    """A lifecycle Update could not start because no Temporal client exists."""


class TemporalControlAckMismatch(RuntimeError):
    """Temporal completed an Update without returning the requested state."""


class ControllerExecutionMissing(RuntimeError):
    """The controller execution this generation names no longer exists.

    Distinct from :class:`TemporalControlUnavailable`, and the distinction is
    the whole point: "unavailable" is transient and worth waiting out, whereas
    a deleted execution never comes back. Treating the second as the first is
    what let a generation sit in ``pausing`` forever, which in turn blocked
    every future Start on that workflow.
    """


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
    from services.workflow_context_migration import (
        import_legacy_context_receipts,
        load_node_parameters,
        persist_parameter_aliases,
    )
    from services.workflow_migrations import normalize_workflow_graph
    from services.workflow_sanitizer import sanitize_workflow_graph

    parameters_by_id = data.get("parameters_by_id")
    if not isinstance(parameters_by_id, dict):
        parameters_by_id = await load_node_parameters(
            container.database(),
            nodes,
        )

    normalization = normalize_workflow_graph(
        str(workflow_id or ""),
        nodes,
        edges,
        parameters_by_id,
    )
    safe_graph = sanitize_workflow_graph(normalization.graph_data())
    nodes = list(safe_graph["nodes"])
    edges = list(safe_graph["edges"])
    normalized_parameters = normalization.node_parameters
    await import_legacy_context_receipts(
        container.database(),
        normalization.state_imports,
    )
    graph_version = normalization.graph_version
    graph_aliases = normalization.aliases
    if normalization.warnings:
        logger.warning("[Deploy] %s", "; ".join(normalization.warnings))
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
        enforce_context=True,
    )
    if deploy_report["errors"]:
        return {
            "success": False,
            "error": "validation_failed",
            "report": deploy_report,
        }

    # Rekey parameter rows onto canonical ids only after the graph is admitted.
    # Running this before the gate orphaned configuration on a failed deploy:
    # the rows were renamed and the originals deleted, while the stored graph
    # kept its old ids, so the next read looked up ids that no longer existed.
    await persist_parameter_aliases(
        container.database(),
        aliases=graph_aliases,
        parameters=normalized_parameters,
    )

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

    await broadcaster.update_workflow_status(
        executing=True,
        current_node=None,
        progress=0,
        workflow_id=workflow_id,
    )
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
                await broadcaster.update_workflow_status(
                    executing=True,
                    current_node=node_id,
                    progress=progress,
                    workflow_id=workflow_id,
                )

    async def run_deployment():
        try:
            result = await workflow_service.deploy_workflow(
                nodes=nodes,
                edges=edges,
                session_id=session_id,
                status_callback=status_callback,
                workflow_id=workflow_id,
                graph_version=graph_version,
                generation=int(data.get("generation") or 0),
                user_id=str(
                    (
                        getattr(
                            getattr(websocket, "state", None),
                            "user_id",
                            None,
                        )
                        if websocket is not None
                        else data.get("user_id")
                    )
                    or "owner"
                ),
            )

            if not result.get("success"):
                logger.error("Deployment setup failed", error=result.get("error"), workflow_id=workflow_id)
                # Clear BOTH UI-facing flags: the deploy handler already
                # broadcast executing=True, and deployments never touch
                # the run counter, so nothing else would ever emit
                # executing=False for this workflow (toolbar stuck).
                await broadcaster.update_workflow_status(
                    executing=False,
                    current_node=None,
                    progress=0,
                    workflow_id=workflow_id,
                )
                await broadcaster.update_deployment_status(
                    is_running=False,
                    status="error",
                    active_runs=0,
                    workflow_id=workflow_id,
                    error=result.get("error"),
                )
                await broadcaster.unlock_workflow(workflow_id)
                _deployment_tasks.pop(workflow_id, None)
                return result
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
                return result

        except asyncio.CancelledError:
            # CancelledError is BaseException — the branch below cannot
            # catch it, and a deploy task cancelled outside
            # handle_cancel_deployment (which unlocks itself) previously
            # left the workflow lock dangling forever.
            await broadcaster.unlock_workflow(workflow_id)
            _deployment_tasks.pop(workflow_id, None)
            raise
        except Exception as e:
            logger.error("Deployment task error", workflow_id=workflow_id, error=str(e))
            await broadcaster.update_workflow_status(
                executing=False,
                current_node=None,
                progress=0,
                workflow_id=workflow_id,
            )
            await broadcaster.update_deployment_status(
                is_running=False,
                status="error",
                active_runs=0,
                workflow_id=workflow_id,
                error=str(e),
            )
            await broadcaster.unlock_workflow(workflow_id)
            _deployment_tasks.pop(workflow_id, None)
            return {"success": False, "error": str(e), "workflow_id": workflow_id}

    _deployment_tasks[workflow_id] = asyncio.create_task(run_deployment())

    return {
        "success": True,
        "message": "Deployment started",
        "workflow_id": workflow_id,
        "is_running": True,
        "locked": True,
        "timestamp": time.time(),
        "graph": {
            "graphVersion": graph_version,
            "nodes": nodes,
            "edges": edges,
        },
        "aliases": graph_aliases,
        "migration_warnings": normalization.warnings,
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

    # Terminal UI-facing state is emitted UNCONDITIONALLY: cancelling a
    # workflow that is not locally deployed (post-restart before re-arm,
    # or a Reset after a crash) previously emitted nothing, leaving the
    # FE showing executing/running forever. The idempotent terminal
    # broadcast is harmless when nothing was running.
    await broadcaster.update_workflow_status(
        executing=False,
        current_node=None,
        progress=0,
        workflow_id=workflow_id,
    )
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


async def _start_controller(control, *, use_existing: bool = False) -> Optional[str]:
    """Start the durable controller, or use local mode when Temporal is disabled.

    ``use_existing=True`` is the rebuild path (Resume after the controller
    was killed): per the documented WorkflowIdConflictPolicy semantics,
    ``USE_EXISTING`` adopts a still-running controller (returning its run
    id) instead of erroring, while the default reuse policy permits a
    fresh run when the previous execution was Terminated/Failed — so the
    rebuild is race-safe against mis-detection.
    """
    from core.container import container
    from temporalio.common import (
        SearchAttributeKey,
        SearchAttributePair,
        TypedSearchAttributes,
        WorkflowIDConflictPolicy,
    )

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        if container.settings().temporal_enabled:
            raise RuntimeError("temporal_control_unavailable")
        return None
    start_kwargs: Dict[str, Any] = {}
    if use_existing:
        start_kwargs["id_conflict_policy"] = WorkflowIDConflictPolicy.USE_EXISTING
    handle = await wrapper.client.start_workflow(
        "WorkflowControlWorkflow",
        args=[{
            "workflow_id": control.workflow_id,
            "generation": control.generation,
            "execution_id": control.execution_id,
            "root_execution_id": control.root_execution_id,
            "data_scope_id": control.data_scope_id or control.execution_id,
            "state": "running",
        }],
        id=control.controller_workflow_id,
        task_queue=container.settings().temporal_task_queue,
        search_attributes=TypedSearchAttributes([
            SearchAttributePair(
                SearchAttributeKey.for_keyword("EventWorkflowId"), control.workflow_id,
            )
        ]),
        **start_kwargs,
    )
    return getattr(handle, "result_run_id", None) or getattr(handle, "first_execution_run_id", None)


def _controller_handle(control):
    """Handle addressed by workflow id only — never pinned to a run id.

    The controller keeps its event history bounded via continue-as-new,
    which mints a new run id under the same workflow id. A handle pinned
    to ``control.controller_run_id`` (the FIRST run) would target a
    closed run after the first rollover and every pause/resume/update/
    query would fail. The workflow id already carries the generation
    (``workflow-control-<wf>-g<N>``), so unpinned addressing cannot
    reach a different generation's controller.
    """
    from core.container import container

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None or not control.controller_workflow_id:
        return None
    return wrapper.client.get_workflow_handle(control.controller_workflow_id)


async def _signal_controller(control, signal_name: str) -> None:
    handle = _controller_handle(control)
    if handle is not None:
        await handle.signal(signal_name)


async def _update_controller_state(
    control,
    requested_state: str,
    *,
    update_id: str,
) -> Optional[Dict[str, Any]]:
    """Apply and await a durable controller state change.

    Temporal-disabled installations retain the local deployment path. When
    Temporal is enabled, losing its client is a failed mutation rather than a
    false-positive pause/resume acknowledgement.
    """
    from core.container import container

    handle = _controller_handle(control)
    if handle is None:
        if container.settings().temporal_enabled:
            raise TemporalControlUnavailable("temporal_control_unavailable")
        return None
    result = await handle.execute_update(
        "set_control_state",
        requested_state,
        id=update_id,
    )
    expected_state = "paused" if requested_state in {"pause", "paused"} else "running"
    if not isinstance(result, dict) or result.get("state") != expected_state:
        raise TemporalControlAckMismatch(
            f"temporal_control_ack_mismatch:{result.get('state') if isinstance(result, dict) else 'missing'}"
        )
    return result


async def _query_controller_state(control) -> Optional[Dict[str, Any]]:
    """Return controller state when reachable; status reads remain resilient.

    Raises :class:`ControllerExecutionMissing` when Temporal reports the
    execution does not exist, rather than folding that into the ``None``
    that means "could not reach it". Callers need to tell the two apart:
    one resolves itself, the other never will.
    """
    handle = _controller_handle(control)
    if handle is None:
        return None
    try:
        result = await handle.query("status")
        return result if isinstance(result, dict) else None
    except Exception as exc:
        if _temporal_target_already_gone(exc):
            # Expected and unremarkable: a controller closes when its
            # generation ends, and Temporal deletes closed executions once
            # the namespace retention window passes. Not a warning.
            logger.debug(
                "Workflow controller execution no longer exists",
                workflow_id=control.workflow_id,
                controller_workflow_id=control.controller_workflow_id,
                status=control.status,
            )
            raise ControllerExecutionMissing(
                str(control.controller_workflow_id)
            ) from exc
        logger.warning(
            "Workflow controller status query failed",
            workflow_id=control.workflow_id,
            controller_workflow_id=control.controller_workflow_id,
            error=str(exc),
        )
        return None


# A missing controller is only actionable for a generation the database still
# believes is alive. ``resetting`` is excluded deliberately: it already has an
# explicit retry path through Reset, and auto-failing it here could race a
# reset running concurrently in another request.
_MISSING_CONTROLLER_FAILS = frozenset(
    {"starting", "running", "pausing", "paused", "resuming"}
)
# The subset of live states that converge to ``paused`` (user-resumable)
# under WORKFLOW_CONTROL_MISSING_CONTROLLER=pause. ``starting`` always
# fails instead — nothing durable is running yet, and Reset + Start
# rebuilds a fresh generation cleanly.
_MISSING_CONTROLLER_PAUSES = frozenset({"running", "pausing", "paused", "resuming"})


async def _fail_missing_controller(service: WorkflowControlService, control):
    """Converge a generation whose controller execution has vanished.

    Without this the row keeps whatever live status it had, and because
    ``begin_generation`` refuses to open a new generation unless the latest
    one is ``reset``, the workflow could never be started again.

    Under the default ``WORKFLOW_CONTROL_MISSING_CONTROLLER=pause``, live
    states converge to ``paused``: a killed workflow (terminated in the
    Temporal UI, crashed server, retention-deleted execution) stays
    user-recoverable — Resume rebuilds the controller from the durable
    row + graph snapshot — whereas ``failed`` forces a Reset that
    archives conversation state. ``fail`` preserves the legacy
    Reset-only behaviour; ``starting`` rows always fail (nothing durable
    is running yet).
    """
    if control.status not in _MISSING_CONTROLLER_FAILS:
        return control
    if (
        control.status in _MISSING_CONTROLLER_PAUSES
        and _missing_controller_policy() == "pause"
    ):
        if control.status == "paused":
            # Already user-resumable; Resume performs the rebuild.
            return control
        logger.warning(
            "Workflow controller execution is gone; pausing the generation so "
            "the user can resume it (Resume rebuilds the controller)",
            workflow_id=control.workflow_id,
            controller_workflow_id=control.controller_workflow_id,
            status=control.status,
            generation=control.generation,
        )
        try:
            recovered = await service.transition(
                control,
                expected_revision=control.revision,
                from_statuses={control.status},
                status="paused",
                values={
                    "pause_reason": PAUSE_REASON_CONTROLLER_MISSING,
                    "pause_detail": "Paused because its background process stopped. Resume to restart it.",
                },
            )
        except ValueError:
            # Lost the CAS to a concurrent writer; their transition wins.
            return control
        await _broadcast_control(recovered, extra={"recovery": "controller_missing"})
        return recovered
    logger.warning(
        "Workflow controller execution is gone; failing the generation so it "
        "can be reset",
        workflow_id=control.workflow_id,
        controller_workflow_id=control.controller_workflow_id,
        status=control.status,
        generation=control.generation,
    )
    try:
        failed = await service.fail(control, "controller_execution_missing")
    except ValueError:
        # Lost the CAS to a concurrent writer, which means someone else has
        # already moved this row. Their transition wins; report what we have.
        return control
    await _broadcast_control(failed)
    return failed


def _generation_visibility_query(control) -> str:
    """Visibility query for controller descendants and standalone trigger roots."""
    if control.controller_workflow_id:
        root_id = str(control.controller_workflow_id).replace("'", "''")
        workflow_id = str(control.workflow_id).replace("'", "''")
        return (
            f"(RootWorkflowId='{root_id}' OR "
            f"EventWorkflowId='{workflow_id}') "
            "AND ExecutionStatus='Running'"
        )
    workflow_id = str(control.workflow_id).replace("'", "''")
    return f"EventWorkflowId='{workflow_id}' AND ExecutionStatus='Running'"


def _visibility_literal(value: Any) -> str:
    return str(value).replace("'", "''")


async def _list_generation_workflows(client, control) -> list[Any]:
    """Resolve tagged roots and every running descendant in those trees.

    Temporal Search Attributes are not inherited by child workflows. The
    first query therefore discovers the controller tree plus tagged standalone
    trigger/graph roots; a second batched RootWorkflowId query expands those
    roots to active Agent/DelegatedTask descendants.
    """
    targets: Dict[tuple[str, str], Any] = {}
    root_ids: set[str] = set()

    async for execution in client.list_workflows(
        query=_generation_visibility_query(control)
    ):
        execution_id = str(execution.id)
        run_id = str(getattr(execution, "run_id", "") or "")
        targets[(execution_id, run_id)] = execution
        root_ids.add(
            str(getattr(execution, "root_id", None) or execution_id)
        )

    ordered_roots = sorted(root_ids)
    batch_size = 40
    for offset in range(0, len(ordered_roots), batch_size):
        batch = ordered_roots[offset : offset + batch_size]
        root_values = ", ".join(
            f"'{_visibility_literal(root_id)}'"
            for root_id in batch
        )
        query = (
            f"RootWorkflowId IN ({root_values}) "
            "AND ExecutionStatus='Running'"
        )
        async for execution in client.list_workflows(query=query):
            execution_id = str(execution.id)
            run_id = str(getattr(execution, "run_id", "") or "")
            targets[(execution_id, run_id)] = execution

    return list(targets.values())


def _temporal_target_already_gone(exc: Exception) -> bool:
    try:
        from temporalio.service import RPCError, RPCStatusCode

        if isinstance(exc, RPCError) and exc.status == RPCStatusCode.NOT_FOUND:
            return True
    except Exception:
        pass
    message = str(exc).lower()
    return "not found" in message or "already completed" in message


async def _signal_generation_workflows(
    control,
    signal_name: str,
    *,
    strict: bool = False,
) -> int:
    """Best-effort cooperative fan-out to this deployment's live executions.

    Visibility is discovery only; durable control state remains authoritative.
    Every matching workflow receives an idempotent pause/resume flag mutation.
    """
    from core.container import container

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        if strict and container.settings().temporal_enabled:
            raise TemporalControlUnavailable("temporal_control_unavailable")
        return 0
    signalled = 0
    failures: list[Exception] = []
    try:
        executions = await _list_generation_workflows(
            wrapper.client,
            control,
        )
        for execution in executions:
            try:
                await wrapper.client.get_workflow_handle(
                    execution.id, run_id=execution.run_id
                ).signal(signal_name)
                signalled += 1
            except Exception as exc:
                if not _temporal_target_already_gone(exc):
                    failures.append(exc)
                logger.warning(
                    "Workflow control signal failed",
                    workflow_id=control.workflow_id,
                    temporal_workflow_id=execution.id,
                    signal=signal_name,
                    error=str(exc),
                )
    except Exception as exc:
        logger.warning(
            "Workflow control visibility fan-out failed",
            workflow_id=control.workflow_id,
            signal=signal_name,
            error=str(exc),
        )
        if strict:
            raise RuntimeError(
                "workflow_signal_visibility_failed"
            ) from exc
    if strict and failures:
        raise RuntimeError(
            f"workflow_signal_failed:{len(failures)}"
        ) from failures[0]
    return signalled


async def _terminate_generation_workflows(control, *, strict: bool = False) -> int:
    """Immediately terminate every visible execution in one application tree."""
    from core.container import container

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        if strict and container.settings().temporal_enabled:
            raise TemporalControlUnavailable("temporal_control_unavailable")
        return 0
    terminated = 0
    failures: list[Exception] = []
    try:
        executions = await _list_generation_workflows(
            wrapper.client,
            control,
        )
        for execution in executions:
            try:
                await wrapper.client.get_workflow_handle(
                    execution.id, run_id=execution.run_id
                ).terminate(reason="workflow_reset")
                terminated += 1
            except Exception as exc:
                if not _temporal_target_already_gone(exc):
                    failures.append(exc)
                logger.warning(
                    "Workflow reset termination failed",
                    workflow_id=control.workflow_id,
                    temporal_workflow_id=execution.id,
                    error=str(exc),
                )
    except Exception as exc:
        logger.warning(
            "Workflow reset visibility scan failed",
            workflow_id=control.workflow_id,
            error=str(exc),
        )
        if strict:
            raise RuntimeError("workflow_visibility_cleanup_failed") from exc
    if strict and failures:
        raise RuntimeError(
            f"workflow_termination_failed:{len(failures)}"
        ) from failures[0]
    return terminated


def _expected_revision(data: Dict[str, Any], control) -> int:
    supplied = data.get("expected_revision")
    if supplied is None:
        raise ValueError("expected_revision_required")
    return int(supplied)


_AUTOMATIC_PAUSE_REASONS = frozenset({PAUSE_REASON_FAILURES, PAUSE_REASON_RECOVERY, PAUSE_REASON_CONTROLLER_MISSING})


def _automatic_pause_values(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """``pause_reason`` / ``pause_detail`` for a pause the server started on
    its own (``_pause_reason`` in a server-side call), or None."""
    reason = data.get("_pause_reason")
    if reason not in _AUTOMATIC_PAUSE_REASONS:
        return None
    detail = str(data.get("_pause_detail") or "").strip()[:500] or None
    return {"pause_reason": reason, "pause_detail": detail}


def _clear_pause_reason_kwargs(control) -> Dict[str, Any]:
    """Transition kwargs that clear an automatic pause's reason, when the
    row has one (running again means the reason no longer applies)."""
    if getattr(control, "pause_reason", None) or getattr(control, "pause_detail", None):
        return {"values": dict(CLEAR_PAUSE_REASON)}
    return {}


def _failure_pause_detail(reason: str) -> str:
    """What the owner reads on a deployment the circuit breaker paused."""
    error = " ".join(str(reason or "").split())[:200]
    base = "Paused after repeated errors. Resume when it's fixed."
    return f"{base} Last error: {error}" if error else base


async def _set_cron_pause(
    workflow_id: str,
    *,
    paused: bool,
    strict: bool = False,
) -> int:
    from core.container import container
    from services.temporal.schedules import set_cron_schedules_paused

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        if strict and container.settings().temporal_enabled:
            raise TemporalControlUnavailable("temporal_control_unavailable")
        return 0
    return await set_cron_schedules_paused(
        wrapper.client,
        workflow_id,
        paused=paused,
        strict=strict,
    )


async def _delete_cron_schedules(
    workflow_id: str,
    *,
    strict: bool = False,
) -> int:
    from core.container import container
    from services.temporal.schedules import (
        delete_cron_schedules_for_deployment,
    )

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        if strict and container.settings().temporal_enabled:
            raise TemporalControlUnavailable("temporal_control_unavailable")
        return 0
    return await delete_cron_schedules_for_deployment(
        wrapper.client,
        workflow_id,
        strict=strict,
    )


async def _with_runtime_counts(payload: Dict[str, Any], workflow_id: str) -> Dict[str, Any]:
    from core.container import container

    status = container.workflow_service().get_deployment_status(workflow_id)
    return {
        **payload,
        "active_count": status.get("active_runs", 0),
        "in_flight_count": status.get("active_runs", 0),
        "queued_count": (
            int(payload.get("queued_count", 0) or 0)
            + int(status.get("queued_events", 0) or 0)
        ),
    }


def _close_local_admission(workflow_id: str) -> None:
    """Synchronously gate legacy trigger callbacks before durable cleanup."""
    from core.container import container

    container.workflow_service().pause_deployment(workflow_id)


async def _control_payload(
    control,
    *,
    extra: Optional[Dict[str, Any]] = None,
    controller_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    payload = serialize_control(control)
    if controller_status is not None:
        payload.update({
            "temporal_state": controller_status.get("state"),
            "temporal_revision": controller_status.get("revision"),
            "queued_count": controller_status.get("queued_events", 0),
            "temporal_available": True,
        })
    payload.update(extra or {})
    return await _with_runtime_counts(payload, control.workflow_id)


async def _broadcast_control(
    control,
    *,
    extra: Optional[Dict[str, Any]] = None,
    controller_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from services.status_broadcaster import get_status_broadcaster

    payload = await _control_payload(
        control,
        extra=extra,
        controller_status=controller_status,
    )
    await get_status_broadcaster().broadcast({
        "type": "workflow_control_status",
        "workflow_id": control.workflow_id,
        "data": payload,
    })
    from services.deployment.control import notify_control_changed

    notify_control_changed(control.workflow_id)
    return payload


async def _reconcile_control(service: WorkflowControlService, control):
    """Finish an interrupted DB transition from acknowledged Temporal state."""
    from core.container import container

    # A generation that has been reset (or failed) closed its controller on
    # purpose, and Temporal deletes closed executions once the namespace
    # retention window passes. Probing one is therefore a guaranteed-failing
    # RPC on every single status read, forever. Terminal generations are
    # answered from the database, which is authoritative for them anyway.
    if control.status not in ACTIVE_STATES:
        return control, None

    try:
        controller_status = await _query_controller_state(control)
    except ControllerExecutionMissing:
        return await _fail_missing_controller(service, control), None

    transition_target = {
        "pausing": ("paused", "paused"),
        "resuming": ("running", "running"),
    }.get(control.status)
    if transition_target is None:
        return control, controller_status
    requested_state, stable_state = transition_target

    if controller_status is None:
        if container.settings().temporal_enabled:
            return control, None
    if control.status == "pausing":
        container.workflow_service().pause_deployment(control.workflow_id)
    if controller_status is not None and controller_status.get("state") != stable_state:
        controller_status = await _update_controller_state(
            control,
            requested_state,
            update_id=(
                f"reconcile:{control.id}:{control.revision}:{stable_state}"
            ),
        )

    if control.status == "pausing":
        paused_schedules = await _set_cron_pause(
            control.workflow_id,
            paused=True,
            strict=True,
        )
        paused_triggers = await container.workflow_service().update_trigger_pause_status(
            control.workflow_id,
            paused=True,
        )
        signalled = await _signal_generation_workflows(
            control,
            "pause",
            strict=True,
        )
        transition_details = {
            "signalled_executions": signalled,
            "paused_schedules": paused_schedules,
            "paused_triggers": paused_triggers,
        }
    else:
        resumed_schedules = await _set_cron_pause(
            control.workflow_id,
            paused=False,
            strict=True,
        )
        signalled = await _signal_generation_workflows(
            control,
            "resume",
            strict=True,
        )
        queued = await container.workflow_service().resume_deployment(
            control.workflow_id,
        )
        resumed_triggers = await container.workflow_service().update_trigger_pause_status(
            control.workflow_id,
            paused=False,
        )
        transition_details = {
            "resumed_queued_events": queued,
            "signalled_executions": signalled,
            "resumed_schedules": resumed_schedules,
            "resumed_triggers": resumed_triggers,
        }
    try:
        control = await service.transition(
            control,
            expected_revision=control.revision,
            from_statuses={control.status},
            status=stable_state,
            **(_clear_pause_reason_kwargs(control) if stable_state == "running" else {}),
        )
        await _broadcast_control(
            control,
            controller_status=controller_status,
            extra=transition_details,
        )
    except ValueError as exc:
        if str(exc) != "control_revision_conflict":
            raise
        latest = await service.database.get_latest_workflow_control(control.workflow_id)
        if latest is not None and latest.generation == control.generation:
            control = latest
    return control, controller_status


# ---------------------------------------------------------------------------
# Boot-time reconcile — called once from services.temporal.lifecycle after
# the workers start. Request-time reconciliation stays lazy; this pass closes
# the unattended-server window where durable intent (running/paused rows) and
# runtime behaviour could diverge indefinitely after a backend restart.
# ---------------------------------------------------------------------------


# Dirty-bit marker for crash detection. Boot stamps "running"; the graceful
# lifespan teardown stamps "clean" (registered as a shutdown hook at the
# bottom of this module). A boot that finds "running" already stamped knows
# the previous process was killed or crashed mid-flight.
_SHUTDOWN_STATE_CACHE_KEY = "workflow_control:shutdown_state"


def _crash_recovery_policy() -> str:
    """Normalized WORKFLOW_CONTROL_CRASH_RECOVERY: ``pause`` | ``resume``."""
    from core.container import container

    value = str(
        getattr(container.settings(), "workflow_control_crash_recovery", "pause") or "pause"
    ).strip().lower()
    if value not in {"pause", "resume"}:
        logger.warning(f"Unknown WORKFLOW_CONTROL_CRASH_RECOVERY={value!r}; using 'pause'")
        return "pause"
    return value


def _missing_controller_policy() -> str:
    """Normalized WORKFLOW_CONTROL_MISSING_CONTROLLER: ``pause`` | ``fail``."""
    from core.container import container

    value = str(
        getattr(container.settings(), "workflow_control_missing_controller", "pause") or "pause"
    ).strip().lower()
    if value not in {"pause", "fail"}:
        logger.warning(f"Unknown WORKFLOW_CONTROL_MISSING_CONTROLLER={value!r}; using 'pause'")
        return "pause"
    return value


async def _consume_shutdown_state() -> bool:
    """True when the previous backend process exited cleanly.

    A missing marker (first boot / pre-feature database) counts as clean
    so a fresh install never boots into a recovery pause.
    ``Database.get_cache_entry`` returns the stored string directly.
    """
    from core.container import container

    database = container.database()
    previous = await database.get_cache_entry(_SHUTDOWN_STATE_CACHE_KEY)
    await database.set_cache_entry(_SHUTDOWN_STATE_CACHE_KEY, "running")
    return previous is None or previous == "clean"


async def mark_clean_shutdown() -> None:
    """Stamp the clean-shutdown marker (lifespan teardown hook)."""
    from core.container import container

    try:
        await container.database().set_cache_entry(_SHUTDOWN_STATE_CACHE_KEY, "clean")
    except Exception as exc:  # noqa: BLE001 — best-effort during teardown
        logger.warning(f"Failed to record clean shutdown marker: {exc}")


async def _pause_for_recovery(service: WorkflowControlService, control, *, reason: str):
    """Durably pause a recovered generation so the user resumes it.

    After a kill or crash the deployment must come back ``paused`` rather
    than silently continuing — the user decides when it is safe to
    resume. Reuses the full pause ceremony (controller update, cron
    schedule pause, trigger flags, local admission) via
    :func:`handle_pause_workflow` so recovery lands in exactly the same
    posture a user-initiated pause produces. Temporal's native
    Pause/Unpause (server 1.28+) is deliberately NOT used here: it is an
    operational control without Python SDK client methods, and it halts
    workflow-task dispatch entirely — a natively-paused controller could
    not process the ``set_control_state`` Update that Resume relies on.
    """
    logger.warning(
        "Pausing recovered generation after unclean shutdown",
        workflow_id=control.workflow_id,
        generation=control.generation,
        reason=reason,
    )
    try:
        result = await handle_pause_workflow(
            {
                "workflow_id": control.workflow_id,
                "expected_revision": control.revision,
                "idempotency_key": f"recovery:{control.id}:{control.revision}",
                "_pause_reason": PAUSE_REASON_RECOVERY,
                "_pause_detail": "Paused after OpenCompany stopped unexpectedly. Resume when you're ready.",
            },
            None,
        )
        if not (isinstance(result, dict) and result.get("success")):
            logger.warning(
                "Recovery pause did not complete",
                workflow_id=control.workflow_id,
                error=(result or {}).get("error") if isinstance(result, dict) else None,
            )
    except Exception as exc:  # noqa: BLE001 — lazy reconcile retries later
        logger.warning(
            "Recovery pause failed",
            workflow_id=control.workflow_id,
            error=str(exc),
        )
    latest = await service.database.get_latest_workflow_control(control.workflow_id)
    return latest if latest is not None else control


async def reconcile_active_controls_on_boot() -> int:
    """Converge every active durable control after a backend restart.

    Responsibilities per active row:

    1. Run the same lazy :func:`_reconcile_control` used by status reads
       (finishes interrupted pause/resume transitions, converges rows
       whose controller vanished).
    2. Converge ``starting`` rows — the one state the lazy path never
       touches while its controller exists, so a crash mid-start used to
       wedge the workflow behind a permanent ``workflow_start_pending``.
       Boot is the only moment guaranteed free of a concurrent in-process
       start, which is why this lives here and not in the lazy path.
    3. Re-arm the process-local half of running/paused generations from
       the persisted graph snapshot: the controller and its Temporal
       children survive a restart on their own, but DeploymentManager
       state (runtime counts, admission, pause fan-in) and in-process
       collectors for non-canary trigger types die with the process and
       previously stayed dead while the row reported ``running`` forever.
    4. After an UNCLEAN shutdown (kill / crash — dirty-bit marker), pause
       every generation still ``running`` so the user consciously
       resumes it (``WORKFLOW_CONTROL_CRASH_RECOVERY=pause``, the
       default; ``resume`` restores them running). A clean
       ``company stop`` + start always restores deployments as they were.

    Returns the number of rows processed; per-row failures are logged and
    skipped so one broken generation cannot block the rest.
    """
    from core.container import container

    database = container.database()
    service = WorkflowControlService(database)
    try:
        clean_shutdown = await _consume_shutdown_state()
    except Exception as exc:  # noqa: BLE001 — marker is best-effort
        logger.warning(f"Shutdown-state marker read failed; assuming clean: {exc}")
        clean_shutdown = True
    recovery_pause = not clean_shutdown and _crash_recovery_policy() == "pause"
    controls = await database.list_active_workflow_controls()
    processed = 0
    for control in controls:
        try:
            control, controller_status = await _reconcile_control(service, control)
            if control.status == "starting":
                control = await _converge_interrupted_start(
                    service, control, controller_status
                )
            if control.status in {"running", "paused"}:
                await _rearm_generation(control)
            if recovery_pause and control.status == "running":
                control = await _pause_for_recovery(
                    service, control, reason="unclean_shutdown"
                )
            processed += 1
        except Exception as exc:  # noqa: BLE001 — per-row isolation
            logger.warning(
                "Boot reconcile failed for workflow control",
                workflow_id=control.workflow_id,
                status=control.status,
                error=str(exc),
            )
    return processed


async def _converge_interrupted_start(
    service: WorkflowControlService,
    control,
    controller_status: Optional[Dict[str, Any]],
):
    """Decide what a crash left behind for a row stuck in ``starting``.

    A vanished controller was already failed by ``_reconcile_control``.
    With the controller alive: triggers registered (or a triggerless
    graph) means the deploy effectively completed — commit ``running``.
    A live-but-empty controller for a graph that declares triggers means
    the crash landed before registration — fail the generation (Reset +
    Start rebuilds cleanly) and close the orphan controller so dispatch
    stops signalling it.
    """
    if controller_status is None:
        # Controller unreachable (not missing) — leave transitional; the
        # lazy path retries when Temporal comes back.
        return control

    from constants import WORKFLOW_TRIGGER_TYPES

    triggers_registered = len(controller_status.get("triggers") or {})
    graph_nodes = (control.graph_snapshot or {}).get("nodes") or []
    graph_has_triggers = any(
        node.get("type") in WORKFLOW_TRIGGER_TYPES for node in graph_nodes
    )
    if triggers_registered or not graph_has_triggers:
        try:
            control = await service.transition(
                control,
                expected_revision=control.revision,
                from_statuses={"starting"},
                status="running",
            )
            await _broadcast_control(control)
        except ValueError:
            # Concurrent writer won the CAS; report what the DB has.
            latest = await service.database.get_latest_workflow_control(control.workflow_id)
            if latest is not None and latest.generation == control.generation:
                control = latest
        return control

    try:
        await _signal_controller(control, "reset")
    except Exception as exc:  # noqa: BLE001 — best-effort close
        logger.warning(
            "Failed to close orphan controller for interrupted start",
            workflow_id=control.workflow_id,
            error=str(exc),
        )
    try:
        failed = await service.fail(control, "interrupted_start")
    except ValueError:
        return control
    await _broadcast_control(failed)
    return failed


async def _rearm_generation(control) -> None:
    """Re-establish process-local deployment state for a durable generation.

    Idempotent by construction: canary triggers re-register with the
    controller keyed by listener id (no-op when already known), legacy
    listener starts use ``id_conflict_policy=USE_EXISTING``, and cron
    schedule creation preserves server-owned paused state on redeploy.
    In-process collectors for non-canary trigger types are re-armed
    fresh (they died with the previous process).
    """
    from core.container import container

    workflow_service = container.workflow_service()
    if workflow_service.is_workflow_deployed(control.workflow_id):
        return
    snapshot = control.graph_snapshot or {}
    nodes = snapshot.get("nodes") or []
    edges = snapshot.get("edges") or []
    if not nodes:
        logger.warning(
            "Cannot re-arm generation without a graph snapshot",
            workflow_id=control.workflow_id,
            generation=control.generation,
        )
        return
    deploy_data = {
        "workflow_id": control.workflow_id,
        "nodes": nodes,
        "edges": edges,
        "generation": control.generation,
        # Snapshots created before the Context topology deliberately retain version 0,
        # so a process restart cannot mutate their Temporal command sequence.
        "graphVersion": int(
            snapshot.get("graphVersion")
            or snapshot.get("graph_version")
            or 0
        ),
        # Runtime persistence is generation-scoped (same contract as
        # handle_start_workflow's deploy call).
        "session_id": control.data_scope_id or control.execution_id,
        "execution_id": control.execution_id,
        "root_execution_id": control.root_execution_id,
        "user_id": str(snapshot.get("owner_id") or "owner"),
    }
    deployed = await handle_deploy_workflow(deploy_data, None)
    if not deployed.get("success"):
        raise RuntimeError(str(deployed.get("error", "deployment_failed")))
    setup = await _await_deployment_setup(control.workflow_id)
    if not setup.get("success"):
        raise RuntimeError(str(setup.get("error", "deployment_setup_failed")))
    if control.status == "paused":
        # Restore the paused posture on the freshly re-armed local half.
        workflow_service.pause_deployment(control.workflow_id)
        await workflow_service.update_trigger_pause_status(
            control.workflow_id,
            paused=True,
        )
        await _set_cron_pause(control.workflow_id, paused=True, strict=False)
        # The deploy handler just broadcast executing=true / status=running
        # on the wire; counter-broadcast the paused posture so a connected
        # client doesn't watch the workflow flip to running and never back.
        from services.status_broadcaster import get_status_broadcaster

        broadcaster = get_status_broadcaster()
        await broadcaster.update_workflow_status(
            executing=False, current_node=None, progress=0,
            workflow_id=control.workflow_id,
        )
        await broadcaster.update_deployment_status(
            is_running=True, status="paused", active_runs=0,
            workflow_id=control.workflow_id,
        )
    logger.info(
        "Re-armed durable generation after restart",
        workflow_id=control.workflow_id,
        generation=control.generation,
        status=control.status,
    )


async def _await_deployment_setup(workflow_id: str) -> Dict[str, Any]:
    """Wait for trigger/listener setup started by the legacy deploy handler."""
    task = _deployment_tasks.get(workflow_id)
    if task is None:
        return {
            "success": False,
            "error": "deployment_setup_task_missing",
            "workflow_id": workflow_id,
        }
    result = await asyncio.shield(task)
    if isinstance(result, dict):
        return result
    return {
        "success": False,
        "error": "deployment_setup_did_not_return_status",
        "workflow_id": workflow_id,
    }


async def _restore_control_after_failed_update(
    service: WorkflowControlService,
    control,
    *,
    transitional_state: str,
    stable_state: str,
):
    """Undo the DB projection when Temporal rejected a lifecycle update."""
    try:
        restored = await service.transition(
            control,
            expected_revision=control.revision,
            from_statuses={transitional_state},
            status=stable_state,
            # A pause that did not happen leaves no reason behind.
            **(_clear_pause_reason_kwargs(control) if stable_state == "running" else {}),
        )
    except ValueError:
        latest = await service.database.get_latest_workflow_control(control.workflow_id)
        restored = latest if latest is not None else control
    await _broadcast_control(restored)


async def _rebuild_missing_controller(service: WorkflowControlService, control):
    """Start a fresh controller for a live generation whose previous one died.

    Resume path for a killed workflow: the generation converged to
    ``paused`` when its controller vanished (terminated in the Temporal
    UI, crashed server, retention-deleted execution); Resume calls this
    to rebuild before applying the running state. Same generation-scoped
    controller workflow id, fresh run chain (``USE_EXISTING`` conflict
    policy makes it race-safe against a mis-detected live controller),
    then the local re-arm re-registers canary triggers with the fresh
    controller from the persisted graph snapshot.
    """
    from core.container import container

    logger.warning(
        "Rebuilding missing workflow controller",
        workflow_id=control.workflow_id,
        controller_workflow_id=control.controller_workflow_id,
        generation=control.generation,
    )
    run_id = await _start_controller(control, use_existing=True)
    if run_id:
        control = await service.transition(
            control,
            expected_revision=control.revision,
            from_statuses={control.status},
            status=control.status,
            values={"controller_run_id": run_id},
        )
        await service.database.update_workflow_run_data_scope(
            control.data_scope_id or control.execution_id,
            temporal_run_id=run_id,
        )
    # Local trigger state may still be armed against the dead controller —
    # tear it down so the re-arm registers with the fresh one. The
    # "not deployed" error from a cold process is expected and benign.
    workflow_service = container.workflow_service()
    if workflow_service.is_workflow_deployed(control.workflow_id):
        try:
            await handle_cancel_deployment({"workflow_id": control.workflow_id}, None)
        except Exception as exc:  # noqa: BLE001 — re-arm still proceeds
            logger.warning(
                "Local cancel before controller rebuild re-arm failed",
                workflow_id=control.workflow_id,
                error=str(exc),
            )
    await _rearm_generation(control)
    await _broadcast_control(control, extra={"recovery": "controller_rebuilt"})
    return control


async def _duplicate_start_response(
    control,
    *,
    controller_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Report the durable outcome of a retried Start idempotency key."""
    payload = await _control_payload(
        control,
        controller_status=controller_status,
    )
    if control.status == "starting":
        return {
            "success": False,
            "error": "workflow_start_pending",
            "idempotent": True,
            **payload,
        }
    if control.status == "failed":
        return {
            "success": False,
            "error": control.terminal_reason or "workflow_start_failed",
            "idempotent": True,
            **payload,
        }
    return {"success": True, "idempotent": True, **payload}


@ws_handler("workflow_id")
async def handle_get_workflow_control_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    service = _control_service()
    control = await service.database.get_latest_workflow_control(data["workflow_id"])
    if control is None:
        return await _with_runtime_counts(
            serialize_control(None),
            data["workflow_id"],
        )
    control, controller_status = await _reconcile_control(service, control)
    return await _control_payload(
        control,
        controller_status=controller_status,
        extra={
            "temporal_available": (
                controller_status is not None
                if control.controller_run_id
                else False
            ),
        },
    )


@ws_handler("workflow_id")
async def handle_start_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Create generation one and retain deploy_workflow wire compatibility."""
    workflow_id = data["workflow_id"]
    # A socket's identity wins; a server-side call (no socket, e.g.
    # start_saved_workflow) names the owner in the payload.
    owner_id = str(
        (getattr(getattr(websocket, "state", None), "user_id", None) if websocket is not None else data.get("user_id"))
        or "owner"
    )
    key = data.get("idempotency_key") or f"start:{workflow_id}:{uuid.uuid4().hex}"
    service = _control_service()
    existing = await service.database.get_workflow_control_by_idempotency_key(
        workflow_id,
        key,
    )
    if existing is not None:
        existing, controller_status = await _reconcile_control(service, existing)
        return await _duplicate_start_response(
            existing,
            controller_status=controller_status,
        )

    latest = await service.database.get_latest_workflow_control(workflow_id)
    if data.get("expected_revision") is None:
        raise ValueError("expected_revision_required")
    expected_revision = int(data["expected_revision"])
    if expected_revision != (latest.revision if latest else 0):
        raise ValueError("control_revision_conflict")
    if latest is not None and latest.status != "reset":
        raise ValueError("workflow_already_started")

    # Admit exactly the normalized, sanitized V2 graph.  Doing this after the
    # control row was created would leave restart recovery with a legacy
    # snapshot/hash even though the live deployment had already migrated.
    from services.workflow_context_migration import (
        import_legacy_context_receipts,
        load_node_parameters,
        persist_parameter_aliases,
    )
    from services.workflow_migrations import normalize_workflow_graph
    from services.workflow_sanitizer import sanitize_workflow_graph

    raw_nodes = list(data.get("nodes") or [])
    raw_edges = list(data.get("edges") or [])
    parameters_by_id = data.get("parameters_by_id")
    if not isinstance(parameters_by_id, dict):
        parameters_by_id = await load_node_parameters(
            service.database,
            raw_nodes,
        )
    normalization = normalize_workflow_graph(
        workflow_id,
        raw_nodes,
        raw_edges,
        parameters_by_id,
    )
    safe_graph = sanitize_workflow_graph(normalization.graph_data())
    admitted_nodes = list(safe_graph["nodes"])
    admitted_edges = list(safe_graph["edges"])
    from services.workflow_validator import validate_workflow

    validation = await validate_workflow(
        nodes=admitted_nodes,
        edges=admitted_edges,
        parameters_by_id=normalization.node_parameters,
    )
    if validation["errors"]:
        return {
            "success": False,
            "error": "validation_failed",
            "report": validation,
            "graph": safe_graph,
            "aliases": normalization.aliases,
            "migration_warnings": normalization.warnings,
        }
    await import_legacy_context_receipts(
        service.database,
        normalization.state_imports,
    )
    await persist_parameter_aliases(
        service.database,
        aliases=normalization.aliases,
        parameters=normalization.node_parameters,
    )
    control, created = await service.begin_generation(
        workflow_id=workflow_id,
        nodes=admitted_nodes,
        edges=admitted_edges,
        session_id=data.get("session_id", "default"),
        idempotency_key=key,
        graph_version=normalization.graph_version,
        owner_id=owner_id,
    )
    if not created:
        control, controller_status = await _reconcile_control(service, control)
        return await _duplicate_start_response(
            control,
            controller_status=controller_status,
        )
    await _broadcast_control(control)
    try:
        run_id = await _start_controller(control)
        if run_id:
            control = await service.transition(
                control, expected_revision=control.revision, from_statuses={"starting"}, status="starting",
                values={"controller_run_id": run_id},
            )
            await service.database.update_workflow_run_data_scope(
                control.data_scope_id or control.execution_id, temporal_run_id=run_id,
            )
            await _broadcast_control(control)
        # Runtime persistence is generation-scoped. The caller's session is
        # retained on the scope for provenance, but must never namespace node
        # outputs for a controlled run.
        deploy_data = {
            **data,
            "nodes": admitted_nodes,
            "edges": admitted_edges,
            "parameters_by_id": normalization.node_parameters,
            "graphVersion": normalization.graph_version,
            "generation": control.generation,
            "session_id": control.data_scope_id or control.execution_id,
            "execution_id": control.execution_id,
            "root_execution_id": control.root_execution_id,
            "user_id": owner_id,
        }
        deployed = await handle_deploy_workflow(deploy_data, websocket)
        if not deployed.get("success"):
            raise RuntimeError(str(deployed.get("error", "deployment_failed")))
        deployed = await _await_deployment_setup(workflow_id)
        if not deployed.get("success"):
            raise RuntimeError(str(deployed.get("error", "deployment_failed")))
        control = await service.transition(control, expected_revision=control.revision, from_statuses={"starting"}, status="running")
    except Exception as exc:
        latest = await service.database.get_latest_workflow_control(workflow_id)
        if latest is not None and latest.generation == control.generation:
            control = latest
        if control.status == "starting":
            control = await service.fail(control, str(exc))
        try:
            await _signal_controller(control, "reset")
        except Exception as reset_exc:
            logger.warning(
                "Failed to reset controller after deployment setup error",
                workflow_id=workflow_id,
                error=str(reset_exc),
            )
        await _terminate_generation_workflows(control)
        # Tear down the local half too: the deploy may have armed
        # listeners and taken the workflow lock, and leaving them meant a
        # locked, armed, invisible deployment that blocked every future
        # Start AND the boot re-arm for this workflow. Cancel unlocks and
        # emits the terminal UI-facing state; "not deployed" is benign.
        try:
            await handle_cancel_deployment({"workflow_id": workflow_id}, None)
        except Exception as cancel_exc:  # noqa: BLE001 — best-effort teardown
            logger.warning(
                "Local teardown after failed start did not complete",
                workflow_id=workflow_id,
                error=str(cancel_exc),
            )
        await _broadcast_control(control)
        raise

    # Reaching ``running`` commits successful deployment setup. Projection
    # failures after that point must not tear down a live durable generation;
    # the client can recover the committed state through its status resync.
    try:
        controller_status = await _query_controller_state(control)
    except ControllerExecutionMissing:
        # Same rule as the comment above: this is a projection read taken
        # after the generation committed. Reporting no controller state is
        # acceptable; propagating and unwinding a live generation is not.
        controller_status = None
    payload = await _broadcast_control(
        control,
        controller_status=controller_status,
    )
    return {
        "success": True,
        **payload,
        "graph": {
            "graphVersion": normalization.graph_version,
            "nodes": admitted_nodes,
            "edges": admitted_edges,
        },
        "aliases": normalization.aliases,
        "migration_warnings": normalization.warnings,
    }


async def start_saved_workflow(
    workflow_id: str,
    *,
    owner_id: str,
    expected_revision: Optional[int] = None,
    idempotency_key: Optional[str] = None,
    reset_if_failed: bool = False,
) -> Dict[str, Any]:
    """Start a saved workflow from the server, without the editor.

    Loads the saved graph and goes through ``handle_start_workflow`` exactly
    as the editor's Start does (normalize, validate, admit a generation,
    deploy), so both starts produce the same generation. With no
    ``expected_revision`` the latest control revision is used. A generation
    that failed must be Reset before it can start again; ``reset_if_failed``
    does that first. Returns the start handler's envelope.
    """
    from core.container import container

    workflow = await container.database().get_workflow(workflow_id)
    if workflow is None:
        return {"success": False, "error": "workflow_not_found"}
    graph = workflow.data if isinstance(workflow.data, dict) else {}
    service = _control_service()
    latest = await service.database.get_latest_workflow_control(workflow_id)
    if latest is not None and latest.status == "failed" and reset_if_failed:
        reset = await handle_reset_workflow(
            {
                "workflow_id": workflow_id,
                "expected_revision": latest.revision,
                "idempotency_key": f"{idempotency_key or uuid.uuid4().hex}:reset",
            },
            None,
        )
        if not reset.get("success"):
            return reset
        latest = await service.database.get_latest_workflow_control(workflow_id)
    revision = expected_revision if expected_revision is not None else (latest.revision if latest else 0)
    return await handle_start_workflow(
        {
            "workflow_id": workflow_id,
            "nodes": list(graph.get("nodes") or []),
            "edges": list(graph.get("edges") or []),
            "expected_revision": revision,
            "idempotency_key": idempotency_key or f"start:{workflow_id}:{uuid.uuid4().hex}",
            "user_id": owner_id,
        },
        None,
    )


@ws_handler("workflow_id")
async def handle_pause_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    workflow_id = data["workflow_id"]
    service = _control_service()
    control = await service.database.get_latest_workflow_control(workflow_id)
    if control is None:
        return {"success": False, "error": "workflow_never_started"}
    control, controller_status = await _reconcile_control(service, control)
    if control.status == "paused":
        return {
            "success": True,
            "idempotent": True,
            **await _control_payload(control, controller_status=controller_status),
        }
    if control.status == "pausing":
        return {
            "success": False,
            "error": "workflow_control_transition_pending",
            **await _control_payload(control, controller_status=controller_status),
        }
    # Only server-side callers (no socket) may say why a pause happened: a
    # client must not be able to dress its own pause up as an automatic one.
    reason_values = _automatic_pause_values(data) if websocket is None else None
    transition_kwargs: Dict[str, Any] = {"values": reason_values} if reason_values else {}
    control = await service.transition(
        control,
        expected_revision=_expected_revision(data, control),
        from_statuses={"running"},
        status="pausing",
        **transition_kwargs,
    )
    await _broadcast_control(control)
    container.workflow_service().pause_deployment(workflow_id)
    try:
        controller_status = await _update_controller_state(
            control,
            "paused",
            update_id=(
                f"{control.id}:{control.revision}:"
                f"{data.get('idempotency_key') or uuid.uuid4().hex}:paused"
            ),
        )
    except (TemporalControlUnavailable, TemporalControlAckMismatch):
        await container.workflow_service().resume_deployment(workflow_id)
        await _restore_control_after_failed_update(
            service,
            control,
            transitional_state="pausing",
            stable_state="running",
        )
        raise
    paused_schedules = await _set_cron_pause(
        workflow_id,
        paused=True,
        strict=True,
    )
    paused_triggers = await container.workflow_service().update_trigger_pause_status(workflow_id, paused=True)
    signalled = await _signal_generation_workflows(
        control,
        "pause",
        strict=True,
    )
    control = await service.transition(control, expected_revision=control.revision, from_statuses={"pausing"}, status="paused")
    # UI-facing execution flags follow the pause: the deployment stays
    # armed but nothing new executes, so the canvas edge animation and
    # toolbar executing indicator must stop. The pause family previously
    # emitted only workflow_control_status, leaving `executing=true` /
    # `status=running` stale on every connected client.
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    await broadcaster.update_workflow_status(
        executing=False, current_node=None, progress=0, workflow_id=workflow_id,
    )
    await broadcaster.update_deployment_status(
        is_running=True, status="paused", active_runs=0, workflow_id=workflow_id,
    )
    payload = await _broadcast_control(control, controller_status=controller_status, extra={
        "signalled_executions": signalled,
        "paused_schedules": paused_schedules,
        "paused_triggers": paused_triggers,
    })
    return {
        "success": True, "signalled_executions": signalled,
        "paused_schedules": paused_schedules, "paused_triggers": paused_triggers,
        **payload,
    }


@ws_handler("workflow_id")
async def handle_resume_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    from core.container import container

    workflow_id = data["workflow_id"]
    service = _control_service()
    control = await service.database.get_latest_workflow_control(workflow_id)
    if control is None:
        return {"success": False, "error": "workflow_never_started"}
    control, controller_status = await _reconcile_control(service, control)
    if control.status == "running":
        return {
            "success": True,
            "idempotent": True,
            **await _control_payload(control, controller_status=controller_status),
        }
    if control.status == "resuming":
        return {
            "success": False,
            "error": "workflow_control_transition_pending",
            **await _control_payload(control, controller_status=controller_status),
        }
    control = await service.transition(
        control, expected_revision=_expected_revision(data, control), from_statuses={"paused"}, status="resuming"
    )
    await _broadcast_control(control)
    resume_update_key = data.get("idempotency_key") or uuid.uuid4().hex

    async def _apply_resume_update(target):
        return await _update_controller_state(
            target,
            "running",
            update_id=f"{target.id}:{target.revision}:{resume_update_key}:running",
        )

    try:
        controller_status = await _apply_resume_update(control)
    except (TemporalControlUnavailable, TemporalControlAckMismatch):
        await _restore_control_after_failed_update(
            service,
            control,
            transitional_state="resuming",
            stable_state="paused",
        )
        raise
    except Exception as exc:
        if not _temporal_target_already_gone(exc):
            raise
        # The controller execution is gone (killed in the Temporal UI /
        # crashed / retention-deleted). Under the missing-controller pause
        # policy the row converged to paused precisely so this Resume can
        # rebuild the controller from the durable row + graph snapshot,
        # then re-apply the running state against the fresh execution.
        try:
            control = await _rebuild_missing_controller(service, control)
            controller_status = await _apply_resume_update(control)
        except Exception as rebuild_exc:  # noqa: BLE001 — restore + surface
            logger.warning(
                "Controller rebuild during resume failed",
                workflow_id=workflow_id,
                error=str(rebuild_exc),
            )
            await _restore_control_after_failed_update(
                service,
                control,
                transitional_state="resuming",
                stable_state="paused",
            )
            raise
    resumed_schedules = await _set_cron_pause(
        workflow_id,
        paused=False,
        strict=True,
    )
    signalled = await _signal_generation_workflows(
        control,
        "resume",
        strict=True,
    )
    queued = await container.workflow_service().resume_deployment(workflow_id)
    resumed_triggers = await container.workflow_service().update_trigger_pause_status(workflow_id, paused=False)
    control = await service.transition(
        control,
        expected_revision=control.revision,
        from_statuses={"resuming"},
        status="running",
        **_clear_pause_reason_kwargs(control),
    )
    # Operator intervention resets the circuit-breaker streak — the next
    # failure after a resume starts a fresh count, not a near-tripped one.
    await _clear_failure_streak(service.database, control)
    # Mirror of the pause-side emission: flip the UI-facing execution
    # flags back so the canvas and toolbar re-animate on every client.
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()
    await broadcaster.update_workflow_status(
        executing=True, current_node=None, progress=0, workflow_id=workflow_id,
    )
    await broadcaster.update_deployment_status(
        is_running=True, status="running", active_runs=0, workflow_id=workflow_id,
    )
    payload = await _broadcast_control(control, controller_status=controller_status, extra={
        "resumed_queued_events": queued,
        "signalled_executions": signalled,
        "resumed_schedules": resumed_schedules,
        "resumed_triggers": resumed_triggers,
    })
    return {
        "success": True, "resumed_queued_events": queued, "signalled_executions": signalled,
        "resumed_schedules": resumed_schedules, "resumed_triggers": resumed_triggers,
        **payload,
    }


@ws_handler("workflow_id")
async def handle_reset_workflow(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    workflow_id = data["workflow_id"]
    service = _control_service()
    current = await service.database.get_latest_workflow_control(workflow_id)
    if current is None:
        return await _with_runtime_counts(
            await service.get_status(workflow_id),
            workflow_id,
        )

    # ``reset`` is a completed cleanup barrier. Re-running generation-wide
    # sweeps here would race a concurrent Start and could terminate resources
    # from the next generation because standalone triggers use the stable
    # application workflow id.
    if current.status == "reset":
        return {
            "success": True,
            "idempotent": True,
            **await _control_payload(current),
        }

    if current.status != "resetting":
        current = await service.transition(
            current, expected_revision=_expected_revision(data, current),
            from_statuses={"starting", "running", "pausing", "paused", "resuming", "failed"}, status="resetting",
            values={"terminal_reason": "workflow_reset", "completed_at": datetime.now(timezone.utc)},
        )

    # Quiesce every producer before the final execution sweep. The local gate
    # is synchronous, so no callback can be admitted while broadcasts or
    # Temporal cleanup yield control.
    _close_local_admission(workflow_id)
    await _broadcast_control(current)

    try:
        await _signal_controller(current, "reset")
    except Exception as exc:
        logger.warning(
            "Workflow reset signal failed; continuing with termination",
            workflow_id=workflow_id,
            error=str(exc),
        )

    # Remove cron producers before terminating executions; otherwise a firing
    # between the execution scan and schedule deletion could survive Reset.
    deleted_schedules = await _delete_cron_schedules(
        workflow_id,
        strict=True,
    )
    cancelled = await handle_cancel_deployment(
        {"workflow_id": workflow_id},
        websocket,
    )
    # Durable Temporal cleanup above is authoritative. The process-local
    # deployment may legitimately be absent after an API-server restart.
    # Any other local teardown failure can leave a listener/admission path
    # alive, so keep the durable control in ``resetting`` for a safe retry.
    local_cleanup_completed = bool(cancelled.get("success"))
    if not local_cleanup_completed:
        # ``handle_cancel_deployment`` retains its historical envelope and
        # exposes manager errors as ``message``. Accept ``error`` as well for
        # direct/internal callers and future wire compatibility.
        local_error = str(
            cancelled.get("error")
            or cancelled.get("message")
            or "unknown"
        )
        expected_absent_error = f"Workflow {workflow_id} is not deployed"
        if local_error != expected_absent_error:
            raise RuntimeError(
                f"workflow_local_cleanup_failed:{local_error}"
            )

    # Controller, cron, and legacy local admission paths are now closed. This
    # final strict sweep therefore observes a fixed set of generation runs.
    terminated = await _terminate_generation_workflows(current, strict=True)

    archived = await service.database.update_workflow_run_data_scope(
        current.data_scope_id or current.execution_id,
        status="archived", archived_at=datetime.now(timezone.utc),
    )
    if not archived:
        raise RuntimeError("workflow_data_scope_archive_failed")

    from services.status_broadcaster import get_status_broadcaster
    from services.deployment.runtime_state import archive_and_reset_node_state
    broadcaster = get_status_broadcaster()
    node_state = await archive_and_reset_node_state(
        current, service.database, broadcaster,
    )

    current = await service.transition(
        current, expected_revision=current.revision, from_statuses={"resetting"}, status="reset",
        values={"terminal_reason": "workflow_reset", "completed_at": datetime.now(timezone.utc)},
    )
    await broadcaster.broadcast({
        "type": "workflow_runtime_reset",
        "workflow_id": workflow_id,
        "generation": current.generation,
        "data_scope_id": current.data_scope_id or current.execution_id,
        "archived_nodes": node_state["archived_nodes"],
        "reset_nodes": node_state["reset_nodes"],
    })
    payload = await _broadcast_control(current, extra={
        "terminated_executions": terminated,
        "deleted_schedules": deleted_schedules,
        "local_cleanup_completed": local_cleanup_completed,
        "archived_nodes": node_state["archived_nodes"],
        "reset_nodes": node_state["reset_nodes"],
    })
    return {
        "success": True,
        "idempotent": False,
        "terminated_executions": terminated,
        "deleted_schedules": deleted_schedules,
        "local_cleanup_completed": local_cleanup_completed,
        "archived_nodes": node_state["archived_nodes"],
        "reset_nodes": node_state["reset_nodes"],
        **payload,
    }


def _pause_on_failure_threshold() -> int:
    """Failures within the rolling window required to trip the breaker.

    Default 3 — a single node hiccup on one firing (missing config,
    transient API error) must never pause a live deployment; only a
    sustained failure streak does. 1 restores pause-on-first-failure.
    """
    from core.container import container

    try:
        value = int(getattr(container.settings(), "workflow_control_pause_on_failure_threshold", 3))
    except (TypeError, ValueError):
        value = 3
    return max(1, value)


def _pause_on_failure_window_seconds() -> float:
    """Rolling window for the failure streak; older failures age out."""
    from core.container import container

    try:
        value = float(
            getattr(container.settings(), "workflow_control_pause_on_failure_window_seconds", 600.0)
        )
    except (TypeError, ValueError):
        value = 600.0
    return max(1.0, value)


def _failure_streak_key(control) -> str:
    # Keyed by the control row id, which embeds workflow + generation —
    # a Reset/Start naturally begins with a fresh streak.
    return f"workflow_control:failure_streak:{control.id}"


async def _bump_failure_streak(database, control, *, window_seconds: float) -> int:
    """Increment the rolling-window failure counter and return it.

    State lives in the durable cache table (value is a JSON blob; the
    TTL doubles as the window so stale streaks age out even if the
    timestamp check is bypassed). Concurrent failed runs can race the
    read-modify-write and miscount by one — acceptable slack for a
    breaker threshold.
    """
    key = _failure_streak_key(control)
    now = time.time()
    count = 0
    raw = await database.get_cache_entry(key)
    if raw:
        try:
            state = json.loads(raw)
            if now - float(state.get("last_at") or 0.0) <= window_seconds:
                count = int(state.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
    count += 1
    await database.set_cache_entry(
        key,
        json.dumps({"count": count, "last_at": now}),
        int(window_seconds),
    )
    return count


async def _clear_failure_streak(database, control) -> None:
    try:
        await database.delete_cache_entry(_failure_streak_key(control))
    except Exception as exc:  # noqa: BLE001 — the TTL ages it out anyway
        logger.debug(f"Failed to clear failure streak: {exc}")


async def pause_generation_on_failure(*, workflow_id: str, reason: str) -> Dict[str, Any]:
    """Circuit breaker: pause a controlled deployment after failed runs.

    Called by the ``workflow_control.pause_on_failure.v1`` activity that
    MachinaWorkflow schedules on its failure path. Without it a trigger
    keeps firing new runs into the same error indefinitely; with it the
    deployment lands ``paused`` so the user fixes the cause and Resumes.

    Trips only after ``WORKFLOW_CONTROL_PAUSE_ON_FAILURE_THRESHOLD``
    failed runs inside the rolling window — small things (one node
    failing on one firing) never pause the deployment. Gated on
    ``WORKFLOW_CONTROL_PAUSE_ON_FAILURE`` (default true) and applies
    only to a generation currently ``running`` — direct manual runs,
    legacy uncontrolled deployments, and already-paused generations are
    untouched. All knobs are evaluated HERE (activity side) rather than
    inside workflow code so config flips never touch recorded commands.
    """
    from core.container import container

    if not workflow_id:
        return {"paused": False, "reason": "no_workflow_id"}
    if not bool(getattr(container.settings(), "workflow_control_pause_on_failure", True)):
        return {"paused": False, "reason": "disabled"}
    service = _control_service()
    control = await service.database.get_latest_workflow_control(workflow_id)
    if control is None or control.status != "running":
        return {
            "paused": False,
            "reason": "not_running",
            "status": getattr(control, "status", None),
        }
    threshold = _pause_on_failure_threshold()
    if threshold > 1:
        streak = await _bump_failure_streak(
            service.database,
            control,
            window_seconds=_pause_on_failure_window_seconds(),
        )
        if streak < threshold:
            logger.info(
                "Run failure below circuit-breaker threshold; not pausing",
                workflow_id=workflow_id,
                streak=streak,
                threshold=threshold,
                failure=reason,
            )
            return {
                "paused": False,
                "reason": "below_threshold",
                "streak": streak,
                "threshold": threshold,
            }
        await _clear_failure_streak(service.database, control)
    logger.warning(
        "Pausing deployment after failed run (circuit breaker)",
        workflow_id=workflow_id,
        generation=control.generation,
        failure=reason,
    )
    try:
        result = await handle_pause_workflow(
            {
                "workflow_id": workflow_id,
                "expected_revision": control.revision,
                "idempotency_key": f"pause-on-failure:{control.id}:{control.revision}",
                "_pause_reason": PAUSE_REASON_FAILURES,
                "_pause_detail": _failure_pause_detail(reason),
            },
            None,
        )
    except Exception as exc:  # noqa: BLE001 — activity reports, never raises
        logger.warning(
            "Pause-on-failure did not complete",
            workflow_id=workflow_id,
            error=str(exc),
        )
        return {"paused": False, "error": str(exc)}
    paused = bool(isinstance(result, dict) and result.get("success"))
    if not paused:
        logger.warning(
            "Pause-on-failure did not complete",
            workflow_id=workflow_id,
            error=(result or {}).get("error") if isinstance(result, dict) else None,
        )
    return {"paused": paused, "failure": reason}


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


# Clean-shutdown marker for crash detection (see _consume_shutdown_state).
# Registered through the generic lifespan shutdown-hook registry so main.py
# never learns about the control plane's internals.
from services.plugin.shutdown_hooks import register_shutdown_hook  # noqa: E402

register_shutdown_hook("workflow_control_clean_shutdown", mark_clean_shutdown)


__all__ = [
    "WS_HANDLERS",
    "handle_cancel_deployment",
    "handle_deploy_workflow",
    "handle_get_deployment_status",
    "handle_get_workflow_lock",
    "handle_update_deployment_settings",
    "mark_clean_shutdown",
    "pause_generation_on_failure",
    "reconcile_active_controls_on_boot",
]
