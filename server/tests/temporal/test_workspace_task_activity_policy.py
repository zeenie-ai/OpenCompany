"""Actual native-agent leaf scheduling, without a Temporal server."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from services.plugin.scaling import RetryPolicy
from services.temporal import agent_workflow as module
from temporalio.workflow import ActivityCancellationType


@pytest.mark.parametrize("pool", [False, True])
async def test_workspace_task_honors_plugin_policy_and_authenticated_principal(monkeypatch, pool):
    plugin = SimpleNamespace(
        workspace_task=True,
        start_to_close_timeout=timedelta(hours=3),
        heartbeat_timeout=timedelta(seconds=75),
        retry_policy=RetryPolicy(maximum_attempts=1),
        task_queue="device-pool",
    )
    monkeypatch.setattr(module, "get_node_class", lambda _: plugin)
    patched = Mock(return_value=True)
    execute = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(module.workflow, "patched", patched)
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    tool_payload = {"node_type": "embedded_task", "node_id": "child", "user_id": "untrusted", "nodes": [{"id": "model"}]}
    result = await module._execute_plugin_tool_activity(
        "node.embedded_task.v1", "call-1", tool_payload,
        {"user_id": "authenticated-owner", "temporal_worker_pool_enabled": pool, "generation": 7, "workflow_id": "wf"}
    )
    patched.assert_called_once_with("workspace-task-activity-policy-v1")
    assert result == {"success": True}
    kwargs = execute.await_args.kwargs
    assert kwargs["start_to_close_timeout"] == timedelta(hours=3)
    assert kwargs["heartbeat_timeout"] == timedelta(seconds=75)
    assert kwargs["retry_policy"].maximum_attempts == 1
    assert kwargs["cancellation_type"] == ActivityCancellationType.WAIT_CANCELLATION_COMPLETED
    assert kwargs.get("task_queue") == ("device-pool" if pool else None)
    assert kwargs["args"][0]["user_id"] == "authenticated-owner"
    assert kwargs["args"][0]["generation"] == 7
    assert kwargs["args"][0]["nodes"] == [{"id": "model"}]
    assert tool_payload["user_id"] == "untrusted"  # caller input is not mutated


def declared(**attributes):
    """A plugin class as the registry holds one: it declares its options."""
    return SimpleNamespace(
        start_to_close_timeout=timedelta(minutes=4),
        heartbeat_timeout=timedelta(seconds=40),
        retry_policy=RetryPolicy(maximum_attempts=2),
        **attributes,
    )


async def test_prepatch_workspace_history_keeps_the_legacy_command(monkeypatch):
    # Before the patch, no workspace policy: the default cancellation, the
    # default queue and the payload as it was.
    plugin = declared(workspace_task=True)
    monkeypatch.setattr(module, "get_node_class", lambda _: plugin)
    monkeypatch.setattr(module.workflow, "patched", Mock(return_value=False))
    execute = AsyncMock()
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    payload = {"node_type": "embedded_task", "node_id": "child"}
    await module._execute_plugin_tool_activity(
        "node.embedded_task.v1", "old-id", payload, {"user_id": "42", "temporal_worker_pool_enabled": True}
    )
    execute.assert_awaited_once_with(
        "node.embedded_task.v1",
        args=[payload],
        activity_id="old-id",
        start_to_close_timeout=timedelta(minutes=4),
        heartbeat_timeout=timedelta(seconds=40),
        retry_policy=plugin.retry_policy.to_temporal(),
    )
    assert "user_id" not in payload


async def test_ordinary_tools_retry_as_declared_without_a_patch_marker(monkeypatch):
    # A manual run's tool call never retries without limit (1b.3). Options
    # only, so nothing in history changes.
    plugin = declared(workspace_task=False)
    monkeypatch.setattr(module, "get_node_class", lambda _: plugin)
    patched = Mock(side_effect=AssertionError("Ordinary tool must not change history"))
    monkeypatch.setattr(module.workflow, "patched", patched)
    execute = AsyncMock()
    monkeypatch.setattr(module.workflow, "execute_activity", execute)
    payload = {"node_type": "ordinary_tool"}
    await module._execute_plugin_tool_activity("node.ordinary_tool.v1", "ordinary-id", payload, {})
    patched.assert_not_called()
    execute.assert_awaited_once_with(
        "node.ordinary_tool.v1",
        args=[payload],
        activity_id="ordinary-id",
        start_to_close_timeout=timedelta(minutes=4),
        heartbeat_timeout=timedelta(seconds=40),
        retry_policy=plugin.retry_policy.to_temporal(),
    )


def test_a_type_no_plugin_registers_takes_the_base_node_options(monkeypatch):
    from services.plugin.base import BaseNode

    monkeypatch.setattr(module, "get_node_class", lambda _: None)
    assert module._declared_tool_options("_builtin_skill") == {
        "start_to_close_timeout": BaseNode.start_to_close_timeout,
        "heartbeat_timeout": BaseNode.heartbeat_timeout,
        "retry_policy": BaseNode.retry_policy.to_temporal(),
    }
