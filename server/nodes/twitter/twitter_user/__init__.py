"""Twitter User — Wave 11.D.8 inlined (me / by_username / by_id / followers / following)."""

from __future__ import annotations

import asyncio
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from services.plugin import ActionNode, NodeContext, Operation, TaskQueue

from .._credentials import TwitterCredential

from .._base import call_with_retry, format_user, get_my_user_id, track_twitter_usage


class TwitterUserParams(BaseModel):
    """Baseline-aligned schema with conditional fields per operation.
    Snake_case throughout."""

    operation: Literal["me", "by_username", "by_id", "followers", "following"] = Field(
        default="me",
        description="User-lookup operation",
    )
    username: str = Field(
        default="",
        description="Twitter username (without @ prefix)",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["by_username"]}},
        },
    )
    user_id: str = Field(
        default="",
        description="Twitter user ID (numeric)",
        json_schema_extra={
            "displayOptions": {
                "show": {
                    "operation": [
                        "by_id",
                        "followers",
                        "following",
                    ]
                }
            },
        },
    )
    max_results: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Number of users to return (1-1000)",
        json_schema_extra={
            "displayOptions": {"show": {"operation": ["followers", "following"]}},
        },
    )

    model_config = ConfigDict(extra="ignore")


class TwitterUserOutput(BaseModel):
    operation: Optional[str] = None
    user: Optional[dict] = None
    users: Optional[list] = None
    count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


def _sync_followers(client, user_id, max_results):
    for page in client.users.get_followers(
        user_id,
        max_results=max_results,
        user_fields=["created_at"],
    ):
        return list(getattr(page, "data", []) or [])
    return []


def _sync_following(client, user_id, max_results):
    for page in client.users.get_following(
        user_id,
        max_results=max_results,
        user_fields=["created_at"],
    ):
        return list(getattr(page, "data", []) or [])
    return []


async def _do_lookup(client, op: str, p: dict, node_id: str, ctx_raw: dict) -> TwitterUserOutput:
    if op == "me":
        result = await asyncio.to_thread(
            client.users.get_me,
            user_fields=["created_at", "description"],
        )
        await track_twitter_usage(node_id, "me", 1, ctx_raw)
        return TwitterUserOutput(operation="me", user=format_user(result.data))

    if op == "by_username":
        username = p.get("username")
        if not username:
            raise RuntimeError("Username is required")
        result = await asyncio.to_thread(
            client.users.get_by_usernames,
            usernames=[username],
            user_fields=["description", "created_at"],
        )
        users = getattr(result, "data", []) or []
        if not users:
            raise RuntimeError(f"User @{username} not found")
        await track_twitter_usage(node_id, "by_username", 1, ctx_raw)
        return TwitterUserOutput(operation="by_username", user=format_user(users[0]))

    if op == "by_id":
        user_id = p.get("user_id")
        if not user_id:
            raise RuntimeError("User ID is required")
        result = await asyncio.to_thread(
            client.users.get_by_ids,
            ids=[user_id],
            user_fields=["description", "created_at"],
        )
        users = getattr(result, "data", []) or []
        if not users:
            raise RuntimeError(f"User ID {user_id} not found")
        await track_twitter_usage(node_id, "by_id", 1, ctx_raw)
        return TwitterUserOutput(operation="by_id", user=format_user(users[0]))

    if op in ("followers", "following"):
        user_id = p.get("user_id") or await get_my_user_id(client)
        max_results = max(1, min(int(p.get("max_results", 100)), 1000))
        fetch = _sync_followers if op == "followers" else _sync_following
        raw = await asyncio.to_thread(fetch, client, user_id, max_results)
        users = [format_user(u) for u in raw]
        if users:
            await track_twitter_usage(node_id, op, len(users), ctx_raw)
        return TwitterUserOutput(operation=op, users=users, count=len(users))

    raise RuntimeError(f"Unknown operation: {op}")


class TwitterUserNode(ActionNode):
    type = "twitterUser"
    display_name = "Twitter User"
    subtitle = "User Profiles"
    group = ("social", "tool")
    description = "Look up Twitter/X user profiles, followers, and following"
    component_kind = "square"
    tool_name = "twitter_user"
    tool_description = "Look up Twitter/X user profiles with description and created_at. Operations: me (authenticated user), by_username, by_id, followers (max_results 1-1000), following (max_results 1-1000)."
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    annotations = {"destructive": False, "readonly": True, "open_world": True}
    credentials = (TwitterCredential,)
    task_queue = TaskQueue.REST_API
    usable_as_tool = True

    Params = TwitterUserParams
    Output = TwitterUserOutput

    @Operation("lookup", cost={"service": "twitter", "action": "user_lookup", "count": 1})
    async def lookup(self, ctx: NodeContext, params: TwitterUserParams) -> TwitterUserOutput:
        p = params.model_dump()
        op = p.get("operation", "me")
        return await call_with_retry(_do_lookup, op, p, ctx.node_id, ctx.raw)
