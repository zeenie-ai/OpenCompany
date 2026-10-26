"""An interrupted wait must be retried, not recorded as a failed node.

``BaseNode._execute_body`` turns every exception into an error envelope, so a
node that waits (the approval gate) and is interrupted because the worker is
shutting down used to *complete* as a failure. Temporal never retried it, the
run failed, and the circuit breaker counted it. ``NodeWaitInterrupted`` is
reported as its own ``error_type``, which the activity wrapper re-raises as a
retryable ``ApplicationError`` so the next attempt resumes the wait.

A cancel that reaches the activity stays a cancel; one caused by a worker
shutdown retries a call that is safe to repeat.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import nodes  # noqa: F401 -- populate plugin registry

from services.node_registry import get_node_class
from services.plugin.base import NODE_WAIT_INTERRUPTED, NodeWaitInterrupted


@pytest.mark.asyncio
async def test_execute_body_reports_an_interrupted_wait(harness, monkeypatch):
    cls = get_node_class("console")

    async def interrupted(self, op_spec, params_obj, context):
        raise NodeWaitInterrupted("worker shutting down")

    monkeypatch.setattr(cls, "_run_operation", interrupted)

    result = await harness.execute("console", {})

    assert result["success"] is False
    assert result["error_type"] == NODE_WAIT_INTERRUPTED
    assert result["error"] == "worker shutting down"


def _install_activity_fakes(monkeypatch, execute_result):
    from core.container import container

    workflow_service = MagicMock()
    workflow_service.execute_node = AsyncMock(return_value=execute_result)
    monkeypatch.setattr(container, "workflow_service", lambda: workflow_service)

    broadcaster = MagicMock()
    broadcaster.update_node_status = AsyncMock()
    broadcaster.update_node_output = AsyncMock()
    monkeypatch.setattr("services.status_broadcaster.get_status_broadcaster", lambda: broadcaster)
    return broadcaster


@pytest.mark.asyncio
async def test_activity_fails_retryably_when_the_wait_is_interrupted(monkeypatch):
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import ActivityEnvironment

    broadcaster = _install_activity_fakes(
        monkeypatch,
        {"success": False, "error": "worker shutting down", "error_type": NODE_WAIT_INTERRUPTED},
    )
    activity_fn = get_node_class("console").as_activity()

    with pytest.raises(ApplicationError) as info:
        await ActivityEnvironment().run(activity_fn, {"node_id": "console_1", "workflow_id": "wf-1", "node_data": {}})

    assert info.value.type == NODE_WAIT_INTERRUPTED
    assert info.value.non_retryable is False
    statuses = [call.args[1] for call in broadcaster.update_node_status.await_args_list]
    assert "error" not in statuses, "an interrupted wait is not an error the user should see"


async def _cut(monkeypatch, details):
    """A console call that waits until its activity is cancelled with
    ``details`` (console is safe to repeat)."""
    from core.container import container
    from temporalio.testing import ActivityEnvironment

    broadcaster = _install_activity_fakes(monkeypatch, {})
    started = asyncio.Event()

    async def execute_node(**_):
        started.set()
        await asyncio.sleep(3600)

    container.workflow_service().execute_node = execute_node
    env = ActivityEnvironment()
    task = asyncio.ensure_future(env.run(get_node_class("console").as_activity(), {"node_id": "console_1", "workflow_id": "wf-1", "node_data": {}}))
    await asyncio.wait_for(started.wait(), 5)
    env.cancel(cancellation_details=details)
    return task, broadcaster


@pytest.mark.asyncio
async def test_a_cancel_stays_a_cancel(monkeypatch):
    from temporalio.activity import ActivityCancellationDetails

    task, broadcaster = await _cut(monkeypatch, ActivityCancellationDetails(cancel_requested=True))
    with pytest.raises(asyncio.CancelledError):
        await task
    statuses = [call.args[1] for call in broadcaster.update_node_status.await_args_list]
    assert statuses[-1] == "error"


@pytest.mark.asyncio
async def test_a_worker_shutdown_retries_a_call_safe_to_repeat(monkeypatch):
    from temporalio.activity import ActivityCancellationDetails
    from temporalio.exceptions import ApplicationError

    task, broadcaster = await _cut(monkeypatch, ActivityCancellationDetails(worker_shutdown=True))
    with pytest.raises(ApplicationError) as info:
        await task
    assert info.value.type == NODE_WAIT_INTERRUPTED and info.value.non_retryable is False
    statuses = [call.args[1] for call in broadcaster.update_node_status.await_args_list]
    assert "error" not in statuses


@pytest.mark.asyncio
async def test_a_cancel_requested_while_the_worker_shuts_down_stays_a_cancel(monkeypatch):
    from temporalio.activity import ActivityCancellationDetails

    task, _ = await _cut(monkeypatch, ActivityCancellationDetails(cancel_requested=True, worker_shutdown=True))
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_ordinary_failures_still_complete_the_attempt(monkeypatch):
    from temporalio.testing import ActivityEnvironment

    broadcaster = _install_activity_fakes(
        monkeypatch,
        {"success": False, "error": "boom", "error_type": "RuntimeError"},
    )
    activity_fn = get_node_class("console").as_activity()

    result = await ActivityEnvironment().run(activity_fn, {"node_id": "console_1", "workflow_id": "wf-1", "node_data": {}})

    assert result["success"] is False
    statuses = [call.args[1] for call in broadcaster.update_node_status.await_args_list]
    assert "error" in statuses
