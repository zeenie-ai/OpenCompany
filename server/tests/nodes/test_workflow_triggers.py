"""Contract tests for workflow_triggers nodes.

Covers: start, timer, cronScheduler, webhookTrigger, chatTrigger, taskTrigger,
webhookResponse.

Each handler is exercised through the full NodeExecutor dispatch via the
shared `harness` fixture. Event-waiter-based triggers (webhookTrigger,
chatTrigger, taskTrigger) are made non-blocking by patching the
`event_waiter` module attributes on the handler namespace so the canned
event payload resolves immediately.
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.nodes._mocks import patched_broadcaster


pytestmark = pytest.mark.node_contract


# ============================================================================
# Helpers
# ============================================================================


@contextmanager
def patched_trigger_waiter(
    canned_event: Optional[Dict[str, Any]] = None,
    *,
    raise_cancelled: bool = False,
) -> Iterator[MagicMock]:
    """Patch event_waiter at its source module for the plugin trigger path.

    Scaling-branch plugin TriggerNode.execute does:
        from services import event_waiter
        waiter = event_waiter.register(...)   # sync
        event_data = await waiter.future      # awaitable

    So `register` is a sync MagicMock and `waiter.future` is a pre-resolved
    asyncio.Future (or raises CancelledError if `raise_cancelled=True`).
    """
    import asyncio as _asyncio

    event_data = canned_event if canned_event is not None else {}

    loop = _asyncio.get_event_loop()
    future = loop.create_future()
    if raise_cancelled:
        future.set_exception(_asyncio.CancelledError())
    else:
        future.set_result(event_data)

    waiter_obj = MagicMock(name="Waiter", id="waiter-test-id")
    waiter_obj.future = future

    mock = MagicMock(name="event_waiter_module")
    mock.get_trigger_config = MagicMock(
        return_value=MagicMock(
            node_type="testTrigger",
            event_type="test_event",
            display_name="Test Trigger",
        )
    )
    mock.register = AsyncMock(return_value=waiter_obj)
    if raise_cancelled:
        mock.wait_for_event = AsyncMock(side_effect=_asyncio.CancelledError())
    else:
        mock.wait_for_event = AsyncMock(return_value=event_data)
    mock.get_backend_mode = MagicMock(return_value="memory")
    mock.is_trigger_node = MagicMock(return_value=True)

    with patch("services.event_waiter", mock):
        yield mock


# ============================================================================
# start
# ============================================================================


class TestStart:
    async def test_happy_path_returns_parsed_initial_data(self, harness):
        result = await harness.execute("start", {"initial_data": '{"message": "hi", "n": 7}'})
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload == {"message": "hi", "n": 7}

    async def test_invalid_json_silently_becomes_empty_dict(self, harness):
        # Documented behaviour: invalid JSON -> {} with success=True.
        result = await harness.execute("start", {"initial_data": "not json"})
        harness.assert_envelope(result, success=True)
        assert result["result"] == {}

    async def test_missing_initial_data_defaults_to_empty_dict(self, harness):
        result = await harness.execute("start", {})
        harness.assert_envelope(result, success=True)
        assert result["result"] == {}


# ============================================================================
# timer
# ============================================================================


class TestTimer:
    async def test_happy_path_short_sleep(self, harness):
        with patched_broadcaster() as broadcaster, patch("asyncio.sleep", new=AsyncMock()):
            result = await harness.execute("timer", {"duration": 1, "unit": "seconds"})
        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(
            result,
            ["timestamp", "elapsed_ms", "duration", "unit", "message"],
        )
        payload = result["result"]
        assert payload["duration"] == 1
        assert payload["unit"] == "seconds"
        assert "Timer completed" in payload["message"]
        # waiting broadcast fired exactly once before the sleep
        broadcaster.update_node_status.assert_called()
        first_call = broadcaster.update_node_status.call_args_list[0]
        # args: (node_id, status, data, ...)
        assert first_call.args[1] == "waiting"
        assert "wait_seconds" in first_call.args[2]

    async def test_unknown_unit_falls_back_to_raw_seconds(self, harness):
        # Post-refactor: unit is a Literal[...]; unknown value rejected by Pydantic.
        with patched_broadcaster():
            result = await harness.execute("timer", {"duration": 1, "unit": "fortnights"})
        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()

    async def test_non_numeric_duration_returns_error_envelope(self, harness):
        with patched_broadcaster():
            result = await harness.execute("timer", {"duration": "abc", "unit": "seconds"})
        harness.assert_envelope(result, success=False)


# ============================================================================
# cronScheduler
# ============================================================================


class TestCronScheduler:
    async def test_once_frequency_fires_immediately(self, harness):
        with patched_broadcaster() as broadcaster:
            result = await harness.execute("cronScheduler", {"frequency": "once"})
        harness.assert_envelope(result, success=True)
        harness.assert_output_shape(
            result,
            [
                "timestamp",
                "iteration",
                "frequency",
                "schedule",
                "waited_seconds",
                "message",
            ],
        )
        payload = result["result"]
        assert payload["frequency"] == "once"
        assert payload["iteration"] == 1
        assert payload["waited_seconds"] == 0
        # 'once' branch leaves next_run as None
        assert payload.get("next_run") is None
        broadcaster.update_node_status.assert_called()

    async def test_recurring_frequency_adds_next_run_and_schedule_string(self, harness):
        # Patch asyncio.sleep so the handler doesn't actually wait 30s.
        with patched_broadcaster(), patch("asyncio.sleep", new=AsyncMock()) as sleep_mock:
            result = await harness.execute("cronScheduler", {"frequency": "seconds", "interval": 30})
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["frequency"] == "seconds"
        assert payload["waited_seconds"] == 30
        # Recurring branch adds next_run + schedule description.
        assert payload["next_run"] == "Every 30 seconds"
        assert "will repeat" in payload["message"]
        sleep_mock.assert_awaited_once()

    async def test_unknown_frequency_rejected(self, harness):
        # Post-refactor: frequency is a Literal; unknown value rejected at Params level.
        with patched_broadcaster():
            result = await harness.execute("cronScheduler", {"frequency": "quantum"})
        harness.assert_envelope(result, success=False)
        assert "invalid parameters" in result["error"].lower()


# ============================================================================
# webhookTrigger
# ============================================================================


class TestWebhookTrigger:
    async def test_happy_path_returns_canned_event(self, harness):
        canned = {
            "method": "POST",
            "path": "my-hook",
            "headers": {"x-api-key": "tk"},
            "body": "{}",
            "json": {"hello": "world"},
        }
        with patched_trigger_waiter(canned), patched_broadcaster() as broadcaster:
            result = await harness.execute("webhookTrigger", {"path": "my-hook", "method": "POST"})
        harness.assert_envelope(result, success=True)
        assert result["result"] == canned

    async def test_unknown_trigger_type_returns_error(self, harness):
        # Force event_waiter.get_trigger_config to return None for this run.
        with patched_trigger_waiter() as ew, patched_broadcaster():
            ew.get_trigger_config.return_value = None
            result = await harness.execute("webhookTrigger", {"path": "x"})
        harness.assert_envelope(result, success=False)
        assert "Unknown trigger type" in result["error"]

    async def test_cancelled_returns_cancelled_by_user(self, harness):
        with patched_trigger_waiter(raise_cancelled=True), patched_broadcaster():
            result = await harness.execute("webhookTrigger", {"path": "x"})
        harness.assert_envelope(result, success=False)
        assert "cancel" in result["error"].lower()


# ============================================================================
# chatTrigger
# ============================================================================


class TestChatTrigger:
    async def test_happy_path_returns_canned_chat_event(self, harness):
        canned = {
            "message": "hello agent",
            "session_id": "default",
            "timestamp": "2026-04-15T10:00:00Z",
        }
        with patched_trigger_waiter(canned), patched_broadcaster():
            result = await harness.execute("chatTrigger", {"sessionId": "default"})
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["message"] == "hello agent"
        assert payload["session_id"] == "default"

    async def test_passes_params_to_register(self, harness):
        # Verify params are forwarded so build_chat_filter can apply sessionId.
        with patched_trigger_waiter({"message": "m"}) as ew, patched_broadcaster():
            await harness.execute("chatTrigger", {"sessionId": "alpha"})
        ew.register.assert_called_once()
        kwargs_params = ew.register.call_args.kwargs.get("params")
        assert kwargs_params.get("sessionId") == "alpha"

    async def test_wait_failure_returns_error_envelope(self, harness):
        import asyncio as _asyncio

        with patched_trigger_waiter() as ew, patched_broadcaster():
            # Plugin awaits waiter.future directly; replace it with a failing future.
            failing = _asyncio.get_event_loop().create_future()
            failing.set_exception(RuntimeError("boom"))
            ew.register.return_value.future = failing
            result = await harness.execute("chatTrigger", {"sessionId": "default"})
        harness.assert_envelope(result, success=False)
        assert "boom" in result["error"]


# ============================================================================
# taskTrigger
# ============================================================================


class TestTaskTrigger:
    async def test_happy_path_completed_task(self, harness):
        canned = {
            "task_id": "t-1",
            "status": "completed",
            "agent_name": "coding_agent",
            "agent_node_id": "node-child",
            "parent_node_id": "node-parent",
            "result": "done",
            "workflow_id": "wf-1",
        }
        with patched_trigger_waiter(canned), patched_broadcaster():
            result = await harness.execute("taskTrigger", {"status_filter": "completed"})
        harness.assert_envelope(result, success=True)
        assert result["result"]["status"] == "completed"
        assert result["result"]["task_id"] == "t-1"

    async def test_error_task_event_shape(self, harness):
        canned = {
            "task_id": "t-2",
            "status": "error",
            "agent_name": "web_agent",
            "agent_node_id": "node-child",
            "parent_node_id": "node-parent",
            "error": "something failed",
            "workflow_id": "wf-1",
        }
        with patched_trigger_waiter(canned), patched_broadcaster():
            result = await harness.execute("taskTrigger", {"status_filter": "error"})
        harness.assert_envelope(result, success=True)
        assert result["result"]["status"] == "error"
        assert result["result"]["error"] == "something failed"

    async def test_register_receives_all_filter_params(self, harness):
        with patched_trigger_waiter({"status": "completed"}) as ew, patched_broadcaster():
            await harness.execute(
                "taskTrigger",
                {
                    "task_id": "t-xyz",
                    "agent_name": "Twitter",
                    "status_filter": "completed",
                    "parent_node_id": "p-1",
                },
            )
        ew.register.assert_called_once()
        forwarded = ew.register.call_args.kwargs.get("params")
        assert forwarded["task_id"] == "t-xyz"
        assert forwarded["agent_name"] == "Twitter"
        assert forwarded["status_filter"] == "completed"
        assert forwarded["parent_node_id"] == "p-1"


# ============================================================================
# webhookResponse
# ============================================================================


@contextmanager
def _patched_resolve_webhook_response() -> Iterator[MagicMock]:
    """Stub `routers.webhook.resolve_webhook_response`.

    The handler does a lazy import inside the function body, so we stub the
    `routers.webhook` module in sys.modules before it is imported.
    """
    resolve = MagicMock(name="resolve_webhook_response", return_value=None)
    # Pre-install a stub module so `from routers.webhook import resolve_webhook_response`
    # inside the handler resolves to this mock without needing the real router
    # (which pulls FastAPI + dependency injection).
    routers_pkg = sys.modules.get("routers")
    if routers_pkg is None:
        routers_pkg = types.ModuleType("routers")
        routers_pkg.__path__ = []
        sys.modules["routers"] = routers_pkg
    webhook_mod = types.ModuleType("routers.webhook")
    webhook_mod.resolve_webhook_response = resolve
    sys.modules["routers.webhook"] = webhook_mod
    try:
        yield resolve
    finally:
        # Leave the stub in place for subsequent tests - re-installing is cheap.
        pass


class TestWebhookResponse:
    async def test_happy_path_with_static_body(self, harness):
        with _patched_resolve_webhook_response() as resolve:
            result = await harness.execute(
                "webhookResponse",
                {
                    "status_code": 201,
                    "body": '{"ok": true}',
                    "content_type": "application/json",
                },
            )
        harness.assert_envelope(result, success=True)
        payload = result["result"]
        assert payload["sent"] is True
        assert payload["statusCode"] == 201
        assert payload["contentType"] == "application/json"
        assert payload["bodyLength"] == len('{"ok": true}')
        resolve.assert_called_once()
        args = resolve.call_args.args
        # (node_id, response_dict)
        assert args[1]["statusCode"] == 201
        assert args[1]["body"] == '{"ok": true}'
        assert args[1]["contentType"] == "application/json"

    async def test_template_substitution_from_connected_outputs(self, harness):
        # Upstream httpRequest node outputs {"status": 200, "data": "hello"}.
        upstream_source = "src-node"
        upstream_output = {"status": 200, "data": "hello"}
        nodes = [
            {"id": upstream_source, "type": "httpRequest", "data": {}},
            {"id": "resp-node", "type": "webhookResponse", "data": {}},
        ]
        edges = [
            {
                "source": upstream_source,
                "target": "resp-node",
                "sourceHandle": "output-main",
                "targetHandle": "input-main",
            }
        ]

        # Pre-seed upstream output via harness upstream_outputs map.
        # NodeExecutor._get_connected_outputs reads output_main via get_output_fn.
        upstream_outputs = {
            f"{upstream_source}::output_main": upstream_output,
            upstream_source: upstream_output,
        }

        with _patched_resolve_webhook_response() as resolve:
            await harness.execute(
                "webhookResponse",
                {
                    "status_code": 200,
                    "body": "echo {{input.status}} and {{httpRequest.data}}",
                    "content_type": "text/plain",
                },
                node_id="resp-node",
                nodes=nodes,
                edges=edges,
                upstream_outputs=upstream_outputs,
            )
        resolve.assert_called_once()
        body = resolve.call_args.args[1]["body"]
        assert "200" in body
        assert "hello" in body

    async def test_empty_body_with_upstream_serialises_first_output(self, harness):
        upstream_source = "src-node"
        upstream_output = {"hello": "world"}
        nodes = [
            {"id": upstream_source, "type": "httpRequest", "data": {}},
            {"id": "resp-node", "type": "webhookResponse", "data": {}},
        ]
        edges = [
            {
                "source": upstream_source,
                "target": "resp-node",
                "sourceHandle": "output-main",
                "targetHandle": "input-main",
            }
        ]
        upstream_outputs = {
            f"{upstream_source}::output_main": upstream_output,
            upstream_source: upstream_output,
        }

        with _patched_resolve_webhook_response() as resolve:
            await harness.execute(
                "webhookResponse",
                {"status_code": 200, "body": "", "content_type": "application/json"},
                node_id="resp-node",
                nodes=nodes,
                edges=edges,
                upstream_outputs=upstream_outputs,
            )
        body = resolve.call_args.args[1]["body"]
        # JSON-serialised first output.
        assert '"hello"' in body and '"world"' in body

    async def test_non_numeric_status_code_returns_error_envelope(self, harness):
        with _patched_resolve_webhook_response():
            result = await harness.execute(
                "webhookResponse",
                {"status_code": "abc", "body": "x"},
            )
        harness.assert_envelope(result, success=False)
