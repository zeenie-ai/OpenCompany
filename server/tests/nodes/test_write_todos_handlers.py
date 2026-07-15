"""writeTodos parameter-panel WS handlers (get_todos / set_todos).

The Current Todos editor reads/writes the live TodoService state through
these handlers. Modern requests key by workflow + node while legacy
missing-node requests retain the workflow fallback. ``set_todos`` normalises through
``TodoService.write`` (drops non-dict items, coerces unknown statuses to
``pending``).
"""

from __future__ import annotations

import asyncio

import pytest

from nodes.tool.write_todos._events import todos_updated
from nodes.tool.write_todos._handlers import handle_get_todos, handle_set_todos
from services.todo_service import get_todo_service, todo_session_key


@pytest.fixture(autouse=True)
def _no_dispatch(monkeypatch):
    """Stub the centralized dispatch so the handler test doesn't depend on
    Temporal Visibility / the WS broadcaster being wired."""

    async def _noop(**_kwargs):
        return None

    monkeypatch.setattr("nodes.tool.write_todos._handlers.dispatch_todos_updated", _noop)


@pytest.mark.asyncio
async def test_set_then_get_round_trips_and_normalises():
    wf = "test-wf-round-trip"
    get_todo_service().clear(wf)

    set_resp = await handle_set_todos(
        {
            "workflow_id": wf,
            "todos": [
                {"content": "first", "status": "in_progress"},
                {"content": "second", "status": "bogus_status"},  # coerced -> pending
                {"status": "completed"},  # no content -> dropped
                "not-a-dict",  # dropped
            ],
        },
        None,
    )

    assert set_resp["success"] is True
    assert set_resp["session_key"] == wf
    assert set_resp["todos"] == [
        {"content": "first", "status": "in_progress"},
        {"content": "second", "status": "pending"},
    ]

    get_resp = await handle_get_todos({"workflow_id": wf}, None)
    assert get_resp["success"] is True
    assert get_resp["todos"] == set_resp["todos"]


@pytest.mark.asyncio
async def test_two_nodes_in_one_workflow_are_isolated_and_replace_independently(monkeypatch):
    workflow_id = "test-wf-isolated"
    first_node = "writeTodos-first"
    second_node = "writeTodos-second"
    service = get_todo_service()
    first_key = todo_session_key(workflow_id, first_node)
    second_key = todo_session_key(workflow_id, second_node)
    service.clear(first_key)
    service.clear(second_key)

    emitted = []

    async def _capture_event(**kwargs):
        emitted.append(todos_updated(**kwargs))

    monkeypatch.setattr(
        "nodes.tool.write_todos._handlers.dispatch_todos_updated",
        _capture_event,
    )

    await asyncio.gather(
        handle_set_todos(
            {
                "workflow_id": workflow_id,
                "node_id": first_node,
                "todos": [{"content": "first list", "status": "pending"}],
            },
            None,
        ),
        handle_set_todos(
            {
                "workflow_id": workflow_id,
                "node_id": second_node,
                "todos": [{"content": "second list", "status": "in_progress"}],
            },
            None,
        ),
    )

    assert {event.subject for event in emitted} == {first_key, second_key}
    assert {
        (event.data["workflow_id"], event.data["node_id"], event.data["session_key"])
        for event in emitted
    } == {
        (workflow_id, first_node, first_key),
        (workflow_id, second_node, second_key),
    }

    # A second write is still a full replacement, but only for its own node.
    await handle_set_todos(
        {
            "workflow_id": workflow_id,
            "node_id": first_node,
            "todos": [{"content": "first replacement", "status": "completed"}],
        },
        None,
    )

    first = await handle_get_todos(
        {"workflow_id": workflow_id, "node_id": first_node},
        None,
    )
    second = await handle_get_todos(
        {"workflow_id": workflow_id, "node_id": second_node},
        None,
    )

    assert first["session_key"] == f"todo:v2:{workflow_id}:{first_node}"
    assert second["session_key"] == f"todo:v2:{workflow_id}:{second_node}"
    assert first["todos"] == [{"content": "first replacement", "status": "completed"}]
    assert second["todos"] == [{"content": "second list", "status": "in_progress"}]


@pytest.mark.asyncio
async def test_unsaved_panel_request_is_still_scoped_to_node_id():
    node_id = "writeTodos-test-node"
    get_todo_service().clear(todo_session_key(None, node_id))

    await handle_set_todos(
        {"node_id": node_id, "todos": [{"content": "node-scoped", "status": "pending"}]},
        None,
    )
    # No workflow_id -> the node remains isolated in the explicit unsaved scope.
    get_resp = await handle_get_todos({"node_id": node_id}, None)
    assert get_resp["session_key"] == f"todo:v2:unsaved:{node_id}"
    assert get_resp["todos"] == [{"content": "node-scoped", "status": "pending"}]


@pytest.mark.asyncio
async def test_get_unknown_session_returns_empty():
    resp = await handle_get_todos({"workflow_id": "never-written"}, None)
    assert resp["success"] is True
    assert resp["todos"] == []


def test_todos_updated_event_carries_exact_node_identity():
    event = todos_updated(
        workflow_id="wf-event",
        node_id="todo-event-node",
        todos=[{"content": "route me", "status": "pending"}],
    )

    assert event.subject == "todo:v2:wf-event:todo-event-node"
    assert event.data["session_key"] == event.subject
    assert event.data["workflow_id"] == "wf-event"
    assert event.data["node_id"] == "todo-event-node"


def test_todos_updated_event_preserves_missing_node_fallback():
    event = todos_updated(
        workflow_id="wf-legacy-event",
        todos=[],
    )

    assert event.subject == "wf-legacy-event"
    assert event.data["session_key"] == "wf-legacy-event"


def test_todos_updated_event_uses_unsaved_scope_when_node_is_present():
    event = todos_updated(
        node_id="todo-unsaved",
        todos=[],
    )

    assert event.subject == "todo:v2:unsaved:todo-unsaved"
    assert event.data["session_key"] == event.subject
