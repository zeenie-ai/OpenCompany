"""Contract tests for ai_tools nodes: calculatorTool, currentTimeTool,
duckduckgoSearch, taskManager, writeTodos.

These tests freeze the input -> output behaviour documented in
`docs-internal/node-logic-flows/ai_tools/`. A refactor that breaks any of
these indicates the docs (and the user-visible contract) need to be updated.

Notes on invocation paths
=========================
- calculatorTool / currentTimeTool / duckduckgoSearch are tool-only (not
  registered in NodeExecutor._handlers). Their real contract is the internal
  `_execute_*` function invoked via `execute_tool`, so tests call those
  functions directly with the same arg shape the tool dispatcher would pass.
- taskManager and writeTodos ARE in the NodeExecutor registry; they are
  exercised through the shared `harness` where it makes sense and via the
  handler directly when we need to inject a mock broadcaster / database.
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


pytestmark = pytest.mark.node_contract


# ============================================================================
# calculatorTool
# ============================================================================


class TestCalculatorTool:
    """Pure-function arithmetic: no network, no state."""

    @pytest.mark.parametrize(
        "op,a,b,expected",
        [
            ("add", 2, 3, 5),
            ("subtract", 10, 4, 6),
            ("multiply", 6, 7, 42),
            ("divide", 20, 5, 4),
            ("power", 2, 10, 1024),
            ("mod", 10, 3, 1),
            ("abs", -7, 0, 7),
        ],
    )
    async def test_supported_operations_compute_expected_result(self, op, a, b, expected):
        from tests.nodes._compat import _execute_calculator

        result = await _execute_calculator({"operation": op, "a": a, "b": b})

        assert result["operation"] == op
        assert result["a"] == float(a)
        assert result["b"] == float(b)
        assert result["result"] == pytest.approx(expected)

    async def test_sqrt_uses_abs_for_negative_input(self):
        from tests.nodes._compat import _execute_calculator

        result = await _execute_calculator({"operation": "sqrt", "a": -9})
        assert result["result"] == pytest.approx(3.0)

    async def test_divide_by_zero_returns_infinity(self):
        from tests.nodes._compat import _execute_calculator

        result = await _execute_calculator({"operation": "divide", "a": 5, "b": 0})
        assert result["result"] == math.inf

    async def test_mod_by_zero_returns_zero(self):
        from tests.nodes._compat import _execute_calculator

        result = await _execute_calculator({"operation": "mod", "a": 5, "b": 0})
        assert result["result"] == 0

    async def test_unknown_operation_returns_error_with_supported_list(self):
        from tests.nodes._compat import _execute_calculator

        result = await _execute_calculator({"operation": "quantum", "a": 1, "b": 2})
        assert "error" in result
        assert "quantum" in result["error"]
        supported = result["supported_operations"]
        assert set(supported) == {
            "add",
            "subtract",
            "multiply",
            "divide",
            "power",
            "sqrt",
            "mod",
            "abs",
        }

    async def test_non_numeric_operand_raises(self):
        # Documented quirk: handler does not catch ValueError on float()
        # conversion -- it propagates to the caller. Frozen as-is so the
        # refactor doesn't accidentally swallow it.
        import pytest as _pytest

        from tests.nodes._compat import _execute_calculator

        with _pytest.raises(ValueError):
            await _execute_calculator({"operation": "add", "a": "abc", "b": 1})

    async def test_operation_is_case_insensitive(self):
        from tests.nodes._compat import _execute_calculator

        result = await _execute_calculator({"operation": "ADD", "a": 1, "b": 2})
        assert result["result"] == 3


# ============================================================================
# currentTimeTool
# ============================================================================


class TestCurrentTimeTool:
    """datetime.now(tz) wrapper - timezone resolution + error path."""

    async def test_happy_path_returns_documented_fields(self):
        from tests.nodes._compat import _execute_current_time

        result = await _execute_current_time({"timezone": "UTC"}, {})

        assert set(result.keys()) == {
            "datetime",
            "date",
            "time",
            "timezone",
            "day_of_week",
            "timestamp",
        }
        assert result["timezone"] == "UTC"
        assert isinstance(result["timestamp"], int)
        # basic shape checks
        assert len(result["date"].split("-")) == 3
        assert len(result["time"].split(":")) == 3

    async def test_arg_timezone_overrides_node_param(self):
        from tests.nodes._compat import _execute_current_time

        result = await _execute_current_time(
            {"timezone": "America/New_York"},
            {"timezone": "UTC"},
        )
        assert result["timezone"] == "America/New_York"

    async def test_empty_arg_falls_back_to_node_param(self):
        from tests.nodes._compat import _execute_current_time

        result = await _execute_current_time({"timezone": ""}, {"timezone": "Europe/London"})
        assert result["timezone"] == "Europe/London"

    async def test_no_timezone_anywhere_defaults_to_utc(self):
        from tests.nodes._compat import _execute_current_time

        result = await _execute_current_time({}, {})
        assert result["timezone"] == "UTC"

    async def test_invalid_timezone_returns_error_dict(self):
        from tests.nodes._compat import _execute_current_time

        result = await _execute_current_time({"timezone": "Mars/OlympusMons"}, {})
        assert "error" in result
        assert "Invalid timezone" in result["error"]
        # must not raise; must not return the happy-path fields
        assert "datetime" not in result


# ============================================================================
# duckduckgoSearch
# ============================================================================


class TestDuckDuckGoSearch:
    """ddgs-library wrapper; heavy mocking so no real network is touched."""

    async def test_happy_path_maps_ddgs_results_to_documented_shape(self):
        fake_ddgs_instance = MagicMock()
        fake_ddgs_instance.text.return_value = [
            {"title": "A", "body": "snippet A", "href": "https://a.example"},
            {"title": "B", "body": "snippet B", "href": "https://b.example"},
        ]
        fake_ddgs_class = MagicMock(return_value=fake_ddgs_instance)

        fake_module = MagicMock()
        fake_module.DDGS = fake_ddgs_class

        from tests.nodes._compat import _execute_duckduckgo_search as _flat_search

        with patch.dict("sys.modules", {"ddgs": fake_module}):
            result = await _flat_search(
                {"query": "python"},
                {"provider": "duckduckgo", "max_results": 2},
            )

        assert result["query"] == "python"
        assert result["provider"] == "duckduckgo"
        assert result["results"] == [
            {"title": "A", "snippet": "snippet A", "url": "https://a.example"},
            {"title": "B", "snippet": "snippet B", "url": "https://b.example"},
        ]
        fake_ddgs_instance.text.assert_called_once_with("python", max_results=2)

    async def test_empty_query_short_circuits_before_import(self):
        from tests.nodes._compat import _execute_duckduckgo_search

        result = await _execute_duckduckgo_search({"query": ""}, {})
        assert result == {"error": "No search query provided"}

    async def test_missing_keys_in_ddgs_output_default_to_empty_string(self):
        from tests.nodes._compat import _execute_duckduckgo_search as _flat_search

        fake_ddgs_instance = MagicMock()
        fake_ddgs_instance.text.return_value = [{"title": "only-title"}]
        fake_module = MagicMock()
        fake_module.DDGS = MagicMock(return_value=fake_ddgs_instance)

        with patch.dict("sys.modules", {"ddgs": fake_module}):
            result = await _flat_search({"query": "q"}, {"max_results": 5})

        assert result["results"] == [{"title": "only-title", "snippet": "", "url": ""}]

    async def test_max_results_defaults_to_5_when_missing(self):
        from tests.nodes._compat import _execute_duckduckgo_search as _flat_search

        fake_ddgs_instance = MagicMock()
        fake_ddgs_instance.text.return_value = []
        fake_module = MagicMock()
        fake_module.DDGS = MagicMock(return_value=fake_ddgs_instance)

        with patch.dict("sys.modules", {"ddgs": fake_module}):
            await _flat_search({"query": "q"}, {})

        fake_ddgs_instance.text.assert_called_once_with("q", max_results=5)


# ============================================================================
# taskManager
# ============================================================================


class TestTaskManager:
    """In-memory delegation-registry inspector. State lives in module globals
    `_delegated_tasks` and `_delegation_results` in services.handlers.tools."""

    @pytest.fixture(autouse=True)
    def _reset_registry(self):
        """Snapshot + restore the module-level dicts around each test."""
        from services.handlers import tools as tools_mod

        tasks_backup = dict(tools_mod._delegated_tasks)
        results_backup = dict(tools_mod._delegation_results)
        tools_mod._delegated_tasks.clear()
        tools_mod._delegation_results.clear()
        try:
            yield tools_mod
        finally:
            tools_mod._delegated_tasks.clear()
            tools_mod._delegated_tasks.update(tasks_backup)
            tools_mod._delegation_results.clear()
            tools_mod._delegation_results.update(results_backup)

    async def test_list_tasks_reports_running_and_completed_entries(self, _reset_registry):
        tools_mod = _reset_registry

        running_task = MagicMock()
        running_task.done.return_value = False
        tools_mod._delegated_tasks["t1"] = running_task

        tools_mod._delegation_results["t2"] = {
            "status": "completed",
            "agent_name": "coding_agent",
            "result": "x" * 500,  # will be truncated to 200 chars
        }

        from nodes.tool.task_manager import _execute_task_manager

        result = await _execute_task_manager({"operation": "list_tasks"}, {"parameters": {}})

        assert result["success"] is True
        assert result["operation"] == "list_tasks"
        assert result["count"] == 2
        assert result["running"] == 1
        assert result["completed"] == 1
        assert result["errors"] == 0

        ids = {t["task_id"] for t in result["tasks"]}
        assert ids == {"t1", "t2"}
        t2 = next(t for t in result["tasks"] if t["task_id"] == "t2")
        assert t2["agent_name"] == "coding_agent"
        assert len(t2["result_summary"]) == 200

    async def test_list_tasks_applies_status_filter(self, _reset_registry):
        tools_mod = _reset_registry

        running = MagicMock()
        running.done.return_value = False
        tools_mod._delegated_tasks["r1"] = running
        tools_mod._delegation_results["c1"] = {"status": "completed"}

        from nodes.tool.task_manager import _execute_task_manager

        result = await _execute_task_manager(
            {"operation": "list_tasks", "status_filter": "running"},
            {"parameters": {}},
        )

        assert result["count"] == 1
        assert result["tasks"][0]["task_id"] == "r1"

    async def test_get_task_without_id_errors(self, _reset_registry):
        tools_mod = _reset_registry

        from nodes.tool.task_manager import _execute_task_manager

        result = await _execute_task_manager({"operation": "get_task"}, {"parameters": {}})
        assert result["success"] is False
        assert "task_id is required" in result["error"]

    async def test_mark_done_removes_tracked_task(self, _reset_registry):
        tools_mod = _reset_registry
        tools_mod._delegation_results["gone"] = {"status": "completed"}

        from nodes.tool.task_manager import _execute_task_manager

        result = await _execute_task_manager(
            {"operation": "mark_done", "task_id": "gone"},
            {"parameters": {}},
        )

        assert result["success"] is True
        assert result["removed"] is True
        assert "gone" not in tools_mod._delegation_results

    async def test_mark_done_untracked_id_returns_removed_false(self, _reset_registry):
        tools_mod = _reset_registry

        from nodes.tool.task_manager import _execute_task_manager

        result = await _execute_task_manager(
            {"operation": "mark_done", "task_id": "never-seen"},
            {"parameters": {}},
        )

        assert result["success"] is True
        assert result["removed"] is False
        assert "was not in active tracking" in result["message"]

    async def test_unknown_operation_returns_failure_envelope(self, _reset_registry):
        tools_mod = _reset_registry

        from nodes.tool.task_manager import _execute_task_manager

        result = await _execute_task_manager({"operation": "self_destruct"}, {"parameters": {}})
        assert result["success"] is False
        assert "Unknown operation" in result["error"]


# ============================================================================
# writeTodos
# ============================================================================


class TestWriteTodos:
    """Dual-purpose tool: TodoService singleton + broadcast side-effect."""

    @pytest.fixture(autouse=True)
    def _fresh_todo_service(self):
        """Reset the TodoService singleton between tests."""
        from services import todo_service as svc_mod

        backup = svc_mod._service
        svc_mod._service = None
        try:
            yield
        finally:
            svc_mod._service = backup

    async def test_happy_path_stores_todos_and_broadcasts(self):
        from tests.nodes._compat import handle_write_todos
        from services.todo_service import get_todo_service

        broadcaster = MagicMock()
        broadcaster.update_node_status = AsyncMock(return_value=None)

        context = {"workflow_id": "wf-42", "broadcaster": broadcaster}

        todos = [
            {"content": "Design schema", "status": "completed"},
            {"content": "Write migration", "status": "in_progress"},
            {"content": "Ship it", "status": "pending"},
        ]

        result = await handle_write_todos(
            node_id="todo-node-1",
            node_type="writeTodos",
            parameters={"todos": todos},
            context=context,
        )

        assert result["success"] is True
        assert result["count"] == 3
        assert "Updated todo list (3 items)" == result["message"]

        # TodoService persists validated list under workflow_id
        stored = get_todo_service().get("wf-42")
        assert len(stored) == 3
        assert stored[0] == {"content": "Design schema", "status": "completed"}

        # Broadcast fired with phase=todo_update
        broadcaster.update_node_status.assert_awaited_once()
        args, kwargs = broadcaster.update_node_status.call_args
        assert args[0] == "todo-node-1"
        assert args[1] == "executing"
        assert args[2]["phase"] == "todo_update"
        assert args[2]["todos"] == stored
        assert kwargs.get("workflow_id") == "wf-42"

    async def test_plugin_returns_list_todos_and_coerces_stringified_args(self):
        """Regression for the Output contract violation surfaced by
        ``_wrap_success`` enforcement: the plugin returned ``todos`` as
        TodoService's raw JSON STRING (``format_for_llm``) while
        ``WriteTodosOutput`` declares ``Optional[list]``. Also locks the
        Params boundary coercion — Gemini sometimes stringifies array
        args in tool calls, so a JSON-encoded ``todos`` string must
        parse instead of failing validation."""
        import json

        from nodes.tool.write_todos import WriteTodosNode
        from services.plugin import NodeContext
        from services.todo_service import get_todo_service

        node = WriteTodosNode()
        ctx = NodeContext.from_legacy(
            node_id="todo-node-2",
            node_type="writeTodos",
            context={"workflow_id": "wf-str"},
        )
        # LLM-stringified array arg (the Gemini failure mode).
        result = await node.execute(
            "todo-node-2",
            {"todos": '[{"content": "From stringified args", "status": "in_progress"}]'},
            ctx,
        )

        # ToolNode flat contract: no success wrapper, real list in todos.
        assert "success" not in result
        assert isinstance(result["todos"], list)
        assert result["todos"] == [{"content": "From stringified args", "status": "in_progress"}]
        assert result["count"] == 1
        json.dumps(result)  # whole payload JSON-safe for node_outputs

        # Service state matches what the output reported.
        assert get_todo_service().get("wf-str") == result["todos"]

    async def test_invalid_items_are_silently_dropped_or_coerced(self):
        from tests.nodes._compat import handle_write_todos
        from services.todo_service import get_todo_service

        todos = [
            {"content": "valid", "status": "pending"},
            {"content": "   ", "status": "pending"},  # dropped (empty)
            {"content": "bad-status", "status": "on_hold"},  # coerced to pending
            "not a dict",  # dropped
        ]

        result = await handle_write_todos(
            node_id="n",
            node_type="writeTodos",
            parameters={"todos": todos},
            context={"workflow_id": "wf"},
        )

        assert result["success"] is True
        assert result["count"] == 2

        stored = get_todo_service().get("wf")
        assert stored == [
            {"content": "valid", "status": "pending"},
            {"content": "bad-status", "status": "pending"},
        ]

    async def test_falls_back_to_default_session_key_without_workflow_id_or_node_id(self):
        from services.handlers.todo import execute_write_todos
        from services.todo_service import get_todo_service

        result = await execute_write_todos(
            {"todos": [{"content": "x", "status": "pending"}]},
            config={},  # no workflow_id, no node_id, no broadcaster
        )

        assert result["success"] is True
        assert get_todo_service().get("default") == [{"content": "x", "status": "pending"}]

    async def test_empty_todos_list_yields_empty_stored_state(self):
        from tests.nodes._compat import handle_write_todos
        from services.todo_service import get_todo_service

        result = await handle_write_todos(
            node_id="n",
            node_type="writeTodos",
            parameters={"todos": []},
            context={"workflow_id": "wf"},
        )

        assert result["success"] is True
        assert result["count"] == 0
        assert get_todo_service().get("wf") == []

    async def test_no_broadcaster_no_broadcast_but_still_success(self):
        """If the caller omits broadcaster (e.g. test harness, CLI), the
        handler must still succeed and persist state."""
        from tests.nodes._compat import handle_write_todos
        from services.todo_service import get_todo_service

        result = await handle_write_todos(
            node_id="n",
            node_type="writeTodos",
            parameters={"todos": [{"content": "x", "status": "pending"}]},
            context={"workflow_id": "wf"},  # no broadcaster key
        )

        assert result["success"] is True
        assert get_todo_service().get("wf") == [{"content": "x", "status": "pending"}]


# ============================================================================
# delegation_wait_seconds (blocking delegation for bridged cloud agents)
# ============================================================================


class TestDelegationWait:
    """Opt-in blocking wait in _execute_delegated_agent.

    ``config["delegation_wait_seconds"] > 0`` awaits the child inline
    (asyncio.shield -- the child is never cancelled) and returns its real
    result; timeout falls back to the task_id + check_delegated_tasks
    polling contract. Absent key = fire-and-forget (native agent loop).
    """

    @pytest.fixture(autouse=True)
    def _reset_registry(self):
        """Snapshot + restore all delegation module state around each test."""
        from services.handlers import tools as tools_mod

        backups = {
            name: dict(getattr(tools_mod, name))
            for name in (
                "_delegated_tasks",
                "_delegation_results",
                "_active_delegations",
                "_active_delegated_nodes",
            )
        }
        for name in backups:
            getattr(tools_mod, name).clear()
        try:
            yield tools_mod
        finally:
            for name, backup in backups.items():
                getattr(tools_mod, name).clear()
                getattr(tools_mod, name).update(backup)

    def _make_env(self, child_result=None, child_delay=0.0):
        """Fake plugin + mocked services for _execute_delegated_agent."""
        calls = {"execute": 0}
        result = child_result if child_result is not None else {"success": True, "result": {"response": "hi"}}

        class FakeAgentPlugin:
            async def execute(self, node_id, params, ctx):
                calls["execute"] += 1
                if child_delay:
                    import asyncio

                    await asyncio.sleep(child_delay)
                return result

        database = MagicMock()
        database.get_node_parameters = AsyncMock(return_value={"api_key": "k", "model": "m", "label": "child_agent"})
        database.save_node_output = AsyncMock(return_value=None)
        database.get_node_output_by_session = AsyncMock(return_value=None)

        broadcaster = MagicMock()
        broadcaster.update_node_status = AsyncMock(return_value=None)

        config = {
            "node_id": "child-1",
            "node_type": "aiAgent",
            "workflow_id": "wf-1",
            "parent_node_id": "vertex-1",
            "parameters": {"label": "child_agent"},
            "ai_service": MagicMock(),
            "database": database,
            "nodes": [],
            "edges": [],
        }
        return FakeAgentPlugin, broadcaster, config, calls

    def _patches(self, plugin_cls, broadcaster):
        return (
            patch("services.status_broadcaster.get_status_broadcaster", return_value=broadcaster),
            patch("services.node_registry.get_node_class", return_value=plugin_cls),
            patch("nodes.agent._events.broadcast_agent_task_completed", new=AsyncMock()),
            patch("nodes.agent._events.broadcast_agent_task_failed", new=AsyncMock()),
        )

    async def test_wait_returns_child_result_inline(self, _reset_registry):
        tools_mod = _reset_registry
        plugin_cls, broadcaster, config, calls = self._make_env()
        config["delegation_wait_seconds"] = 5

        p1, p2, p3, p4 = self._patches(plugin_cls, broadcaster)
        with p1, p2, p3, p4:
            result = await tools_mod._execute_delegated_agent({"task": "do it"}, config)

        assert result["status"] == "completed"
        assert result["success"] is True
        assert result["result"] == "hi"
        assert result["delegation_lifecycle"] is True
        assert calls["execute"] == 1
        assert tools_mod._active_delegations == {}

    async def test_wait_timeout_keeps_child_running(self, _reset_registry):
        import asyncio

        tools_mod = _reset_registry
        plugin_cls, broadcaster, config, calls = self._make_env(child_delay=0.3)
        config["delegation_wait_seconds"] = 0.05

        p1, p2, p3, p4 = self._patches(plugin_cls, broadcaster)
        with p1, p2, p3, p4:
            result = await tools_mod._execute_delegated_agent({"task": "slow"}, config)
            task_id = result["task_id"]

            assert result["status"] == "delegated"
            assert "STILL WORKING" in result["message"]
            assert "check_delegated_tasks" in result["message"]

            # The shield must NOT have cancelled the child: it completes
            # in the background and caches its result.
            await asyncio.sleep(0.5)

        assert tools_mod._delegation_results[task_id]["status"] == "completed"
        assert tools_mod._delegation_results[task_id]["result"] == "hi"

    async def test_already_delegated_with_wait_awaits_existing(self, _reset_registry):
        tools_mod = _reset_registry
        plugin_cls, broadcaster, config, calls = self._make_env(child_delay=0.2)
        config["delegation_wait_seconds"] = 0.05

        p1, p2, p3, p4 = self._patches(plugin_cls, broadcaster)
        with p1, p2, p3, p4:
            first = await tools_mod._execute_delegated_agent({"task": "same"}, config)
            assert first["status"] == "delegated"

            # Identical re-call while the child is in flight: md5 dedupe
            # converts it into "await the existing task".
            config["delegation_wait_seconds"] = 5
            second = await tools_mod._execute_delegated_agent({"task": "same"}, config)

        assert second["status"] == "completed"
        assert second["task_id"] == first["task_id"]
        assert second["result"] == "hi"
        assert calls["execute"] == 1

    async def test_no_wait_preserves_fire_and_forget(self, _reset_registry):
        import asyncio

        tools_mod = _reset_registry
        plugin_cls, broadcaster, config, calls = self._make_env()
        # No delegation_wait_seconds key at all.

        p1, p2, p3, p4 = self._patches(plugin_cls, broadcaster)
        with p1, p2, p3, p4:
            result = await tools_mod._execute_delegated_agent({"task": "bg"}, config)

            assert result["status"] == "delegated"
            assert "SUCCESS: Task delegated" in result["message"]
            assert "delegation_lifecycle" not in result
            # Child had no chance to run before the ack returned.
            assert calls["execute"] == 0

            await asyncio.sleep(0.05)  # let the background task finish

        assert calls["execute"] == 1

    async def test_execute_tool_no_success_broadcast_on_awaited_error(self, _reset_registry):
        tools_mod = _reset_registry
        plugin_cls, broadcaster, config, calls = self._make_env(child_result={"success": False, "error": "boom"})
        config["delegation_wait_seconds"] = 5

        p1, p2, p3, p4 = self._patches(plugin_cls, broadcaster)
        with p1, p2, p3, p4:
            result = await tools_mod.execute_tool("delegate_to_ai_agent", {"task": "fail"}, config)

        assert result["status"] == "error"
        assert result["error"] == "boom"
        # Marker consumed by execute_tool, not leaked to the LLM payload.
        assert "delegation_lifecycle" not in result
        # run_child_agent owns the child's terminal broadcast; execute_tool
        # must not stomp the error glow with a success broadcast.
        statuses = [call.args[1] for call in broadcaster.update_node_status.await_args_list]
        assert "error" in statuses
        assert "success" not in statuses
