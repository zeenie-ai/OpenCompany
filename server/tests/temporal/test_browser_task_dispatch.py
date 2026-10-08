"""Versioned native dispatch, owner cleanup, and admission commands."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from services.temporal import node_invocation as module
from services.temporal.agent_workflow import _frozen_tool_activity_options


async def test_native_invocation_attaches_child_and_counts_completion_once(monkeypatch):
    payload = {"principal": "owner", "workflow_id": "wf", "node_id": "agent", "fingerprint": "hash",
        "context": {"generation": 0, "native_workspace_version": 1},
        "dispatch_version": 1, "dispatch_kind": "native_agent", "history_version": 1}
    result = {"success": True, "result": {"response": "done"}}
    execute = AsyncMock()
    child = AsyncMock(return_value=result)
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "execute_child_workflow", child)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="stable", run_id="attempt"))
    run = module.NodeInvocationWorkflow()
    assert await run.run(payload) == result
    child.assert_awaited_once()
    assert child.await_args.args == ("AgentWorkflow", payload["context"])
    assert child.await_args.kwargs["id"] == "stable:agent"
    assert child.await_args.kwargs["cancellation_type"] == module.workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED
    assert child.await_args.kwargs["parent_close_policy"] == module.workflow.ParentClosePolicy.REQUEST_CANCEL
    assert [call.args[0] for call in execute.await_args_list] == ["workspace_tasks.update_record", "workspace_tasks.update_record", "workflow_runs.record_completion"]
    assert execute.await_args_list[1].args[1]["status"] == "completed"


def test_owner_queue_is_required_even_when_worker_pool_is_disabled():
    policy = {"start_to_close_seconds": 60, "heartbeat_seconds": 10,
        "task_queue": "browser-owner-test", "owner_routing": True,
        "retry_policy": {"initial_interval_seconds": 1, "backoff_coefficient": 2,
            "maximum_interval_seconds": 10, "maximum_attempts": 3, "non_retryable_error_types": []}}
    options = _frozen_tool_activity_options(policy, {"native_workspace_version": 1, "temporal_worker_pool_enabled": False})
    assert options["task_queue"] == "browser-owner-test"
    assert "schedule_to_close_timeout" not in options
    assert options["retry_policy"].maximum_attempts == 3
    policy.pop("owner_routing")
    assert "task_queue" not in _frozen_tool_activity_options(policy, {"temporal_worker_pool_enabled": False})


def test_child_task_owns_its_claim_while_tool_calls_keep_the_parent_token():
    from services.temporal.agent_workflow import _inherited_scope, _trusted_tool_scope
    context = {"native_workspace_version": 1, "browser_bindings": {"browser": {"profile_id": "profile"}},
               "_browser_task_id": "parent"}
    assert "_browser_task_id" not in _inherited_scope(context, delegation=True)
    assert _trusted_tool_scope(context)["_browser_task_id"] == "parent"
    assert _inherited_scope(context, delegation=True)["browser_bindings"] == context["browser_bindings"]


async def test_controller_records_admission_before_child_start(monkeypatch):
    from services.temporal import workspace_tasks_workflow as controller_module
    commands = []
    async def execute(*args, **kwargs):
        commands.append(args[0])
    child = asyncio.Future()
    async def start(*args, **kwargs):
        commands.append("child")
        return child
    monkeypatch.setattr(controller_module.workflow, "execute_activity", execute)
    monkeypatch.setattr(controller_module.workflow, "start_child_workflow", start)
    controller = controller_module.WorkspaceTaskControllerWorkflow({"workflow_id": "wf"})
    payload = {"workflow_id": "wf", "fingerprint": "hash", "context": {"workflow_id": "wf", "execution_id": "task"}, "history_version": 1}
    assert (await controller.submit(payload))["status"] == "accepted"
    assert commands == ["workspace_tasks.admit_record", "child"]
    await controller.submit(payload)
    assert commands == ["workspace_tasks.admit_record", "child"]
    child.set_result({"success": True})
    await asyncio.sleep(0)


async def test_terminal_projection_prevents_duplicate_effects_after_controller_eviction(monkeypatch):
    from services.temporal import workspace_tasks_workflow as controller_module
    execute = AsyncMock(return_value={"status": "completed"})
    start = AsyncMock()
    monkeypatch.setattr(controller_module.workflow, "execute_activity", execute)
    monkeypatch.setattr(controller_module.workflow, "start_child_workflow", start)
    controller = controller_module.WorkspaceTaskControllerWorkflow({"workflow_id": "wf"})
    payload = {"workflow_id": "wf", "fingerprint": "hash", "context": {"workflow_id": "wf", "execution_id": "task"}, "history_version": 1}
    assert (await controller.submit(payload))["status"] == "duplicate"
    start.assert_not_awaited()
    assert controller._submissions == {"task": "hash"}
    assert controller._starting is None
