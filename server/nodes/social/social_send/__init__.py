"""Social Send — Wave 11.C migration.

Unified send action for any social platform (WhatsApp / Telegram /
Discord / Slack / SMS / etc.). Multi-input handle topology:
message / media / contact / metadata as separate left-side handles.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


_SOCIAL_SIZE = {"width": 260, "height": 160}


class SocialSendParams(BaseModel):
    """40-field schema matching main-branch baseline. Unified send across
    10 platforms × 11 message types × 5 recipient types. Heavy
    ``displayOptions`` cascading.
    """

    # ===== CHANNEL / PLATFORM =====
    channel: Literal[
        "whatsapp", "telegram", "discord", "slack", "signal",
        "sms", "webchat", "email", "matrix", "teams",
    ] = Field(
        default="whatsapp",
        description="Target chat platform",
    )

    # ===== RECIPIENT =====
    recipient_type: Literal["phone", "group", "channel", "user", "chat"] = Field(
        default="phone",
        description="Type of recipient",
    )
    phone: str = Field(
        default="",
        description="Recipient phone number (no + prefix)",
        json_schema_extra={
            "displayOptions": {"show": {"recipient_type": ["phone"]}},
        },
    )
    group_id: str = Field(
        default="",
        description="Group identifier",
        json_schema_extra={
            "displayOptions": {"show": {"recipient_type": ["group"]}},
        },
    )
    channel_id: str = Field(
        default="",
        description="Channel identifier",
        json_schema_extra={
            "displayOptions": {"show": {"recipient_type": ["channel"]}},
        },
    )
    user_id: str = Field(
        default="",
        description="User identifier",
        json_schema_extra={
            "displayOptions": {"show": {"recipient_type": ["user"]}},
        },
    )
    chat_id: str = Field(
        default="",
        description="Generic chat identifier",
        json_schema_extra={
            "displayOptions": {"show": {"recipient_type": ["chat"]}},
        },
    )
    thread_id: str = Field(
        default="",
        description="Thread to reply in (optional)",
    )

    # ===== MESSAGE TYPE =====
    message_type: Literal[
        "text", "image", "video", "audio", "document",
        "sticker", "location", "contact", "poll", "buttons", "list",
    ] = Field(default="text")

    # ===== TEXT =====
    message: str = Field(
        default="",
        description="Text message content",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"message_type": ["text"]}},
        },
    )
    format: Literal["plain", "markdown", "html"] = Field(
        default="plain",
        description="Text formatting",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["text"]}},
        },
    )

    # ===== MEDIA (image / video / audio / document / sticker) =====
    media_source: Literal["url", "base64", "file"] = Field(
        default="url",
        description="Source of media data",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": [
                "image", "video", "audio", "document", "sticker",
            ]}},
        },
    )
    media_url: str = Field(
        default="",
        description="URL to download media from",
        json_schema_extra={
            "displayOptions": {"show": {
                "message_type": ["image", "video", "audio", "document", "sticker"],
                "media_source": ["url"],
            }},
        },
    )
    media_data: str = Field(
        default="",
        description="Base64-encoded media data",
        json_schema_extra={
            "rows": 3,
            "displayOptions": {"show": {
                "message_type": ["image", "video", "audio", "document", "sticker"],
                "media_source": ["base64"],
            }},
        },
    )
    file_path: str = Field(
        default="",
        description="Server file path",
        json_schema_extra={
            "displayOptions": {"show": {
                "message_type": ["image", "video", "audio", "document", "sticker"],
                "media_source": ["file"],
            }},
        },
    )
    mime_type: str = Field(
        default="",
        description="MIME type (auto-detected if empty)",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": [
                "image", "video", "audio", "document", "sticker",
            ]}},
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
        description="Document filename",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["document"]}},
        },
    )

    # ===== LOCATION =====
    latitude: float = Field(
        default=0.0,
        description="Location latitude",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )
    longitude: float = Field(
        default=0.0,
        description="Location longitude",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )
    location_name: str = Field(
        default="",
        description="Display name for location",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )
    address: str = Field(
        default="",
        description="Address text",
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
    contact_phone: str = Field(
        default="",
        description="Contact phone number",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["contact"]}},
        },
    )
    vcard: str = Field(
        default="",
        description="vCard 3.0 format string (optional if phone provided)",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"message_type": ["contact"]}},
        },
    )

    # ===== POLL =====
    poll_question: str = Field(
        default="",
        description="Poll question",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["poll"]}},
        },
    )
    poll_options: str = Field(
        default="",
        description="Comma-separated poll options",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["poll"]}},
        },
    )
    poll_allow_multiple: bool = Field(
        default=False,
        description="Allow multiple selections",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["poll"]}},
        },
    )

    # ===== BUTTONS =====
    button_text: str = Field(
        default="",
        description="Text displayed above buttons",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["buttons"]}},
        },
    )
    buttons: str = Field(
        default="[]",
        description="JSON array: [{id, text}, ...]",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"message_type": ["buttons"]}},
        },
    )

    # ===== LIST =====
    list_title: str = Field(
        default="",
        description="Title for the list",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["list"]}},
        },
    )
    list_button_text: str = Field(
        default="View Options",
        description="Text for the list button",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["list"]}},
        },
    )
    list_sections: str = Field(
        default="[]",
        description="JSON array of sections with rows",
        json_schema_extra={
            "rows": 6,
            "displayOptions": {"show": {"message_type": ["list"]}},
        },
    )

    # ===== REPLY / QUOTE =====
    reply_to_message: bool = Field(
        default=False,
        description="Quote an existing message",
    )
    reply_message_id: str = Field(
        default="",
        description="ID of message to reply to",
        json_schema_extra={
            "displayOptions": {"show": {"reply_to_message": [True]}},
        },
    )
    reply_to_current: bool = Field(
        default=False,
        description="Reply to the message that triggered this workflow",
        json_schema_extra={
            "displayOptions": {"show": {"reply_to_message": [True]}},
        },
    )

    # ===== SEND OPTIONS =====
    audio_as_voice: bool = Field(
        default=False,
        description="Send audio as voice message",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["audio"]}},
        },
    )
    disable_preview: bool = Field(
        default=False,
        description="Disable link preview in text messages",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["text"]}},
        },
    )
    silent: bool = Field(
        default=False,
        description="Send without notification sound",
    )
    protect_content: bool = Field(
        default=False,
        description="Prevent forwarding/saving (if supported)",
    )

    model_config = ConfigDict(extra="allow")


class SocialSendOutput(BaseModel):
    sent: Optional[bool] = None
    message_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class SocialSendNode(ActionNode):
    type = "socialSend"
    display_name = "Social Send"
    subtitle = "Send Message"
    group = ("social", "tool")
    description = "Unified send action for any social platform"
    component_kind = "agent"   # multi-handle layout uses AIAgentNode component
    handles = (
        {"name": "input-message",  "kind": "input", "position": "left", "offset": "15%", "label": "Message",  "role": "main"},
        {"name": "input-media",    "kind": "input", "position": "left", "offset": "35%", "label": "Media",    "role": "main"},
        {"name": "input-contact",  "kind": "input", "position": "left", "offset": "55%", "label": "Contact",  "role": "main"},
        {"name": "input-metadata", "kind": "input", "position": "left", "offset": "75%", "label": "Metadata", "role": "main"},
    )
    ui_hints = _SOCIAL_SIZE
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True

    Params = SocialSendParams
    Output = SocialSendOutput

    @Operation("send", cost={"service": "social", "action": "send", "count": 1})
    async def send(self, ctx: NodeContext, params: SocialSendParams) -> Any:
        from .._base import handle_social_send
        response = await handle_social_send(
            node_id=ctx.node_id, node_type=self.type,
            parameters=params.model_dump(), context=ctx.raw,
        )
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "social send failed")
