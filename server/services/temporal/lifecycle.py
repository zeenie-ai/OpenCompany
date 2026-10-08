"""Temporal lifecycle owner: bootstrap, wiring, and resident supervision.

``main.py``'s lifespan schedules exactly one background task —
:func:`run_temporal_lifecycle` — and this module owns the entire
Temporal runtime story for the process:

1. Dev-server supervision (loopback deployments only) through the
   :class:`~services.temporal._runtime.TemporalServerRuntime`
   ``BaseSupervisor`` singleton, registered with the supervisor
   registry so ``shutdown_all_supervisors()`` reaches it at teardown.
2. Client connect loop — retries forever on a fixed cadence; the
   backend serves HTTP immediately while this converges.
3. The debug-only startup terminate sweep (config-gated, default off,
   vetoed by any active durable workflow control).
4. Execution-engine wiring: ``TemporalExecutor`` into the workflow
   service, then ``TemporalWorkerManager`` + ``TemporalWorkerPool``.
5. Boot-time reconcile of durable workflow-control generations so
   running/paused deployments survive a backend restart.
6. A resident dev-server watchdog: a child that dies or wedges while
   the backend stays up is respawned, because deployed workflows must
   keep executing for months without operator attention.

Remote/production clusters are never spawned at, never restarted, and
never swept beyond the config-gated sweep — only loopback addresses
are treated as backend-owned.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Optional

from core.config import Settings
from core.logging import get_logger

logger = get_logger(__name__)

# Cadence of the connect loop while Temporal is still unreachable.
_CONNECT_RETRY_SECONDS = 3.0

# Consecutive failed gRPC health probes before the watchdog restarts a
# supervisor-owned dev-server child that still holds the port ("wedged").
_WATCHDOG_UNHEALTHY_THRESHOLD = 4

# No-op default so ``startup_log`` is always callable.
_NULL_LOG: Callable[[str], None] = lambda line: None  # noqa: E731


def owns_dev_server(server_address: str) -> bool:
    """True when ``server_address`` is loopback — this process owns the
    SQLite dev server. Remote/production clusters are never spawned at."""
    host = server_address.rsplit(":", 1)[0].strip("[]")
    return host in ("localhost", "127.0.0.1", "::1")


async def run_temporal_lifecycle(
    app_state: Any,
    settings: Settings,
    startup_log: Optional[Callable[[str], None]] = None,
) -> None:
    """Connect, wire the execution engine, then stay resident supervising.

    Runs as one background task for the process lifetime (cancelled by
    the lifespan at shutdown). Retries the connect until it succeeds;
    after success it reconciles durable workflow controls and — for
    backend-owned (loopback) deployments — remains alive as the
    dev-server watchdog.
    """
    log = startup_log or _NULL_LOG
    from core.container import container

    wrapper = container.temporal_client()
    distributed = getattr(settings, "distributed_mode", False)
    if distributed and owns_dev_server(settings.temporal_server_address):
        raise ValueError("Distributed deployment requires an external shared Temporal service")
    owned = owns_dev_server(settings.temporal_server_address) and not distributed
    if owned:
        # Same BaseSupervisor singleton pattern as the WhatsApp and
        # JS-executor runtimes; registering makes lifespan teardown
        # (shutdown_all_supervisors) actually stop the child.
        from services._supervisor import register_supervisor
        from services.temporal._runtime import get_temporal_server_runtime

        register_supervisor(get_temporal_server_runtime())

    attempt = 0
    while True:
        attempt += 1
        if owned:
            _set_phase(app_state, "installing_temporal" if not _temporal_binary_present() else "starting_temporal")
            await _ensure_dev_server(attempt, log)
        _set_phase(app_state, "connecting")
        client = await wrapper.connect(retries=1, delay=0)
        if client is None:
            # Surface every failed attempt to stdout so users can see the
            # retry loop is alive when "Temporal is up" but the Python
            # client can't connect (server-up != client-up).
            log(
                f"[Temporal] Connect attempt {attempt} failed for "
                f"{settings.temporal_server_address} (ns={settings.temporal_namespace}); "
                f"retrying in {_CONNECT_RETRY_SECONDS:g}s"
            )
        else:
            try:
                _set_phase(app_state, "starting_workers")
                await _startup_sweep(wrapper, settings, log)
                await _start_execution_engine(client, app_state, settings, log)
                _set_phase(app_state, "ready")
                log(f"[Temporal] Worker started, execution engine ready (attempt {attempt})")
                logger.info(
                    "Temporal integration initialized successfully",
                    attempts=attempt,
                )
                break
            except Exception as exc:  # noqa: BLE001 — loop retries
                log(f"[Temporal] Executor/worker setup failed (attempt {attempt}): {exc}; will retry")
                logger.error(
                    "Temporal executor/worker setup failed; will retry",
                    error=str(exc),
                )
                # Drop the client so the next iteration reconnects cleanly.
                await wrapper.disconnect()
        await asyncio.sleep(_CONNECT_RETRY_SECONDS)

    await _boot_reconcile(log)
    if owned:
        await _watch_dev_server(wrapper, settings)


def _set_phase(app_state: Any, phase: str) -> None:
    """Record coarse progress for ``/health/ready`` (read by the desktop splash)."""
    try:
        app_state.temporal_phase = phase
    except Exception:  # noqa: BLE001 — app_state may be a bare object in tests
        pass


def _temporal_binary_present() -> bool:
    """True when the pooch-managed ``temporal`` CLI is already on disk.

    Distinguishes a first-run download (~114 MB, tens of seconds) from a
    plain spawn so the readiness phase can say which one is happening.
    """
    try:
        from core.paths import package_dir

        root = package_dir("temporal")
        if not root.is_dir():
            return False
        return any(p.is_file() and p.name.startswith("temporal") for p in root.rglob("temporal*"))
    except Exception:  # noqa: BLE001 — never let a probe break the connect loop
        return True


async def _ensure_dev_server(attempt: int, log: Callable[[str], None]) -> None:
    """Probe-or-spawn the backend-owned dev server (idempotent)."""
    from services.temporal._runtime import get_temporal_server_runtime

    try:
        await get_temporal_server_runtime().ensure_started()
    except Exception as exc:  # noqa: BLE001 — connect loop retries
        log(f"[Temporal] Dev server start failed (attempt {attempt}): {exc}")


async def _startup_sweep(
    wrapper: Any,
    settings: Settings,
    log: Callable[[str], None],
) -> None:
    """Debug-only escape hatch (default false): terminate Running workflows.

    Running and paused deployments must survive backend restarts —
    durable workflow-control generations are reconciled after the
    workers start. When the sweep IS enabled, any active control row
    still vetoes it so a live deployment is never killed.
    """
    if not settings.temporal_terminate_running_on_startup:
        return
    from core.container import container

    try:
        if await container.database().has_active_workflow_controls():
            logger.info("Skipping startup Temporal termination sweep; durable workflow controls are active")
            return
        terminated = await wrapper.terminate_running_workflows()
        if terminated:
            log(f"[Temporal] Terminated {terminated} running workflow(s) at startup (history preserved)")
    except Exception as exc:  # noqa: BLE001 — non-fatal
        logger.warning(f"Startup terminate-running sweep failed: {exc}")


async def _start_execution_engine(
    client: Any,
    app_state: Any,
    settings: Settings,
    log: Callable[[str], None],
) -> None:
    """Wire the executor and start the worker manager (+ optional pool)."""
    from core.container import container
    from services.temporal import TemporalExecutor
    from services.temporal.worker import TemporalWorkerManager

    executor = TemporalExecutor(
        client=client,
        task_queue=settings.temporal_task_queue,
    )
    container.workflow_service().set_temporal_executor(executor)

    manager = TemporalWorkerManager(
        client=client,
        task_queue=settings.temporal_task_queue,
    )
    if getattr(settings, "distributed_mode", False):
        from services.browser_owners import register_browser_owner, replica_id
        from services.temporal.worker import BrowserOwnerWorkerPool
        await register_browser_owner(container.database())
    pool = None
    try:
        await manager.start()
        if getattr(settings, "distributed_mode", False):
            manager._browser_pool = BrowserOwnerWorkerPool(client, replica_id())
            await manager._browser_pool.start()

        # Start specialized queues after framework workflow registration.
        # The Browser owner above is mandatory regardless of this flag.
        if settings.temporal_worker_pool_enabled:
            from services.temporal.worker import TemporalWorkerPool
            pool = TemporalWorkerPool(client=client)
            await pool.start()
    except BaseException:
        # The outer lifecycle retries startup. Do not leave workers polling
        # an abandoned client if one of the later pool constructions fails.
        if pool is not None:
            await pool.stop()
        await manager.stop()
        raise
    app_state.temporal_worker_manager = manager
    container.temporal_client().worker_manager = manager
    if pool is not None:
        app_state.temporal_pool = pool
        log(f"[Temporal] Worker pool started ({len(pool.queues)} queues)")


async def _boot_reconcile(log: Callable[[str], None]) -> None:
    """Converge durable control rows left transitional by a crash/restart.

    Non-fatal — every status/pause/resume request also reconciles
    lazily; this pass just closes the unattended-server window where
    durable intent and runtime behaviour could diverge indefinitely.
    """
    try:
        from services.deployment.handlers import reconcile_active_controls_on_boot

        count = await reconcile_active_controls_on_boot()
        if count:
            log(f"[Temporal] Reconciled {count} active workflow control(s)")
    except Exception as exc:  # noqa: BLE001 — non-fatal
        logger.warning(f"Boot-time workflow-control reconcile failed: {exc}")


async def _watch_dev_server(wrapper: Any, settings: Settings) -> None:
    """Resident watchdog for the backend-owned dev server.

    Deployed workflows must keep executing for months, so a dev-server
    child that dies (or wedges) while the backend stays up is respawned
    here — previously nothing probed it again after startup and every
    deployment froze until an operator intervened.
    """
    from services.temporal._runtime import get_temporal_server_runtime

    runtime = get_temporal_server_runtime()
    interval = settings.temporal_health_monitor_interval_seconds
    unhealthy = 0
    while True:
        await asyncio.sleep(interval)
        try:
            # Respawn if the child died and freed the port; no-op while
            # anything is listening on it.
            await runtime.ensure_started()
            if await wrapper.check_health():
                unhealthy = 0
                continue
            unhealthy += 1
            if unhealthy < _WATCHDOG_UNHEALTHY_THRESHOLD:
                continue
            # Port bound but gRPC not SERVING. Restart a wedged child we
            # own; an orphan we didn't spawn is only reported.
            if runtime.is_running():
                logger.warning(
                    f"Temporal dev server unhealthy for {unhealthy} consecutive probes; restarting owned child"
                )
                await runtime.stop()
                await runtime.start()
            else:
                logger.warning(
                    "Temporal gRPC port is bound but not SERVING and the process "
                    "is not supervisor-owned; cannot restart it safely"
                )
            unhealthy = 0
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — keep the watchdog alive
            logger.warning(f"Temporal health monitor iteration failed: {exc}")


__all__ = [
    "owns_dev_server",
    "run_temporal_lifecycle",
]
