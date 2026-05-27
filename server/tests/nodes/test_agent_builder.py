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
        assert "Available on your next turn" in result.summary


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
    async def test_rejects_empty_name(self):
        node = ab.AgentBuilderNode()
        ctx = _make_ctx()
        params = ab.AgentBuilderParams(operation="create_workflow", workflow_name="")

        result = await node.create_workflow(ctx, params)

        assert result.workflow_id is None
        assert "workflow_name is required" in result.summary

    async def test_persists_via_database_and_returns_id(self):
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

    async def test_returns_failure_summary_when_persist_fails(self):
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
