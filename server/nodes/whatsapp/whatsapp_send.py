"""WhatsApp Send — Wave 11.C migration.

Dual-purpose ActionNode + AI tool. Sends text / media / location /
contact / sticker via the whatsapp-rpc bridge. Delegates to the
existing handler — recipient/message-type matrix is already encoded
there with full media + newsletter-channel support.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class WhatsAppSendParams(BaseModel):
    """Full 24-field schema matching main-branch baseline. Heavy
    ``displayOptions`` cascading keyed off ``recipient_type`` (4 branches),
    ``message_type`` (8 branches), ``media_source`` (3 branches),
    and ``is_reply`` (2 branches).
    """

    # ===== RECIPIENT =====
    recipient_type: Literal["self", "phone", "group", "channel"] = Field(
        default="self",
        description="Send to self (connected phone), individual, group, or channel",
    )
    phone: str = Field(
        default="",
        description="Recipient phone number (without + prefix)",
        json_schema_extra={
            "displayOptions": {"show": {"recipient_type": ["phone"]}},
        },
    )
    group_id: str = Field(
        default="",
        description="Group JID (format: 123456789@g.us). Use Load button to select.",
        json_schema_extra={
            "component": "GroupIdSelector",
            "loadOptionsMethod": "whatsappGroups",
            "displayOptions": {"show": {"recipient_type": ["group"]}},
        },
    )
    channel_jid: str = Field(
        default="",
        description=("Newsletter channel JID (format: 120363...@newsletter). " "Admin/owner role required to send."),
        json_schema_extra={
            "component": "ChannelJidSelector",
            "loadOptionsMethod": "whatsappChannels",
            "displayOptions": {"show": {"recipient_type": ["channel"]}},
        },
    )

    # ===== MESSAGE TYPE =====
    message_type: Literal[
        "text",
        "image",
        "video",
        "audio",
        "document",
        "sticker",
        "location",
        "contact",
    ] = Field(
        default="text",
        description=("Type of message. Channels only support: text, image, video, audio, document."),
    )

    # ===== TEXT =====
    message: str = Field(
        default="",
        description="Text message content",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"message_type": ["text"]}},
        },
    )
    format_markdown: bool = Field(
        default=True,
        description=("Convert LLM markdown (bold, italic, code, lists) to WhatsApp-native formatting"),
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["text"]}},
        },
    )

    # ===== MEDIA (image / video / audio / document / sticker) =====
    media_source: Literal["base64", "file", "url"] = Field(
        default="base64",
        description="Source of media data",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "message_type": [
                        "image",
                        "video",
                        "audio",
                        "document",
                        "sticker",
                    ]
                }
            },
        },
    )
    media_data: str = Field(
        default="",
        description="Base64-encoded media data",
        json_schema_extra={
            "rows": 3,
            "displayOptions": {
                "show": {
                    "message_type": ["image", "video", "audio", "document", "sticker"],
                    "media_source": ["base64"],
                }
            },
        },
    )
    file_path: str = Field(
        default="",
        description="Server file path or uploaded filename",
        json_schema_extra={
            "widget": "file",
            "accept": "*/*",
            "displayOptions": {
                "show": {
                    "message_type": ["image", "video", "audio", "document", "sticker"],
                    "media_source": ["file"],
                }
            },
        },
    )
    media_url: str = Field(
        default="",
        description="HTTPS URL to download media from",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "message_type": ["image", "video", "audio", "document", "sticker"],
                    "media_source": ["url"],
                }
            },
        },
    )
    mime_type: str = Field(
        default="",
        description="MIME type (auto-detected if empty). e.g. image/jpeg, video/mp4",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "message_type": [
                        "image",
                        "video",
                        "audio",
                        "document",
                        "sticker",
                    ]
                }
            },
        },
    )
    caption: str = Field(
        default="",
        description="Optional caption for media",
        json_schema_extra={
            "rows": 2,
            "displayOptions": {"show": {"message_type": ["image", "video", "document"]}},
        },
    )
    filename: str = Field(
        default="",
        description="Document filename (e.g. document.pdf)",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["document"]}},
        },
    )

    # ===== LOCATION =====
    latitude: float = Field(
        default=0.0,
        description="Location latitude (-90 to 90)",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )
    longitude: float = Field(
        default=0.0,
        description="Location longitude (-180 to 180)",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )
    location_name: str = Field(
        default="",
        description="Display name for location (e.g. 'San Francisco')",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )
    address: str = Field(
        default="",
        description="Address text (e.g. 'California, USA')",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )

    # ===== CONTACT =====
    contact_name: str = Field(
        default="",
        description="Display name for contact",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["contact"]}},
        },
    )
    vcard: str = Field(
        default="",
        description="vCard 3.0 format string",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"message_type": ["contact"]}},
        },
    )

    # ===== REPLY / QUOTE =====
    is_reply: bool = Field(
        default=False,
        description="Quote an existing message",
    )
    reply_message_id: str = Field(
        default="",
        description="ID of message to quote",
        json_schema_extra={
            "displayOptions": {"show": {"is_reply": [True]}},
        },
    )
    reply_sender: str = Field(
        default="",
        description="Sender JID of quoted message",
        json_schema_extra={
            "displayOptions": {"show": {"is_reply": [True]}},
        },
    )
    reply_content: str = Field(
        default="",
        description="Text preview of quoted message",
        json_schema_extra={
            "displayOptions": {"show": {"is_reply": [True]}},
        },
    )

    model_config = ConfigDict(extra="allow")


class WhatsAppSendOutput(BaseModel):
    message_id: Optional[str] = None
    sent: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class WhatsAppSendNode(ActionNode):
    type = "whatsappSend"
    display_name = "WhatsApp Send"
    subtitle = "Send Message"
    group = ("whatsapp", "tool")
    description = "Send WhatsApp messages (text, media, location, contact, sticker)"
    component_kind = "square"
    tool_name = "whatsapp_send"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True

    Params = WhatsAppSendParams
    Output = WhatsAppSendOutput

    @Operation("send", cost={"service": "whatsapp", "action": "send", "count": 1})
    async def send(self, ctx: NodeContext, params: WhatsAppSendParams) -> Any:
        from ._base import handle_whatsapp_send

        response = await handle_whatsapp_send(
            node_id=ctx.node_id,
            node_type=self.type,
            parameters=params.model_dump(by_alias=False),
            context=ctx.raw,
        )
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "WhatsApp send failed")
