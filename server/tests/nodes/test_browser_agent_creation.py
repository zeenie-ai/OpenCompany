"""Agent Builder shares the atomic Browser capability recipe."""
from tests.nodes._agent_builder_harness import (
    builder_fixture, database_fixture, call, ctx, node, save_graph, saved,
)
from services.graph_build import tool_edge


async def test_builder_creates_one_complete_browser_bundle_with_inherited_model(builder, database):
    lead = "7:orchestrator_agent:1"
    await save_graph(database, {"nodes": [node(lead, "orchestrator_agent", "Lead"), node("7:agentBuilder:1", "agentBuilder", "Builder")],
        "edges": [tool_edge("7:agentBuilder:1", lead).to_dict()]}, {lead: {"provider": "openai", "model": "saved-model"}})
    result = await call("add_subagent", ctx(lead), agent_type="browser_agent", purpose="Read fixture account")
    assert result.operations, result.summary
    graph = await saved(database)
    agent = next(node for node in graph["nodes"] if node["type"] == "browser_agent")
    assert (await database.get_node_parameters(agent["id"]))["model"] == "saved-model"
    assert "Read fixture account" in (await database.get_node_parameters(agent["id"]))["system_message"]
    assert {node["type"] for node in graph["nodes"]} >= {"browser", "visionAnalyze", "context", "masterSkill"}
    browser = next(node for node in graph["nodes"] if node["type"] == "browser")
    assert any(edge["source"] == browser["id"] and edge["target"] == agent["id"] and edge["targetHandle"] == "input-tools" for edge in graph["edges"])
    await call("add_subagent", ctx(lead), agent_type="browser_agent", purpose="Read fixture account")
    assert len((await saved(database))["nodes"]) == len(graph["nodes"])
    assert result.operations
