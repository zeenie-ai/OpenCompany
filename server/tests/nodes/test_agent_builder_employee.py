"""The Agent Builder on a hired employee: the rule Hire applies
(services/employees/policy.py), one selected agent as the tool target,
skills copied into the shared Skills node, and refusals in plain words."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from nodes.tool import agent_builder as ab
from services.employees import store
from services.employees.genui_catalog import load_genui_catalog
from services.graph_build import main_edge, skill_edge, tool_edge
from services.skill_loader import get_skill_loader
from tests.nodes._agent_builder_harness import (
    WORKFLOW_ID,
    builder_fixture,  # noqa: F401 - the ``builder`` fixture
    call,
    ctx,
    database_fixture,  # noqa: F401 - the ``database`` fixture
    edges_into,
    node,
    save_graph,
    saved,
)

pytestmark = pytest.mark.node_contract

WORKER, TALK, SKILLS = "7:aiAgent:1", "7:aiAgent:2", "7:masterSkill:1"
APPLY = "It becomes part of all your work when the owner presses Apply on your page."
SKILL_TOOL_ENTRY = {"enabled": True, "instructions": "", "isCustomized": False, "required": True}
REAL_PERMISSION = ab._permission


@pytest.fixture(autouse=True)
def approved_owner_changes(monkeypatch):
    """These tests isolate tool/skill policy after owner authorization.

    The public-message and durable grant boundary is exercised separately
    in test_agent_builder_docs and employee permission tests.
    """
    async def approved(*args, **kwargs):
        return None
    monkeypatch.setattr(ab, "_permission", approved)


def employee_graph(*, skills: bool = False) -> dict:
    """Maya: a worker on WhatsApp and the agent the owner talks to, which
    has the Agent Builder."""
    graph = {
        "nodes": [
            node("7:whatsappReceive:1", "whatsappReceive", "New WhatsApp message"),
            node(WORKER, "aiAgent", "Maya", 360, 200),
            node("7:chatTrigger:1", "chatTrigger", "Talk"),
            node(TALK, "aiAgent", "Talk with Maya", 360, 600),
            node("7:agentBuilder:1", "agentBuilder", "Agent Builder"),
        ],
        "edges": [
            main_edge("7:whatsappReceive:1", WORKER).to_dict(),
            main_edge("7:chatTrigger:1", TALK).to_dict(),
            tool_edge("7:agentBuilder:1", TALK).to_dict(),
        ],
    }
    if skills:
        graph["nodes"].append(node(SKILLS, "masterSkill", "Skills"))
        graph["edges"] += [skill_edge(SKILLS, WORKER).to_dict(), skill_edge(SKILLS, TALK).to_dict()]
    return graph


async def hire(database, *, ask_first: bool = True, skills: bool = False) -> None:
    row, _ = await store.reserve(
        database,
        owner_id="owner",
        idempotency_key="hire-1",
        payload_hash="h",
        fields={"role": "Receptionist", "rules": {"ask_first": ask_first, "items": []}},
    )
    roles = {"agent": WORKER, "talk_agent": TALK, "trigger": "7:whatsappReceive:1", **({"skills": SKILLS} if skills else {})}
    await store.mark_ready(database, row.id, workflow_id=WORKFLOW_ID, node_roles=roles)


def talk(**kwargs):
    """A call from the agent the owner talks to."""
    return ctx(TALK, **kwargs)


# ============================================================================
# Tools
# ============================================================================


class TestTools:
    @pytest.mark.parametrize("managed_team", [False, True])
    async def test_explicit_target_is_the_only_tool_owner(self, builder, database, managed_team):
        await save_graph(database, employee_graph())
        await hire(database, ask_first=False)
        if managed_team:
            from models.employees import Employee

            employee = await store.get_by_workflow(database, WORKFLOW_ID)
            async with database.reserved_session() as session:
                row = await session.get(Employee, employee.id)
                row.team_plan = {"version": 2, "members": [{"node_id": WORKER}, {"node_id": TALK}]}
                await session.commit()
        result = await call("add_tool", talk(), node_type="duckduckgoSearch", target_member_id=WORKER)
        graph = await saved(database)
        assert [edge["target"] for edge in graph["edges"] if edge["source"] == "7:duckduckgoSearch:1"] == [WORKER]
        assert not any(op.get("node_type") == "duckduckgoSearch" for op in result.operations)
        assert result.binding_results[0]["available_in_run"] is False

    async def test_target_must_belong_to_employee_even_without_team_plan(self, builder, database):
        graph = employee_graph()
        graph["nodes"].append(node("7:aiAgent:3", "aiAgent", "Another agent"))
        await save_graph(database, graph)
        await hire(database, ask_first=False)
        result = await call("add_tool", talk(), node_type="duckduckgoSearch", target_member_id="7:aiAgent:3")
        assert result.summary == "Choose a member of this employee’s team."
        assert await saved(database) == graph

    async def test_access_is_reviewed_only_for_selected_member(self, builder, database, monkeypatch):
        from services.employees.permissions import decide_access

        async def trusted(*args):
            return True

        monkeypatch.setattr(ab, "_permission", REAL_PERMISSION)
        monkeypatch.setattr("services.employees.permissions.trusted_owner_request", trusted)
        graph = employee_graph()
        await save_graph(database, graph)
        await hire(database, ask_first=False)
        review = await call("add_tool", talk(), node_type="duckduckgoSearch", target_member_id=WORKER)
        assert [access["member_id"] for access in review.required_access] == [WORKER]
        assert await saved(database) == graph
        assert await decide_access(database, review.required_access[0]["request_id"], "owner", True)
        result = await call("add_tool", talk(), node_type="duckduckgoSearch", target_member_id=WORKER)
        assert result.activation_state == "saved"
        other_review = await call("add_tool", talk(call="other-member"), node_type="duckduckgoSearch")
        assert [access["member_id"] for access in other_review.required_access] == [TALK]
        assert other_review.required_access[0]["request_id"] != review.required_access[0]["request_id"]

    async def test_shared_legacy_tool_is_split_with_configuration_and_safe_rebind(self, builder, database):
        graph = employee_graph()
        old_id = "7:duckduckgoSearch:1"
        graph["nodes"].append(node(old_id, "duckduckgoSearch", "Custom web search"))
        graph["edges"] += [tool_edge(old_id, WORKER).to_dict(), tool_edge(old_id, TALK).to_dict()]
        config = {"max_results": 12, "account_id": "existing-account"}
        await save_graph(database, graph, {old_id: config})
        await hire(database, ask_first=False)
        result = await call("add_tool", talk(run=graph), node_type="duckduckgoSearch")
        updated = await saved(database)
        new_id = "7:duckduckgoSearch:2"
        assert [edge["target"] for edge in updated["edges"] if edge["source"] == old_id] == [WORKER]
        assert [edge["target"] for edge in updated["edges"] if edge["source"] == new_id] == [TALK]
        assert await database.get_node_parameters(old_id) == config
        assert await database.get_node_parameters(new_id) == config
        assert result.operations == []  # active snapshot retains its one admitted callable
        assert result.binding_results == [{"node_id": new_id, "saved": True, "available_in_run": False}]
        assert "Apply" in result.summary
        assert any(op["type"] == "delete_edge" for op in builder.frames[0]["data"]["operations"])
        retried = await call("add_tool", talk(run=graph), node_type="duckduckgoSearch")
        later = await call("add_tool", talk(run=graph, call="later"), node_type="duckduckgoSearch")
        assert retried.operations == later.operations == []
        assert not later.binding_results[0]["available_in_run"]
        clean = await call("add_tool", talk(run=updated, call="clean"), node_type="duckduckgoSearch")
        assert clean.operations == []
        assert clean.binding_results[0]["available_in_run"]

    async def test_a_tool_goes_only_to_the_calling_talk_agent(self, builder, database):
        await save_graph(database, employee_graph())
        await hire(database, ask_first=False)

        result = await call("add_tool", talk(), node_type="duckduckgoSearch")

        graph = await saved(database)
        added = graph["nodes"][-1]
        assert (added["id"], added["data"]["label"]) == ("7:duckduckgoSearch:1", "Web search")
        assert edges_into(graph, WORKER, "input-tools") == []
        assert "7:duckduckgoSearch:1" in [e["source"] for e in edges_into(graph, TALK, "input-tools")]
        assert await database.get_node_parameters("7:duckduckgoSearch:1") == {"max_results": 5}
        assert result.summary == f"Added Web search. You can use it now in this conversation. {APPLY}"
        assert result.operations[0]["minted_id"] == "7:duckduckgoSearch:1"

    async def test_the_clock_keeps_the_owners_time(self, builder, database):
        await save_graph(database, employee_graph())
        await hire(database)
        assert await database.save_user_settings({"profile_timezone": "Asia/Kolkata"})

        await call("add_tool", talk(), node_type="currentTimeTool")

        assert await database.get_node_parameters("7:currentTimeTool:1") == {"timezone": "Asia/Kolkata"}

    @pytest.mark.parametrize(
        "node_type,params",
        [
            ("googleCalendar", {"operation": "list", "calendar_id": "primary", "send_updates": "none"}),
            ("stripeAction", {"command": ""}),
            ("browser", {"interaction": "full"}),
        ],
    )
    async def test_asking_first_adds_what_sends_or_spends_whole(self, builder, database, node_type, params):
        # Each call waits for the owner (Stripe is refused, the browser reads
        # only) while they ask first, decided per call: services/approvals.
        await save_graph(database, employee_graph())
        await hire(database, ask_first=True)
        builder.connected = ["google_calendar", "stripe", "web"]

        result = await call("add_tool", talk(), node_type=node_type)

        assert result.summary.startswith("Added "), result.summary
        assert await database.get_node_parameters(f"7:{node_type}:1") == params

    async def test_an_app_that_is_not_connected_is_refused(self, builder, database):
        await save_graph(database, employee_graph())
        await hire(database, ask_first=False)

        result = await call("add_tool", talk(), node_type="googleSheets")

        assert result.summary == "Google Sheets isn't connected yet. Connect it in Settings > Connectors first."
        assert builder.frames == []

    async def test_a_tool_no_hire_can_have_is_refused(self, builder, database):
        await save_graph(database, employee_graph())
        await hire(database, ask_first=False)

        result = await call("add_tool", talk(), node_type="httpRequest")

        assert result.summary.endswith("isn't something a hired employee can be given.")
        assert builder.frames == []

    async def test_a_name_that_is_no_node_type_gets_a_hint_for_the_agent(self, builder, database):
        await save_graph(database, employee_graph())
        await hire(database, ask_first=False)

        result = await call("add_tool", talk(), node_type="Google Calendar")

        assert result.summary == "add_tool: 'Google Calendar' is not a node type. Use a type from inspect_canvas available_tools."

    async def test_the_hire_allowlist_is_honoured(self, builder, database, monkeypatch):
        await save_graph(database, employee_graph())
        await hire(database, ask_first=False)
        monkeypatch.setattr("services.node_allowlist.is_hire_allowed", lambda node_type: node_type != "duckduckgoSearch")

        result = await call("add_tool", talk(), node_type="duckduckgoSearch")

        assert result.summary == "Web search isn't available to hired employees."
        assert builder.frames == []

    async def test_a_tool_the_worker_has_is_not_shared_with_the_talk_agent(self, builder, database):
        """Same type, separate physical nodes and independent configuration."""
        graph = employee_graph()
        graph["nodes"].append(node("7:duckduckgoSearch:1", "duckduckgoSearch", "Web search"))
        graph["edges"].append(tool_edge("7:duckduckgoSearch:1", WORKER).to_dict())
        await save_graph(database, graph, {"7:duckduckgoSearch:1": {"max_results": 12}})
        await hire(database, ask_first=False)

        result = await call("add_tool", talk(run=graph), node_type="duckduckgoSearch")

        saved_graph = await saved(database)
        assert [n["id"] for n in saved_graph["nodes"] if n["type"] == "duckduckgoSearch"] == ["7:duckduckgoSearch:1", "7:duckduckgoSearch:2"]
        assert [e["source"] for e in edges_into(saved_graph, WORKER, "input-tools")] == ["7:duckduckgoSearch:1"]
        assert "7:duckduckgoSearch:2" in [e["source"] for e in edges_into(saved_graph, TALK, "input-tools")]
        assert await database.get_node_parameters("7:duckduckgoSearch:1") == {"max_results": 12}
        bind, edge = result.operations
        assert (bind["type"], bind["minted_id"], bind["parameters"]) == ("add_node", "7:duckduckgoSearch:2", {"max_results": 5})
        assert (edge["type"], edge["target"]) == ("add_edge", TALK)
        assert result.summary.startswith("Added Web search 2.")

    async def test_a_tool_saved_after_the_snapshot_is_bound_again(self, builder, database):
        graph = employee_graph()
        graph["nodes"].append(node("7:duckduckgoSearch:1", "duckduckgoSearch", "Web search"))
        graph["edges"].append(tool_edge("7:duckduckgoSearch:1", TALK).to_dict())
        await save_graph(database, graph)
        await hire(database, ask_first=False)

        result = await call("add_tool", talk(run=employee_graph()), node_type="duckduckgoSearch")

        assert [op["minted_id"] for op in result.operations] == ["7:duckduckgoSearch:1"]
        assert result.summary == "You have Web search. You can use it now in this conversation."
        assert builder.frames == []

    async def test_a_tool_the_run_has_needs_nothing(self, builder, database):
        graph = employee_graph()
        graph["nodes"].append(node("7:duckduckgoSearch:1", "duckduckgoSearch", "Web search"))
        graph["edges"].append(tool_edge("7:duckduckgoSearch:1", TALK).to_dict())
        await save_graph(database, graph)
        await hire(database, ask_first=False)

        result = await call("add_tool", talk(run=graph), node_type="duckduckgoSearch")

        assert result.operations == []
        assert result.summary == "You already have Web search. Use it directly."


# ============================================================================
# Skills
# ============================================================================


class TestSkills:
    async def test_a_library_skill_is_copied_into_the_shared_skills_node(self, builder, database):
        await save_graph(database, employee_graph(skills=True), {SKILLS: {"skill_folder": "assistant", "skills_config": {"skill": SKILL_TOOL_ENTRY}}})
        await hire(database, skills=True)
        await database.create_user_skill(name="refund-policy", display_name="Refund policy", description="Refunds.", instructions="Refund within 30 days.")

        result = await call("add_skill", talk(), skill_name="refund-policy")

        config = (await database.get_node_parameters(SKILLS))["skills_config"]
        assert config == {
            "skill": SKILL_TOOL_ENTRY,
            "refund-policy": {"enabled": True, "instructions": "Refund within 30 days.", "isCustomized": False, "description": "Refunds."},
        }
        assert await saved(database) == employee_graph(skills=True)
        assert result.summary == "Learned 'refund-policy'. It applies from the next message."
        assert result.operations == []

    async def test_a_discover_skill_is_copied_with_its_text(self, builder, database):
        await save_graph(database, employee_graph(skills=True), {SKILLS: {"skills_config": {"skill": SKILL_TOOL_ENTRY}}})
        await hire(database, skills=True)

        await call("add_skill", talk(), skill_name="book-appointments")

        entry = (await database.get_node_parameters(SKILLS))["skills_config"]["book-appointments"]
        text = get_skill_loader().load_skill("book-appointments").instructions
        assert entry["instructions"] == text and text.strip()

    async def test_without_a_skills_node_one_is_added_for_both_agents(self, builder, database):
        await save_graph(database, employee_graph())
        await hire(database)
        await database.create_user_skill(name="refund-policy", display_name="Refund policy", description="Refunds.", instructions="Refund within 30 days.")

        result = await call("add_skill", talk(), skill_name="refund-policy")

        graph = await saved(database)
        added = graph["nodes"][-1]
        assert (added["id"], added["data"]["label"]) == (SKILLS, "Skills")
        assert edges_into(graph, WORKER, "input-skill")[0]["source"] == SKILLS
        assert edges_into(graph, TALK, "input-skill")[0]["source"] == SKILLS
        assert (await database.get_node_parameters(SKILLS))["skills_config"]["skill"] == SKILL_TOOL_ENTRY
        assert result.summary == f"Learned 'refund-policy'. {APPLY}"

    async def test_a_built_in_outside_discover_is_not_offered(self, builder, database):
        await save_graph(database, employee_graph())
        await hire(database)

        result = await call("add_skill", talk(), skill_name="memory-skill")

        assert result.summary == "There's no skill called 'memory-skill' in the owner's library or in Discover."
        assert builder.frames == []

    @pytest.mark.parametrize("name,code", [("assistant-personality", "personality"), ("skill", "reserved")])
    async def test_skills_no_hire_can_have_are_refused(self, builder, database, name, code):
        from services.employees.policy import check_skill

        await save_graph(database, employee_graph())
        await hire(database)

        result = await call("add_skill", talk(), skill_name=name)

        decision = check_skill(name)
        assert decision.code == code and result.summary == decision.reason
        assert builder.frames == []


# ============================================================================
# What an employee is offered
# ============================================================================


class TestCatalogue:
    async def test_inspect_lists_only_what_policy_allows(self, builder, database):
        await save_graph(database, employee_graph())
        await hire(database, ask_first=True)
        builder.connected = ["web"]
        await database.create_user_skill(name="refund-policy", display_name="Refund policy", description="Refunds.", instructions="Refund within 30 days.")

        result = await call("inspect_canvas", talk())

        tools = {entry["type"]: entry for entry in result.available_tools}
        # Asking first takes nothing away: what sends waits per call.
        from services.employees.apps import get_apps

        app_tools = {tool.type for app in get_apps().values() for tool in app.tools}
        assert {"duckduckgoSearch", "writeTodos", "currentTimeTool", "simpleMemory", "canvas"} | app_tools == set(tools)
        assert tools["browser"] == {
            "type": "browser",
            "display_name": "Browser",
            "description": tools["browser"]["description"],
            "app": "Web browser",
            "connected": True,
        }
        assert tools["googleSheets"]["connected"] is False
        assert result.available_agents == []
        skills = [entry["name"] for entry in result.available_skills]
        assert skills[0] == "refund-policy" and "book-appointments" in skills
        assert "memory-skill" not in skills and "assistant-personality" not in skills
        assert result.employee == {"asks_first": True, "agents": [WORKER, TALK]}

    def test_the_discover_folder_is_the_clients(self):
        source = (Path(__file__).resolve().parents[3] / "client" / "src" / "features" / "home" / "data" / "skills.ts").read_text(encoding="utf-8")
        match = re.search(r"export const DISCOVER_SKILL_FOLDER = '([^']+)';", source)
        assert match and match.group(1) == ab._DISCOVER_SKILL_FOLDER


async def test_a_hired_employee_cannot_add_teammates(builder, database):
    await save_graph(database, employee_graph())
    await hire(database)

    result = await call("add_subagent", talk(), agent_type="coding_agent")

    assert "only team-lead agents" in result.summary
    assert builder.frames == []
