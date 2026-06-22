"""writeTodos parameter-panel WS handlers (get_todos / set_todos).

The Current Todos editor reads/writes the live TodoService state through
these handlers. They key by ``workflow_id`` (``node_id`` fallback) —
mirroring the ``write`` op — and ``set_todos`` normalises through
``TodoService.write`` (drops non-dict items, coerces unknown statuses to
``pending``).
"""

from __future__ import annotations

import pytest

from nodes.tool.write_todos._handlers import handle_get_todos, handle_set_todos
from services.todo_service import get_todo_service


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
async def test_session_key_falls_back_to_node_id():
    node_id = "writeTodos-test-node"
    get_todo_service().clear(node_id)

    await handle_set_todos(
        {"node_id": node_id, "todos": [{"content": "node-scoped", "status": "pending"}]},
        None,
    )
    # No workflow_id -> keyed by node_id; get with the same fallback resolves it.
    get_resp = await handle_get_todos({"node_id": node_id}, None)
    assert get_resp["session_key"] == node_id
    assert get_resp["todos"] == [{"content": "node-scoped", "status": "pending"}]


@pytest.mark.asyncio
async def test_get_unknown_session_returns_empty():
    resp = await handle_get_todos({"workflow_id": "never-written"}, None)
    assert resp["success"] is True
    assert resp["todos"] == []
