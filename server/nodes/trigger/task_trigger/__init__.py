"""Task Trigger — Wave 11.C migration.

Event-based trigger that fires when a delegated child agent completes
its task (success or error). Listens for ``task_completed`` events on
``event_waiter``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TaskQueue, TriggerNode


class TaskTriggerParams(BaseModel):
    task_id: str = Field(default="")
    agent_name: str = Field(default="")
    status_filter: Literal["all", "completed", "error"] = Field(
        default="all",
    )
    parent_node_id: str = Field(default="")

    model_config = ConfigDict(extra="ignore")


class TaskTriggerOutput(BaseModel):
    task_id: Optional[str] = None
    status: Optional[str] = None
    agent_name: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    workflow_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class TaskTriggerNode(TriggerNode):
    type = "taskTrigger"
    display_name = "Task Completed"
    subtitle = "Delegated Task Done"
    group = ("trigger", "workflow")
    description = "Triggers when a delegated child agent completes its task (success or error)"
    component_kind = "trigger"
    handles = (
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    task_queue = TaskQueue.TRIGGERS_EVENT
    mode = "event"
    event_type = "task_completed"

    Params = TaskTriggerParams
    Output = TaskTriggerOutput

    def build_filter(self, params: TaskTriggerParams) -> Callable[[Dict[str, Any]], bool]:
        task_id = params.task_id
        agent_name = params.agent_name.lower()
        status = params.status_filter
        parent = params.parent_node_id

        def matches(event: Dict[str, Any]) -> bool:
            if task_id and event.get("task_id") != task_id:
                return False
            if status != "all" and event.get("status") != status:
                return False
            if agent_name and agent_name not in str(event.get("agent_name", "")).lower():
                return False
            if parent and event.get("parent_node_id") != parent:
                return False
            return True

        return matches

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: TaskTriggerParams) -> TaskTriggerOutput:
        raise NotImplementedError(
            "Event triggers return via TriggerNode.execute, not the op body"
        )
