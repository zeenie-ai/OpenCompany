"""discordSend — post a message to a channel or a user.

Separate from ``discordAction`` for the same reason telegram and
whatsapp_business split send from the rest: it is the high-frequency
agent-facing operation, and a focused schema makes a far better LLM tool than
a twenty-operation union.
"""

from __future__ import annotations

import json as jsonlib
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, NodeUserError, Operation, TaskQueue
from services.plugin.approval import ApprovalSpec

from . import _base
from ._accounts import DEFAULT_ACCOUNT
from ._base import AccountScopedNode
from ._credentials import DiscordBotCredential

# Discord counts message content in Unicode code points, so len() is the right
# measure here -- unlike Telegram, which measures UTF-16 code units.
MAX_CONTENT = 2000

# Suppresses the link/embed preview on the sent message.
_FLAG_SUPPRESS_EMBEDS = 1 << 2


def split_content(text: str, limit: int = MAX_CONTENT) -> List[str]:
    """Split at the cleanest boundary that still fills most of a chunk.

    Preferring a paragraph, then a line, then a sentence keeps code blocks and
    lists intact far more often than a hard cut. The halfway floor stops a
    boundary near the start from producing a stream of tiny messages.
    """
    if not text:
        return []

    chunks: List[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = -1
        for boundary in ("\n\n", "\n", ". ", "! ", "? ", " "):
            found = window.rfind(boundary)
            if found > limit // 2:
                cut = found + len(boundary)
                break
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


class DiscordSendParams(BaseModel):
    account_id: str = Field(
        default="",
        description="Which Discord bot to send as. Blank uses the default credential.",
        json_schema_extra={"loadOptionsMethod": "discordAccounts"},
    )
    target_type: Literal["channel", "user"] = Field(
        default="channel",
        description="Post in a channel, or open a DM with a user.",
    )
    channel_id: str = Field(
        default="",
        description="Channel ID. Enable Developer Mode in Discord to copy one.",
        json_schema_extra={"displayOptions": {"show": {"target_type": ["channel"]}}},
    )
    user_id: str = Field(
        default="",
        description="User ID. A DM channel is opened automatically.",
        json_schema_extra={"displayOptions": {"show": {"target_type": ["user"]}}},
    )
    message: str = Field(
        default="",
        description="Message text. Longer than 2000 characters is split across messages.",
        json_schema_extra={"rows": 4},
    )
    embeds: List[dict] = Field(
        default_factory=list,
        description="Rich embed objects. Up to 10, 6000 characters across all of them.",
    )
    attachment: Any = Field(
        default=None,
        description="A file to attach.",
        json_schema_extra={"widget": "file", "accept": "*/*"},
    )
    reply_to_message_id: str = Field(
        default="",
        description="Reply to a message by ID.",
    )
    suppress_embeds: bool = Field(
        default=False,
        description="Hide link previews on this message.",
    )
    tts: bool = Field(
        default=False,
        description="Send as a text-to-speech message.",
    )

    model_config = ConfigDict(extra="ignore")


class DiscordSendOutput(BaseModel):
    message_id: Optional[str] = None
    channel_id: Optional[str] = None
    # Populated when the text was long enough to split.
    parts: Optional[int] = None
    message_ids: List[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class DiscordSendNode(AccountScopedNode):
    type = "discordSend"
    display_name = "Discord Send"
    subtitle = "Send Message"
    group = ("discord",)
    description = "Send a message, embed or file to a Discord channel or user"
    component_kind = "square"
    tool_name = "discord_send"
    tool_description = (
        "Send a message to a Discord channel or user. Supports plain text, rich "
        "embeds and a file attachment."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (DiscordBotCredential,)
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True
    # Which bot sends is operator configuration, not a model decision: every
    # tool path keeps it from the model (AccountScopedNode strips it again).
    server_controlled_fields = frozenset({"account_id"})
    approval = ApprovalSpec(
        channel="Discord",
        action="Send a Discord message",
        recipient=("channel_id", "user_id"),
        body=("message",),
        max_length=2000,
    )
    # Canvas handles are auto-hidden for usable_as_tool nodes; this one is
    # meant to be wired into a workflow as well.
    hide_input_handle = False
    hide_output_handle = False

    Params = DiscordSendParams
    Output = DiscordSendOutput

    @Operation("send", cost={"service": "discord", "action": "send", "count": 1})
    async def send(self, ctx: NodeContext, params: DiscordSendParams) -> DiscordSendOutput:
        account_id = params.account_id or DEFAULT_ACCOUNT
        channel_id = await self._resolve_channel(params, account_id)

        if not params.message.strip() and not params.embeds and params.attachment in (None, ""):
            raise NodeUserError("Provide a message, an embed or an attachment to send.")

        if len(params.embeds) > 10:
            raise NodeUserError(f"Discord accepts at most 10 embeds; got {len(params.embeds)}.")

        chunks = split_content(params.message) or [""]
        flags = _FLAG_SUPPRESS_EMBEDS if params.suppress_embeds else 0

        message_ids: List[str] = []
        first: Dict[str, Any] = {}

        for index, chunk in enumerate(chunks):
            is_last = index == len(chunks) - 1
            body: Dict[str, Any] = {"content": chunk, "tts": params.tts}
            if flags:
                body["flags"] = flags
            # Embeds and the attachment ride the final message so they appear
            # after the text they belong to.
            if is_last and params.embeds:
                body["embeds"] = params.embeds
            if index == 0 and params.reply_to_message_id.strip():
                body["message_reference"] = {"message_id": params.reply_to_message_id.strip()}

            if is_last and params.attachment not in (None, ""):
                sent = await self._post_with_attachment(ctx, channel_id, body, params, account_id)
            else:
                sent = await _base.post(
                    f"channels/{channel_id}/messages", body, account_id=account_id
                )

            message_id = str(sent.get("id") or "")
            if message_id:
                message_ids.append(message_id)
            if index == 0:
                first = sent

        return DiscordSendOutput(
            message_id=str(first.get("id") or ""),
            channel_id=str(first.get("channel_id") or channel_id),
            parts=len(chunks) if len(chunks) > 1 else None,
            message_ids=message_ids,
        )

    async def _resolve_channel(self, params: DiscordSendParams, account_id: str) -> str:
        """Channel id to post to, opening a DM channel when targeting a user."""
        if params.target_type == "user":
            user_id = params.user_id.strip()
            if not user_id:
                raise NodeUserError("A user ID is required to send a direct message.")
            dm = await _base.post("users/@me/channels", {"recipient_id": user_id}, account_id=account_id)
            return str(dm.get("id") or "")

        channel_id = params.channel_id.strip()
        if not channel_id:
            raise NodeUserError("A channel ID is required. Enable Developer Mode in Discord to copy one.")
        return channel_id

    async def _post_with_attachment(
        self,
        ctx: NodeContext,
        channel_id: str,
        body: Dict[str, Any],
        params: DiscordSendParams,
        account_id: str,
    ) -> Dict[str, Any]:
        """Send as multipart, with the JSON body alongside the file part.

        The file is passed as bytes rather than a handle: the request layer may
        replay the same kwargs, and a consumed handle would resend as empty.
        """
        from services.media import coerce_file_param

        filename, blob = coerce_file_param(params.attachment, ctx=ctx)
        body["attachments"] = [{"id": 0, "filename": filename}]
        return await _base.post(
            f"channels/{channel_id}/messages",
            account_id=account_id,
            files={"files[0]": (filename, blob)},
            data={"payload_json": jsonlib.dumps(body)},
        )


__all__ = ["DiscordSendNode", "DiscordSendOutput", "DiscordSendParams", "split_content"]
