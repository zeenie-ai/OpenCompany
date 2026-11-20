"""Worker self-restart in TemporalWorkerManager / TemporalWorkerPool.

The Temporal worker shuts down on a transient poll failure rather than
auto-retrying, and the run task is detached (main.py's startup retry loop
has already returned). The SDK ``Worker`` is single-use — a second
``run()`` on the same instance raises ``RuntimeError("Already started")``
— so every restart attempt must REBUILD a fresh worker. The previous
restart loop re-ran the same instance, which meant one crash left the
task queue permanently unpolled while the loop logged "restarting"
forever; the previous version of this test masked that by mocking a
re-runnable worker.

Cancellation (from ``stop()``) always wins so shutdown is never delayed
by a restart. Backoff knobs come from Settings (env-driven); the conftest
stubs Settings, so we patch it to a SimpleNamespace with tiny real
backoffs.
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.temporal.worker import TemporalWorkerManager, TemporalWorkerPool


def _patch_backoff():
    return patch(
        "core.config.Settings",
        side_effect=lambda: SimpleNamespace(
            temporal_worker_restart_backoff_seconds=0.001,
            temporal_worker_restart_backoff_max_seconds=0.002,
        ),
    )


class _SingleUseWorker:
    """Mimics the SDK contract: a second run() on one instance raises."""

    def __init__(self, run_impl):
        self._run_impl = run_impl
        self._started = False

    async def run(self):
        if self._started:
            raise RuntimeError("Already started")
        self._started = True
        await self._run_impl()


def _manager_with_builds(run_impls) -> tuple[TemporalWorkerManager, dict]:
    """Manager whose _build_worker yields single-use workers in sequence."""
    mgr = TemporalWorkerManager(client=MagicMock(), task_queue="q")
    state = {"builds": 0, "runs": 0}
    impls = list(run_impls)

    async def _tracked(impl):
        state["runs"] += 1
        await impl()

    async def fake_build_worker():
        impl = impls[min(state["builds"], len(impls) - 1)]
        state["builds"] += 1
        return _SingleUseWorker(lambda impl=impl: _tracked(impl))

    mgr._build_worker = fake_build_worker  # type: ignore[method-assign]
    return mgr, state


async def test_worker_restarts_with_fresh_instance_after_transient_crash():
    async def crash():
        raise RuntimeError("transient poll failure")

    async def healthy():
        await asyncio.Event().wait()  # healthy run blocks

    mgr, state = _manager_with_builds([crash, healthy])
    mgr._worker = await mgr._build_worker()

    with _patch_backoff():
        task = asyncio.create_task(mgr._run_worker())
        await asyncio.sleep(0.05)  # allow crash + rebuild + restart
        # First build happened before the loop; the restart rebuilt once.
        assert state["builds"] == 2
        assert state["runs"] == 2
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_restart_never_reuses_the_single_use_instance():
    """Locks the regression the old mock hid: re-running the same SDK
    worker raises "Already started" instantly. The restart loop must
    rebuild before every retry, so the crashed instance is run once."""
    crashed = _SingleUseWorker(run_impl=None)

    async def crash():
        raise RuntimeError("transient poll failure")

    async def healthy():
        await asyncio.Event().wait()

    mgr, state = _manager_with_builds([healthy])
    crashed._run_impl = crash
    mgr._worker = crashed

    with _patch_backoff():
        task = asyncio.create_task(mgr._run_worker())
        await asyncio.sleep(0.05)
        # The crashed single-use instance was replaced, not re-run.
        assert mgr._worker is not crashed
        assert state["builds"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_cancellation_does_not_restart():
    calls = {"n": 0}

    async def healthy():
        calls["n"] += 1
        await asyncio.Event().wait()  # healthy run blocks until cancelled

    mgr, state = _manager_with_builds([healthy])
    mgr._worker = await mgr._build_worker()

    with _patch_backoff():
        task = asyncio.create_task(mgr._run_worker())
        await asyncio.sleep(0.02)
        assert calls["n"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert calls["n"] == 1  # cancellation wins; not restarted
    assert state["builds"] == 1  # and nothing was rebuilt


async def test_pool_queue_worker_restarts_with_fresh_instance():
    """Pool workers previously ran as bare worker.run() tasks with no
    restart machinery — a crash silently killed the only worker for the
    queue and its activities pended forever."""
    pool = TemporalWorkerPool(client=MagicMock(), queues=["code-exec"])
    state = {"builds": 0}

    async def crash():
        raise RuntimeError("poll failure")

    async def healthy():
        await asyncio.Event().wait()

    def fake_build(queue):
        impl = crash if state["builds"] == 0 else healthy
        state["builds"] += 1
        return _SingleUseWorker(impl)

    pool._build_queue_worker = fake_build  # type: ignore[method-assign]

    with _patch_backoff():
        first = pool._build_queue_worker("code-exec")
        task = asyncio.create_task(pool._run_queue_worker("code-exec", first))
        await asyncio.sleep(0.05)
        assert state["builds"] == 2  # crash triggered one rebuild
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_pool_builds_its_workers_off_the_event_loop():
    """Building a worker reads the settings files several times; nine in a
    row on the loop stalled every request for over a second at each boot."""
    pool = TemporalWorkerPool(client=MagicMock(), queues=["code-exec", "rest-api"])
    built_on: list[int] = []

    async def healthy():
        await asyncio.Event().wait()

    def fake_build(queue):
        built_on.append(threading.get_ident())
        return _SingleUseWorker(healthy)

    pool._build_queue_worker = fake_build  # type: ignore[method-assign]

    with _patch_backoff():
        await pool.start()
        try:
            await asyncio.sleep(0.01)
            assert len(built_on) == 2
            assert threading.get_ident() not in built_on
            assert pool.is_running
        finally:
            await pool.stop()


async def test_pool_queue_worker_stops_when_queue_has_no_activities():
    pool = TemporalWorkerPool(client=MagicMock(), queues=["code-exec"])

    async def crash():
        raise RuntimeError("poll failure")

    pool._build_queue_worker = lambda queue: None  # type: ignore[method-assign]

    with _patch_backoff():
        task = asyncio.create_task(
            pool._run_queue_worker("code-exec", _SingleUseWorker(crash))
        )
        # Crash, then rebuild returns None -> the supervisor task ends.
        await asyncio.wait_for(task, timeout=1.0)


def _startable_manager(monkeypatch):
    from services.temporal import worker

    session = SimpleNamespace(closed=False, close=AsyncMock())
    pool = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    monkeypatch.setattr(worker, "create_shared_session", AsyncMock(return_value=session))
    monkeypatch.setattr(worker, "PiDriverWorkerPool", lambda client: pool)
    manager = TemporalWorkerManager(client=MagicMock(), task_queue="q")
    manager._build_worker = AsyncMock(return_value=object())

    async def healthy():
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_run_worker", healthy)
    return manager, session, pool


async def test_worker_scans_locally_and_retries_legacy_retirement_independently(monkeypatch):
    from services.agent_instances import runtime

    manager, session, pool = _startable_manager(monkeypatch)
    scanning_while_retiring = asyncio.Event()
    retiring = asyncio.Event()
    retired = asyncio.Event()
    scanned_after_retirement = asyncio.Event()
    scans, retirements = [], []

    async def scan(*, client):
        scans.append(client)
        if len(scans) == 1:
            raise RuntimeError("database temporarily unavailable")
        if len(scans) == 2:
            return None  # Another worker holds the scan lease.
        if retiring.is_set():
            scanning_while_retiring.set()
        if retired.is_set():
            scanned_after_retirement.set()
        return 0

    async def retire(*, client):
        retirements.append((client, len(scans)))
        if len(retirements) == 1:
            raise RuntimeError("transient migration failure")
        retiring.set()
        await scanning_while_retiring.wait()
        retired.set()

    monkeypatch.setattr(runtime, "scheduled_reconcile_pending", scan)
    monkeypatch.setattr(runtime, "retire_legacy_reconciler", retire)
    monkeypatch.setattr(runtime, "RECONCILE_INTERVAL_SECONDS", 0.002)
    with _patch_backoff():
        await manager.start()
        worker_task = manager._worker_task
        try:
            await manager.start()
            assert manager._worker_task is worker_task
            await asyncio.wait_for(scanned_after_retirement.wait(), timeout=1)
            assert all(client is manager.client for client in scans)
            assert [client for client, _ in retirements] == [manager.client] * 2
            assert retirements[0][1] == 3
            manager.client.start_workflow.assert_not_called()
            assert manager.is_running
            manager._build_worker.assert_awaited_once()
            pool.start.assert_awaited_once()
        finally:
            await manager.stop()
    assert worker_task.done()
    session.close.assert_awaited_once()


@pytest.mark.parametrize("phase", ["scan", "scan_backoff", "sleep", "retirement"])
async def test_worker_stop_drains_maintenance(monkeypatch, phase):
    from services.agent_instances import runtime

    manager, session, pool = _startable_manager(monkeypatch)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    scans = []

    async def scan(*, client):
        scans.append(client)
        if phase == "retirement":
            return 0
        entered.set()
        if phase == "scan_backoff":
            raise RuntimeError("retry scan later")
        if phase == "sleep":
            return None
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def retire(*, client):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(runtime, "scheduled_reconcile_pending", scan)
    monkeypatch.setattr(runtime, "retire_legacy_reconciler", retire)
    monkeypatch.setattr("core.config.Settings", lambda: SimpleNamespace(
        temporal_worker_restart_backoff_seconds=60,
        temporal_worker_restart_backoff_max_seconds=60,
    ))
    if phase in {"scan", "retirement"}:
        async def draining_worker():
            try:
                await asyncio.Event().wait()
            finally:
                # A worker may await graceful shutdown. Recovery must be
                # cancelled concurrently, before that drain completes.
                await cancelled.wait()

        monkeypatch.setattr(manager, "_run_worker", draining_worker)
    await manager.start()
    worker_task = manager._worker_task
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
    finally:
        await asyncio.wait_for(manager.stop(), timeout=1)
    assert worker_task.done() and not manager.is_running
    assert scans == [manager.client]
    if phase in {"scan", "retirement"}:
        assert cancelled.is_set()
    pool.stop.assert_awaited_once()
    session.close.assert_awaited_once()
