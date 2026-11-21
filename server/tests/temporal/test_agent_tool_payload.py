"""Realistic lifecycle manifests stay bounded and retain trusted spawn scope."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.converter import DefaultPayloadConverter


async def prepare_team(monkeypatch, database, *, frozen=False):
    from models.database import AgentTeam, TeamMember
    from services.agent_instances.service import AgentInstanceService
    from services.temporal.agent_activities import prepare_agent_payload
    from services.temporal.agent_instance_workflow import turn_context

    async with database.reserved_session() as session:
        session.add(AgentTeam(id="team", workflow_id="wf", team_lead_node_id="lead", execution_id="exec"))
        for identifier, role, kind in [("lead", "team_lead", "ai_employee"), ("worker", "teammate", "aiAgent"),
                                        ("second", "teammate", "aiAgent")]:
            session.add(TeamMember(team_id="team", agent_node_id=identifier, agent_type=kind, role=role))
        await session.commit()
    params = {"provider": "openai", "model": "test-model", "system_message": "Coordinate useful work"}
    if frozen:
        params["runtime_behavior_version"] = 1
    nodes = [{"id": "lead", "type": "ai_employee", "data": {}},
             {"id": "worker", "type": "aiAgent", "data": {"label": "Research"}},
             {"id": "second", "type": "aiAgent", "data": {"label": "Review"}}]
    edges = [{"source": node, "target": "lead", "targetHandle": "input-teammates"} for node in ("worker", "second")]
    parameters = {node["id"]: dict(params) for node in nodes}
    # The reported employee had a 35 KB input with thirty configured nodes.
    for index in range(27):
        identifier = f"configured-{index}"
        nodes.append({"id": identifier, "type": "simpleMemory", "data": {}})
        parameters[identifier] = {"content": "Saved specialist configuration. " * 45}
    runtime = {"nodes": nodes, "edges": edges, "parameter_snapshot": parameters,
               "user_id": "owner", "session_id": "session", "workspace_dir": "configured-workspace"}
    scope = {"workflow_id": "wf", "generation": 1, "team_id": "team", "job_id": "job",
             "execution_id": "exec", "root_execution_id": "root", "lead_node_id": "lead"}
    store = AgentInstanceService(database)
    lead = await store.ensure_lead(scope=scope, template={"node_id": "lead", "node_type": "ai_employee",
        "parameters": params, "runtime_context": runtime, "initial_instructions": "Find evidence"}, request_id="lead")
    claimed = await store.claim_run(lead["id"], request_id="initial")
    context = turn_context(claimed)

    monkeypatch.setattr(database, "get_node_parameters", AsyncMock(side_effect=lambda node: parameters.get(node, {})))
    monkeypatch.setattr(database, "get_user_settings", AsyncMock(return_value=None))
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: database,
        auth_service=lambda: SimpleNamespace(get_api_key=AsyncMock(return_value=None), has_valid_key=AsyncMock(return_value=True)),
        ai_service=lambda: SimpleNamespace(_build_tool_from_node=AsyncMock(return_value=(None, {}))),
        workflow_service=lambda: SimpleNamespace(_param_resolver=SimpleNamespace(
            resolve=AsyncMock(side_effect=lambda parameters, *args, **kwargs: parameters)))))
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: database)
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(max_concurrent_subagents=3,
        max_delegation_depth=2, agent_recursion_limit=20, tool_result_max_chars=100_000))
    monkeypatch.setattr("services.llm.config.is_model_valid_for_provider", lambda *_: True)
    monkeypatch.setattr("services.llm.config.resolve_max_tokens", lambda *_: 1000)
    monkeypatch.setattr("services.llm.config.resolve_temperature", lambda *_: 0.2)
    monkeypatch.setattr("services.compaction.get_compaction_service", lambda: None)
    monkeypatch.setattr("services.employees.permissions.assert_runtime_access", AsyncMock())
    monkeypatch.setattr("services.employees.team_runtime.employee_runtime_plan", AsyncMock(return_value={"lead_node_id": "lead"}))
    monkeypatch.setattr("services.employees.team_runtime.admit_employee_runtime_job", AsyncMock())
    monkeypatch.setattr("services.plugin.edge_walker.collect_agent_connections", AsyncMock(return_value=(None, [], [], {}, None)))
    monkeypatch.setattr("services.agent_instances.runtime.wake_instances", AsyncMock())
    monkeypatch.setattr("services.browser_resources.validate_saved_browser_resources", AsyncMock())
    return await prepare_agent_payload(context), context, parameters, store


def encoded_bytes(value):
    return DefaultPayloadConverter().to_payload(value).ByteSize()


@pytest.mark.asyncio
@pytest.mark.parametrize("frozen", [False, True])
async def test_real_manifest_is_small_and_workflow_dispatch_spawns_authorized_child(monkeypatch, real_database, frozen):
    import services.temporal.agent_workflow as module
    from services.temporal.agent_instance_workflow import execute_instance_tool
    from tests.temporal.test_agent_workflow_pressure import _final, _tool_turn

    prepared, context, parameters, store = await prepare_team(monkeypatch, real_database, frozen=frozen)
    spawns = [tool for tool in prepared["tools"] if tool.get("agent_operation") == "spawn"]
    assert len(spawns) == 2
    assert len([tool for tool in prepared["tools"] if tool.get("agent_operation")]) == 21
    assert encoded_bytes(prepared) < 256_000  # Old per-binding copies exceed 3 MB.
    assert "runtime_context" not in spawns[0]["agent_runtime"]
    if frozen:
        assert spawns[0]["agent_runtime"]["runtime_behavior_version"] == 1
    assert prepared["agent_tool_runtime"]["parameter_snapshot"] == parameters
    seen, spawned = [], []

    async def execute(name, arg=None, *, args=None, **kwargs):
        data = arg if arg is not None else (args or [{}])[0]
        if name == "agent.prepare_payload":
            return prepared
        if name == "agent.instance.drain":
            return {"messages": [], "deliveries": []}
        if name == "agent.instance.admit":
            return {"allowed": True}
        if name == "agent.execute_llm_step":
            seen.append(data)
            assert all(set(tool) <= {"name", "description", "parameters"} for tool in data["tools"])
            if len(seen) == 1:
                result = _tool_turn(("spawn-call", spawns[0]["name"]))
                result["calls"][0]["args"] = {"instructions": "Find cited facts"}
                return result
            return _final()
        if name == "agent.instance.tool":
            assert encoded_bytes(data) < 256_000
            runtime = data["agent_tool_config"]["agent_runtime"]["runtime_context"]
            assert runtime["parameter_snapshot"] == parameters
            result = await execute_instance_tool(data)
            spawned.append(result)
            return result
        if name == "agent.instance.checkpoint":
            return {"revision": data["expected_revision"] + 1}
        if name == "agent.finalize_team":
            return {"status": "active"}
        if name in {"agent.broadcast_progress", "agent.skill.clear", "agent.store_output"}:
            return {}
        raise AssertionError(name)

    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="physical", run_id="physical-run"))
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    result = await module.AgentWorkflow().run(context)
    assert result["success"], result
    assert spawned[0]["success"], spawned[0]
    child = await store.get_instance(spawned[0]["agent"]["id"])
    assert child["config"]["runtime_context"]["parameter_snapshot"] == parameters
    assert child["config"]["runtime_context"]["user_id"] == "owner"
    assert child["template_node_id"] == spawns[0]["agent_template"]["node_id"]

    # Compact bindings do not weaken the graph check at execution time.
    rejected = module._lifecycle_binding(spawns[0], {**context, "edges": []}, prepared)
    refusal = await execute_instance_tool({"agent_tool_config": rejected,
        "tool_args": {"instructions": "Unconnected target"}, "tool_call_id": "disconnected"})
    assert refusal["error_type"] == "invalid_target"


@pytest.mark.asyncio
async def test_refresh_adds_spawn_bindings_and_rollover_preserves_compact_manifest(monkeypatch, real_database):
    from models.database import TeamMember
    from services.temporal.agent_activities import refresh_agent_tools
    from services.temporal.agent_instance_workflow import execute_instance_tool
    from services.temporal.agent_workflow import _carried_payload, _lifecycle_binding

    prepared, context, parameters, store = await prepare_team(monkeypatch, real_database)
    original = {"nodes": context["nodes"], "edges": context["edges"]}
    new_node = {"id": "added", "type": "aiAgent", "data": {"label": "New specialist"}}
    new_edge = {"source": "added", "target": "lead", "targetHandle": "input-teammates"}
    graph = {"nodes": [*original["nodes"], new_node], "edges": [*original["edges"], new_edge]}
    parameters["added"] = {"provider": "openai", "model": "test-model", "system_message": "New scoped role"}
    monkeypatch.setattr(real_database, "get_workflow", AsyncMock(return_value=SimpleNamespace(data=graph)))
    async with real_database.reserved_session() as session:
        session.add(TeamMember(team_id="team", agent_node_id="added", agent_type="aiAgent", role="teammate"))
        await session.commit()
    manager = next(tool for tool in prepared["tools"] if tool.get("agent_operation"))
    refreshed = await refresh_agent_tools({
        "operations": [{"type": "add_node", "node_type": "aiAgent", "minted_id": "added"}, {"type": "add_edge", **new_edge}],
        "workflow_id": "wf", "invoking_agent_node_id": "lead", "agent_node_type": "ai_employee",
        "graph_snapshot": original, "parameter_snapshot": {key: value for key, value in parameters.items() if key != "added"},
        "bound_node_ids": [tool["tool_node_id"] for tool in prepared["tools"]],
        "bound_binding_keys": [tool["binding_key"] for tool in prepared["tools"]],
        "agent_tool_runtime": prepared["agent_tool_runtime"], "agent_tool_identity": manager["agent_runtime"],
        "agent_task_manager_node_id": manager["tool_node_id"],
    })
    lifecycle = [tool for tool in refreshed["tools"] if tool.get("agent_operation")]
    assert {tool["agent_operation"] for tool in lifecycle} == {"spawn", "reassign"}
    assert encoded_bytes(refreshed) < 64_000
    current = {**context, **refreshed["graph_snapshot"], "parameter_snapshot": parameters}
    all_tools = [*prepared["tools"], *refreshed["tools"]]
    resume = {**current, "_agent_resume": {"prepared_payload": _carried_payload(prepared), "tools": all_tools}}
    assert encoded_bytes(resume) < 384_000
    carried = resume["_agent_resume"]["prepared_payload"]
    spawn = next(tool for tool in resume["_agent_resume"]["tools"] if tool.get("agent_operation") == "spawn"
                 and tool["agent_template"]["node_id"] == "added")
    result = await execute_instance_tool({"agent_tool_config": _lifecycle_binding(spawn, resume, carried),
        "tool_args": {"instructions": "Handle the new assignment"}, "tool_call_id": "new-spawn"})
    assert result["success"], result
    child = await store.get_instance(result["agent"]["id"])
    assert child["config"]["template"]["parameters"] == parameters["added"]


def test_legacy_inline_bindings_are_unchanged():
    from services.temporal.agent_workflow import _lifecycle_binding
    old = {"agent_operation": "spawn", "agent_runtime": {"runtime_context": {"nodes": ["recorded"]}}}
    before = deepcopy(old)
    assert _lifecycle_binding(old, {"nodes": ["current"]}, {}) is old
    assert old == before
