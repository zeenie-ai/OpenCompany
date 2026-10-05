"""Agent Builder changes are transactional (services/workflow_storage/mutate.py):
distinct calls both survive, a retried call replays its first result, and
the ledger key is the runtime's tool-call id."""

from __future__ import annotations

import asyncio
import pytest

from nodes.tool import agent_builder as ab
from services.workflow_storage import mutate
from tests.nodes._agent_builder_harness import (
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


async def test_distinct_calls_both_survive(builder, database):
    await save_graph(database, agents_graph())

    await call("add_tool", ctx(call="call-1"), node_type="httpRequest")
    await call("add_tool", ctx(call="call-2"), node_type="calculatorTool")

    graph = await saved(database)
    assert {edge["source"] for edge in edges_into(graph, "7:aiAgent:1", "input-tools")} == {
        "7:agentBuilder:1",
        "7:httpRequest:1",
        "7:calculatorTool:1",
    }
    assert await database.get_node_parameters("7:calculatorTool:1") == {}
    assert len(builder.frames) == 2


async def test_asking_twice_in_one_run_adds_one(builder, database):
    """The second call finds the first one's node in the saved graph."""
    await save_graph(database, agents_graph())

    await call("add_tool", ctx(call="call-1"), node_type="httpRequest")
    second = await call("add_tool", ctx(call="call-2"), node_type="httpRequest")

    graph = await saved(database)
    assert [n["id"] for n in graph["nodes"] if n["type"] == "httpRequest"] == ["7:httpRequest:1"]
    assert [op["minted_id"] for op in second.operations] == ["7:httpRequest:1"]
    assert len(builder.frames) == 1


async def test_concurrent_same_type_calls_reuse_inside_transaction(builder, database, monkeypatch):
    await save_graph(database, agents_graph())
    original = ab._load_canvas
    ready = asyncio.Event()
    readers = 0

    async def simultaneous_read(*args):
        nonlocal readers
        canvas = await original(*args)
        readers += 1
        if readers == 2:
            ready.set()
        await ready.wait()
        return canvas

    monkeypatch.setattr(ab, "_load_canvas", simultaneous_read)
    results = await asyncio.gather(
        call("add_tool", ctx(call="concurrent-1"), node_type="httpRequest"),
        call("add_tool", ctx(call="concurrent-2"), node_type="httpRequest"),
    )
    graph = await saved(database)
    assert len([n for n in graph["nodes"] if n["type"] == "httpRequest"]) == 1
    assert len([edge for edge in edges_into(graph, "7:aiAgent:1", "input-tools") if "httpRequest" in edge["source"]]) == 1
    assert all("httpRequest:1" in result.operations[0]["minted_id"] for result in results)


async def test_a_retried_teammate_call_adds_one_teammate(builder, database):
    """aiAgent teammates are repeatable, so only the ledger stops a retry of
    the same call from adding a second one."""
    await save_graph(
        database,
        {"nodes": [node("7:orchestrator_agent:1", "orchestrator_agent", "Lead")], "edges": []},
    )

    first = await call("add_subagent", ctx("7:orchestrator_agent:1", call="call-5"), agent_type="aiAgent")
    again = await call("add_subagent", ctx("7:orchestrator_agent:1", call="call-5"), agent_type="aiAgent")

    graph = await saved(database)
    assert [n["id"] for n in graph["nodes"] if n["type"] == "aiAgent"] == ["7:aiAgent:1"]
    assert again.operations == first.operations
    assert "Added" in again.summary


def test_the_ledger_key_is_the_tool_call_scoped_to_the_run():
    params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")

    assert ab._mutation_id(ctx(call="call-1", execution="run-1"), params, "7:aiAgent:1") == "agent-builder:run-1:call-1"
    # Positional fallback ids ("1:1") repeat across runs; the run keeps them apart.
    assert ab._mutation_id(ctx(call="1:1", execution="run-2"), params, "7:aiAgent:1") != ab._mutation_id(
        ctx(call="1:1", execution="run-3"), params, "7:aiAgent:1"
    )


def test_without_a_call_id_the_ledger_key_is_the_request():
    params = ab.AgentBuilderParams(operation="add_tool", node_type="httpRequest")
    other = ab.AgentBuilderParams(operation="add_tool", node_type="calculatorTool")

    key = ab._mutation_id(ctx(call=""), params, "7:aiAgent:1")
    assert key == ab._mutation_id(ctx(call=""), params, "7:aiAgent:1")
    assert key.startswith("agent-builder:")
    assert key != ab._mutation_id(ctx(call=""), other, "7:aiAgent:1")
    assert key != ab._mutation_id(ctx(call=""), params, "7:aiAgent:2")


async def test_a_change_that_no_longer_fits_is_refused_plainly(builder, database, monkeypatch):
    await save_graph(database, agents_graph())

    async def no_longer_fits(*_args, **_kwargs):
        raise ValueError("'7:aiAgent:1' is neither a ref in the batch nor a node in the graph")

    monkeypatch.setattr(mutate, "apply_graph_additions", no_longer_fits)

    result = await call("add_tool", ctx(), node_type="httpRequest")

    assert result.operations == []
    assert result.summary == "The workflow changed while I was adding to it. Try again."


@pytest.mark.parametrize("operation,fields", [("add_tool", {"node_type": "httpRequest"}), ("add_skill", {"skill_name": "memory-skill"})])
async def test_a_workflow_deleted_meanwhile_is_refused(builder, database, monkeypatch, operation, fields):
    await save_graph(database, agents_graph())

    async def gone(*_args, **_kwargs):
        return None

    monkeypatch.setattr(mutate, "apply_graph_additions", gone)

    result = await call(operation, ctx(), **fields)

    assert result.summary == "Save the workflow first, then ask again."
