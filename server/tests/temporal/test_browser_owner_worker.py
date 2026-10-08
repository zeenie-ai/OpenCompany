"""Owner worker startup, routing separation and partial-start cleanup."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.parametrize("owner_fails", [False, True])
async def test_owner_worker_is_required_without_general_pool_and_startup_failure_is_cleaned(monkeypatch, owner_fails):
    from core import container as container_module
    from services import browser_owners
    from services.temporal import lifecycle, worker
    events = []
    async def register(database):
        events.append("registered")
    async def start_main():
        events.append("main")
    async def start_owner():
        events.append("owner")
        if owner_fails:
            raise RuntimeError("owner construction failed")
    manager = SimpleNamespace(start=start_main, stop=AsyncMock(), _browser_pool=None)
    owner = SimpleNamespace(start=start_owner)
    wrapper = SimpleNamespace(worker_manager=None)
    fake_container = SimpleNamespace(database=lambda: object(),
        workflow_service=lambda: SimpleNamespace(set_temporal_executor=MagicMock()), temporal_client=lambda: wrapper)
    monkeypatch.setattr(container_module, "container", fake_container)
    monkeypatch.setattr(browser_owners, "register_browser_owner", register)
    monkeypatch.setattr(browser_owners, "replica_id", lambda: "replica")
    monkeypatch.setattr(worker, "TemporalWorkerManager", lambda **kwargs: manager)
    monkeypatch.setattr(worker, "BrowserOwnerWorkerPool", lambda *args: owner)
    settings = SimpleNamespace(temporal_task_queue="main", distributed_mode=True, temporal_worker_pool_enabled=False)
    state = SimpleNamespace()
    if owner_fails:
        with pytest.raises(RuntimeError, match="owner construction failed"):
            await lifecycle._start_execution_engine(object(), state, settings, lambda line: None)
        manager.stop.assert_awaited_once()
        assert wrapper.worker_manager is None
        assert not hasattr(state, "temporal_worker_manager")
    else:
        await lifecycle._start_execution_engine(object(), state, settings, lambda line: None)
        assert wrapper.worker_manager is manager
        assert manager._browser_pool is owner
        manager.stop.assert_not_awaited()
    assert events == ["registered", "main", "owner"]


async def test_stop_closes_partial_manager_session_before_worker_started():
    from services.temporal.worker import TemporalWorkerManager
    manager = TemporalWorkerManager(client=MagicMock())
    session = SimpleNamespace(closed=False, close=AsyncMock())
    manager._session = session
    await manager.stop()
    session.close.assert_awaited_once()
    assert manager._session is None


def test_owner_registration_uses_plugin_types_and_generic_workers_exclude_browser(monkeypatch):
    from services.temporal import worker, plugin_activities
    from services import node_registry, browser_owners
    from services.temporal.browser_task_activities import BROWSER_OWNER_ACTIVITIES
    settings = SimpleNamespace(distributed_mode=True)
    monkeypatch.setattr("core.config.Settings", lambda: settings)
    monkeypatch.setattr(node_registry, "registered_node_classes", lambda: {"browser": object(), "rest": object()})
    collect = MagicMock(return_value=["plugin"])
    monkeypatch.setattr(plugin_activities, "collect_plugin_activities", collect)
    assert worker._generic_plugin_activities() == ["plugin"]
    collect.assert_called_once_with(include_types=["rest"])
    collect.reset_mock()
    monkeypatch.setattr(browser_owners, "owner_queue", lambda identity: "physical-owner")
    monkeypatch.setattr(worker, "_graceful_shutdown_timeout", lambda: timedelta(seconds=1))
    monkeypatch.setattr(worker, "_worker_identity", lambda queue: "fixture")
    factory = MagicMock()
    monkeypatch.setattr(worker, "Worker", factory)
    pool = worker.BrowserOwnerWorkerPool(MagicMock(), "replica")
    pool._build_queue_worker("physical-owner")
    collect.assert_called_once_with(include_types=["browser"])
    assert factory.call_args.kwargs["task_queue"] == "physical-owner"
    assert factory.call_args.kwargs["activities"] == ["plugin", *BROWSER_OWNER_ACTIVITIES]
