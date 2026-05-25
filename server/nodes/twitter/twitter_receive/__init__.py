"""Twitter Receive — Wave 11.C migration (polling trigger)."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import NodeContext, Operation, TaskQueue, TriggerNode

from .._credentials import TwitterCredential


class TwitterReceiveParams(BaseModel):
    trigger_type: Literal["mentions", "search", "timeline"] = Field(
        default="mentions",
    )
    search_query: str = Field(default="")
    user_id: str = Field(default="")
    filter_retweets: bool = Field(default=True)
    filter_replies: bool = Field(default=False)
    poll_interval: int = Field(default=60, ge=15, le=3600)

    model_config = ConfigDict(extra="ignore")


class TwitterReceiveOutput(BaseModel):
    tweet_id: Optional[str] = None
    text: Optional[str] = None
    author: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class TwitterReceiveNode(TriggerNode):
    type = "twitterReceive"
    display_name = "Twitter Receive"
    subtitle = "Mentions / DMs"
    group = ("social", "trigger")
    description = "Trigger workflow on Twitter mentions, search results, or timeline updates (polling-based)"
    component_kind = "trigger"
    # Wave 11.I, milestone K: ``event_type`` ClassVar lets
    # ``event_waiter._auto_populate_from_plugins`` backfill
    # TRIGGER_REGISTRY without a hardcoded entry in event_waiter.
    event_type = "twitter_event_received"
    handles = ({"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},)
    credentials = (TwitterCredential,)
    task_queue = TaskQueue.TRIGGERS_POLL
    mode = "polling"
    default_poll_interval = 60

    Params = TwitterReceiveParams
    Output = TwitterReceiveOutput

    async def execute(
        self,
        node_id: str,
        parameters: Dict[str, Any],
        context: NodeContext,
    ) -> Dict[str, Any]:
        from services.handlers.triggers import handle_trigger_node

        return await handle_trigger_node(
            node_id=node_id,
            node_type=self.type,
            parameters=parameters,
            context=context.raw,
        )

    @Operation("wait")
    async def wait(self, ctx: NodeContext, params: TwitterReceiveParams) -> TwitterReceiveOutput:
        raise NotImplementedError("Polling trigger uses execute() override")
