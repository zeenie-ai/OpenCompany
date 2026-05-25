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
from temporalio.client import Client
from temporalio.runtime import LoggingConfig, Runtime, TelemetryConfig
from temporalio.worker import Worker

from core.logging import get_logger
from .workflow import MachinaWorkflow
from .trigger_listener_workflow import TriggerListenerWorkflow
from .polling_trigger_workflow import PollingTriggerWorkflow
from .plugin_registry import temporal_plugins
from .activities import (
    NodeExecutionActivities,
    create_shared_session,
)

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

            # F4.A: register per-type activities alongside the legacy
            # `execute_node_activity` dispatcher. The orchestrator at
            # workflow.py:_resolve_activity picks one of the two paths per node
            # based on the temporal_per_type_dispatch flag; both must be served
            # by the worker. Per-type activities register WITHOUT a task_queue
            # filter (cls.task_queue is the *declared* preference; the single
            # worker actually polls self.task_queue regardless). When
            # TemporalWorkerPool is wired (future enhancement), per-queue
            # filtering will move there. Registration cost: ~1.6s startup;
            # zero runtime cost when the flag is off (orchestrator routes to
            # execute_node_activity, per-type entries sit idle).
            #
            # F4.B: register AgentWorkflow + its three activities
            # (execute_llm_step / persist_turn / compact_memory). The
            # orchestrator schedules AgentWorkflow as a child workflow
            # for the 15 migrating agent types when
            # ``temporal_agent_workflow_enabled`` is on.
            from services.temporal.plugin_activities import (
                collect_plugin_activities,
                collect_polling_activities,
            )
            from services.temporal.agent_activities import collect_agent_activities
            from services.temporal.agent_workflow import AgentWorkflow
            from services.temporal.activities import (
                broadcast_trigger_status_activity,
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
            self._worker = Worker(
                self.client,
                task_queue=self.task_queue,
                plugins=plugin_list,
                workflows=[
                    MachinaWorkflow,
                    AgentWorkflow,
                    TriggerListenerWorkflow,
                    PollingTriggerWorkflow,
                ],
                activities=[
                    self._activities.execute_node_activity,
                    broadcast_trigger_status_activity,
                    store_node_output_activity,
                    *per_type,
                    *agent_activities,
                    *polling_activities,
                ],
                # Allow concurrent activity execution for parallel branches
                max_concurrent_activities=self.pool_size,
                max_concurrent_workflow_tasks=10,
                graceful_shutdown_timeout=_graceful_shutdown_timeout(),
            )
            logger.info(
                "Registered Temporal activities",
                legacy=1,
                per_type=len(per_type),
                agent=len(agent_activities),
                task_queue=self.task_queue,
            )
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

    async def _run_worker(self) -> None:
        """Run the worker (background task)."""
        try:
            await self._worker.run()
        except asyncio.CancelledError:
            logger.info("Temporal worker cancelled")
        except Exception as e:
            logger.error(f"Temporal worker error: {str(e)}")
            raise

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
        import os

        env_key = f"TEMPORAL_{queue.upper().replace('-', '_')}_CONCURRENCY"
        raw = os.environ.get(env_key)
        if raw and raw.isdigit():
            return int(raw)
        return self.DEFAULT_CONCURRENCY.get(queue, self.default_pool_size)

    @property
    def is_running(self) -> bool:
        return any(t is not None and not t.done() for t in self._tasks)

    async def start(self) -> None:
        from services.temporal.plugin_activities import (
            collect_plugin_activities,
        )

        for queue in self.queues:
            activities = collect_plugin_activities(task_queue=queue)
            if not activities:
                logger.info(f"[Pool] Skipping empty queue {queue!r}")
                continue
            concurrency = self._concurrency_for(queue)
            worker = Worker(
                self.client,
                task_queue=queue,
                activities=activities,
                max_concurrent_activities=concurrency,
                max_concurrent_workflow_tasks=10,
                graceful_shutdown_timeout=_graceful_shutdown_timeout(),
            )
            task = asyncio.create_task(worker.run(), name=f"worker-{queue}")
            self._workers.append(worker)
            self._tasks.append(task)
            logger.info(f"[Pool] Started worker queue={queue!r} " f"activities={len(activities)} concurrency={concurrency}")

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
    server_address: str = "localhost:7233",
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
        server_address: Temporal server address
        namespace: Temporal namespace
        task_queue: Task queue to poll
        pool_size: Connection pool size
    """
    logger.info(
        "Starting standalone Temporal worker",
        server_address=server_address,
        namespace=namespace,
        task_queue=task_queue,
        pool_size=pool_size,
    )

    # Use custom runtime with heartbeating disabled to avoid warning on older servers
    runtime = create_runtime()

    # Connect with retries (server may still be starting)
    client = None
    for attempt in range(1, 6):
        try:
            logger.info(f"Connecting to Temporal server (attempt {attempt}/5)")
            client = await Client.connect(server_address, namespace=namespace, runtime=runtime)
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
        store_node_output_activity,
    )

    try:
        worker = Worker(
            client,
            task_queue=task_queue,
            workflows=[
                MachinaWorkflow,
                TriggerListenerWorkflow,
                PollingTriggerWorkflow,
            ],
            activities=[
                activities.execute_node_activity,
                broadcast_trigger_status_activity,
                store_node_output_activity,
            ],
            max_concurrent_activities=pool_size,
            max_concurrent_workflow_tasks=10,
            graceful_shutdown_timeout=_graceful_shutdown_timeout(),
        )

        logger.info("Worker running. Press Ctrl+C to stop.")
        await worker.run()

    finally:
        # Cleanup session on shutdown
        if not session.closed:
            await session.close()


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
        store_node_output_activity,
    )

    return Worker(
        client,
        task_queue=task_queue,
        workflows=[
            MachinaWorkflow,
            TriggerListenerWorkflow,
            PollingTriggerWorkflow,
        ],
        activities=[
            activities.execute_node_activity,
            broadcast_trigger_status_activity,
            store_node_output_activity,
        ],
        max_concurrent_activities=100,
        max_concurrent_workflow_tasks=10,
        graceful_shutdown_timeout=_graceful_shutdown_timeout(),
    )


if __name__ == "__main__":
    # Allow running worker standalone
    # Usage: python -m services.temporal.worker
    asyncio.run(run_standalone_worker())
