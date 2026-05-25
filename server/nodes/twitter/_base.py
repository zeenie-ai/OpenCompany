"""Twitter/X shared helpers (Wave 11.D.8).

Used by the four twitter plugins (send / search / user / receive).
Wraps the XDK SDK with:
- Lazy client auth (refresh on 401/403).
- asyncio.to_thread for all sync XDK calls.
- Pricing-service usage tracking.
- Response / tweet / user formatters.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from core.logging import get_logger
from services.pricing import get_pricing_service

logger = get_logger(__name__)


def _is_auth_error(e: Exception) -> bool:
    s = str(e)
    return any(code in s for code in ("401", "403", "Unauthorized", "Forbidden"))


async def track_twitter_usage(
    node_id: str,
    action: str,
    resource_count: int,
    context: Dict[str, Any],
) -> Dict[str, float]:
    """Record a Twitter API call in ``api_usage_metrics``."""
    from services.plugin.deps import get_database

    pricing = get_pricing_service()
    cost_data = pricing.calculate_api_cost("twitter", action, resource_count)

    db = get_database()
    await db.save_api_usage_metric(
        {
            "session_id": context.get("session_id", "default"),
            "node_id": node_id,
            "workflow_id": context.get("workflow_id"),
            "service": "twitter",
            "operation": cost_data.get("operation", action),
            "endpoint": action,
            "resource_count": resource_count,
            "cost": cost_data.get("total_cost", 0.0),
        }
    )
    return cost_data


async def get_twitter_client():
    """Build an XDK Client from the stored OAuth2 access token."""
    from services.plugin.deps import get_auth_service
    from xdk import Client

    auth_service = get_auth_service()
    tokens = await auth_service.get_oauth_tokens("twitter", customer_id="owner")
    if not tokens or not tokens.get("access_token"):
        raise RuntimeError("Twitter not connected. Please authenticate via Credentials.")
    return Client(access_token=tokens["access_token"])


async def refresh_and_get_client():
    """Refresh the OAuth2 token and return a new client."""
    from nodes.twitter._oauth import TwitterOAuth
    from services.plugin.deps import get_auth_service
    from xdk import Client

    auth_service = get_auth_service()
    tokens = await auth_service.get_oauth_tokens("twitter", customer_id="owner")
    refresh_token = tokens.get("refresh_token", "") if tokens else ""
    if not refresh_token:
        raise RuntimeError("Twitter token expired. Please re-authenticate.")

    client_id = await auth_service.get_api_key("twitter_client_id") or ""
    client_secret = await auth_service.get_api_key("twitter_client_secret")

    oauth = TwitterOAuth(client_id=client_id, client_secret=client_secret, redirect_uri="")
    result = await oauth.refresh_access_token(refresh_token)
    if not result.get("success"):
        raise RuntimeError("Twitter token refresh failed. Please re-authenticate.")

    new_access = result["access_token"]
    new_refresh = result.get("refresh_token", refresh_token)

    await auth_service.store_oauth_tokens(
        provider="twitter",
        access_token=new_access,
        refresh_token=new_refresh,
        email=tokens.get("email", ""),
        name=tokens.get("name", ""),
        scopes=tokens.get("scopes", ""),
        customer_id="owner",
    )
    logger.info("Twitter token refreshed successfully")
    return Client(access_token=new_access)


async def call_with_retry(fn, *args, **kwargs):
    """Run ``fn`` with an existing client; on auth failure, refresh and retry once.

    ``fn`` is an async callable taking the client as its first argument:
    ``async def fn(client, ...) -> Any``.
    """
    client = await get_twitter_client()
    try:
        return await fn(client, *args, **kwargs)
    except Exception as e:
        if _is_auth_error(e):
            logger.info(f"Twitter auth error, refreshing token: {e}")
            client = await refresh_and_get_client()
            return await fn(client, *args, **kwargs)
        raise


async def get_my_user_id(client) -> str:
    resp = await asyncio.to_thread(client.users.get_me)
    return resp.data["id"]


def format_response(response) -> Dict[str, Any]:
    if hasattr(response, "data"):
        data = response.data
        if isinstance(data, dict):
            return data
        if hasattr(data, "__dict__"):
            return {k: v for k, v in data.__dict__.items() if not k.startswith("_")}
        return {"data": str(data)}
    return {"response": str(response)}


def format_user(user) -> Dict[str, Any]:
    def g(attr, default=None):
        if isinstance(user, dict):
            return user.get(attr, default)
        return getattr(user, attr, default)

    return {
        "id": g("id"),
        "username": g("username"),
        "name": g("name"),
        "profile_image_url": g("profile_image_url"),
        "verified": g("verified", False),
        "description": g("description"),
        "created_at": str(g("created_at", "")),
    }


def format_tweet(
    tweet,
    users_by_id: Optional[Dict] = None,
    media_by_key: Optional[Dict] = None,
    tweets_by_id: Optional[Dict] = None,
) -> Dict[str, Any]:
    """Format a tweet with enriched data from search includes."""
    users_by_id = users_by_id or {}
    media_by_key = media_by_key or {}
    tweets_by_id = tweets_by_id or {}

    def g(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

    tweet_id = g(tweet, "id")
    author_id = g(tweet, "author_id")
    text = g(tweet, "text", "")

    entities = g(tweet, "entities") or {}
    if isinstance(entities, dict):
        urls_list = entities.get("urls", [])
    elif hasattr(entities, "urls"):
        urls_list = entities.urls or []
    else:
        urls_list = []

    expanded_urls = []
    display_text = text
    for u in urls_list:
        if isinstance(u, dict):
            short = u.get("url", "")
            expanded = u.get("expanded_url", "")
            display = u.get("display_url", "")
        else:
            short = getattr(u, "url", "")
            expanded = getattr(u, "expanded_url", "")
            display = getattr(u, "display_url", "")
        if short and expanded:
            expanded_urls.append({"url": short, "expanded_url": expanded, "display_url": display})
            display_text = display_text.replace(short, expanded)

    note_tweet = g(tweet, "note_tweet")
    if note_tweet:
        note_text = note_tweet.get("text", "") if isinstance(note_tweet, dict) else getattr(note_tweet, "text", "")
        if note_text:
            text = note_text
            display_text = note_text

    author_info = users_by_id.get(str(author_id)) if author_id else None

    attachments = g(tweet, "attachments") or {}
    if isinstance(attachments, dict):
        media_keys = attachments.get("media_keys", [])
    elif hasattr(attachments, "media_keys"):
        media_keys = attachments.media_keys or []
    else:
        media_keys = []
    media_list = [media_by_key[k] for k in media_keys if k in media_by_key]

    ref_raw = g(tweet, "referenced_tweets") or []
    referenced = []
    for ref in ref_raw:
        if isinstance(ref, dict):
            ref_type = ref.get("type", "")
            ref_id = ref.get("id", "")
        else:
            ref_type = getattr(ref, "type", "")
            ref_id = getattr(ref, "id", "")
        ref_data = tweets_by_id.get(str(ref_id))
        referenced.append(
            {
                "type": ref_type,
                "id": ref_id,
                "text": ref_data.get("text", "") if ref_data else None,
                "author_id": ref_data.get("author_id") if ref_data else None,
            }
        )

    metrics = g(tweet, "public_metrics") or {}
    if not isinstance(metrics, dict) and hasattr(metrics, "model_dump"):
        metrics = metrics.model_dump()
    elif not isinstance(metrics, dict):
        metrics = {}

    result = {
        "id": tweet_id,
        "text": text,
        "display_text": display_text,
        "author_id": author_id,
        "created_at": str(g(tweet, "created_at", "")),
        "lang": g(tweet, "lang"),
        "source": g(tweet, "source"),
        "conversation_id": g(tweet, "conversation_id"),
        "in_reply_to_user_id": g(tweet, "in_reply_to_user_id"),
        "possibly_sensitive": g(tweet, "possibly_sensitive", False),
        "public_metrics": metrics,
    }
    if author_info:
        result["author"] = author_info
    if expanded_urls:
        result["urls"] = expanded_urls
    if media_list:
        result["media"] = media_list
    if referenced:
        result["referenced_tweets"] = referenced
    return result


def _includes_to_dict(raw) -> Dict[str, Any]:
    if not raw:
        return {}
    if hasattr(raw, "model_dump"):
        try:
            return raw.model_dump()
        except Exception:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def sync_search_recent(client, query: str, max_results: int) -> Dict[str, Any]:
    """Run ``posts.search_recent`` synchronously; returns {tweets, includes}."""
    for page in client.posts.search_recent(
        query=query,
        max_results=max_results,
        tweet_fields=[
            "author_id",
            "created_at",
            "entities",
            "public_metrics",
            "possibly_sensitive",
            "lang",
            "source",
            "conversation_id",
            "in_reply_to_user_id",
            "referenced_tweets",
            "note_tweet",
        ],
        expansions=[
            "author_id",
            "attachments.media_keys",
            "referenced_tweets.id",
            "referenced_tweets.id.author_id",
        ],
        media_fields=["url", "preview_image_url", "type", "alt_text", "variants"],
        user_fields=["username", "name", "profile_image_url"],
    ):
        return {
            "tweets": getattr(page, "data", []) or [],
            "includes": _includes_to_dict(getattr(page, "includes", None)),
        }
    return {"tweets": [], "includes": {}}


def includes_lookups(includes: Dict[str, Any]):
    """Build {users_by_id, media_by_key, tweets_by_id} from a search-includes dict."""

    def _as_dict(obj):
        if isinstance(obj, dict):
            return obj
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
        return {}

    users_by_id = {}
    for u in includes.get("users") or []:
        uid = u.get("id") if isinstance(u, dict) else getattr(u, "id", None)
        if uid:
            users_by_id[str(uid)] = _as_dict(u)

    media_by_key = {}
    for m in includes.get("media") or []:
        mk = m.get("media_key") if isinstance(m, dict) else getattr(m, "media_key", None)
        if mk:
            media_by_key[mk] = _as_dict(m)

    tweets_by_id = {}
    for t in includes.get("tweets") or []:
        tid = t.get("id") if isinstance(t, dict) else getattr(t, "id", None)
        if tid:
            tweets_by_id[str(tid)] = _as_dict(t)

    return users_by_id, media_by_key, tweets_by_id
