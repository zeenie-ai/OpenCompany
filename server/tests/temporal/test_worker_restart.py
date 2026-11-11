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
from unittest.mock import MagicMock, patch

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
