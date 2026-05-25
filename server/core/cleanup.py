"""Periodic cleanup service for long-running daemon.

Follows the RecoverySweeper pattern from execution/recovery.py.
All configuration from Settings (environment variables).
"""

import asyncio
import gc
from typing import Optional, TYPE_CHECKING

from core.logging import get_logger

if TYPE_CHECKING:
    from core.config import Settings
    from core.database import Database
    from core.cache import CacheService

logger = get_logger(__name__)


class CleanupService:
    """Background cleanup to prevent resource exhaustion.

    Periodically cleans up:
    - Expired cache entries
    - Old console logs (keeps configurable count)
    - Old cache entries by age
    - Forces garbage collection
    """

    def __init__(self, database: "Database", cache: "CacheService", settings: "Settings"):
        self.database = database
        self.cache = cache
        self.settings = settings
        self._running = False
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start the cleanup service background task."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            "Cleanup service started",
            interval=self.settings.cleanup_interval,
            logs_max=self.settings.cleanup_logs_max_count,
            cache_max_age_hours=self.settings.cleanup_cache_max_age_hours,
        )

    async def stop(self) -> None:
        """Stop the cleanup service gracefully."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Cleanup service stopped")

    async def _cleanup_loop(self) -> None:
        """Main cleanup loop - runs at configured interval."""
        while self._running:
            try:
                await self._run_cleanup()
            except Exception as e:
                logger.error("Cleanup failed", error=str(e))
            await asyncio.sleep(self.settings.cleanup_interval)

    async def _run_cleanup(self) -> None:
        """Execute all cleanup tasks."""
        results = {}

        # 1. Expired cache entries (TTL-based)
        try:
            results["expired_cache"] = await self.database.cleanup_expired_cache()
        except Exception as e:
            logger.warning("Failed to cleanup expired cache", error=str(e))
            results["expired_cache"] = 0

        # 2. Old console logs (keep last N)
        try:
            results["old_logs"] = await self.database.cleanup_old_console_logs(keep=self.settings.cleanup_logs_max_count)
        except Exception as e:
            logger.warning("Failed to cleanup old console logs", error=str(e))
            results["old_logs"] = 0

        # 3. Old cache entries by age
        try:
            results["old_cache"] = await self.database.cleanup_old_cache(max_age_hours=self.settings.cleanup_cache_max_age_hours)
        except Exception as e:
            logger.warning("Failed to cleanup old cache", error=str(e))
            results["old_cache"] = 0

        # 4. Force garbage collection
        gc.collect()

        # Only log if something was cleaned up
        total_cleaned = sum(results.values())
        if total_cleaned > 0:
            logger.info("Cleanup completed", **results)

    async def run_once(self) -> dict:
        """Run cleanup once and return results. Useful for testing."""
        results = {}
        results["expired_cache"] = await self.database.cleanup_expired_cache()
        results["old_logs"] = await self.database.cleanup_old_console_logs(keep=self.settings.cleanup_logs_max_count)
        results["old_cache"] = await self.database.cleanup_old_cache(max_age_hours=self.settings.cleanup_cache_max_age_hours)
        gc.collect()
        return results
