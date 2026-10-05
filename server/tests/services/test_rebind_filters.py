"""The hot rebind binds new tools, and nothing else: an Agent Builder batch
can carry a Skills node (masterSkill is tool-kind but feeds input-skill;
bound, it became a bogus ``tool_master_skill``), and a saved tool the run
may already hold."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from services import workflow_ops
from services.temporal.agent_activities import refresh_agent_tools


async def test_temporal_refresh_binds_the_tool_but_not_the_skills_node(monkeypatch):
    built = []

    async def build(tool_info):
        built.append(tool_info["node_type"])
        return SimpleNamespace(name=f"tool_{tool_info['node_type']}", definition=None, args_schema=None, description=""), {}

    monkeypatch.setattr("core.container.container", SimpleNamespace(ai_service=lambda: SimpleNamespace(_build_tool_from_node=build)))
    operations = [
        workflow_ops.add_node("skills", "masterSkill", {"skills_config": {}}, label="Skills", minted_id="7:masterSkill:1"),
        workflow_ops.add_node("tool", "calculatorTool", {}, label="Calculator", minted_id="7:calculatorTool:1"),
        workflow_ops.add_edge("7:calculatorTool:1", "7:aiAgent:1", source_handle="output-tool", target_handle="input-tools"),
    ]

    result = await refresh_agent_tools({"operations": operations})

    assert built == ["calculatorTool"]
    assert [tool["tool_node_id"] for tool in result["tools"]] == ["7:calculatorTool:1"]


def test_in_process_rebinds_skip_the_skills_node_and_bound_nodes():
    """Both agent paths rebind through their own closure (they capture
    different tool maps)."""
    from services.ai import AIService

    for method in (AIService.execute_agent, AIService.execute_chat_agent):
        source = inspect.getsource(method)
        start = source.index("async def _rebind_from_operations")
        rebind = source[start : source.index("return [tool for tool, _ in new_bindings]", start)]
        assert '.get("isMasterSkillEditor")' in rebind, method.__name__
        assert 'bound = {identity["node_id"] for identity in tool_identities}' in rebind, method.__name__
        assert 'if tool_info["node_id"] in bound:' in rebind, method.__name__


async def test_temporal_refresh_binds_delegate_once_and_skips_already_bound_nodes(monkeypatch):
    built = []
    async def build(tool_info):
        built.append(tool_info["node_id"])
        return SimpleNamespace(name="delegate_to_ai_agent", definition=None, args_schema=None, description=""), {}
    monkeypatch.setattr("core.container.container", SimpleNamespace(ai_service=lambda: SimpleNamespace(_build_tool_from_node=build)))
    operations = [workflow_ops.add_node("agent", "aiAgent", {}, label="Researcher", minted_id="7:aiAgent:2"),
                  workflow_ops.add_node("agent", "aiAgent", {}, label="Researcher", minted_id="7:aiAgent:2"),
                  workflow_ops.add_node("old", "aiAgent", {}, label="Existing", minted_id="7:aiAgent:3")]
    result = await refresh_agent_tools({"operations": operations, "bound_node_ids": ["7:aiAgent:3"], "agent_node_type": "ai_employee"})
    assert built == ["7:aiAgent:2"]
    assert result["tools"][0]["llm_hidden"] is True
