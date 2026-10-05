"""Contract tests for the agentBuilder multi-op plugin in an editor-built
workflow (no employee row; test_agent_builder_employee.py covers hired
employees).

Operations run against a real database: every change is saved through
``apply_graph_additions``, so the saved graph, the parameter rows and the
frame open editors receive are what is asserted.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nodes.tool import agent_builder as ab
from services.graph_build import skill_edge, tool_edge
from services.node_registry import get_node_class
from tests.nodes._agent_builder_harness import (
    WORKFLOW_ID,
    agents_graph,
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


def _registry(**type_to_kind) -> dict:
    return {ntype: SimpleNamespace(component_kind=kind) for ntype, kind in type_to_kind.items()}


def _node_class_full(
    component_kind: str,
    display_name: str = "",
    description: str = "",
    tool_description: str = "",
    *,
    usable_as_tool: bool = False,
    group: tuple = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        component_kind=component_kind,
        display_name=display_name,
        description=description,
        tool_description=tool_description,
        usable_as_tool=usable_as_tool,
        group=group,
    )


def _allowlist(**overrides):
    config = {
        "show_all": True,
        "enabled_nodes": [],
        "disabled_groups": [],
        "disabled_nodes": [],
        "disabled_credential_categories": [],
        "disabled_skill_folders": [],
        **overrides,
    }
    from services import node_allowlist as nal

    return patch.object(nal.NodeAllowlistService, "get_config", lambda self: config)


# ============================================================================
# Plugin registration
# ============================================================================


class TestRegistration:
    def test_node_is_registered(self):
        assert get_node_class("agentBuilder") is ab.AgentBuilderNode

    def test_preserves_operations_and_adds_documentation_access(self):
        assert set(ab.AgentBuilderNode._operations.keys()) == {
            "inspect_canvas",
            "inspect_node",
            "search_docs",
            "read_doc",
            "plan_update",
            "apply_update",
            "add_tool",
            "add_skill",
            "add_subagent",
            "create_workflow",
        }

    def test_default_operation_is_inspect_canvas(self):
        assert ab.AgentBuilderParams().operation == "inspect_canvas"

    def test_the_llm_calls_it_agent_builder(self):
        assert ab.AgentBuilderNode.tool_name == "agent_builder"

    def test_is_tool_node(self):
        from services.plugin import ToolNode

        assert issubclass(ab.AgentBuilderNode, ToolNode)

    def test_handle_topology_matches_canonical_tool_shape(self):
        handles = {h["name"]: h for h in ab.AgentBuilderNode.handles}
        assert set(handles) == {"input-main", "output-tool"}
        assert (handles["output-tool"]["kind"], handles["output-tool"]["position"], handles["output-tool"]["role"]) == ("output", "top", "tools")


# ============================================================================
# The caller: the agent the runtime names
# ============================================================================


class TestCaller:
    async def test_the_caller_is_the_agent_the_runtime_names(self, builder, database):
        """One Agent Builder serves two agents; the change goes to the one
        that called, not to whichever edge comes first."""
        await save_graph(database, agents_graph())

        result = await call("add_tool", ctx("7:aiAgent:2"), node_type="httpRequest")

        [edge] = [op for op in result.operations if op["type"] == "add_edge"]
        assert edge["target"] == "7:aiAgent:2"
        assert builder.frames[0]["data"]["caller_node_id"] == "7:aiAgent:2"

    async def test_temporal_names_the_caller_as_the_invoking_agent(self, builder, database):
        await save_graph(database, agents_graph())

        result = await call("add_tool", ctx("7:aiAgent:2", invoking=True), node_type="httpRequest")

        assert [op["target"] for op in result.operations if op["type"] == "add_edge"] == ["7:aiAgent:2"]

    async def test_without_a_caller_nothing_is_added(self, builder, database):
        await save_graph(database, agents_graph())

        result = await call("add_tool", ctx(None), node_type="httpRequest")

        assert result.operations == []
        assert result.summary == "Only an agent can ask me to add things."
        assert builder.frames == []

    def test_no_caller_is_guessed_from_edges(self):
        assert not hasattr(ab, "_resolve_caller")
        assert not hasattr(ab, "_resolve_caller_from")


# ============================================================================
# inspect_canvas
# ============================================================================


class TestInspectCanvas:
    async def test_returns_the_saved_canvas_and_what_is_wired_to_the_caller(self, builder, database):
        graph = agents_graph()
        graph["nodes"].append(node("7:httpRequest:1", "httpRequest", "HTTP Request"))
        graph["edges"].append(tool_edge("7:httpRequest:1", "7:aiAgent:1").to_dict())
        await save_graph(database, graph, {"7:httpRequest:1": {"url": "https://x", "headers": "secret"}})

        result = await call("inspect_canvas", ctx("7:aiAgent:1"))

        assert result.operation == "inspect_canvas"
        assert len(result.nodes) == 5 and len(result.edges) == 3
        assert result.you["node_id"] == "7:aiAgent:1"
        assert {entry["source_id"] for entry in result.you["incoming"]} == {"7:agentBuilder:1", "7:httpRequest:1"}
        # Only planner-relevant fields, read from the parameter row.
        http = next(entry for entry in result.nodes if entry["id"] == "7:httpRequest:1")
        assert http["key_params"] == {"url": "https://x"}
        assert "2 tool(s) wired to you" in result.summary
        assert result.employee is None

    async def test_without_a_caller_there_is_no_you(self, builder, database):
        await save_graph(database, agents_graph())

        result = await call("inspect_canvas", ctx(None))

        assert result.you is None

    async def test_available_tools_come_from_the_registry_minus_the_denied(self, builder):
        registry = {
            "httpRequest": _node_class_full("tool", "HTTP Request", "Make HTTP requests", ""),
            "braveSearch": _node_class_full("tool", "Brave Search", "Search the web", "Search the web via Brave"),
            "agentBuilder": _node_class_full("tool", "Agent Builder"),
            "masterSkill": _node_class_full("tool", "Master Skill"),
        }
        with patch.object(ab, "registered_node_classes", return_value=registry):
            result = await call("inspect_canvas", ctx())

        assert {t["type"] for t in result.available_tools} == {"httpRequest", "braveSearch"}
        brave = next(t for t in result.available_tools if t["type"] == "braveSearch")
        # tool_description wins: it is the LLM-facing text.
        assert (brave["display_name"], brave["description"]) == ("Brave Search", "Search the web via Brave")

    async def test_available_agents(self, builder):
        registry = {
            "coding_agent": _node_class_full("agent", "Coding Agent", "Writes code"),
            "web_agent": _node_class_full("agent", "Web Agent", "Browses the web"),
            "httpRequest": _node_class_full("tool", "HTTP Request"),
        }
        with patch.object(ab, "registered_node_classes", return_value=registry):
            result = await call("inspect_canvas", ctx())

        assert {a["type"] for a in result.available_agents} == {"coding_agent", "web_agent"}

    async def test_dual_purpose_plugins_are_tools_but_chat_models_are_not(self, builder):
        registry = {
            "calculatorTool": _node_class_full("tool", "Calculator"),
            "googleGmail": _node_class_full("square", "Gmail", usable_as_tool=True, group=("google", "tool")),
            "openaiChatModel": _node_class_full("model", "OpenAI", usable_as_tool=True, group=("model", "tool")),
        }
        with patch.object(ab, "registered_node_classes", return_value=registry):
            result = await call("inspect_canvas", ctx())

        assert {t["type"] for t in result.available_tools} == {"calculatorTool", "googleGmail"}

    async def test_the_operator_blocklists_hide_tools(self, builder):
        registry = {
            "twitterSearch": _node_class_full("square", "Twitter Search", usable_as_tool=True, group=("social", "tool")),
            "emailSend": _node_class_full("square", "Email Send", usable_as_tool=True, group=("email", "tool")),
            "taskManager": _node_class_full("tool", "Task Manager"),
            "httpRequest": _node_class_full("tool", "HTTP Request"),
        }
        with patch.object(ab, "registered_node_classes", return_value=registry), _allowlist(
            disabled_groups=["email"], disabled_nodes=["httpRequest"]
        ):
            result = await call("inspect_canvas", ctx())

        assert {t["type"] for t in result.available_tools} == {"twitterSearch"}

    async def test_available_skills_are_the_skill_registry_by_name_and_the_library(self, builder, database):
        """The real SkillLoader registry (no patch), by frontmatter name, and
        the owner's library after it."""
        await database.create_user_skill(
            name="refund-policy", display_name="Refund policy", description="How refunds work.", instructions="Refund within 30 days."
        )
        # Named like a built-in: the library copy is the one offered.
        await database.create_user_skill(
            name="memory-skill", display_name="Memory", description="The owner's own memory skill.", instructions="Remember things."
        )

        result = await call("inspect_canvas", ctx())

        skills = {entry["name"]: entry for entry in result.available_skills}
        assert skills["refund-policy"]["description"] == "How refunds work."
        assert skills["memory-skill"]["description"] == "The owner's own memory skill."
        assert [entry["name"] for entry in result.available_skills].count("memory-skill") == 1
        assert "http-request-skill" in skills and "book-appointments" in skills
        assert all(set(entry) == {"name", "description"} for entry in result.available_skills)

    async def test_skills_in_a_disabled_folder_are_not_offered(self, builder):
        with _allowlist(disabled_skill_folders=["web_agent"]):
            result = await call("inspect_canvas", ctx())

        names = {entry["name"] for entry in result.available_skills}
        assert "http-request-skill" not in names and "memory-skill" in names

    def test_task_manager_is_intrinsic_not_palette_addable(self):
        from services.node_allowlist import get_node_allowlist_service

        config = get_node_allowlist_service().get_config()
        assert "taskManager" not in config["enabled_nodes"]
        assert "taskManager" not in config["disabled_nodes"]
        assert "taskManager" not in ab._allowed_tool_types()

    def test_android_nodes_enabled_in_allowlist_json(self):
        from constants import ANDROID_SERVICE_NODE_TYPES
        from services.node_allowlist import get_node_allowlist_service

        config = get_node_allowlist_service().get_config()
        android_types = set(ANDROID_SERVICE_NODE_TYPES)
        assert android_types <= set(config["enabled_nodes"])
        assert "android_agent" in config["enabled_nodes"]
        assert "androidTool" not in config["enabled_nodes"]
        assert android_types <= ab._allowed_tool_types()
        assert "android_agent" in ab._allowed_subagent_types()


# ============================================================================
# add_tool
# ============================================================================


class TestAddTool:
    async def test_rejects_empty_node_type(self, builder):
        result = await call("add_tool", ctx(), node_type="")

        assert result.operations == []
        assert "node_type is required" in result.summary

    async def test_rejects_disallowed_type(self, builder, database):
        await save_graph(database, agents_graph())
        with patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool")):
            result = await call("add_tool", ctx(), node_type="unknownTool")

        assert result.operations == []
        assert "not an allowed tool type" in result.summary

    async def test_rejects_self_and_master_skill(self, builder, database):
        await save_graph(database, agents_graph())
        with patch.object(ab, "registered_node_classes", return_value=_registry(agentBuilder="tool", masterSkill="tool", httpRequest="tool")):
            for forbidden in ("agentBuilder", "masterSkill"):
                result = await call("add_tool", ctx(), node_type=forbidden)
                assert result.operations == []
                assert "not an allowed tool type" in result.summary

    async def test_adds_a_canonical_tool_wired_through_output_tool(self, builder, database):
        await save_graph(database, agents_graph())

        result = await call("add_tool", ctx(), node_type="httpRequest")

        graph = await saved(database)
        label = get_node_class("httpRequest").display_name
        added = graph["nodes"][-1]
        assert (added["id"], added["type"], added["data"]) == ("7:httpRequest:1", "httpRequest", {"label": label})
        [edge] = [e for e in edges_into(graph, "7:aiAgent:1", "input-tools") if e["source"] == "7:httpRequest:1"]
        assert edge["sourceHandle"] == "output-tool"
        assert edge["id"] == "e-7:httpRequest:1-output-tool-7:aiAgent:1-input-tools"
        # A real parameter row, not parameters in the graph JSON.
        assert await database.get_node_parameters("7:httpRequest:1") == {}
        assert "parameters" not in added["data"]

        add_node, add_edge = result.operations
        assert (add_node["type"], add_node["minted_id"], add_node["node_type"], add_node["label"]) == ("add_node", "7:httpRequest:1", "httpRequest", label)
        assert (add_edge["edge_id"], add_edge["source_handle"], add_edge["target_handle"]) == (edge["id"], "output-tool", "input-tools")
        [frame] = builder.frames
        assert frame["type"] == "workflow_ops_apply"
        assert frame["data"]["persisted"] is True
        assert frame["data"]["operations"] == result.operations
        assert builder.changed == [WORKFLOW_ID]
        assert "Available immediately" in result.summary

    async def test_summary_follows_the_auto_rebind_setting(self, builder, database):
        await save_graph(database, agents_graph())

        result = await call("add_tool", ctx(auto_rebind_tools=False), node_type="httpRequest")

        assert "Available on your next turn" in result.summary
        assert "Available immediately" not in result.summary

    async def test_a_tool_the_run_has_is_reused(self, builder, database):
        graph = agents_graph()
        graph["nodes"].append(node("7:httpRequest:1", "httpRequest", "HTTP"))
        graph["edges"].append(tool_edge("7:httpRequest:1", "7:aiAgent:1").to_dict())
        await save_graph(database, graph)

        result = await call("add_tool", ctx(run=graph), node_type="httpRequest")

        assert result.operations == []
        assert "already wired" in result.summary and "7:httpRequest:1" in result.summary
        assert builder.frames == []
        assert await saved(database) == graph

    async def test_a_saved_tool_the_run_lacks_is_handed_back_to_bind(self, builder, database):
        """Added after this run's snapshot (an earlier message, say): nothing
        is saved, and the saved node comes back for the agent loop to bind,
        instead of an "already wired" the agent cannot use."""
        graph = agents_graph()
        graph["nodes"].append(node("7:httpRequest:1", "httpRequest", "HTTP"))
        graph["edges"].append(tool_edge("7:httpRequest:1", "7:aiAgent:1").to_dict())
        await save_graph(database, graph, {"7:httpRequest:1": {"url": "https://x"}})

        result = await call("add_tool", ctx(run=agents_graph()), node_type="httpRequest")

        assert result.operations == [
            {
                "type": "add_node",
                "client_ref": "7:httpRequest:1",
                "node_type": "httpRequest",
                "parameters": {"url": "https://x"},
                "label": "HTTP",
                "minted_id": "7:httpRequest:1",
            }
        ]
        assert "this run started without it" in result.summary
        assert builder.frames == []
        assert await saved(database) == graph

    async def test_a_retried_call_replays_instead_of_adding_twice(self, builder, database):
        await save_graph(database, agents_graph())

        first = await call("add_tool", ctx(call="call-9"), node_type="httpRequest")
        again = await call("add_tool", ctx(call="call-9"), node_type="httpRequest")

        graph = await saved(database)
        assert [n["id"] for n in graph["nodes"] if n["type"] == "httpRequest"] == ["7:httpRequest:1"]
        assert again.operations == first.operations
        # Announced again: the first announcement may never have gone out.
        assert len(builder.frames) == 2 and builder.frames[0] == builder.frames[1]

    async def test_an_unsaved_caller_is_refused(self, builder, database):
        await save_graph(database, agents_graph())

        result = await call("add_tool", ctx("7:aiAgent:9"), node_type="httpRequest")

        assert result.operations == []
        assert result.summary == "Save the workflow first: the agent that asked isn't in the saved workflow yet."
        assert builder.frames == []

    async def test_an_unsaved_workflow_is_refused(self, builder):
        result = await call("add_tool", ctx(), node_type="httpRequest")

        assert result.summary == "Save the workflow first, then ask again."


# ============================================================================
# add_skill
# ============================================================================

_SKILL_TOOL_ENTRY = {"enabled": True, "instructions": "", "isCustomized": False, "required": True}


class TestAddSkill:
    async def test_rejects_empty_skill_name(self, builder):
        result = await call("add_skill", ctx(), skill_name="")

        assert result.operations == []
        assert "skill_name is required" in result.summary

    async def test_rejects_an_unknown_skill(self, builder, database):
        await save_graph(database, agents_graph())

        result = await call("add_skill", ctx(), skill_name="nonexistent-skill")

        assert "no skill named 'nonexistent-skill'" in result.summary
        assert builder.frames == []

    async def test_a_built_in_skill_by_its_real_name_gets_a_master_skill(self, builder, database):
        """The SkillLoader registry, unpatched: a skill is found by its
        frontmatter name wherever its folder is."""
        await save_graph(database, agents_graph())
        from services.skill_loader import get_skill_loader

        description = get_skill_loader()._registry["memory-skill"].description

        result = await call("add_skill", ctx(), skill_name="memory-skill")

        graph = await saved(database)
        added = graph["nodes"][-1]
        assert (added["id"], added["type"], added["data"]["label"]) == ("7:masterSkill:1", "masterSkill", "Master Skill")
        assert edges_into(graph, "7:aiAgent:1", "input-skill") == [skill_edge("7:masterSkill:1", "7:aiAgent:1").to_dict()]
        # A built-in keeps no text: the runtime reads its SKILL.md.
        assert await database.get_node_parameters("7:masterSkill:1") == {
            "skill_folder": "assistant",
            "skills_config": {
                "skill": _SKILL_TOOL_ENTRY,
                "memory-skill": {"enabled": True, "instructions": "", "isCustomized": False, "description": description},
            },
        }
        # Nothing for the agent loop to bind; editors got the batch.
        assert result.operations == []
        assert builder.frames[0]["data"]["persisted"] is True
        assert "7:masterSkill:1" in result.summary

    async def test_merges_into_the_existing_master_skill_row(self, builder, database):
        graph = agents_graph()
        graph["nodes"].append(node("7:masterSkill:1", "masterSkill", "Master Skill"))
        graph["edges"].append(skill_edge("7:masterSkill:1", "7:aiAgent:1").to_dict())
        old = {"enabled": True, "instructions": "Mine.", "isCustomized": True}
        await save_graph(database, graph, {"7:masterSkill:1": {"skill_folder": "assistant", "skills_config": {"old-skill": old}}})

        result = await call("add_skill", ctx(), skill_name="http-request-skill")

        row = await database.get_node_parameters("7:masterSkill:1")
        assert row["skills_config"]["old-skill"] == old
        assert row["skills_config"]["http-request-skill"]["enabled"] is True
        assert await saved(database) == graph
        [frame] = builder.frames
        assert frame["data"]["operations"] == [{"type": "set_node_parameters", "node_id": "7:masterSkill:1", "parameters": row}]
        assert result.summary.startswith("Enabled 'http-request-skill' on your Master Skill")

    async def test_an_enabled_skill_changes_nothing(self, builder, database):
        graph = agents_graph()
        graph["nodes"].append(node("7:masterSkill:1", "masterSkill", "Master Skill"))
        graph["edges"].append(skill_edge("7:masterSkill:1", "7:aiAgent:1").to_dict())
        await save_graph(database, graph, {"7:masterSkill:1": {"skills_config": {"http-request-skill": {"enabled": True, "instructions": ""}}}})

        result = await call("add_skill", ctx(), skill_name="http-request-skill")

        assert "already enabled" in result.summary and "7:masterSkill:1" in result.summary
        assert builder.frames == []

    async def test_a_library_skill_carries_its_text(self, builder, database):
        """The runtime's fallback for an entry without text reads files only,
        so a library skill's text is copied in."""
        await save_graph(database, agents_graph())
        await database.create_user_skill(name="refund-policy", display_name="Refund policy", description="Refunds.", instructions="Refund within 30 days.")

        await call("add_skill", ctx(), skill_name="refund-policy")

        entry = (await database.get_node_parameters("7:masterSkill:1"))["skills_config"]["refund-policy"]
        assert entry == {"enabled": True, "instructions": "Refund within 30 days.", "isCustomized": False, "description": "Refunds."}

    async def test_a_skill_in_a_disabled_folder_is_refused(self, builder, database):
        await save_graph(database, agents_graph())
        with _allowlist(disabled_skill_folders=["assistant"]):
            result = await call("add_skill", ctx(), skill_name="memory-skill")

        assert "no skill named 'memory-skill'" in result.summary


# ============================================================================
# add_subagent
# ============================================================================


def _lead_graph() -> dict:
    return {
        "nodes": [node("7:orchestrator_agent:1", "orchestrator_agent", "Lead", 100, 100), node("7:agentBuilder:1", "agentBuilder", "AB")],
        "edges": [tool_edge("7:agentBuilder:1", "7:orchestrator_agent:1").to_dict()],
    }


class TestAddSubagent:
    async def test_rejects_empty_agent_type(self, builder):
        result = await call("add_subagent", ctx(), agent_type="")

        assert result.operations == []
        assert "agent_type is required" in result.summary

    async def test_rejects_when_caller_is_not_team_lead(self, builder, database):
        await save_graph(database, agents_graph())
        with patch.object(ab, "registered_node_classes", return_value=_registry(coding_agent="agent")):
            result = await call("add_subagent", ctx(), agent_type="coding_agent")

        assert result.operations == []
        assert "team-lead" in result.summary

    async def test_rejects_disallowed_agent_type(self, builder, database):
        await save_graph(database, _lead_graph())
        with patch.object(ab, "registered_node_classes", return_value=_registry(coding_agent="agent")):
            result = await call("add_subagent", ctx("7:orchestrator_agent:1"), agent_type="not_a_real_agent")

        assert "not an allowed agent type" in result.summary

    async def test_rejects_spawning_another_team_lead(self, builder, database):
        await save_graph(database, _lead_graph())
        with patch.object(ab, "registered_node_classes", return_value=_registry(ai_employee="agent")):
            result = await call("add_subagent", ctx("7:orchestrator_agent:1"), agent_type="ai_employee")

        assert "cannot spawn another team-lead" in result.summary

    async def test_adds_the_teammate_with_its_context(self, builder, database):
        await save_graph(database, _lead_graph())

        result = await call("add_subagent", ctx("7:orchestrator_agent:1"), agent_type="coding_agent")

        graph = await saved(database)
        agent, context = graph["nodes"][-2:]
        assert (agent["id"], agent["type"]) == ("7:coding_agent:1", "coding_agent")
        assert context["id"] == "7:context:1"
        assert context["data"] == {"label": "Context", "systemManaged": True, "agentNodeId": "7:coding_agent:1"}
        assert edges_into(graph, "7:coding_agent:1", "input-context")[0]["source"] == "7:context:1"
        [teammate] = edges_into(graph, "7:orchestrator_agent:1", "input-teammates")
        assert (teammate["source"], teammate["sourceHandle"]) == ("7:coding_agent:1", "output-top")
        assert [op["type"] for op in result.operations] == ["add_node", "add_node", "add_edge", "add_edge"]
        assert result.operations[0]["node_type"] == "coding_agent"
        assert result.operations[-1]["target_handle"] == "input-teammates"

    async def test_an_existing_teammate_is_reused(self, builder, database):
        graph = _lead_graph()
        graph["nodes"].append(node("7:coding_agent:1", "coding_agent", "Coder"))
        graph["edges"].append({"id": "t", "source": "7:coding_agent:1", "sourceHandle": "output-top", "target": "7:orchestrator_agent:1", "targetHandle": "input-teammates"})
        await save_graph(database, graph)

        result = await call("add_subagent", ctx("7:orchestrator_agent:1"), agent_type="coding_agent")

        assert result.operations == []
        assert "already wired" in result.summary and "7:coding_agent:1" in result.summary
        assert builder.frames == []


# ============================================================================
# create_workflow
# ============================================================================


class TestCreateWorkflow:
    """Creation delegates to the shared Hire/control boundary, never a placeholder graph."""

    async def test_operator_can_disable_creation(self, monkeypatch):
        monkeypatch.setattr(ab, "_CREATE_WORKFLOW_ENABLED", False)
        result = await call("create_workflow", ctx(), workflow_name="Should Not Be Created")

        assert result.workflow_id is None
        assert "disabled by the operator" in result.summary.lower()

    async def test_rejects_empty_name(self, monkeypatch):
        monkeypatch.setattr(ab, "_CREATE_WORKFLOW_ENABLED", True)

        result = await call("create_workflow", ctx(), workflow_name="")

        assert result.workflow_id is None
        assert "workflow_name is required" in result.summary

    async def test_uses_shared_control_and_stable_identity(self, monkeypatch):
        create = AsyncMock(return_value={"success": True, "workflow_id": "1", "activation_state": "starting"})
        monkeypatch.setattr("services.employees.control.builder_create", create)
        context = ctx(call="create-call")
        result = await call("create_workflow", context, workflow_name="My New Workflow", workflow_description="An optional description")
        assert result.workflow_id == "1"
        assert result.activation_state == "starting"
        assert create.await_args.args == (context, "My New Workflow", "An optional description", result.request_id)
        again = await call("create_workflow", context, workflow_name="My New Workflow", workflow_description="An optional description")
        assert again.request_id == result.request_id

    async def test_returns_failure_summary_when_persist_fails(self, monkeypatch):
        create = AsyncMock(return_value={"success": False, "summary": "Could not save the employee", "activation_state": "failed"})
        monkeypatch.setattr("services.employees.control.builder_create", create)
        result = await call("create_workflow", ctx(), workflow_name="Doomed Workflow")
        assert result.workflow_id is None
        assert result.summary == "Could not save the employee"
        assert result.activation_state == "failed"
