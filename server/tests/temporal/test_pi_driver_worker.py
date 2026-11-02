"""Pi driver capacity cannot consume model/tool or nested-child slots."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.parametrize("mode, expected", [("local", 1), ("cloud", 2), ("self_hosted", 2)])
def test_private_driver_capacity_defaults_and_positive_override(monkeypatch, mode, expected):
    from services.temporal import worker

    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(deployment_mode=mode))
    monkeypatch.delenv("TEMPORAL_CODING_PI_DRIVER_CONCURRENCY", raising=False)
    pool = worker.PiDriverWorkerPool(MagicMock())
    assert pool.queues == ["coding-pi-driver"]
    assert pool._concurrency_for(pool.QUEUE) == expected
    monkeypatch.setenv("TEMPORAL_CODING_PI_DRIVER_CONCURRENCY", "3")
    assert pool._concurrency_for(pool.QUEUE) == 3
    for invalid in ("0", "-1", "", "many", "1.5"):
        monkeypatch.setenv("TEMPORAL_CODING_PI_DRIVER_CONCURRENCY", invalid)
        with pytest.raises(ValueError, match="positive integer"):
            pool._concurrency_for(pool.QUEUE)


def test_private_worker_registers_only_maintenance_activities(monkeypatch):
    from services.temporal import pi_activities, worker

    driver = object()
    client = MagicMock()
    factory = MagicMock()
    collector = MagicMock(return_value=SimpleNamespace(activities=lambda: [driver]))
    monkeypatch.setattr(pi_activities, "PiDriverActivities", collector)
    monkeypatch.setattr(worker, "Worker", factory)
    monkeypatch.setattr(worker, "_graceful_shutdown_timeout", lambda: timedelta(seconds=1))
    monkeypatch.setattr(worker, "_worker_identity", lambda queue: queue)
    monkeypatch.setenv("TEMPORAL_CODING_PI_DRIVER_CONCURRENCY", "1")
    pool = worker.PiDriverWorkerPool(client)
    pool._build_queue_worker(pool.QUEUE)
    collector.assert_called_once_with(client)
    assert factory.call_args.kwargs["activities"] == [driver]
    assert factory.call_args.kwargs["task_queue"] == "coding-pi-driver"
    assert factory.call_args.kwargs["max_concurrent_activities"] == 1
    assert "workflows" not in factory.call_args.kwargs


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_driver_start", [False, True])
async def test_manager_owns_private_worker_and_rolls_back_failed_start(monkeypatch, fail_driver_start):
    from services.temporal import worker

    session = SimpleNamespace(closed=False, close=AsyncMock())
    pool = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    if fail_driver_start:
        pool.start.side_effect = RuntimeError("driver registration failed")
    monkeypatch.setattr(worker, "create_shared_session", AsyncMock(return_value=session))
    monkeypatch.setattr(worker, "PiDriverWorkerPool", lambda client: pool)
    manager = worker.TemporalWorkerManager(MagicMock())
    manager._build_worker = AsyncMock(return_value=object())

    async def resident():
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_run_worker", resident)
    if fail_driver_start:
        with pytest.raises(RuntimeError, match="driver registration failed"):
            await manager.start()
        assert not manager.is_running
        assert manager._session is None
    else:
        await manager.start()
        assert manager.is_running
        assert manager._pi_driver_pool is pool
        await manager.stop()
        assert not manager.is_running
    pool.start.assert_awaited_once()
    pool.stop.assert_awaited_once()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_custom_setup_can_construct_separate_driver_worker(monkeypatch):
    from services.temporal import worker

    candidate = object()
    build = MagicMock(return_value=candidate)
    monkeypatch.setattr(worker.PiDriverWorkerPool, "_build_queue_worker", build)
    assert await worker.create_pi_driver_worker(MagicMock()) is candidate
    build.assert_called_once_with("coding-pi-driver")


def test_virtual_workspace_leaf_is_registered_on_its_plugin_queue(monkeypatch):
    from services.temporal import agent_activities, plugin_activities, worker

    monkeypatch.setattr(plugin_activities, "collect_plugin_activities", lambda **kwargs: [])
    monkeypatch.setattr("services.node_registry.get_node_class", lambda kind: SimpleNamespace(task_queue="machina-default"))
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(distributed_mode=False, deployment_mode="local"))
    factory = MagicMock()
    monkeypatch.setattr(worker, "Worker", factory)
    monkeypatch.setattr(worker, "_graceful_shutdown_timeout", lambda: timedelta(seconds=1))
    monkeypatch.setattr(worker, "_worker_identity", lambda queue: queue)
    pool = worker.TemporalWorkerPool(MagicMock(), queues=["machina-default"])
    assert pool._build_queue_worker("machina-default") is not None
    assert factory.call_args.kwargs["activities"] == [agent_activities.coding_workspace_tool]
    assert pool._build_queue_worker("code-exec") is None


@pytest.mark.asyncio
async def test_standalone_bootstrap_wires_native_compaction_before_polling(monkeypatch):
    from core.container import container
    from services import compaction, model_registry
    from services.llm.protocol import LLMResponse, Message, Usage, message_to_wire
    from services.temporal import agent_activities, worker

    database = SimpleNamespace(startup=AsyncMock(),
        get_or_create_session_token_state=AsyncMock(return_value={"cumulative_total": 300, "compaction_count": 0}),
        save_token_metric=AsyncMock(), save_compaction_event=AsyncMock(), update_session_token_state=AsyncMock())
    unifier = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="Retain the current assignment",
        usage=Usage(input_tokens=2, output_tokens=1, total_tokens=3))))
    ai_service = SimpleNamespace(chat_unifier=unifier)
    monkeypatch.setattr(compaction, "_service", None)
    monkeypatch.setattr(container, "database", lambda: database)
    monkeypatch.setattr(container, "ai_service", lambda: ai_service)
    monkeypatch.setattr(container, "compaction_service", lambda: compaction.get_compaction_service() or
        compaction.init_compaction_service(database, SimpleNamespace(compaction_enabled=True)))
    monkeypatch.setattr(model_registry, "get_model_registry", lambda: SimpleNamespace(startup=lambda: None,
        get_max_output_tokens=lambda *_args: 4096))
    monkeypatch.setattr(worker, "create_runtime", lambda: None)
    monkeypatch.setattr(worker.Client, "connect", AsyncMock(return_value=object()))
    # A standalone worker runs only in distributed mode (1b.35).
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(distributed_mode=True))
    monkeypatch.setattr(agent_activities.activity, "heartbeat", lambda *_args: None)
    monkeypatch.setattr(agent_activities, "_resolve_activity_api_key", AsyncMock(return_value="owner-key"))

    class BeforePolling(Exception):
        pass

    async def check_compaction_then_stop(_pool_size):
        database.startup.assert_awaited_once()
        assert compaction.get_compaction_service() is not None
        result = await agent_activities.compact_context({"node_id": "coding", "provider": "openai", "model": "stub",
            "messages": [message_to_wire(Message(role="user", content="Continue the existing assignment. " * 10))]})
        assert result["success"] and "Retain the current assignment" in result["summary"]
        assert result["usage"]["total_tokens"] == 3
        call = unifier.chat.await_args.kwargs
        assert call["provider"] == "openai" and call["api_key"] == "owner-key"
        assert call["sdk_max_retries"] == 0
        database.save_compaction_event.assert_awaited_once()
        raise BeforePolling

    monkeypatch.setattr(worker, "create_shared_session", check_compaction_then_stop)
    with pytest.raises(BeforePolling):
        await worker.run_standalone_worker(server_address="test-only")
