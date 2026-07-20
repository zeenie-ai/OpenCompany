"""Execution identity passed from CLI BatchContext to connected tools."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from services.cli_agent import mcp_server, workflow_tools
from services.cli_agent.mcp_server import BatchContext


class _Params(BaseModel):
    value: str


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
