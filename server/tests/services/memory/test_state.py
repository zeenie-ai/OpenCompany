"""Cross-store memory clear coverage for node-scoped todo state."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from services.memory.state import clear_agent_session_state
from services.todo_service import TodoService, todo_session_key


@pytest.mark.asyncio
async def test_memory_clear_removes_all_todo_nodes_in_workflow_and_legacy_keys(monkeypatch):
    from services import todo_service as todo_service_module

    service = TodoService()
    monkeypatch.setattr(todo_service_module, "_service", service)

    workflow_id = "wf-clear"
    session_id = "memory-session"
    first_key = todo_session_key(workflow_id, "todo-one")
    second_key = todo_session_key(workflow_id, "todo-two")
    other_key = todo_session_key("other-workflow", "todo-one")

    for key in (first_key, second_key, other_key, workflow_id, session_id, "default"):
        service.write(key, [{"content": key, "status": "pending"}])

    ai_stub = ModuleType("services.ai")
    ai_stub._memory_vector_stores = {}
    monkeypatch.setitem(sys.modules, "services.ai", ai_stub)

    result = await clear_agent_session_state(
        session_id=session_id,
        workflow_id=workflow_id,
    )

    assert set(result["cleared_todo_keys"]) == {
        first_key,
        second_key,
        workflow_id,
        session_id,
        "default",
    }
    assert service.get(first_key) == []
    assert service.get(second_key) == []
    assert service.get(workflow_id) == []
    assert service.get(session_id) == []
    assert service.get("default") == []
    assert service.get(other_key) == [{"content": other_key, "status": "pending"}]


def test_todo_session_key_versions_every_node_identity():
    assert todo_session_key("wf", "node") == "todo:v2:wf:node"
    assert todo_session_key("wf", None) == "wf"
    assert todo_session_key(None, "node") == "todo:v2:unsaved:node"
    assert todo_session_key() == "default"


@pytest.mark.asyncio
async def test_unsaved_memory_clear_removes_every_unsaved_todo_node(monkeypatch):
    from services import todo_service as todo_service_module

    service = TodoService()
    monkeypatch.setattr(todo_service_module, "_service", service)
    unsaved_a = todo_session_key(None, "todo-a")
    unsaved_b = todo_session_key(None, "todo-b")
    saved = todo_session_key("saved-workflow", "todo-a")
    for key in (unsaved_a, unsaved_b, saved):
        service.write(key, [{"content": key, "status": "pending"}])

    ai_stub = ModuleType("services.ai")
    ai_stub._memory_vector_stores = {}
    monkeypatch.setitem(sys.modules, "services.ai", ai_stub)

    result = await clear_agent_session_state(session_id="unsaved-session")

    assert unsaved_a in result["cleared_todo_keys"]
    assert unsaved_b in result["cleared_todo_keys"]
    assert service.get(unsaved_a) == []
    assert service.get(unsaved_b) == []
    assert service.get(saved)


@pytest.mark.asyncio
async def test_memory_node_clear_uses_atomic_mutation_and_preserves_unrelated_fields(
    monkeypatch,
):
    class AtomicDatabase:
        def __init__(self):
            self.parameters = {
                "memory_content": "old",
                "memory_jsonl": "old-jsonl",
                "last_session_id": "old-session",
                "vertex_interaction_id": "old-interaction",
                "vertex_environment_id": "old-environment",
                "unrelated": "preserve-me",
            }
            self.atomic_calls = 0

        async def mutate_node_parameters_atomic(
            self,
            node_id,
            mutator,
            **_kwargs,
        ):
            self.atomic_calls += 1
            self.parameters = mutator(dict(self.parameters))
            return dict(self.parameters), {}, True

    database = AtomicDatabase()
    monkeypatch.setattr(
        "services.plugin.deps.get_database",
        lambda: database,
    )
    ai_stub = ModuleType("services.ai")
    ai_stub._memory_vector_stores = {}
    monkeypatch.setitem(sys.modules, "services.ai", ai_stub)

    result = await clear_agent_session_state(
        session_id="session",
        workflow_id="workflow",
        memory_node_id="memory-node",
    )

    assert result["cleared_memory_node"] is True
    assert database.atomic_calls == 1
    assert database.parameters["memory_content"] == (
        "# Conversation History\n\n*No messages yet.*\n"
    )
    assert database.parameters["last_session_id"] is None
    assert database.parameters["unrelated"] == "preserve-me"
    assert "memory_jsonl" not in database.parameters
    assert "vertex_interaction_id" not in database.parameters
    assert "vertex_environment_id" not in database.parameters
