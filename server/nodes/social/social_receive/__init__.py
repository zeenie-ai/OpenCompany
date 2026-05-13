"""Social Receive — Wave 11.C migration.

Normalises messages from any platform trigger (whatsappReceive,
telegramReceive, etc.) into a unified shape and fans out across four
right-side output handles: message / media / contact / metadata.

Reads ``connected_outputs`` from the executor's enriched context;
delegates the per-platform unwrap to the existing handler.
"""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue


_SOCIAL_SIZE = {"width": 260, "height": 160}


class SocialReceiveParams(BaseModel):
    """9-field schema matching main-branch baseline. Normaliser for
    messages from any upstream platform trigger; filters by channel /
    content-type / sender before fanning out across four output handles.
    Snake_case throughout, no aliases.
    """

    channel_filter: Literal[
        "all", "whatsapp", "telegram", "discord", "slack", "signal",
        "sms", "webchat", "email", "matrix", "teams",
    ] = Field(
        default="all",
        description="Filter by source platform",
    )
    message_type_filter: Literal[
        "all", "text", "image", "video", "audio", "document",
        "sticker", "location", "contact", "poll", "reaction",
    ] = Field(
        default="all",
        description="Filter by content type",
    )
    sender_filter: Literal[
        "all", "any_contact", "contact", "group", "keywords",
    ] = Field(
        default="all",
        description="Filter which messages pass through",
    )
    contact_phone: str = Field(
        default="",
        description="Contact phone number (no + prefix)",
        json_schema_extra={
            "displayOptions": {"show": {"sender_filter": ["contact"]}},
        },
    )
    group_id: str = Field(
        default="",
        description="Group identifier",
        json_schema_extra={
            "displayOptions": {"show": {"sender_filter": ["group"]}},
        },
    )
    keywords: str = Field(
        default="",
        description="Comma-separated keywords (case-insensitive)",
        json_schema_extra={
            "displayOptions": {"show": {"sender_filter": ["keywords"]}},
        },
    )
    ignore_own_messages: bool = Field(
        default=True,
        description="Exclude messages sent by the connected account",
    )
    ignore_bots: bool = Field(
        default=False,
        description="Exclude messages from other bots",
    )
    include_media_data: bool = Field(
        default=False,
        description="Include base64 media payload (memory-intensive)",
    )

    model_config = ConfigDict(extra="allow")


class SocialReceiveOutput(BaseModel):
    message: Optional[Any] = None
    media: Optional[Any] = None
    contact: Optional[Any] = None
    metadata: Optional[Any] = None

    model_config = ConfigDict(extra="allow")


class SocialReceiveNode(ActionNode):
    type = "socialReceive"
    display_name = "Social Receive"
    subtitle = "Normalize Message"
    group = ("social",)
    description = "Normalizes messages from platform triggers into unified format"
    component_kind = "agent"   # multi-handle layout uses AIAgentNode
    handles = (
        {"name": "output-message",  "kind": "output", "position": "right", "offset": "20%", "label": "Message",  "role": "main"},
        {"name": "output-media",    "kind": "output", "position": "right", "offset": "40%", "label": "Media",    "role": "main"},
        {"name": "output-contact",  "kind": "output", "position": "right", "offset": "60%", "label": "Contact",  "role": "main"},
        {"name": "output-metadata", "kind": "output", "position": "right", "offset": "80%", "label": "Metadata", "role": "main"},
    )
    ui_hints = _SOCIAL_SIZE
    annotations = {"destructive": False, "readonly": True, "open_world": False}
    task_queue = TaskQueue.DEFAULT

    Params = SocialReceiveParams
    Output = SocialReceiveOutput

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        context,
    ) -> Dict[str, Any]:
        """Override the base envelope wrap so the ``filtered`` metadata
        flag the handler emits survives to the top-level result dict.
        Without this override, BaseNode._wrap_success would re-wrap
        ``response.get("result")`` and drop every sibling key, including
        ``filtered`` and ``reason``.
        """
        import time

        from .._base import handle_social_receive

        start_time = time.time()
        try:
            params_obj = self._validate_params(parameters)
        except Exception as e:
            return self._wrap_error(start_time=start_time, error=f"Invalid parameters: {e}")

        outputs = context.raw.get("connected_outputs") or {}
        source_nodes = context.raw.get("source_nodes") or []
        response = await handle_social_receive(
            node_id=node_id, node_type=self.type,
            parameters=params_obj.model_dump(), context=context.raw,
            outputs=outputs, source_nodes=source_nodes,
        )
        return response

    @Operation("normalize")
    async def normalize(self, ctx: NodeContext, params: SocialReceiveParams) -> Any:
        # Kept for the contract invariant that every plugin declares an
        # @Operation. Real dispatch goes through :meth:`execute` above.
        raise NotImplementedError("socialReceive uses execute() override")


# socialReceive needs the executor's connected_outputs injection
# (alongside console / pythonExecutor / webhookResponse / socialReceive
# already in _NEEDS_CONNECTED_OUTPUTS); the legacy entry stays.
