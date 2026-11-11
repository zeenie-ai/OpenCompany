"""Exercise the real inline tool/plugin boundary for recorded Coding routing."""

from contextlib import asynccontextmanager
from copy import deepcopy
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from nodes.agent.coding_agent import CodingAgentNode  # noqa: F401 -- registry
from nodes.tool.task_manager import TaskManagerNode  # noqa: F401 -- registry
from services.handlers.tools import execute_tool


@pytest_asyncio.fixture
async def runtime(monkeypatch):
    from core.container import container
    from models.database import NodeOutput
    from models.employees import Employee, EmployeeApply, EmployeeJob
    from models.pi_checkpoint import PiRuntimeBlob
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy import select

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        for model in (PiRuntimeBlob, NodeOutput, Employee, EmployeeApply, EmployeeJob):
            await connection.run_sync(model.__table__.create)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session():
        async with sessions() as value:
            yield value

    async def get_output(session_id, output_name):
        async with session() as value:
            result = await value.execute(select(NodeOutput).where(
                NodeOutput.session_id == session_id, NodeOutput.output_name == output_name))
            row = result.scalar_one_or_none()
            return {"data": row.data} if row else None

    plan = {"version": 2, "lead_node_id": "coding", "talk_node_id": "talk",
            "members": [], "delivery_node_ids": ["chat"], "talk_delivery_node_ids": ["chat"]}
    nodes = [{"id": "parent", "type": "orchestrator_agent"},
             {"id": "coding", "type": "coding_agent", "data": {
                 "employee_recipe_version": 2, "employee_team_plan": plan}},
             {"id": "talk", "type": "chatAgent"}, {"id": "chat", "type": "chatReply"}]
    edges = [{"source": "coding", "target": "parent", "targetHandle": "input-teammates"}]
    parameters = {node["id"]: {"model": "admitted-model"} for node in nodes}
    # A live generation: dispatch_job cancels a job of an ended one (1b.21).
    admitted = SimpleNamespace(graph_snapshot={"nodes": nodes, "edges": edges, "parameters": parameters}, status="running")
    database = SimpleNamespace(
        get_workflow_control_generation=AsyncMock(return_value=admitted),
        get_session=session, reserved_session=session,
        get_node_output_by_session=get_output,
        get_node_parameters=AsyncMock(return_value={"model": "mutated-model", "api_key": "secret"}),
        get_user_settings=AsyncMock(return_value={}),
        get_durable_team_task=AsyncMock(return_value={"id": "task", "status": "queued", "assigned_to": "coding"}),
        get_workflow=AsyncMock(return_value=SimpleNamespace(data={"nodes": [], "edges": [], "graphVersion": 99})),
    )
    async with session() as value:
        value.add(Employee(id="employee", workflow_id="wf", owner_id="alice", hire_state="ready"))
        await value.commit()
    team = SimpleNamespace(
        assign_durable_task=AsyncMock(return_value={"id": "task", "team_id": "team", "status": "queued"}),
        get_or_create_execution_team=AsyncMock(return_value={"team_id": "team"}),
    )
    client = SimpleNamespace(start_workflow=AsyncMock(), execute_workflow=AsyncMock())
    service = SimpleNamespace(_temporal_executor=SimpleNamespace(client=client),
                              _resolve_workflow_slug=AsyncMock(return_value="managed"),
                              _get_workspace_dir=MagicMock(return_value="managed-workspace"))
    monkeypatch.setattr(container, "workflow_service", lambda: service)
    monkeypatch.setattr(container, "temporal_client", lambda: SimpleNamespace(client=client))
    monkeypatch.setattr(container, "settings", lambda: SimpleNamespace(temporal_task_queue="framework"))
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(temporal_enabled=True,
        temporal_task_queue="framework", max_delegation_depth=2, max_concurrent_subagents=3))
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: database)
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: team)
    monkeypatch.setattr("services.employees.permissions.assert_runtime_access", AsyncMock())
    monkeypatch.setattr("services.employees.team_runtime.team_runtime_error", lambda: None)
    queue = AsyncMock()
    monkeypatch.setattr("services.temporal.agent_activities.queue_agent_delegation", queue)
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: None)
    scope = {"pi_harness_version": 1, "pi_local_control_version": 1,
             "pi_local_control_state": "running", "pi_local_control_revision": 3,
             "node_type": "taskManager", "node_id": "manager", "parameters": {},
             "parent_node_id": "parent", "workflow_id": "wf", "execution_id": "execution",
             "root_execution_id": "root", "user_id": "alice", "credential_customer_id": "alice-tokens",
             "tool_call_id": "recorded-call", "operation_id": "recorded-operation", "generation": 4,
             "data_scope_id": "original-data", "context_execution_id": "context-run",
             "context_session_id": "context-session", "session_id": "session",
             "run_scope": "owner_chat", "nodes": nodes, "edges": edges, "parameter_snapshot": parameters,
             "outputs": {"trigger": {"recipient": "original"}},
             "ai_service": object(), "database": database}
    monkeypatch.setattr("services.plugin.deps.get_ai_service", lambda: scope["ai_service"])
    yield SimpleNamespace(database=database, client=client, scope=scope, team=team, queue=queue, plan=plan)
    await engine.dispose()


def assignment():
    return {"operation": "assign_task", "title": "Edit", "mission": "Implement the change",
            "assignee_node_id": "coding", "context": {"file": "app.py"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("controlled", [False, True])
async def test_execute_tool_task_manager_starts_recorded_pi_wrapper(runtime, controlled):
    scope = dict(runtime.scope)
    if controlled:
        scope.update(execution_control_version=1, controller_workflow_id="controller",
                     execution_control_state="paused", execution_control_revision=7,
                     execution_control_producers_held=True)
    answer = await execute_tool("task_manager", assignment(), scope)
    assert "delegation" in answer, answer
    assert answer["delegation"]["status"] == "delegated"
    runtime.client.start_workflow.assert_awaited_once()
    assert runtime.client.start_workflow.await_args.args[0] == "PiInlineDelegationWorkflow"
    request = runtime.client.start_workflow.await_args.args[1]
    child = request["child_context"]
    assert child["pi_harness_version"] == child["pi_local_control_version"] == 1
    for key in ("user_id", "credential_customer_id", "workflow_id", "execution_id",
                "root_execution_id", "generation", "data_scope_id", "context_execution_id",
                "context_session_id", "session_id", "run_scope", "pi_local_control_revision"):
        assert child[key] == scope[key]
    if controlled:
        for key in ("controller_workflow_id", "execution_control_version", "execution_control_state",
                    "execution_control_revision", "execution_control_producers_held"):
            assert child[key] == scope[key]
    assert child["node_data"]["model"] == "admitted-model"
    assert isinstance(child["node_data"]["prompt"], str)
    expected_context = {"file": "app.py", "acceptance_criteria": None, "depends_on": []}
    assert json.loads(child["node_data"]["prompt"]) == expected_context
    assert json.loads(child["invocation"]["context"]) == expected_context
    assert request["lifecycle"]["context"] == expected_context
    assert request["lifecycle"]["trace_id"] == "recorded-operation"
    assert child["workspace_dir"] == "managed-workspace"
    assert "database" not in child and "ai_service" not in child
    runtime.database.get_node_parameters.assert_not_awaited()
    runtime.client.execute_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pi_version", [None, 1])
async def test_execute_tool_submit_job_freezes_and_dispatches_recorded_harness(runtime, monkeypatch, pi_version):
    from models.employees import EmployeeJob
    from services.temporal import employee_job_workflow as module
    scope = {**runtime.scope, "parent_node_id": "talk", "graphVersion": 2}
    if pi_version is None:
        for key in list(scope):
            if key.startswith("pi_"):
                scope.pop(key)
    arguments = {"operation": "submit_job", "mission": "Implement the feature"}
    answer = await execute_tool("task_manager", arguments, scope)
    assert "job_id" in answer, answer
    async with runtime.database.get_session() as session:
        job = await session.get(EmployeeJob, answer["job_id"])
    assert job.source.get("pi_harness_version") == pi_version
    assert job.source["user_id"] == "alice" and job.source["generation"] == 4
    assert job.source["parameters"]["coding"]["model"] == "admitted-model"
    assert job.source["outputs"] == scope["outputs"] and job.delivery["node_ids"] == ["chat"]
    if pi_version == 1:
        assert job.source["pi_local_control_version"] == 1
    assert runtime.client.start_workflow.await_args.args[0] == "EmployeeJobWorkflow"
    context = runtime.client.start_workflow.await_args.args[1]
    assert context.get("pi_harness_version") == pi_version
    # Exercise the EmployeeJobWorkflow selector with its persisted input.
    execute_child = AsyncMock(return_value={"success": False, "error": "fixture terminal"})
    monkeypatch.setattr(module.workflow, "execute_child_workflow", execute_child)
    monkeypatch.setattr(module.workflow, "execute_activity", AsyncMock())
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id=job.id))
    await module.EmployeeJobWorkflow(context)._run_job(deepcopy(context))
    assert execute_child.await_args.args[0] == ("PiCodingAgentWorkflow" if pi_version == 1 else "AgentWorkflow")
    runtime.database.get_node_parameters.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmarked_task_manager_assignment_preserves_native_dispatch(runtime, monkeypatch):
    native = AsyncMock(return_value={"success": True, "status": "delegated", "task_id": "task"})
    monkeypatch.setattr("services.handlers.tools._execute_delegated_agent", native)
    scope = {key: value for key, value in runtime.scope.items() if not key.startswith("pi_")}
    answer = await execute_tool("task_manager", assignment(), scope)
    assert "delegation" in answer, answer
    assert answer["delegation"]["task_id"] == "task"
    child_config = native.await_args.args[1]
    assert "pi_harness_version" not in child_config
    assert child_config["ai_service"] is scope["ai_service"]
    assert child_config["database"] is scope["database"]
    assert native.await_args.kwargs["precreated_task_id"] == "task"
    runtime.client.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
async def test_temporal_task_manager_without_inline_services_returns_only_envelope(runtime):
    scope = {key: value for key, value in runtime.scope.items() if key not in {"ai_service", "database"}}
    answer = await execute_tool("task_manager", assignment(), scope)
    assert "delegation" in answer, answer
    assert answer["delegation"] is None
    assert answer["delegation_request"]["team_task_id"] == "task"
    runtime.client.start_workflow.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [
    {**assignment(), "pi_harness_version": None, "user_id": "mallory",
     "credential_customer_id": "mallory-tokens", "workflow_id": "forged",
     "execution_id": "forged", "root_execution_id": "forged", "generation": 99,
     "parameter_snapshot": {}, "database": "forged", "controller_workflow_id": "forged",
     "pi_local_control_revision": 99},
    {"operation": "submit_job", "mission": "Implement the feature", "pi_harness_version": 1,
     "lead_node_id": "mallory", "delivery_node_ids": ["mallory"], "parameter_snapshot": {},
     "user_id": "mallory", "generation": 99},
    {**assignment(), "pi_harness_version": 1},
])
@pytest.mark.parametrize("pi_version", [None, 1])
async def test_task_manager_rejects_model_supplied_runtime_scope(runtime, arguments, pi_version):
    from models.employees import EmployeeJob
    from sqlalchemy import select

    scope = dict(runtime.scope)
    if pi_version is None:
        scope = {key: value for key, value in scope.items() if not key.startswith("pi_")}
    if arguments["operation"] == "submit_job":
        scope.update(parent_node_id="talk", graphVersion=2)
    answer = await execute_tool("task_manager", arguments, scope)

    assert answer["error_type"] == "ValidationError"
    assert "Extra inputs are not permitted" in answer["error"]
    runtime.team.assign_durable_task.assert_not_awaited()
    runtime.client.start_workflow.assert_not_awaited()
    runtime.queue.assert_not_awaited()
    async with runtime.database.get_session() as session:
        assert not (await session.execute(select(EmployeeJob))).scalars().all()


@pytest.mark.asyncio
async def test_rlm_source_task_manager_call_reaches_real_pi_admission(runtime):
    from nodes.tool.task_manager import TaskManagerToolInput
    from services.rlm.adapters import ToolBridgeAdapter
    scope = {key: value for key, value in runtime.scope.items() if key not in {"tool_call_id", "operation_id"}}
    tool = {"node_id": "manager", "node_type": "taskManager", "parameters": {},
            "_agent_tool_name": "task_manager", "_agent_tool_input_model": TaskManagerToolInput}
    tools = ToolBridgeAdapter.bridge([tool], context=scope, loop=asyncio.get_running_loop(),
        parent_node_id="parent", workflow_id="wf")

    def execute_code(code):
        namespace = {"task_manager": tools["task_manager"]["tool"]}
        exec(code, namespace, namespace)
        return namespace["answer"]

    answer = await asyncio.to_thread(execute_code,
        'answer = task_manager(operation="assign_task", title="Edit", mission="Implement", assignee_node_id="coding", context={"file":"app.py"})')
    assert answer["delegation"]["status"] == "delegated"
    request = runtime.client.start_workflow.await_args.args[1]
    assert request["lifecycle"]["trace_id"].startswith("rlm:")
    assert request["child_context"]["parameter_snapshot"]["coding"]["model"] == "admitted-model"


@pytest.mark.asyncio
async def test_native_ai_service_task_manager_uses_shared_recorded_call_identity(runtime, monkeypatch):
    from services import ai as module
    from services.ai import AIService
    from services.llm.protocol import LLMResponse, ToolCall
    service = object.__new__(AIService)
    service.database = runtime.database
    service.settings = SimpleNamespace(tool_result_max_chars=0)
    service._prepare_context = AsyncMock(return_value=None)
    runtime.database.get_tool_schema = AsyncMock(return_value=None)
    runtime.database.get_user_settings.return_value = {"agent_recursion_limit": 2}
    service.chat_unifier = SimpleNamespace(chat=AsyncMock(side_effect=[
        LLMResponse(tool_calls=[ToolCall(id="native-recorded-call", name="task_manager", args=assignment())]),
        LLMResponse(content="delegated"),
    ]))
    monkeypatch.setattr(module, "is_model_valid_for_provider", lambda *_: True)
    monkeypatch.setattr(module, "_resolve_max_tokens", lambda *_: 100)
    monkeypatch.setattr(module, "_resolve_temperature", lambda *_: 0)
    context = {key: value for key, value in runtime.scope.items() if key not in {"tool_call_id", "operation_id"}}
    result = await service.execute_agent("parent", {"prompt": "Assign the work", "provider": "openai",
        "model": "test", "api_key": "test"}, context=context, workflow_id="wf",
        tool_data=[{"node_id": "manager", "node_type": "taskManager", "parameters": {}}])
    assert result["success"] is True, result
    assert runtime.client.start_workflow.await_count == 1
    request = runtime.client.start_workflow.await_args.args[1]
    assert request["lifecycle"]["trace_id"] == "openai:iteration:1:tool:1:native-recorded-call"
    second_turn = service.chat_unifier.chat.await_args_list[1].kwargs["messages"]
    receipt = next(message for message in second_turn if message.role == "tool")
    assert json.loads(receipt.content)["delegation"]["status"] == "delegated"


@pytest.mark.asyncio
async def test_claude_mcp_task_manager_reaches_real_pi_admission(runtime, monkeypatch):
    from nodes.tool.task_manager import TaskManagerToolInput
    from services.cli_agent import mcp_server, workflow_tools
    from services.temporal.pi_protocol import canonical_digest
    scope = {key: value for key, value in runtime.scope.items() if key not in {"tool_call_id", "operation_id"}}
    request = SimpleNamespace(request_id=1, request=SimpleNamespace(headers={"mcp-session-id": "validated-session"}))
    monkeypatch.setattr(workflow_tools, "_get_mcp", lambda: SimpleNamespace(
        get_context=lambda: SimpleNamespace(request_context=request)))
    ctx = mcp_server.BatchContext(workflow_id="wf", node_id="parent", execution_id="execution",
        workspace_dir=Path("workspace"), user_id="owner",
        broadcaster=SimpleNamespace(update_node_status=AsyncMock(), broadcast_agent_capability=AsyncMock()),
        connected_tools=[{"node_id": "manager", "node_type": "taskManager", "parameters": {},
            "_agent_tool_name": "task_manager", "_agent_tool_input_model": TaskManagerToolInput,
            "pi_harness_version": 1, "_pi_inline_scope": scope}])
    token = mcp_server._current_batch.set(ctx)
    try:
        answer = await workflow_tools._build_handler("task_manager", "taskManager", TaskManagerToolInput)(**assignment())
    finally:
        mcp_server._current_batch.reset(token)
    assert answer["delegation"]["status"] == "delegated"
    admission = runtime.client.start_workflow.await_args.args[1]
    assert admission["lifecycle"]["trace_id"] == "mcp:" + canonical_digest({"session": "validated-session", "request": 1})
    assert admission["child_context"]["user_id"] == "alice"


@pytest.mark.asyncio
async def test_pi_polling_excludes_other_parents_globals_and_explicit_receipts(runtime, monkeypatch):
    from models.database import NodeOutput
    from services.handlers import tools
    await execute_tool("task_manager", assignment(), runtime.scope)
    foreign_live = asyncio.create_task(asyncio.Event().wait())
    monkeypatch.setattr(tools, "_delegated_tasks", {"foreign-running": foreign_live})
    monkeypatch.setattr(tools, "_delegation_results", {
        "foreign-cached": {"task_id": "foreign-cached", "status": "completed", "result": "private-memory"},
        "own-native": {"task_id": "own-native", "status": "completed", "result": "owned-memory"},
    })
    monkeypatch.setattr(tools, "_delegation_scopes", {"own-native": tools._delegation_scope(runtime.scope)})
    async with runtime.database.get_session() as session:
        session.add(NodeOutput(node_id="private", session_id="delegation_foreign-stored", output_name="delegation_result",
            data={"task_id": "foreign-stored", "status": "completed", "result": "private-database"}))
        await session.commit()
    try:
        implicit = await tools.get_delegated_task_status(database=runtime.database, scope=runtime.scope)
        assert {task["task_id"] for task in implicit["tasks"]} == {"task", "own-native"}
        explicit = await tools.get_delegated_task_status(
            ["task", "foreign-running", "foreign-cached", "foreign-stored"], runtime.database, scope=runtime.scope)
        assert next(task for task in explicit["tasks"] if task["task_id"] == "task")["status"] == "running"
        for task in explicit["tasks"]:
            if task["task_id"] != "task":
                assert task == {"task_id": task["task_id"], "status": "not_found"}
        assert "private-" not in json.dumps(explicit)
        # Legacy callers retain their preexisting global polling behavior.
        legacy = await tools.get_delegated_task_status()
        assert {task["task_id"] for task in legacy["tasks"]} == {"foreign-running", "foreign-cached", "own-native"}
    finally:
        foreign_live.cancel()
        await asyncio.gather(foreign_live, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [("user_id", "mallory"), ("parent_node_id", "other-parent"),
    ("root_execution_id", "other-root"), ("workflow_id", "other-workflow"), ("generation", 999)])
async def test_explicit_pi_polling_checks_immutable_full_scope(runtime, key, value, monkeypatch):
    from services.handlers.tools import get_delegated_task_status
    await execute_tool("task_manager", assignment(), runtime.scope)
    # Prove explicit authorization works even outside the bounded recent list.
    monkeypatch.setattr("services.temporal.pi_inline_delegation.list_inline_delegation_ids", AsyncMock(return_value=[]))
    own = await get_delegated_task_status(["task"], runtime.database, scope=runtime.scope)
    assert own["tasks"][0]["status"] == "running"
    rejected = await get_delegated_task_status(["task"], runtime.database, scope={**runtime.scope, key: value})
    assert rejected == {"tasks": [{"task_id": "task", "status": "not_found"}]}


@pytest.mark.asyncio
async def test_marked_parent_native_receipt_remains_scoped_after_restart(runtime, monkeypatch):
    from models.database import NodeOutput
    from services.handlers import tools
    monkeypatch.setattr(tools, "_delegated_tasks", {})
    monkeypatch.setattr(tools, "_delegation_results", {})
    monkeypatch.setattr(tools, "_delegation_scopes", {})
    async with runtime.database.get_session() as session:
        session.add(NodeOutput(node_id="native-child", session_id="delegation_native-task", output_name="delegation_result",
            data={"task_id": "native-task", "status": "completed", "result": "owned-native-result",
                  "delegation_scope": tools._delegation_scope(runtime.scope)}))
        await session.commit()
    owned = await tools.get_delegated_task_status(["native-task"], runtime.database, scope=runtime.scope)
    assert owned["tasks"][0]["result"] == "owned-native-result"
    foreign = await tools.get_delegated_task_status(["native-task"], runtime.database,
        scope={**runtime.scope, "user_id": "mallory"})
    assert foreign == {"tasks": [{"task_id": "native-task", "status": "not_found"}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [("root_execution_id", "other-root"), ("user_id", "bob")])
async def test_real_native_child_receipts_capture_marked_parent_scope(runtime, monkeypatch, key, value):
    from models.database import NodeOutput
    from services import node_registry
    from services.handlers import tools
    entered, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(tools, "_delegated_tasks", {})
    monkeypatch.setattr(tools, "_delegation_results", {})
    monkeypatch.setattr(tools, "_delegation_scopes", {})
    monkeypatch.setattr(tools, "_active_delegations", {})
    monkeypatch.setattr(tools, "_active_delegated_nodes", {})
    monkeypatch.setattr(tools, "_delegation_semaphores", {})
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster",
        lambda: SimpleNamespace(update_node_status=AsyncMock()))
    monkeypatch.setattr("nodes.agent._events.broadcast_agent_task_completed", AsyncMock())

    async def save_output(*, node_id, session_id, output_name, data):
        async with runtime.database.get_session() as session:
            session.add(NodeOutput(node_id=node_id, session_id=session_id, output_name=output_name, data=data))
            await session.commit()
        return True

    runtime.database.save_node_output = save_output

    class NativeNode:
        async def execute(self, node_id, params, ctx):
            entered.set()
            await release.wait()
            return {"success": True, "result": {"response": "native completed"}}

    original = node_registry.get_node_class
    monkeypatch.setattr(node_registry, "get_node_class", lambda name: NativeNode if name == "aiAgent" else original(name))
    config = {**runtime.scope, "node_id": "native", "node_type": "aiAgent",
        "nodes": [*runtime.scope["nodes"], {"id": "native", "type": "aiAgent"}],
        "edges": [{"source": "native", "target": "parent", "targetHandle": "input-tools"}]}
    ack = await execute_tool("delegate_native", {"task": "Native work"}, config)
    child = tools._delegated_tasks[ack["task_id"]]
    second_child = None
    try:
        await entered.wait()
        # Two admitted executions can share the same canvas nodes and mission.
        # A different principal/root must never attach to the first one's live
        # native specialist or its blocking receipt through duplicate tracking.
        second_config = {**config, key: value}
        second_ack = await execute_tool("delegate_native", {"task": "Native work"}, second_config)
        assert second_ack["status"] == "delegated"
        assert second_ack["task_id"] != ack["task_id"]
        second_child = tools._delegated_tasks[second_ack["task_id"]]
        owned = await tools.get_delegated_task_status(database=runtime.database, scope=config)
        assert owned == {"tasks": [{"task_id": ack["task_id"], "status": "running"}]}
        foreign = await tools.get_delegated_task_status([ack["task_id"]], runtime.database,
            scope={**config, "user_id": "mallory"})
        assert foreign == {"tasks": [{"task_id": ack["task_id"], "status": "not_found"}]}
    finally:
        release.set()
        await asyncio.gather(child, *([second_child] if second_child is not None else []))
    receipt = await runtime.database.get_node_output_by_session(f"delegation_{ack['task_id']}", "delegation_result")
    assert receipt["data"]["delegation_scope"] == tools._delegation_scope(config)
    assert receipt["data"]["result"] == "native completed"
    other_receipt = await runtime.database.get_node_output_by_session(f"delegation_{second_ack['task_id']}", "delegation_result")
    assert other_receipt["data"]["delegation_scope"] == tools._delegation_scope(second_config)
    tools._delegation_results.clear()
    tools._delegation_scopes.clear()
    recovered = await tools.get_delegated_task_status([ack["task_id"]], runtime.database, scope=config)
    assert recovered["tasks"][0]["result"] == "native completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"success": True, "task_id": "task", "delegation_request": {"assignee_node_id": "coding"}},
    {"success": True, "result": {"response": "payload"}, "metadata": {"retained": True}},
    {"result": {"response": "payload"}, "metadata": {"retained": True}},
])
async def test_tool_node_public_success_payload_is_not_an_action_envelope(payload):
    from services.plugin import NodeContext, Operation, ToolNode

    class FlatTool(ToolNode, abstract=True):
        @Operation("run")
        async def run(self, ctx, params):
            return deepcopy(payload)

    result = await FlatTool().execute_as_tool({}, {}, NodeContext(node_id="flat", node_type="test"))
    assert result == payload
