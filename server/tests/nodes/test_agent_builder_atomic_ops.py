"""Focused idempotency tests for transactional agentBuilder batches."""

from unittest.mock import AsyncMock, patch

from services import workflow_ops
from nodes.tool import agent_builder as ab


def _tool_ops(node_id: str):
    add = workflow_ops.add_node("new_http", "httpRequest", {})
    add["minted_id"] = node_id
    return [
        add,
        workflow_ops.add_edge(
            {"client_ref": "new_http"},
            "agent-1",
            source_handle="output-main",
            target_handle="input-tools",
        ),
    ]


def test_distinct_parallel_batches_both_survive():
    initial = {
        "nodes": [{"id": "agent-1", "type": "aiAgent"}],
        "edges": [],
    }
    first, first_meta = ab._apply_canvas_ops(initial, _tool_ops("http-first"))
    second, second_meta = ab._apply_canvas_ops(first, _tool_ops("http-second"))

    assert first_meta["changed"] is True
    assert second_meta["changed"] is True
    tools = [node for node in second["nodes"] if node.get("type") == "httpRequest"]
    assert [node["id"] for node in tools] == ["http-first", "http-second"]
    assert len(second["edges"]) == 2


def test_reapplying_same_batch_is_a_noop():
    initial = {
        "nodes": [{"id": "agent-1", "type": "aiAgent"}],
        "edges": [],
    }
    once, _ = ab._apply_canvas_ops(initial, _tool_ops("http-one"))
    twice, metadata = ab._apply_canvas_ops(once, _tool_ops("http-one"))

    assert metadata["changed"] is False
    assert twice == once


async def test_missing_workflow_still_broadcasts_live_ops():
    ops = _tool_ops("http-live")
    with (
        patch.object(
            ab,
            "_persist_canvas_mutation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "nodes.tool.agent_builder._events.broadcast_workflow_ops",
            new_callable=AsyncMock,
        ) as broadcast,
    ):
        await ab._broadcast("missing-workflow", "agent-1", ops)

    broadcast.assert_awaited_once_with(
        workflow_id="missing-workflow",
        caller_node_id="agent-1",
        operations=ops,
    )
