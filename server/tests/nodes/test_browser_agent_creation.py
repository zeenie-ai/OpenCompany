"""Agent Builder shares the atomic Browser capability recipe."""
import pytest

from tests.nodes._agent_builder_harness import (
    builder_fixture, database_fixture,  # noqa: F401 - registers the pytest fixtures
    call, ctx, node, save_graph, saved,
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


async def test_builder_refuses_browser_on_a_coding_agent(builder, database):
    lead, coder = "7:orchestrator_agent:1", "7:coding_agent:1"
    graph = {"nodes": [node(lead, "orchestrator_agent", "Lead"), node(coder, "coding_agent", "Coder"),
                       node("7:agentBuilder:1", "agentBuilder", "Builder")],
             "edges": [tool_edge("7:agentBuilder:1", lead).to_dict()]}
    await save_graph(database, graph)
    create = await call("add_subagent", ctx(lead), agent_type="coding_agent", purpose="Build a website", tool_types=["browser"])
    attach = await call("add_tool", ctx(coder), node_type="browser")
    assert all(not result.operations and "Browser AI Agent" in result.summary for result in (create, attach))
    assert await saved(database) == graph


async def test_saved_coding_browser_edge_is_ignored_at_admission_and_blocked_at_dispatch(database, monkeypatch):
    from nodes.browser.browser import BrowserToolInput, _config
    from services.agent_bindings import rebind_allowed
    from services.browser_resources import BrowserAgentRequired, validate_new_browser_connections
    from services.plugin import NodeUserError
    from services.plugin.context import NodeContext
    from services.plugin.edge_walker import collect_agent_connections

    browser, coder, specialist = "7:browser:1", "7:coding_agent:1", "7:browser_agent:1"
    graph = {"nodes": [node(browser, "browser", "Browser"), node(coder, "coding_agent", "Coder"),
                       node(specialist, "browser_agent", "Browser agent")],
             "edges": [tool_edge(browser, owner).to_dict() for owner in (coder, specialist)]}
    with pytest.raises(BrowserAgentRequired):
        validate_new_browser_connections(graph)
    validate_new_browser_connections(graph, graph)
    await save_graph(database, graph, {browser: {}})
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: database)
    tool_call = {"type": "add_node", "node_type": "browser", "minted_id": browser}
    for owner in (coder, specialist):
        _, _, tools, _, _ = await collect_agent_connections(owner, {**graph, "workflow_id": "7"}, database)
        assert [tool["node_type"] for tool in tools] == (["browser"] if owner == specialist else [])
        assert rebind_allowed(tool_call, owner, **graph) is (owner == specialist)
        context = NodeContext.from_legacy(browser, "browser", {"workflow_id": "7", "parent_node_id": owner, "tool_args": {}})
        if owner == specialist:
            await _config(context, BrowserToolInput(operation="snapshot"))
        else:
            with pytest.raises(NodeUserError, match="Browser AI Agent"):
                await _config(context, BrowserToolInput(operation="snapshot"))
