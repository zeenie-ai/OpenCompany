from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from services.plugin.edge_walker import collect_teammate_connections


class _Params(BaseModel):
    pass


class _Node:
    Params = _Params
    credentials = ()


def node(node_id, node_type, label=None):
    return {"id": node_id, "type": node_type, "data": {"label": label} if label is not None else {}}


def edge(source, target, handle="input-teammates", *, legacy=False):
    result = {"source": source, "target": target}
    result["target_handle" if legacy else "targetHandle"] = handle
    return result


def patch_types(monkeypatch, nodes):
    known = {item["type"] for item in nodes}
    monkeypatch.setattr("services.workflow_validator.get_node_class", lambda value: _Node if value in known else None)


@pytest.mark.asyncio
async def test_custom_agents_are_label_addressable_and_collision_safe():
    nodes = [node("lead", "orchestrator_agent"), node("custom-a", "aiAgent", "Research"), node("custom-b", "aiAgent", "Research")]
    context = {"nodes": nodes, "edges": [edge("custom-a", "lead"), edge("custom-b", "lead", legacy=True)]}
    database = AsyncMock()
    database.get_node_parameters.return_value = {}

    teammates = await collect_teammate_connections("lead", context, database)

    names = [item["delegate_tool_name"] for item in teammates]
    assert len(set(names)) == 2
    assert all(name.startswith("delegate_to_research_") for name in names)
    # Stable across repeated expansion (required for durable dispatch IDs).
    again = await collect_teammate_connections("lead", context, database)
    assert [item["delegate_tool_name"] for item in again] == names


@pytest.mark.asyncio
async def test_specialized_delegate_name_is_stable_and_capabilities_are_collected():
    nodes = [node("lead", "ai_employee"), node("coder", "coding_agent", "Backend"), node("search", "duckduckgoSearch", "Search")]
    context = {"nodes": nodes, "edges": [edge("coder", "lead"), edge("search", "coder", "input-tools")]}
    database = AsyncMock()
    database.get_node_parameters.return_value = {}

    [teammate] = await collect_teammate_connections("lead", context, database)

    assert teammate["delegate_tool_name"] == "delegate_to_coding_agent"
    assert teammate["child_tools"] == [{"node_id": "search", "node_type": "duckduckgoSearch", "label": "Search"}]


@pytest.mark.asyncio
async def test_duplicate_specialized_type_is_blocking(monkeypatch):
    from services.workflow_validator import validate_workflow

    nodes = [node("lead", "orchestrator_agent"), node("a", "coding_agent"), node("b", "coding_agent")]
    patch_types(monkeypatch, nodes)
    report = await validate_workflow(nodes, [edge("a", "lead"), edge("b", "lead")])
    issue = next(item for item in report["errors"] if item["code"] == "DUPLICATE_TEAMMATE_TYPE")
    assert issue["nodes"] == ["a", "b"]


@pytest.mark.asyncio
async def test_ai_agent_type_may_repeat(monkeypatch):
    from services.workflow_validator import validate_workflow

    nodes = [node("lead", "orchestrator_agent"), node("a", "aiAgent", "One"), node("b", "aiAgent", "Two")]
    patch_types(monkeypatch, nodes)
    report = await validate_workflow(nodes, [edge("a", "lead"), edge("b", "lead")])
    assert not [item for item in report["errors"] if item["code"].startswith("DUPLICATE_")]


@pytest.mark.asyncio
async def test_invalid_team_endpoint_cycle_and_depth_codes(monkeypatch):
    from services.workflow_validator import validate_workflow

    nodes = [
        node("lead-a", "orchestrator_agent"), node("lead-b", "ai_employee"),
        node("lead-c", "orchestrator_agent"), node("lead-d", "ai_employee"),
        node("worker", "coding_agent"), node("tool", "duckduckgoSearch"),
    ]
    patch_types(monkeypatch, nodes)
    edges = [
        edge("lead-b", "lead-a"), edge("lead-a", "lead-b"),
        edge("lead-c", "lead-b"), edge("lead-d", "lead-c"), edge("worker", "lead-d"),
        edge("tool", "worker"),
    ]
    report = await validate_workflow(nodes, edges)
    codes = {item["code"] for item in report["errors"]}
    assert {"INVALID_TEAM_EDGE", "TEAM_DELEGATION_CYCLE", "TEAM_DEPTH_EXCEEDED"} <= codes


def test_import_remap_normalizes_legacy_handle():
    from services.workflow_import import remap_node_ids

    nodes = [node("worker", "aiAgent"), node("lead", "orchestrator_agent")]
    _, [remapped], _ = remap_node_ids(nodes, [edge("worker", "lead", legacy=True)])
    assert remapped["targetHandle"] == "input-teammates"
    assert "target_handle" not in remapped
