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


def build(*, manual=True, trigger=None, allowed=lambda _: True, skills=(), job="Research customers and book appointments", memory=True, timezone="UTC"):
    request = HireEmployeeRequest.model_validate({
        "idempotency_key": "team-test", "job": job, "name": "Maya", "role": "Receptionist",
        "apps": ["Google Calendar"] if manual else ["WhatsApp", "Google Calendar"],
        "steps": [{"title": "Research information"}, {"title": "Book appointments"}],
        "trigger": trigger or ({"kind": "manual"} if manual else {"kind": "app_event", "app": "WhatsApp"}),
    })
    return build_employee_graph(BuildInputs(
        workflow_id="71", request=request, apps=[get_app("google_calendar")] if manual else [get_app("whatsapp"), get_app("google_calendar")],
        connected_app_ids={"google_calendar", "whatsapp"}, llm=LLMChoice(provider="openai", model="configured-model", local=False),
        team=True, allowed=allowed, skills=skills, memory=memory, timezone=timezone,
    ))


def node(built, role):
    return next(node for node in built.nodes if node["id"] == built.node_roles[role])


@pytest.mark.parametrize("manual", [True, False])
@pytest.mark.parametrize("memory", [True, False])
def test_each_tool_node_has_one_owner_and_private_clock(manual, memory):
    built = build(manual=manual, memory=memory, timezone="Asia/Kolkata")
    owners = {}
    for edge in built.edges:
        if edge.get("targetHandle") == "input-tools":
            owners.setdefault(edge["source"], set()).add(edge["target"])
    assert all(len(targets) == 1 for targets in owners.values()), owners
    agents = [built.node_roles["agent"], built.node_roles["talk_agent"],
              *(member["node_id"] for member in built.team_plan["members"])]
    clocks = {node["id"] for node in built.nodes if node["type"] == "currentTimeTool"}
    for agent in agents:
        assigned = [clock for clock in clocks if owners.get(clock) == {agent}]
        assert len(assigned) == 1
        assert built.parameters[assigned[0]] == {"timezone": "Asia/Kolkata"}
    for member in built.team_plan["members"]:
        assert set(member["tools"]) == {tool for tool, targets in owners.items() if targets == {member["node_id"]}}


def test_selection_is_bounded_and_job_specific():
    assert team_preview("Research competitors") == [{"responsibility": "Researches information"}]
    assert [role.key for role in select_responsibilities("Manage meetings and Gmail", ["Google Calendar"])] == ["calendar", "operations"]
    assert 1 <= len(select_responsibilities("Do a task")) <= 3
    assert len(select_responsibilities("Research software and social posts and email appointments")) == 3


def test_new_browser_employee_gets_complete_private_capability():
    built = build(job="Use the browser to read website accounts")
    member = next(member for member in built.team_plan["members"] if member["node_type"] == "browser_agent")
    tools = [node for node in built.nodes if node["id"] in member["tools"]]
    assert {node["type"] for node in tools} >= {"browser", "visionAnalyze"}
    browser = next(node for node in tools if node["type"] == "browser")
    assert [edge["target"] for edge in built.edges if edge.get("source") == browser["id"] and edge.get("targetHandle") == "input-tools"] == [member["node_id"]]
    skills = next(edge["source"] for edge in built.edges if edge["target"] == member["node_id"] and edge.get("targetHandle") == "input-skill")
    assert built.parameters[skills]["skills_config"]["browser-skill"]["enabled"]
    assert sum(edge["target"] == member["node_id"] and edge.get("targetHandle") == "input-context" for edge in built.edges) == 1
    assert built.parameters[member["node_id"]]["model"] == "configured-model"


def test_browser_employee_respects_companion_node_policy():
    with pytest.raises(BuildError, match="Browser tools"):
        build(job="Use the browser to read website accounts", allowed=lambda kind: kind != "visionAnalyze")


@pytest.mark.parametrize("job", ["Develop software", "Create a dynamic interactive weather report site", "Build a web app"])
async def test_new_coding_employee_gets_its_own_connected_pi_tools(job):
    from services.coding_agent_recipe import default_pi_tool_types
    from services.node_registry import get_node_class

    built = build(job=job)
    member = next(member for member in built.team_plan["members"] if member["node_type"] == "coding_agent")
    tools = [item for item in built.nodes if item["type"] in default_pi_tool_types()]
    assert {item["type"] for item in tools} == set(default_pi_tool_types())
    assert len(tools) == len(default_pi_tool_types())
    for tool in tools:
        assert tool["id"] in member["tools"]
        assert tool["data"]["label"] == get_node_class(tool["type"]).display_name
        assert [edge["target"] for edge in built.edges if edge["source"] == tool["id"]
                and edge.get("targetHandle") == "input-tools"] == [member["node_id"]]
    assert built.parameters[member["node_id"]]["model"] == "configured-model"
    assert sum(edge["target"] == member["node_id"] and edge.get("targetHandle") == "input-context" for edge in built.edges) == 1
    report = await validate_workflow(nodes=built.nodes, edges=built.edges, parameters_by_id=built.parameters)
    assert report["errors"] == [], report["errors"]


def test_coding_employee_respects_companion_node_policy():
    with pytest.raises(BuildError, match="Pi coding tools"):
        build(job="Develop software", allowed=lambda kind: kind != "piBash")
    with pytest.raises(BuildError, match="coding specialist"):
        build(job="Build a website", allowed=lambda kind: kind != "coding_agent")


def test_researching_an_existing_website_does_not_grant_coding_tools():
    roles = select_responsibilities("Read a website and create a report about that website", ["Web browser"])
    assert [role.key for role in roles] == ["research"]


def test_role_prompts_describe_the_tools_the_contact_and_lead_own():
    built = build()
    contact = built.parameters[built.node_roles["talk_agent"]]["system_message"]
    lead = built.parameters[built.node_roles["agent"]]["system_message"]
    assert "submit_job" in contact and "write_todos" not in contact
    assert "add_subagent" in contact and "target_member_id" in contact
    assert "'continue'" in contact and "required_access" in contact
    assert "spawn_" in lead and "write_todos" in lead
    assert "agent_builder" not in lead and "goes straight to" not in lead


def test_shipped_hire_allowlist_allows_the_complete_coding_bundle():
    from services.coding_agent_recipe import default_pi_tool_types
    from services.node_allowlist import get_node_allowlist_service

    built = build(job="Develop software", allowed=get_node_allowlist_service().is_hire_allowed)
    member = next(member for member in built.team_plan["members"] if member["node_type"] == "coding_agent")
    tool_types = {item["type"] for item in built.nodes if item["id"] in member["tools"]}
    assert set(default_pi_tool_types()) <= tool_types


def test_hire_refuses_a_recipe_that_reintroduces_shared_tools(monkeypatch):
    from services.employees import team_recipe
    from services.graph_build import tool_edge

    compose = team_recipe.build_team

    def broken_recipe(built, inputs):
        team = compose(built, inputs)
        tool = next(edge["source"] for edge in team.edges if edge.get("targetHandle") == "input-tools"
                    and edge.get("target") == team.node_roles["talk_agent"])
        team.edges.append(tool_edge(tool, team.node_roles["agent"]).to_dict())
        return team

    monkeypatch.setattr(team_recipe, "build_team", broken_recipe)
    with pytest.raises(BuildError) as error:
        build()
    assert error.value.code == "invalid_tool_ownership"


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
    calendar = next(member for member in members if member["role"] == "calendar")
    assert "calendar-care" in calendar["skills"]
    assert "software-style" not in calendar["skills"]
    for member in members:
        assert any(edge["source"] == member["node_id"] and edge["sourceHandle"] == "output-top" and edge["target"] == lead and edge["targetHandle"] == "input-teammates" for edge in built.edges)
    assert built.parameters[built.node_roles["task_trigger"]]["parent_node_id"] == lead


def test_submission_receipts_cannot_reach_reviewed_delivery():
    built = build()
    roles = built.node_roles
    assert built.team_plan["talk_delivery_node_ids"] == [roles["job_reply"]]
    assert built.parameters[roles["job_reply"]]["message"] == "{{" + label_key(node(built, "agent")["data"]["label"]) + ".response}}"
    assert not any(edge["target"] == roles["job_reply"] for edge in built.edges)
    assert not any(node["type"] == "employeeJob" for node in built.nodes)
    assert built.team_plan["version"] == 2
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
    assert any(edge["source"] == roles["trigger"] and edge["target"] == roles["agent"] for edge in built.edges)
    assert {roles["gate"], roles["reply"]} <= set(built.team_plan["delivery_node_ids"])


def test_unavailable_registered_specialist_uses_configured_custom_agent():
    built = build(allowed=lambda kind: kind != "web_agent")
    researcher = next(member for member in built.team_plan["members"] if member["role"] == "research")
    assert researcher["node_type"] == "aiAgent"
    assert "Researches information" in built.parameters[researcher["node_id"]]["system_message"]


@pytest.mark.parametrize("trigger", [{"kind": "manual"}, {"kind": "schedule", "every": "weekday", "at": "08:15"},
                                    {"kind": "app_event", "app": "WhatsApp"}], ids=["talk", "schedule", "app"])
@pytest.mark.parametrize("library", [False, True])
def test_lead_and_talk_own_their_tools_and_instructions_under_default_hire_policy(trigger, library):
    from services.graph_build import skill_edge, tool_edge
    from services.node_allowlist import get_node_allowlist_service

    selected = [LibrarySkill("report-style", "Custom format", "Use the owner's saved report format.")] if library else []
    built = build(manual=trigger["kind"] == "manual", trigger=trigger, skills=selected,
                  allowed=get_node_allowlist_service().is_hire_allowed)
    plan, roles = built.team_plan, built.node_roles
    assert built.trigger["kind"] == trigger["kind"]
    assert {node["id"] for node in built.nodes if node["type"] == "taskManager"} == {
        roles["task_manager"], roles["talk_task_manager"],
    }
    assigned = set()
    for owner, manager, holder in (
        (plan["lead_node_id"], plan["task_manager_node_id"], plan["lead_skill_node_id"]),
        (plan["talk_node_id"], plan["talk_task_manager_node_id"], plan["talk_skill_node_id"]),
    ):
        assert [edge for edge in built.edges if edge["source"] == manager] == [tool_edge(manager, owner).to_dict()]
        assert [edge for edge in built.edges if edge["source"] == holder] == [skill_edge(holder, owner).to_dict()]
        assert [edge["source"] for edge in built.edges if edge["target"] == owner and edge["targetHandle"] == "input-skill"] == [holder]
        assert built.parameters[manager] == {}
        assert built.parameters[holder]["skill_folder"] == "assistant"
        assigned.update((manager, holder))
    assert len(assigned) == 4
    assert roles["task_manager"] == plan["task_manager_node_id"] and roles["talk_task_manager"] == plan["talk_task_manager_node_id"]
    assert roles["lead_skills"] == plan["lead_skill_node_id"] and roles["talk_skills"] == plan["talk_skill_node_id"]
    lead_skills = built.parameters[roles["lead_skills"]]["skills_config"]
    talk_skills = built.parameters[roles["talk_skills"]]["skills_config"]
    assert set(lead_skills) == {"skill", "team-delegation"} and lead_skills["team-delegation"]["instructions"]
    if library:
        assert roles["skills"] == roles["talk_skills"]
        assert set(talk_skills) == {"skill", "report-style"}
        assert talk_skills["report-style"]["instructions"] == selected[0].instructions
    else:
        assert set(talk_skills) == {"skill", "team-intake"} and talk_skills["team-intake"]["instructions"]


@pytest.mark.parametrize("kind", ["ai_employee", "taskManager", "taskTrigger", "masterSkill"])
def test_required_team_capabilities_fail_closed(kind):
    with pytest.raises(BuildError):
        build(allowed=lambda candidate: candidate != kind)


async def test_team_passes_real_live_contract_validation():
    built = build()
    report = await validate_workflow(nodes=built.nodes, edges=built.edges, parameters_by_id=built.parameters)
    assert report["errors"] == [], report["errors"]
