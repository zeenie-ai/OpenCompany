"""The visible Talk route authorizes jobs without dispatching either agent twice."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import nodes  # noqa: F401

from services.employees.apps import get_app
from services.employees.builder import BuildInputs, build_employee_graph
from services.employees.hire_request import HireEmployeeRequest
from services.employees.llm import LLMChoice
from services.employee_team_connection import (
    EMPLOYEE_TEAM_RESOURCE_FIELDS, EmployeeTeamValidationError, connected_task_manager_ids,
    employee_team_edge, is_employee_team_edge, validate_employee_team_connection,
)
from services.employees.team_runtime import admit_team_plan, team_plan_in
from services.run_graph import collect_downstream_node_ids
from services.workflow_validator import validate_workflow
from tests.services.employees.test_team_plan_admission import inbox_assistant


def built_team(kind=None):
    if kind is None:
        return inbox_assistant()
    trigger = {"kind": kind}
    if kind == "schedule":
        trigger.update(every="day", at="09:00")
    elif kind == "app_event":
        trigger["app"] = "WhatsApp"
    request = HireEmployeeRequest.model_validate({
        "idempotency_key": "visible-team", "name": "Maya", "role": "Research assistant",
        "job": "Research competitors", "steps": [{"title": "Research information"}], "trigger": trigger,
    })
    return build_employee_graph(BuildInputs(workflow_id="71", request=request,
        apps=[get_app("whatsapp")] if kind == "app_event" else [], team=True,
        llm=LLMChoice(provider="openai", model="configured-model", local=False), memory=False))


def graph_of(built):
    return {"nodes": deepcopy(built.nodes), "edges": deepcopy(built.edges)}


@pytest.mark.parametrize("kind", ["manual", "schedule", "app_event"])
async def test_new_hire_has_a_visible_validated_route_without_crossing_trigger_flows(kind):
    built = built_team(kind)
    roles, graph = built.node_roles, graph_of(built)
    lead, talk = roles["orchestrator"], roles["talk_agent"]
    assert [edge["source"] for edge in built.edges if edge["target"] == talk and
            edge["targetHandle"] == "input-teammates"] == [lead]
    assert team_plan_in(graph, talk) == built.team_plan
    report = await validate_workflow(nodes=built.nodes, edges=built.edges, parameters_by_id=built.parameters)
    assert report["errors"] == []

    talk_run = collect_downstream_node_ids(roles["talk_trigger"], built.nodes, built.edges)
    assert talk in talk_run and roles["talk_reply"] in talk_run
    assert roles["job_reply"] not in talk_run
    if kind != "manual":
        work_run = collect_downstream_node_ids(roles["trigger"], built.nodes, built.edges)
        assert talk in work_run
        assert roles["talk_reply"] not in work_run


@pytest.mark.parametrize("change", ["missing", "duplicate", "wrong_lead", "wrong_source_handle", "wrong_target_type"])
async def test_new_plan_cannot_submit_through_a_missing_or_invalid_saved_route(change):
    built = built_team()
    graph = graph_of(built)
    link = next(edge for edge in graph["edges"] if is_employee_team_edge(edge))
    if change == "missing":
        graph["edges"].remove(link)
    elif change == "duplicate":
        graph["edges"].append(deepcopy(link))
    elif change == "wrong_lead":
        graph["nodes"].append({"id": "other-lead", "type": "ai_employee", "data": {}})
        link["source"] = "other-lead"
    elif change == "wrong_source_handle":
        link["sourceHandle"] = "output-main"
    else:
        next(node for node in graph["nodes"] if node["id"] == built.node_roles["talk_agent"])["type"] = "web_agent"
    with pytest.raises(ValueError, match="[Cc]onnect|connection"):
        team_plan_in(graph, built.node_roles["talk_agent"])
    report = await validate_workflow(**graph, parameters_by_id=built.parameters)
    assert any(issue["code"] in {"INVALID_EMPLOYEE_TEAM_CONNECTION", "MULTIPLE_EMPLOYEE_TEAM_CONNECTIONS"}
               for issue in report["errors"])


def test_existing_admitted_generation_keeps_legacy_route_without_rewriting_history():
    built = built_team()
    graph = graph_of(built)
    graph["edges"] = [edge for edge in graph["edges"] if not is_employee_team_edge(edge)]
    plan = next(node["data"]["employee_team_plan"] for node in graph["nodes"] if node["id"] == built.node_roles["agent"])
    plan.pop("talk_connection_version")
    resources = {plan.pop(key) for key in EMPLOYEE_TEAM_RESOURCE_FIELDS.values()}
    graph["edges"] = [edge for edge in graph["edges"] if edge["source"] not in resources]
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] not in resources]
    assert team_plan_in(graph, built.node_roles["talk_agent"]) == plan


@pytest.mark.parametrize("broken", ["removed", "wrong_type", "disabled", "unwired", "wrong_handle",
                                    "shared", "second_manager", "duplicate_edge", "wrong_id"])
async def test_admission_requires_exactly_the_saved_leads_task_manager(monkeypatch, broken):
    built = built_team()
    plan, graph = deepcopy(built.team_plan), graph_of(built)
    manager, lead = plan["task_manager_node_id"], plan["lead_node_id"]
    assert connected_task_manager_ids(graph, lead) == [manager]
    node = next(node for node in graph["nodes"] if node["id"] == manager)
    link = next(edge for edge in graph["edges"] if edge["source"] == manager)
    if broken == "removed":
        graph["nodes"].remove(node)
    elif broken == "wrong_type":
        node["type"] = "writeTodos"
    elif broken == "disabled":
        node["data"]["disabled"] = True
    elif broken == "unwired":
        graph["edges"].remove(link)
    elif broken == "wrong_handle":
        link["sourceHandle"] = "output-main"
    elif broken == "shared":
        graph["edges"].append({**link, "target": plan["talk_node_id"]})
    elif broken == "second_manager":
        graph["nodes"].append({**node, "id": "another-manager"})
        graph["edges"].append({**link, "source": "another-manager"})
    elif broken == "duplicate_edge":
        graph["edges"].append(dict(link))
    else:
        plan["task_manager_node_id"] = "different-id"
    monkeypatch.setattr("services.employees.store.get_by_workflow", AsyncMock(return_value=SimpleNamespace(team_plan=plan)))
    with pytest.raises(EmployeeTeamValidationError) as caught:
        await admit_team_plan(object(), "71", graph["nodes"], graph["edges"])
    assert caught.value.issues[0]["code"] == "INVALID_EMPLOYEE_TASK_MANAGER"


async def test_old_runtime_fallback_and_new_runtime_physical_manager():
    from services.plugin.edge_walker import collect_agent_connections

    lead = "lead"
    graph = {"workflow_id": "71", "nodes": [{"id": lead, "type": "ai_employee"}, {"id": "trigger", "type": "start"}],
             "edges": [{"source": "trigger", "target": lead, "targetHandle": "input-main"}]}
    old = {"version": 2, "talk_connection_version": 1, "lead_node_id": lead, "talk_node_id": "talk"}
    _, _, tools, _, _ = await collect_agent_connections(lead, {**graph, "employee_team_plan": old}, object())
    assert [tool["node_id"] for tool in tools] == ["builtin_task_manager_lead"]
    with pytest.raises(EmployeeTeamValidationError, match="Task Manager"):
        await collect_agent_connections(lead, {**graph, "employee_team_plan": {**old, "task_manager_node_id": "assigned"}}, object())


@pytest.mark.parametrize(("field", "owner_field", "error_code"), [
    ("talk_task_manager_node_id", "talk_node_id", "INVALID_EMPLOYEE_TASK_MANAGER"),
    ("lead_skill_node_id", "lead_node_id", "INVALID_EMPLOYEE_MASTER_SKILL"),
    ("talk_skill_node_id", "talk_node_id", "INVALID_EMPLOYEE_MASTER_SKILL"),
])
@pytest.mark.parametrize("broken", ["missing", "shared", "disabled", "miswired"])
def test_recorded_role_resources_belong_exclusively_to_their_own_agent(field, owner_field, error_code, broken):
    built = built_team()
    plan, graph = built.team_plan, graph_of(built)
    assigned, owner = plan[field], plan[owner_field]
    node = next(node for node in graph["nodes"] if node["id"] == assigned)
    link = next(edge for edge in graph["edges"] if edge["source"] == assigned and edge["target"] == owner)
    if broken == "missing":
        graph["nodes"].remove(node)
    elif broken == "disabled":
        node["data"]["disabled"] = True
    elif broken == "miswired":
        link["sourceHandle"] = "output-main"
    else:
        graph["edges"].append({**link, "target": plan["members"][0]["node_id"]})
    with pytest.raises(EmployeeTeamValidationError) as caught:
        validate_employee_team_connection(graph, plan)
    assert caught.value.issues[0]["code"] == error_code


def test_previous_lead_manager_only_plan_does_not_require_new_role_resources():
    built = built_team()
    plan, graph = deepcopy(built.team_plan), graph_of(built)
    historical = {plan.pop(key) for key in ("talk_task_manager_node_id", "lead_skill_node_id", "talk_skill_node_id")}
    graph["nodes"] = [node for node in graph["nodes"] if node["id"] not in historical]
    graph["edges"] = [edge for edge in graph["edges"] if edge["source"] not in historical]
    validate_employee_team_connection(graph, plan)


def test_role_can_keep_additional_owner_added_skills():
    built = built_team()
    graph, plan = graph_of(built), built.team_plan
    graph["nodes"].append({"id": "my-skills", "type": "masterSkill"})
    graph["edges"].append({"source": "my-skills", "sourceHandle": "output-tool",
                           "target": plan["talk_node_id"], "targetHandle": "input-skill"})
    validate_employee_team_connection(graph, plan)


async def test_admission_validates_persisted_plan_after_ui_metadata_was_stripped(monkeypatch):
    built = built_team()
    graph = graph_of(built)
    for node in graph["nodes"]:
        node["data"].pop("employee_team_plan", None)
        node["data"].pop("employee_recipe_version", None)
    monkeypatch.setattr("services.employees.store.get_by_workflow", AsyncMock(return_value=SimpleNamespace(team_plan=built.team_plan)))
    admitted = await admit_team_plan(object(), "71", graph["nodes"], graph["edges"])
    assert team_plan_in({"nodes": admitted, "edges": graph["edges"]}, built.node_roles["talk_agent"]) == built.team_plan
    with pytest.raises(ValueError, match="Connect"):
        await admit_team_plan(object(), "71", graph["nodes"], [])


def test_existing_plan_with_a_new_visible_route_cannot_silently_redirect_jobs():
    built = built_team()
    plan = {key: value for key, value in built.team_plan.items() if key != "talk_connection_version"}
    graph = graph_of(built)
    validate_employee_team_connection(graph, plan)
    next(edge for edge in graph["edges"] if is_employee_team_edge(edge))["source"] = "other-lead"
    with pytest.raises(ValueError, match="Connect"):
        validate_employee_team_connection(graph, plan)


def test_execution_planners_ignore_route_without_excluding_the_scheduled_lead():
    from services.temporal.workflow import MachinaWorkflow
    from services.execution.executor import WorkflowExecutor
    from services.execution.models import ExecutionContext

    nodes = [{"id": "lead", "type": "ai_employee"}, {"id": "talk", "type": "aiAgent"}]
    edges = [employee_team_edge("lead", "talk").to_dict()]
    executable, flow = MachinaWorkflow()._filter_executable_graph(nodes, edges)
    assert {node["id"] for node in executable} == {"lead", "talk"}
    assert flow == []
    executor = object.__new__(WorkflowExecutor)
    assert [set(layer) for layer in executor._compute_execution_layers(nodes, edges)] == [{"lead", "talk"}]
    context = ExecutionContext.create("71", nodes=nodes, edges=edges)
    assert {item.node_id for item in executor._find_ready_nodes(context)} == {"lead", "talk"}
    context.outputs["lead"] = {"response": "Do not inject into Talk"}
    assert executor._gather_node_inputs(context, "talk") == {}


@pytest.mark.parametrize(("change", "code"), [
    ("lead_removed", "INVALID_EMPLOYEE_TEAM_LEAD"),
    ("lead_type_changed", "INVALID_EMPLOYEE_TEAM_LEAD"),
    ("member_removed", "INVALID_EMPLOYEE_TEAM_MEMBER"),
    ("member_type_changed", "INVALID_EMPLOYEE_TEAM_MEMBER"),
    ("member_disconnected", "INVALID_EMPLOYEE_TEAM_MEMBER_CONNECTION"),
    ("member_wrong_handle", "INVALID_EMPLOYEE_TEAM_MEMBER_CONNECTION"),
    ("no_members", "INVALID_EMPLOYEE_TEAM_MEMBERS"),
    ("unapproved_member", "UNAPPROVED_EMPLOYEE_TEAM_MEMBER"),
])
async def test_admission_refuses_a_broken_marked_team_instead_of_single_agent_fallback(monkeypatch, change, code):
    built = built_team()
    graph, plan = graph_of(built), deepcopy(built.team_plan)
    lead, member = plan["lead_node_id"], plan["members"][0]["node_id"]
    if change.endswith("removed"):
        removed = lead if change == "lead_removed" else member
        graph["nodes"] = [node for node in graph["nodes"] if node["id"] != removed]
    elif change.endswith("type_changed"):
        changed = lead if change == "lead_type_changed" else member
        next(node for node in graph["nodes"] if node["id"] == changed)["type"] = "aiAgent"
    elif change == "member_disconnected":
        graph["edges"] = [edge for edge in graph["edges"] if not (edge["source"] == member and edge["target"] == lead)]
    elif change == "member_wrong_handle":
        next(edge for edge in graph["edges"] if edge["source"] == member and edge["target"] == lead)["sourceHandle"] = "output-main"
    elif change == "no_members":
        plan["members"] = []
    else:
        graph["nodes"].append({"id": "unapproved", "type": "aiAgent", "data": {}})
        graph["edges"].append({"source": "unapproved", "target": lead, "sourceHandle": "output-top", "targetHandle": "input-teammates"})
    monkeypatch.setattr("services.employees.store.get_by_workflow", AsyncMock(return_value=SimpleNamespace(team_plan=plan)))
    with pytest.raises(EmployeeTeamValidationError) as caught:
        await admit_team_plan(object(), "71", graph["nodes"], graph["edges"])
    assert caught.value.issues[0]["code"] == code


@pytest.mark.parametrize("action", ["start", "apply"])
@pytest.mark.parametrize("broken", ["talk_link", "lead", "specialist"])
async def test_start_and_apply_return_actionable_team_issues_before_changing_runtime(monkeypatch, action, broken):
    from services.deployment import handlers
    from services.employees.safe_apply import apply_saved_changes
    from services.workflow_sanitizer import sanitize_workflow_graph

    built = built_team()
    graph = sanitize_workflow_graph({**graph_of(built), "owner_id": "owner"})
    if broken == "talk_link":
        graph["edges"] = [edge for edge in graph["edges"] if not is_employee_team_edge(edge)]
    elif broken == "lead":
        graph["nodes"] = [node for node in graph["nodes"] if node["id"] != built.node_roles["agent"]]
    else:
        graph["edges"] = [edge for edge in graph["edges"] if edge.get("targetHandle") != "input-teammates"]
    database = SimpleNamespace(
        get_workflow_control_by_idempotency_key=AsyncMock(return_value=None),
        get_latest_workflow_control=AsyncMock(return_value=None if action == "start" else SimpleNamespace(status="running")),
        get_workflow=AsyncMock(return_value=SimpleNamespace(data=graph)),
        get_node_parameters=AsyncMock(return_value={}),
    )
    service = SimpleNamespace(database=database, begin_generation=AsyncMock())
    monkeypatch.setattr("services.employees.store.get_by_workflow", AsyncMock(return_value=SimpleNamespace(team_plan=built.team_plan)))
    monkeypatch.setattr(handlers, "_control_service", lambda: service)
    monkeypatch.setattr("core.container.container", SimpleNamespace(settings=lambda: SimpleNamespace(employee_safe_apply_enabled=True)))
    if action == "start":
        result = await handlers._run_start({"workflow_id": "71", **graph, "expected_revision": 0,
                                           "parameters_by_id": built.parameters}, None)
        assert result["error"] == "validation_failed"
        issues = result["report"]["errors"]
    else:
        result = await apply_saved_changes(database, "71", owner_id="owner", key="broken")
        assert result["error"] == "invalid_changes"
        issues = result["validation_issues"]
    assert result["success"] is False
    assert issues and issues[0]["message"] and issues[0]["node_id"]
    service.begin_generation.assert_not_awaited()
