"""Chat Trigger — Wave 11.C.

Event-based trigger that fires when the Console panel's chat tab
sends a message. Filter narrows to ``session_id`` so two chatTrigger
nodes with different session IDs receive independent streams.

Replaces:
- ``nodes/triggers.py:chatTrigger`` metadata-only registration.
- ``event_waiter.build_chat_filter`` stays wired until the generic
  trigger handler consults ``TriggerNode.build_filter`` directly
  (Wave 11.F).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TaskQueue, TriggerNode


class ChatTriggerParams(BaseModel):
    session_id: str = Field(default="default")
    placeholder: str = "Type a message..."

    model_config = ConfigDict(extra="ignore")


class ChatTriggerOutput(BaseModel):
    message: Optional[str] = None
    timestamp: Optional[str] = None
    session_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatTriggerNode(TriggerNode):
    type = "chatTrigger"
    display_name = "Chat Trigger"
    subtitle = "Console Chat"
    group = ("utility", "trigger")
    description = "Trigger workflow when user sends a chat message from the console input"
    component_kind = "trigger"
    handles = (
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    ui_hints = {"isChatTrigger": True}
    task_queue = TaskQueue.TRIGGERS_EVENT
    mode = "event"
    event_type = "chat_message_received"

    Params = ChatTriggerParams
    Output = ChatTriggerOutput

    def build_filter(self, params: ChatTriggerParams) -> Callable[[Dict[str, Any]], bool]:
        session_id = params.session_id

        def matches(event: Dict[str, Any]) -> bool:
            if session_id and session_id != "default":
                return event.get("session_id") == session_id
            return True

        return matches

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: ChatTriggerParams) -> ChatTriggerOutput:
        raise NotImplementedError(
            "Event triggers return via TriggerNode.execute, not the op body"
        )
