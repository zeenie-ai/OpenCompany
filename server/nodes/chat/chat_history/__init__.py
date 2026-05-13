"""Chat History — Wave 11.D.10 fix.

Retrieves chat history via the external chat backend JSON-RPC 2.0
WebSocket (``services.chat_client``).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class ChatHistoryParams(BaseModel):
    host: str = Field(default="localhost")
    port: int = Field(default=8080, ge=1, le=65535)
    session_id: str = Field(default="default")
    api_key: str = Field(default="", json_schema_extra={"password": True})
    limit: int = Field(default=50, ge=1, le=500)

    model_config = ConfigDict(extra="ignore")


class ChatHistoryOutput(BaseModel):
    messages: Optional[list] = None
    count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class ChatHistoryNode(ActionNode):
    type = "chatHistory"
    display_name = "Chat History"
    subtitle = "Retrieve Messages"
    group = ("chat",)
    description = "Retrieve chat conversation history"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.DEFAULT

    Params = ChatHistoryParams
    Output = ChatHistoryOutput

    @Operation("read")
    async def read(self, ctx: NodeContext, params: ChatHistoryParams) -> ChatHistoryOutput:
        from services.chat_client import get_chat_history

        result = await get_chat_history(
            host=params.host,
            port=params.port,
            session_id=params.session_id,
            api_key=params.api_key,
            limit=params.limit,
        )
        if not result.get("success"):
            raise RuntimeError(result.get("error", "chatHistory fetch failed"))

        messages = result.get("messages", []) or []
        return ChatHistoryOutput(messages=messages, count=len(messages))
