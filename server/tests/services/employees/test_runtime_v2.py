"""Admitted recipe metadata, intrinsic submission and terminal lifecycle."""
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import pytest
from services.employees.team_runtime import employee_runtime_plan, admit_employee_runtime_job
from services.temporal.agent_workflow import AgentWorkflow, _trusted_tool_scope
from nodes.tool.task_manager import _execute_task_manager


def admitted():
    plan = {"version": 2, "lead_node_id": "lead", "talk_node_id": "talk", "members": [{"node_id": "child"}],
            "delivery_node_ids": ["gate", "send"], "talk_delivery_node_ids": ["chat"]}
    nodes = [{"id": name, "type": kind} for name, kind in (("lead", "ai_employee"), ("talk", "chatAgent"), ("child", "aiAgent"), ("gate", "approvalGate"), ("send", "telegramSend"), ("chat", "chatReply"))]
    nodes[0]["data"] = {"employee_recipe_version": 2, "employee_team_plan": deepcopy(plan)}
    return {"workflow_id": "wf", "node_id": "lead", "execution_id": "origin", "nodes": nodes, "edges": [],
            "generation": 4, "data_scope_id": "original-scope", "user_id": "owner", "parameter_snapshot": {"send": {"recipient": "original"}},
            "inputs": {"trigger": {"recipient": "original"}}}, plan


def generation(context):
    """A database holding the generation's admitted graph and nothing saved."""
    control = SimpleNamespace(graph_snapshot={"nodes": context["nodes"], "edges": context["edges"]})
    return SimpleNamespace(get_workflow_control_generation=AsyncMock(return_value=control),
                           get_session=AsyncMock(side_effect=AssertionError("must not read saved metadata")))


async def test_plan_is_from_admitted_graph_even_after_saved_rollback():
    context, original = admitted()
    database = generation(context)
    # A run holds only what its trigger reaches; the plan names nodes beyond it.
    run = {**context, "nodes": [node for node in context["nodes"] if node["id"] == "lead"]}
    assert await employee_runtime_plan(database, run) == original
    database.get_workflow_control_generation.assert_awaited_with("wf", 4)
    context["nodes"][0]["data"]["employee_team_plan"]["delivery_node_ids"].append("unactivated")
    with pytest.raises(ValueError, match="outside its admitted graph"):
        await employee_runtime_plan(database, run)


async def test_admission_preserves_original_outputs_and_review_reuses_job(monkeypatch):
    context, plan = admitted()
    create = AsyncMock(return_value=SimpleNamespace(id="job"))
    monkeypatch.setattr("services.employees.jobs.create_job", create)
    await admit_employee_runtime_job(object(), context, plan, "Owner mission")
    assert context["employee_job_id"] == "job"
    assert create.await_args.args[1].outputs == {"trigger": {"recipient": "original"}}
    assert create.await_args.kwargs["dispatch"] is False
    @asynccontextmanager
    async def session():
        yield SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(workflow_id="wf", team_id="team")),
            execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(id="job", workflow_id="wf", lead_node_id="lead", team_id=None, source={}))))
    review = {**context, "execution_id": "later-review"}
    review.pop("employee_job_id")
    await admit_employee_runtime_job(SimpleNamespace(get_session=session), review, plan, "Review submitted work", {"team_id": "team", "task_id": "task"})
    assert review["employee_job_id"] == "job"
    create.assert_awaited_once()


@pytest.mark.parametrize("state,message", [("running", "Admitted, no teammate assigned."),
    ("failed", "The runtime payload exceeded the size limit."), ("cancelled", "This request was stopped.")])
async def test_intrinsic_talk_submission_ignores_model_destinations(monkeypatch, state, message):
    context, plan = admitted()
    scope = {**context, "parent_node_id": "talk", "node_id": "builtin_task_manager_talk"}
    create = AsyncMock(return_value=SimpleNamespace(id="job", state="queued"))
    dispatch = AsyncMock()
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: generation(context))
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: object())
    monkeypatch.setattr("services.employees.jobs.create_job", create)
    monkeypatch.setattr("services.employees.jobs.dispatch_job", dispatch)
    monkeypatch.setattr("services.employees.job_status.get_job_status", AsyncMock(return_value={"job": {
        "job_id": "job", "state": state, "message": message, "task_count": 0}}))
    result = await _execute_task_manager({"operation": "submit_job", "mission": "Do research", "lead_node_id": "forged", "delivery_node_ids": ["forged"]}, scope)
    assert result["job_id"] == "job"
    assert result["state"] == state and result["acknowledgement"] == message
    assert result["task_count"] == 0
    assert create.await_args.kwargs["lead_node_id"] == "lead"
    assert create.await_args.kwargs["delivery_node_ids"] == ["chat"]
    assert create.await_args.args[1].raw["generation"] == 4
    assert create.await_args.args[1].raw["data_scope_id"] == "original-scope"


async def test_talk_job_status_uses_only_admitted_workflow_and_lead(monkeypatch):
    context, _ = admitted()
    scope = {**context, "parent_node_id": "talk", "node_id": "builtin_task_manager_talk"}
    database = generation(context)
    read = AsyncMock(return_value={"success": True, "job": None})
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: database)
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: object())
    monkeypatch.setattr("services.employees.job_status.get_job_status", read)
    await _execute_task_manager({"operation": "get_job_status", "job_id": "job", "workflow_id": "foreign", "lead_node_id": "foreign"}, scope)
    read.assert_awaited_once_with(database, workflow_id="wf", lead_node_id="lead", job_id="job")
    from services.plugin import NodeUserError
    with pytest.raises(NodeUserError, match="conversational contact"):
        await _execute_task_manager({"operation": "get_job_status"}, {**scope, "parent_node_id": "child"})


def test_trusted_tool_scope_carries_managed_job_and_original_generation():
    context, _ = admitted()
    context["employee_job_id"] = "server-job"
    scope = _trusted_tool_scope(context)
    for key in ("generation", "data_scope_id", "user_id", "parameter_snapshot", "employee_job_id"):
        assert scope[key] == context[key]
    assert scope["outputs"] == context["inputs"]


@pytest.mark.parametrize("mode", ["failed", "cancelled", "acknowledged"])
async def test_runtime_terminal_cleanup_preserves_live_delegated_acknowledgement(monkeypatch, mode):
    context, _ = admitted()
    context["employee_job_id"] = "job"
    agent = AgentWorkflow()
    if mode == "cancelled":
        agent._run_impl = AsyncMock(side_effect=asyncio.CancelledError())
    else:
        agent._run_impl = AsyncMock(return_value={"success": mode == "acknowledged"})
    execute = AsyncMock(return_value={"saved": True})
    monkeypatch.setattr("services.temporal.agent_workflow.workflow.patched", lambda _: True)
    monkeypatch.setattr("services.temporal.agent_workflow.workflow.execute_activity", execute)
    if mode == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            await agent.run(context)
    else:
        await agent.run(context)
    if mode == "acknowledged":
        execute.assert_not_awaited()
    else:
        assert execute.await_args.args[0] == "employee.job.failed"
        assert execute.await_args.args[1]["cancelled"] == (mode == "cancelled")
