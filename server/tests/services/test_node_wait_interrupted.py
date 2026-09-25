"""An interrupted wait must be retried, not recorded as a failed node.

``BaseNode._execute_body`` turns every exception into an error envelope, so a
node that waits (the approval gate) and is interrupted because the worker is
shutting down used to *complete* as a failure. Temporal never retried it, the
run failed, and the circuit breaker counted it. ``NodeWaitInterrupted`` is
reported as its own ``error_type``, which the activity wrapper re-raises as a
retryable ``ApplicationError`` so the next attempt resumes the wait.
"""

from __future__ import annotations

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
