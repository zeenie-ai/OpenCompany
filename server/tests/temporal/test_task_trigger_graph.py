"""Regression tests for taskTrigger runtime data entering Temporal agents."""

import pytest
from types import SimpleNamespace

from services.temporal.workflow import MachinaWorkflow


def test_task_trigger_input_task_is_runtime_dependency() -> None:
    trigger = {"id": "task-trigger", "type": "taskTrigger", "_pre_executed": True}
    agent = {"id": "lead", "type": "orchestrator_agent"}
    edge = {
        "id": "task-to-lead",
        "source": "task-trigger",
        "target": "lead",
        "targetHandle": "input-task",
    }

    nodes, edges = MachinaWorkflow()._filter_executable_graph([trigger, agent], [edge])

    assert nodes == [trigger, agent]
    assert edges == [edge]
    dependencies, _ = MachinaWorkflow()._build_dependency_maps(nodes, edges)
    assert dependencies["lead"] == {"task-trigger"}


def test_non_trigger_input_task_remains_configuration() -> None:
    task_config = {"id": "task-config", "type": "taskManager"}
    agent = {"id": "lead", "type": "orchestrator_agent"}
    edge = {
        "id": "config-to-lead",
        "source": "task-config",
        "target": "lead",
        "targetHandle": "input-task",
    }

    nodes, edges = MachinaWorkflow()._filter_executable_graph([task_config, agent], [edge])

    assert nodes == [agent]
    assert edges == []


@pytest.mark.asyncio
async def test_agent_connection_collection_accepts_legacy_todo_handle() -> None:
    from services.plugin.edge_walker import collect_agent_connections

    class Database:
        async def get_node_parameters(self, _node_id):
            return {}

    nodes = [
        {"id": "lead", "type": "orchestrator_agent", "data": {}},
        {"id": "todos", "type": "writeTodos", "data": {"label": "Todos"}},
    ]
    edges = [{"source": "todos", "target": "lead", "target_handle": "input-tools"}]

    _memory, _skills, tools, _input, _task = await collect_agent_connections(
        "lead", {"nodes": nodes, "edges": edges, "workflow_id": "workflow-1"}, Database(),
    )

    assert any(tool["node_id"] == "todos" and tool["node_type"] == "writeTodos" for tool in tools)


@pytest.mark.asyncio
async def test_latest_graph_activity_returns_canonical_saved_tool_edge(monkeypatch) -> None:
    from core.container import container
    from services.temporal.activities import load_persisted_workflow_graph_activity

    saved = SimpleNamespace(data={
        "nodes": [
            {"id": "lead", "type": "orchestrator_agent"},
            {"id": "todos", "type": "writeTodos"},
        ],
        "edges": [{"source": "todos", "target": "lead", "target_handle": "input-tools"}],
    })

    class Database:
        async def get_workflow(self, workflow_id):
            assert workflow_id == "workflow-1"
            return saved

    monkeypatch.setattr(container, "database", lambda: Database())
    result = await load_persisted_workflow_graph_activity({"workflow_id": "workflow-1"})

    assert result["found"] is True
    assert result["edges"] == [{"source": "todos", "target": "lead", "targetHandle": "input-tools"}]
