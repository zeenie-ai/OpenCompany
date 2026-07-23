"""Recovery sweeper for crash recovery.

Runs as background task to:
- Detect abandoned executions (running but no heartbeat)
- Resume interrupted workflows
- Clean up stale data
"""

import asyncio
import time
from typing import List, Optional, Callable, Awaitable

from core.logging import get_logger
from .models import TaskStatus, WorkflowStatus
from .cache import ExecutionCache

logger = get_logger(__name__)


class RecoverySweeper:
    """Background task that recovers abandoned workflow executions.

    Conductor's sweeper pattern:
    - Periodically scans for stuck executions
    - Detects nodes with stale heartbeats
    - Resets stuck nodes to PENDING for retry
    - Triggers workflow_decide to resume
    """

    def __init__(
        self,
        cache: ExecutionCache,
        heartbeat_timeout: int = 300,  # 5 minutes
        sweep_interval: int = 60,  # 1 minute
        max_retries: int = 3,
    ):
        """Initialize recovery sweeper.

        Args:
            cache: ExecutionCache for Redis access
            heartbeat_timeout: Seconds before node is considered stuck
            sweep_interval: Seconds between sweep runs
            max_retries: Max retry attempts per node
        """
        self.cache = cache
        self.heartbeat_timeout = heartbeat_timeout
        self.sweep_interval = sweep_interval
        self.max_retries = max_retries
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # Recovery callbacks (set by workflow service)
        self._on_recovery: Optional[Callable[[str], Awaitable[None]]] = None

    def set_recovery_callback(self, callback: Callable[[str], Awaitable[None]]) -> None:
        """Set callback to invoke when execution needs recovery.

        Args:
            callback: Async function that takes execution_id
        """
        self._on_recovery = callback

    async def start(self) -> None:
        """Start the recovery sweeper background task."""
        if self._running:
            logger.warning("Recovery sweeper already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._sweep_loop())
        logger.info("Recovery sweeper started", heartbeat_timeout=self.heartbeat_timeout, sweep_interval=self.sweep_interval)

    async def stop(self) -> None:
        """Stop the recovery sweeper."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Recovery sweeper stopped")

    async def _sweep_loop(self) -> None:
        """Main sweep loop - runs continuously."""
        while self._running:
            try:
                await self._sweep_once()
            except Exception as e:
                logger.error("Sweep iteration failed", error=str(e))

            # Wait before next sweep
            await asyncio.sleep(self.sweep_interval)

    async def _sweep_once(self) -> None:
        """Single sweep iteration - check all active executions."""
        # Get all active executions
        active_ids = await self.cache.get_active_executions()

        if not active_ids:
            return

        logger.debug("Sweeping active executions", count=len(active_ids))

        for execution_id in active_ids:
            try:
                await self._check_execution(execution_id)
            except Exception as e:
                logger.error("Failed to check execution", execution_id=execution_id, error=str(e))

    async def _check_execution(self, execution_id: str) -> None:
        """Check single execution for stuck nodes.

        Args:
            execution_id: Execution to check
        """
        # Load execution state
        ctx = await self.cache.load_execution_state(execution_id)
        if not ctx:
            # Execution not found - remove from active set
            logger.warning("Orphan execution in active set", execution_id=execution_id)
            if self.cache.cache.is_redis_available():
                await self.cache.cache.redis.srem("executions:active", execution_id)
            return

        # Check if already complete
        if ctx.status in (WorkflowStatus.COMPLETED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED):
            # Should not be in active set
            if self.cache.cache.is_redis_available():
                await self.cache.cache.redis.srem("executions:active", execution_id)
            return

        # Check for stuck nodes
        needs_recovery = False
        current_time = time.time()

        for node_id, node_exec in ctx.node_executions.items():
            if node_exec.status == TaskStatus.RUNNING:
                # Check heartbeat
                last_heartbeat = await self.cache.get_heartbeat(execution_id, node_id)

                if last_heartbeat is None:
                    # No heartbeat - node is stuck
                    stuck_duration = current_time - (node_exec.started_at or current_time)
                    if stuck_duration > self.heartbeat_timeout:
                        logger.warning(
                            "Node stuck (no heartbeat)", execution_id=execution_id, node_id=node_id, stuck_seconds=stuck_duration
                        )
                        needs_recovery = True

                elif current_time - last_heartbeat > self.heartbeat_timeout:
                    # Heartbeat too old
                    logger.warning(
                        "Node stuck (stale heartbeat)",
                        execution_id=execution_id,
                        node_id=node_id,
                        heartbeat_age=current_time - last_heartbeat,
                    )
                    needs_recovery = True

        if needs_recovery and self._on_recovery:
            logger.info("Triggering recovery", execution_id=execution_id)
            try:
                await self._on_recovery(execution_id)
            except Exception as e:
                logger.error("Recovery callback failed", execution_id=execution_id, error=str(e))

    async def scan_on_startup(self) -> List[str]:
        """Scan for executions that need recovery on server startup.

        Returns:
            List of execution IDs that need recovery
        """
        needs_recovery = []

        # Get all active executions
        active_ids = await self.cache.get_active_executions()

        logger.info("Startup scan for incomplete executions", active_count=len(active_ids))

        for execution_id in active_ids:
            ctx = await self.cache.load_execution_state(execution_id)
            if not ctx:
                continue

            # Check if execution was interrupted
            if ctx.status == WorkflowStatus.RUNNING:
                # Check how long it's been stuck
                if ctx.updated_at:
                    age = time.time() - ctx.updated_at
                    if age > self.heartbeat_timeout:
                        logger.info("Found interrupted execution", execution_id=execution_id, age_seconds=age)
                        needs_recovery.append(execution_id)

        return needs_recovery


# Global sweeper instance (initialized by main.py)
_sweeper: Optional[RecoverySweeper] = None


def get_recovery_sweeper() -> Optional[RecoverySweeper]:
    """Get global recovery sweeper instance."""
    return _sweeper


def set_recovery_sweeper(sweeper: RecoverySweeper) -> None:
    """Set global recovery sweeper instance."""
    global _sweeper
    _sweeper = sweeper
