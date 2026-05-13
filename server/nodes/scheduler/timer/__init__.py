"""Timer — Wave 11.C migration.

Sleep / delay node. Has both input and output handles (componentKind
"square") so it can be inserted mid-workflow as a deliberate pause,
not just at the start.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class TimerParams(BaseModel):
    duration: int = Field(default=1, ge=1, le=86400)
    unit: Literal["seconds", "minutes", "hours"] = "seconds"

    model_config = ConfigDict(extra="ignore")


class TimerOutput(BaseModel):
    elapsed: Optional[float] = None

    model_config = ConfigDict(extra="allow")


class TimerNode(ActionNode):
    type = "timer"
    display_name = "Timer"
    subtitle = "Delay Trigger"
    group = ("scheduler",)
    description = "Timer-based trigger with configurable delay"
    component_kind = "square"  # has input handle
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT
    usable_as_tool = True

    Params = TimerParams
    Output = TimerOutput

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: TimerParams) -> Any:
        import asyncio
        import time
        from datetime import datetime, timedelta

        from services.status_broadcaster import get_status_broadcaster

        start_time = time.time()
        duration = int(params.duration)
        unit = params.unit
        match unit:
            case "minutes":
                wait_seconds = duration * 60
            case "hours":
                wait_seconds = duration * 3600
            case _:
                wait_seconds = duration

        complete_time = datetime.now() + timedelta(seconds=wait_seconds)
        await get_status_broadcaster().update_node_status(
            ctx.node_id, "waiting",
            {
                "message": f"Waiting {duration} {unit}...",
                "complete_time": complete_time.isoformat(),
                "wait_seconds": wait_seconds,
            },
            workflow_id=ctx.workflow_id,
        )

        try:
            await asyncio.sleep(wait_seconds)
        except asyncio.CancelledError:
            raise RuntimeError("Timer cancelled")

        elapsed_ms = int((time.time() - start_time) * 1000)
        return {
            "timestamp": datetime.now().isoformat(),
            "elapsed_ms": elapsed_ms,
            "duration": duration,
            "unit": unit,
            "message": f"Timer completed after {duration} {unit}",
        }
