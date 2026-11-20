"""Deterministic execution tests for serialized reusable agent turns."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.agent_instances.protocol import enabled, should_bootstrap
from services.temporal.agent_instance_workflow import AgentInstanceSessionWorkflow, turn_context


def claim(run="r1", *, role="teammate", node_type="aiAgent", job=False):
    original = {"nodes": [{"id": "template", "type": node_type}], "edges": [],
                "user_id": "owner", "chat_run_id": "private-chat",
                "execution_control_root_run_id": "ancestor", "execution_control_released_revision": 10}
    if job:
        original["employee_job_id"] = "job"
    value = {"run_id": run, "instance": {"id": "instance", "role": role,
        "template_node_id": "template", "team_id": "team", "job_id": "job", "config": {
            "template": {"node_id": "template", "node_type": node_type, "parameters": {"system_message": "role"}},
            "runtime_context": original,
            "scope": {"workflow_id": "workflow", "generation": 2, "team_id": "team", "job_id": "job",
                      "execution_id": "execution", "root_execution_id": "root", "lead_node_id": "lead"}}},
        "messages": [{"role": "user", "content": "[peer] research"}], "deliveries": [f"delivery-{run}"],
        "transcript": [], "transcript_revision": 0}
    value["context"] = turn_context(value)
    return value


def test_context_separates_template_instance_run_and_owner_chat():
    value = claim()
    context = value["context"]
    assert context["node_id"] == "template"
    assert context["agent_instance_id"] == "instance"
    assert context["agent_run_id"] == "r1"
    assert context["agent_delivery_ids"] == ["delivery-r1"]
    assert context["employee_job_id"] is None
    assert "chat_run_id" not in context
    assert "execution_control_root_run_id" not in context
    assert "execution_control_released_revision" not in context
    assert context["parameter_snapshot"]["template"] == {"system_message": "role"}


def test_version_is_explicit_and_existing_instances_do_not_bootstrap_again():
    assert not enabled({})
    assert enabled({"agent_lifecycle_version": 1})
    assert enabled({"agent_protocol_version": 1})
    context = {"agent_lifecycle_version": 1, "node_type": "ai_employee"}
    assert should_bootstrap(context)
    assert not should_bootstrap({**context, "agent_instance_id": "existing"})


@pytest.mark.asyncio
async def test_session_serializes_turns_and_releases_each_run_permit(monkeypatch):
    import services.temporal.agent_instance_workflow as module
    claimed = [claim("r1"), claim("r2"), None]
    for item in claimed[:-1]:
        item["context"]["max_concurrent_subagents"] = 7
    events = []
    async def execute(name, payload, **kwargs):
        events.append((name, deepcopy(payload)))
        if name == "agent.instance.claim":
            return claimed.pop(0)
        if name == "agent.acquire_subagent_permit":
            assert payload["limit"] == 7
            return {"lease_id": f"lease-{payload['permit_id']}"}
        if name == "agent.instance.admit":
            return {"allowed": True}
        return {}
    async def start(name, context, **kwargs):
        events.append(("child-start", context["agent_run_id"]))
        async def finish():
            events.append(("child-finish", context["agent_run_id"]))
            return {"success": True, "result": {"response": "done"}}
        return finish()
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "start_activity", lambda name, payload, **kwargs: __import__("asyncio").create_task(execute(name, payload, **kwargs)))
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(first_execution_run_id="owner-chain", workflow_id="owner", run_id="physical-run"))
    result = await AgentInstanceSessionWorkflow({}).run({"instance_id": "instance"})
    assert result["success"]
    assert [event for event in events if event[0].startswith("child-")] == [
        ("child-start", "r1"), ("child-finish", "r1"), ("child-start", "r2"), ("child-finish", "r2")]
    names = [name for name, _ in events]
    assert names.index("agent.instance.claim") < names.index("agent.acquire_subagent_permit")
    assert names.index("agent.instance.complete") < names.index("agent.release_subagent_permit")
    assert len([name for name in names if name == "agent.release_subagent_permit"]) == 2
    assert all(payload["workflow_run_id"] == "physical-run" for name, payload in events if name == "agent.instance.claim")


@pytest.mark.asyncio
async def test_opaque_result_checkpoint_is_separate_from_execution(monkeypatch):
    import asyncio
    import services.temporal.agent_instance_workflow as module
    claimed = [claim(node_type="rlm_agent"), None]
    events = []
    async def execute(name, payload, **kwargs):
        events.append(name)
        if name == "agent.instance.claim":
            return claimed.pop(0)
        if name == "agent.acquire_subagent_permit":
            return {"lease_id": "lease"}
        if name == "agent.instance.admit":
            return {"allowed": True}
        return {}
    def start(name, context, **kwargs):
        if name == "agent.acquire_subagent_permit":
            return asyncio.create_task(execute(name, context, **kwargs))
        assert name == "agent.instance.execute"
        assert kwargs["retry_policy"].maximum_attempts == 1
        events.append("opaque")
        future = asyncio.get_running_loop().create_future()
        future.set_result({"success": True, "agent_transcript": {
            "messages": [{"role": "assistant", "content": "done"}], "expected_revision": 0,
            "delivery_ids": ["delivery-r1"], "request_id": "opaque-turn"}})
        return future
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "start_activity", start)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(first_execution_run_id="owner-chain", workflow_id="owner", run_id="physical-run"))
    await AgentInstanceSessionWorkflow({}).run({"instance_id": "instance"})
    assert events.count("opaque") == 1
    assert events.index("opaque") < events.index("agent.instance.checkpoint") < events.index("agent.instance.complete")


@pytest.mark.asyncio
@pytest.mark.parametrize("reused_call_id", [False, True])
async def test_native_invalid_calls_stop_at_three_and_persist_all_tool_receipts(monkeypatch, reused_call_id):
    import services.temporal.agent_workflow as module
    from tests.temporal.test_agent_workflow_pressure import _payload, _tool, _tool_turn
    tool = {**_tool("spawn_research", "taskManager", "manager"), "agent_operation": "spawn"}
    payload = _payload(tools=[tool], user_prompt="", agent_instance_id="instance", agent_run_id="run",
                       agent_transcript_revision=0, agent_delivery_ids=["delivery"], agent_input_messages=[{"role": "user", "content": "research"}])
    counts = {"model": 0, "tool": 0}
    checkpoints = []
    operation_ids = []
    async def execute(name, arg=None, *, args=None, **kwargs):
        data = arg if arg is not None else (args or [{}])[0]
        if name == "agent.prepare_payload": return payload
        if name == "agent.instance.drain": return {"messages": [], "deliveries": []}
        if name == "agent.instance.admit": return {"allowed": True}
        if name == "agent.execute_llm_step":
            counts["model"] += 1
            if reused_call_id:
                return _tool_turn(("provider-reused-id", "spawn_research"))
            # Five calls in one response must still execute only the first three.
            return _tool_turn(*[(f"call-{i}", "spawn_research") for i in range(5)])
        if name == "agent.instance.tool":
            counts["tool"] += 1
            operation_ids.append(data["agent_tool_config"]["operation_id"])
            if reused_call_id:
                assert data["tool_call_id"] == "provider-reused-id"
            return {"success": False, "error_type": "invalid_tool_input", "error": "instructions required"}
        if name == "agent.instance.checkpoint":
            checkpoints.append(deepcopy(data))
            return {"revision": data["expected_revision"] + 1}
        if name in {"agent.instance.halt", "agent.broadcast_progress", "agent.skill.clear"}: return {}
        raise AssertionError(name)
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="workflow-run", run_id="run-id"))
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(module, "get_node_class", lambda _: None)
    result = await module.AgentWorkflow().run({"node_id": "agent-1", "execution_id": "execution",
        "agent_instance_id": "instance", "agent_run_id": "run"})
    assert result["error_type"] == "agent_turn_halted"
    assert counts == {"model": 3 if reused_call_id else 1, "tool": 3}
    assert len(set(operation_ids)) == 3
    receipts = [item for item in checkpoints[-1]["messages"] if item["role"] == "tool"]
    assert len(receipts) == (3 if reused_call_id else 5)
    assert checkpoints[0]["delivery_ids"] == ["delivery"]
    if reused_call_id:
        assert all(item["tool_call_id"] == "provider-reused-id" for item in receipts)


@pytest.mark.asyncio
async def test_real_store_prepare_manifest_spawn_completion_and_review(monkeypatch, real_database):
    """Exercise the real boundary chain; only provider/config discovery is fake."""
    from models.database import AgentTeam, TeamMember
    from services.agent_instances.service import AgentInstanceService
    from services.temporal.agent_activities import prepare_agent_payload
    from services.temporal.agent_instance_workflow import execute_instance_tool
    db = real_database
    async with db.reserved_session() as session:
        session.add(AgentTeam(id="team", workflow_id="wf", team_lead_node_id="lead", execution_id="exec"))
        session.add(TeamMember(team_id="team", agent_node_id="lead", agent_type="ai_employee", role="team_lead"))
        session.add(TeamMember(team_id="team", agent_node_id="worker", agent_type="aiAgent", role="teammate"))
        await session.commit()
    parameters = {"provider": "openai", "model": "test-model", "system_message": "Configured role"}
    nodes = [{"id": "lead", "type": "ai_employee", "data": {}}, {"id": "worker", "type": "aiAgent", "data": {}}]
    edges = [{"source": "worker", "target": "lead", "targetHandle": "input-teammates"}]
    runtime = {"nodes": nodes, "edges": edges, "user_id": "owner", "session_id": "session"}
    scope = {"workflow_id": "wf", "generation": 1, "team_id": "team", "job_id": "job",
             "execution_id": "exec", "root_execution_id": "root", "lead_node_id": "lead"}
    store = AgentInstanceService(db)
    lead = await store.ensure_lead(scope=scope, template={"node_id": "lead", "node_type": "ai_employee",
        "parameters": parameters, "runtime_context": runtime, "initial_instructions": "Research facts"}, request_id="lead")
    admitted = await store.claim_run(lead["id"], request_id="initial")
    lead_context = turn_context(admitted)

    monkeypatch.setattr(db, "get_node_parameters", AsyncMock(return_value=parameters))
    monkeypatch.setattr(db, "get_user_settings", AsyncMock(return_value=None))
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: db,
        auth_service=lambda: SimpleNamespace(get_api_key=AsyncMock(return_value=None), has_valid_key=AsyncMock(return_value=True)),
        ai_service=lambda: SimpleNamespace(), workflow_service=lambda: SimpleNamespace(
            _param_resolver=SimpleNamespace(resolve=AsyncMock(side_effect=lambda parameters, *args, **kwargs: parameters)))))
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: db)
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
    teammate = {"node_id": "worker", "node_type": "aiAgent", "label": "Research", "parameters": parameters}
    monkeypatch.setattr("services.plugin.edge_walker.collect_teammate_connections", AsyncMock(return_value=[teammate]))
    monkeypatch.setattr("services.agent_instances.runtime.wake_instances", AsyncMock())

    prepared = await prepare_agent_payload(lead_context)
    spawn = next(item for item in prepared["tools"] if item.get("agent_operation") == "spawn")
    assert spawn["definition"]["parameters"]["required"] == ["instructions"]
    assert spawn["agent_runtime"]["agent_instance_id"] == lead["id"]
    assert prepared["conversation_key"]["agent_instance_id"] == lead["id"]
    assert prepared["conversation"] == []
    assert prepared["memory_node_id"] == ""
    assert "Configured role" in prepared["system_message"]
    assert prepared["employee_runtime_delivery"] is False

    result = await execute_instance_tool({"agent_tool_config": spawn, "tool_args": {"instructions": "Find cited facts"},
        "workflow_id": "wf", "execution_id": "exec", "root_execution_id": "root", "parent_node_id": "lead",
        "nodes": nodes, "edges": edges, "tool_call_id": "spawn-call"})
    assert result.get("success", True), result
    child_id, task_id = result["agent"]["id"], result["task"]["id"]
    assert child_id != lead["id"]
    await store.checkpoint(lead["id"], admitted["run_id"], messages=[*admitted["messages"],
        {"role": "assistant", "content": "Assigned research"}], expected_revision=0,
        delivery_ids=admitted["deliveries"], request_id="initial-response")
    await store.complete_run(lead["id"], admitted["run_id"], result={"response": "Assigned research"}, request_id="initial-done")
    worker = await store.claim_run(child_id, request_id="worker-run")
    worker_context = turn_context(worker)
    assert worker_context["node_id"] == "worker"
    assert worker_context["employee_job_id"] is None
    worker_prepared = await prepare_agent_payload(worker_context)
    assert worker_prepared["conversation"] == []
    assert any(item.get("agent_operation") == "send_message" for item in worker_prepared["tools"])
    await store.checkpoint(child_id, worker["run_id"], messages=[*worker["messages"],
        {"role": "assistant", "content": "Facts with sources"}], expected_revision=0,
        delivery_ids=worker["deliveries"], request_id="worker-response")
    await store.complete_run(child_id, worker["run_id"], result={"response": "Facts with sources"}, request_id="worker-done")
    from services.temporal.agent_instance_workflow import bootstrap_instance
    from models.agent_instances import AgentDelivery, AgentInstance
    from sqlmodel import select
    prepare_again = AsyncMock(side_effect=AssertionError("Review events must not prepare another lead"))
    monkeypatch.setattr("services.temporal.agent_activities.prepare_agent_payload", prepare_again)
    event_context = {"node_id": "lead", "node_type": "ai_employee", "workflow_id": "wf", "generation": 1,
        "nodes": [*nodes, {"id": "task-event", "type": "taskTrigger"}],
        "edges": [{"source": "task-event", "target": "lead", "targetHandle": "input-task"}],
        "inputs": {"task-event": {"result": {"task_id": task_id, "status": "completed", "team_id": "team"}}}}
    for _ in range(2):
        routed = await bootstrap_instance(event_context)
        assert routed["review_event"]
        assert routed["result"]["agent_instance_id"] == lead["id"]
    stale = await bootstrap_instance({**event_context, "generation": 2})
    assert stale["result"]["status"] == "ignored_task_event"
    prepare_again.assert_not_awaited()
    async with db.get_session() as session:
        notices = (await session.execute(select(AgentDelivery).where(
            AgentDelivery.recipient_id == lead["id"], AgentDelivery.kind == "task_review"))).scalars().all()
        assert len(notices) == 1
        leads = (await session.execute(select(AgentInstance).where(AgentInstance.role == "lead"))).scalars().all()
        assert len(leads) == 1
    review = await store.claim_run(lead["id"], request_id="review")
    assert review["instance"]["id"] == lead["id"]
    assert any(task_id in message["content"] for message in review["messages"])
    assert any(message.get("content") == "Assigned research" for message in review["transcript"])
    assert not any(message.get("content") == "Facts with sources" for message in review["transcript"])


@pytest.mark.asyncio
async def test_session_programming_error_does_not_complete_or_clean_up_owner(monkeypatch):
    import services.temporal.agent_instance_workflow as module
    events = []
    async def execute(name, payload, **kwargs):
        events.append(name)
        if name == "agent.instance.claim": return claim(role="lead")
        if name == "agent.instance.admit": return {"allowed": True}
        raise AssertionError(name)
    async def start(*args, **kwargs):
        raise ValueError("workflow implementation bug")
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(
        first_execution_run_id="chain", workflow_id="owner", run_id="physical"))
    owner = AgentInstanceSessionWorkflow({})
    monkeypatch.setattr(owner._control, "unregister_root", AsyncMock())
    with pytest.raises(ValueError, match="implementation bug"):
        await owner.run({"instance_id": "instance"})
    assert "agent.instance.complete" not in events
    owner._control.unregister_root.assert_not_awaited()


@pytest.mark.asyncio
async def test_halted_lead_uses_atomic_no_assignment_failure_gate(monkeypatch):
    import services.temporal.agent_instance_workflow as module
    claimed = [claim(role="lead", job=True), None]
    events = []
    async def execute(name, payload, **kwargs):
        events.append((name, payload))
        if name == "agent.instance.claim": return claimed.pop(0)
        if name == "agent.instance.admit": return {"allowed": True}
        return {}
    async def start(*args, **kwargs):
        async def result():
            return {"success": False, "error": "Three invalid requests", "error_type": "agent_turn_halted"}
        return result()
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "start_child_workflow", start)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(
        first_execution_run_id="chain", workflow_id="owner", run_id="physical"))
    await AgentInstanceSessionWorkflow({}).run({"instance_id": "instance"})
    failed = next(payload for name, payload in events if name == "employee.job.failed")
    assert failed["failure_code"] == "lead_no_assignments"
    assert failed["employee_job_id"] == "job"


@pytest.mark.asyncio
async def test_native_wait_releases_capacity_and_returns_fresh_state(monkeypatch, real_database):
    import services.temporal.agent_workflow as module
    from tests.temporal.test_agent_workflow_pressure import _payload, _tool, _tool_turn, _final
    tool = {**_tool("wait_for_agents", "taskManager", "manager"),
        "agent_operation": "wait_for_agents", "execution_traits": ["waits_for_child"]}
    payload = _payload(tools=[tool], user_prompt="", agent_instance_id="instance", agent_run_id="run", max_concurrent_subagents=1)
    state = {"permit": True, "model": 0, "wait": 0, "child_finished": False}
    assert await real_database.acquire_subagent_permit("execution", "only-permit", 1)
    async def execute(name, arg=None, *, args=None, **kwargs):
        data = arg if arg is not None else (args or [{}])[0]
        if name == "agent.prepare_payload": return payload
        if name == "agent.instance.drain": return {"messages": [], "deliveries": []}
        if name == "agent.instance.admit": return {"allowed": True}
        if name == "agent.execute_llm_step":
            assert state["permit"], "Model resumed without its root permit"
            state["model"] += 1
            return _tool_turn((f"wait-call-{state['model']}", "wait_for_agents")) if state["model"] <= 2 else _final()
        if name == "agent.release_subagent_permit":
            await real_database.release_subagent_permit(data["root_execution_id"], data["permit_id"])
            state["permit"] = False
            return {}
        if name == "agent.acquire_subagent_permit":
            assert state["child_finished"]
            assert data["lease_version"] == 2
            lease_id = f"fresh-{kwargs['activity_id']}"
            assert await real_database.acquire_subagent_permit(data["root_execution_id"], lease_id, data["limit"]), "A released lease was reused or the previous lease leaked"
            state["permit"] = True
            return {"lease_id": lease_id}
        if name == "agent.instance.tool":
            assert not state["permit"], "Waiting parent retained the only root permit"
            state["wait"] += 1
            return {"status": "idle" if state["child_finished"] else "pending", "agents": []}
        if name == "agent.instance.checkpoint": return {"revision": data["expected_revision"] + 1}
        if name in {"agent.broadcast_progress", "agent.skill.clear", "agent.store_output"}: return {}
        raise AssertionError(name)
    async def sleep(*args):
        assert not state["permit"]
        state["child_finished"] = True
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "sleep", sleep)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="workflow-run", run_id="run-id"))
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(module, "get_node_class", lambda _: None)
    result = await module.AgentWorkflow().run({"node_id": "agent-1", "execution_id": "execution",
        "agent_instance_id": "instance", "agent_run_id": "run", "team_permit_id": "only-permit"})
    assert result["success"]
    assert state["wait"] == 3
    assert state["model"] == 3
    assert await real_database.acquire_subagent_permit("execution", "after-completion", 1), "Final physical lease was not released"


@pytest.mark.asyncio
async def test_pi_parks_only_driver_slot_before_instance_wait():
    from services.temporal.pi_workflow import PiCodingAgentWorkflow
    value = PiCodingAgentWorkflow({})
    value._pi_ready = True
    value._stop_driver = AsyncMock()
    await value._before_runtime_tool_batch([{"name": "wait_for_agents", "id": "wait-call"}],
        [{"name": "wait_for_agents", "execution_traits": ["waits_for_child"]}])
    value._stop_driver.assert_awaited_once()
    assert value._pi_detached


@pytest.mark.asyncio
async def test_reconciler_only_releases_confirmed_terminal_owner():
    from services.agent_instances.runtime import _recover_missing_owner
    from temporalio.client import WorkflowExecutionStatus
    store = SimpleNamespace(database=object(), get_run=AsyncMock(return_value={
        "id": "run", "instance_id": "instance", "temporal_workflow_id": "agent-instance:instance", "temporal_run_id": "exact-run",
        "input_snapshot": {"instance": {"config": {"template": {"node_type": "rlm_agent"}}}}}),
        complete_run=AsyncMock(return_value={}), fence_orphan_run=AsyncMock())
    handle = SimpleNamespace(describe=AsyncMock(return_value=SimpleNamespace(status=WorkflowExecutionStatus.RUNNING)))
    client = SimpleNamespace(get_workflow_handle=MagicMock(return_value=handle))
    instance = {"id": "instance", "active_run_id": "run"}
    assert not await _recover_missing_owner(store, instance, client)
    store.complete_run.assert_not_awaited()
    store.fence_orphan_run.assert_not_awaited()
    handle.describe.return_value = SimpleNamespace(status=WorkflowExecutionStatus.TERMINATED)
    assert await _recover_missing_owner(store, instance, client)
    client.get_workflow_handle.assert_called_with("agent-instance:instance", run_id="exact-run")
    store.complete_run.assert_awaited_once_with("instance", "run", error="interrupted", request_id="reconciled-stop:run")
    store.fence_orphan_run.assert_awaited_once_with("instance", "run")


@pytest.mark.asyncio
async def test_unrelated_tool_errors_do_not_spend_orchestration_budget(monkeypatch):
    import services.temporal.agent_workflow as module
    from tests.temporal.test_agent_workflow_pressure import _payload, _tool, _tool_turn, _final
    payload = _payload(tools=[_tool("scrape", "tikhubAction", "scraper")], user_prompt="",
        agent_instance_id="instance", agent_run_id="run")
    model_requests = 0
    async def execute(name, arg=None, *, args=None, **kwargs):
        nonlocal model_requests
        data = arg if arg is not None else (args or [{}])[0]
        if name == "agent.prepare_payload": return payload
        if name == "agent.instance.drain": return {"messages": [], "deliveries": []}
        if name == "agent.instance.admit": return {"allowed": True}
        if name == "agent.execute_llm_step":
            model_requests += 1
            return _tool_turn(*[(f"call-{index}", "scrape") for index in range(4)]) if model_requests == 1 else _final()
        if name == "node.tikhubAction.v1":
            assert data["agent_instance_id"] == "instance"
            assert data["agent_run_id"] == "run"
            assert data["task_attempt"] == 4
            return {"success": False, "error_type": "invalid_tool_input", "error": "URL missing"}
        if name == "agent.instance.checkpoint": return {"revision": data["expected_revision"] + 1}
        if name in {"agent.broadcast_progress", "agent.skill.clear", "agent.store_output"}: return {}
        raise AssertionError(name)
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(workflow_id="workflow-run", run_id="run-id"))
    monkeypatch.setattr(module.workflow, "patched", lambda _: True)
    monkeypatch.setattr(module.workflow, "logger", MagicMock())
    monkeypatch.setattr(module, "get_node_class", lambda _: None)
    result = await module.AgentWorkflow().run({"node_id": "agent-1", "execution_id": "execution",
        "agent_instance_id": "instance", "agent_run_id": "run", "task_attempt": 4})
    assert result["success"]
    assert model_requests == 2


@pytest.mark.asyncio
async def test_opaque_activity_forwards_scope_and_waits_for_cancelled_provider_drain(monkeypatch):
    import asyncio
    import services.temporal.agent_instance_workflow as module
    started, cancelled, drained = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def execute_node(**kwargs):
        assert kwargs["extras"]["agent_instance_id"] == "instance"
        assert kwargs["extras"]["agent_run_id"] == "r1"
        assert kwargs["extras"]["agent_delivery_ids"] == ["delivery-r1"]
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            await drained.wait()
            raise
    monkeypatch.setattr("core.container.container", SimpleNamespace(
        workflow_service=lambda: SimpleNamespace(execute_node=execute_node)))
    monkeypatch.setattr(module, "admit_instance_operation", AsyncMock(return_value={"allowed": True}))
    monkeypatch.setattr(module.activity, "heartbeat", lambda *args: None)
    task = asyncio.create_task(module.execute_instance_node(claim(node_type="rlm_agent")["context"]))
    await started.wait()
    task.cancel()
    await cancelled.wait()
    assert not task.done(), "Instance owner acknowledged stop before provider effects drained"
    task.cancel()  # repeated cancellation cannot detach the real executor
    await asyncio.sleep(0)
    assert not task.done()
    drained.set()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_stop_hint_does_not_cancel_newer_or_still_admitted_run(monkeypatch):
    import asyncio
    import services.temporal.agent_instance_workflow as module
    owner = AgentInstanceSessionWorkflow({})
    owner._instance_id, owner._current_run_id = "instance", "new-run"
    handle = asyncio.get_running_loop().create_future()
    owner._current_handle = handle
    monkeypatch.setattr(module.workflow, "execute_activity", AsyncMock(return_value={"allowed": True}))
    await owner.stop_current({})
    assert not handle.cancelled()
    old_handle = handle
    async def replaced(*args, **kwargs):
        owner._current_run_id = "newer-run"
        owner._current_handle = asyncio.get_running_loop().create_future()
        return {"allowed": False}
    monkeypatch.setattr(module.workflow, "execute_activity", replaced)
    await owner.stop_current({})
    assert not old_handle.cancelled()
    assert not owner._current_handle.cancelled()
    monkeypatch.setattr(module.workflow, "execute_activity", AsyncMock(return_value={"allowed": False}))
    await owner.stop_current({})
    assert owner._current_handle.cancelled()


@pytest.mark.asyncio
async def test_admission_rejects_draining_or_fenced_executor(monkeypatch):
    import services.temporal.agent_instance_workflow as module
    store = SimpleNamespace(admission_state=AsyncMock(return_value={"reason": "stale_run"}))
    monkeypatch.setattr(module, "_store", lambda: store)
    assert not (await module.admit_instance_operation({"instance_id": "instance", "run_id": "run"}))["allowed"]
    assert (await module.drain_instance({"instance_id": "instance", "run_id": "run"}))["halted"]


@pytest.mark.asyncio
async def test_stop_agent_drains_pending_child_start_without_generation_resume(monkeypatch):
    import asyncio
    from contextlib import asynccontextmanager
    import services.temporal.agent_instance_workflow as module
    entered = asyncio.Event()
    never_resumed = asyncio.Event()
    claimed = [claim(), None]
    events = []
    allowed = True
    async def execute(name, payload, **kwargs):
        events.append((name, payload))
        if name == "agent.instance.claim": return claimed.pop(0)
        if name == "agent.instance.admit": return {"allowed": allowed}
        if name == "agent.acquire_subagent_permit": return {"lease_id": "initial-physical-lease"}
        return {}
    @asynccontextmanager
    async def stopped_child_gate():
        entered.set()
        await never_resumed.wait()
        yield
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    monkeypatch.setattr(module.workflow, "start_activity", lambda name, payload, **kwargs:
        asyncio.create_task(execute(name, payload, **kwargs)))
    child_start = AsyncMock()
    monkeypatch.setattr(module.workflow, "start_child_workflow", child_start)
    monkeypatch.setattr(module.workflow, "info", lambda: SimpleNamespace(
        first_execution_run_id="chain", workflow_id="owner", run_id="physical"))
    owner = AgentInstanceSessionWorkflow({})
    monkeypatch.setattr(owner._control, "child_start", stopped_child_gate)
    running = asyncio.create_task(owner.run({"instance_id": "instance"}))
    await entered.wait()
    allowed = False  # committed stop_agent fence
    await owner.stop_current({})
    result = await asyncio.wait_for(running, timeout=1)
    assert not result["success"]
    assert not never_resumed.is_set()
    child_start.assert_not_awaited()
    assert next(payload for name, payload in events if name == "agent.instance.complete")["error"] == "interrupted"
    assert next(payload for name, payload in events if name == "agent.release_subagent_permit")["permit_id"] == "initial-physical-lease"


@pytest.mark.asyncio
@pytest.mark.parametrize("completed_before_recovery", [False, True])
async def test_terminal_owner_permit_recovery_waits_for_child_and_fences_late_acquire(monkeypatch, real_database, completed_before_recovery):
    from models.database import AgentTeam, TeamMember
    from services.agent_instances.service import AgentInstanceService
    from services.agent_instances.runtime import _recover_missing_owner, _retire_terminal_run_permits
    from temporalio.client import WorkflowExecutionStatus as Status
    db = real_database
    async with db.reserved_session() as session:
        session.add(AgentTeam(id="permit-team", workflow_id="wf", team_lead_node_id="lead", execution_id="exec"))
        session.add(TeamMember(team_id="permit-team", agent_node_id="lead", agent_type="aiAgent", role="team_lead"))
        await session.commit()
    scope = {"workflow_id": "wf", "generation": 1, "team_id": "permit-team", "job_id": "job",
        "execution_id": "exec", "root_execution_id": "root", "lead_node_id": "lead"}
    store = AgentInstanceService(db)
    instance = await store.ensure_lead(scope=scope, template={"node_id": "lead", "node_type": "aiAgent",
        "parameters": {}, "initial_instructions": "Work"}, request_id="initial")
    admitted = await store.claim_run(instance["id"], request_id="run", workflow_id="session-owner", workflow_run_id="owner-physical")
    run_id = admitted["run_id"]
    child_id = f"agent-instance-run:{run_id}"
    import services.temporal.agent_activities as activities
    async def acquire_with_provenance(root_id, lease_id, limit):
        recorded = await store.get_run(instance["id"], run_id)
        assert any(lease["lease_id"] == lease_id for lease in recorded["permit_leases"]), "Capacity acquired before recovery provenance was durable"
        return await db.acquire_subagent_permit(root_id, lease_id, limit)
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: SimpleNamespace(
        database=db, acquire_subagent_permit=acquire_with_provenance, release_subagent_permit=db.release_subagent_permit))
    monkeypatch.setattr(activities.activity, "info", lambda: SimpleNamespace(
        workflow_id="session-owner", workflow_run_id="owner-physical", activity_id="acquire-first", attempt=1))
    acquired = await activities.acquire_subagent_permit({"agent_instance_id": instance["id"], "agent_run_id": run_id,
        "root_execution_id": "root", "permit_id": run_id, "lease_version": 2, "limit": 1})
    assert acquired["lease_id"].startswith("subagent-lease-v2-")
    # A process can fail after reserving provenance but before capacity is acquired.
    await store.record_run_permit(instance["id"], run_id, lease={"lease_id": "not-yet-acquired",
        "root_execution_id": "root", "workflow_id": child_id, "workflow_run_id": "child-physical"})
    if completed_before_recovery:
        await store.checkpoint(instance["id"], run_id, messages=[*admitted["messages"], {"role": "assistant", "content": "Done"}],
            expected_revision=0, delivery_ids=admitted["deliveries"], request_id="response")
        await store.complete_run(instance["id"], run_id, result={"response": "Done"}, request_id="completed-before-cleanup")
    states = {"session-owner": Status.RUNNING, child_id: Status.RUNNING}
    handles = {}
    def get_handle(identity, **kwargs):
        if identity == "session-owner": assert kwargs.get("run_id") == "owner-physical"
        if identity not in handles:
            async def describe(): return SimpleNamespace(status=states[identity])
            handles[identity] = SimpleNamespace(describe=describe, cancel=AsyncMock())
        return handles[identity]
    client = SimpleNamespace(get_workflow_handle=get_handle)
    async def recover():
        if completed_before_recovery:
            pending = await store.list_pending_permit_runs()
            assert len(pending) == 1
            return await _retire_terminal_run_permits(store, pending[0], client)
        return await _recover_missing_owner(store, await store.get_instance(instance["id"]), client)
    assert not await recover()  # exact session is still running
    states["session-owner"] = Status.TERMINATED
    assert not await recover()  # its independent native child is still running
    assert not await db.acquire_subagent_permit("root", "probe", 1)
    if not completed_before_recovery:
        handles[child_id].cancel.assert_awaited_once()
        assert instance["id"] in await store.list_stopping()
    states[child_id] = Status.COMPLETED
    assert await recover()
    assert not await store.list_pending_permit_runs()
    assert await db.acquire_subagent_permit("root", "probe", 1)
    await db.release_subagent_permit("root", "probe")
    assert not await db.acquire_subagent_permit("root", "not-yet-acquired", 1), "Late executor recreated an orphan reservation"


@pytest.mark.asyncio
async def test_permit_recovery_scan_rotates_without_rewriting_creation_time(real_database):
    from datetime import datetime, timezone
    from models.agent_instances import AgentRun
    from services.agent_instances.service import AgentInstanceService
    async with real_database.reserved_session() as session:
        for suffix in ("a", "b"):
            session.add(AgentRun(id=f"run-{suffix}", instance_id=f"instance-{suffix}", request_id="request",
                fence=1, status="completed", permits_pending=True, created_at=datetime(2000, 1, 1, tzinfo=timezone.utc)))
        await session.commit()
    store = AgentInstanceService(real_database)
    visits = [await store.list_pending_permit_runs(limit=1) for _ in range(3)]
    assert [batch[0]["id"] for batch in visits] == ["run-a", "run-b", "run-a"]
    assert all(batch[0]["created_at"].startswith("2000-01-01") for batch in visits)
