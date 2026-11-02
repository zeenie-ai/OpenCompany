"""Replay-safe Temporal root routing and worker registration contracts."""

from __future__ import annotations

import inspect
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import nodes  # noqa: F401 -- populate the node registry


def _settings(
    *,
    agent: bool,
    per_type: bool,
    worker_pool: bool,
) -> SimpleNamespace:
    return SimpleNamespace(
        temporal_agent_workflow_enabled=agent,
        temporal_per_type_dispatch=per_type,
        temporal_worker_pool_enabled=worker_pool,
    )


def test_routing_snapshot_captures_all_command_shaping_flags():
    from services.temporal.executor import capture_temporal_routing_input
    from services.temporal.workflow import TEMPORAL_ROUTING_INPUT_KEY

    with patch(
        "core.config.Settings",
        side_effect=lambda: _settings(
            agent=False,
            per_type=True,
            worker_pool=True,
        ),
    ):
        value = capture_temporal_routing_input()

    assert value == {
        TEMPORAL_ROUTING_INPUT_KEY: {
            "version": 1,
            "agent_workflow_enabled": False,
            "per_type_dispatch_enabled": True,
            "worker_pool_enabled": True,
            "pi_harness_version": 1,
        }
    }


def test_unknown_routing_input_version_uses_deterministic_safe_defaults():
    from services.temporal.workflow import (
        TEMPORAL_ROUTING_INPUT_KEY,
        _frozen_routing_from_input,
    )

    value = _frozen_routing_from_input(
        {
            TEMPORAL_ROUTING_INPUT_KEY: {
                "version": 999,
                "agent_workflow_enabled": False,
                "per_type_dispatch_enabled": False,
                "worker_pool_enabled": True,
            }
        }
    )

    assert value == {
        "version": 1,
        "agent_workflow_enabled": True,
        "per_type_dispatch_enabled": True,
        "worker_pool_enabled": False,
    }


@pytest.mark.asyncio
async def test_executor_places_frozen_routing_in_new_root_input():
    from services.temporal.executor import TemporalExecutor
    from services.temporal.workflow import TEMPORAL_ROUTING_INPUT_KEY

    client = MagicMock()
    client.execute_workflow = AsyncMock(
        return_value={
            "success": True,
            "execution_trace": [],
            "outputs": {},
        }
    )
    executor = TemporalExecutor(client)

    with patch(
        "core.config.Settings",
        side_effect=lambda: _settings(
            agent=True,
            per_type=False,
            worker_pool=False,
        ),
    ):
        result = await executor.execute_workflow(
            workflow_id="wf-1",
            nodes=[],
            edges=[],
        )

    assert result["success"] is True
    payload = client.execute_workflow.await_args.args[1]
    assert payload["user_id"] == "owner"
    assert payload[TEMPORAL_ROUTING_INPUT_KEY] == {
        "version": 1,
        "agent_workflow_enabled": True,
        "per_type_dispatch_enabled": False,
        "worker_pool_enabled": False,
        "pi_harness_version": 1,
    }


def test_frozen_dispatch_never_reads_mutable_settings():
    from services.temporal.workflow import MachinaWorkflow

    workflow_instance = MachinaWorkflow()
    frozen = {
        "version": 1,
        "agent_workflow_enabled": False,
        "per_type_dispatch_enabled": True,
        "worker_pool_enabled": True,
    }
    with patch(
        "core.config.Settings",
        side_effect=AssertionError("workflow dispatch read live Settings"),
    ):
        dispatch = workflow_instance._resolve_dispatch(
            "pythonExecutor",
            routing_snapshot=frozen,
        )

    assert dispatch == {
        "kind": "activity",
        "name": "node.pythonExecutor.v1",
        "queue": "code-exec",
    }


def test_frozen_agent_dispatch_selects_context_protocol():
    from services.temporal.workflow import MachinaWorkflow

    dispatch = MachinaWorkflow()._resolve_dispatch(
        "aiAgent",
        graph_version=2,
        generation=3,
        context_enabled=True,
        routing_snapshot={
            "version": 1,
            "agent_workflow_enabled": True,
            "per_type_dispatch_enabled": True,
            "worker_pool_enabled": False,
        },
    )

    assert dispatch == {
        "kind": "child_workflow",
        "name": "AgentWorkflow",
    }




@pytest.mark.asyncio
async def test_create_worker_registers_plugin_and_pause_activities(
    monkeypatch,
):
    from services.temporal import agent_activities
    from services.temporal import plugin_activities
    from services.temporal import worker as worker_module
    from services.temporal.activities import (
        pause_workflow_on_failure_activity,
    )

    async def plugin_activity(_payload):
        return None

    monkeypatch.setattr(
        plugin_activities,
        "collect_plugin_activities",
        lambda: [plugin_activity],
    )
    monkeypatch.setattr(
        agent_activities,
        "collect_agent_activities",
        lambda: [],
    )
    monkeypatch.setattr(
        worker_module,
        "_graceful_shutdown_timeout",
        lambda: timedelta(seconds=1),
    )

    captured = {}

    def fake_worker(_client, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(worker_module, "Worker", fake_worker)
    session = MagicMock()
    await worker_module.create_worker(
        MagicMock(),
        session=session,
    )

    assert plugin_activity in captured["activities"]
    assert pause_workflow_on_failure_activity in captured["activities"]
    # The same registration as the manager's (1b.36): the cron workflow
    # arrives through the plugins.
    assert captured["plugins"] == worker_module.temporal_plugins()


@pytest.mark.asyncio
async def test_standalone_worker_registers_plugin_and_pause_activities(
    monkeypatch,
):
    from core import container as container_module
    from services import model_registry
    from services.temporal import agent_activities
    from services.temporal import plugin_activities
    from services.temporal import worker as worker_module
    from services.temporal.activities import (
        pause_workflow_on_failure_activity,
    )

    async def plugin_activity(_payload):
        return None

    # A standalone worker runs only in distributed mode (1b.35).
    monkeypatch.setattr("core.config.Settings", lambda: MagicMock(distributed_mode=True))
    monkeypatch.setattr(
        plugin_activities,
        "collect_plugin_activities",
        lambda **_kwargs: [plugin_activity],
    )
    monkeypatch.setattr(
        agent_activities,
        "collect_agent_activities",
        lambda: [],
    )
    monkeypatch.setattr(
        worker_module,
        "_graceful_shutdown_timeout",
        lambda: timedelta(seconds=1),
    )
    monkeypatch.setattr(worker_module, "create_runtime", lambda: MagicMock())
    monkeypatch.setattr(
        worker_module.Client,
        "connect",
        AsyncMock(return_value=MagicMock()),
    )

    session = MagicMock(closed=False)
    session.close = AsyncMock()
    monkeypatch.setattr(
        worker_module,
        "create_shared_session",
        AsyncMock(return_value=session),
    )
    registry = MagicMock()
    monkeypatch.setattr(
        model_registry,
        "get_model_registry",
        lambda: registry,
    )
    unifier = MagicMock()
    unifier.aclose = AsyncMock()
    database = MagicMock()
    database.startup = AsyncMock()
    monkeypatch.setattr(container_module.container, "database", lambda: database)
    monkeypatch.setattr(
        container_module.container,
        "chat_unifier",
        lambda: unifier,
    )

    captured = {}

    class _Worker:
        def __init__(self, _client, **kwargs):
            captured.update(kwargs)

        async def run(self):
            return None

    monkeypatch.setattr(worker_module, "Worker", _Worker)

    await worker_module.run_standalone_worker(
        server_address="temporal.test:7233",
    )

    assert plugin_activity in captured["activities"]
    assert pause_workflow_on_failure_activity in captured["activities"]
    assert captured["plugins"] == worker_module.temporal_plugins()
    session.close.assert_awaited_once()
    unifier.aclose.assert_awaited_once()
    database.startup.assert_awaited_once()


@pytest.mark.asyncio
async def test_the_standalone_worker_refuses_outside_distributed_mode(monkeypatch):
    # Without the backend's plugins and credentials it would fail the tasks it took (1b.35).
    from services.temporal import worker as worker_module

    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(distributed_mode=False))
    connect = AsyncMock()
    monkeypatch.setattr(worker_module.Client, "connect", connect)

    with pytest.raises(RuntimeError, match="DISTRIBUTED_MODE"):
        await worker_module.run_standalone_worker(server_address="temporal.test:7233")
    connect.assert_not_awaited()
