"""The public creation boundary owns identity and the runtime birth policy."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from routers import agents, browser_agents
from services.coding_agent_recipe import default_pi_tool_types
from tests.nodes._agent_builder_harness import (  # noqa: F401 -- pytest registers imported fixtures
    builder_fixture, database_fixture, save_graph, saved,
)


def request(owner="owner"):
    return SimpleNamespace(state=SimpleNamespace(user_id=owner))


@pytest.mark.asyncio
async def test_editor_enrolls_a_manager_and_its_selected_worker_atomically(database, creation, monkeypatch):
    from routers import websocket
    from services.employees import store
    from services.employees.team_runtime import admit_team_plan
    from services.workflow_sanitizer import sanitize_workflow_graph
    from tests.services.employees.test_team_plan_admission import hierarchical_inbox_assistant

    built = hierarchical_inbox_assistant()
    await save_graph(database, sanitize_workflow_graph({"nodes": built.nodes, "edges": built.edges, "owner_id": "owner"}), built.parameters)
    employee, _ = await store.reserve(database, owner_id="owner", idempotency_key="review", payload_hash="case",
                                      fields={"role": "Assistant", "team_plan": built.team_plan})
    await store.mark_ready(database, employee.id, workflow_id="7", node_roles=built.node_roles)
    manager = await agents.create_agent(agents.CreateAgent(workflow_id="7", node_type="ai_employee", mutation_id=uuid4()), request())
    parent = manager["node_ids"]["agent"]
    body = agents.CreateAgent(workflow_id="7", node_type="aiAgent", mutation_id=uuid4(), parent_node_id=parent)
    first, retry = await agents.create_agent(body, request()), await agents.create_agent(body, request())
    assert first["applied"] and not retry["applied"] and first["node_ids"] == retry["node_ids"]
    graph = await saved(database)
    admitted = await admit_team_plan(database, "7", graph["nodes"], graph["edges"])
    plan = next(n["data"]["employee_team_plan"] for n in admitted if n["id"] == built.team_plan["talk_node_id"])
    members = {m["node_id"]: m for m in plan["members"]}
    assert members[parent]["parent_node_id"] == plan["orchestrator_node_id"]
    assert members[first["node_ids"]["agent"]]["parent_node_id"] == parent
    monkeypatch.setattr(websocket, "container", SimpleNamespace(database=lambda: database))
    report = await websocket._validate_workflow_draft({**graph, "workflow_id": "7"}, request())
    assert report["errors"] == []


@pytest.fixture
def creation(database, builder, monkeypatch):
    monkeypatch.setattr(agents, "container", SimpleNamespace(database=lambda: database))
    monkeypatch.setattr(browser_agents, "container", SimpleNamespace(database=lambda: database))
    monkeypatch.setenv("NEW_AGENT_RUNTIME_V1_ENABLED", "true")
    return builder


@pytest.mark.asyncio
async def test_fresh_creation_is_atomic_and_retry_returns_same_ids(database, creation):
    await save_graph(database, {"nodes": [], "edges": [], "owner_id": "owner"})
    body = agents.CreateAgent(workflow_id="7", node_type="aiAgent", mutation_id=uuid4(),
        parameters={"runtime_behavior_version": 0, "model": "gemini-2.5-flash"})
    first, retry = await agents.create_agent(body, request()), await agents.create_agent(body, request())
    assert first["applied"] is True
    assert retry["applied"] is False
    assert retry["node_ids"] == first["node_ids"]
    graph = await saved(database)
    assert {item["id"] for item in graph["nodes"]} == set(first["node_ids"].values())
    # Receipts replay the same announcement if the original response was lost.
    assert len(creation.frames) == 2
    assert creation.frames[0] == creation.frames[1]
    params = await database.get_node_parameters(first["node_ids"]["agent"])
    assert params["runtime_behavior_version"] == 1
    assert params["model"] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_gate_off_never_accepts_client_upgrade(database, creation, monkeypatch):
    monkeypatch.setenv("NEW_AGENT_RUNTIME_V1_ENABLED", "false")
    await save_graph(database, {"nodes": [], "edges": [], "owner_id": "owner"})
    result = await agents.create_agent(agents.CreateAgent(workflow_id="7", node_type="aiAgent",
        mutation_id=uuid4(), parameters={"runtime_behavior_version": 1}), request())
    assert "runtime_behavior_version" not in await database.get_node_parameters(result["node_ids"]["agent"])


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_v1", ["false", "true"])
async def test_coding_agent_has_connected_pi_tools_and_idempotent_creation(database, creation, monkeypatch, runtime_v1):
    monkeypatch.setenv("NEW_AGENT_RUNTIME_V1_ENABLED", runtime_v1)
    await save_graph(database, {"nodes": [], "edges": [], "owner_id": "owner"})
    body = agents.CreateAgent(workflow_id="7", node_type="coding_agent", mutation_id=uuid4(),
        position=(400, 300), parameters={"model": "gemini-2.5-flash"})
    first = await agents.create_agent(body, request())
    retry = await agents.create_agent(body, request())
    assert first["applied"] and not retry["applied"]
    assert retry["node_ids"] == first["node_ids"]
    graph = await saved(database)
    by_id = {node["id"]: node for node in graph["nodes"]}
    agent_id = first["node_ids"]["agent"]
    tool_edges = [edge for edge in graph["edges"] if edge["target"] == agent_id and edge["targetHandle"] == "input-tools"]
    assert {by_id[edge["source"]]["type"] for edge in tool_edges} == set(default_pi_tool_types())
    assert all(edge["sourceHandle"] == "output-tool" for edge in tool_edges)
    assert len(tool_edges) == len(default_pi_tool_types())
    assert len(by_id) == len(default_pi_tool_types()) + 2
    assert any(edge["target"] == agent_id and edge["targetHandle"] == "input-context" for edge in graph["edges"])
    assert (await database.get_node_parameters(agent_id))["model"] == "gemini-2.5-flash"


@pytest.mark.asyncio
async def test_editor_ai_employee_has_one_persisted_task_manager_after_a_retry(database, creation):
    await save_graph(database, {"nodes": [], "edges": [], "owner_id": "owner"})
    body = agents.CreateAgent(workflow_id="7", node_type="ai_employee", mutation_id=uuid4())
    first, retry = await agents.create_agent(body, request()), await agents.create_agent(body, request())
    assert first["node_ids"] == retry["node_ids"] and first["applied"] and not retry["applied"]
    graph = await saved(database)
    lead, manager = first["node_ids"]["agent"], first["node_ids"]["task_manager"]
    assert [n["id"] for n in graph["nodes"] if n["type"] == "taskManager"] == [manager]
    assert [(e["sourceHandle"], e["targetHandle"], e["target"]) for e in graph["edges"]
            if e["source"] == manager] == [("output-tool", "input-tools", lead)]
    skills = first["node_ids"]["lead_skills"]
    assert [(e["sourceHandle"], e["targetHandle"], e["target"]) for e in graph["edges"]
            if e["source"] == skills] == [("output-tool", "input-skill", lead)]
    assert "team-delegation" in (await database.get_node_parameters(skills))["skills_config"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("agent_type", "tool_type", "label"), [
    ("coding_agent", "piBash", "Pi Bash"), ("ai_employee", "taskManager", "Task Manager"),
])
async def test_disabled_tool_rejects_whole_agent_bundle(database, creation, monkeypatch, agent_type, tool_type, label):
    from services import node_allowlist
    monkeypatch.setattr(node_allowlist, "get_node_allowlist_service", lambda: SimpleNamespace(get_config=lambda: {
        "disabled_nodes": [tool_type], "disabled_groups": [],
    }))
    await save_graph(database, {"nodes": [], "edges": [], "owner_id": "owner"})
    with pytest.raises(HTTPException) as caught:
        await agents.create_agent(agents.CreateAgent(workflow_id="7", node_type=agent_type, mutation_id=uuid4()), request())
    assert caught.value.status_code == 403
    assert label in caught.value.detail
    assert (await saved(database))["nodes"] == []
    assert creation.frames == []


@pytest.mark.asyncio
async def test_receipt_never_bypasses_owner_check(database, creation):
    graph = {"nodes": [], "edges": [], "owner_id": "owner"}
    await save_graph(database, graph)
    body = agents.CreateAgent(workflow_id="7", node_type="aiAgent", mutation_id=uuid4())
    await agents.create_agent(body, request())
    with pytest.raises(HTTPException) as caught:
        await agents.create_agent(body, request("stranger"))
    assert caught.value.status_code == 404
    assert len(creation.frames) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("bundle", [False, True])
async def test_ownerless_legacy_workflow_still_belongs_to_local_owner(database, creation, bundle):
    await save_graph(database, {"nodes": [], "edges": []})
    body = agents.CreateAgent(workflow_id="7", node_type="aiAgent", mutation_id=uuid4())
    with pytest.raises(HTTPException) as caught:
        if bundle:
            await browser_agents.create_browser_agent(browser_agents.CreateBrowserAgent(
                workflow_id="7", mutation_id=uuid4()), request("stranger"))
        else:
            await agents.create_agent(body, request("stranger"))
    assert caught.value.status_code == 404
    assert (await saved(database))["nodes"] == []
    assert creation.frames == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,initial", [("shell", {}), ("browser_agent", {}), ("aiAgent", {"temperature": "invalid"})])
async def test_rejected_creation_has_no_nodes_parameters_or_edges(database, creation, kind, initial):
    await save_graph(database, {"nodes": [], "edges": []})
    with pytest.raises(HTTPException) as caught:
        await agents.create_agent(agents.CreateAgent(workflow_id="7", node_type=kind,
            mutation_id=uuid4(), parameters=initial), request())
    assert caught.value.status_code == 400
    assert (await saved(database))["nodes"] == []
    assert (await saved(database))["edges"] == []
    assert creation.frames == []


@pytest.mark.asyncio
@pytest.mark.parametrize("original", [{}, {"runtime_behavior_version": 0}, {"runtime_behavior_version": 1}])
async def test_save_and_inplace_mutation_preserve_saved_identity(database, original):
    node_id = "7:aiAgent:1"
    assert await database.save_node_parameters(node_id, original)
    assert await database.save_node_parameters(node_id, {"runtime_behavior_version": 1 - original.get("runtime_behavior_version", 0), "model": "next"})
    params = await database.get_node_parameters(node_id)
    assert params.get("runtime_behavior_version") == original.get("runtime_behavior_version")
    def mutate(value):
        value["runtime_behavior_version"] = 1 - original.get("runtime_behavior_version", 0)
        value["model"] = "last"
        return value
    await database.mutate_node_parameters_atomic(node_id, mutate)
    params = await database.get_node_parameters(node_id)
    assert params.get("runtime_behavior_version") == original.get("runtime_behavior_version")
    assert params["model"] == "last"
