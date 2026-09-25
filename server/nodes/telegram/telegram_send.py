"""Telegram Send — Wave 11.C migration.

Workflow-only ActionNode (no AI-tool exposure). The Telegram bot
token lives in ``auth_service`` under the ``telegram`` credential id
(was ``telegram_bot_token`` pre-rename). Plugin delegates to the
legacy ``handle_telegram_send`` handler during thin-migration; 11.E
converts to a declarative ``TelegramCredential``.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from ._credentials import TelegramCredential


class TelegramSendParams(BaseModel):
    """13-field schema matching main-branch baseline. All field names use
    snake_case to match baseline directly (no camelCase aliases) — the
    frontend stores params under these exact keys and ``displayOptions``
    must reference them to stay consistent.
    """

    # ===== RECIPIENT =====
    recipient_type: Literal["self", "user", "group"] = Field(
        default="self",
        description="Send to bot owner (self), specific user, or group",
    )
    chat_id: str = Field(
        default="",
        description="Telegram chat ID (numeric) or @username",
        json_schema_extra={
            "displayOptions": {"show": {"recipient_type": ["user", "group"]}},
        },
    )

    # ===== MESSAGE TYPE =====
    message_type: Literal["text", "photo", "document", "location", "contact"] = Field(
        default="text",
        description="Type of message to send",
    )

    # ===== TEXT =====
    text: str = Field(
        default="",
        description="Text message content",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"message_type": ["text"]}},
        },
    )

    # ===== MEDIA (photo / document) =====
    media_url: str = Field(
        default="",
        description="URL of the media file or file_id from previous message",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["photo", "document"]}},
        },
    )
    caption: str = Field(
        default="",
        description="Optional caption for media",
        json_schema_extra={
            "rows": 2,
            "displayOptions": {"show": {"message_type": ["photo", "document"]}},
        },
    )

    # ===== LOCATION =====
    # Optional[float] with default None so the handler can tell the
    # "user omitted the field" case apart from "user set 0.0 deliberately"
    # (Null Island is a real place). The matching handler check is
    # ``if params.latitude is None or params.longitude is None: raise``.
    latitude: Optional[float] = Field(
        default=None,
        description="Location latitude (-90 to 90)",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )
    longitude: Optional[float] = Field(
        default=None,
        description="Location longitude (-180 to 180)",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["location"]}},
        },
    )

    # ===== CONTACT =====
    phone_number: str = Field(
        default="",
        description="Contact phone number (with country code)",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["contact"]}},
        },
    )
    first_name: str = Field(
        default="",
        description="Contact first name",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["contact"]}},
        },
    )
    last_name: str = Field(
        default="",
        description="Contact last name (optional)",
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["contact"]}},
        },
    )

    # ===== OPTIONS =====
    parse_mode: Literal["Auto", "", "HTML", "Markdown", "MarkdownV2"] = Field(
        default="Auto",
        description=("Auto converts LLM markdown to Telegram HTML. " "Empty string = no parse mode (raw text)."),
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["text", "photo", "document"]}},
            "uiHints": {
                "options": [
                    {"name": "Auto", "value": "Auto"},
                    {"name": "None (raw text)", "value": ""},
                    {"name": "HTML", "value": "HTML"},
                    {"name": "Markdown", "value": "Markdown"},
                    {"name": "Markdown V2", "value": "MarkdownV2"},
                ],
            },
        },
    )
    silent: bool = Field(
        default=False,
        description="Send message without notification sound",
    )
    reply_to_message_id: int = Field(
        default=0,
        description="If > 0, sends the message as a reply to this message ID",
    )

    model_config = ConfigDict(extra="ignore")

    @field_validator("chat_id", mode="before")
    @classmethod
    def _coerce_chat_id(cls, value: Any) -> Any:
        # The trigger emits chat_id as an int, and a whole-string template
        # ({{telegramreceive.chat_id}}) keeps that type. Pydantic v2 will
        # not turn an int into a str on its own.
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value


class TelegramSendOutput(BaseModel):
    message_id: Optional[int] = None
    chat_id: Optional[int] = None
    sent: Optional[bool] = None
    # ``message_type`` and ``date`` were always returned but never declared,
    # surviving only via ``extra="allow"``. Declaring them makes them visible
    # in the frontend data picker; the runtime payload is unchanged.
    message_type: Optional[str] = None
    date: Optional[str] = None
    # Populated when one logical send became several Telegram messages:
    # text over the 4096 cap, or a caption over 1024 whose remainder was
    # threaded underneath as a follow-up.
    parts: Optional[int] = None
    message_ids: Optional[List[int]] = None
    caption_truncated: Optional[bool] = None
    follow_up_message_ids: Optional[List[int]] = None

    model_config = ConfigDict(extra="allow")


class TelegramSendNode(ActionNode):
    type = "telegramSend"
    display_name = "Telegram Send"
    subtitle = "Send Message"
    group = ("social",)
    description = "Send text, photo, document, location, or contact via Telegram bot"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    credentials = (TelegramCredential,)
    task_queue = TaskQueue.MESSAGING

    Params = TelegramSendParams
    Output = TelegramSendOutput

    @Operation("send", cost={"service": "telegram", "action": "send", "count": 1})
    async def send(self, ctx: NodeContext, params: TelegramSendParams) -> Any:
        """Inlined from handlers/telegram.py:handle_telegram_send (Wave 11.D.1)."""
        from core.logging import get_logger

        from ._service import get_telegram_service

        log = get_logger(__name__)
        service = get_telegram_service()
        if not service.connected:
            raise RuntimeError(
                "Telegram bot not connected. Add bot token in Credentials.",
            )

        # Validation + dispatch live in _send.py so the WebSocket command path
        # cannot drift from this one.
        from ._send import perform_send, resolve_chat_id

        chat_id = await resolve_chat_id(service, params)
        mt = params.message_type
        result = await perform_send(service, chat_id, params)

        log.info(
            f"[Telegram] Message sent: type={mt}, chat={chat_id}, " f"msg_id={result.get('message_id')}",
        )
        # ``sent`` has been declared on TelegramSendOutput since the node was
        # written but was never populated, and ``_serialize_result`` dumps with
        # ``exclude_unset=True`` — so the data picker advertised a field that
        # never materialised. Emit it.
        payload = {
            "message_id": result.get("message_id"),
            "chat_id": result.get("chat_id"),
            "message_type": mt,
            "date": result.get("date"),
            "sent": True,
        }
        # Multi-part sends (text over 4096, or a caption that spilled into a
        # threaded follow-up) report the whole chain, not just the first id.
        for key in ("parts", "message_ids", "caption_truncated", "follow_up_message_ids"):
            if key in result:
                payload[key] = result[key]
        return payload
