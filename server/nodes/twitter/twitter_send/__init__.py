"""Twitter Send — Wave 11.D.8 inlined (tweet/reply/retweet/like/unlike/delete)."""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import TwitterCredential

from .._base import (
    call_with_retry,
    format_response,
    get_my_user_id,
    track_twitter_usage,
)


class TwitterSendParams(BaseModel):
    """8-field schema matching main-branch baseline. Snake_case throughout,
    no aliases. ``reply`` action uses ``tweet_id`` as the target (unified
    with other id-based actions) — baseline behaviour.
    """

    action: Literal[
        "tweet",
        "reply",
        "retweet",
        "quote",
        "like",
        "unlike",
        "delete",
    ] = Field(default="tweet", description="Action to perform")
    text: str = Field(
        default="",
        description="Tweet text (max 280 chars)",
        json_schema_extra={
            "rows": 4,
            "displayOptions": {"show": {"action": ["tweet", "reply", "quote"]}},
        },
    )
    tweet_id: str = Field(
        default="",
        description="Target tweet ID (for reply/retweet/quote/like/unlike/delete)",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "action": [
                        "reply",
                        "retweet",
                        "quote",
                        "like",
                        "unlike",
                        "delete",
                    ]
                }
            },
        },
    )
    include_media: bool = Field(
        default=False,
        description="Attach images or videos",
        json_schema_extra={
            "displayOptions": {"show": {"action": ["tweet", "reply", "quote"]}},
        },
    )
    media_urls: str = Field(
        default="",
        description="Comma-separated URLs; max 4 images or 1 video",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "action": ["tweet", "reply", "quote"],
                    "include_media": [True],
                }
            },
        },
    )
    include_poll: bool = Field(
        default=False,
        description="Create poll with tweet",
        json_schema_extra={
            "displayOptions": {"show": {"action": ["tweet"]}},
        },
    )
    poll_options: str = Field(
        default="",
        description="Comma-separated options (2-4 items, 25 chars each)",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "action": ["tweet"],
                    "include_poll": [True],
                }
            },
        },
    )
    poll_duration: int = Field(
        default=1440,
        ge=5,
        le=10080,
        description="Poll duration in minutes (5 min - 7 days)",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "action": ["tweet"],
                    "include_poll": [True],
                }
            },
        },
    )

    model_config = ConfigDict(extra="ignore")


class TwitterSendOutput(BaseModel):
    action: Optional[str] = None
    data: Optional[dict] = None

    model_config = ConfigDict(extra="allow")


async def _do_send(client, action: str, p: dict, node_id: str, ctx_raw: dict) -> TwitterSendOutput:
    if action == "tweet":
        text = p.get("text", "")
        if not text:
            raise RuntimeError("Tweet text is required")
        result = await asyncio.to_thread(client.posts.create, body={"text": text[:280]})
        await track_twitter_usage(node_id, "tweet", 1, ctx_raw)
        return TwitterSendOutput(action="tweet_sent", data=format_response(result))

    if action == "reply":
        text = p.get("text", "")
        reply_to = p.get("tweet_id")
        if not text or not reply_to:
            raise RuntimeError("Text and tweet_id are required for reply")
        result = await asyncio.to_thread(
            client.posts.create,
            body={"text": text[:280], "reply": {"in_reply_to_tweet_id": reply_to}},
        )
        await track_twitter_usage(node_id, "reply", 1, ctx_raw)
        return TwitterSendOutput(action="reply_sent", data=format_response(result))

    if action == "quote":
        text = p.get("text", "")
        quote_id = p.get("tweet_id")
        if not text or not quote_id:
            raise RuntimeError("Text and tweet_id are required for quote")
        result = await asyncio.to_thread(
            client.posts.create,
            body={"text": text[:280], "quote_tweet_id": quote_id},
        )
        await track_twitter_usage(node_id, "quote", 1, ctx_raw)
        return TwitterSendOutput(action="quoted", data=format_response(result))

    tweet_id = p.get("tweet_id")
    if not tweet_id:
        raise RuntimeError("tweet_id is required")

    if action == "retweet":
        user_id = await get_my_user_id(client)
        result = await asyncio.to_thread(
            client.users.repost_post,
            user_id,
            body={"tweet_id": tweet_id},
        )
        await track_twitter_usage(node_id, "retweet", 1, ctx_raw)
        return TwitterSendOutput(action="retweeted", data=format_response(result))

    if action == "like":
        user_id = await get_my_user_id(client)
        result = await asyncio.to_thread(
            client.users.like_post,
            user_id,
            body={"tweet_id": tweet_id},
        )
        await track_twitter_usage(node_id, "like", 1, ctx_raw)
        return TwitterSendOutput(action="liked", data=format_response(result))

    if action == "unlike":
        user_id = await get_my_user_id(client)
        result = await asyncio.to_thread(
            client.users.unlike_post,
            user_id,
            tweet_id=tweet_id,
        )
        await track_twitter_usage(node_id, "unlike", 1, ctx_raw)
        return TwitterSendOutput(action="unliked", data=format_response(result))

    if action == "delete":
        result = await asyncio.to_thread(client.posts.delete, tweet_id)
        await track_twitter_usage(node_id, "delete", 1, ctx_raw)
        return TwitterSendOutput(action="deleted", data=format_response(result))

    raise RuntimeError(f"Unknown action: {action}")


class TwitterSendNode(ActionNode):
    type = "twitterSend"
    display_name = "Twitter Send"
    subtitle = "Tweet / Reply"
    group = ("social", "tool")
    description = "Post tweets, reply, retweet, like, or delete tweets on Twitter/X"
    component_kind = "square"
    tool_name = "twitter_send"
    tool_description = "Post, reply, retweet, like, unlike, or delete tweets on Twitter/X. Actions: tweet, reply, retweet, like, unlike, delete. Specify text (280 char max), tweet_id, reply_to_id as needed."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    credentials = (TwitterCredential,)
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True

    Params = TwitterSendParams
    Output = TwitterSendOutput

    @Operation("send", cost={"service": "twitter", "action": "send", "count": 1})
    async def send(self, ctx: NodeContext, params: TwitterSendParams) -> TwitterSendOutput:
        p = params.model_dump()
        action = p.get("action", "tweet")
        return await call_with_retry(_do_send, action, p, ctx.node_id, ctx.raw)
