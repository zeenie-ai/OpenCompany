from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from models.employees import EmployeeGrant, EmployeeJob
from nodes.tool.task_manager import _execute_task_manager
from services.chat.ledger import admit_message
from services.employees import store
from services.employees.permissions import assert_runtime_access, decide_access, finish_approved_change, list_access
from services.employees.talk_team_change import route_talk_team_change
from services.employees.team_runtime import admit_team_plan
from tests.nodes._agent_builder_harness import builder_fixture, database_fixture, call, ctx, save_graph, saved
from tests.services.employees.test_team_plan_admission import inbox_assistant


@pytest.fixture
async def contact(database, builder, monkeypatch):
    built = inbox_assistant()
    await save_graph(database, {"nodes": built.nodes, "edges": built.edges}, built.parameters)
    employee, _ = await store.reserve(database, owner_id="owner", idempotency_key="team-change",
        payload_hash="test", fields={"role": "Weather Checker", "team_plan": built.team_plan})
    await store.mark_ready(database, employee.id, workflow_id="7", node_roles=built.node_roles)
    admitted = await admit_team_plan(database, "7", built.nodes, built.edges)
    snapshot = {"nodes": admitted, "edges": built.edges, "parameters": built.parameters}
    monkeypatch.setattr(database, "get_workflow_control_generation", AsyncMock(return_value=SimpleNamespace(graph_snapshot=snapshot)))
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: database)
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: None)
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: AsyncMock())
    chat = await admit_message(database, session_id="7", workflow_id="7", execution_id="turn",
        text="Add the coding specialist to my employee's team", track=True)
    config = {**snapshot, "workflow_id": "7", "generation": 1, "parent_node_id": built.node_roles["talk_agent"],
              "user_id": "owner", "run_scope": {"run_id": chat.run.run_id}, "execution_id": "turn",
              "tool_call_id": "call-add", "agent_operation": "submit_job",
              "parameters": {"_agent_operation": "submit_job"}}
    return config, built


async def test_submit_job_routes_real_misdelegation_to_builder_without_starting_work(database, contact):
    config, built = contact
    mission = "Implement and verify the website. If no teammate can write code, add a coding specialist to the team for this."
    args = {"operation": "submit_job", "mission": mission}
    response = await _execute_task_manager(args, config)
    assert response["state"] == "awaiting_approval" and not response["success"], response
    assert not response["job_created"] and "job_id" not in response
    [request] = response["required_access"]
    again = await _execute_task_manager(args, config)
    assert again["request_id"] == request["request_id"]
    assert not any(n["type"] == "coding_agent" for n in (await saved(database))["nodes"])

    assert await decide_access(database, request["request_id"], "owner", True)
    result = await _execute_task_manager(args, config)
    assert result["state"] == "pending_apply" and result["job_created"] is False, result
    assert (await _execute_task_manager(args, config))["state"] == "pending_apply"
    team = (await store.get_by_workflow(database, "7")).team_plan
    [coder] = [m for m in team["members"] if m["node_type"] == "coding_agent"]
    graph = await saved(database)
    assert any(edge["source"] == coder["node_id"] and edge["target"] == built.node_roles["agent"]
               and edge["targetHandle"] == "input-teammates" for edge in graph["edges"])
    assert {n["type"] for n in graph["nodes"] if n["id"] in coder["tools"]} >= {"piRead", "piWrite"}
    async with database.get_session() as session:
        assert not (await session.execute(select(EmployeeJob))).scalars().all()


async def test_ask_first_off_saves_missing_coder_on_first_submission(database, contact):
    from services.approvals.rules import set_ask_first

    config, built = contact
    await set_ask_first(database, "7", False)
    args = {"operation": "submit_job",
            "mission": "Build and verify the site. If no teammate can write code, add a coding specialist."}
    response = await _execute_task_manager(args, config)
    assert response["state"] == "pending_apply" and response["activation_state"] == "saved", response
    assert response["required_access"] == [] and response["job_created"] is False and "job_id" not in response
    employee = await store.get_by_workflow(database, "7")
    [coder] = [m for m in employee.team_plan["members"] if m["node_type"] == "coding_agent"]
    graph = await saved(database)
    assert any(e["source"] == coder["node_id"] and e["target"] == built.node_roles["agent"]
               and e["targetHandle"] == "input-teammates" for e in graph["edges"])
    assert {n["type"] for n in graph["nodes"] if n["id"] in coder["tools"]} >= {"piRead", "piWrite"}
    async with database.get_session() as session:
        grants = (await session.execute(select(EmployeeGrant).where(EmployeeGrant.workflow_id == "7"))).scalars().all()
        assert grants and all(grant.limits.get("approved") and grant.revoked_at is None for grant in grants)
        assert not (await session.execute(select(EmployeeJob))).scalars().all()


@pytest.mark.parametrize("first_extra, second_extra, second_purpose, expected, interleave", [
    (["currentTimeTool"], [], "Build the site", "saved", False),
    ([], ["currentTimeTool"], "Build the site", "superseded", False),
    ([], [], "Build a different site", "superseded", False),
    ([], ["currentTimeTool"], "Build the site", "superseded", True),
])
async def test_settles_competing_approvals_for_single_coder(
    database, contact, monkeypatch, first_extra, second_extra, second_purpose, expected, interleave,
):
    from nodes.tool.agent_builder import AgentBuilderNode

    config, built = contact
    async def request(call_id, tools, purpose):
        talk = ctx(built.node_roles["talk_agent"], call=call_id, run_scope=config["run_scope"])
        talk.node_id = built.node_roles["builder"]
        result = await call("add_subagent", talk, agent_type="coding_agent", tool_types=tools, purpose=purpose)
        [review] = result.required_access
        return review["request_id"]

    first = await request("first", first_extra, "Build the site")
    second = await request("second", second_extra, second_purpose)
    assert first != second
    graph = {}
    async def finish_first():
        nonlocal graph
        assert await decide_access(database, first, "owner", True)
        assert (await finish_approved_change(database, first, "owner"))["activation_state"] == "saved"
        graph = await saved(database)

    if interleave:
        add = AgentBuilderNode.add_subagent
        async def finish_first_after_preflight(node, context, params):
            if context.raw.get("reviewed_employee_grant") == second:
                await finish_first()
            return await add(node, context, params)
        monkeypatch.setattr(AgentBuilderNode, "add_subagent", finish_first_after_preflight)
    else:
        await finish_first()
    assert await decide_access(database, second, "owner", True)
    outcome = await finish_approved_change(database, second, "owner")
    assert outcome["success"] and outcome["activation_state"] == expected, outcome
    if expected == "superseded":
        assert "update the existing specialist" in outcome["message"]
    assert len([n for n in graph["nodes"] if n["type"] == "coding_agent"]) == 1
    assert await saved(database) == graph
    assert not next(r for r in await list_access(database, "7", "owner") if r["id"] == second)["change_pending"]


async def test_revoked_bound_specialist_rejects_another_unbound_approval(database, contact):
    from services.plugin.base import NodeUserError

    config, built = contact
    lead = built.node_roles["agent"]
    reviews = []
    for index, purpose in enumerate(["Build the site", "Prepare a different project"]):
        talk = ctx(built.node_roles["talk_agent"], call=f"request-{index}", run_scope=config["run_scope"])
        talk.node_id = built.node_roles["builder"]
        request = await call("add_subagent", talk, agent_type="coding_agent", purpose=purpose)
        reviews.append(request.required_access[0]["request_id"])
    for review in reviews:
        assert await decide_access(database, review, "owner", True)
        assert (await finish_approved_change(database, review, "owner"))["success"]
    [coder] = [n["id"] for n in (await saved(database))["nodes"] if n["type"] == "coding_agent"]
    await assert_runtime_access(database, "7", coder, "coding_agent", {"parent_node_id": lead})
    assert await decide_access(database, reviews[0], "owner", False)
    with pytest.raises(NodeUserError, match="owner removed permission"):
        await assert_runtime_access(database, "7", coder, "coding_agent", {"parent_node_id": lead})
    assert await decide_access(database, reviews[0], "owner", True)
    await assert_runtime_access(database, "7", coder, "coding_agent", {"parent_node_id": lead})


@pytest.mark.parametrize("mission", [
    "Build a website with live weather and test it.",
    "Add a coding agent to the application settings page.",
    "Do not add a coding agent. Update the mockup.",
    'Document the button label "Add a coding agent."',
    'Summarize the following quote:\n"Plan the sprint.\nAdd a coding specialist.\nPrepare the brief."',
    "Document the following text:\n> Add a coding agent to the team.",
    "Show the following example.\n```text\nAdd a coding agent to the team.\n```",
])
async def test_ordinary_jobs_do_not_change_the_team(mission):
    assert await route_talk_team_change(None, {}, {}, mission) is None


async def test_admitted_specialist_or_an_equipped_teammate_can_do_the_job(contact, database):
    config, built = contact
    browser_team = {**built.team_plan, "members": [{"node_type": "browser_agent"}]}
    assert await route_talk_team_change(database, config, browser_team,
        "Check the forecast. If no teammate can browse the web, add a browser agent.") is None
    equipped = deepcopy(built.team_plan)
    equipped["members"][0]["tools"].append("7:piWrite:1")
    control = await database.get_workflow_control_generation("7", 1)
    control.graph_snapshot["nodes"].append({"id": "7:piWrite:1", "type": "piWrite"})
    assert await route_talk_team_change(database, config, equipped,
        "Build the site. If no teammate can write code, add a coding specialist.") is None


async def test_disconnected_builder_and_untrusted_chat_do_not_create_jobs_or_grants(contact, database):
    config, built = contact
    args = {"operation": "submit_job", "mission": "Add a coding agent to the team."}
    public = await _execute_task_manager(args, {**config, "run_scope": {}})
    assert public["state"] == "team_change_blocked" and not public["required_access"]
    graph = await saved(database)
    graph["edges"] = [e for e in graph["edges"] if e.get("source") != built.node_roles["builder"]]
    await save_graph(database, graph)
    disconnected = await _execute_task_manager(args, config)
    assert disconnected["state"] == "team_change_blocked" and not disconnected["job_created"]
    assert "connected" in disconnected["message"]
