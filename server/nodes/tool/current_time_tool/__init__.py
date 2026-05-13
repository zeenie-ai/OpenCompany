"""Current Time Tool — Wave 11.C migration."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from services.plugin import NodeContext, Operation, TaskQueue, ToolNode


class CurrentTimeParams(BaseModel):
    timezone: str = Field(
        default="UTC",
        description="Timezone (e.g. UTC, America/New_York, Europe/London)",
    )


class CurrentTimeOutput(BaseModel):
    iso: str
    timezone: str
    unix: int


class CurrentTimeToolNode(ToolNode):
    type = "currentTimeTool"
    display_name = "Current Time"
    subtitle = "Date / Time"
    group = ("tool", "ai")
    description = "Get current date/time with timezone support"
    component_kind = "tool"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-tool", "kind": "output", "position": "top",
         "label": "Tool", "role": "tools"},
    )
    ui_hints = {"isToolPanel": True, "hideRunButton": True}
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = CurrentTimeParams
    Output = CurrentTimeOutput

    @Operation("now")
    async def now(self, ctx: NodeContext, params: CurrentTimeParams) -> CurrentTimeOutput:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(params.timezone)
        except Exception:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        return CurrentTimeOutput(
            iso=now.isoformat(),
            timezone=params.timezone,
            unix=int(now.timestamp()),
        )
