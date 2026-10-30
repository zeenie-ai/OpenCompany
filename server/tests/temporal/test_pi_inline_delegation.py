"""Inline callers acknowledge durable admission without occupying a driver."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from services.temporal import pi_inline_delegation as module


@pytest_asyncio.fixture
async def runtime(monkeypatch):
    from core.container import container
    from models.database import NodeOutput
    from models.pi_checkpoint import PiRuntimeBlob
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(PiRuntimeBlob.__table__.create)
        await connection.run_sync(NodeOutput.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def get_session():
        async with sessions() as session:
            yield session
    async def get_output(session_id, output_name):
        async with sessions() as session:
            row = (await session.execute(select(NodeOutput).where(NodeOutput.session_id == session_id,
                                                                  NodeOutput.output_name == output_name))).scalar_one_or_none()
            return {"data": row.data} if row else None
    async def save_output(*, node_id, session_id, output_name, data):
        async with sessions() as session:
            row = (await session.execute(select(NodeOutput).where(NodeOutput.session_id == session_id,
                                                                  NodeOutput.output_name == output_name))).scalar_one()
            row.data = data
            await session.commit()
            return True
    database = SimpleNamespace(get_session=get_session, get_node_parameters=AsyncMock(return_value={"api_key": "secret", "model": "custom-model"}),
        get_user_settings=AsyncMock(return_value={}), get_durable_team_task=AsyncMock(return_value={"status": "queued"}),
        get_node_output_by_session=get_output, save_node_output=save_output)
    client = SimpleNamespace(start_workflow=AsyncMock(), execute_workflow=AsyncMock(side_effect=AssertionError("waited for a business result")))
    service = SimpleNamespace(_temporal_executor=SimpleNamespace(client=client), _resolve_workflow_slug=AsyncMock(return_value="managed"),
                              _get_workspace_dir=MagicMock(return_value="trusted-workspace"))
    monkeypatch.setattr(container, "workflow_service", lambda: service)
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(temporal_enabled=True, temporal_task_queue="manager",
        max_delegation_depth=2, max_concurrent_subagents=3))
    team = SimpleNamespace(get_or_create_execution_team=AsyncMock(return_value={"team_id": "team"}))
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: team)
    monkeypatch.setattr("services.employees.permissions.assert_runtime_access", AsyncMock())
    queue = AsyncMock()
    monkeypatch.setattr("services.temporal.agent_activities.queue_agent_delegation", queue)
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: database)
    config = {"node_type": "coding_agent", "node_id": "coding", "parent_node_id": "parent", "workflow_id": "wf",
        "execution_id": "execution", "root_execution_id": "root", "tool_call_id": "recorded-call", "pi_harness_version": 1,
        "user_id": "alice", "database": database, "nodes": [{"id": "parent", "type": "rlm_agent"}, {"id": "coding", "type": "coding_agent"}],
        "edges": [{"source": "coding", "target": "parent", "targetHandle": "input-tools"}], "workspace_dir": "forged",
        "execution_control_version": 1, "controller_workflow_id": "controller", "generation": 3,
        "execution_control_root_run_id": "parent-root", "execution_control_state": "paused", "execution_control_revision": 4}
    yield SimpleNamespace(database=database, client=client, queue=queue, team=team, config=config)
    await engine.dispose()


@pytest.mark.asyncio
async def test_inline_admission_is_start_only_serializable_scoped_and_persisted(runtime):
    from services.handlers.tools import _execute_delegated_agent
    answer = await _execute_delegated_agent({"task": "edit", "context": "context"}, {**runtime.config, "delegation_wait_seconds": 300})
    assert answer["status"] == "delegated"
    assert runtime.client.start_workflow.await_count == 1
    runtime.client.execute_workflow.assert_not_awaited()
    request = runtime.client.start_workflow.await_args.args[1]
    module.validate_inline_request(request)
    child = request["child_context"]
    assert child["user_id"] == "alice" and child["workspace_dir"] == "trusted-workspace"
    assert child["controller_workflow_id"] == "controller" and child["execution_control_state"] == "paused"
    assert "execution_control_root_run_id" not in child
    assert "api_key" not in child["node_data"] and "api_key" not in child["parameter_snapshot"]["coding"]
    runtime.queue.assert_awaited_once_with(request["lifecycle"])
    receipt = await runtime.database.get_node_output_by_session(f"delegation_{answer['task_id']}", "delegation_result")
    assert receipt["data"]["status"] == "running"
    assert runtime.team.get_or_create_execution_team.await_args.kwargs["teammates"][0]["node_id"] == "coding"


@pytest.mark.asyncio
async def test_lost_start_response_reuses_call_and_never_overwrites_terminal_receipt(runtime):
    from temporalio.exceptions import WorkflowAlreadyStartedError
    first = await module.start_inline_pi_delegation({"task": "edit"}, runtime.config)
    request = runtime.client.start_workflow.await_args.args[1]
    runtime.database.get_durable_team_task.return_value = {"status": "submitted", "result": {"response": "done"}}
    result = await module.persist_inline_delegation_status({"request": request})
    assert result["status"] == "completed"
    runtime.client.start_workflow.side_effect = WorkflowAlreadyStartedError(request["runner_workflow_id"], module.INLINE_DELEGATION_WORKFLOW)
    second = await module.start_inline_pi_delegation({"task": "edit"}, runtime.config)
    assert first["task_id"] == second["task_id"]
    assert runtime.queue.await_count == 1  # Completed work is never requeued.
    receipt = await runtime.database.get_node_output_by_session(f"delegation_{first['task_id']}", "delegation_result")
    assert receipt["data"]["status"] == "completed" and receipt["data"]["result"]["response"] == "done"
    runtime.client.execute_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflicting_recorded_call_is_terminal_before_runner_start(runtime):
    await module.start_inline_pi_delegation({"task": "first"}, runtime.config)
    with pytest.raises(Exception) as error:
        await module.start_inline_pi_delegation({"task": "different"}, runtime.config)
    assert error.value.type == "PiProtocolConflict"
    assert runtime.client.start_workflow.await_count == 1


@pytest.mark.asyncio
async def test_depth_and_connectivity_fail_before_team_or_child_effect(runtime):
    with pytest.raises(Exception) as error:
        await module.start_inline_pi_delegation({"task": "edit"}, {**runtime.config, "delegation_depth": 2})
    assert error.value.type == "DelegationDepthExceeded"
    with pytest.raises(Exception):
        await module.start_inline_pi_delegation({"task": "edit"}, {**runtime.config, "edges": []})
    runtime.team.get_or_create_execution_team.assert_not_awaited()
    runtime.client.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_terminal_polling_survives_missing_process_local_state(runtime):
    from services.handlers.tools import get_delegated_task_status
    answer = await module.start_inline_pi_delegation({"task": "edit"}, runtime.config)
    request = runtime.client.start_workflow.await_args.args[1]
    runtime.database.get_durable_team_task.return_value = {"status": "cancelled", "cancellation_reason": "Stopped"}
    await module.persist_inline_delegation_status({"request": request, "error": "cancelled"})
    reply = await get_delegated_task_status(task_ids=[answer["task_id"]], database=runtime.database)
    assert reply["tasks"][0]["status"] == "error"
    assert reply["tasks"][0]["error"] == "Stopped"


def test_scope_forwarding_does_not_copy_model_values_or_services():
    scope = module.forward_inline_scope({"pi_harness_version": 1, "user_id": "alice", "tool_call_id": "id",
                                        "database": object(), "args": {"user_id": "mallory"}})
    assert scope == {"pi_harness_version": 1, "user_id": "alice"}


@pytest.mark.asyncio
async def test_unadmitted_or_modified_request_is_rejected_before_running_status(runtime):
    await module.start_inline_pi_delegation({"task": "edit"}, runtime.config)
    from copy import deepcopy
    request = deepcopy(runtime.client.start_workflow.await_args.args[1])
    request["child_context"]["user_id"] = "mallory"
    request["request_digest"] = module._request_digest(request)
    with pytest.raises(Exception) as error:
        await module.persist_inline_delegation_status({"request": request, "status": "running"})
    assert error.value.type == "PiProtocolConflict"


@pytest.mark.asyncio
async def test_wrapper_does_not_start_or_cancel_work_from_unverified_input(runtime, monkeypatch):
    from temporalio.exceptions import ApplicationError
    await module.start_inline_pi_delegation({"task": "edit"}, runtime.config)
    request = runtime.client.start_workflow.await_args.args[1]
    instance = module.PiInlineDelegationWorkflow(request)

    @asynccontextmanager
    async def action():
        yield

    # The admission check itself refuses (an AttributeError from an incomplete
    # fake would be a bug in workflow code, which records no cleanup; 1b.13).
    instance._control = SimpleNamespace(register_root=AsyncMock(), unregister_root=AsyncMock(),
        wait_until_running=AsyncMock(), begin_bookkeeping=MagicMock(), end_action=MagicMock(), enabled=False,
        action=action)
    execute = AsyncMock(side_effect=ApplicationError("not admitted", type="PiProtocolConflict", non_retryable=True))
    start = AsyncMock()
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    with pytest.raises(ApplicationError):
        await instance.run(request)
    start.assert_not_awaited()
    assert all(call.args[0] == "coding.inline_delegation_status" for call in execute.await_args_list)
    instance._control.unregister_root.assert_awaited_once()


@pytest.mark.asyncio
async def test_tool_ack_does_not_publish_child_terminal_success(runtime, monkeypatch):
    from services.handlers.tools import execute_tool
    broadcaster = SimpleNamespace(update_node_status=AsyncMock())
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: broadcaster)
    result = await execute_tool("delegate_to_coding", {"task": "edit"}, runtime.config)
    assert result["status"] == "delegated" and result["agent_node_id"] == "coding"
    assert [call.args[1] for call in broadcaster.update_node_status.await_args_list] == ["executing"]


@pytest.mark.asyncio
async def test_implicit_polling_recovers_only_this_durable_parent_scope(runtime):
    from services.handlers.tools import get_delegated_task_status
    ack = await module.start_inline_pi_delegation({"task": "edit"}, runtime.config)
    response = await get_delegated_task_status(database=runtime.database, scope=runtime.config)
    assert [task["task_id"] for task in response["tasks"]] == [ack["task_id"]]
    assert response["tasks"][0]["status"] == "running"
    other = await get_delegated_task_status(database=runtime.database, scope={**runtime.config, "user_id": "mallory"})
    assert other["tasks"] == []
