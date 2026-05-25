"""WhatsApp DB — Wave 11.C migration.

Dual-purpose ActionNode + AI tool. 18-operation query interface to the
WhatsApp database (chat history, contacts, groups, channels, …).
Operation matrix is large; legacy handler dispatches all of it.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


class WhatsAppDbParams(BaseModel):
    """32-field schema matching main-branch baseline. Operation selector
    drives 18 conditional branches; each operation surfaces only its
    relevant fields via ``displayOptions.show``.
    """

    operation: Literal[
        "chat_history",
        "search_groups",
        "get_group_info",
        "get_contact_info",
        "list_contacts",
        "check_contacts",
        "list_channels",
        "get_channel_info",
        "channel_messages",
        "channel_stats",
        "channel_follow",
        "channel_unfollow",
        "channel_create",
        "channel_mute",
        "channel_mark_viewed",
        "newsletter_react",
        "newsletter_live_updates",
        "contact_profile_pic",
    ] = Field(default="chat_history", description="Operation to perform")

    # ===== CHAT HISTORY =====
    chat_type: Literal["individual", "group"] = Field(
        default="individual",
        description="Individual or group chat",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["chat_history"]}},
        },
    )
    phone: str = Field(
        default="",
        description="Contact phone number (no + prefix)",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": ["chat_history"],
                    "chat_type": ["individual"],
                }
            },
        },
    )
    group_id: str = Field(
        default="",
        description="Group JID (format: 123456789@g.us)",
        json_schema_extra={
            "component": "GroupIdSelector",
            "displayOptions": {
                "show": {
                    "operation": ["chat_history", "get_group_info"],
                    "chat_type": ["group"],
                }
            },
        },
    )
    group_filter: Literal["all", "contact"] = Field(
        default="all",
        description="Filter messages in group",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": ["chat_history"],
                    "chat_type": ["group"],
                }
            },
        },
    )
    sender_phone: str = Field(
        default="",
        description="Filter to messages from specific group member",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": ["chat_history"],
                    "chat_type": ["group"],
                    "group_filter": ["contact"],
                }
            },
        },
    )
    message_filter: Literal["all", "text_only"] = Field(
        default="all",
        description="Filter by message type",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["chat_history"]}},
        },
    )
    limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of messages (1-500)",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["chat_history"]}},
        },
    )
    offset: int = Field(
        default=0,
        ge=0,
        description="Number of messages to skip",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["chat_history"]}},
        },
    )

    # ===== MEDIA DOWNLOAD (chat_history + channel_messages) =====
    include_media_data: bool = Field(
        default=False,
        description="Download base64 media data (may be slow for many messages)",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": ["chat_history", "channel_messages"],
                }
            },
        },
    )

    # ===== SEARCH / LIST =====
    query: str = Field(
        default="",
        description="Search query (leave empty for all)",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": ["search_groups", "list_contacts"],
                }
            },
        },
    )

    # ===== GET GROUP INFO =====
    group_id_for_info: str = Field(
        default="",
        description="Group JID to get info for",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["get_group_info"]}},
        },
    )

    # ===== GET CONTACT INFO =====
    contact_phone: str = Field(
        default="",
        description="Phone number to get contact info for",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["get_contact_info"]}},
        },
    )

    # ===== CHECK CONTACTS =====
    phones: str = Field(
        default="",
        description="Comma-separated phone numbers to check WhatsApp registration",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["check_contacts"]}},
        },
    )

    # ===== CHANNELS (9 operations share channel_jid) =====
    channel_jid: str = Field(
        default="",
        description="Newsletter JID or invite link",
        json_schema_extra={
            "component": "ChannelJidSelector",
            "displayOptions": {
                "show": {
                    "operation": [
                        "get_channel_info",
                        "channel_messages",
                        "channel_stats",
                        "channel_follow",
                        "channel_unfollow",
                        "channel_mute",
                        "channel_mark_viewed",
                        "newsletter_react",
                        "newsletter_live_updates",
                    ]
                }
            },
        },
    )
    refresh: bool = Field(
        default=False,
        description="Bypass 24h cache and fetch fresh data",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": [
                        "list_channels",
                        "get_channel_info",
                        "channel_messages",
                    ]
                }
            },
        },
    )

    # ===== CHANNEL MESSAGES / STATS =====
    channel_count: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of messages to retrieve (1-100)",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": [
                        "channel_messages",
                        "channel_stats",
                    ]
                }
            },
        },
    )
    before_server_id: int = Field(
        default=0,
        ge=0,
        description="Pagination: get messages before this server ID (0 = latest)",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["channel_messages"]}},
        },
    )
    message_offset: int = Field(
        default=0,
        ge=0,
        description="Skip this many messages",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["channel_messages"]}},
        },
    )
    since: str = Field(
        default="",
        description="Unix timestamp: only messages after this time",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["channel_messages"]}},
        },
    )
    until: str = Field(
        default="",
        description="Unix timestamp: only messages before this time",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["channel_messages"]}},
        },
    )
    media_type: Literal["all", "image", "video", "audio", "document", "sticker"] = Field(
        default="all",
        description="Filter messages by media type",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["channel_messages"]}},
        },
    )
    search: str = Field(
        default="",
        description="Search for messages containing this text",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["channel_messages"]}},
        },
    )

    # ===== CHANNEL CREATE =====
    channel_name: str = Field(
        default="",
        description="Name for the new channel",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["channel_create"]}},
        },
    )
    channel_description: str = Field(
        default="",
        description="Optional description for the new channel",
        json_schema_extra={
            "rows": 3,
            "displayOptions": {"show": {"operation": ["channel_create"]}},
        },
    )
    picture: str = Field(
        default="",
        description="Optional base64-encoded profile picture",
        json_schema_extra={
            "rows": 3,
            "displayOptions": {"show": {"operation": ["channel_create"]}},
        },
    )

    # ===== CHANNEL MUTE =====
    mute: bool = Field(
        default=True,
        description="True to mute, false to unmute",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["channel_mute"]}},
        },
    )

    # ===== MARK VIEWED / LIVE UPDATES =====
    server_ids: str = Field(
        default="",
        description="Comma-separated message server IDs",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": [
                        "channel_mark_viewed",
                        "newsletter_live_updates",
                    ]
                }
            },
        },
    )

    # ===== NEWSLETTER REACT =====
    react_server_id: int = Field(
        default=0,
        ge=0,
        description="Server ID of the message to react to",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["newsletter_react"]}},
        },
    )
    reaction: str = Field(
        default="",
        description="Reaction emoji (empty to remove)",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["newsletter_react"]}},
        },
    )

    # ===== CONTACT PROFILE PICTURE =====
    profile_pic_jid: str = Field(
        default="",
        description="Contact JID or phone number",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["contact_profile_pic"]}},
        },
    )
    preview: bool = Field(
        default=False,
        description="Low-resolution preview instead of full picture",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["contact_profile_pic"]}},
        },
    )

    model_config = ConfigDict(extra="allow")


class WhatsAppDbOutput(BaseModel):
    operation: Optional[str] = None
    messages: Optional[list] = None
    contacts: Optional[list] = None
    groups: Optional[list] = None
    channels: Optional[list] = None
    total: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class WhatsAppDbNode(ActionNode):
    type = "whatsappDb"
    display_name = "WhatsApp DB"
    subtitle = "Query DB"
    group = ("whatsapp", "tool")
    description = "Query WhatsApp database (chat history, contacts, groups, channels)"
    component_kind = "square"
    tool_name = "whatsapp_db"
    tool_description = "Query WhatsApp database - list contacts, search groups, get contact/group info, retrieve chat history."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True

    Params = WhatsAppDbParams
    Output = WhatsAppDbOutput

    @Operation("query", cost={"service": "whatsapp", "action": "db_query", "count": 1})
    async def query(self, ctx: NodeContext, params: WhatsAppDbParams) -> Any:
        from ._base import handle_whatsapp_db

        response = await handle_whatsapp_db(
            node_id=ctx.node_id,
            node_type=self.type,
            parameters=params.model_dump(),
            context=ctx.raw,
        )
        if response.get("success"):
            return response.get("result") or response
        raise RuntimeError(response.get("error") or "WhatsApp DB query failed")
