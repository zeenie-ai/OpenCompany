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
from unittest.mock import ANY, AsyncMock, MagicMock, patch

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


@pytest.mark.parametrize("arguments,error_type,feedback", [
    ({"operation": "assign_task"}, "ValidationError", "title"),
    ({"operation": "assign_task", "title": "Research", "mission": "Find evidence", "assignee_node_id": "missing"},
     "NodeUserError", "connected teammate"),
    ({"operation": "modify_task", "task_id": "task-1", "title": "Updated title"},
     "NodeUserError", "status=running"),
])
@pytest.mark.asyncio
async def test_task_manager_activity_preserves_validation_and_runtime_failure(monkeypatch, arguments, error_type, feedback):
    """A real tool failure must end the per-type activity's step as failed."""
    from core.container import container
    from nodes.tool.task_manager import TaskManagerNode
    from services.agent_team import AgentTeamService
    from services.chat import steps
    from temporalio import activity
    from temporalio.testing import ActivityEnvironment

    database = AsyncMock()
    database.find_team.return_value = {"id": "team-1"}
    database.get_durable_team_task.return_value = {
        "id": "task-1", "status": "running", "revision": 0,
        "current_attempt": 0, "retry_count": 0, "allowed_actions": ["cancel"],
    }
    database.transition_team_task.return_value = None
    task_events = SimpleNamespace(broadcast_team_event=AsyncMock())
    task_service = AgentTeamService(database, task_events)
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: task_service)
    handler = TaskManagerNode._make_legacy_handler()

    async def execute_node(**kwargs):
        return await handler(
            kwargs["node_id"], kwargs["node_type"], kwargs["parameters"],
            {**_context(), **kwargs["extras"], "nodes": kwargs["nodes"], "edges": kwargs["edges"]},
        )

    workflow_service = SimpleNamespace(execute_node=AsyncMock(side_effect=execute_node))
    monkeypatch.setattr(container, "workflow_service", lambda: workflow_service)
    broadcaster = SimpleNamespace(update_node_status=AsyncMock(), update_node_output=AsyncMock())
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: broadcaster)
    logger = MagicMock()
    monkeypatch.setattr(activity, "logger", logger)
    step = object()
    monkeypatch.setattr(steps, "begin", lambda *_: step)
    end_step = AsyncMock()
    monkeypatch.setattr(steps, "end", end_step)

    result = await ActivityEnvironment().run(TaskManagerNode.as_activity(), {
        **_context(), "node_id": "task-manager", "parent_node_id": "lead",
        "node_data": {"label": "Manager", **arguments}, "tool_args": arguments,
        "nodes": [], "edges": [],
    })

    assert result["success"] is False
    assert result["error_type"] == error_type
    assert feedback in result["error"]
    assert "delegation_request" not in result
    database.create_durable_team_task.assert_not_awaited()
    task_events.broadcast_team_event.assert_not_awaited()
    if arguments["operation"] == "modify_task":
        database.transition_team_task.assert_awaited_once_with(
            "team-1", "task-1", 0, ["blocked", "queued"], {"title": "Updated title"}, create_attempt=False,
        )
        assert "revision=0" in result["error"] and "allowed_actions=['cancel']" in result["error"]
        assert "get_task" in result["hint"]
        assert database.get_durable_team_task.return_value["status"] == "running"
    else:
        assert database.mock_calls == []
    assert [call.args[1] for call in broadcaster.update_node_status.await_args_list] == ["executing", "error"]
    broadcaster.update_node_output.assert_not_awaited()
    end_step.assert_awaited_once_with(step, state="failed", detail=None)
    logger.warning.assert_called_once()
    assert not any("succeeded" in str(call) for call in logger.info.call_args_list)


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
