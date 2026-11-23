"""A hired hierarchy exercised through its actual tools, durable turns and chat receipt."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlmodel import select

from models.database import ChatMessage, TeamTask, WorkflowControlExecution
from models.employees import Employee, EmployeeJob
from services.agent_team import AgentTeamService
from services.chat import ledger
from services.employees.builder import BuildInputs, build_employee_graph
from services.employees.hire_request import HireEmployeeRequest
from services.employees.jobs import deliver_job
from services.employees.llm import LLMChoice
from services.employees.team_runtime import admit_team_plan
from services.plugin import NodeContext
from services.temporal.agent_activities import prepare_agent_payload
from services.temporal.agent_instance_workflow import (
    bootstrap_instance, checkpoint_instance, claim_instance, complete_instance, execute_instance_tool,
)
from services.temporal.agent_workflow import _inherited_scope, _lifecycle_binding
from services.temporal.trigger_listener_workflow import _build_run_graph
from services.workflow_migrations import normalize_workflow_graph
from services.workflow_sanitizer import sanitize_workflow_graph


@pytest.fixture
def hierarchy_graph():
    request = HireEmployeeRequest.model_validate({
        "idempotency_key": "hierarchy", "job": "Research and review evidence", "name": "Maya",
        "role": "Research coordinator", "trigger": {"kind": "manual"}, "steps": [{"title": "Check evidence"}],
        "team": [
            {"key": "research", "kind": "employee", "name": "Research manager", "responsibility": "Review evidence"},
            {"key": "analyst", "name": "Analyst", "responsibility": "Collect evidence", "reports_to": "research"},
        ],
    })
    return build_employee_graph(BuildInputs(workflow_id="7", request=request, apps=[], memory=False, team=True,
        llm=LLMChoice(provider="openai", model="test-model", local=False)))


@pytest.fixture
async def hierarchy(real_database, monkeypatch, hierarchy_graph):
    db, built = real_database, hierarchy_graph
    norm = normalize_workflow_graph("7", built.nodes, built.edges, built.parameters)
    saved = sanitize_workflow_graph({**norm.graph_data(), "owner_id": "owner"})
    assert await db.save_workflow("7", "Maya", "Maya_1", saved)
    for node_id, parameters in norm.node_parameters.items():
        assert await db.save_node_parameters(node_id, parameters)
    async with db.reserved_session() as session:
        session.add(Employee(id="employee", workflow_id="7", owner_id="owner", hire_state="ready",
                             team_plan=built.team_plan, node_roles=built.node_roles))
        await session.commit()
    admitted = await admit_team_plan(db, "7", saved["nodes"], saved["edges"])
    control = WorkflowControlExecution(id="control", workflow_id="7", generation=1, execution_id="run",
        root_execution_id="run", data_scope_id="scope", controller_workflow_id="workflow-control:7:1",
        status="running", idempotency_key="start", graph_hash="hash",
        graph_snapshot={**saved, "nodes": admitted, "parameters": norm.node_parameters})
    async with db.reserved_session() as session:
        session.add(control)
        await session.commit()
    from core.config import Settings
    settings = Settings()
    settings.temporal_enabled = settings.temporal_agent_workflow_enabled = True
    settings.max_concurrent_subagents = 1
    settings.max_delegation_depth = 2
    transport = Mock(start_workflow=AsyncMock())
    transport.get_workflow_handle.return_value.signal = AsyncMock()
    driver = SimpleNamespace(is_connected=True, client=transport)
    resolver = AsyncMock(side_effect=lambda parameters, *args, **kwargs: parameters)
    monkeypatch.setattr("core.container.container", SimpleNamespace(
        database=lambda: db, temporal_client=lambda: driver, settings=lambda: settings,
        auth_service=lambda: SimpleNamespace(get_api_key=AsyncMock(return_value=None), has_valid_key=AsyncMock(return_value=True)),
        ai_service=lambda: SimpleNamespace(), workflow_service=lambda: SimpleNamespace(_param_resolver=SimpleNamespace(resolve=resolver))))
    monkeypatch.setattr("core.config.Settings", lambda: settings)
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: db)
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: AgentTeamService(db))
    monkeypatch.setattr("services.llm.config.is_model_valid_for_provider", lambda *_: True)
    monkeypatch.setattr("services.llm.config.resolve_max_tokens", lambda *_: 1000)
    monkeypatch.setattr("services.llm.config.resolve_temperature", lambda *_: 0.2)
    monkeypatch.setattr("services.compaction.get_compaction_service", lambda: None)
    monkeypatch.setattr("services.employees.events.employee_changed", Mock())
    monkeypatch.setattr("services.employees.events.employee_changed_now", Mock())
    monkeypatch.setattr("services.agent_instances.runtime.wake_instances", AsyncMock())
    return built, control, transport


async def call_tool(context, prepared, operation, arguments, call_id):
    manifest = next(tool for tool in prepared["tools"] if tool.get("agent_operation") == operation)
    binding = {**_lifecycle_binding(manifest, context, prepared), **_inherited_scope(context)}
    result = await execute_instance_tool({
        "agent_tool_config": binding, "tool_args": arguments, "parent_node_id": context["node_id"],
        "workflow_id": context["workflow_id"], "execution_id": context["execution_id"],
        "root_execution_id": context["root_execution_id"], "nodes": context["nodes"], "edges": context["edges"],
        "tool_call_id": call_id,
    })
    assert result.get("success", True), result
    return result


async def take_turn(instance_id, name):
    turn = await claim_instance({"instance_id": instance_id, "request_id": name})
    assert turn and turn["instance"]["active_run_id"] == turn["run_id"], name
    return turn, await prepare_agent_payload(turn["context"])


async def finish(turn, text):
    identity = {"instance_id": turn["instance"]["id"], "run_id": turn["run_id"]}
    await checkpoint_instance({**identity, "request_id": "response", "expected_revision": turn["transcript_revision"],
        "delivery_ids": turn["deliveries"], "messages": [*turn["transcript"], *turn["messages"],
                                                         {"role": "assistant", "content": text}]})
    return await complete_instance({**identity, "result": {"response": text}, "request_id": "done"})


async def review(turn, prepared, task_id, *, feedback=None):
    current = await call_tool(turn["context"], prepared, "get_task", {"task_ref": task_id}, "read")
    task = current["task"]
    assert task["status"] == "submitted"
    arguments = {"task_ref": task["task_ref"]}
    if feedback:
        arguments["feedback"] = feedback
    return await call_tool(turn["context"], prepared, "request_changes" if feedback else "accept_task", arguments, "review")


async def test_hired_chat_assigns_nested_tasks_and_delivers_its_own_review(real_database, hierarchy):
    db = real_database
    built, control, transport = hierarchy
    chat = built.team_plan["talk_node_id"]
    trigger = built.node_roles["talk_trigger"]
    mission = "Collect evidence for the quarterly report."
    origin = await ledger.admit_message(db, session_id="7", workflow_id="7", execution_id="owner-turn",
                                        text=mission, track=True)
    run = await ledger.start_run(db, run_id=origin.run.run_id, temporal_workflow_id="owner-turn", temporal_run_id="r1")
    graph = control.graph_snapshot
    nodes, edges = _build_run_graph(trigger_node_id=trigger, trigger_output={"message": mission},
                                    nodes=graph["nodes"], edges=graph["edges"])
    context = {"node_id": chat, "node_type": "ai_employee", "nodes": nodes, "edges": edges,
               "outputs": {trigger: {"message": mission}}, "workflow_id": "7", "execution_id": "owner-turn",
               "root_execution_id": "owner-turn", "session_id": "7", "user_id": "owner", "generation": 1,
               "data_scope_id": control.data_scope_id, "controller_workflow_id": control.controller_workflow_id,
               "agent_lifecycle_version": 1, "agent_protocol_version": 1, "run_scope": {"run_id": run.run_id},
               "parameter_snapshot": graph["parameters"]}
    assert not (await bootstrap_instance(context))["applicable"]
    prepared = await prepare_agent_payload(context)
    assert {item["agent_operation"] for item in prepared["tools"] if item.get("agent_operation")} == {"submit_job", "get_job_status"}
    receipt = await call_tool(context, prepared, "submit_job", {"instructions": mission}, "assign")
    repeated = await call_tool(context, prepared, "submit_job", {"instructions": mission}, "second-tool-call")
    assert repeated["job_id"] == receipt["job_id"]
    transport.start_workflow.assert_awaited_once()
    _, job_context = transport.start_workflow.await_args.args
    await ledger.post_reply(db, run=run, node_id=chat, text="I’ll get that report for you.", execution_id="owner-turn")
    await ledger.finish_run(db, run_id=run.run_id, temporal_workflow_id="owner-turn", temporal_run_id="r1", success=True)

    boot = await bootstrap_instance(job_context)
    assert boot["applicable"]
    handoff = {"instance_id": boot["instance_id"], "request_id": "initial-handoff"}
    outer = await claim_instance(handoff)
    assert (await claim_instance(handoff))["result"] == outer["result"]
    outer_id = outer["result"]["task_id"]
    orch_id, = outer["instance_wake"]
    orch, prepared = await take_turn(orch_id, "assign-research")
    assert orch["task"]["id"] == outer_id
    manager = await call_tool(orch["context"], prepared, "spawn", {"instructions": "Review the field evidence."}, "spawn-manager")
    assert "task_outcome" not in await finish(orch, "I assigned the research.")
    research, prepared = await take_turn(manager["agent"]["id"], "assign-analyst")
    child = await call_tool(research["context"], prepared, "spawn", {"instructions": "Collect the field count."}, "spawn-analyst")
    team = AgentTeamService(db)
    boards = {chat: outer_id, orch["context"]["node_id"]: manager["task"]["id"],
              research["context"]["node_id"]: child["task"]["id"]}
    for owner, task_id in boards.items():
        rows = await team.list_durable_tasks(workflow_id="7", team_lead_node_id=owner,
                                             execution_id=receipt["job_id"], task_board=True)
        assert [task["id"] for task in rows] == [task_id]
        assert "reviewed_job_result" not in rows[0]
    assert "task_outcome" not in await finish(research, "I assigned the count.")
    worker, _ = await take_turn(child["agent"]["id"], "collect")
    submitted = await finish(worker, "The count was 14.")
    assert submitted["task_outcome"]["reviewer_instance_id"] == research["instance"]["id"]
    assert await claim_instance({"instance_id": boot["instance_id"], "request_id": "no-early-chat-review"}) is None

    research, prepared = await take_turn(manager["agent"]["id"], "review-analyst")
    assert research["task"]["id"] == manager["task"]["id"]
    await review(research, prepared, child["task"]["id"])
    submitted = await finish(research, "I checked the count of 14.")
    assert submitted["task_outcome"]["reviewer_instance_id"] == orch_id
    orch, prepared = await take_turn(orch_id, "review-research")
    await review(orch, prepared, manager["task"]["id"])
    submitted = await finish(orch, "The research manager verified the count.")
    assert submitted["task_outcome"]["reviewer_instance_id"] == boot["instance_id"]

    chat_turn, prepared = await take_turn(boot["instance_id"], "request-revision")
    changed = await review(chat_turn, prepared, outer_id, feedback="Include the actual count.")
    assert changed["task"]["id"] == outer_id
    await finish(chat_turn, "I requested the count.")
    revised, _ = await take_turn(orch_id, "revise")
    assert revised["task"]["id"] == outer_id
    await finish(revised, "The verified field count was 14.")
    chat_turn, prepared = await take_turn(boot["instance_id"], "accept-revision")
    await review(chat_turn, prepared, outer_id)
    final = "Your quarterly report has a verified count of 14."
    await finish(chat_turn, final)
    delivery_context = NodeContext.from_legacy(chat, "ai_employee", {
        **job_context, "outputs": {chat: {"response": final, "team_id": chat_turn["instance"]["team_id"]}}})
    assert (await deliver_job(db, delivery_context, lead_node_id=chat))["delivered"]
    assert (await deliver_job(db, delivery_context, lead_node_id=chat))["delivered"]
    for owner, task_id in boards.items():
        row, = await team.list_durable_tasks(workflow_id="7", team_lead_node_id=owner,
                                             execution_id=receipt["job_id"], task_board=True)
        assert row["id"] == task_id and row["status"] == "accepted"
        if owner == chat:
            assert row["reviewed_job_result"] == final
        else:
            assert "reviewed_job_result" not in row
    async with db.get_session() as session:
        job = await session.get(EmployeeJob, receipt["job_id"])
        assert job.chat_run_id == run.run_id and job.result == final
        tasks = (await session.execute(select(TeamTask).where(TeamTask.team_id == job.team_id))).scalars().all()
        assert len(tasks) == 3 and all(task.status == "accepted" for task in tasks)
        finals = (await session.execute(select(ChatMessage).where(ChatMessage.message == final))).scalars().all()
        assert len(finals) == 1 and finals[0].session_id == "7"
