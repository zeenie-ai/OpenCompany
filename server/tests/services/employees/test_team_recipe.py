"""Real template team graphs: isolation, bindings, review, delivery gates."""
from __future__ import annotations

import pytest
import nodes  # noqa: F401

from services.employees.builder import BuildError, BuildInputs, LibrarySkill, build_employee_graph
from services.employees.apps import get_app
from services.employees.hire_request import HireEmployeeRequest
from services.employees.llm import LLMChoice
from services.employees.team_recipe import select_responsibilities, team_preview
from services.graph_build import label_key
from services.workflow_validator import validate_workflow


def build(*, manual=True, allowed=lambda _: True, skills=(), job="Research customers and book appointments"):
    request = HireEmployeeRequest.model_validate({
        "idempotency_key": "team-test", "job": job, "name": "Maya", "role": "Receptionist",
        "apps": ["Google Calendar"] if manual else ["WhatsApp", "Google Calendar"],
        "steps": [{"title": "Research information"}, {"title": "Book appointments"}],
        "trigger": {"kind": "manual"} if manual else {"kind": "app_event", "app": "WhatsApp"},
    })
    return build_employee_graph(BuildInputs(
        workflow_id="71", request=request, apps=[get_app("google_calendar")] if manual else [get_app("whatsapp"), get_app("google_calendar")],
        connected_app_ids={"google_calendar", "whatsapp"}, llm=LLMChoice(provider="openai", model="configured-model", local=False),
        team=True, allowed=allowed, skills=skills,
    ))


def node(built, role):
    return next(node for node in built.nodes if node["id"] == built.node_roles[role])


def test_selection_is_bounded_and_job_specific():
    assert team_preview("Research competitors") == [{"responsibility": "Researches information"}]
    assert [role.key for role in select_responsibilities("Manage meetings and Gmail", ["Google Calendar"])] == ["calendar", "operations"]
    assert 1 <= len(select_responsibilities("Do a task")) <= 3
    assert len(select_responsibilities("Research software and social posts and email appointments")) == 3


def test_manual_team_has_separate_contact_and_scoped_specialists():
    built = build(skills=[LibrarySkill("calendar-care", "Book appointments", "Check for conflicts."), LibrarySkill("software-style", "Write software", "Prefer clear code.")])
    lead, talk = built.node_roles["agent"], built.node_roles["talk_agent"]
    assert lead != talk
    assert node(built, "agent")["type"] == "ai_employee"
    assert node(built, "talk_agent")["type"] == "aiAgent"
    assert built.team_plan["requires"] == ["temporal", "agent_workflow"]
    members = built.team_plan["members"]
    assert 1 <= len(members) <= 3
    assert {member["node_type"] for member in members} >= {"web_agent", "productivity_agent"}
    for agent in [lead, talk, *(member["node_id"] for member in members)]:
        assert built.parameters[agent]["model"] == "configured-model"
        contexts = [edge["source"] for edge in built.edges if edge["target"] == agent and edge["targetHandle"] == "input-context"]
        assert len(contexts) == 1
    all_contexts = [edge["source"] for edge in built.edges if edge["targetHandle"] == "input-context"]
    assert len(set(all_contexts)) == len(all_contexts)
    assert not any(node["type"] == "taskManager" for node in built.nodes)
    calendar = next(member for member in members if member["role"] == "calendar")
    assert "calendar-care" in calendar["skills"]
    assert "software-style" not in calendar["skills"]
    for member in members:
        assert any(edge["source"] == member["node_id"] and edge["sourceHandle"] == "output-top" and edge["target"] == lead and edge["targetHandle"] == "input-teammates" for edge in built.edges)
    assert built.parameters[built.node_roles["task_trigger"]]["parent_node_id"] == lead


def test_submission_receipts_cannot_reach_reviewed_delivery():
    built = build()
    roles = built.node_roles
    assert built.parameters[roles["job_submit"]]["delivery_node_ids"] == [roles["job_reply"]]
    assert built.parameters[roles["job_reply"]]["message"] == "{{" + label_key(node(built, "agent")["data"]["label"]) + ".response}}"
    assert not any(edge["target"] == roles["job_reply"] for edge in built.edges)
    assert any(edge["source"] == roles["agent"] and edge["target"] == roles["job_delivery"] for edge in built.edges)
    assert any(edge["source"] == roles["talk_agent"] and edge["target"] == roles["talk_reply"] for edge in built.edges)


def test_public_jobs_keep_recipients_and_approvals_behind_job_boundary():
    built = build(manual=False)
    roles = built.node_roles
    trigger_key = label_key(node(built, "trigger")["data"]["label"])
    assert built.parameters[roles["gate"]]["recipient"] == "{{" + trigger_key + ".sender_phone}}"
    gate_key = label_key(node(built, "gate")["data"]["label"])
    assert built.parameters[roles["reply"]]["phone"] == "{{" + gate_key + ".recipient}}"
    assert not any(edge["source"] in {roles["agent"], roles["trigger"]} and edge["target"] in {roles["gate"], roles["reply"]} for edge in built.edges)
    assert any(edge["source"] == roles["gate"] and edge["target"] == roles["reply"] and edge["data"]["condition"] for edge in built.edges)
    assert any(edge["source"] == roles["trigger"] and edge["target"] == roles["job_intake"] for edge in built.edges)


def test_unavailable_registered_specialist_uses_configured_custom_agent():
    built = build(allowed=lambda kind: kind != "web_agent")
    researcher = next(member for member in built.team_plan["members"] if member["role"] == "research")
    assert researcher["node_type"] == "aiAgent"
    assert "Researches information" in built.parameters[researcher["node_id"]]["system_message"]


@pytest.mark.parametrize("kind", ["ai_employee", "employeeJob", "taskTrigger", "masterSkill"])
def test_required_team_capabilities_fail_closed(kind):
    with pytest.raises(BuildError):
        build(allowed=lambda candidate: candidate != kind)


async def test_team_passes_real_live_contract_validation():
    built = build()
    report = await validate_workflow(nodes=built.nodes, edges=built.edges, parameters_by_id=built.parameters)
    assert report["errors"] == [], report["errors"]
