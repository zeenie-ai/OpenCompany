"""Exercise real MCP/Python entrypoints, before their typed handlers run."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from models.database import AgentTeam, TeamMember
from services.agent_instances.admission import AdmissionHalted, restore_gate
from services.agent_instances.service import AgentInstanceService
from services.agent_instances.tools import OPERATIONS


@pytest.fixture
async def admitted_instance(real_database, monkeypatch):
    from weakref import WeakValueDictionary
    from services.agent_instances import admission
    # Each real_database fixture uses deterministic instance/run IDs in a
    # separate database. A failed test traceback can retain its weak gate;
    # that process-local registry must not cross these database boundaries.
    monkeypatch.setattr(admission, "_gates", WeakValueDictionary())
    async with real_database.reserved_session() as session:
        session.add(AgentTeam(id="boundary-team", workflow_id="wf", team_lead_node_id="lead", execution_id="exec"))
        session.add(TeamMember(team_id="boundary-team", agent_node_id="lead", agent_type="ai_employee", role="team_lead"))
        session.add(TeamMember(team_id="boundary-team", agent_node_id="worker", agent_type="aiAgent", role="teammate"))
        await session.commit()
    service = AgentInstanceService(real_database)
    scope = dict(workflow_id="wf", generation=1, team_id="boundary-team", job_id="job", execution_id="exec", lead_node_id="lead")
    lead = await service.ensure_lead(scope=scope, template={"node_id": "lead"}, request_id="lead")
    scope["agent_instance_id"] = lead["id"]
    spawned = await service.execute("spawn", scope=scope, arguments={"instructions": "Initial work"}, request_id="spawn",
                                    template={"node_id": "worker", "node_type": "aiAgent"})
    instance_id = spawned["agent"]["id"]
    run = await service.claim_run(instance_id, request_id="run")
    scope.update(agent_instance_id=instance_id, agent_run_id=run["run_id"])
    context = {**scope, "agent_instance_scope": scope, "agent_protocol_version": 1}
    return real_database, service, context


def entry_for(context):
    model = OPERATIONS["spawn"][0]
    return {"node_type": "taskManager", "node_id": "manager", "parameters": {}, "agent_runtime": context,
            "_agent_tool_name": "spawn_worker", "_agent_tool_description": "Spawn worker",
            "_agent_tool_schema": model.model_json_schema(), "_agent_tool_input_model": model,
            "_agent_tool_execution": {"agent_operation": "spawn", "agent_runtime": context}}


async def test_real_mcp_call_counts_missing_fields_before_signature_validation(admitted_instance, monkeypatch):
    from mcp import types
    from services.cli_agent import mcp_server as server
    db, service, context = admitted_instance
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: db)
    executor = AsyncMock()
    monkeypatch.setattr("services.handlers.tools.execute_tool", executor)
    server._reset_for_tests()
    server.get_mcp_app()
    mcp = server._mcp_singleton
    token = server.issue_token()
    server.register_batch(token, server.BatchContext(workflow_id="wf", node_id="worker", workspace_dir=Path.cwd(),
        execution_id="exec", connected_tools=[entry_for(context)]))
    gate = await restore_gate(context, db)
    request_context = SimpleNamespace(request=None, request_id="one")
    monkeypatch.setattr(mcp, "get_context", lambda: SimpleNamespace(request_context=request_context))
    reset = server._current_batch.set(server.lookup_batch(token))
    try:
        listed = {tool.name: tool for tool in await mcp.list_tools()}
        assert listed["spawn_worker"].inputSchema == OPERATIONS["spawn"][0].model_json_schema()
        handler = mcp._mcp_server.request_handlers[types.CallToolRequest]
        for request_id, expected_count in (("one", 1), ("one", 1), ("two", 2), ("three", 3)):
            request_context.request_id = request_id
            result = await handler(types.CallToolRequest(params=types.CallToolRequestParams(name="spawn_worker", arguments={})))
            assert result.root.isError
            assert result.root.structuredContent["error_type"] == "invalid_tool_input"
            assert (await service.admission_state(context["agent_instance_id"], context["agent_run_id"]))["invalid_count"] == expected_count
        request_context.request_id = "four"
        result = await handler(types.CallToolRequest(params=types.CallToolRequestParams(name="spawn_worker", arguments={"instructions": "Too late"})))
        assert result.root.isError
        executor.assert_not_awaited()
        with pytest.raises(AdmissionHalted):
            gate.admit("model")
        assert (await service.admission_state(context["agent_instance_id"], context["agent_run_id"]))["invalid_count"] == 3
    finally:
        server._current_batch.reset(reset)
        server._reset_for_tests()


async def test_rlm_missing_and_positional_arguments_are_persisted_at_callable_boundary(admitted_instance, monkeypatch):
    from services.rlm.adapters import ToolBridgeAdapter
    db, service, context = admitted_instance
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: db)
    executor = AsyncMock()
    monkeypatch.setattr("services.handlers.tools.execute_tool", executor)
    gate = await restore_gate(context, db)
    tool = ToolBridgeAdapter.bridge([entry_for(context)], context, loop=asyncio.get_running_loop(),
                                   parent_node_id="worker", workflow_id="wf")["spawn_worker"]["tool"]
    for args in ((), ("positional instructions",), ()):
        result = await asyncio.to_thread(tool, *args)
        assert result["error_type"] == "invalid_tool_input"
    state = await service.admission_state(context["agent_instance_id"], context["agent_run_id"])
    assert state["invalid_count"] == 3 and len(state["invalid_ids"]) == 3
    with pytest.raises(AdmissionHalted):
        await asyncio.to_thread(tool, instructions="No fourth call")
    with pytest.raises(AdmissionHalted):
        gate.admit("nested_model")
    executor.assert_not_awaited()


async def test_opaque_gate_restores_prior_invalid_count_before_dispatch(admitted_instance):
    db, service, context = admitted_instance
    await service.record_invalid_request(context["agent_instance_id"], context["agent_run_id"], request_id="old-1")
    await service.record_invalid_request(context["agent_instance_id"], context["agent_run_id"], request_id="old-2")
    gate = await restore_gate(context, db)
    assert gate.invalid_count == 2
    await service.record_invalid_request(context["agent_instance_id"], context["agent_run_id"], request_id="old-3")
    with pytest.raises(AdmissionHalted):
        await restore_gate(context, db)
    assert gate.halted
    await service.complete_run(context["agent_instance_id"], context["agent_run_id"],
                               error="invalid request limit", request_id="halted-completion")
    await service.enqueue_input(context["agent_instance_id"], content="Automatic follow-up", request_id="next-turn")
    assert await service.claim_run(context["agent_instance_id"], request_id="automatic-next-turn") is None


async def instance_bridge(db, context, provider):
    from services.cli_agent.context_bridge import SpecializedAgentContextBridge
    return await SpecializedAgentContextBridge.resolve(db, {**context, "kind": "context"},
                                                       provider=provider, agent_node_id="worker")


async def test_claude_uuid_is_durable_before_pool_starts(admitted_instance, monkeypatch, tmp_path):
    from services.cli_agent import mcp_server
    from services.cli_agent.protocol import SessionResult
    from services.cli_agent.service import AICliService
    from services.cli_agent.types import ClaudeTaskSpec

    db, store, context = admitted_instance
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: db)
    svc = AICliService()
    monkeypatch.setattr(svc, "_resolve_repo_root", AsyncMock(return_value=tmp_path))
    monkeypatch.setattr(svc, "_canonical_tool_surface", AsyncMock(return_value=[]))
    monkeypatch.setattr("services.cli_agent.service.create_cli_provider", lambda _: SimpleNamespace(name="claude"))
    monkeypatch.setattr("services.cli_agent.service.get_session_pool", lambda _: SimpleNamespace(terminate=AsyncMock()))

    async def add_worktree(repo, path, branch=None):
        path.mkdir(parents=True, exist_ok=True)
        (path / ".git").write_text("gitdir: test", encoding="utf-8")
        return path
    monkeypatch.setattr("services.cli_agent.worktree.add_worktree", add_worktree)
    async def run(**kwargs):
        state = (await store.get_instance(context["agent_instance_id"]))["continuation"]
        assert state["startup_state"] == "opening"
        assert state["provider_session_id"] == kwargs["task"].session_id
        assert state["cwd"] == str(kwargs["cwd"].resolve())
        assert kwargs["task"].resume_session_id is None
        assert kwargs["task"].continue_session is False
        await kwargs["context_event_sink"]({"type": "system", "subtype": "init", "session_id": state["provider_session_id"]})
        return SessionResult(task_id="turn", session_id=state["provider_session_id"], response="x" * 5000, success=True, cost_usd=0)
    monkeypatch.setattr(svc, "_run_pooled_turn", run)
    mcp_server._reset_for_tests()
    try:
        result = await svc.run_batch("claude", tasks=[ClaudeTaskSpec(prompt="work", continue_session=True, resume_session_id="untrusted-config-id")],
            node_id="worker", workflow_id="wf", workspace_dir=tmp_path, broadcaster=None, instance_context=context)
        assert len(result.tasks[0].response) == 5000
        assert (await store.get_instance(context["agent_instance_id"]))["continuation"]["startup_state"] == "ready"
    finally:
        mcp_server._reset_for_tests()


async def test_unknown_codex_startup_identity_blocks_fresh_retry(admitted_instance, tmp_path):
    db, _, context = admitted_instance
    path = tmp_path / "instance"
    path.mkdir()
    (path / ".git").write_text("gitdir: test", encoding="utf-8")
    bridge = await instance_bridge(db, context, "codex")
    await bridge.prepare_provider_start(path)
    restored = await instance_bridge(db, context, "codex")
    with pytest.raises(ValueError, match="startup did not record"):
        await restored.provider_session_id()
    # The actual first event can resolve the opening obligation.
    await bridge.record_event({"type": "thread.started", "thread_id": "exact-thread"})
    assert await restored.provider_session_id() == "exact-thread"


async def test_resumed_workspace_must_exist_and_match(admitted_instance, tmp_path):
    db, _, context = admitted_instance
    path = tmp_path / "instance"
    path.mkdir()
    marker = path / ".git"
    marker.write_text("gitdir: test", encoding="utf-8")
    bridge = await instance_bridge(db, context, "claude_code")
    await bridge.prepare_provider_start(path, session_id="known-session")
    assert await bridge.provider_session_id() == "known-session"
    with pytest.raises(ValueError, match="workspace changed"):
        await bridge.validate_provider_workspace(tmp_path / "other")
    marker.unlink()
    path.rmdir()
    with pytest.raises(ValueError, match="workspace is missing"):
        await bridge.validate_provider_workspace(path)
    assert not path.exists()
