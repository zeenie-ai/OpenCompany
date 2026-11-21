"""A hired team's plan reaches its runs through the admitted generation (#148).

The saved graph keeps node data to UI fields, so the plan is saved on the
employee row; Start and Apply copy it onto the lead in the generation's
snapshot. A run holds only what its trigger reaches, so the runtime reads the
plan, and a job its graph, from that whole snapshot.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import nodes  # noqa: F401

from models.employees import Employee
from services.employees.apps import get_app
from services.employees.builder import BuildInputs, build_employee_graph
from services.employees.hire_request import HireEmployeeRequest
from services.employees.jobs import create_job
from services.employees.llm import LLMChoice
from services.employees.prompt import OwnerProfile
from services.employees.team_runtime import employee_runtime_plan
from services.plugin import NodeContext
from services.temporal.trigger_listener_workflow import _build_run_graph
from services.workflow_migrations import normalize_workflow_graph
from services.workflow_sanitizer import sanitize_workflow_graph


def inbox_assistant():
    request = HireEmployeeRequest.model_validate({
        "idempotency_key": "inbox", "job": "Read new email, answer what you can and flag the rest",
        "name": "Leo", "role": "Inbox assistant", "apps": ["Gmail"],
        "steps": [{"title": "Read new email", "role": "trigger", "app": "Gmail"}], "rules": {"ask_first": True}})
    return build_employee_graph(BuildInputs(
        workflow_id="7", request=request, apps=[get_app("gmail")], connected_app_ids={"gmail"},
        owner=OwnerProfile(name="Alex", role="Owner", preferences=""), owner_values={"google_email": "alex@example.com"},
        timezone="UTC", llm=LLMChoice(provider="openai", model="gpt-x", local=False), memory=True,
        now=datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc), team=True))


@pytest.fixture
async def started(real_database, monkeypatch):
    """A hire saved the way commit_hire saves it, then started through the real Start and deploy handlers."""
    from services.deployment import handlers
    built = inbox_assistant()
    normalized = normalize_workflow_graph("7", built.nodes, built.edges, built.parameters)
    saved = sanitize_workflow_graph({**normalized.graph_data(), "owner_id": "owner"})
    async with real_database.reserved_session() as session:
        session.add(Employee(id="employee", workflow_id="7", owner_id="owner", hire_state="ready",
                             team_plan=built.team_plan, node_roles=built.node_roles))
        await session.commit()
    monkeypatch.setattr(real_database, "get_workflow", AsyncMock(return_value=SimpleNamespace(data=saved, slug="Leo_1")))
    # Temporal and the status broadcasts are the only parts not run for real.
    workflow_service = SimpleNamespace(
        is_workflow_deployed=lambda _workflow_id: False,
        deploy_workflow=AsyncMock(return_value={"success": True, "triggers_setup": [], "deployment_id": "deployment"}))
    broadcaster = SimpleNamespace(
        lock_workflow=AsyncMock(return_value=True), unlock_workflow=AsyncMock(), update_node_status=AsyncMock(),
        update_workflow_status=AsyncMock(), update_deployment_status=AsyncMock())
    monkeypatch.setattr("core.container.container", SimpleNamespace(database=lambda: real_database,
                                                                    workflow_service=lambda: workflow_service))
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: broadcaster)
    monkeypatch.setattr("services.workflow_validator.validate_workflow", AsyncMock(return_value={"errors": []}))
    monkeypatch.setattr(handlers, "_deployment_tasks", {})
    monkeypatch.setattr(handlers, "_broadcast_control", AsyncMock(return_value={}))
    monkeypatch.setattr(handlers, "_start_controller", AsyncMock(return_value=None))
    monkeypatch.setattr(handlers, "_query_controller_state", AsyncMock(return_value=None))
    result = await handlers.handle_start_workflow(
        {"workflow_id": "7", "nodes": saved["nodes"], "edges": saved["edges"], "user_id": "owner",
         "parameters_by_id": normalized.node_parameters, "expected_revision": 0, "idempotency_key": "start"}, None)
    assert result["success"] is True, result
    deployed = workflow_service.deploy_workflow.await_args.kwargs
    return SimpleNamespace(database=real_database, saved=saved, control=await real_database.get_latest_workflow_control("7"),
                           listeners={"nodes": deployed["nodes"], "edges": deployed["edges"]},
                           roles=built.node_roles, plan=built.team_plan)


def run(started, trigger, node_id):
    """The context one agent gets in a run its trigger's deployed listener started."""
    nodes, edges = _build_run_graph(trigger_node_id=trigger, trigger_output={"subject": "Hello"},
                                    nodes=started.listeners["nodes"], edges=started.listeners["edges"])
    return {"workflow_id": "7", "generation": started.control.generation, "data_scope_id": started.control.data_scope_id,
            "execution_id": f"run-{node_id}", "session_id": "7", "user_id": "owner", "node_id": node_id,
            "nodes": nodes, "edges": edges, "outputs": {trigger: {"subject": "Hello"}},
            "parameter_snapshot": started.control.graph_snapshot["parameters"]}


async def test_start_admits_the_plan_the_saved_graph_cannot_hold(started):
    lead = started.roles["agent"]
    assert set(next(node for node in started.saved["nodes"] if node["id"] == lead)["data"]) == {"label"}
    # The generation's snapshot and the listeners deployed from it both carry it.
    for graph in (started.control.graph_snapshot, started.listeners):
        admitted = next(node for node in graph["nodes"] if node["id"] == lead)["data"]
        assert admitted["employee_recipe_version"] == 2 and admitted["employee_team_plan"] == started.plan


async def test_role_runs_load_skills_from_their_own_admitted_master_skill(started):
    from services.plugin.edge_walker import collect_agent_connections

    for trigger, agent, skill_key in [("talk_trigger", "talk_agent", "talk_skill_node_id"),
                                     ("trigger", "agent", "lead_skill_node_id")]:
        owner = started.roles[agent]
        context = {**run(started, started.roles[trigger], owner), "employee_team_plan": started.plan}
        _, skills, _, _, _ = await collect_agent_connections(owner, context, started.database)
        assert {item["master_skill_node_id"] for item in skills} == {started.plan[skill_key]}


async def test_a_trigger_run_resolves_the_plan_and_its_job_holds_the_delivery_nodes(started):
    roles, plan = started.roles, started.plan
    context = run(started, roles["trigger"], roles["agent"])
    delivery = set(plan["delivery_node_ids"])
    assert {roles["gate"], roles["reply"]} <= delivery
    # The run itself never reaches the delivery nodes.
    assert not delivery & {node["id"] for node in context["nodes"]}
    # AgentWorkflow and the Gmail projection read the marker on the run's own lead node.
    assert next(node for node in context["nodes"] if node["id"] == roles["agent"])["data"]["employee_recipe_version"] == 2
    assert await employee_runtime_plan(started.database, context) == plan
    job = await create_job(started.database, NodeContext.from_legacy(roles["agent"], "ai_employee", context),
                           mission="Answer the email", lead_node_id=roles["agent"],
                           delivery_node_ids=plan["delivery_node_ids"], dispatch=False)
    assert delivery <= {node["id"] for node in job.source["nodes"]} and delivery <= set(job.source["parameters"])
    assert any(edge["source"] == roles["gate"] and edge["target"] == roles["reply"] for edge in job.source["edges"])


async def test_a_talk_run_resolves_the_plan_and_submits_a_job_that_holds_the_lead(started):
    roles, plan = started.roles, started.plan
    context = run(started, roles["talk_trigger"], roles["talk_agent"])
    assert roles["agent"] not in {node["id"] for node in context["nodes"]}
    assert await employee_runtime_plan(started.database, context) == plan
    job = await create_job(started.database, NodeContext.from_legacy(roles["talk_agent"], "aiAgent", context),
                           mission="Draft a reply to Sam", lead_node_id=plan["lead_node_id"],
                           delivery_node_ids=plan["talk_delivery_node_ids"], dispatch=True)
    assert roles["agent"] in {node["id"] for node in job.source["nodes"]}


async def test_a_run_outside_a_started_generation_has_no_plan(started):
    context = {**run(started, started.roles["trigger"], started.roles["agent"]), "generation": 0}
    assert await employee_runtime_plan(started.database, context) is None


@pytest.mark.parametrize("manager_available", [True, False])
async def test_chat_job_reaches_the_connected_lead_and_specialist(started, monkeypatch, manager_available):
    """Exercise the production job, binding and task stores without paying for inference."""
    from models.database import TeamTask
    from services.agent_instances.service import AgentInstanceService
    from services.agent_team import AgentTeamService
    from services.temporal.agent_activities import prepare_agent_payload
    from services.temporal.agent_instance_workflow import bootstrap_instance, execute_instance_tool, turn_context
    from services.temporal.agent_workflow import _inherited_scope, _lifecycle_binding

    db = started.database
    # Capture the exact job dispatched to Temporal. Run its bootstrap and
    # tool activities below; the external worker and LLM are unnecessary.
    dispatch = AsyncMock()
    settings = SimpleNamespace(temporal_task_queue="employees", max_concurrent_subagents=3,
                               max_delegation_depth=2, agent_recursion_limit=20, tool_result_max_chars=100_000)
    resolver = AsyncMock(side_effect=lambda parameters, *args, **kwargs: parameters)
    monkeypatch.setattr("core.container.container", SimpleNamespace(
        database=lambda: db, settings=lambda: settings, ai_service=lambda: SimpleNamespace(),
        temporal_client=lambda: SimpleNamespace(client=SimpleNamespace(start_workflow=dispatch)),
        auth_service=lambda: SimpleNamespace(has_valid_key=AsyncMock(return_value=True)),
        workflow_service=lambda: SimpleNamespace(_param_resolver=SimpleNamespace(resolve=resolver))))
    monkeypatch.setattr("core.config.Settings", lambda: settings)
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: db)
    monkeypatch.setattr("services.agent_team._service", AgentTeamService(db))
    monkeypatch.setattr("services.employees.team_runtime.team_runtime_error", lambda: None)
    monkeypatch.setattr("services.employees.events.employee_changed_now", lambda _: None)
    monkeypatch.setattr("services.llm.config.is_model_valid_for_provider", lambda *_: True)
    monkeypatch.setattr("services.llm.config.resolve_max_tokens", lambda *_: 1000)
    monkeypatch.setattr("services.llm.config.resolve_temperature", lambda *_: 0.2)
    monkeypatch.setattr("services.compaction.get_compaction_service", lambda: None)
    wake = AsyncMock()
    monkeypatch.setattr("services.agent_instances.runtime.wake_instances", wake)

    contact = started.roles["talk_agent"]
    talk = {**run(started, started.roles["talk_trigger"], contact), "node_type": "aiAgent",
            "agent_lifecycle_version": 1, "agent_protocol_version": 1}
    talk_tools = await prepare_agent_payload(talk)
    contact_manager = started.plan["talk_task_manager_node_id"]
    assert contact_manager == started.roles["talk_task_manager"]
    assert contact_manager != started.plan["task_manager_node_id"]
    assert {(tool["tool_node_id"], tool.get("agent_operation")) for tool in talk_tools["tools"]
            if tool["node_type"] == "taskManager"} == {(contact_manager, "submit_job"), (contact_manager, "get_job_status")}
    async def call_contact_tool(operation, arguments):
        binding = next(tool for tool in talk_tools["tools"] if tool.get("agent_operation") == operation)
        return await execute_instance_tool({
            "agent_tool_config": {**_lifecycle_binding(binding, talk, talk_tools), **_inherited_scope(talk)},
            "parent_node_id": contact, "workflow_id": talk["workflow_id"], "execution_id": talk["execution_id"],
            "nodes": talk["nodes"], "edges": talk["edges"], "tool_call_id": f"contact-{operation}",
            "tool_args": arguments,
        })

    receipt = await call_contact_tool("submit_job", {"instructions": "Draft a response"})
    assert receipt["success"]
    dispatch.assert_awaited_once()
    workflow_type, context = dispatch.await_args.args
    assert workflow_type == "EmployeeJobWorkflow" and context["node_id"] == started.plan["lead_node_id"]
    bootstrap = await bootstrap_instance(context)
    store = AgentInstanceService(db)
    claimed = await store.claim_run(bootstrap["instance_id"], request_id="first-turn")
    turn = turn_context(claimed)
    if not manager_available:
        from temporalio.exceptions import ApplicationError
        from services.employees.failures import lead_failure

        # Node adapters can fail to build their bindings. Report that failure
        # before the lead can tell the owner it delegated work.
        monkeypatch.setattr("services.agent_instances.tools.build_bindings", lambda *args, **kwargs: [])
        with pytest.raises(ApplicationError, match="assigned Task Manager") as caught:
            await prepare_agent_payload(turn)
        assert lead_failure(caught.value)["code"] == "lead_task_manager_unavailable"
        return
    prepared = await prepare_agent_payload(turn)
    manager = started.roles["task_manager"]
    assert started.plan["task_manager_node_id"] == manager
    management = [tool for tool in prepared["tools"] if tool["node_type"] == "taskManager"]
    assert {tool["tool_node_id"] for tool in management} == {manager}
    spawns = [tool for tool in management if tool.get("agent_operation") == "spawn"]
    assert sorted(tool["agent_template"]["node_id"] for tool in spawns) == sorted(
        member["node_id"] for member in started.plan["members"]
    )
    spawn = spawns[0]
    result = await execute_instance_tool({
        "agent_tool_config": _lifecycle_binding(spawn, turn, prepared),
        "workflow_id": talk["workflow_id"], "execution_id": context["execution_id"],
        "root_execution_id": context["root_execution_id"], "parent_node_id": context["node_id"],
        "nodes": turn["nodes"], "edges": turn["edges"], "tool_call_id": "call-1",
        "tool_args": {"instructions": "Draft the customer's reply for review"},
    })
    assert result.get("success", True), result
    async with db.get_session() as session:
        task = await session.get(TeamTask, result["task"]["id"])
        assert task.assigned_instance_id == result["agent"]["id"]
        assert task.assigned_to == spawn["agent_template"]["node_id"]
    wake.assert_awaited_once()
    child = await store.claim_run(result["agent"]["id"], request_id="specialist-turn")
    assert child["instance"]["job_id"] == receipt["job_id"]
    assert child["run_id"] and child["messages"]
    status = await call_contact_tool("get_job_status", {"job_id": receipt["job_id"]})
    assert status["job"]["task_count"] == 1
    assert status["job"]["tasks_by_status"] == {"running": 1}


async def test_talk_gets_submit_job_from_the_plan_prepare_resolved():
    from services.plugin.edge_walker import collect_agent_connections
    plan = {"version": 2, "lead_node_id": "lead", "talk_node_id": "talk"}
    context = {"workflow_id": "7", "edges": [{"source": "chat", "target": "talk", "targetHandle": "input-main"}],
               "nodes": [{"id": "chat", "type": "chatTrigger", "data": {"label": "Chat"}},
                         {"id": "talk", "type": "aiAgent", "data": {"label": "Talk"}}]}
    _, _, tools, _, _ = await collect_agent_connections("talk", {**context, "employee_team_plan": plan}, object())
    assert [tool["node_type"] for tool in tools] == ["taskManager"]
    _, _, tools, _, _ = await collect_agent_connections("talk", context, object())
    assert tools == []
