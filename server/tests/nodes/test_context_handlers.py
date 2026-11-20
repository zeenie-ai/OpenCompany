"""Contract for the Context panel WS handlers over the plain store.

The panel shows the agent's CURRENT context only — rows from the newest
stored generation. Prior generations stay in the store as inert history
but are deliberately not browsable from the panel.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.agent_context import save_conversation


@pytest.fixture
async def handler_database():
    # Root conftest stubs core.database; load the real module privately.
    module_name = f"tests._context_handler_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[2] / "core" / "database.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    db_path = Path.cwd() / f".context-handler-{uuid.uuid4().hex}.db"
    database = module.Database(
        SimpleNamespace(
            database_url=f"sqlite+aiosqlite:///{db_path.as_posix()}",
            database_echo=False,
            database_pool_size=5,
            database_max_overflow=5,
        )
    )
    await database.startup()
    try:
        yield database
    finally:
        await database.shutdown()
        sys.modules.pop(module_name, None)
        for candidate in (
            db_path,
            Path(f"{db_path}-wal"),
            Path(f"{db_path}-shm"),
        ):
            candidate.unlink(missing_ok=True)


class _Socket:
    """Authenticated external socket double."""

    state = SimpleNamespace(user_id="owner")
    scope = {"path": "/ws/status", "user_id": "owner"}


@pytest.mark.asyncio
async def test_instance_viewer_is_isolated_and_cannot_clear_job_history(handler_database, monkeypatch):
    from models.agent_instances import AgentInstance, AgentInstanceConversation
    from nodes.context import _handlers

    database = handler_database
    await database.save_workflow("wf-1", "WF", "wf", _graph())
    async with database.get_session() as session:
        for instance_id, workflow_id, content in (("first", "wf-1", "first job"),
                                                   ("second", "wf-1", "second job"),
                                                   ("private", "wf-2", "private job")):
            session.add(AgentInstance(id=instance_id, workflow_id=workflow_id, generation=2,
                                     team_id="team", job_id=instance_id, template_node_id="agent-a"))
            session.add(AgentInstanceConversation(instance_id=instance_id, messages=[{"role": "user", "content": content}]))
        await session.commit()
    monkeypatch.setattr(_handlers, "_database", lambda: database)
    request = {"workflow_id": "wf-1", "context_node_id": "ctx-1", "agent_instance_id": "second"}
    result = await _handlers.handle_get_agent_context(request, _Socket())
    assert result["context"]["agent_instance_id"] == "second"
    assert result["context"]["messages"][0]["content"] == "second job"
    assert {row["agent_instance_id"] for row in result["context"]["conversations"]} == {"first", "second"}
    private = await _handlers.handle_get_agent_context({**request, "agent_instance_id": "private"}, _Socket())
    assert private["context"]["messages"] == []
    refused = await _handlers.handle_clear_agent_context(request, _Socket())
    assert refused["success"] is False
    again = await _handlers.handle_get_agent_context(request, _Socket())
    assert again["context"]["messages"] == result["context"]["messages"]


def _graph() -> dict:
    return {
        "owner_id": "owner",
        "nodes": [
            {"id": "ctx-1", "type": "context"},
            {"id": "agent-a", "type": "aiAgent"},
        ],
        "edges": [],
    }


@pytest.mark.asyncio
async def test_get_agent_context_returns_the_live_generation_only(
    handler_database, monkeypatch
):
    from nodes.context import _handlers

    database = handler_database
    assert await database.save_workflow("wf-1", "WF", "wf", _graph())
    for generation, content in ((1, "old"), (2, "live")):
        await save_conversation(
            database,
            workflow_id="wf-1",
            generation=generation,
            agent_node_id="agent-a",
            messages=[{"role": "user", "content": content}],
        )
    await save_conversation(
        database,
        workflow_id="wf-1",
        generation=2,
        agent_node_id="agent-b",
        messages=[{"role": "user", "content": "sibling"}],
    )
    monkeypatch.setattr(_handlers, "_database", lambda: database)

    result = await _handlers.handle_get_agent_context(
        {"workflow_id": "wf-1", "context_node_id": "ctx-1"},
        _Socket(),
    )

    assert result["success"] is True
    context = result["context"]
    # Only the newest generation's agents are listed.
    assert {row["generation"] for row in context["conversations"]} == {2}
    assert {row["agent_node_id"] for row in context["conversations"]} == {
        "agent-a",
        "agent-b",
    }
    # Default selection is the newest row; its transcript is the live one.
    assert context["generation"] == 2
    assert [m["content"] for m in context["messages"]] in (
        ["live"],
        ["sibling"],
    )

    # Selecting a specific agent narrows within the live generation.
    narrowed = await _handlers.handle_get_agent_context(
        {
            "workflow_id": "wf-1",
            "context_node_id": "ctx-1",
            "agent_node_id": "agent-a",
        },
        _Socket(),
    )
    assert narrowed["context"]["agent_node_id"] == "agent-a"
    assert [m["content"] for m in narrowed["context"]["messages"]] == ["live"]


@pytest.mark.asyncio
async def test_workflow_reset_clears_the_stored_conversations(
    handler_database, monkeypatch
):
    """Reset must actually wipe: the panel shows the newest STORED
    generation, so surviving rows would keep rendering the pre-Reset
    conversation as the live context and Reset would look like a no-op."""

    from nodes.context import AgentContextNode
    from services.agent_context import list_conversations

    database = handler_database
    for generation in (1, 2):
        await save_conversation(
            database,
            workflow_id="wf-1",
            generation=generation,
            agent_node_id="agent-a",
            messages=[{"role": "user", "content": "before reset"}],
        )

    broadcasts: list[dict] = []

    async def capture(**kwargs):
        broadcasts.append(kwargs)

    from nodes.context import _events

    monkeypatch.setattr(_events, "dispatch_context_updated", capture)

    result = await AgentContextNode.reset_execution_state(
        node_id="ctx-1",
        workflow_id="wf-1",
        execution_id="exec-1",
        generation=2,
        graph={},
        database=database,
    )

    assert result["reset"] is True
    assert result["cleared_conversations"] == 2
    assert await list_conversations(database, workflow_id="wf-1") == []
    # An open panel refreshes off the broadcast rather than waiting for a
    # manual refresh.
    assert broadcasts and broadcasts[0]["workflow_id"] == "wf-1"


@pytest.mark.asyncio
async def test_clearing_the_chat_forgets_the_workflows_conversations(
    handler_database, monkeypatch
):
    """The owner's chat Clear (services/chat_thread.clear_chat_session)
    reaches the plugin's listener: every conversation of that workflow
    goes, so the agent starts over with the chat; other workflows keep
    theirs."""

    from nodes.context import _handlers
    from services import chat_thread
    from services.agent_context import list_conversations

    assert _handlers.on_chat_cleared in chat_thread._CLEARED_LISTENERS

    database = handler_database
    for workflow_id, generation in (("wf-1", 1), ("wf-1", 2), ("wf-2", 1)):
        await save_conversation(
            database,
            workflow_id=workflow_id,
            generation=generation,
            agent_node_id="agent-a",
            messages=[{"role": "user", "content": "what did I ask?"}],
        )

    broadcasts: list[dict] = []

    async def capture(**kwargs):
        broadcasts.append(kwargs)

    monkeypatch.setattr(_handlers, "dispatch_context_updated", capture)

    await _handlers.on_chat_cleared(database=database, workflow_id="wf-1")

    assert await list_conversations(database, workflow_id="wf-1") == []
    assert len(await list_conversations(database, workflow_id="wf-2")) == 1
    # An open Context panel refreshes off the broadcast.
    assert broadcasts == [
        {"workflow_id": "wf-1", "generation": 0, "agent_node_id": "wf-1", "message_count": 0}
    ]


@pytest.mark.asyncio
async def test_reset_without_an_admitted_generation_is_a_no_op(
    handler_database,
):
    from nodes.context import AgentContextNode

    result = await AgentContextNode.reset_execution_state(
        node_id="ctx-1",
        workflow_id="wf-1",
        execution_id="exec-1",
        generation=0,
        graph={},
        database=handler_database,
    )
    assert result == {"reset": False, "cleared_conversations": 0}


@pytest.mark.asyncio
async def test_get_agent_context_empty_store_is_an_empty_context(
    handler_database, monkeypatch
):
    from nodes.context import _handlers

    database = handler_database
    assert await database.save_workflow("wf-1", "WF", "wf", _graph())
    monkeypatch.setattr(_handlers, "_database", lambda: database)

    result = await _handlers.handle_get_agent_context(
        {"workflow_id": "wf-1", "context_node_id": "ctx-1"},
        _Socket(),
    )
    assert result["success"] is True
    assert result["context"]["conversations"] == []
    assert result["context"]["messages"] == []
