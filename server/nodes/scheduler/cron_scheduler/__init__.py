"""Cron Scheduler — Wave 11.D.10 inlined.

Cron-expression-based scheduling trigger. Deployment-mode lifecycle
(starting/stopping the cron job) is owned by ``deployment/triggers.py``;
the run-button path executes this plugin once for testing.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

logger = get_logger(__name__)


def _calculate_wait_seconds(p: Dict[str, Any]) -> int:
    frequency = p.get("frequency", "minutes")
    match frequency:
        case "seconds":
            return int(p.get("interval", 30))
        case "minutes":
            return int(p.get("interval_minutes", 5)) * 60
        case "hours":
            return int(p.get("interval_hours", 1)) * 3600
        case "days":
            return 24 * 3600
        case "weeks":
            return 7 * 24 * 3600
        case "months":
            return 30 * 24 * 3600
        case "once":
            return 0
        case _:
            return 300


def _format_wait_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} seconds"
    if seconds < 3600:
        m = seconds // 60
        return f"{m} minute{'s' if m != 1 else ''}"
    if seconds < 86400:
        h = seconds // 3600
        return f"{h} hour{'s' if h != 1 else ''}"
    d = seconds // 86400
    return f"{d} day{'s' if d != 1 else ''}"


def _get_schedule_description(p: Dict[str, Any]) -> str:
    frequency = p.get("frequency", "minutes")
    match frequency:
        case "seconds":
            return f"Every {p.get('interval', 30)} seconds"
        case "minutes":
            return f"Every {p.get('interval_minutes', 5)} minutes"
        case "hours":
            return f"Every {p.get('interval_hours', 1)} hours"
        case "days":
            return f"Daily at {p.get('daily_time', '09:00')}"
        case "weeks":
            weekday = str(p.get("weekday", "1"))
            days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
            day_name = days[int(weekday)] if weekday.isdigit() else weekday
            return f"Weekly on {day_name} at {p.get('weekly_time', '09:00')}"
        case "months":
            return f"Monthly on day {p.get('month_day', '1')} at {p.get('monthly_time', '09:00')}"
        case "once":
            return "Once (no repeat)"
        case _:
            return "Unknown schedule"


class CronSchedulerParams(BaseModel):
    frequency: Literal[
        "seconds", "minutes", "hours", "days", "weeks", "months", "once"
    ] = Field(
        default="minutes",
        description="How often the schedule fires",
    )
    interval: int = Field(
        default=30, ge=5, le=59,
        description="Seconds between fires (5-59)",
        json_schema_extra={"displayOptions": {"show": {"frequency": ["seconds"]}}},
    )
    interval_minutes: int = Field(
        default=5, ge=1, le=59,
        description="Minutes between fires (1-59)",
        json_schema_extra={"displayOptions": {"show": {"frequency": ["minutes"]}}},
    )
    interval_hours: int = Field(
        default=1, ge=1, le=23,
        description="Hours between fires (1-23)",
        json_schema_extra={"displayOptions": {"show": {"frequency": ["hours"]}}},
    )
    daily_time: Literal[
        "00:00", "02:00", "04:00", "06:00", "08:00", "09:00",
        "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00",
    ] = Field(
        default="09:00",
        description="Time of day (HH:MM) for daily schedule",
        json_schema_extra={"displayOptions": {"show": {"frequency": ["days"]}}},
    )
    weekday: Literal["0", "1", "2", "3", "4", "5", "6"] = Field(
        default="1",
        description="Day of week for weekly schedule (0=Sunday … 6=Saturday)",
        json_schema_extra={"displayOptions": {"show": {"frequency": ["weeks"]}}},
    )
    weekly_time: Literal[
        "00:00", "02:00", "04:00", "06:00", "08:00", "09:00",
        "10:00", "12:00", "14:00", "16:00", "18:00", "20:00", "22:00",
    ] = Field(
        default="09:00",
        description="Time of day for weekly schedule",
        json_schema_extra={"displayOptions": {"show": {"frequency": ["weeks"]}}},
    )
    month_day: Literal[
        "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
        "11", "12", "13", "14", "15", "16", "17", "18", "19", "20",
        "21", "22", "23", "24", "25", "26", "27", "28", "L",
    ] = Field(
        default="1",
        description="Day of month (1-28, or 'L' for last day)",
        json_schema_extra={"displayOptions": {"show": {"frequency": ["months"]}}},
    )
    timezone: Literal[
        "UTC",
        "America/New_York",
        "America/Los_Angeles",
        "Europe/London",
        "Europe/Berlin",
        "Asia/Tokyo",
        "Asia/Kolkata",
    ] = Field(
        default="UTC",
        description="IANA timezone identifier",
    )
    # Vestigial fields kept for backward compatibility with older workflows.
    cron_expression: str = Field(default="0 * * * *")
    monthly_time: str = Field(default="09:00")

    model_config = ConfigDict(extra="allow")


class CronSchedulerOutput(BaseModel):
    timestamp: Optional[str] = None
    iteration: Optional[int] = None
    frequency: Optional[str] = None
    timezone: Optional[str] = None
    schedule: Optional[str] = None
    scheduled_time: Optional[str] = None
    triggered_at: Optional[str] = None
    waited_seconds: Optional[int] = None
    next_run: Optional[str] = None
    message: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class CronSchedulerNode(ActionNode):
    type = "cronScheduler"
    display_name = "Cron Scheduler"
    subtitle = "Time-Based Trigger"
    group = ("scheduler", "trigger", "tool")
    description = "Cron expression-based scheduling trigger"
    component_kind = "trigger"
    handles = (
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.TRIGGERS_POLL
    usable_as_tool = True

    Params = CronSchedulerParams
    Output = CronSchedulerOutput

    @Operation("trigger")
    async def trigger(self, ctx: NodeContext, params: CronSchedulerParams) -> CronSchedulerOutput:
        from services.status_broadcaster import get_status_broadcaster

        start_time = time.time()
        p = params.model_dump(by_alias=False)
        frequency = p.get("frequency", "minutes")
        timezone = p.get("timezone", "UTC")

        schedule_desc = _get_schedule_description(p)
        wait_seconds = _calculate_wait_seconds(p)

        now = datetime.now()
        trigger_time = now + timedelta(seconds=wait_seconds)

        await get_status_broadcaster().update_node_status(
            ctx.node_id, "waiting",
            {
                "message": f"Waiting {schedule_desc}...",
                "trigger_time": trigger_time.isoformat(),
                "wait_seconds": wait_seconds,
            },
            workflow_id=ctx.workflow_id,
        )

        logger.info(
            f"[CronScheduler] Waiting {wait_seconds}s for trigger",
            node_id=ctx.node_id, trigger_time=trigger_time.isoformat(),
        )

        try:
            await asyncio.sleep(wait_seconds)
        except asyncio.CancelledError:
            raise RuntimeError("Scheduler cancelled")

        triggered_at = datetime.now()
        output = CronSchedulerOutput(
            timestamp=triggered_at.isoformat(),
            iteration=1,
            frequency=frequency,
            timezone=timezone,
            schedule=schedule_desc,
            scheduled_time=trigger_time.isoformat(),
            triggered_at=triggered_at.isoformat(),
            waited_seconds=wait_seconds,
        )
        if frequency == "once":
            output.message = f"Triggered after waiting {_format_wait_time(wait_seconds)}"
        else:
            output.next_run = schedule_desc
            output.message = (
                f"Triggered after {_format_wait_time(wait_seconds)}, "
                f"will repeat: {schedule_desc}"
            )
        return output
