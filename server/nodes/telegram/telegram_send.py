"""Telegram Send — Wave 11.C migration.

Workflow-only ActionNode (no AI-tool exposure). The Telegram bot
token lives in ``auth_service`` under the ``telegram`` credential id
(was ``telegram_bot_token`` pre-rename). Plugin delegates to the
legacy ``handle_telegram_send`` handler during thin-migration; 11.E
converts to a declarative ``TelegramCredential``.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

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
        description=(
            "Auto converts LLM markdown to Telegram HTML. "
            "Empty string = no parse mode (raw text)."
        ),
        json_schema_extra={
            "displayOptions": {"show": {"message_type": ["text", "photo", "document"]}},
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


class TelegramSendOutput(BaseModel):
    message_id: Optional[int] = None
    chat_id: Optional[int] = None
    sent: Optional[bool] = None

    model_config = ConfigDict(extra="allow")


class TelegramSendNode(ActionNode):
    type = "telegramSend"
    display_name = "Telegram Send"
    subtitle = "Send Message"
    group = ("social",)
    description = "Send text, photo, document, location, or contact via Telegram bot"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
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

        if params.recipient_type == "self":
            chat_id = service.owner_chat_id
            if not chat_id:
                try:
                    from services.plugin.deps import get_auth_service
                    saved = await get_auth_service().get_api_key("telegram_owner_chat_id")
                    if saved:
                        owner_id = int(saved)
                        await service.set_owner(owner_id)
                        chat_id = owner_id
                        log.info(f"[Telegram] Owner restored from credentials: {owner_id}")
                except Exception as e:
                    log.warning(f"[Telegram] Failed to restore owner: {e}")
            if not chat_id:
                raise RuntimeError(
                    "Bot owner not detected. Send any private message to your bot "
                    "on Telegram to auto-detect, or set TELEGRAM_OWNER_CHAT_ID in .env",
                )
        else:
            chat_id = params.chat_id
            if not chat_id:
                raise RuntimeError("chat_id is required")

        # Empty string = no parse mode (Python None); other values pass through
        # so the TelegramService can handle "Auto" and the formal parse modes.
        parse_mode = params.parse_mode or None
        reply_to = int(params.reply_to_message_id) if params.reply_to_message_id else None

        common = dict(
            chat_id=chat_id,
            disable_notification=params.silent,
            reply_to_message_id=reply_to,
        )
        mt = params.message_type
        if mt == "text":
            if not params.text:
                raise RuntimeError("text is required for text message")
            result = await service.send_message(
                text=params.text, parse_mode=parse_mode, **common,
            )
        elif mt == "photo":
            if not params.media_url:
                raise RuntimeError("media_url is required for photo message")
            result = await service.send_photo(
                photo=params.media_url, caption=params.caption or None,
                parse_mode=parse_mode, **common,
            )
        elif mt == "document":
            if not params.media_url:
                raise RuntimeError("media_url is required for document message")
            result = await service.send_document(
                document=params.media_url, caption=params.caption or None,
                parse_mode=parse_mode, **common,
            )
        elif mt == "location":
            if params.latitude is None or params.longitude is None:
                raise RuntimeError(
                    "latitude and longitude are required for location message",
                )
            result = await service.send_location(
                latitude=float(params.latitude),
                longitude=float(params.longitude),
                **common,
            )
        elif mt == "contact":
            if not params.phone_number or not params.first_name:
                raise RuntimeError(
                    "phone_number and first_name are required for contact message",
                )
            result = await service.send_contact(
                phone_number=params.phone_number,
                first_name=params.first_name,
                last_name=params.last_name or None,
                **common,
            )
        else:
            raise RuntimeError(f"Unsupported message type: {mt}")

        log.info(
            f"[Telegram] Message sent: type={mt}, chat={chat_id}, "
            f"msg_id={result.get('message_id')}",
        )
        return {
            "message_id": result.get("message_id"),
            "chat_id": result.get("chat_id"),
            "message_type": mt,
            "date": result.get("date"),
        }
