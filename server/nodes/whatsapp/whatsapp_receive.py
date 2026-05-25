"""WhatsApp Receive — Wave 11.C migration (event-based trigger).

Inbound WhatsApp message events arrive via the whatsapp-rpc bridge.
``build_filter`` delegates to the existing ``build_whatsapp_filter``
to preserve the full sender/group/forwarded/keyword filter matrix.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TaskQueue, TriggerNode


class WhatsAppReceiveParams(BaseModel):
    """Snake_case field names throughout; JSON Schema keys match field
    names exactly (no aliases). ``displayOptions.show`` references are
    guaranteed consistent with the driver field's JSON key.
    """

    message_type_filter: Literal[
        "all",
        "text",
        "image",
        "video",
        "audio",
        "document",
        "location",
        "contact",
    ] = Field(default="all")
    filter: Literal[
        "all",
        "self",
        "any_contact",
        "contact",
        "group",
        "channel",
        "keywords",
    ] = "all"
    phone_number: str = Field(
        default="",
        description="Contact phone (digits only, no + prefix)",
        json_schema_extra={"displayOptions": {"show": {"filter": ["contact"]}}},
    )
    group_id: str = Field(
        default="",
        description="WhatsApp group JID",
        json_schema_extra={
            "component": "GroupIdSelector",
            "displayOptions": {"show": {"filter": ["group"]}},
        },
    )
    sender_number: str = Field(
        default="",
        description="Optional: filter group messages to a specific member",
        json_schema_extra={
            "component": "SenderNumberSelector",
            "dependsOn": "group_id",
            "displayOptions": {"show": {"filter": ["group"]}},
        },
    )
    channel_jid: str = Field(
        default="",
        description="Newsletter channel JID",
        json_schema_extra={
            "component": "ChannelJidSelector",
            "displayOptions": {"show": {"filter": ["channel"]}},
        },
    )
    keywords: str = Field(
        default="",
        description="Comma-separated keywords (case-insensitive)",
        json_schema_extra={"displayOptions": {"show": {"filter": ["keywords"]}}},
    )
    forwarded_filter: Literal["all", "only_forwarded", "ignore_forwarded"] = Field(default="all")
    ignore_own_messages: bool = Field(
        default=True,
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "filter": [
                        "all",
                        "any_contact",
                        "contact",
                        "group",
                        "channel",
                        "keywords",
                    ]
                }
            },
        },
    )
    include_media_data: bool = Field(default=False)

    model_config = ConfigDict(extra="ignore")


class WhatsAppReceiveOutput(BaseModel):
    message_id: Optional[str] = None
    sender: Optional[str] = None
    chat_id: Optional[str] = None
    message_type: Optional[str] = None
    text: Optional[str] = None
    timestamp: Optional[str] = None
    is_group: Optional[bool] = None
    is_from_me: Optional[bool] = None
    push_name: Optional[str] = None
    media: Optional[dict] = None
    group_info: Optional[dict] = None

    model_config = ConfigDict(extra="allow")


class WhatsAppReceiveNode(TriggerNode):
    type = "whatsappReceive"
    display_name = "WhatsApp Receive"
    subtitle = "Inbound Message"
    group = ("whatsapp", "trigger")
    description = "Trigger workflow when WhatsApp message is received"
    component_kind = "trigger"
    handles = ({"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},)
    task_queue = TaskQueue.TRIGGERS_EVENT
    mode = "event"
    event_type = "whatsapp_message_received"

    Params = WhatsAppReceiveParams
    Output = WhatsAppReceiveOutput

    def build_filter(self, params: WhatsAppReceiveParams) -> Callable[[Dict[str, Any]], bool]:
        from services.event_waiter import build_whatsapp_filter

        return build_whatsapp_filter(params.model_dump())

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: WhatsAppReceiveParams) -> WhatsAppReceiveOutput:
        raise NotImplementedError("Event triggers return via TriggerNode.execute, not the op body")
