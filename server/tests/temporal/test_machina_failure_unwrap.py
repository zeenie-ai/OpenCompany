"""MachinaWorkflow: typed activity failures keep the plugin's message.

Once ``BaseNode.as_activity`` raises ``ApplicationError`` for a structured
failure, ``_wait_any_complete`` receives an ``ActivityError`` whose
``str()`` is the SDK wrapper text. The orchestrator must unwrap the cause
so ``errors[]`` and the pause-on-failure reason carry what the plugin
said, and must schedule node activities with the plugin's effective
policy under the ``machina-plugin-failure-retries-v1`` patch while still
issuing the pre-patch command when the gate is closed.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest
from temporalio.exceptions import (
    ActivityError,
    ApplicationError,
    CancelledError,
    RetryState,
    TimeoutError as TemporalTimeoutError,
    TimeoutType,
)

import nodes  # noqa: F401 -- populate plugin registry
from services.temporal._failures import activity_failure_envelope
from services.temporal.workflow import PLUGIN_FAILURE_RETRY_PATCH, MachinaWorkflow

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _patch_workflow_logger(monkeypatch):
    from temporalio import workflow as temporal_workflow

    monkeypatch.setattr(temporal_workflow, "logger", MagicMock())


def _envelope(error: str = "credential expired", error_type: str = "NodeUserError", **extra: Any) -> Dict[str, Any]:
    return {"success": False, "error": error, "error_type": error_type, "retryable": False, "node_id": "py-1", **extra}


def _activity_error(cause: BaseException) -> ActivityError:
    err = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker-1",
        activity_type="node.pythonExecutor.v1",
        activity_id="py-1",
        retry_state=RetryState.MAXIMUM_ATTEMPTS_REACHED,
    )
    err.__cause__ = cause
    return err


class TestActivityFailureEnvelope:
    def test_details_envelope_is_returned_verbatim(self):
        envelope = _envelope(execution_time=0.2)
        failure = ApplicationError("credential expired", envelope, type="NodeUserError", non_retryable=True)
        result = activity_failure_envelope(_activity_error(failure))
        assert result["error"] == "credential expired"
        assert result["error_type"] == "NodeUserError"
        assert result["execution_time"] == 0.2
        assert result["success"] is False

    def test_message_only_cause_uses_message_and_type(self):
        result = activity_failure_envelope(_activity_error(ApplicationError("boom", type="RuntimeError")))
        assert result == {"success": False, "error": "boom", "error_type": "RuntimeError"}

    def test_plain_exception_falls_back_to_str(self):
        result = activity_failure_envelope(RuntimeError("boom"))
        assert result == {"success": False, "error": "boom", "error_type": "RuntimeError"}

    def test_timeout_cause_names_the_timeout_kind(self):
        timeout = TemporalTimeoutError("activity timed out", type=TimeoutType.START_TO_CLOSE, last_heartbeat_details=[])
        result = activity_failure_envelope(_activity_error(timeout))
        assert result["error_type"] == "TimeoutError"
        assert "START_TO_CLOSE" in result["error"]
        assert result["retryable"] is True

    def test_cancelled_cause(self):
        result = activity_failure_envelope(_activity_error(CancelledError("cancelled")))
        assert result["error_type"] == "Cancelled"
        assert result["retryable"] is False

    def test_never_returns_the_sdk_wrapper_text(self):
        result = activity_failure_envelope(_activity_error(ApplicationError("credential expired", _envelope(), type="NodeUserError")))
        assert result["error"] != "Activity task failed"


class TestWaitAnyComplete:
    async def test_unwraps_the_cause(self):
        fut = asyncio.get_running_loop().create_future()
        fut.set_exception(_activity_error(ApplicationError("credential expired", _envelope(), type="NodeUserError", non_retryable=True)))

        node_id, result = await MachinaWorkflow()._wait_any_complete({"py-1": fut})

        assert node_id == "py-1"
        assert result["success"] is False
        assert result["error"] == "credential expired"
        assert result["error_type"] == "NodeUserError"

    async def test_returns_successful_results_untouched(self):
        fut = asyncio.get_running_loop().create_future()
        fut.set_result({"success": True, "result": {"answer": 42}})
        _, result = await MachinaWorkflow()._wait_any_complete({"py-1": fut})
        assert result["result"] == {"answer": 42}


def _graph(*, pre_executed_trigger: bool = False):
    nodes_ = [
        {"id": "start-1", "type": "start", "data": {"label": "Start"}},
        {"id": "py-1", "type": "pythonExecutor", "data": {"label": "Code"}},
        {"id": "http-1", "type": "httpScraper", "data": {"label": "Fetch"}},
    ]
    edges = [
        {"id": "e0", "source": "start-1", "target": "py-1", "targetHandle": "input-main"},
        {"id": "e1", "source": "py-1", "target": "http-1", "targetHandle": "input-main"},
    ]
    if pre_executed_trigger:
        nodes_.insert(
            0,
            {
                "id": "t-1",
                "type": "webhookTrigger",
                "data": {"label": "Hook"},
                "_pre_executed": True,
                "_trigger_output": {"path": "/x"},
            },
        )
        edges.insert(0, {"id": "et", "source": "t-1", "target": "start-1", "targetHandle": "input-main"})
    return nodes_, edges


def _install_fakes(monkeypatch, *, failures: Dict[str, BaseException] | None = None):
    """Patch out dispatch; return ``(scheduled kwargs by node, execute_activity calls)``."""
    from temporalio import workflow as temporal_workflow

    failures = failures or {}
    scheduled: Dict[str, Dict[str, Any]] = {}
    executed: list = []

    def fake_start_activity(name, **kwargs):
        ctx = kwargs["args"][0]
        node_id = ctx["node_id"]
        scheduled[node_id] = kwargs
        fut = asyncio.get_event_loop().create_future()
        if node_id in failures:
            fut.set_exception(failures[node_id])
        else:
            fut.set_result({"success": True, "node_id": node_id, "result": {"answer": 42}})
        return fut

    async def fake_execute_activity(name, *args, **kwargs):
        executed.append((name, args[0] if args else kwargs.get("args", [None])[0]))
        return None

    monkeypatch.setattr(temporal_workflow, "start_activity", fake_start_activity)
    monkeypatch.setattr(temporal_workflow, "execute_activity", fake_execute_activity)
    monkeypatch.setattr(
        MachinaWorkflow,
        "_resolve_dispatch",
        lambda self, node_type, **_kwargs: {"kind": "activity", "name": f"node.{node_type}.v1", "queue": None},
    )
    return scheduled, executed


async def _run(graph, execution_id: str):
    nodes_, edges = graph
    return await asyncio.wait_for(
        MachinaWorkflow().run(
            {"nodes": nodes_, "edges": edges, "session_id": "test", "workflow_id": "wf-unwrap", "execution_id": execution_id}
        ),
        timeout=5.0,
    )


class TestRunSurfacesThePluginMessage:
    async def test_errors_list_carries_the_plugin_text(self, monkeypatch):
        from temporalio import workflow as temporal_workflow

        monkeypatch.setattr(temporal_workflow, "patched", lambda _pid: True)
        failure = _activity_error(ApplicationError("credential expired", _envelope(), type="NodeUserError", non_retryable=True))
        _install_fakes(monkeypatch, failures={"py-1": failure})

        result = await _run(_graph(), "run-unwrap-1")

        assert result["success"] is False
        assert result["errors"] == [{"node_id": "py-1", "error": "credential expired", "error_type": "NodeUserError"}]

    async def test_pause_reason_is_the_plugin_text_end_to_end(self, monkeypatch):
        from temporalio import workflow as temporal_workflow

        monkeypatch.setattr(temporal_workflow, "patched", lambda _pid: True)
        failure = _activity_error(ApplicationError("credential expired", _envelope(), type="NodeUserError", non_retryable=True))
        _, executed = _install_fakes(monkeypatch, failures={"py-1": failure})

        await _run(_graph(pre_executed_trigger=True), "run-unwrap-2")

        breaker = [payload for name, payload in executed if name == "workflow_control.pause_on_failure"]
        assert breaker, "a trigger-spawned failing run must schedule the circuit breaker"
        assert breaker[0]["reason"] == "credential expired"


class TestRetryPolicyResolution:
    def test_patch_marker_follows_the_established_naming(self):
        assert PLUGIN_FAILURE_RETRY_PATCH.startswith("machina-")
        assert PLUGIN_FAILURE_RETRY_PATCH == "machina-plugin-failure-retries-v1"

    async def test_patched_run_schedules_the_effective_policy(self, monkeypatch):
        from temporalio import workflow as temporal_workflow

        monkeypatch.setattr(temporal_workflow, "patched", lambda _pid: True)
        scheduled, _ = _install_fakes(monkeypatch)

        await _run(_graph(), "run-unwrap-3")

        assert scheduled["py-1"]["retry_policy"].maximum_attempts == 1, "pythonExecutor is destructive: one attempt"
        assert scheduled["http-1"]["retry_policy"].maximum_attempts == 3, "httpScraper is read-only: default attempts"
        assert "OutputValidationError" in scheduled["py-1"]["retry_policy"].non_retryable_error_types

    async def test_unpatched_history_keeps_the_class_policy(self, monkeypatch):
        """Determinism guard: histories recorded before the patch were
        scheduled with ``cls.retry_policy`` and must replay identically."""
        from temporalio import workflow as temporal_workflow

        monkeypatch.setattr(temporal_workflow, "patched", lambda _pid: False)
        scheduled, _ = _install_fakes(monkeypatch)

        await _run(_graph(), "run-unwrap-4")

        assert scheduled["py-1"]["retry_policy"].maximum_attempts == 3
        assert scheduled["http-1"]["retry_policy"].maximum_attempts == 3
