"""Dead Letter Queue (DLQ) handler for failed node executions.

This module provides optional DLQ functionality that can be enabled/disabled
via configuration. When enabled, failed nodes (after all retries exhausted)
are stored for later inspection and replay.

Usage:
    from services.execution.dlq import DLQHandler, NullDLQHandler

    # Create handler based on config
    dlq = DLQHandler(cache) if settings.dlq_enabled else NullDLQHandler()

    # Add failed node
    await dlq.add_failed_node(ctx, node, inputs, error)
"""

from typing import Dict, Any, Protocol, TYPE_CHECKING
from core.logging import get_logger
from .models import ExecutionContext, NodeExecution, DLQEntry

if TYPE_CHECKING:
    from .cache import ExecutionCache

logger = get_logger(__name__)


class DLQHandlerProtocol(Protocol):
    """Protocol for DLQ handlers (enables duck typing)."""

    async def add_failed_node(self, ctx: ExecutionContext, node: NodeExecution, inputs: Dict[str, Any], error: str) -> bool:
        """Add a failed node to the DLQ."""
        ...

    @property
    def enabled(self) -> bool:
        """Whether DLQ is enabled."""
        ...


class NullDLQHandler:
    """No-op DLQ handler when DLQ is disabled.

    This follows the Null Object pattern - all operations succeed silently.
    """

    @property
    def enabled(self) -> bool:
        return False

    async def add_failed_node(self, ctx: ExecutionContext, node: NodeExecution, inputs: Dict[str, Any], error: str) -> bool:
        """No-op: silently succeed without storing anything."""
        logger.debug("DLQ disabled, skipping failed node storage", node_id=node.node_id, error=error)
        return True


class DLQHandler:
    """Active DLQ handler that stores failed nodes in Redis.

    Stores failed node executions with full context for later inspection
    and replay via the replay_dlq_entry API.
    """

    def __init__(self, cache: "ExecutionCache"):
        """Initialize DLQ handler.

        Args:
            cache: ExecutionCache instance for Redis persistence
        """
        self.cache = cache

    @property
    def enabled(self) -> bool:
        return True

    async def add_failed_node(self, ctx: ExecutionContext, node: NodeExecution, inputs: Dict[str, Any], error: str) -> bool:
        """Add a failed node to the Dead Letter Queue.

        Args:
            ctx: ExecutionContext with workflow info
            node: Failed NodeExecution with retry info
            inputs: Node inputs at time of failure
            error: Final error message

        Returns:
            True if successfully added, False otherwise
        """
        try:
            dlq_entry = DLQEntry.create(ctx, node, inputs)

            success = await self.cache.add_to_dlq(dlq_entry)
            if success:
                logger.info(
                    "Node added to DLQ", entry_id=dlq_entry.id, node_id=node.node_id, node_type=node.node_type, retry_count=node.retry_count
                )

                await self.cache.add_event(
                    ctx.execution_id,
                    "node_dlq",
                    {
                        "node_id": node.node_id,
                        "dlq_entry_id": dlq_entry.id,
                        "error": error,
                        "retry_count": node.retry_count,
                    },
                )
                return True
            else:
                logger.error("Failed to add node to DLQ", node_id=node.node_id, error=error)
                return False

        except Exception as e:
            logger.error("Exception adding node to DLQ", node_id=node.node_id, error=str(e))
            return False


def create_dlq_handler(cache: "ExecutionCache", enabled: bool = False) -> DLQHandlerProtocol:
    """Factory function to create appropriate DLQ handler.

    Args:
        cache: ExecutionCache instance
        enabled: Whether DLQ should be enabled

    Returns:
        DLQHandler if enabled, NullDLQHandler otherwise
    """
    if enabled:
        logger.info("DLQ enabled")
        return DLQHandler(cache)
    else:
        logger.debug("DLQ disabled")
        return NullDLQHandler()
