"""LLM tool calls must reach ToolNodes through execute_as_tool everywhere.

The Temporal AgentWorkflow schedules each tool call as the tool's own
per-type activity, which executes through the plugin's legacy handler.
That handler used to call plain ``execute``, validating the merged
params+args dict against ``Params`` — and for a split-schema ToolNode like
Simple Memory (``Params`` is ``extra="ignore"``), the model's ``operation``
and ``content`` were silently dropped and every ``remember`` degraded to a
harmless ``list``. The activity reported success, the agent believed it
had remembered, and the store stayed empty.

The fix threads the model's unmerged arguments as ``context["tool_args"]``
(agent_workflow payload → as_activity extras → legacy handler), which
routes ToolNodes through ``execute_as_tool`` with real ToolInput
validation.
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

import pytest

from nodes.tool.simple_memory import SimpleMemoryNode


@pytest.fixture
async def memory_database():
    # Root conftest stubs core.database for fast unit tests; load the real
    # implementation privately (same pattern as tests/services/memory/).
    module_name = f"tests._tool_dispatch_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[2] / "core" / "database.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    db_path = Path.cwd() / f".tool-dispatch-{uuid.uuid4().hex}.db"
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


def _context(**extra):
    return {
        "workflow_id": "wf-1",
        "user_id": "owner",
        "session_id": "default",
        "execution_id": "run-1",
        **extra,
    }


@pytest.mark.asyncio
async def test_tool_args_route_toolnodes_through_execute_as_tool():
    handler = SimpleMemoryNode._make_legacy_handler()
    tool_args = {"operation": "remember", "content": "launch is Tuesday"}
    with patch.object(
        SimpleMemoryNode,
        "execute_as_tool",
        AsyncMock(return_value={"operation": "remember"}),
    ) as as_tool:
        result = await handler(
            "mem-1",
            "simpleMemory",
            {"reset_policy": "preserve", **tool_args},
            _context(tool_args=tool_args),
        )
    as_tool.assert_awaited_once_with(
        tool_args,
        {"reset_policy": "preserve", **tool_args},
        ANY,
    )
    assert result == {"operation": "remember"}


@pytest.mark.asyncio
async def test_plain_execution_without_tool_args_is_unchanged():
    """A canvas Run of a tool node (no tool_args) keeps the execute path."""
    handler = SimpleMemoryNode._make_legacy_handler()
    with patch.object(
        SimpleMemoryNode,
        "execute",
        AsyncMock(return_value={"operation": "list", "items": []}),
    ) as execute, patch.object(
        SimpleMemoryNode,
        "execute_as_tool",
        AsyncMock(),
    ) as as_tool:
        await handler(
            "mem-1",
            "simpleMemory",
            {"reset_policy": "preserve"},
            _context(),
        )
    execute.assert_awaited_once()
    as_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_remember_through_the_activity_path_actually_stores(
    memory_database, monkeypatch
):
    """The regression, end to end: the merged dict alone stored nothing."""
    import nodes.tool.simple_memory as simple_memory_module

    monkeypatch.setattr(
        simple_memory_module, "get_database", lambda: memory_database
    )
    handler = SimpleMemoryNode._make_legacy_handler()
    tool_args = {"operation": "remember", "content": "the launch is Tuesday"}
    # node_data is the merged params+args dict exactly as the AgentWorkflow
    # builds it; tool_args rides alongside, unmerged.
    result = await handler(
        "1:simpleMemory:1",
        "simpleMemory",
        {"reset_policy": "preserve", **tool_args},
        _context(tool_args=tool_args, tool_call_id="call-1"),
    )
    assert result.get("operation") == "remember"
    assert result.get("memory", {}).get("content") == "the launch is Tuesday"

    from services.memory.tool_store import MemoryScope, MemoryToolStore

    listing = await MemoryToolStore(memory_database).list(
        MemoryScope(
            owner_id="owner",
            workflow_id="wf-1",
            memory_node_id="1:simpleMemory:1",
        )
    )
    assert [item["content"] for item in listing["items"]] == [
        "the launch is Tuesday"
    ]


def test_agent_workflow_payload_carries_unmerged_tool_args():
    import inspect

    from services.temporal.agent_workflow import AgentWorkflow

    # The entry point owns lifecycle cleanup; activity payloads are built by
    # the execution core it delegates to.
    source = inspect.getsource(AgentWorkflow._run_impl)
    assert '"tool_args": call_args' in source, (
        "the per-type tool activity payload must carry the model's unmerged "
        "arguments or split-schema ToolNodes silently drop them"
    )


def test_as_activity_forwards_tool_args_into_context():
    import inspect

    from services.plugin.base import BaseNode

    source = inspect.getsource(BaseNode.as_activity.__func__)
    assert '"tool_args"' in source, (
        "as_activity must forward tool_args through extras so the legacy "
        "handler can route ToolNodes through execute_as_tool"
    )


@pytest.mark.asyncio
async def test_memory_mutations_broadcast_a_panel_refresh(monkeypatch):
    """Both writers announce durable mutations so the panel stays live."""
    from nodes.tool.simple_memory import _events

    sent: list[dict] = []

    async def capture(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr(_events, "dispatch_memory_updated", capture)
    import nodes.tool.simple_memory as plugin_module

    monkeypatch.setattr(
        plugin_module, "dispatch_memory_updated", capture, raising=False
    )

    class _Store:
        async def remember(self, scope, **kwargs):
            return {"operation": "remember", "memory": {"id": "m1"}}

    with patch.object(
        plugin_module, "MemoryToolStore", lambda _db: _Store()
    ), patch.object(plugin_module, "get_database", lambda: object()):
        node = SimpleMemoryNode()
        from services.plugin.context import NodeContext

        ctx = NodeContext(
            node_id="1:simpleMemory:1",
            node_type="simpleMemory",
            raw={"workflow_id": "wf-1", "user_id": "owner"},
        )
        from nodes.tool.simple_memory import SimpleMemoryToolInput

        await node.memory(
            ctx,
            SimpleMemoryToolInput(operation="remember", content="x"),
        )
    assert sent == [
        {
            "workflow_id": "wf-1",
            "memory_node_id": "1:simpleMemory:1",
            "operation": "remember",
        }
    ]


def test_frontend_handles_the_memory_wire_key():
    """Same direction as the context.updated lock: the backend owns the
    wire key, and renaming it would silently stop the Memory panel
    refreshing with no test failing anywhere."""
    import inspect
    import re

    from nodes.tool.simple_memory import _events

    source = inspect.getsource(_events)
    emitted = set(re.findall(r'"type": "([a-z.]+)"', source))
    assert "memory.updated" in emitted

    ws_context = (
        Path(__file__).resolve().parents[3]
        / "client"
        / "src"
        / "contexts"
        / "WebSocketContext.tsx"
    )
    if not ws_context.exists():  # server-only checkouts
        pytest.skip("client sources not present")
    consumed = ws_context.read_text(encoding="utf-8")
    assert "case 'memory.updated'" in consumed
