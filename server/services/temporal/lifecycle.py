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
    owned = owns_dev_server(settings.temporal_server_address)
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

    # Bootstrap tenant clients BEFORE reconcile: the reconcile pass
    # re-arms durable generations (schedules, listeners, controllers) via
    # _client_for_namespace. With the old order (_boot_reconcile first)
    # tenant namespaces weren't registered yet, so the re-arm either
    # failed or silently landed in the default namespace (fail-open).
    await _bootstrap_tenant_clients(settings, log)
    await _boot_reconcile(log)
    if getattr(settings, "multi_tenant_namespaces", False) and getattr(settings, "temporal_tenant_worker_pool", False):
        await _start_tenant_workers(settings, log, app_state)
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


def _check_multi_tenant_worker_topology(settings: Settings) -> None:
    """Raise loudly when the multi-tenant and worker-pool flags are incompatible.

    MachinaWorkflow reads ``settings.temporal_worker_pool_enabled`` INSIDE
    the workflow body and records the queue choice in Temporal history.  A
    tenant namespace that has only the default-namespace pool worker would
    schedule plugin activities on queues nobody polls — workflows hang
    forever with no error.  Rather than let that happen silently, we refuse
    to start when the flags are in a dangerous combination.

    Safe combinations:
    - MULTI_TENANT_NAMESPACES=false (default): no constraint — single namespace.
    - MULTI_TENANT_NAMESPACES=true + TEMPORAL_WORKER_POOL_ENABLED=false: OK
      (no pool, so the manager worker on machina-tasks covers all activity
      types; the manager polls all queues when the pool is off).
    - MULTI_TENANT_NAMESPACES=true + TEMPORAL_WORKER_POOL_ENABLED=true
      + TEMPORAL_TENANT_WORKER_POOL=true: OK — a pool is started per namespace.

    Dangerous:
    - MULTI_TENANT_NAMESPACES=true + TEMPORAL_WORKER_POOL_ENABLED=true
      + TEMPORAL_TENANT_WORKER_POOL=false (= the trap).
    """
    if not getattr(settings, "multi_tenant_namespaces", False):
        return
    if not settings.temporal_worker_pool_enabled:
        return
    if getattr(settings, "temporal_tenant_worker_pool", False):
        return
    raise RuntimeError(
        "Incompatible Temporal worker topology for multi-tenant mode.\n\n"
        "MULTI_TENANT_NAMESPACES=true requires one worker pool per tenant "
        "namespace because MachinaWorkflow records queue choices in Temporal "
        "history.  A tenant namespace with no pool worker silently hangs "
        "every workflow.\n\n"
        "Fix: set TEMPORAL_TENANT_WORKER_POOL=true in .env.  This starts "
        "one TemporalWorkerManager + TemporalWorkerPool per ready tenant "
        "namespace using the per-namespace client registry.\n\n"
        "Alternative (single-worker mode): set TEMPORAL_WORKER_POOL_ENABLED=false "
        "to route all activity types through the manager worker on machina-tasks. "
        "This disables per-queue concurrency tuning but avoids the topology trap."
    )


async def _start_tenant_workers(
    settings: Settings,
    log: Callable[[str], None],
    app_state: Any,
) -> None:
    """Start one TemporalWorkerManager (+ optional pool) per ready tenant namespace.

    Called only when ``MULTI_TENANT_NAMESPACES=true`` and
    ``TEMPORAL_TENANT_WORKER_POOL=true``.  Uses the client registry
    populated by ``_bootstrap_tenant_clients``.  Per-namespace failures
    are logged but do not abort the other namespaces.
    """
    from services.temporal.client_registry import get_client_for_namespace, registered_namespaces
    from services.temporal.worker import TemporalWorkerManager

    default_ns = settings.temporal_namespace
    tenant_managers: list = getattr(app_state, "temporal_tenant_worker_managers", None) or []
    tenant_pools: list = getattr(app_state, "temporal_tenant_pools", None) or []

    for namespace in registered_namespaces():
        if namespace == default_ns:
            continue
        wrapper = get_client_for_namespace(namespace)
        if wrapper is None or wrapper.client is None:
            log(f"[Temporal] Skipping worker for {namespace!r}: client not ready")
            continue
        try:
            manager = TemporalWorkerManager(
                client=wrapper.client,
                task_queue=settings.temporal_task_queue,
            )
            await manager.start()
            tenant_managers.append(manager)
            log(f"[Temporal] Worker started for tenant namespace {namespace!r}")

            if settings.temporal_worker_pool_enabled:
                from services.temporal.worker import TemporalWorkerPool

                pool = TemporalWorkerPool(client=wrapper.client)
                await pool.start()
                tenant_pools.append(pool)
                log(f"[Temporal] Worker pool started for tenant namespace {namespace!r} ({len(pool.queues)} queues)")
        except Exception as exc:  # noqa: BLE001 — per-namespace isolation
            logger.warning(
                "Failed to start workers for tenant namespace",
                namespace=namespace,
                error=str(exc),
            )

    app_state.temporal_tenant_worker_managers = tenant_managers
    app_state.temporal_tenant_pools = tenant_pools


async def _start_execution_engine(
    client: Any,
    app_state: Any,
    settings: Settings,
    log: Callable[[str], None],
) -> None:
    """Wire the executor and start the worker manager (+ optional pool)."""
    # Validate the worker topology before starting anything.  Raises
    # RuntimeError if MULTI_TENANT_NAMESPACES=true and the flag combination
    # would silently hang tenant workflows.
    _check_multi_tenant_worker_topology(settings)

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
    await manager.start()
    app_state.temporal_worker_manager = manager

    # Wave 16: per-queue activity worker pool (default-on since 16.4;
    # TEMPORAL_WORKER_POOL_ENABLED=false is the rollback channel).
    # Starts AFTER the manager so workflow registration is in place
    # before specialised activity workers poll.
    if settings.temporal_worker_pool_enabled:
        from services.temporal.worker import TemporalWorkerPool

        pool = TemporalWorkerPool(client=client)
        await pool.start()
        app_state.temporal_pool = pool
        log(f"[Temporal] Worker pool started ({len(pool.queues)} queues)")


async def _bootstrap_tenant_clients(settings: Settings, log: Callable[[str], None]) -> None:
    """Connect a Temporal client for every ready tenant namespace.

    No-op when ``MULTI_TENANT_NAMESPACES=false``.  When true, reads the
    ``namespaces`` table (the authoritative registry introduced with the
    many-to-many namespace model) filtered to ``status=ready`` and
    ``temporal_provisioned=true``.  Falls back to ``tenant_namespaces``
    when ``list_namespaces`` is unavailable for backward compatibility.
    Failures per namespace are logged but do not block the remaining ones.

    Registered clients share the module-level Runtime in the registry so
    no extra SDK-level threads are spawned.
    """
    if not getattr(settings, "multi_tenant_namespaces", False):
        return

    from core.container import container
    from services.temporal.client_registry import register
    from services.tenancy import STATUS_READY

    database = container.database()
    default_ns = settings.temporal_namespace
    try:
        # Prefer the Namespace registry (many-to-many model); fall back to
        # the legacy TenantNamespace 1:1 table for older deployments.
        if hasattr(database, "list_namespaces"):
            all_rows = await database.list_namespaces(ready_only=True)
            rows = [
                {"namespace": r["namespace"]}
                for r in all_rows
                if r.get("temporal_provisioned") and r["namespace"] != default_ns
            ]
        else:
            legacy = await database.list_tenant_namespaces()
            rows = [r for r in legacy if r.get("status") == STATUS_READY and r.get("namespace") != default_ns]
    except Exception as exc:  # noqa: BLE001 — non-fatal; single-namespace fallback still works
        logger.warning("Failed to list tenant namespaces for Temporal bootstrap", error=str(exc))
        return

    ready_rows = rows
    if not ready_rows:
        return

    logger.info(
        "Bootstrapping tenant namespace Temporal clients",
        count=len(ready_rows),
        namespaces=[r["namespace"] for r in ready_rows],
    )
    log(f"[Temporal] Bootstrapping {len(ready_rows)} tenant namespace client(s)")
    for row in ready_rows:
        ns = row["namespace"]
        try:
            wrapper = await register(
                ns,
                server_address=settings.temporal_server_address,
                settings=settings,
            )
            if wrapper is None:
                log(f"[Temporal] Tenant namespace {ns!r} client failed to connect (will retry on access)")
                logger.warning("Tenant namespace client failed to connect", namespace=ns)
            else:
                log(f"[Temporal] Tenant namespace {ns!r} client ready")
                logger.info("Tenant namespace client ready", namespace=ns)
        except Exception as exc:  # noqa: BLE001 — per-namespace isolation
            logger.warning("Tenant namespace Temporal bootstrap failed", namespace=ns, error=str(exc))


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
