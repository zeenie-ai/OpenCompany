"""discordReceive — fire a workflow on an inbound Discord message.

Attachments arrive as metadata only. Downloading here would put the payload
into the trigger's result, which is persisted, broadcast and replayed into LLM
context -- and on a deployed workflow this node body never runs at all, since
the engine marks the trigger pre-executed and passes its output through
verbatim. Wire discordAction[download_attachments] downstream to fetch them.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Dict, List, Literal, Optional, Set

from pydantic import BaseModel, ConfigDict, Field

from core.logging import get_logger
from services.plugin import NodeContext, NodeUserError, Operation, TaskQueue, TriggerNode

from ._accounts import DEFAULT_ACCOUNT
from ._credentials import DiscordBotCredential
from ._events import MESSAGE_RECEIVED_TYPE

logger = get_logger(__name__)

# Strong references, so a scheduled gateway start is not garbage-collected
# before it finishes.
_background_tasks: Set[asyncio.Task] = set()


async def _start_gateway_for_deployment(account_id: str, node_id: str, workflow_id: str) -> None:
    from ._gateway import get_gateway

    try:
        gateway = await get_gateway(account_id)
        await gateway.start()
    except NodeUserError as exc:
        # Armed anyway: connecting the bot in Connectors starts the gateway,
        # and messages flow from then on.
        logger.warning(
            "[Discord] deployed discordReceive is waiting: %s",
            exc,
            account_id=account_id,
            node_id=node_id,
            workflow_id=workflow_id,
        )
    except Exception:
        logger.warning(
            "[Discord] could not start the gateway for a deployed discordReceive",
            account_id=account_id,
            node_id=node_id,
            workflow_id=workflow_id,
            exc_info=True,
        )


class DiscordAttachment(BaseModel):
    """A reference to a Discord attachment, never its bytes.

    ``url`` is signed and expires, so it is resolved at download time rather
    than trusted from a stored node output.
    """

    id: Optional[str] = None
    filename: Optional[str] = None
    size: Optional[int] = None
    url: Optional[str] = None
    content_type: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class DiscordReceiveParams(BaseModel):
    account_id: str = Field(
        default="",
        description="Which bot to listen as. Blank listens on the default credential.",
        json_schema_extra={"loadOptionsMethod": "discordAccounts"},
    )
    scope: Literal["all", "guild", "dm"] = Field(
        default="all",
        description="Listen to server channels, direct messages, or both.",
    )
    guild_id: str = Field(
        default="",
        description="Only fire for this server.",
        json_schema_extra={"displayOptions": {"show": {"scope": ["all", "guild"]}}},
    )
    channel_id: str = Field(
        default="",
        description="Only fire for this channel.",
    )
    author_id: str = Field(
        default="",
        description="Only fire for messages from this user.",
    )
    keywords: str = Field(
        default="",
        description="Comma-separated words; fires only if the message contains one.",
    )
    require_mention: bool = Field(
        default=False,
        description="Only fire when the bot is mentioned.",
    )
    require_attachment: bool = Field(
        default=False,
        description="Only fire for messages carrying a file.",
    )
    ignore_bots: bool = Field(
        default=True,
        description="Ignore messages from other bots. Leaving this off can create loops.",
    )

    model_config = ConfigDict(extra="ignore")


class DiscordReceiveOutput(BaseModel):
    account_id: Optional[str] = None
    message_id: Optional[str] = None
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    guild_id: Optional[str] = None
    guild_name: Optional[str] = None
    is_dm: Optional[bool] = None
    author_id: Optional[str] = None
    author_name: Optional[str] = None
    author_display_name: Optional[str] = None
    author_is_bot: Optional[bool] = None
    content: Optional[str] = None
    timestamp: Optional[str] = None
    attachments: List[DiscordAttachment] = Field(default_factory=list)
    has_attachments: Optional[bool] = None
    mentions_me: Optional[bool] = None
    reply_to_message_id: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class DiscordReceiveNode(TriggerNode):
    type = "discordReceive"
    display_name = "Discord Receive"
    subtitle = "Inbound Message"
    group = ("discord", "trigger")
    description = "Trigger a workflow when a Discord message arrives"
    component_kind = "trigger"
    handles = (
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (DiscordBotCredential,)
    task_queue = TaskQueue.TRIGGERS_EVENT
    mode = "event"
    # Legacy snake_case key for the event-waiter registry. The CloudEvents
    # type in _events.py is what the deployed listener actually routes on.
    event_type = "discord_message_received"

    Params = DiscordReceiveParams
    Output = DiscordReceiveOutput

    @classmethod
    async def prepare_deployment(
        cls,
        *,
        node_id: str,
        workflow_id: str,
        parameters: Dict[str, Any],
    ) -> None:
        """Start the Discord gateway of the trigger's bot for a deployment.

        Only the owner's Connect command started the gateway, so without this
        a deployed trigger would receive nothing after a backend restart.
        Run in the background: connecting must not hold up Start or the boot
        re-arm.
        """
        account_id = str((parameters or {}).get("account_id") or DEFAULT_ACCOUNT)
        task = asyncio.create_task(_start_gateway_for_deployment(account_id, node_id, workflow_id))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)

    def build_filter(self, params: BaseModel) -> Callable[[Dict[str, Any]], bool]:
        """Normally unreachable: the plugin pre-registers the builder with
        event_waiter, which short-circuits the auto-populate path. Kept so the
        contract holds if that registration is ever removed."""
        from ._filters import build_discord_filter

        return build_discord_filter(params.model_dump())

    async def execute(
        self, node_id: str, parameters: Dict[str, Any], context: NodeContext
    ) -> Dict[str, Any]:
        """Refuse to wait when no gateway is connected.

        Without this the Run button registers a waiter that can never resolve,
        which looks like a hung node rather than a missing connection.
        """
        import time

        from ._accounts import DEFAULT_ACCOUNT
        from ._gateway import known_gateways

        account_id = (parameters or {}).get("account_id") or DEFAULT_ACCOUNT
        gateway = known_gateways().get(account_id)
        if gateway is None or not gateway.is_running():
            return self._wrap_error(
                start_time=time.time(),
                error=(
                    "Discord bot is not connected. Connect it from the Credentials "
                    "modal before running this trigger."
                ),
            )
        return await super().execute(node_id, parameters, context)

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: DiscordReceiveParams) -> DiscordReceiveOutput:
        """Never called. Event triggers resolve through event_waiter; this
        exists so the class satisfies the one-operation contract."""
        raise NotImplementedError("discordReceive resolves through the event waiter")


__all__ = [
    "DiscordAttachment",
    "DiscordReceiveNode",
    "DiscordReceiveOutput",
    "DiscordReceiveParams",
    "MESSAGE_RECEIVED_TYPE",
]
