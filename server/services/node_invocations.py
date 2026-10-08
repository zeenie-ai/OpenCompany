"""Authoritative admission for direct Workspace tasks. Never accepts client graphs."""

from __future__ import annotations
import asyncio
import hashlib
import json
from typing import Any
from uuid import UUID, uuid4


def invocation_id(principal: str, workflow_id: str, node_id: str, submission_id: str) -> str:
    submission_id = str(UUID(submission_id))
    identity = json.dumps([principal, workflow_id, node_id, submission_id], separators=(",", ":"))
    return "node-invoke-" + hashlib.sha256(identity.encode()).hexdigest()


def temporal_client() -> Any:
    from core.container import container
    from services.plugin import NodeUserError

    wrapper = container.temporal_client()
    if wrapper is None or wrapper.client is None:
        raise NodeUserError("Workflow engine is not ready. Try again after startup completes.")
    return wrapper.client


def controller_id(workflow_id: str) -> str:
    return "workspace-tasks-" + hashlib.sha256(workflow_id.encode()).hexdigest()


async def controller_status(workflow_id: str, *, client=None) -> dict | None:
    from services.temporal.workspace_tasks_workflow import WorkspaceTaskControllerWorkflow
    from temporalio.service import RPCError, RPCStatusCode

    client = client or temporal_client()
    try:
        return await client.get_workflow_handle(controller_id(workflow_id)).query(WorkspaceTaskControllerWorkflow.describe)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            return None
        raise


async def _controller_update(workflow_id: str, update, argument, *, update_id: str):
    from core.config import Settings
    from services.temporal.workspace_tasks_workflow import WorkspaceTaskControllerWorkflow
    from temporalio.client import WithStartWorkflowOperation, WorkflowUpdateFailedError
    from temporalio.common import WorkflowIDConflictPolicy
    from services.plugin import NodeUserError

    try:
        return await temporal_client().execute_update_with_start_workflow(
            update, argument, id=update_id,
            start_workflow_operation=WithStartWorkflowOperation(
                WorkspaceTaskControllerWorkflow.run, {"workflow_id": workflow_id},
                id=controller_id(workflow_id), task_queue=Settings().temporal_task_queue,
                id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            ),
        )
    except WorkflowUpdateFailedError as exc:
        cause = exc.cause
        messages = {
            "WorkspaceResetInProgress": "Workspace tasks are resetting. Wait for Reset to finish.",
            "WorkspaceAdmissionChanged": "This submission crossed a Reset. Submit a new task.",
            "WorkspaceSubmissionConflict": "Submission ID was already used for a different task",
            "WorkspaceTaskQueueFull": "The Workspace task queue is full",
        }
        for _ in range(8):
            if getattr(cause, "type", None) in messages:
                raise NodeUserError(messages[cause.type]) from None
            if getattr(cause, "cause", None) is None:
                break
            cause = cause.cause
        raise


async def workspace_task_nodes(database, workflow_id: str, control=None) -> tuple[dict, list[dict]]:
    """Include frozen nodes removed from the editable graph while paused."""
    from services.node_registry import get_node_class

    saved = await database.get_workflow(workflow_id)
    graph = (saved.data if hasattr(saved, "data") else saved.get("data", saved)) if saved else {}
    # Filter before merging: replacing an old phone node with a non-device
    # node in the editable graph must not hide its frozen cleanup hook.
    candidates = [*(getattr(control, "graph_snapshot", None) or {}).get("nodes", []), *graph.get("nodes", [])]
    nodes = {(node["id"], node["type"]): node for node in candidates
             if node.get("id") and getattr(get_node_class(node.get("type", "")), "workspace_task", False)}
    return graph, list(nodes.values())


async def reset(workflow_id: str, request_id: str) -> dict:
    """Durably cancel admitted tasks and await plugin cleanup before acknowledging."""
    from services.temporal.workspace_tasks_workflow import WorkspaceTaskControllerWorkflow

    state = await controller_status(workflow_id)
    # A failed cleanup keeps its admission fence. Resume that operation even
    # when the user's Retry Reset has a new transport request identity.
    request_id = (state or {}).get("reset_request_id") or request_id
    return await _controller_update(
        workflow_id, WorkspaceTaskControllerWorkflow.reset, request_id,
        update_id="reset:" + uuid4().hex,
    )


async def cancel_legacy_invocations(workflow_id: str) -> int:
    """Migration path for pre-controller roots; Visibility is not new-task admission."""
    from services.temporal.node_invocation import NodeInvocationWorkflow
    from temporalio.service import RPCError, RPCStatusCode

    client = temporal_client()
    targets = []
    async for execution in client.list_workflows(query="WorkflowType='NodeInvocationWorkflow' AND ExecutionStatus='Running'"):
        # New children are cancelled by the durable controller, not a visibility scan.
        if getattr(execution, "parent_id", None):
            continue
        handle = client.get_workflow_handle(execution.id, run_id=execution.run_id)
        try:
            metadata = await handle.query(NodeInvocationWorkflow.describe)
            if metadata.get("workflow_id") == workflow_id:
                targets.append(handle)
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND:
                raise
    await asyncio.gather(*(_cancel_and_wait(handle) for handle in targets))
    return len(targets)


async def _cancel_and_wait(handle) -> None:
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import is_cancelled_exception
    from temporalio.service import RPCError, RPCStatusCode

    try:
        await handle.cancel()
        try:
            await asyncio.wait_for(handle.result(), timeout=180)
        except WorkflowFailureError as exc:
            # Any already-terminal failure has closed the activity's workflow.
            # Unexpected RPC/timeouts still propagate; they are not cleanup receipts.
            if not is_cancelled_exception(exc.cause):
                description = await handle.describe()
                from temporalio.client import WorkflowExecutionStatus
                if description.status == WorkflowExecutionStatus.RUNNING:
                    raise
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise


async def submit(principal: str, workflow_id: str, node_id: str, prompt: str, submission_id: str) -> dict:
    from core.config import Settings
    from services.authz.workflow_node import resolve_workflow_node
    from services.node_registry import get_node_class
    from services.plugin import NodeUserError
    from services.plugin.deps import get_database
    from services.temporal.node_invocation import NodeInvocationWorkflow
    from services.temporal.workspace_tasks_workflow import WorkspaceTaskControllerWorkflow

    # Establish the admission epoch before any saved-graph/authorization await.
    # Reads are harmless; authorization still completes before any mutation.
    # A Reset during graph resolution must reject this original request.
    admission = await controller_status(workflow_id)
    saved, graph, node = await resolve_workflow_node(principal, workflow_id, node_id)
    cls = get_node_class(node["type"])
    if cls is None or not getattr(cls, "workspace_task", False):
        raise NodeUserError("This node does not accept direct Workspace tasks")
    if (node.get("data") or {}).get("disabled"):
        raise NodeUserError("Enable the node before submitting a task")
    if not prompt.strip() or len(prompt) > 20000:
        raise NodeUserError("Task must contain between 1 and 20000 characters")
    database = get_database()
    control = await database.get_latest_workflow_control(workflow_id)
    if (admission or {}).get("resetting") or (control and control.status == "resetting"):
        raise NodeUserError("Workspace tasks are resetting. Wait for Reset to finish.")
    run_id = invocation_id(principal, workflow_id, node_id, submission_id)
    fingerprint = hashlib.sha256(prompt.encode()).hexdigest()
    params = {**(await get_database().get_node_parameters(node_id) or {}), "prompt": prompt}
    settings = Settings()
    context = {
        "node_id": node_id,
        "node_type": node["type"],
        "node_data": params,
        "workflow_id": workflow_id,
        "workflow_slug": getattr(saved, "name", "workflow"),
        "execution_id": run_id,
        "session_id": run_id,
        "user_id": principal,
        "nodes": graph.get("nodes", []),
        "edges": graph.get("edges", []),
        "generation": 0,
        "graphVersion": graph.get("graphVersion", 1),
        "inputs": {},
    }
    payload = {
        "principal": principal,
        "workflow_id": workflow_id,
        "node_id": node_id,
        "fingerprint": fingerprint,
        "activity": f"node.{cls.type}.v{cls.version}",
        "task_queue": cls.task_queue if settings.temporal_worker_pool_enabled else settings.temporal_task_queue,
        "timeout_s": int(cls.start_to_close_timeout.total_seconds()),
        "context": context,
        "admission_epoch": (admission or {}).get("epoch", 0),
    }
    if node["type"] == "browser_agent":
        if getattr(settings, "distributed_mode", False) is True:
            from services.credentials.preflight import assert_cluster_credentials
            await assert_cluster_credentials(database, graph, context)
        from services.browser_tasks import browser_tool_for_agent
        from services.browser_owners import routing_for_node
        browser_node = browser_tool_for_agent(graph, node_id)
        binding = await routing_for_node(database, workflow_id, browser_node["id"], principal)
        snapshot = {saved_node["id"]: await database.get_node_parameters(saved_node["id"]) or {}
                    for saved_node in graph.get("nodes", []) if saved_node.get("id")}
        from services.temporal.agent_activities import _strip_credentials
        snapshot = _strip_credentials(snapshot)
        context["node_data"] = _strip_credentials(context["node_data"])
        context["nodes"] = _strip_credentials(context["nodes"])
        context.update(native_workspace_version=1, workspace_task_prompt=prompt,
                       parameter_snapshot=snapshot, temporal_worker_pool_enabled=settings.temporal_worker_pool_enabled,
                       browser_bindings={browser_node["id"]: binding}, _browser_task_id=run_id)
        payload.update(dispatch_version=1, dispatch_kind="native_agent", history_version=1,
                       history_record={"invocation_id": run_id, "submission_id": str(UUID(submission_id)),
                                       "principal": principal, "workflow_id": workflow_id, "node_id": node_id,
                                       "fingerprint": fingerprint, "prompt": prompt,
                                       "browser_owner_ids": [binding["owner_id"]] if binding.get("task_queue") else []})
    client = temporal_client()
    # The controller owns submission idempotence. A new Update ID permits a
    # retry after queue-full/reset rejection; a completed failed Update would
    # otherwise permanently cache that transient admission result.
    admitted = await _controller_update(
        workflow_id, WorkspaceTaskControllerWorkflow.submit, payload,
        update_id=f"submit:{run_id}:{uuid4().hex}",
    )
    if admitted.get("status") == "duplicate":
        from temporalio.service import RPCError, RPCStatusCode
        try:
            existing = await client.get_workflow_handle(run_id).query(NodeInvocationWorkflow.describe)
            existing_fingerprint = existing["fingerprint"]
        except RPCError as exc:
            if exc.status != RPCStatusCode.NOT_FOUND or node["type"] != "browser_agent":
                raise
            from services.workspace_task_history import get_task, TERMINAL
            record = await get_task(database, principal, workflow_id, run_id)
            if record is None or record.status not in TERMINAL:
                raise NodeUserError("This Workspace task was not found") from None
            existing_fingerprint = record.fingerprint
        if existing_fingerprint != fingerprint:
            raise NodeUserError("Submission ID was already used for a different task") from None
    from services.deployment.handlers import _with_runtime_counts
    from services.deployment.control import serialize_control
    from services.status_broadcaster import get_status_broadcaster
    try:
        await get_status_broadcaster().broadcast({
            "type": "workflow_control_status", "workflow_id": workflow_id,
            "data": await _with_runtime_counts(serialize_control(control), workflow_id),
        })
    except Exception:
        # Admission is already committed. Poll/reconnect can recover status;
        # a best-effort UI notification must not report submission failure.
        from core.logging import get_logger
        get_logger(__name__).warning("Workspace admission notification failed", workflow_id=workflow_id, exc_info=True)
    return {"run_id": run_id, "submission_id": submission_id, "status": "accepted"}


async def status(principal: str, workflow_id: str, node_id: str, submission_id: str, *, cancel: bool = False) -> dict:
    from services.authz.workflow_node import resolve_workflow_node
    from services.temporal.node_invocation import NodeInvocationWorkflow
    from services.plugin import NodeUserError
    from temporalio.service import RPCError, RPCStatusCode

    from services.plugin.deps import get_database
    from services.workspace_task_history import get_task
    database = get_database()
    record = await get_task(database, principal, workflow_id, invocation_id(principal, workflow_id, node_id, submission_id))
    if record is None:
        # Legacy device tasks have no history projection and retain saved-node authorization.
        await resolve_workflow_node(principal, workflow_id, node_id)
    handle = temporal_client().get_workflow_handle(invocation_id(principal, workflow_id, node_id, submission_id))
    try:
        if cancel:
            await _cancel_and_wait(handle)
        result = await handle.query(NodeInvocationWorkflow.describe)
    except RPCError as exc:
        if exc.status == RPCStatusCode.NOT_FOUND:
            if record is not None and record.status in ("completed", "failed", "cancelled"):
                from services.workspace_task_history import serialize
                return serialize(record)
            raise NodeUserError("This Workspace task was not found") from None
        raise NodeUserError("Workflow engine is temporarily unavailable") from None
    if result.get("status") in ("completed", "failed"):
        try:
            result["result"] = await handle.result()
        except Exception:
            result["status"] = "failed"
    if record is not None:
        from services.workspace_task_history import task_availability, safe_result
        result.update(await task_availability(database, record.browser_owner_ids))
        if "result" in result:
            result["result"] = safe_result(result["result"])
    return result
