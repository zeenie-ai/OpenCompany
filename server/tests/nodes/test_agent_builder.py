"""Contract tests for the agentBuilder multi-op plugin.

agentBuilder is a single multi-op tool node exposing 5 canvas-mutation
operations through the standard `@Operation` plugin pattern (matching
gmail / calendar / drive). The LLM sees one tool with an `operation`
discriminator; we test each operation's happy + reject paths directly
on the node instance, mocking the broadcaster and registry lookups so
no live state is touched.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nodes.tool import agent_builder as ab
from services.plugin import NodeContext


pytestmark = pytest.mark.node_contract


# ============================================================================
# Helpers
# ============================================================================


def _make_ctx(
    *,
    nodes=None,
    edges=None,
    workflow_id: str = "wf-test",
    self_id: str = "ab-1",
) -> NodeContext:
    return NodeContext(
        node_id=self_id,
        node_type="agentBuilder",
        workflow_id=workflow_id,
        nodes=nodes or [],
        edges=edges or [],
        raw={"workflow_id": workflow_id},
    )


def _agent_node(node_id: str, node_type: str = "aiAgent", **params) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "data": {"label": node_type, "parameters": params},
    }


def _edge(source: str, target: str, target_handle: str = "input-tools") -> dict:
    return {
        "source": source,
        "target": target,
        "sourceHandle": "output-main",
        "targetHandle": target_handle,
    }


def _node_class(component_kind: str) -> SimpleNamespace:
    return SimpleNamespace(component_kind=component_kind)


def _registry(**type_to_kind) -> dict:
    return {ntype: _node_class(kind) for ntype, kind in type_to_kind.items()}


# ============================================================================
# Plugin registration
# ============================================================================


class TestRegistration:
    def test_node_is_registered(self):
        from services.node_registry import get_node_class

        cls = get_node_class("agentBuilder")
        assert cls is ab.AgentBuilderNode

    def test_has_five_operations(self):
        ops = set(ab.AgentBuilderNode._operations.keys())
        assert ops == {
            "inspect_canvas",
            "add_tool",
            "add_skill",
            "add_subagent",
            "create_workflow",
        }

    def test_default_operation_is_inspect_canvas(self):
        params = ab.AgentBuilderParams()
        assert params.operation == "inspect_canvas"

    def test_is_tool_node(self):
        """agentBuilder is a tool-only plugin; subclasses ToolNode (not
        ActionNode + usable_as_tool=True). The ToolNode base IS the
        contract for nodes that exist solely to be invoked by an LLM
        through an agent's input-tools handle."""
        from services.plugin import ToolNode

        assert issubclass(ab.AgentBuilderNode, ToolNode)

    def test_handle_topology_matches_canonical_tool_shape(self):
        """Canonical tool node: input-main left + output-tool top role=tools.
        The output-tool shape is what wires into an agent's input-tools handle."""
        handles = list(ab.AgentBuilderNode.handles)
        assert len(handles) == 2
        names = {h["name"] for h in handles}
        assert names == {"input-main", "output-tool"}
        out = next(h for h in handles if h["name"] == "output-tool")
        assert out["kind"] == "output"
        assert out["position"] == "top"
        assert out["role"] == "tools"

    def test_no_provided_tools_dict(self):
        """Sanity check: the tribal PROVIDED_TOOLS attr from the original
        design must NOT exist on the multi-op rewrite."""
        assert not hasattr(ab.AgentBuilderNode, "PROVIDED_TOOLS")


# ============================================================================
# inspect_canvas
# ============================================================================


class TestInspectCanvas:
    async def test_returns_node_and_edge_summaries(self):
        nodes = [
            _agent_node("agent-1"),
            {"id": "ab-1", "type": "agentBuilder", "data": {"label": "AB"}},
            {"id": "tool-1", "type": "httpRequest", "data": {"label": "HTTP", "parameters": {"url": "https://x"}}},
        ]
        edges = [
            _edge("ab-1", "agent-1", "input-tools"),
            _edge("tool-1", "agent-1", "input-tools"),
        ]
        node = ab.AgentBuilderNode()
        ctx = _make_ctx(nodes=nodes, edges=edges)
        params = ab.AgentBuilderParams(operation="inspect_canvas")

        result = await node.inspect_canvas(ctx, params)

        assert result.operation == "inspect_canvas"
        assert len(result.nodes) == 3
        assert len(result.edges) == 2
        assert result.you["node_id"] == "agent-1"  # caller resolved via input-tools edge
        # Both agentBuilder and httpRequest are wired to agent-1's input-tools.
        assert "2 tool(s) wired" in result.summary
        assert "httpRequest" in result.summary

    async def test_falls_back_to_self_when_no_caller_wired(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx(nodes=[], edges=[], self_id="ab-1")
        params = ab.AgentBuilderParams(operation="inspect_canvas")

        result = await node.inspect_canvas(ctx, params)

        assert result.you["node_id"] == "ab-1"


# ============================================================================
# add_tool
# ============================================================================


class TestAddTool:
    async def test_rejects_empty_node_type(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="add_tool", node_type="")

        result = await node.add_tool(ctx, params)

        assert result.operations == []
        assert "node_type is required" in result.summary

    async def test_rejects_disallowed_type(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="add_tool", node_type="unknownTool")

        with patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool")):
            result = await node.add_tool(ctx, params)

        assert result.operations == []
        assert "not an allowed tool type" in result.summary

    async def test_rejects_self_and_master_skill(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()

        with patch.object(
            ab, "registered_node_classes", return_value=_registry(agentBuilder="tool", masterSkill="tool", httpRequest="tool")
        ):
            for forbidden in ("agentBuilder", "masterSkill"):
                params = ab.AgentBuilderParams(operation="add_tool", node_type=forbidden)
                result = await node.add_tool(ctx, params)
                assert result.operations == []
                assert "not an allowed tool type" in result.summary

    async def test_emits_add_node_and_add_edge_ops(self):
        node = ab.AgentBuilderNode()
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        ctx = _make_ctx(edges=edges)
        params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool")),
            patch.object(ab, "_broadcast", new_callable=AsyncMock) as mock_bcast,
        ):
            result = await node.add_tool(ctx, params)

        assert len(result.operations) == 2
        assert result.operations[0]["type"] == "add_node"
        assert result.operations[0]["node_type"] == "httpRequest"
        assert result.operations[1]["type"] == "add_edge"
        assert result.operations[1]["target"] == "agent-1"
        assert result.operations[1]["target_handle"] == "input-tools"
        mock_bcast.assert_awaited_once()
        # Default ctx has no ``auto_rebind_tools`` in raw → defaults True
        # → the LLM is told the new tool is callable in the same turn.
        assert "Available immediately" in result.summary

    async def test_add_node_op_carries_minted_id_for_fe_alignment(self):
        """The ``minted_id`` field is the contract that keeps the
        BE-side tool_node_id (used for status broadcasts in the rebind
        path) aligned with the React Flow node id the FE applier
        renders under. Without it the canvas node won't glow when the
        rebound tool runs because the broadcast target id never
        matches a real node."""
        node = ab.AgentBuilderNode()
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        ctx = _make_ctx(edges=edges)
        params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool")),
            patch.object(ab, "_broadcast", new_callable=AsyncMock),
        ):
            result = await node.add_tool(ctx, params)

        add_node_op = result.operations[0]
        assert add_node_op["type"] == "add_node"
        assert "minted_id" in add_node_op, "add_tool must stamp minted_id on the add_node op"
        # Convention: ``{type}-{ms}-{salt}`` — same shape as
        # services.workflow_import.remap_node_ids + frontend newId().
        assert add_node_op["minted_id"].startswith("httpRequest-")
        parts = add_node_op["minted_id"].split("-")
        assert len(parts) == 3, "minted_id must be 3 segments: type-timestamp-salt"

    async def test_summary_branches_on_auto_rebind_flag(self):
        """When the user disables the "Auto-Rebind Tools After Canvas
        Changes" toggle, the operation summary must read
        "Available on your next turn" so the LLM doesn't try to call
        the new tool before a Run-stop-Run cycle."""
        node = ab.AgentBuilderNode()
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        ctx = NodeContext(
            node_id="ab-1",
            node_type="agentBuilder",
            workflow_id="wf-test",
            nodes=[],
            edges=edges,
            raw={"workflow_id": "wf-test", "auto_rebind_tools": False},
        )
        params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool")),
            patch.object(ab, "_broadcast", new_callable=AsyncMock),
        ):
            result = await node.add_tool(ctx, params)

        assert "Available on your next turn" in result.summary
        assert "Available immediately" not in result.summary


# ============================================================================
# add_skill
# ============================================================================


class TestAddSkill:
    async def test_rejects_empty_skill_folder(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="add_skill", skill_folder="")

        result = await node.add_skill(ctx, params)

        assert result.operations == []
        assert "skill_folder is required" in result.summary

    async def test_rejects_unknown_skill_folder(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(
            operation="add_skill",
            skill_folder="nonexistent-skill",
        )

        with patch.object(ab, "_skill_folder_exists", return_value=False):
            result = await node.add_skill(ctx, params)

        assert result.operations == []
        assert "not found" in result.summary

    async def test_toggles_on_existing_master_skill(self):
        master = {
            "id": "ms-1",
            "type": "masterSkill",
            "data": {"parameters": {"skills_config": {"old-skill": {"enabled": True}}}},
        }
        agent = _agent_node("agent-1")
        edges = [
            _edge("ab-1", "agent-1", "input-tools"),
            _edge("ms-1", "agent-1", "input-skill"),
        ]
        node = ab.AgentBuilderNode()
        ctx = _make_ctx(nodes=[agent, master], edges=edges)
        params = ab.AgentBuilderParams(
            operation="add_skill",
            skill_folder="http-request-skill",
        )

        with patch.object(ab, "_skill_folder_exists", return_value=True), patch.object(ab, "_broadcast", new_callable=AsyncMock):
            result = await node.add_skill(ctx, params)

        assert len(result.operations) == 1
        op = result.operations[0]
        assert op["type"] == "set_node_parameters"
        assert op["node_id"] == "ms-1"
        new_cfg = op["parameters"]["skills_config"]
        assert new_cfg["http-request-skill"]["enabled"] is True
        assert new_cfg["old-skill"]["enabled"] is True  # preserved

    async def test_spawns_master_skill_when_absent(self):
        agent = _agent_node("agent-1")
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        node = ab.AgentBuilderNode()
        ctx = _make_ctx(nodes=[agent], edges=edges)
        params = ab.AgentBuilderParams(
            operation="add_skill",
            skill_folder="memory-skill",
        )

        with patch.object(ab, "_skill_folder_exists", return_value=True), patch.object(ab, "_broadcast", new_callable=AsyncMock):
            result = await node.add_skill(ctx, params)

        assert len(result.operations) == 2
        assert result.operations[0]["type"] == "add_node"
        assert result.operations[0]["node_type"] == "masterSkill"
        seeded = result.operations[0]["parameters"]["skills_config"]
        assert seeded["memory-skill"]["enabled"] is True
        assert result.operations[1]["type"] == "add_edge"


# ============================================================================
# add_subagent
# ============================================================================


class TestAddSubagent:
    async def test_rejects_empty_agent_type(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="add_subagent", agent_type="")

        result = await node.add_subagent(ctx, params)

        assert result.operations == []
        assert "agent_type is required" in result.summary

    async def test_rejects_when_caller_is_not_team_lead(self):
        agent = _agent_node("agent-1", "aiAgent")  # aiAgent is NOT a team-lead
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        node = ab.AgentBuilderNode()
        ctx = _make_ctx(nodes=[agent], edges=edges)
        params = ab.AgentBuilderParams(
            operation="add_subagent",
            agent_type="coding_agent",
        )

        with patch.object(ab, "registered_node_classes", return_value=_registry(coding_agent="agent")):
            result = await node.add_subagent(ctx, params)

        assert result.operations == []
        assert "team-lead" in result.summary

    async def test_rejects_disallowed_agent_type(self):
        agent = _agent_node("agent-1", "orchestrator_agent")
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        node = ab.AgentBuilderNode()
        ctx = _make_ctx(nodes=[agent], edges=edges)
        params = ab.AgentBuilderParams(
            operation="add_subagent",
            agent_type="not_a_real_agent",
        )

        with patch.object(ab, "registered_node_classes", return_value=_registry(coding_agent="agent")):
            result = await node.add_subagent(ctx, params)

        assert result.operations == []
        assert "not an allowed agent type" in result.summary

    async def test_rejects_spawning_another_team_lead(self):
        agent = _agent_node("agent-1", "orchestrator_agent")
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        node = ab.AgentBuilderNode()
        ctx = _make_ctx(nodes=[agent], edges=edges)
        params = ab.AgentBuilderParams(
            operation="add_subagent",
            agent_type="ai_employee",
        )

        with patch.object(ab, "registered_node_classes", return_value=_registry(ai_employee="agent")):
            result = await node.add_subagent(ctx, params)

        assert result.operations == []
        assert "cannot spawn another team-lead" in result.summary

    async def test_emits_add_node_and_teammate_edge(self):
        agent = _agent_node("agent-1", "orchestrator_agent")
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        node = ab.AgentBuilderNode()
        ctx = _make_ctx(nodes=[agent], edges=edges)
        params = ab.AgentBuilderParams(
            operation="add_subagent",
            agent_type="coding_agent",
        )

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(coding_agent="agent")),
            patch.object(ab, "_broadcast", new_callable=AsyncMock),
        ):
            result = await node.add_subagent(ctx, params)

        assert len(result.operations) == 2
        assert result.operations[0]["node_type"] == "coding_agent"
        assert result.operations[1]["target_handle"] == "input-teammates"


# ============================================================================
# create_workflow
# ============================================================================


class TestCreateWorkflow:
    """create_workflow is temporarily disabled. The first test locks
    the disabled default; the remaining tests flip the
    ``_CREATE_WORKFLOW_ENABLED`` flag so the underlying implementation
    keeps being exercised — when the operator re-enables it, no test
    rewrite needed."""

    async def test_disabled_by_default(self):
        """With the temporary feature flag OFF, ``create_workflow``
        returns a polite no-op summary regardless of the workflow_name
        param. No DB calls, no workflow created."""
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(
            operation="create_workflow",
            workflow_name="Should Not Be Created",
        )

        # Ensure the flag is in its default OFF state (defensive — the
        # constant lives at module top so this also catches an
        # accidental commit of `_CREATE_WORKFLOW_ENABLED = True`).
        assert ab._CREATE_WORKFLOW_ENABLED is False, "Temporary disable flag must default to False"

        result = await node.create_workflow(ctx, params)

        assert result.workflow_id is None
        assert "temporarily disabled" in result.summary.lower()

    async def test_rejects_empty_name(self, monkeypatch):
        monkeypatch.setattr(ab, "_CREATE_WORKFLOW_ENABLED", True)
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="create_workflow", workflow_name="")

        result = await node.create_workflow(ctx, params)

        assert result.workflow_id is None
        assert "workflow_name is required" in result.summary

    async def test_persists_via_database_and_returns_id(self, monkeypatch):
        monkeypatch.setattr(ab, "_CREATE_WORKFLOW_ENABLED", True)
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(
            operation="create_workflow",
            workflow_name="My New Workflow",
            workflow_description="An optional description",
        )

        mock_db = MagicMock()
        mock_db.save_workflow = AsyncMock(return_value=True)
        # Wave 14: agent_builder calls next_available_slug, which
        # reads ``list_workflow_slugs`` for the dedup counter scan.
        mock_db.list_workflow_slugs = AsyncMock(return_value=[])
        mock_container = MagicMock()
        mock_container.database.return_value = mock_db

        with patch("core.container.container", mock_container):
            result = await node.create_workflow(ctx, params)

        assert result.workflow_id is not None
        # Wave 14: workflow ids are bare 32-hex UUIDs (no prefix).
        assert len(result.workflow_id) == 32
        assert all(c in "0123456789abcdef" for c in result.workflow_id)
        # Slug is the human-readable identifier surfaced in the summary.
        assert "My_New_Workflow_1" in result.summary
        mock_db.save_workflow.assert_awaited_once()

    async def test_returns_failure_summary_when_persist_fails(self, monkeypatch):
        monkeypatch.setattr(ab, "_CREATE_WORKFLOW_ENABLED", True)
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(
            operation="create_workflow",
            workflow_name="Doomed Workflow",
        )

        mock_db = MagicMock()
        mock_db.save_workflow = AsyncMock(return_value=False)
        mock_db.list_workflow_slugs = AsyncMock(return_value=[])
        mock_container = MagicMock()
        mock_container.database.return_value = mock_db

        with patch("core.container.container", mock_container):
            result = await node.create_workflow(ctx, params)

        assert result.workflow_id is None
        assert "failed to persist" in result.summary


# ============================================================================
# Catalogue visibility — inspect_canvas exposes the FULL registry of
# tools / agents / skills so the LLM can pick the right one in a single
# response instead of probing through error messages.
# ============================================================================


def _node_class_full(
    component_kind: str,
    display_name: str = "",
    description: str = "",
    tool_description: str = "",
    *,
    usable_as_tool: bool = False,
    group: tuple = (),
) -> SimpleNamespace:
    """Like ``_node_class`` but carries every attr the catalogue helpers
    read (``component_kind`` / ``usable_as_tool`` / ``group`` /
    ``display_name`` / ``description`` / ``tool_description``)."""
    return SimpleNamespace(
        component_kind=component_kind,
        display_name=display_name,
        description=description,
        tool_description=tool_description,
        usable_as_tool=usable_as_tool,
        group=group,
    )


class TestCatalogueVisibility:
    async def test_inspect_canvas_includes_available_tools(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="inspect_canvas")
        registry = {
            "httpRequest": _node_class_full("tool", "HTTP Request", "Make HTTP requests", ""),
            "braveSearch": _node_class_full("tool", "Brave Search", "Search the web", "Search the web via Brave"),
            "calculatorTool": _node_class_full("tool", "Calculator", "Arithmetic"),
            "agentBuilder": _node_class_full("tool", "Agent Builder"),  # in DENIED set
            "masterSkill": _node_class_full("tool", "Master Skill"),  # in DENIED set
        }

        with patch.object(ab, "registered_node_classes", return_value=registry):
            result = await node.inspect_canvas(ctx, params)

        assert result.available_tools is not None
        types = {t["type"] for t in result.available_tools}
        assert types == {"httpRequest", "braveSearch", "calculatorTool"}, (
            "available_tools must include every component_kind='tool' plugin "
            "minus the denylist (agentBuilder, masterSkill)."
        )

    async def test_available_tools_carry_metadata(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="inspect_canvas")
        registry = {
            "httpRequest": _node_class_full("tool", "HTTP Request", "Generic description", "LLM-facing description"),
        }

        with patch.object(ab, "registered_node_classes", return_value=registry):
            result = await node.inspect_canvas(ctx, params)

        entry = next(t for t in result.available_tools if t["type"] == "httpRequest")
        assert entry["display_name"] == "HTTP Request"
        # tool_description wins over description so the LLM sees the
        # spawnable-from-this-tool variant.
        assert entry["description"] == "LLM-facing description"

    async def test_inspect_canvas_includes_available_agents(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="inspect_canvas")
        registry = {
            "coding_agent": _node_class_full("agent", "Coding Agent", "Writes code"),
            "web_agent": _node_class_full("agent", "Web Agent", "Browses the web"),
            "httpRequest": _node_class_full("tool", "HTTP Request"),  # NOT an agent
        }

        with patch.object(ab, "registered_node_classes", return_value=registry):
            result = await node.inspect_canvas(ctx, params)

        types = {a["type"] for a in result.available_agents}
        assert types == {"coding_agent", "web_agent"}

    async def test_dual_purpose_plugins_in_available_tools(self):
        """Plugins like twitterSearch / googleGmail / pythonExecutor are
        ``component_kind='square'`` ActionNodes with ``usable_as_tool=True``.
        They form the bulk of useful LLM-callable tools; the catalogue
        MUST include them, not just the ~7 pure ToolNodes."""
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="inspect_canvas")
        registry = {
            "calculatorTool": _node_class_full("tool", "Calculator"),  # pure ToolNode
            "twitterSearch": _node_class_full(
                "square", "Twitter Search", usable_as_tool=True, group=("social", "tool")
            ),
            "googleGmail": _node_class_full(
                "square", "Gmail", usable_as_tool=True, group=("google", "tool")
            ),
            "pythonExecutor": _node_class_full(
                "square", "Python Executor", usable_as_tool=True, group=("code", "tool")
            ),
            "openaiChatModel": _node_class_full(
                "model", "OpenAI", usable_as_tool=True, group=("model", "tool")
            ),  # model — must be EXCLUDED
        }

        with patch.object(ab, "registered_node_classes", return_value=registry):
            result = await node.inspect_canvas(ctx, params)

        types = {t["type"] for t in (result.available_tools or [])}
        assert "calculatorTool" in types, "pure ToolNode plugins remain visible"
        assert "twitterSearch" in types, "dual-purpose plugins (usable_as_tool=True) must surface"
        assert "googleGmail" in types
        assert "pythonExecutor" in types
        assert "openaiChatModel" not in types, (
            "chat-model plugins must be excluded even when usable_as_tool=True "
            "— they're configured via input-model, not spawned as agent tools."
        )

    async def test_disabled_groups_excluded_from_available_tools(self):
        """``disabled_groups`` is a coarse blocklist — every plugin
        whose ``group`` tuple contains a disabled group name is hidden
        from the LLM's spawnable set. Mirrors the UI palette filter."""
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="inspect_canvas")
        registry = {
            "twitterSearch": _node_class_full(
                "square", "Twitter Search", usable_as_tool=True, group=("social", "tool")
            ),
            "emailSend": _node_class_full(
                "square", "Email Send", usable_as_tool=True, group=("email", "tool")
            ),  # email group disabled
            "wifiAutomation": _node_class_full(
                "square", "WiFi", usable_as_tool=True, group=("android", "service")
            ),  # android group disabled
        }
        from services import node_allowlist as nal

        def patched_get_config(self):
            return {
                "show_all": True,
                "enabled_nodes": [],
                "disabled_groups": ["android", "email"],
                "disabled_nodes": [],
                "disabled_credential_categories": [],
                "disabled_skill_folders": [],
            }

        with (
            patch.object(ab, "registered_node_classes", return_value=registry),
            patch.object(nal.NodeAllowlistService, "get_config", patched_get_config),
        ):
            result = await node.inspect_canvas(ctx, params)

        types = {t["type"] for t in (result.available_tools or [])}
        assert types == {"twitterSearch"}, (
            f"disabled_groups must exclude every plugin whose group tuple contains "
            f"a disabled group name (got {types})"
        )

    async def test_disabled_nodes_excluded_from_available_tools(self):
        """node_allowlist.json's ``disabled_nodes`` blocklist is the
        single source of truth for "what the LLM cannot spawn".
        Operators add an entry once and both the UI palette and the
        agentBuilder catalogue honor it (replaces the older
        per-callsite ``_DENIED_TOOL_TYPES``-only filter)."""
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="inspect_canvas")
        registry = {
            "httpRequest": _node_class_full("tool", "HTTP Request"),
            "taskManager": _node_class_full("tool", "Task Manager"),  # disabled
        }
        # Mock the allowlist to mark taskManager as disabled.
        from services import node_allowlist as nal

        original_get_config = nal.NodeAllowlistService.get_config

        def patched_get_config(self):
            return {
                "show_all": True,
                "enabled_nodes": [],
                "disabled_groups": [],
                "disabled_nodes": ["taskManager"],
                "disabled_credential_categories": [],
                "disabled_skill_folders": [],
            }

        with (
            patch.object(ab, "registered_node_classes", return_value=registry),
            patch.object(nal.NodeAllowlistService, "get_config", patched_get_config),
        ):
            result = await node.inspect_canvas(ctx, params)

        types = {t["type"] for t in (result.available_tools or [])}
        assert "httpRequest" in types
        assert "taskManager" not in types, (
            "Disabled node types must be invisible to the LLM in inspect_canvas. "
            "If this fails, _allowed_tool_types isn't honoring the allowlist."
        )
        _ = original_get_config  # silence unused-var lint

    def test_task_manager_enabled_in_allowlist_json(self):
        """The Task Manager is visible to both the palette and Agent Builder."""
        from services.node_allowlist import get_node_allowlist_service

        config = get_node_allowlist_service().get_config()
        assert "taskManager" in config["enabled_nodes"]
        assert "taskManager" not in config["disabled_nodes"]
        assert "taskManager" in ab._allowed_tool_types()

    def test_android_nodes_enabled_in_allowlist_json(self):
        """Android services bind directly to agents as ordinary tools."""
        from constants import ANDROID_SERVICE_NODE_TYPES
        from services.node_allowlist import get_node_allowlist_service

        config = get_node_allowlist_service().get_config()
        android_types = set(ANDROID_SERVICE_NODE_TYPES)
        assert android_types <= set(config["enabled_nodes"])
        assert "android_agent" in config["enabled_nodes"]
        assert "androidTool" not in config["enabled_nodes"]
        assert "android" not in config["disabled_groups"]
        assert "android_agent" not in config["disabled_nodes"]
        assert "android" not in config["disabled_credential_categories"]
        assert "android_agent" not in config["disabled_skill_folders"]
        assert android_types <= ab._allowed_tool_types()
        assert "android_agent" in ab._allowed_subagent_types()

    async def test_inspect_canvas_includes_available_skills(self):
        """available_skills must use the live SkillLoader registry. We
        don't mock the loader — agentBuilder reads it via the singleton
        so this is a smoke test that the integration works."""
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="inspect_canvas")

        with patch.object(ab, "registered_node_classes", return_value={}):
            result = await node.inspect_canvas(ctx, params)

        # The SkillLoader populates 60+ skills under server/skills/.
        assert result.available_skills is not None
        assert len(result.available_skills) > 0, (
            "available_skills must surface the SkillLoader registry; the "
            "LLM can't pick a skill folder otherwise."
        )
        # Each entry has folder + name + description.
        for entry in result.available_skills:
            assert "folder" in entry
            assert "name" in entry
            assert "description" in entry


# ============================================================================
# Duplicate detection — mutation ops are idempotent. Already-wired
# nodes / already-enabled skills return success with empty operations
# so the LLM doesn't loop trying again.
# ============================================================================


class TestDbPersistence:
    """agentBuilder must persist canvas mutations to the DB via
    ``database.save_workflow`` so that:

    1. The next chat-message trigger (next workflow run) sees the
       updated workflow.data instead of the stale snapshot. Without
       this, the LLM keeps re-spawning the same tools it already
       added in a prior run.
    2. ``_find_wired_types`` duplicate detection works across runs
       because the persisted ``edges`` reflect the new wiring.
    """

    async def test_add_tool_persists_via_save_workflow(self):
        node = ab.AgentBuilderNode()
        edges = [_edge("ab-1", "agent-1", "input-tools")]
        ctx = _make_ctx(edges=edges, workflow_id="wf-test")
        params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")

        # Pre-mutation workflow snapshot.
        existing_workflow = SimpleNamespace(
            name="Test Workflow",
            slug="Test_Workflow_1",
            description="",
            data={"nodes": [{"id": "agent-1", "type": "aiAgent"}], "edges": []},
        )
        mock_db = MagicMock()
        mock_db.get_workflow = AsyncMock(return_value=existing_workflow)
        mock_db.save_workflow = AsyncMock(return_value=True)
        mock_container = MagicMock()
        mock_container.database.return_value = mock_db

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool")),
            patch("core.container.container", mock_container),
        ):
            result = await node.add_tool(ctx, params)

        # Tool was added (not duplicate) — broadcast + persist path fired.
        assert len(result.operations) == 2
        # save_workflow must be called with the mutated data dict containing
        # the new node + edge.
        mock_db.save_workflow.assert_awaited_once()
        save_kwargs = mock_db.save_workflow.await_args.kwargs
        new_data = save_kwargs["data"]
        new_nodes = new_data["nodes"]
        new_edges = new_data["edges"]
        assert any(n.get("type") == "httpRequest" for n in new_nodes), (
            "Persisted nodes must include the spawned httpRequest tool."
        )
        assert len(new_edges) == 1, "Persisted edges must include the new input-tools edge."
        # The persisted node id must match the minted_id surfaced on the
        # broadcast op so BE and FE views stay aligned across reload.
        add_node_op = result.operations[0]
        persisted_ids = {n.get("id") for n in new_nodes}
        assert add_node_op["minted_id"] in persisted_ids


class TestInRunCanvasReload:
    """Within a single AgentWorkflow run, ``ctx.nodes`` / ``ctx.edges``
    is frozen at MachinaWorkflow start — so the SECOND agentBuilder
    call inside the run wouldn't see the FIRST call's mutation just
    by reading ``ctx``. Each operation now reloads ``workflow.data``
    from the DB via ``_load_live_canvas`` so duplicate detection
    catches in-run repeats, not just cross-run repeats."""

    async def test_add_tool_uses_db_reload_for_duplicate_check(self):
        """ctx has empty edges (simulating frozen-at-run-start
        snapshot) but the DB has the httpRequest already wired
        (simulating an earlier-in-run add_tool that persisted). The
        reload must see the DB state and return idempotent success
        instead of spawning a duplicate."""
        node = ab.AgentBuilderNode()
        # ctx looks like a fresh workflow — no tools wired anywhere.
        # Only the agentBuilder->agent input-tools edge that
        # _resolve_caller needs.
        ctx = _make_ctx(
            nodes=[
                _agent_node("agent-1"),
                {"id": "ab-1", "type": "agentBuilder", "data": {"label": "AB"}},
            ],
            edges=[_edge("ab-1", "agent-1", "input-tools")],
            workflow_id="wf-test",
        )
        params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")

        # DB reflects the prior in-run add_tool mutation: httpRequest
        # node spawned + edge to agent-1.
        live_workflow = SimpleNamespace(
            name="Test",
            slug="Test_1",
            description="",
            data={
                "nodes": [
                    {"id": "agent-1", "type": "aiAgent"},
                    {"id": "ab-1", "type": "agentBuilder"},
                    {"id": "http-existing", "type": "httpRequest"},
                ],
                "edges": [
                    {"source": "ab-1", "target": "agent-1", "targetHandle": "input-tools"},
                    {"source": "http-existing", "target": "agent-1", "targetHandle": "input-tools"},
                ],
            },
        )
        mock_db = MagicMock()
        mock_db.get_workflow = AsyncMock(return_value=live_workflow)
        mock_db.save_workflow = AsyncMock(return_value=True)
        mock_container = MagicMock()
        mock_container.database.return_value = mock_db

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool")),
            patch("core.container.container", mock_container),
        ):
            result = await node.add_tool(ctx, params)

        # Duplicate detected via DB reload — not via ctx.edges (which
        # didn't have httpRequest).
        assert result.operations == []
        assert "already wired" in result.summary
        assert "http-existing" in result.summary
        # save_workflow must NOT be called on the idempotent path —
        # no mutation to persist.
        mock_db.save_workflow.assert_not_called()


class TestDuplicateDetection:
    async def test_add_tool_is_idempotent_when_already_wired(self):
        """If the caller already has a node of the requested type wired
        via input-tools, ``add_tool`` returns empty operations + an
        "already wired" summary instead of spawning a duplicate."""
        node = ab.AgentBuilderNode()
        nodes = [
            _agent_node("agent-1"),
            {"id": "ab-1", "type": "agentBuilder", "data": {"label": "AB"}},
            {"id": "http-existing", "type": "httpRequest", "data": {"label": "HTTP"}},
        ]
        edges = [
            _edge("ab-1", "agent-1", "input-tools"),
            _edge("http-existing", "agent-1", "input-tools"),
        ]
        ctx = _make_ctx(nodes=nodes, edges=edges)
        params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool")),
            patch.object(ab, "_broadcast", new_callable=AsyncMock) as mock_bcast,
        ):
            result = await node.add_tool(ctx, params)

        assert result.operations == []
        assert "already wired" in result.summary
        assert "http-existing" in result.summary  # existing node id surfaced for the LLM
        mock_bcast.assert_not_awaited()  # no canvas mutation fires

    async def test_add_tool_still_spawns_when_different_type_wired(self):
        """No false positives: if the caller has DIFFERENT tools wired,
        spawning a fresh type still proceeds normally."""
        node = ab.AgentBuilderNode()
        nodes = [
            _agent_node("agent-1"),
            {"id": "ab-1", "type": "agentBuilder", "data": {"label": "AB"}},
            {"id": "calc-1", "type": "calculatorTool", "data": {"label": "Calc"}},
        ]
        edges = [
            _edge("ab-1", "agent-1", "input-tools"),
            _edge("calc-1", "agent-1", "input-tools"),
        ]
        ctx = _make_ctx(nodes=nodes, edges=edges)
        params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(httpRequest="tool", calculatorTool="tool")),
            patch.object(ab, "_broadcast", new_callable=AsyncMock) as mock_bcast,
        ):
            result = await node.add_tool(ctx, params)

        assert len(result.operations) == 2  # spawn + edge
        assert result.operations[0]["node_type"] == "httpRequest"
        mock_bcast.assert_awaited_once()

    async def test_add_subagent_is_idempotent_when_already_wired(self):
        node = ab.AgentBuilderNode()
        nodes = [
            {"id": "lead-1", "type": "orchestrator_agent", "data": {"label": "Lead"}},
            {"id": "ab-1", "type": "agentBuilder", "data": {"label": "AB"}},
            {"id": "coder-existing", "type": "coding_agent", "data": {"label": "Coder"}},
        ]
        edges = [
            _edge("ab-1", "lead-1", "input-tools"),
            _edge("coder-existing", "lead-1", "input-teammates"),
        ]
        ctx = _make_ctx(nodes=nodes, edges=edges)
        params = ab.AgentBuilderParams(operation="add_subagent", agent_type="coding_agent")

        with (
            patch.object(ab, "registered_node_classes", return_value=_registry(coding_agent="agent")),
            patch.object(ab, "_broadcast", new_callable=AsyncMock) as mock_bcast,
        ):
            result = await node.add_subagent(ctx, params)

        assert result.operations == []
        assert "already wired" in result.summary
        assert "coder-existing" in result.summary
        mock_bcast.assert_not_awaited()

    async def test_add_skill_is_idempotent_when_already_enabled(self):
        """When the masterSkill is already wired AND the requested skill
        is already ``enabled=True`` in its skills_config, ``add_skill``
        returns empty operations + an "already enabled" summary."""
        node = ab.AgentBuilderNode()
        master_skill_node = {
            "id": "ms-1",
            "type": "masterSkill",
            "data": {
                "label": "Master Skill",
                "parameters": {
                    "skills_config": {
                        "http-request-skill": {
                            "enabled": True,
                            "instructions": "",
                            "isCustomized": False,
                        },
                    },
                },
            },
        }
        nodes = [
            _agent_node("agent-1"),
            {"id": "ab-1", "type": "agentBuilder", "data": {"label": "AB"}},
            master_skill_node,
        ]
        edges = [
            _edge("ab-1", "agent-1", "input-tools"),
            _edge("ms-1", "agent-1", "input-skill"),
        ]
        ctx = _make_ctx(nodes=nodes, edges=edges)
        params = ab.AgentBuilderParams(operation="add_skill", skill_folder="http-request-skill")

        with (
            patch.object(ab, "_skill_folder_exists", return_value=True),
            patch.object(ab, "_broadcast", new_callable=AsyncMock) as mock_bcast,
        ):
            result = await node.add_skill(ctx, params)

        assert result.operations == []
        assert "already enabled" in result.summary
        assert "ms-1" in result.summary
        mock_bcast.assert_not_awaited()
