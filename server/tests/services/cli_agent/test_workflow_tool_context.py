"""Execution identity passed from CLI BatchContext to connected tools."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

from pydantic import BaseModel

from services.cli_agent import mcp_server, workflow_tools
from services.cli_agent.mcp_server import BatchContext


class _Params(BaseModel):
    value: str


async def test_handler_forwards_execution_workspace_and_parent_identity():
    context = BatchContext(
        workflow_id="workflow-1",
        node_id="cli-parent-1",
        execution_id="execution-1",
        workspace_dir=Path("workspace-1").resolve(),
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
