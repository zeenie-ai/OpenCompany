"""Serialized, durable execution for a reusable agent conversation.

An owner drains a busy period and exits when idle. The instance and inbox
remain durable, and signal-with-start reopens the same owner identity. A
database run fence also protects against late executors after cancellation.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError
from temporalio.workflow import ActivityCancellationType, ChildWorkflowCancellationType

from services.temporal.execution_control import ExecutionControl
from services.temporal._exits import is_workflow_bug

BOOKKEEPING = RetryPolicy(initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=30))


def _store():
    from core.container import container
    from services.agent_instances.service import AgentInstanceService
    return AgentInstanceService(container.database())


async def _notify_instance(instance_id: str) -> None:
    """Wake UI readers after commit; transport failures cannot roll back work."""
    try:
        from services.status_broadcaster import get_status_broadcaster
        from services.employees.events import employee_changed_now
        instance = await _store().get_instance(instance_id)
        broadcaster = get_status_broadcaster()
        if broadcaster:
            await broadcaster.broadcast_team_event(instance["team_id"], "agent.instance.updated", {
                "workflow_id": instance["workflow_id"], "team_id": instance["team_id"],
                "agent_instance_id": instance_id, "state": instance["state"],
            })
        employee_changed_now(instance["workflow_id"])
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Agent state notification deferred", exc_info=True)


def turn_context(claim: dict[str, Any]) -> dict[str, Any]:
    """Project admitted configuration; peer text never supplies runtime scope."""
    instance = claim["instance"]
    config = instance.get("config") or {}
    template = config.get("template") or config
    scope = config.get("scope") or instance.get("scope") or {}
    original = config.get("runtime_context") or template.get("runtime_context") or scope.get("runtime_context") or {}
    context = {**original, **{key: value for key, value in scope.items() if key != "runtime_context"}}
    context.update({
        "node_id": instance.get("template_node_id") or template.get("node_id"),
        "node_type": template.get("node_type") or instance.get("node_type") or "aiAgent",
        "node_data": template.get("parameters") or {},
        "agent_lifecycle_version": 1, "agent_protocol_version": 1,
        "agent_instance_id": instance["id"], "agent_run_id": claim["run_id"],
        "session_id": f"agent-instance:{instance['id']}",
        "context_session_id": f"agent-instance:{instance['id']}",
        "agent_instance_role": instance.get("role"),
        "agent_instance_scope": scope,
        "agent_delivery_ids": claim.get("deliveries") or [],
        "agent_transcript_revision": claim.get("transcript_revision", 0),
        "agent_input_messages": claim.get("messages") or [],
        "agent_initial_transcript": claim.get("transcript") or [],
        "team_id": instance.get("team_id") or scope.get("team_id"),
        "employee_job_id": original.get("employee_job_id"),
    })
    # Stored template configuration wins over mutable graph defaults.
    context["parameter_snapshot"] = {**(context.get("parameter_snapshot") or {}),
                                     context["node_id"]: template.get("parameters") or {}}
    task = claim.get("task") or {}
    if task:
        context.update(team_task_id=task["id"], parent_task_id=task["id"],
                       task_attempt=task.get("current_attempt", 0),
                       parent_node_id=scope.get("lead_node_id"))
    context.pop("invocation", None)
    context.pop("chat_run_id", None)  # only Talk streams into the owner's chat
    context.pop("run_scope", None)  # chat_stream_for also derives identity here
    context.pop("_agent_resume", None)
    context.pop("execution_control_root_run_id", None)
    context.pop("execution_control_released_revision", None)
    return context


@activity.defn(name="agent.instance.bootstrap")
async def bootstrap_instance(context: dict) -> dict:
    from services.agent_instances.protocol import capture_runtime_context
    from services.workflow_sanitizer import sanitize_runtime_payload
    from services.temporal.agent_activities import prepare_agent_payload
    review = await route_instance_task_event(context)
    if review is not None:
        return {"applicable": False, "review_event": True, "result": review}
    prepared = await prepare_agent_payload({**context, "_agent_instance_bootstrap": True})
    team_id = prepared.get("team_id")
    if not team_id:
        return {"applicable": False}
    scope = {key: context.get(key) for key in (
        "workflow_id", "generation", "execution_id", "root_execution_id", "user_id")}
    scope.update(team_id=team_id, job_id=prepared.get("employee_job_id") or prepared.get("team_execution_id") or context.get("execution_id"),
                 lead_node_id=context["node_id"])
    scope["execution_id"] = prepared.get("team_execution_id") or context.get("execution_id")
    captured = capture_runtime_context({**context, "parameter_snapshot": prepared.get("parameter_snapshot") or {},
                "employee_job_id": prepared.get("employee_job_id"),
                "max_concurrent_subagents": prepared.get("max_concurrent_subagents", 3),
                "max_delegation_depth": prepared.get("max_delegation_depth", 2)})
    template = {"node_id": context["node_id"], "node_type": context["node_type"],
                "parameters": sanitize_runtime_payload(prepared.get("instance_template_parameters") or
                    (prepared.get("parameter_snapshot") or {}).get(context["node_id"], context.get("node_data") or {})),
                "runtime_context": captured,
                "initial_instructions": prepared.get("user_prompt") or "Review the current job and coordinate its tasks."}
    instance = await _store().ensure_lead(scope=scope, template=template,
        request_id=f"lead:{scope['job_id']}:{context['node_id']}")
    return {"applicable": True, "instance_id": instance["id"], "context": captured}


async def route_instance_task_event(context: dict) -> dict | None:
    """Treat taskTrigger as a hint for its exact existing instance owner.

    Submission already committed the canonical review delivery. External
    events neither create a second delivery nor bootstrap a replacement job.
    """
    from sqlmodel import select
    from models.database import TeamTask
    from models.agent_instances import AgentInstance
    from services.plugin.edge_walker import extract_task_event_payload
    from services.agent_instances.runtime import wake_instances
    nodes = {node.get("id"): node for node in context.get("nodes") or []}
    outputs = context.get("outputs") or context.get("inputs") or {}
    event = None
    for edge in context.get("edges") or []:
        if edge.get("target") != context.get("node_id") or (nodes.get(edge.get("source")) or {}).get("type") != "taskTrigger":
            continue
        if edge.get("targetHandle") not in {None, "", "input-main", "input-task"}:
            continue
        event = extract_task_event_payload(outputs.get(edge.get("source")))
        if event:
            break
    if event is None:
        return None
    ignored = {"success": True, "status": "ignored_task_event", "instance_managed": True}
    database = _store().database
    async with database.get_session() as session:
        task = await session.get(TeamTask, str(event["task_id"]))
        if (task is None or task.protocol_version != 1 or not task.assigned_instance_id
                or task.workflow_id != context.get("workflow_id")
                or task.parent_agent_id != context.get("node_id")
                or task.status not in {"submitted", "failed"}):
            return ignored
        owner = await session.get(AgentInstance, task.assigned_instance_id)
        if (owner is None or owner.generation != int(context.get("generation") or 0)
                or owner.workflow_id != context.get("workflow_id")
                or owner.team_id != task.team_id):
            return ignored
        for key, expected in (("team_id", task.team_id), ("workflow_id", task.workflow_id),
                              ("attempt", task.current_attempt), ("task_attempt", task.current_attempt),
                              ("revision", task.revision), ("generation", owner.generation)):
            if key in event and str(event[key]) != str(expected):
                return ignored
        leads = (await session.execute(select(AgentInstance).where(
            AgentInstance.team_id == task.team_id, AgentInstance.job_id == owner.job_id,
            AgentInstance.generation == owner.generation, AgentInstance.role == "lead",
            AgentInstance.template_node_id == context["node_id"], AgentInstance.state != "closed"))).scalars().all()
        if len(leads) != 1:
            return ignored
        lead_id = leads[0].id
    await wake_instances({"instance_wake": [lead_id]})
    return {"success": True, "status": "review_queued", "agent_instance_id": lead_id, "instance_managed": True}


@activity.defn(name="agent.instance.dispatch_wait")
async def dispatch_wait(payload: dict) -> dict:
    from services.agent_instances.runtime import wake_instance
    handle = await wake_instance(payload["instance_id"], context=payload.get("context"))
    if handle is None:
        return {"success": False, "error_type": "agent_turn_halted", "error": "Agent execution is halted.", "instance_managed": True}
    waiter = asyncio.create_task(handle.result())
    try:
        while not waiter.done():
            await asyncio.wait({waiter}, timeout=10)
            activity.heartbeat("Waiting for serialized agent instance")
        return {**(await waiter), "instance_managed": True}
    finally:
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


@activity.defn(name="agent.instance.claim")
async def claim_instance(payload: dict) -> dict | None:
    claim = await _store().claim_run(payload["instance_id"], request_id=payload["request_id"],
        workflow_id=payload.get("workflow_id"), workflow_run_id=payload.get("workflow_run_id"))
    if claim:
        claim["context"] = turn_context(claim)
        await _notify_instance(payload["instance_id"])
    return claim


@activity.defn(name="agent.instance.checkpoint")
async def checkpoint_instance(payload: dict) -> dict:
    from services.agent_instances.service import InstanceError
    try:
        result = await _store().checkpoint(payload["instance_id"], payload["run_id"],
            messages=payload["messages"], expected_revision=payload["expected_revision"],
            delivery_ids=payload.get("delivery_ids") or [], request_id=payload["request_id"])
        await _notify_instance(payload["instance_id"])
        return result
    except InstanceError as exc:
        raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc


@activity.defn(name="agent.instance.drain")
async def drain_instance(payload: dict) -> dict:
    state = await _store().admission_state(payload["instance_id"], payload["run_id"])
    if state.get("reason"):
        return {"messages": [], "deliveries": [], "halted": True}
    return await _store().drain_messages(payload["instance_id"], payload["run_id"], request_id=payload["request_id"])


@activity.defn(name="agent.instance.admit")
async def admit_instance_operation(payload: dict) -> dict:
    state = await _store().admission_state(payload["instance_id"], payload["run_id"])
    return {"allowed": not state.get("reason"), "admission_state": state}


@activity.defn(name="agent.instance.halt")
async def halt_instance(payload: dict) -> dict:
    result = await _store().halt_instance(payload["instance_id"], reason=payload["reason"], request_id=payload["request_id"])
    await _notify_instance(payload["instance_id"])
    return result


@activity.defn(name="agent.instance.invalid")
async def record_invalid_instance_request(payload: dict) -> dict:
    return await _store().record_invalid_request(payload["instance_id"], payload["run_id"],
        request_id=payload["request_id"])


@activity.defn(name="agent.instance.complete")
async def complete_instance(payload: dict) -> dict:
    from services.agent_instances.runtime import wake_instances
    result = await _store().complete_run(payload["instance_id"], payload["run_id"],
        result=payload.get("result"), error=payload.get("error"), request_id=payload["request_id"])
    await wake_instances(result)
    await _notify_instance(payload["instance_id"])
    return result


@activity.defn(name="agent.instance.reconcile")
async def reconcile_instances(payload: dict) -> dict:
    from services.agent_instances.runtime import reconcile_pending
    return {"woken": await reconcile_pending(limit=100)}


@activity.defn(name="agent.instance.tool")
async def execute_instance_tool(payload: dict) -> dict:
    from services.handlers.tools import execute_tool
    config = {**(payload.get("agent_tool_config") or {}), **{
        key: payload[key] for key in ("workflow_id", "execution_id", "root_execution_id", "parent_node_id", "nodes", "edges", "tool_call_id", "operation_id", "agent_iteration", "tool_call_index") if key in payload}}
    # Name and public arguments are preserved; only the execution projection
    # carries the trusted manifest binding and authenticated scope.
    config["node_id"] = config.get("tool_node_id") or payload.get("node_id")
    if config.get("agent_operation") == "submit_job":
        # Talk has its own conversation. The existing job admission validates
        # its authenticated graph role before creating an independent lead.
        return await execute_tool(config.get("name") or "submit_job", payload.get("tool_args") or {}, config)
    runtime = config.get("agent_runtime") or config
    admitted = await admit_instance_operation({"instance_id": runtime["agent_instance_id"], "run_id": runtime["agent_run_id"]})
    if not admitted["allowed"]:
        return {"success": False, "error_type": "agent_turn_halted", "error": "Agent execution is halted."}
    return await execute_tool(config.get("name") or "", payload.get("tool_args") or {}, config)


@activity.defn(name="agent.instance.execute")
async def execute_instance_node(context: dict) -> dict:
    """Execute an opaque turn in the owning worker, preserving cancellation.

    The legacy websocket bridge omits instance identity and disconnecting it
    does not await the remote executor. Direct dispatch keeps provider session
    affinity, trusted context and the real coroutine in the activity lifetime.
    """
    from core.container import container
    admitted = await admit_instance_operation({"instance_id": context["agent_instance_id"],
        "run_id": context["agent_run_id"]})
    if not admitted["allowed"]:
        return {"success": False, "error_type": "agent_turn_halted", "error": "Agent execution is halted."}
    service = container.workflow_service()
    execution = asyncio.create_task(service.execute_node(
        node_id=context["node_id"], node_type=context["node_type"], parameters=context.get("node_data") or {},
        nodes=context.get("nodes") or [], edges=context.get("edges") or [],
        session_id=context.get("session_id") or "default", execution_id=context.get("execution_id"),
        workflow_id=context.get("workflow_id"), workflow_slug=context.get("workflow_slug"),
        outputs=context.get("outputs") or context.get("inputs") or {}, extras=context,
        user_id=str(context.get("user_id") or "owner")))
    try:
        while not execution.done():
            await asyncio.wait({execution}, timeout=10)
            activity.heartbeat("Executing isolated agent turn")
        return execution.result()
    except asyncio.CancelledError:
        execution.cancel()
        # RLM may still be draining an admitted Python completion. A second
        # cancellation must not falsely acknowledge that it has stopped.
        while not execution.done():
            try:
                # wait() does not propagate cancellation into its children.
                # Continue heartbeating so a long drain is not mistaken for a
                # dead worker and released while effects are still running.
                await asyncio.wait({execution}, timeout=10)
                activity.heartbeat("Draining interrupted agent execution")
            except asyncio.CancelledError:
                continue
        if not execution.cancelled():
            execution.exception()  # retrieve any failure from the drained call
        raise


@workflow.defn(name="AgentInstanceSessionWorkflow", sandboxed=False)
class AgentInstanceSessionWorkflow:
    @workflow.init
    def __init__(self, request: dict | None = None):
        self._wake_revision = 0
        self._opaque_handle = None
        self._current_handle = None
        self._current_run_id = None
        self._control = ExecutionControl()
        if request:
            self._control.bind(request)

    @workflow.signal
    def wake(self, payload: dict | None = None):
        self._wake_revision += 1

    @workflow.signal
    async def stop_current(self, payload: dict | None = None):
        captured_run, captured_handle = self._current_run_id, self._current_handle
        if captured_run is None or captured_handle is None:
            return
        state = await workflow.execute_activity("agent.instance.admit", {
            "instance_id": self._instance_id, "run_id": captured_run},
            start_to_close_timeout=timedelta(seconds=60), retry_policy=BOOKKEEPING)
        if not state["allowed"] and self._current_run_id == captured_run and self._current_handle is not None:
            self._current_handle.cancel()

    @workflow.update
    async def set_control_state(self, payload: dict) -> dict:
        return await self._control.set_control_state(payload)

    @set_control_state.validator
    def validate_control(self, payload: dict):
        self._control.validate_control(payload)

    @workflow.update
    async def wait_for_checkpoint(self, payload: dict | None = None) -> dict:
        return await self._control.wait_for_checkpoint(payload)

    @workflow.query
    def execution_control_status(self) -> dict:
        return self._control.status()

    @workflow.run
    async def run(self, request: dict | None = None) -> dict:
        from services.coding_agent_runtime import agent_workflow_name
        from services.temporal.agent_workflow import _normalise_delegated_result
        request = request or {}
        self._instance_id = request["instance_id"]
        self._control.bind(request)
        await self._control.register_root()
        last = {"success": True, "status": "idle"}
        sequence = int(request.get("sequence") or 0)
        workflow_bug = False
        try:
            while True:
                await self._control.wait_until_running()
                wake_revision = self._wake_revision
                claim = await workflow.execute_activity("agent.instance.claim", {
                    "instance_id": request["instance_id"],
                    "request_id": f"{workflow.info().first_execution_run_id}:{sequence}",
                    "workflow_id": workflow.info().workflow_id,
                    "workflow_run_id": workflow.info().run_id},
                    start_to_close_timeout=timedelta(seconds=60), retry_policy=BOOKKEEPING)
                if not claim:
                    if wake_revision != self._wake_revision:
                        continue
                    return last
                sequence += 1
                if claim.get("status") in {"completed", "failed", "cancelled"}:
                    last = {"success": claim["status"] == "completed", "result": claim.get("result"), "error": claim.get("error")}
                    continue
                context = claim["context"]
                run_id = claim["run_id"]
                self._current_run_id = run_id
                permit_id = None
                error = None
                try:
                    if (claim.get("instance") or {}).get("role") != "lead":
                        permit_handle = workflow.start_activity("agent.acquire_subagent_permit", {
                            "agent_instance_id": request["instance_id"], "agent_run_id": run_id,
                            "root_execution_id": context.get("root_execution_id") or context.get("execution_id"),
                            "permit_id": run_id, "limit": max(1, int(context.get("max_concurrent_subagents") or 3)), "lease_version": 2},
                            start_to_close_timeout=timedelta(hours=1), heartbeat_timeout=timedelta(seconds=10), retry_policy=BOOKKEEPING,
                            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
                        self._current_handle = permit_handle
                        permit = await permit_handle
                        permit_id = permit.get("lease_id") or run_id
                        context["team_permit_id"] = permit_id
                    admission = await workflow.execute_activity("agent.instance.admit", {
                        "instance_id": request["instance_id"], "run_id": run_id},
                        start_to_close_timeout=timedelta(seconds=60), retry_policy=BOOKKEEPING)
                    if not admission.get("allowed"):
                        raise asyncio.CancelledError()
                    async def execute_turn():
                        async with self._control.child_start():
                            if context["node_type"] in {"claude_code_agent", "codex_agent", "rlm_agent", "gemini_cli_agent"}:
                                handle = workflow.start_activity("agent.instance.execute", context,
                                    start_to_close_timeout=timedelta(days=1), heartbeat_timeout=timedelta(minutes=2),
                                    retry_policy=RetryPolicy(maximum_attempts=1),
                                    cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED)
                                self._opaque_handle = handle
                            else:
                                handle = await workflow.start_child_workflow(
                                    agent_workflow_name(context["node_type"], context), context,
                                    id=f"agent-instance-run:{run_id}",
                                    cancellation_type=ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED)
                        return await handle
                    # The cancellable turn includes the child-start control
                    # gate. stop_agent can drain a fenced queued turn even
                    # while the generation remains stopped.
                    self._current_handle = asyncio.create_task(execute_turn())
                    last = await self._current_handle
                    if not isinstance(last, dict):
                        last = {"success": True, "result": last}
                    transcript = last.pop("agent_transcript", None)
                    if transcript is None and isinstance(last.get("result"), dict):
                        transcript = last["result"].pop("agent_transcript", None)
                    if transcript:
                        await workflow.execute_activity("agent.instance.checkpoint", {
                            **transcript, "instance_id": request["instance_id"], "run_id": run_id},
                            start_to_close_timeout=timedelta(seconds=60), retry_policy=BOOKKEEPING)
                    error = last.get("error") if not last.get("success", True) else None
                except Exception as exc:
                    if is_workflow_bug(exc):
                        workflow_bug = True
                        raise
                    error = str(exc)
                    last = {"success": False, "error": error}
                except asyncio.CancelledError:
                    error = "interrupted"
                    last = {"success": False, "error": error, "error_type": "agent_turn_halted"}
                finally:
                    self._opaque_handle = None
                    self._current_handle = None
                    self._current_run_id = None
                    if not workflow_bug:
                        _text, summary = _normalise_delegated_result(last)
                        await workflow.execute_activity("agent.instance.complete", {
                            "instance_id": request["instance_id"], "run_id": run_id,
                            "result": summary, "error": error, "request_id": f"complete:{run_id}"},
                            start_to_close_timeout=timedelta(seconds=60), retry_policy=BOOKKEEPING)
                        if permit_id:
                            await workflow.execute_activity("agent.release_subagent_permit", {
                                "agent_instance_id": request["instance_id"], "agent_run_id": run_id,
                                "root_execution_id": context.get("root_execution_id") or context.get("execution_id"), "permit_id": permit_id},
                                start_to_close_timeout=timedelta(seconds=60), retry_policy=BOOKKEEPING)
                if error and (claim.get("instance") or {}).get("role") == "lead" and context.get("employee_job_id"):
                    # This transaction refuses to fail a job with any child
                    # tasks. Those jobs remain recoverable through review.
                    await workflow.execute_activity("employee.job.failed", {
                        **context, "failure_code": "lead_no_assignments", "correction_count": 3},
                        start_to_close_timeout=timedelta(seconds=60), retry_policy=BOOKKEEPING)
                if last.get("success", True) and (claim.get("instance") or {}).get("role") == "lead" and context.get("employee_job_id"):
                    delivery = {**context, "outputs": {**(context.get("outputs") or {}), context["node_id"]: {
                        **summary, "team_id": context.get("team_id"), "execution_id": context.get("execution_id")}}}
                    await workflow.execute_activity("employee.job.deliver", delivery,
                        start_to_close_timeout=timedelta(days=31), heartbeat_timeout=timedelta(minutes=2), retry_policy=BOOKKEEPING)
                if sequence % 100 == 0:
                    workflow.continue_as_new({**request, "sequence": sequence})
        except BaseException as exc:
            workflow_bug = workflow_bug or is_workflow_bug(exc)
            raise
        finally:
            if not workflow_bug:
                await self._control.unregister_root()


@workflow.defn(name="AgentInstanceReconcilerWorkflow", sandboxed=False)
class AgentInstanceReconcilerWorkflow:
    @workflow.run
    async def run(self, request: dict) -> None:
        for _ in range(100):
            await workflow.execute_activity("agent.instance.reconcile", {},
                start_to_close_timeout=timedelta(minutes=2), retry_policy=BOOKKEEPING)
            await workflow.sleep(timedelta(seconds=30))
        workflow.continue_as_new({})


INSTANCE_ACTIVITIES = [bootstrap_instance, dispatch_wait, claim_instance, checkpoint_instance,
                       drain_instance, admit_instance_operation, halt_instance, record_invalid_instance_request,
                       complete_instance, reconcile_instances, execute_instance_tool, execute_instance_node]
