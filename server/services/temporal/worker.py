"""Temporal worker for distributed node execution.

Uses class-based activities with shared aiohttp session for proper
connection pooling across concurrent activity executions.

The worker polls the task queue and executes:
- MachinaWorkflow: Orchestrates the graph, schedules node activities
- NodeExecutionActivities: Executes individual nodes with shared session

Multiple workers can be started on different machines for horizontal scaling.
Each node activity can execute on any available worker in the cluster.

References:
- https://docs.temporal.io/develop/python/python-sdk-sync-vs-async
- https://docs.temporal.io/develop/worker-performance
"""

import asyncio
from datetime import timedelta
from typing import Optional

import aiohttp
from opentelemetry import trace
from temporalio.contrib.opentelemetry import TracingInterceptor
from temporalio.client import Client
from temporalio.runtime import LoggingConfig, Runtime, TelemetryConfig
from temporalio.worker import PollerBehaviorAutoscaling, Worker

from core.logging import get_logger
from ._interceptors import ObservabilityWorkerInterceptor
from .workflow import MachinaWorkflow
from .trigger_listener_workflow import TriggerListenerWorkflow
from .polling_trigger_workflow import PollingTriggerWorkflow
from .workflow_control_workflow import WorkflowControlWorkflow
from .plugin_registry import temporal_plugins
from .activities import (
    NodeExecutionActivities,
    create_shared_session,
)



def _framework_workflows() -> list:
    """The framework workflow classes every worker must register.

    Single source of truth: the three worker constructions below used to
    hand-maintain identical copies of this list, which is how they drifted.
    Imports stay lazy — the workflow modules pull in the plugin registry.
    """
    from services.temporal.workflow import MachinaWorkflow
    from services.temporal.agent_workflow import AgentWorkflow, DelegatedTaskWorkflow
    from services.temporal.trigger_listener_workflow import TriggerListenerWorkflow
    from services.temporal.polling_trigger_workflow import PollingTriggerWorkflow
    from services.temporal.workflow_control_workflow import WorkflowControlWorkflow

    return [
        MachinaWorkflow,
        AgentWorkflow,
        DelegatedTaskWorkflow,
        TriggerListenerWorkflow,
        PollingTriggerWorkflow,
        WorkflowControlWorkflow,
    ]


logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)


def _graceful_shutdown_timeout() -> timedelta:
    """Wave 12 A3: SIGTERM grace window for Temporal workers.

    Read from ``Settings.temporal_graceful_shutdown_seconds`` at the
    moment each Worker is instantiated (rather than module load) so
    test fixtures + env reloads pick up overrides.
    """
    from core.config import Settings

    return timedelta(seconds=Settings().temporal_graceful_shutdown_seconds)


def _worker_identity(queue: str) -> str:
    """Wave 17.4: explicit worker identity for Temporal Web UI ops.

    Default SDK identity is ``<pid>@<hostname>`` which makes the
    Workers tab unreadable once the per-queue pool runs 9 workers on
    one host. ``opencompany-<queue>-<deployment_mode>`` names the role and
    the topology at a glance.
    """
    from core.config import Settings

    return f"opencompany-{queue}-{Settings().deployment_mode}"


def _sticky_cache_size() -> int:
    """Wave 18.2: sticky workflow cache budget by deployment mode.

    Cached workflow executions skip Event-History replay on the next
    workflow task (docs.temporal.io/develop/worker-performance). Cache
    entries hold the workflow's Python state in memory, so the budget
    tracks host RAM: laptops get a small cache, always-on boxes a big
    one. Evictions surface as ``sticky_cache_evictions`` + replay cost,
    not errors — sizing is a latency knob, not a correctness one.
    """
    from core.config import Settings

    return {"local": 50, "cloud": 500, "self_hosted": 100}[Settings().deployment_mode]


def create_runtime() -> Runtime:
    """Create a Temporal runtime with worker heartbeating disabled.

    Disables the runtime-level worker heartbeating feature to avoid
    the warning on older Temporal server versions that don't support it.
    """
    return Runtime(
        telemetry=TelemetryConfig(
            logging=LoggingConfig(filter="ERROR"),
        ),
        worker_heartbeat_interval=None,  # Disable runtime heartbeating
    )


class TemporalWorkerManager:
    """Manages the Temporal worker lifecycle with shared resources.

    Creates a shared aiohttp.ClientSession that is passed to the activity
    class, following Temporal's recommended dependency injection pattern.
    """

    def __init__(
        self,
        client: Client,
        task_queue: str = "machina-tasks",
        pool_size: int = 100,
    ):
        """Initialize the worker manager.

        Args:
            client: Connected Temporal client
            task_queue: Task queue name to poll
            pool_size: Connection pool size for aiohttp session
        """
        self.client = client
        self.task_queue = task_queue
        self.pool_size = pool_size
        self._worker: Optional[Worker] = None
        self._worker_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._activities: Optional[NodeExecutionActivities] = None

    @property
    def is_running(self) -> bool:
        """Check if the worker is running."""
        return self._worker_task is not None and not self._worker_task.done()

    async def start(self) -> None:
        """Start the Temporal worker in the background.

        Span emitted via OTel for cold-start benchmarking — exposes the
        wall-clock cost of session creation, activity instantiation, and
        worker registration.
        """
        with tracer.start_as_current_span("temporal.worker_start") as span:
            if self.is_running:
                span.set_attribute("already_running", True)
                logger.warning("Temporal worker already running")
                return

            # Create shared aiohttp session with connection pooling
            self._session = await create_shared_session(self.pool_size)

            # Create activity instance with shared session
            self._activities = NodeExecutionActivities(self._session)

            self._worker = await self._build_worker()
            span.set_attribute("task_queue", self.task_queue)
            span.set_attribute("pool_size", self.pool_size)

            logger.info(
                "Starting Temporal worker",
                task_queue=self.task_queue,
                pool_size=self.pool_size,
            )

            # Run worker in background task
            self._worker_task = asyncio.create_task(
                self._run_worker(),
                name="temporal-worker",
            )

    async def _build_worker(self) -> Worker:
        """Construct a fresh, ready-to-run Worker instance.

        Called from :meth:`start` AND from every crash-restart attempt in
        :meth:`_run_worker` — the SDK ``Worker`` is single-use (a second
        ``run()`` on the same instance raises ``RuntimeError("Already
        started")``), so restarting must always rebuild.
        """
        # F4.A: register per-type activities alongside the legacy
        # `execute_node_activity` dispatcher. The orchestrator at
        # workflow.py:_resolve_activity picks one of the two paths per node
        # based on the temporal_per_type_dispatch flag; both must be served
        # by the worker. Per-type activities register WITHOUT a task_queue
        # filter here (cls.task_queue is the *declared* preference; this
        # framework worker polls self.task_queue regardless). Per-queue
        # filtering lives in TemporalWorkerPool (below in this file),
        # which is default-on (Wave 16.4) and runs one activity-only
        # worker per declared queue. Registration cost: ~1.6s startup;
        # zero runtime cost when the flag is off (orchestrator routes to
        # execute_node_activity, per-type entries sit idle).
        #
        # F4.B: register AgentWorkflow + its agent activities
        # (execute_llm_step / persist_turn / compact_context / ...). The
        # orchestrator schedules AgentWorkflow as a child workflow
        # for the 15 migrating agent types when
        # ``temporal_agent_workflow_enabled`` is on.
        from services.temporal.plugin_activities import (
            collect_plugin_activities,
            collect_polling_activities,
        )
        from services.temporal.agent_activities import collect_agent_activities
        from services.temporal.activities import (
            broadcast_trigger_status_activity,
            evaluate_trigger_filter_activity,
            load_persisted_workflow_graph_activity,
            pause_workflow_on_failure_activity,
            record_run_completion_activity,
            store_node_output_activity,
        )

        per_type = collect_plugin_activities()  # no queue filter; all plugins
        agent_activities = collect_agent_activities()
        polling_activities = collect_polling_activities()
        # Plugin-owned workflow classes (e.g. cron's
        # CronTriggerWorkflow) self-register a SimplePlugin via
        # services.temporal.plugin_registry.register_temporal_plugin
        # from their plugin __init__.py. The Temporal SDK's plugin
        # chain merges each registered plugin's workflows / activities
        # / interceptors into the effective worker configuration —
        # the framework worker stays plugin-agnostic.
        plugin_list = temporal_plugins()

        # The Worker() constructor derives its default build id by
        # MD5-hashing the bytecode of every module in sys.modules
        # (disk reads included) — a synchronous ~3s stall at our
        # module count (152 plugins + LLM SDK stack) that freezes
        # the event loop and starves concurrent boot work (e.g.
        # broadcaster refreshes). The result is memoized in an SDK
        # module global, so one off-loop call here makes this
        # Worker AND every TemporalWorkerPool worker construct
        # cheaply. load_default_build_id is not re-exported from
        # temporalio.worker (private module in 1.30); if an SDK
        # upgrade moves it, skip the pre-warm and pay the
        # synchronous cost again rather than fail startup.
        try:
            from temporalio.worker._worker import load_default_build_id
        except ImportError:
            logger.warning(
                "temporalio private API moved: load_default_build_id "
                "unavailable, skipping off-loop build-id pre-warm"
            )
        else:
            await asyncio.to_thread(load_default_build_id)

        worker = Worker(
            self.client,
            task_queue=self.task_queue,
            plugins=plugin_list,
            workflows=_framework_workflows(),
            activities=[
                self._activities.execute_node_activity,
                broadcast_trigger_status_activity,
                evaluate_trigger_filter_activity,
                load_persisted_workflow_graph_activity,
                pause_workflow_on_failure_activity,
                record_run_completion_activity,
                store_node_output_activity,
                *per_type,
                *agent_activities,
                *polling_activities,
            ],
            # Allow concurrent activity execution for parallel branches
            max_concurrent_activities=self.pool_size,
            max_concurrent_workflow_tasks=10,
            graceful_shutdown_timeout=_graceful_shutdown_timeout(),
            identity=_worker_identity(self.task_queue),
            # ``TracingInterceptor`` is registered once on the shared client;
            # the SDK prepends client interceptors to every worker, so
            # repeating it here double-instruments each Signal / Query /
            # Update / Activity and doubles the emitted spans.
            interceptors=[ObservabilityWorkerInterceptor()],
            # Wave 18.2: sticky cache sized by deployment mode so
            # cached workflows skip Event-History replay without
            # blowing laptop RAM.
            max_cached_workflows=_sticky_cache_size(),
            # Wave 18.3: autoscaling pollers replace fixed counts —
            # scale between min/max on demand (SDK-recommended over
            # manual poller tuning). Pollers stay well below the
            # executor slot counts per the worker-performance docs.
            activity_task_poller_behavior=PollerBehaviorAutoscaling(initial=2, minimum=1, maximum=10),
            workflow_task_poller_behavior=PollerBehaviorAutoscaling(initial=2, minimum=1, maximum=20),
        )
        logger.info(
            "Registered Temporal activities",
            legacy=1,
            per_type=len(per_type),
            agent=len(agent_activities),
            task_queue=self.task_queue,
        )
        return worker

    async def _run_worker(self) -> None:
        """Run the worker (background task), self-restarting on crash.

        The Temporal worker shuts down on a poll failure rather than
        auto-retrying (per the Python ``Worker`` docs), and this task is
        detached — the startup retry loop in ``main.py`` has already
        returned by the time it runs. So a transient crash (e.g. the
        server briefly unavailable mid-poll) would otherwise leave the
        worker permanently dead until a process restart.

        Every restart attempt REBUILDS the worker via
        :meth:`_build_worker`: the SDK ``Worker`` is single-use, so
        re-running the same instance raises ``RuntimeError("Already
        started")`` on the first retry and every one after it — the old
        loop spun forever without ever polling again. Cancellation (from
        ``stop()``) always wins so shutdown is never delayed by a restart.
        Backoff knobs are env-driven (canonical defaults in .env.template).
        """
        from core.config import Settings

        _s = Settings()
        backoff = _s.temporal_worker_restart_backoff_seconds
        backoff_max = _s.temporal_worker_restart_backoff_max_seconds
        while True:
            try:
                await self._worker.run()
                return  # clean shutdown only
            except asyncio.CancelledError:
                logger.info("Temporal worker cancelled")
                raise
            except Exception as e:
                logger.error(f"Temporal worker crashed; restarting in {backoff:.1f}s: {e}")
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise  # cancellation during backoff still wins
            backoff = min(backoff * 2, backoff_max)
            try:
                self._worker = await self._build_worker()
            except Exception as build_exc:  # noqa: BLE001 — retried next loop
                # Next iteration's run() on the stale single-use instance
                # fails fast and re-enters this backoff, so rebuild
                # failures are retried rather than fatal.
                logger.error(f"Temporal worker rebuild failed; will retry: {build_exc}")

    async def stop(self) -> None:
        """Stop the Temporal worker and cleanup resources."""
        if not self.is_running:
            return

        logger.info("Stopping Temporal worker")

        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None

        # Close shared session
        if self._session and not self._session.closed:
            await self._session.close()

        self._worker = None
        self._session = None
        self._activities = None
        logger.info("Temporal worker stopped")


class TemporalWorkerPool:
    """Wave 11.F: multi-queue worker pool.

    Runs one :class:`Worker` per declared plugin ``task_queue``. Each
    worker only registers activities whose plugin opts in to that
    queue, so specialised workloads can be scaled independently.
    Env overrides for per-pool concurrency: ``TEMPORAL_<QUEUE>_CONCURRENCY``
    (e.g. ``TEMPORAL_AI_HEAVY_CONCURRENCY=4``).

    Usage::

        pool = TemporalWorkerPool(client, queues=["rest-api", "ai-heavy"])
        await pool.start()
        try:
            ...
        finally:
            await pool.stop()
    """

    # Default concurrency per queue — override via env.
    DEFAULT_CONCURRENCY: dict[str, int] = {
        "machina-default": 20,
        "rest-api": 50,
        "ai-heavy": 4,
        "code-exec": 10,
        "triggers-poll": 100,
        "triggers-event": 100,
        "android": 10,
        "browser": 4,
        "messaging": 20,
    }

    # Wave 18.1: worker-level activities/second ceilings — protect
    # external API quotas from runaway fan-out (Anthropic/OpenAI tier
    # limits on ai-heavy; provider send limits on messaging). None =
    # unthrottled (local subprocess work has no external quota).
    # Override via TEMPORAL_<QUEUE>_RATE_LIMIT (float, per second).
    DEFAULT_RATE_LIMIT: dict[str, Optional[float]] = {
        "machina-default": None,
        "rest-api": 100.0,
        "ai-heavy": 60.0,
        "code-exec": None,
        "triggers-poll": None,
        "triggers-event": None,
        "android": None,
        "browser": 10.0,
        "messaging": 20.0,
    }

    def __init__(
        self,
        client: Client,
        *,
        queues: Optional[list[str]] = None,
        default_pool_size: int = 20,
    ):
        self.client = client
        self.default_pool_size = default_pool_size
        # Lazy import to avoid circular at module load time.
        from services.temporal.plugin_activities import distinct_task_queues

        self.queues = queues or distinct_task_queues()
        self._workers: list[Worker] = []
        self._tasks: list[asyncio.Task] = []

    def _concurrency_for(self, queue: str) -> int:
        """Slots for a queue: explicit env override > mode-scaled default.

        Wave 17.5: ``deployment_mode == "local"`` halves the per-queue
        defaults (floor 1) — a dev laptop sharing 4-8 cores with an IDE
        + browser shouldn't run cloud-sized activity fan-out. Explicit
        ``TEMPORAL_<QUEUE>_CONCURRENCY`` env vars always win.
        """
        import os

        env_key = f"TEMPORAL_{queue.upper().replace('-', '_')}_CONCURRENCY"
        raw = os.environ.get(env_key)
        if raw and raw.isdigit():
            return int(raw)

        base = self.DEFAULT_CONCURRENCY.get(queue, self.default_pool_size)

        from core.config import Settings

        if Settings().deployment_mode == "local":
            return max(1, base // 2)
        return base

    def _rate_limit_for(self, queue: str) -> Optional[float]:
        """Wave 18.1: activities/second ceiling for a queue's worker.

        Explicit ``TEMPORAL_<QUEUE>_RATE_LIMIT`` env override (float)
        wins; falls back to ``DEFAULT_RATE_LIMIT``; None = unthrottled.
        """
        import os

        env_key = f"TEMPORAL_{queue.upper().replace('-', '_')}_RATE_LIMIT"
        raw = os.environ.get(env_key)
        if raw:
            try:
                return float(raw)
            except ValueError:
                logger.warning(f"[Pool] Ignoring non-numeric {env_key}={raw!r}")
        return self.DEFAULT_RATE_LIMIT.get(queue)

    # Wave 18.4: queues whose workloads are too unpredictable for fixed
    # slot counts. LLM turns range 2s-5min; browser sessions idle then
    # spike on page render. The resource-based supplier targets 80%
    # host CPU + memory instead — per the worker-performance docs it
    # "can provide reasonable performance without the need for
    # profiling". Other queues keep fixed sizing (predictable work).
    RESOURCE_TUNED_QUEUES: frozenset = frozenset({"ai-heavy", "browser"})

    def _tuner_for(self, queue: str, concurrency: int):
        """Return a WorkerTuner for resource-tuned queues, else None.

        The Worker rejects ``tuner`` + ``max_concurrent_activities``
        together, so the caller passes exactly one of the two. The
        fixed workflow/local/nexus suppliers only exist to satisfy
        ``create_composite`` — pool workers host no workflows.
        """
        if queue not in self.RESOURCE_TUNED_QUEUES:
            return None

        from temporalio.worker import (
            FixedSizeSlotSupplier,
            ResourceBasedSlotConfig,
            ResourceBasedSlotSupplier,
            ResourceBasedTunerConfig,
            WorkerTuner,
        )

        tuner_config = ResourceBasedTunerConfig(target_memory_usage=0.8, target_cpu_usage=0.8)
        return WorkerTuner.create_composite(
            workflow_supplier=FixedSizeSlotSupplier(10),
            activity_supplier=ResourceBasedSlotSupplier(
                # minimum keeps the queue live under load spikes elsewhere
                # on the host; maximum reuses the per-queue concurrency
                # (env-overridable) as the hard ceiling.
                ResourceBasedSlotConfig(minimum_slots=1, maximum_slots=max(concurrency, 2)),
                tuner_config,
            ),
            local_activity_supplier=FixedSizeSlotSupplier(10),
            nexus_supplier=FixedSizeSlotSupplier(10),
        )

    @property
    def is_running(self) -> bool:
        return any(t is not None and not t.done() for t in self._tasks)

    def _build_queue_worker(self, queue: str) -> Optional[Worker]:
        """Construct a fresh Worker for one queue.

        Returns ``None`` when the queue has no registered activities.
        Called from :meth:`start` AND from every crash-restart attempt in
        :meth:`_run_queue_worker` — the SDK ``Worker`` is single-use, so
        restarting must always rebuild.
        """
        from services.temporal.plugin_activities import (
            collect_plugin_activities,
        )

        activities = collect_plugin_activities(task_queue=queue)
        if not activities:
            return None
        concurrency = self._concurrency_for(queue)
        rate_limit = self._rate_limit_for(queue)
        tuner = self._tuner_for(queue, concurrency)
        worker_kwargs: dict = dict(
            task_queue=queue,
            activities=activities,
            graceful_shutdown_timeout=_graceful_shutdown_timeout(),
            identity=_worker_identity(queue),
            # ``TracingInterceptor`` is registered once on the shared client;
            # the SDK prepends client interceptors to every worker, so
            # repeating it here double-instruments each Signal / Query /
            # Update / Activity and doubles the emitted spans.
            interceptors=[ObservabilityWorkerInterceptor()],
            # Wave 18.1: per-queue activities/second ceiling.
            max_activities_per_second=rate_limit,
            # Wave 18.3: activity-only workers need a small
            # autoscaling poller budget — specialised queues see
            # lower task volume than the manager's default queue.
            activity_task_poller_behavior=PollerBehaviorAutoscaling(initial=1, minimum=1, maximum=5),
        )
        # Wave 18.4: the Worker rejects `tuner` alongside ANY of
        # `max_concurrent_workflow_tasks` / `max_concurrent_activities` /
        # `max_concurrent_local_activities` / `max_concurrent_nexus_tasks`
        # — the SDK enforces "size via tuner OR via max_concurrent_*,
        # never both" (temporalio.worker.Worker.__init__). Resource-tuned
        # queues get the tuner (which already carries a
        # FixedSizeSlotSupplier(10) for workflow slots via
        # `_tuner_for`); the rest keep fixed sizing.
        if tuner is not None:
            worker_kwargs["tuner"] = tuner
        else:
            worker_kwargs["max_concurrent_activities"] = concurrency
            worker_kwargs["max_concurrent_workflow_tasks"] = 10

        worker = Worker(self.client, **worker_kwargs)
        logger.info(
            f"[Pool] Built worker queue={queue!r} "
            f"activities={len(activities)} "
            f"slots={'resource-tuned<=' + str(max(concurrency, 2)) if tuner else concurrency} "
            f"rate_limit={rate_limit if rate_limit is not None else 'unthrottled'}"
        )
        return worker

    async def _run_queue_worker(self, queue: str, worker: Worker) -> None:
        """Run one queue's worker, rebuilding + restarting on crash.

        Previously each queue worker ran as a bare ``worker.run()`` task:
        a crash silently killed the ONLY worker for that queue and every
        activity routed there pended forever (its workflows stalled)
        until a backend restart. Same backoff + rebuild contract as
        :meth:`TemporalWorkerManager._run_worker`; cancellation (from
        :meth:`stop`) always wins.
        """
        from core.config import Settings

        _s = Settings()
        backoff = _s.temporal_worker_restart_backoff_seconds
        backoff_max = _s.temporal_worker_restart_backoff_max_seconds
        current = worker
        while True:
            try:
                await current.run()
                return  # clean shutdown only
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — supervised restart
                logger.error(f"[Pool] Worker for queue {queue!r} crashed; restarting in {backoff:.1f}s: {exc}")
            try:
                await asyncio.sleep(backoff)
            except asyncio.CancelledError:
                raise  # cancellation during backoff still wins
            backoff = min(backoff * 2, backoff_max)
            rebuilt = self._build_queue_worker(queue)
            if rebuilt is None:
                logger.error(f"[Pool] Queue {queue!r} has no activities on rebuild; worker not restarted")
                return
            current = rebuilt

    async def start(self) -> None:
        for queue in self.queues:
            worker = self._build_queue_worker(queue)
            if worker is None:
                logger.info(f"[Pool] Skipping empty queue {queue!r}")
                continue
            task = asyncio.create_task(
                self._run_queue_worker(queue, worker),
                name=f"worker-{queue}",
            )
            # Initial instances only — after a crash-restart the live
            # instance is owned by the supervising task.
            self._workers.append(worker)
            self._tasks.append(task)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._workers.clear()
        self._tasks.clear()
        logger.info("[Pool] All workers stopped")


async def run_standalone_worker(
    server_address: str | None = None,
    namespace: str = "default",
    task_queue: str = "machina-tasks",
    pool_size: int = 100,
) -> None:
    """Run the Temporal worker as a standalone process.

    This can be used for running workers separately from the main server,
    enabling horizontal scaling across multiple machines.

    Example:
        # Start multiple workers for horizontal scaling
        python -m services.temporal.worker

    Args:
        server_address: Temporal server address. Defaults to
            ``TEMPORAL_SERVER_ADDRESS`` (canonical value in ``.env.template``
            via ``core.env_defaults``) so a bare
            ``python -m services.temporal.worker`` dials the same server
            as the backend.
        namespace: Temporal namespace
        task_queue: Task queue to poll
        pool_size: Connection pool size
    """
    if server_address is None:
        from core.env_defaults import env_value

        server_address = env_value("TEMPORAL_SERVER_ADDRESS")
    logger.info(
        "Starting standalone Temporal worker",
        server_address=server_address,
        namespace=namespace,
        task_queue=task_queue,
        pool_size=pool_size,
    )

    # Embedded workers get this from main.py's lifespan; standalone
    # workers must load the model registry themselves or max_tokens /
    # context_length lookups degrade to hard fallbacks (4096).
    from services.model_registry import get_model_registry

    get_model_registry().startup()

    # Use custom runtime with heartbeating disabled to avoid warning on older servers
    runtime = create_runtime()

    # Connect with retries (server may still be starting)
    client = None
    for attempt in range(1, 6):
        try:
            logger.info(f"Connecting to Temporal server (attempt {attempt}/5)")
            client = await Client.connect(
                server_address,
                namespace=namespace,
                runtime=runtime,
                interceptors=[TracingInterceptor()],
            )
            logger.info("Connected to Temporal server")
            break
        except Exception as e:
            logger.warning(f"Temporal connection attempt {attempt}/5 failed: {e}")
            if attempt < 5:
                await asyncio.sleep(3.0)

    if client is None:
        logger.error(f"Could not connect to Temporal server at {server_address} after 5 attempts")
        return

    # Create shared session and activities
    session = await create_shared_session(pool_size)
    activities = NodeExecutionActivities(session)

    from services.temporal.activities import (
        broadcast_trigger_status_activity,
        evaluate_trigger_filter_activity,
        load_persisted_workflow_graph_activity,
        pause_workflow_on_failure_activity,
        record_run_completion_activity,
        store_node_output_activity,
    )
    from services.temporal.agent_activities import collect_agent_activities
    from services.temporal.agent_workflow import (
        AgentWorkflow,
        DelegatedTaskWorkflow,
    )
    from services.temporal.plugin_activities import (
        collect_plugin_activities,
    )

    registered_agent_activities = [
        *collect_agent_activities(),
    ]
    registered_plugin_activities = collect_plugin_activities()

    try:
        worker = Worker(
            client,
            task_queue=task_queue,
            workflows=_framework_workflows(),
            activities=[
                activities.execute_node_activity,
                broadcast_trigger_status_activity,
                evaluate_trigger_filter_activity,
                load_persisted_workflow_graph_activity,
                pause_workflow_on_failure_activity,
                record_run_completion_activity,
                store_node_output_activity,
                *registered_plugin_activities,
                *registered_agent_activities,
            ],
            max_concurrent_activities=pool_size,
            max_concurrent_workflow_tasks=10,
            graceful_shutdown_timeout=_graceful_shutdown_timeout(),
            # ``TracingInterceptor`` is registered once on the shared client;
            # the SDK prepends client interceptors to every worker, so
            # repeating it here double-instruments each Signal / Query /
            # Update / Activity and doubles the emitted spans.
            interceptors=[ObservabilityWorkerInterceptor()],
        )

        logger.info("Worker running. Press Ctrl+C to stop.")
        await worker.run()

    finally:
        # Cleanup session on shutdown
        if not session.closed:
            await session.close()
        # Activities resolve the singleton unifier through the global
        # container. Close its pooled SDK clients in standalone workers too.
        from core.container import container

        await container.chat_unifier().aclose()


async def create_worker(
    client: Client,
    task_queue: str = "machina-tasks",
    session: Optional[aiohttp.ClientSession] = None,
) -> Worker:
    """Create a worker instance for use in tests or custom setups.

    Args:
        client: Connected Temporal client
        task_queue: Task queue name
        session: Optional shared aiohttp session (created if not provided)

    Returns:
        Configured Worker instance (not started)
    """
    if session is None:
        session = await create_shared_session()

    activities = NodeExecutionActivities(session)
    from services.temporal.activities import (
        broadcast_trigger_status_activity,
        evaluate_trigger_filter_activity,
        load_persisted_workflow_graph_activity,
        pause_workflow_on_failure_activity,
        record_run_completion_activity,
        store_node_output_activity,
    )
    from services.temporal.agent_activities import collect_agent_activities
    from services.temporal.agent_workflow import (
        AgentWorkflow,
        DelegatedTaskWorkflow,
    )
    from services.temporal.plugin_activities import (
        collect_plugin_activities,
    )

    registered_agent_activities = [
        *collect_agent_activities(),
    ]
    registered_plugin_activities = collect_plugin_activities()

    return Worker(
        client,
        task_queue=task_queue,
        workflows=_framework_workflows(),
        activities=[
            activities.execute_node_activity,
            broadcast_trigger_status_activity,
            evaluate_trigger_filter_activity,
            load_persisted_workflow_graph_activity,
            pause_workflow_on_failure_activity,
            record_run_completion_activity,
            store_node_output_activity,
            *registered_plugin_activities,
            *registered_agent_activities,
        ],
        max_concurrent_activities=100,
        max_concurrent_workflow_tasks=10,
        graceful_shutdown_timeout=_graceful_shutdown_timeout(),
        # ``TracingInterceptor`` is registered once on the shared client;
        # the SDK prepends client interceptors to every worker, so
        # repeating it here double-instruments each Signal / Query /
        # Update / Activity and doubles the emitted spans.
        interceptors=[ObservabilityWorkerInterceptor()],
    )


if __name__ == "__main__":
    # Allow running worker standalone
    # Usage: python -m services.temporal.worker
    asyncio.run(run_standalone_worker())
