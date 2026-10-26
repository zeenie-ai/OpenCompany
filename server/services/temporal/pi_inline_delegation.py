"""Detached durable Coding delegations from agents that retain inline runtimes.

The caller waits only for admission/start acknowledgement. This workflow owns
the attached delegation runner and the legacy polling receipt across restarts.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import WorkflowIDReusePolicy
from temporalio.exceptions import ApplicationError, WorkflowAlreadyStartedError
from temporalio.workflow import ActivityCancellationType, ChildWorkflowCancellationType, ParentClosePolicy

from services.temporal._retry_policies import DELEGATION_CLEANUP_RETRY
from services.temporal.execution_control import ExecutionControl
from services.temporal.pi_protocol import canonical_digest, canonical_json

INLINE_DELEGATION_VERSION = 1
INLINE_DELEGATION_WORKFLOW = "PiInlineDelegationWorkflow"

# Copy only server execution metadata; tool arguments cannot set these fields.
INLINE_SCOPE_KEYS = (
    "pi_harness_version", "pi_local_control_version", "pi_local_control_state", "pi_local_control_revision",
    "workflow_slug", "user_id", "credential_customer_id",
    "root_execution_id", "execution_id", "session_id", "nodes", "edges",
    "parameter_snapshot", "graphVersion", "generation", "run_scope",
    "data_scope_id", "context_execution_id", "context_session_id", "employee_job_id",
    "delegation_depth", "max_delegation_depth", "max_concurrent_subagents",
    "team_id", "team_task_id", "team_execution_id", "temporal_worker_pool_enabled", "browser_bindings",
    "execution_control_version", "controller_workflow_id", "execution_control_state",
    "execution_control_revision", "execution_control_producers_held",
    "execution_control_root_run_id", "execution_control_released_revision",
    # What asks, for a CLI or in-process agent's tool calls (RFC-0007 §4.9).
    "approval_wait_version",
)


def forward_inline_scope(source: dict) -> dict:
    return {key: source[key] for key in INLINE_SCOPE_KEYS if key in source}


def _invalid(message: str, kind: str = "CodingAgentRequiresDurableCaller") -> ApplicationError:
    return ApplicationError(message, type=kind, non_retryable=True)


def _request_scope(request: dict) -> dict:
    child = request["child_context"]
    return {key: child.get(key) for key in ("workflow_id", "root_execution_id", "user_id", "generation")}


def _request_digest(request: dict) -> str:
    return canonical_digest({key: value for key, value in request.items() if key != "request_digest"})


def validate_inline_request(request: dict) -> None:
    if request.get("inline_delegation_version") != INLINE_DELEGATION_VERSION:
        raise _invalid("Unsupported detached Coding delegation protocol", "PiProtocolConflict")
    child = request.get("child_context") or {}
    lifecycle = request.get("lifecycle") or {}
    if (child.get("node_type") != "coding_agent" or child.get("pi_harness_version") != 1
            or not all(child.get(key) for key in ("node_id", "workflow_id", "root_execution_id", "user_id"))
            or not all(lifecycle.get(key) for key in ("team_id", "team_task_id", "parent_agent_node_id", "child_agent_node_id", "task"))):
        raise _invalid("Detached Coding delegation is missing trusted execution scope")
    if lifecycle["child_agent_node_id"] != child["node_id"] or lifecycle["team_task_id"] != child.get("team_task_id"):
        raise _invalid("Detached Coding delegation identities disagree", "PiProtocolConflict")
    expected = _request_digest(request)
    if request.get("request_digest") != expected:
        raise _invalid("Detached Coding delegation digest does not match", "PiProtocolConflict")
    if len(canonical_json(request).encode("utf-8")) > 1_900_000:
        raise _invalid("Detached Coding delegation exceeds the continuation payload limit", "PiProtocolConflict")


async def _record_admission(database: Any, request: dict) -> None:
    """One immutable request per recorded call, including conflicting retries."""
    from models.pi_checkpoint import PiRuntimeBlob
    from models.database import NodeOutput
    from sqlalchemy.exc import IntegrityError

    identity = canonical_digest({"inline_delegation": request["runner_workflow_id"]})
    scope_digest = canonical_digest(_request_scope(request))
    async with database.get_session() as session:
        row = await session.get(PiRuntimeBlob, identity)
        if row is None:
            session.add(PiRuntimeBlob(id=identity, scope_digest=scope_digest, kind="inline-delegation-v1",
                                     digest=request["request_digest"], payload=request))
            lifecycle = request["lifecycle"]
            task_id = lifecycle["team_task_id"]
            # Admission and the initial polling receipt commit together. A
            # matching retry cannot race a terminal receipt with a new write.
            session.add(NodeOutput(node_id=lifecycle["child_agent_node_id"], session_id=f"delegation_{task_id}",
                output_name="delegation_result", data={"task_id": task_id, "status": "running",
                    "agent_name": lifecycle["child_agent_name"], "agent_node_id": lifecycle["child_agent_node_id"],
                    "result": {}, "error": None, "request_digest": request["request_digest"],
                    "runner_workflow_id": request["runner_workflow_id"]}))
            try:
                await session.commit()
                return
            except IntegrityError:
                await session.rollback()
                row = await session.get(PiRuntimeBlob, identity)
        if (row is None or row.kind != "inline-delegation-v1" or row.digest != request["request_digest"]
                or row.scope_digest != scope_digest or _request_digest(row.payload) != row.digest):
            raise _invalid("Recorded Coding delegation call was retried with different content", "PiProtocolConflict")


async def _verify_admission(database: Any, request: dict) -> None:
    from models.pi_checkpoint import PiRuntimeBlob

    validate_inline_request(request)
    async with database.get_session() as session:
        row = await session.get(PiRuntimeBlob, canonical_digest({"inline_delegation": request["runner_workflow_id"]}))
        if (row is None or row.kind != "inline-delegation-v1" or row.digest != request["request_digest"]
                or row.scope_digest != canonical_digest(_request_scope(request)) or _request_digest(row.payload) != row.digest):
            raise _invalid("Detached Coding delegation was not admitted", "PiProtocolConflict")


async def start_inline_pi_delegation(args: dict, config: dict, *, precreated_task_id: str | None = None) -> dict:
    """Admit a trusted tool call and return without waiting for child work."""
    from core.config import Settings
    from services.agent_team import get_agent_team_service
    from services.employees.permissions import assert_runtime_access
    from services.workflow_sanitizer import sanitize_runtime_payload
    from core.container import container

    database = config.get("database")
    node_id = str(config.get("node_id") or "")
    parent_id = str(config.get("parent_node_id") or "")
    workflow_id = str(config.get("workflow_id") or "")
    root_id = str(config.get("root_execution_id") or config.get("execution_id") or "")
    call_id = str(config.get("operation_id") or config.get("tool_call_id") or "")
    mission = str(args.get("task") or "").strip()
    original_context = args.get("context")
    context_text = (original_context if isinstance(original_context, str)
                    else json.dumps(original_context, default=str, ensure_ascii=False)
                    if original_context is not None else "")
    if not database or not all((node_id, parent_id, workflow_id, root_id, call_id, mission)):
        raise _invalid("Coding delegation requires a recorded call identity and trusted workflow, parent, execution, and database scope")
    nodes, edges = config.get("nodes") or [], config.get("edges") or []
    if not any(edge.get("source") == node_id and edge.get("target") == parent_id
               and (edge.get("targetHandle") or edge.get("target_handle")) in {"input-tools", "input-teammates"} for edge in edges):
        raise _invalid("Coding delegation target is not connected to the calling agent")
    target = next((node for node in nodes if node.get("id") == node_id), None)
    if not target or target.get("type") != "coding_agent":
        raise _invalid("Coding delegation target does not match the admitted graph")
    settings = Settings()
    service = container.workflow_service()
    executor = service._temporal_executor
    if not settings.temporal_enabled or executor is None:
        raise _invalid("Coding Agent requires a connected Temporal runtime. Start Temporal and its workers, then run again.", "CodingAgentTemporalRequired")
    user_settings = await database.get_user_settings() or {}
    depth = int(config.get("delegation_depth") or 0)
    maximum_depth = int(config.get("max_delegation_depth") or user_settings.get("max_delegation_depth") or settings.max_delegation_depth)
    if depth >= maximum_depth:
        raise _invalid(f"Maximum delegation depth {maximum_depth} exceeded", "DelegationDepthExceeded")
    limit = max(1, int(config.get("max_concurrent_subagents") or user_settings.get("max_concurrent_subagents") or settings.max_concurrent_subagents))
    await assert_runtime_access(database, workflow_id, node_id, "coding_agent", config)

    identity = canonical_digest({"version": INLINE_DELEGATION_VERSION, "root": root_id, "parent": parent_id,
                                 "target": node_id, "call": call_id, "precreated_task": precreated_task_id})
    task_id = precreated_task_id or f"delegated_pi_{identity[:32]}"
    runner_id = f"coding-inline-{identity}"
    snapshot = dict(config.get("parameter_snapshot") or {})
    for node in nodes:
        identifier = str(node.get("id") or "")
        if identifier and identifier not in snapshot:
            snapshot[identifier] = await database.get_node_parameters(identifier) or {}
    child_params = {**(snapshot.get(node_id) or {}), "system_message": mission,
                    "prompt": context_text or mission}
    snapshot[node_id] = child_params
    parent = next((node for node in nodes if node.get("id") == parent_id), {})
    teammates = [{"node_id": node.get("id"), "node_type": node.get("type"), "label": (node.get("data") or {}).get("label")}
                 for node in nodes if any(edge.get("source") == node.get("id") and edge.get("target") == parent_id
                     and (edge.get("targetHandle") or edge.get("target_handle")) in {"input-tools", "input-teammates"}
                     for edge in edges)]
    team_service = get_agent_team_service()
    team = await team_service.get_or_create_execution_team(team_lead_node_id=parent_id, teammates=teammates,
        workflow_id=workflow_id, execution_id=str(config.get("team_execution_id") or config.get("employee_job_id") or config.get("execution_id") or root_id), root_execution_id=root_id,
        team_lead_type=parent.get("type", "aiAgent"), team_lead_label=(parent.get("data") or {}).get("label"),
        config={"mode": "parallel", "max_concurrent_subagents": limit})
    team_id = str((team or {}).get("team_id") or (team or {}).get("id") or "")
    if not team_id:
        raise RuntimeError("Failed to persist Coding delegation execution team")
    if precreated_task_id:
        task = await database.get_durable_team_task(team_id, task_id)
        if not task or task.get("assigned_to") != node_id:
            raise _invalid("Precreated task is not assigned to this Coding Agent")
    child = {**forward_inline_scope(config), "node_id": node_id, "node_type": "coding_agent", "node_data": child_params,
        "workflow_id": workflow_id, "execution_id": str(config.get("execution_id") or root_id), "root_execution_id": root_id,
        "user_id": str(config.get("user_id") or "owner"), "parameter_snapshot": snapshot,
        "parent_node_id": parent_id, "delegation_depth": depth + 1, "max_delegation_depth": maximum_depth,
        "max_concurrent_subagents": limit, "team_id": team_id, "team_task_id": task_id, "trace_id": call_id,
        "inputs": {}, "outputs": {}, "invocation": {"task": mission, "context": context_text},
        "pi_harness_version": 1}
    from services.coding_agent_runtime import freeze_pi_local_control
    freeze_pi_local_control(child)
    # Each detached workflow registers independently before business effects.
    child.pop("execution_control_root_run_id", None)
    child.pop("execution_control_released_revision", None)
    child["workflow_slug"] = config.get("workflow_slug") or await service._resolve_workflow_slug(workflow_id)
    child["workspace_dir"] = service._get_workspace_dir(child["workflow_slug"])
    lifecycle = {"team_id": team_id, "team_task_id": task_id, "parent_agent_node_id": parent_id,
        "child_agent_node_id": node_id, "child_agent_name": str(child_params.get("label") or "Coding Agent"),
        "workflow_id": workflow_id, "task": mission, "context": args.get("context"), "employee_job_id": config.get("employee_job_id"),
        "root_execution_id": root_id, "delegation_depth": depth + 1, "trace_id": call_id,
        "assignment_event_id": f"{task_id}:assigned", "queued_event_id": f"{task_id}:queued"}
    request = sanitize_runtime_payload({"inline_delegation_version": INLINE_DELEGATION_VERSION,
        "runner_workflow_id": runner_id, "child_workflow_id": f"{runner_id}-agent", "limit": limit,
        "lifecycle": lifecycle, "child_context": child})
    request["request_digest"] = canonical_digest(request)
    validate_inline_request(request)
    await _record_admission(database, request)
    from services.temporal.agent_activities import queue_agent_delegation
    previous_task = await database.get_durable_team_task(team_id, task_id)
    if (previous_task or {}).get("status") not in {"submitted", "accepted", "completed", "failed", "cancelled", "skipped"}:
        await queue_agent_delegation(request["lifecycle"])
    options = {"id": runner_id, "task_queue": settings.temporal_task_queue,
               "id_reuse_policy": WorkflowIDReusePolicy.REJECT_DUPLICATE,
               "memo": {"pi_inline_digest": request["request_digest"]}}
    try:
        await executor.client.start_workflow(INLINE_DELEGATION_WORKFLOW, request, **options)
    except WorkflowAlreadyStartedError:
        # Immutable admission matched above; attach to this recorded operation.
        pass
    return {"success": True, "status": "delegated", "task_id": task_id, "agent_node_id": node_id,
            "agent_name": lifecycle["child_agent_name"], "runner_workflow_id": runner_id,
            "message": f"Task delegated to '{lifecycle['child_agent_name']}'.",
            "result": f"SUCCESS: Task delegated (task_id: {task_id}). Use 'check_delegated_tasks' to check status."}


async def list_inline_delegation_ids(database: Any, scope: dict) -> list[str]:
    """Recover only this principal/root/parent's admitted detached tasks."""
    from models.pi_checkpoint import PiRuntimeBlob
    from sqlalchemy import select

    if scope.get("pi_harness_version") != 1 or not all(scope.get(key) for key in ("workflow_id", "parent_node_id", "user_id")):
        return []
    root_id = scope.get("root_execution_id") or scope.get("execution_id")
    if not root_id:
        return []
    expected_scope = canonical_digest({"workflow_id": scope["workflow_id"], "root_execution_id": root_id,
                                      "user_id": scope["user_id"], "generation": scope.get("generation")})
    async with database.get_session() as session:
        rows = (await session.execute(select(PiRuntimeBlob).where(PiRuntimeBlob.kind == "inline-delegation-v1",
            PiRuntimeBlob.scope_digest == expected_scope).order_by(PiRuntimeBlob.created_at.desc()).limit(1000))).scalars().all()
        return [row.payload["lifecycle"]["team_task_id"] for row in rows
                if row.payload["lifecycle"]["parent_agent_node_id"] == scope["parent_node_id"]
                and _request_digest(row.payload) == row.digest]


async def inline_delegation_receipt_matches(database: Any, scope: dict, task_id: str, receipt: dict) -> bool:
    """Authorize an explicit polling ID against its immutable admission.

    Explicit IDs are not bounded by the recent-list limit, and a mutable
    NodeOutput receipt cannot grant another principal access on its own.
    """
    from models.pi_checkpoint import PiRuntimeBlob

    runner_id = receipt.get("runner_workflow_id")
    if not runner_id or scope.get("pi_harness_version") != 1:
        return False
    expected_scope = {"workflow_id": scope.get("workflow_id"),
                      "root_execution_id": scope.get("root_execution_id") or scope.get("execution_id"),
                      "user_id": scope.get("user_id"), "generation": scope.get("generation")}
    if not all(expected_scope[key] for key in ("workflow_id", "root_execution_id", "user_id")) or not scope.get("parent_node_id"):
        return False
    identity = canonical_digest({"inline_delegation": runner_id})
    async with database.get_session() as session:
        row = await session.get(PiRuntimeBlob, identity)
    if row is None or row.kind != "inline-delegation-v1" or row.scope_digest != canonical_digest(expected_scope):
        return False
    payload = row.payload
    lifecycle = payload.get("lifecycle") or {}
    return (lifecycle.get("parent_agent_node_id") == scope["parent_node_id"]
            and lifecycle.get("team_task_id") == task_id
            and payload.get("runner_workflow_id") == runner_id
            and _request_digest(payload) == row.digest == receipt.get("request_digest"))


@activity.defn(name="coding.inline_delegation_status")
async def persist_inline_delegation_status(payload: dict) -> dict:
    """Project durable TeamTask state into the existing detached polling API."""
    from services.plugin.deps import get_database

    database = get_database()
    request = payload["request"]
    await _verify_admission(database, request)
    lifecycle = request["lifecycle"]
    task_id = lifecycle["team_task_id"]
    existing = await database.get_node_output_by_session(f"delegation_{task_id}", "delegation_result")
    previous = (existing or {}).get("data") or {}
    if previous and previous.get("request_digest") != request["request_digest"]:
        raise _invalid("Coding delegation polling receipt conflicts", "PiProtocolConflict")
    task = await database.get_durable_team_task(lifecycle["team_id"], task_id)
    task_status = str((task or {}).get("status") or "pending")
    terminal = task_status in {"submitted", "accepted", "completed", "failed", "cancelled", "skipped"}
    if payload.get("status") == "running" and previous:
        return previous  # A lost start acknowledgement never overwrites a result.
    if not terminal and payload.get("status") == "running":
        status, error = "running", None
    elif task_status in {"submitted", "accepted", "completed"}:
        status, error = "completed", None
    else:
        status, error = "error", str((task or {}).get("error") or (task or {}).get("cancellation_reason")
                                     or payload.get("error") or f"Delegation ended with status {task_status}")
    data = {"task_id": task_id, "status": status, "agent_name": lifecycle["child_agent_name"],
            "agent_node_id": lifecycle["child_agent_node_id"], "result": (task or {}).get("result") or {},
            "error": error, "request_digest": request["request_digest"], "runner_workflow_id": request["runner_workflow_id"]}
    if not await database.save_node_output(node_id=lifecycle["child_agent_node_id"], session_id=f"delegation_{task_id}",
                                          output_name="delegation_result", data=data):
        raise RuntimeError("Failed to persist Coding delegation polling receipt")
    return data


@workflow.defn(sandboxed=False, name=INLINE_DELEGATION_WORKFLOW)
class PiInlineDelegationWorkflow:
    @workflow.init
    def __init__(self, request: dict) -> None:
        context = request.get("child_context") or {}
        if context.get("pi_local_control_version") == 1 and context.get("execution_control_version") != 1:
            from services.temporal.pi_control import PiLocalExecutionControl
            self._control = PiLocalExecutionControl()
        else:
            self._control = ExecutionControl()
        self._control.bind(context)

    @workflow.update
    async def set_control_state(self, payload: dict) -> dict:
        return await self._control.set_control_state(payload)

    @set_control_state.validator
    def validate_set_control_state(self, payload: dict) -> None:
        self._control.validate_control(payload)

    @workflow.update
    async def wait_for_checkpoint(self, payload: dict | None = None) -> dict:
        return await self._control.wait_for_checkpoint(payload)

    @wait_for_checkpoint.validator
    def validate_wait_for_checkpoint(self, payload: dict | None = None) -> None:
        self._control.validate_control(payload)

    @workflow.query
    def execution_control_status(self) -> dict:
        return self._control.status()

    @workflow.run
    async def run(self, request: dict) -> dict:
        validate_inline_request(request)
        error = None
        admission_verified = False
        try:
            await self._control.register_root()
            await self._control.wait_until_running()
            # Verify the immutable caller admission before the attached runner
            # can acquire permits, claim a task, or start the Coding Agent.
            async with self._control.action():
                await workflow.execute_activity("coding.inline_delegation_status", {"request": request, "status": "running"},
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=DELEGATION_CLEANUP_RETRY,
                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
            admission_verified = True
            async with self._control.child_start():
                child = await workflow.start_child_workflow("DelegatedTaskWorkflow",
                    {"lifecycle": request["lifecycle"], "child_context": request["child_context"],
                     "child_workflow_id": request["child_workflow_id"], "limit": request["limit"]},
                    id=f"{request['runner_workflow_id']}-runner", parent_close_policy=ParentClosePolicy.REQUEST_CANCEL,
                    cancellation_type=ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED)
            await child
        except asyncio.CancelledError:
            error = "Coding delegation cancelled"
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            self._control.begin_bookkeeping()
            try:
                if not admission_verified:
                    # Registration may fail before the admission check. No
                    # cleanup mutation may trust an unverified external input.
                    await workflow.execute_activity("coding.inline_delegation_status", {"request": request, "status": "running"},
                        start_to_close_timeout=timedelta(seconds=60), retry_policy=DELEGATION_CLEANUP_RETRY,
                        cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
                if error:
                    await workflow.execute_activity("agent.cancel_delegation", {**request["lifecycle"], "reason": error,
                        "terminal_event_id": f"{request['lifecycle']['team_task_id']}:inline-terminal"},
                        start_to_close_timeout=timedelta(seconds=60), retry_policy=DELEGATION_CLEANUP_RETRY,
                        cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
                await workflow.execute_activity("coding.inline_delegation_status", {"request": request, "error": error},
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=DELEGATION_CLEANUP_RETRY,
                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
            finally:
                try:
                    await self._control.unregister_root()
                finally:
                    self._control.end_action()
                    if self._control.enabled:
                        await workflow.wait_condition(workflow.all_handlers_finished)
        return {"task_id": request["lifecycle"]["team_task_id"], "status": "completed"}


def collect_pi_inline_activities() -> list:
    return [persist_inline_delegation_status]
