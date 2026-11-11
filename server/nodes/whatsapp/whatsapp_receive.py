"""WhatsApp Receive — Wave 11.C migration (event-based trigger).

Inbound WhatsApp message events arrive via the whatsapp-rpc bridge.
``build_filter`` delegates to the existing ``build_whatsapp_filter``
to preserve the full sender/group/forwarded/keyword filter matrix.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import NodeContext, Operation, TaskQueue, TriggerNode

logger = get_logger(__name__)

# Strong references, so a scheduled bridge start is not garbage-collected
# before it finishes.
_background_tasks: Set[asyncio.Task] = set()


async def _start_bridge_for_deployment(node_id: str, workflow_id: str) -> None:
    from ._service import handle_whatsapp_start

    try:
        result = await handle_whatsapp_start()
    except Exception:
        logger.warning(
            "[WhatsApp] could not start the bridge for a deployed whatsappReceive",
            node_id=node_id,
            workflow_id=workflow_id,
            exc_info=True,
        )
        return
    if not result.get("success"):
        # Armed anyway: pressing Start in Connectors starts the bridge, and
        # messages flow from then on.
        logger.warning(
            "[WhatsApp] deployed whatsappReceive is waiting: %s",
            result.get("error"),
            node_id=node_id,
            workflow_id=workflow_id,
        )


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


class WhatsAppGroupInfo(BaseModel):
    group_jid: Optional[str] = None
    sender_jid: Optional[str] = None
    sender_phone: Optional[str] = None
    sender_name: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class WhatsAppReceiveOutput(BaseModel):
    message_id: Optional[str] = None
    sender: Optional[str] = None
    sender_phone: Optional[str] = None
    chat_id: Optional[str] = None
    message_type: Optional[str] = None
    text: Optional[str] = None
    timestamp: Optional[str] = None
    is_group: Optional[bool] = None
    is_from_me: Optional[bool] = None
    push_name: Optional[str] = None
    media: Optional[dict] = None
    group_info: Optional[WhatsAppGroupInfo] = None

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

    @classmethod
    async def prepare_deployment(
        cls,
        *,
        node_id: str,
        workflow_id: str,
        parameters: Dict[str, Any],
    ) -> None:
        """Start the WhatsApp bridge for a deployed trigger.

        The status refresh at boot only probes the bridge and never starts it,
        so without this a deployed trigger would receive nothing after a
        backend restart until the owner pressed Start. Run in the background:
        starting the bridge must not hold up Start or the boot re-arm.
        """
        task = asyncio.create_task(_start_bridge_for_deployment(node_id, workflow_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    def build_filter(self, params: WhatsAppReceiveParams) -> Callable[[Dict[str, Any]], bool]:
        from services.event_waiter import build_whatsapp_filter

        return build_whatsapp_filter(params.model_dump())

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: WhatsAppReceiveParams) -> WhatsAppReceiveOutput:
        raise NotImplementedError("Event triggers return via TriggerNode.execute, not the op body")
