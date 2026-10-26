"""Excluded specialist runtimes retain their execution and delegate via ack."""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel


class DelegationInput(BaseModel):
    task: str
    context: str = ""


def scope():
    return {"pi_harness_version": 1, "user_id": "alice", "root_execution_id": "root",
            "execution_id": "run", "workflow_slug": "saved", "generation": 2, "graphVersion": 3,
            "parameter_snapshot": {"coding": {"model": "model"}}, "execution_control_version": 1,
            "controller_workflow_id": "controller", "execution_control_revision": 8,
            "nodes": [{"id": "coding", "type": "coding_agent"}], "edges": [], "approval_wait_version": 1}


@pytest.mark.asyncio
async def test_claude_mcp_uses_authenticated_request_identity_and_trusted_principal(monkeypatch):
    from services.cli_agent import mcp_server, workflow_tools
    execute = AsyncMock(return_value={"status": "DELEGATED"})
    monkeypatch.setattr("services.handlers.tools.execute_tool", execute)
    monkeypatch.setattr("services.plugin.deps.get_ai_service", lambda: object())
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: object())
    request_context = SimpleNamespace(request_id="rpc-23", request=SimpleNamespace(headers={"mcp-session-id": "session-1"}))
    monkeypatch.setattr(workflow_tools, "_get_mcp", lambda: SimpleNamespace(get_context=lambda: SimpleNamespace(request_context=request_context)))
    ctx = mcp_server.BatchContext(workflow_id="wf", node_id="parent", execution_id="run", workspace_dir=Path("workspace"),
        user_id="owner", broadcaster=SimpleNamespace(update_node_status=AsyncMock(), broadcast_agent_capability=AsyncMock()),
        connected_tools=[{"node_id": "coding", "node_type": "coding_agent", "_agent_tool_name": "delegate",
                          "_agent_tool_input_model": DelegationInput, "pi_harness_version": 1, "_pi_inline_scope": scope()}])
    token = mcp_server._current_batch.set(ctx)
    try:
        result = await workflow_tools._build_handler("delegate", "coding_agent", DelegationInput)(task="edit")
    finally:
        mcp_server._current_batch.reset(token)
    assert result["status"] == "DELEGATED"
    config = execute.await_args.args[2]
    from services.temporal.pi_protocol import canonical_digest
    assert config["tool_call_id"] == "mcp:" + canonical_digest({"session": "session-1", "request": "rpc-23"})
    assert config["user_id"] == "alice" and config["root_execution_id"] == "root"
    assert config["execution_control_revision"] == 8
    # What asks rides the same scope (RFC-0007 §4.9): Start admits every
    # generation with the Pi flag, so a generation that waits forwards it.
    assert config["approval_wait_version"] == 1
    assert execute.await_args.args[1] == {"task": "edit"}

    # Independent clients can both call request 1 in the same admitted batch.
    # A matching retry attaches; another client or a differently typed ID does
    # not accidentally attach to the first client's durable Coding operation.
    token = mcp_server._current_batch.set(ctx)
    try:
        handler = workflow_tools._build_handler("delegate", "coding_agent", DelegationInput)
        request_context.request_id = 1
        await handler(task="edit")
        first = execute.await_args.args[2]["tool_call_id"]
        await handler(task="edit")
        assert execute.await_args.args[2]["tool_call_id"] == first
        request_context.request.headers["mcp-session-id"] = "session-2"
        await handler(task="edit")
        second = execute.await_args.args[2]["tool_call_id"]
        assert second != first
        request_context.request_id = "1"
        await handler(task="edit")
        assert execute.await_args.args[2]["tool_call_id"] not in {first, second}
    finally:
        mcp_server._current_batch.reset(token)


@pytest.mark.asyncio
async def test_rlm_exposes_only_marked_coding_and_records_source_position(monkeypatch):
    from services.rlm.adapters import ToolBridgeAdapter
    execute = AsyncMock(return_value={"status": "DELEGATED"})
    monkeypatch.setattr("services.handlers.tools.execute_tool", execute)
    monkeypatch.setattr("services.plugin.deps.get_ai_service", lambda: object())
    monkeypatch.setattr("services.plugin.deps.get_database", lambda: object())
    tool = {"node_id": "coding", "node_type": "coding_agent", "_agent_tool_name": "delegate",
            "_agent_tool_input_model": DelegationInput, "parameters": {}}
    assert ToolBridgeAdapter.bridge([tool], context={}) == {}
    tools = ToolBridgeAdapter.bridge([tool], context=scope(), loop=asyncio.get_running_loop(),
                                    parent_node_id="parent", workflow_id="wf")
    def execute_code(code):
        namespace = {"delegate": tools["delegate"]["tool"]}
        exec(code, namespace, namespace)
    await asyncio.to_thread(execute_code, 'reply = delegate(task="edit")')
    config = execute.await_args.args[2]
    assert config["tool_call_id"].startswith("rlm:")
    assert config["root_execution_id"] == "root" and config["controller_workflow_id"] == "controller"
    assert config["approval_wait_version"] == 1
    assert execute.await_args.args[1] == {"task": "edit"}
