"""Chat Trigger — Wave 11.C.

Event-based trigger that fires when the Console panel's chat tab
sends a message. Filter narrows to ``session_id`` so two chatTrigger
nodes with different session IDs receive independent streams.

A Reset clears the workflow's chat thread (``reset_execution_state``), as
``chatReply`` does: a workflow with a trigger and no reply yet (Turn on
Talk resets on that graph before adding the reply) must not carry its old
messages into the next generation.

The owner's message reaches it from ``services/chat/handlers.py``
(``send_chat_message``), which saves it and signals it through
``services/chat/events.py``. Its output names the saved message
(``message_id``) and, when the message started a chat run, the run
(``run_id``): the run that answers it is that chat run.

Replaces:
- ``nodes/triggers.py:chatTrigger`` metadata-only registration.
- ``event_waiter.build_chat_filter`` stays wired until the generic
  trigger handler consults ``TriggerNode.build_filter`` directly
  (Wave 11.F).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.deployment.canary_registry import register_canary_trigger_type
from services.plugin import NodeContext, Operation, TaskQueue, TriggerNode


class ChatTriggerParams(BaseModel):
    session_id: str = Field(default="default")
    placeholder: str = "Type a message..."

    model_config = ConfigDict(extra="ignore")


class ChatTriggerOutput(BaseModel):
    message: Optional[str] = None
    timestamp: Optional[str] = None
    session_id: Optional[str] = None
    message_id: Optional[str] = None
    run_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatTriggerNode(TriggerNode):
    type = "chatTrigger"
    display_name = "Chat Trigger"
    subtitle = "Console Chat"
    group = ("utility", "trigger")
    description = "Trigger workflow when user sends a chat message from the console input"
    component_kind = "trigger"
    handles = ({"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},)
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
        raise NotImplementedError("Event triggers return via TriggerNode.execute, not the op body")

    @classmethod
    async def reset_execution_state(
        cls,
        *,
        node_id: str,
        workflow_id: str,
        execution_id: str,
        generation: int,
        graph: Dict[str, Any],
        database: Any,
    ) -> Dict[str, Any]:
        """A Reset clears the workflow's chat thread: the session ended with
        the generation. Only the workflow's own thread (session = workflow
        id), never a custom ``session_id`` another workflow may share."""
        del node_id, execution_id, generation, graph
        from services.chat_thread import clear_chat_thread

        cleared = await clear_chat_thread(database, str(workflow_id))
        return {"reset": bool(cleared), "cleared_chat_messages": cleared}


# Wave 12 C1 rollout #1: opt this trigger into the TriggerListenerWorkflow
# consumer path. Producer side: dispatch_chat_message_received in
# services/chat/events.py calls dispatch.emit; the canary registry tells
# DeploymentManager to start a listener for this type. See
# services/deployment/canary_registry.py.
register_canary_trigger_type(ChatTriggerNode.type, "com.opencompany.chat.message.received")
