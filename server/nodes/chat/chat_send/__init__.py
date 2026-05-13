"""Chat Send — Wave 11.D.10 fix.

Sends a message via the external chat backend JSON-RPC 2.0 WebSocket
(``services.chat_client``). Replaces my earlier 11.D.1 placeholder that
wrote to the local ``chat_messages`` table — that was a different
semantic (console-panel history, not chat backend).
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class ChatSendParams(BaseModel):
    host: str = Field(default="localhost")
    port: int = Field(default=8080, ge=1, le=65535)
    session_id: str = Field(default="default")
    api_key: str = Field(default="", json_schema_extra={"password": True})
    content: str = Field(default="")
    # legacy field alias — earlier migration used ``message``
    message: str = Field(default="")

    model_config = ConfigDict(extra="ignore")


class ChatSendOutput(BaseModel):
    sent: Optional[bool] = None
    message_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class ChatSendNode(ActionNode):
    type = "chatSend"
    display_name = "Chat Send"
    subtitle = "Send to Chat"
    group = ("chat",)
    description = "Send messages to chat conversations"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.DEFAULT

    Params = ChatSendParams
    Output = ChatSendOutput

    @Operation("send")
    async def send(self, ctx: NodeContext, params: ChatSendParams) -> ChatSendOutput:
        from services.chat_client import send_chat_message

        content = params.content or params.message
        if not content:
            raise RuntimeError("Message content is required")

        result = await send_chat_message(
            host=params.host,
            port=params.port,
            session_id=params.session_id,
            api_key=params.api_key,
            content=content,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error", "chatSend failed"))

        payload = result.get("result") or {}
        return ChatSendOutput(
            sent=True,
            message_id=payload.get("message_id") if isinstance(payload, dict) else None,
            **({k: v for k, v in payload.items() if k != "message_id"} if isinstance(payload, dict) else {}),
        )
