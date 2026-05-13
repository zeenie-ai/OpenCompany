"""Twitter Search — Wave 11.D.8 inlined."""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import TwitterCredential

from .._base import (
    call_with_retry, format_tweet, includes_lookups,
    sync_search_recent, track_twitter_usage,
)


class TwitterSearchParams(BaseModel):
    """7-field schema matching main-branch baseline. Snake_case throughout."""

    query: str = Field(
        default="",
        description=(
            "X / Twitter search query for /2/tweets/search/recent. "
            "MUST include at least one STANDALONE term: a keyword, "
            "quoted \"phrase\", #hashtag, @mention, from:user, to:user, "
            "or url:domain. "
            "Operators like lang:, -is:retweet, is:reply, has:media, "
            "has:links are CONJUNCTION-REQUIRED -- they only work when "
            "combined with a standalone term. Operator-only queries "
            "(e.g. '-is:retweet lang:en' or 'has:media') return HTTP 400. "
            "Valid: 'python -is:retweet', '\"machine learning\" lang:en', "
            "'from:elonmusk -is:retweet', '#ai has:media'."
        ),
    )
    max_results: int = Field(
        default=10, ge=10, le=100,
        description="Number of results (X API minimum 10, max 100)",
    )
    sort_order: Literal["recency", "relevancy"] = Field(
        default="recency",
        description="Sort by recent or relevant",
    )
    start_time: str = Field(
        default="",
        description="ISO 8601 timestamp (optional); e.g. 2024-01-01T00:00:00Z",
    )
    end_time: str = Field(
        default="",
        description="ISO 8601 timestamp (optional)",
    )
    include_metrics: bool = Field(
        default=True,
        description="Include likes / retweets / replies / quote counts",
    )
    include_author: bool = Field(
        default=True,
        description="Include author profile (username, name, verified)",
    )

    model_config = ConfigDict(extra="ignore")


class TwitterSearchOutput(BaseModel):
    tweets: Optional[list] = None
    count: Optional[int] = None
    query: Optional[str] = None

    model_config = ConfigDict(extra="allow")


async def _do_search(client, query: str, max_results: int, node_id: str, ctx_raw: dict) -> TwitterSearchOutput:
    search = await asyncio.to_thread(sync_search_recent, client, query, max_results)
    users_by_id, media_by_key, tweets_by_id = includes_lookups(search["includes"])
    tweets = [format_tweet(t, users_by_id, media_by_key, tweets_by_id) for t in search["tweets"]]
    if tweets:
        await track_twitter_usage(node_id, "search", len(tweets), ctx_raw)
    return TwitterSearchOutput(tweets=tweets, count=len(tweets), query=query)


class TwitterSearchNode(ActionNode):
    type = "twitterSearch"
    display_name = "Twitter Search"
    subtitle = "Search Tweets"
    group = ("social", "tool")
    description = "Search recent tweets on Twitter/X using the Search API"
    component_kind = "square"
    handles = (
        {"name": "input-main", "kind": "input", "position": "left",
         "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right",
         "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    credentials = (TwitterCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = TwitterSearchParams
    Output = TwitterSearchOutput

    @Operation("search", cost={"service": "twitter", "action": "search", "count": 1})
    async def search(self, ctx: NodeContext, params: TwitterSearchParams) -> TwitterSearchOutput:
        if not params.query:
            raise RuntimeError("Search query is required")
        max_results = max(10, min(params.max_results, 100))
        return await call_with_retry(_do_search, params.query, max_results, ctx.node_id, ctx.raw)
