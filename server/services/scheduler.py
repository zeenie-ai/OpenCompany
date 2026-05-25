"""
Cron Scheduler Service using APScheduler.
Manages scheduled jobs for workflow automation.
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.base import JobLookupError
from typing import Callable, Dict, Optional
import logging

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncIOScheduler] = None


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the singleton scheduler instance."""
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


def start_scheduler():
    """Start the scheduler if not already running."""
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("[Scheduler] Started")


def shutdown_scheduler():
    """Shutdown the scheduler gracefully."""
    scheduler = get_scheduler()
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[Scheduler] Shutdown")


def register_cron_job(job_id: str, cron_expression: str, callback: Callable, timezone: str = "UTC", **kwargs) -> str:
    """
    Register a cron job with the scheduler.

    Args:
        job_id: Unique identifier for the job
        cron_expression: 6-field cron expression (second minute hour day month weekday)
                        or 5-field (minute hour day month weekday)
        callback: Async function to call when job fires
        timezone: Timezone for schedule (default: UTC)
        **kwargs: Additional arguments passed to the callback

    Returns:
        The job_id
    """
    scheduler = get_scheduler()

    # Parse cron expression - support both 5-field and 6-field formats
    parts = cron_expression.split()

    if len(parts) >= 6:
        # 6-field format: second minute hour day month weekday
        trigger = CronTrigger(
            second=parts[0], minute=parts[1], hour=parts[2], day=parts[3], month=parts[4], day_of_week=parts[5], timezone=timezone
        )
    else:
        # 5-field format: minute hour day month weekday (default second=0)
        if len(parts) < 5:
            parts.extend(["*"] * (5 - len(parts)))
        trigger = CronTrigger(
            second="0", minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4], timezone=timezone
        )

    scheduler.add_job(callback, trigger=trigger, id=job_id, replace_existing=True, kwargs=kwargs)

    logger.info(f"[Scheduler] Registered cron job: {job_id} with expression: {cron_expression}")
    return job_id


def remove_cron_job(job_id: str) -> bool:
    """
    Remove a cron job from the scheduler.

    Args:
        job_id: The job identifier to remove

    Returns:
        True if job was removed, False if not found
    """
    scheduler = get_scheduler()
    try:
        scheduler.remove_job(job_id)
        logger.info(f"[Scheduler] Removed cron job: {job_id}")
        return True
    except JobLookupError:
        logger.warning(f"[Scheduler] Job not found: {job_id}")
        return False


def get_job_info(job_id: str) -> Optional[Dict]:
    """
    Get information about a scheduled job.

    Args:
        job_id: The job identifier

    Returns:
        Dict with job info or None if not found
    """
    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)
    if job:
        return {"id": job.id, "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None, "trigger": str(job.trigger)}
    return None


def get_all_jobs() -> list:
    """Get list of all scheduled jobs."""
    scheduler = get_scheduler()
    jobs = scheduler.get_jobs()
    return [
        {"id": job.id, "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None, "trigger": str(job.trigger)}
        for job in jobs
    ]
