"""Execution identity passed from CLI BatchContext to connected tools."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from services.cli_agent import mcp_server, workflow_tools
from services.cli_agent.mcp_server import BatchContext


class _Params(BaseModel):
    value: str


class _OtherParams(BaseModel):
    count: int


async def test_handler_forwards_execution_workspace_and_parent_identity():
    broadcaster = SimpleNamespace(
        update_node_status=AsyncMock(),
        broadcast_agent_capability=AsyncMock(),
    )
    context = BatchContext(
        workflow_id="workflow-1",
        node_id="cli-parent-1",
        execution_id="execution-1",
        workspace_dir=Path("workspace-1").resolve(),
        broadcaster=broadcaster,
        connected_tools=[
            {
                "node_id": "tool-1",
                "node_type": "contextTestTool",
                "label": "Context Tool",
                "_agent_tool_name": "contextTestTool",
                "_agent_tool_input_model": _Params,
                "parameters": {},
            }
        ],
    )
    context_token = mcp_server._current_batch.set(context)
    execute = AsyncMock(return_value={"ok": True})
    try:
        with patch("services.handlers.tools.execute_tool", new=execute):
            handler = workflow_tools._build_handler("contextTestTool", _Params)
            await handler(value="hello")
    finally:
        mcp_server._current_batch.reset(context_token)

    config = execute.await_args.args[2]
    assert config["workflow_id"] == "workflow-1"
    assert config["execution_id"] == "execution-1"
    assert config["workspace_dir"] == str(Path("workspace-1").resolve())
    assert config["parent_node_id"] == "cli-parent-1"
    assert [call.kwargs["state"] for call in broadcaster.broadcast_agent_capability.await_args_list] == [
        "started",
        "completed",
    ]
    started = broadcaster.broadcast_agent_capability.await_args_list[0]
    assert started.args == ("cli-parent-1",)
    assert started.kwargs["capability_name"] == "contextTestTool"
    assert started.kwargs["target_node_id"] == "tool-1"
    assert started.kwargs["workflow_id"] == "workflow-1"
    assert started.kwargs["execution_id"] == "execution-1"


async def test_every_call_carries_its_request_identity(monkeypatch):
    # A held call's draft is keyed by it (RFC-0007 §4.9), not only a delegation.
    from services.temporal.pi_protocol import canonical_digest

    request_context = SimpleNamespace(request_id=7, request=SimpleNamespace(headers={"mcp-session-id": "session-1"}))
    monkeypatch.setattr(workflow_tools, "_get_mcp", lambda: SimpleNamespace(get_context=lambda: SimpleNamespace(request_context=request_context)))
    context = BatchContext(
        workflow_id="workflow-3",
        node_id="cli-parent-3",
        execution_id="execution-3",
        workspace_dir=Path("workspace-3").resolve(),
        broadcaster=SimpleNamespace(update_node_status=AsyncMock(), broadcast_agent_capability=AsyncMock()),
        connected_tools=[
            {
                "node_id": "tool-3",
                "node_type": "contextTestTool",
                "_agent_tool_name": "contextTestTool",
                "_agent_tool_input_model": _Params,
                "parameters": {},
            }
        ],
    )
    context_token = mcp_server._current_batch.set(context)
    execute = AsyncMock(return_value={"ok": True})
    try:
        with patch("services.handlers.tools.execute_tool", new=execute):
            await workflow_tools._build_handler("contextTestTool", _Params)(value="hello")
    finally:
        mcp_server._current_batch.reset(context_token)

    assert execute.await_args.args[2]["tool_call_id"] == "mcp:" + canonical_digest({"session": "session-1", "request": 7})


async def test_handler_validates_the_authenticated_batch_schema():
    context = BatchContext(
        workflow_id="workflow-2",
        node_id="cli-parent-2",
        execution_id="execution-2",
        workspace_dir=Path("workspace-2").resolve(),
        broadcaster=SimpleNamespace(
            update_node_status=AsyncMock(),
            broadcast_agent_capability=AsyncMock(),
        ),
        connected_tools=[
            {
                "node_id": "tool-2",
                "node_type": "contextTestTool",
                "_agent_tool_name": "shared_tool",
                "_agent_tool_input_model": _OtherParams,
                "parameters": {},
            }
        ],
    )
    context_token = mcp_server._current_batch.set(context)
    execute = AsyncMock(return_value={"ok": True})
    try:
        with patch("services.handlers.tools.execute_tool", new=execute):
            handler = workflow_tools._build_handler(
                "shared_tool",
                "contextTestTool",
                _Params,
            )
            await handler(count=3)
    finally:
        mcp_server._current_batch.reset(context_token)

    assert execute.await_args.args[1] == {"count": 3}


def test_global_mcp_name_rejects_incompatible_concurrent_schema(monkeypatch):
    class _FakeMcp:
        def add_tool(self, *_args, **_kwargs):
            return None

        def remove_tool(self, *_args, **_kwargs):
            return None

    from nodes.tool.current_time_tool import CurrentTimeToolNode

    workflow_tools._reset_for_tests()
    monkeypatch.setattr(workflow_tools, "_get_mcp", lambda: _FakeMcp())
    first = {
        "node_id": "tool-a",
        "node_type": CurrentTimeToolNode.type,
        "_agent_tool_name": "shared_tool",
        "_agent_tool_input_model": _Params,
        "_agent_tool_schema": _Params.model_json_schema(),
    }
    second = {
        "node_id": "tool-b",
        "node_type": CurrentTimeToolNode.type,
        "_agent_tool_name": "shared_tool",
        "_agent_tool_input_model": _OtherParams,
        "_agent_tool_schema": _OtherParams.model_json_schema(),
    }
    workflow_tools.expose_workflow_tools([first])

    with pytest.raises(ValueError, match="schema conflict"):
        workflow_tools.expose_workflow_tools([second])

    assert workflow_tools._active_tool_refcounts == {"shared_tool": 1}
