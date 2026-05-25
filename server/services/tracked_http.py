"""
API Call Tracking via HTTPX Event Hooks

Uses HTTPX's built-in event hooks for transparent tracking.
No wrapper class needed - just configure the shared client.

Usage:
    from services.tracked_http import get_tracked_client, set_tracking_context

    # Set context for current request
    set_tracking_context(node_id="twitter-1", session_id="user-123")

    # Use tracked client - tracking happens automatically!
    client = get_tracked_client()
    response = await client.post("https://api.twitter.com/2/tweets", json={...})
"""

import re
import asyncio
import contextvars
from typing import Dict, Any, Optional, Tuple
import httpx

from services.pricing import get_pricing_service
from core.container import container
from core.logging import get_logger

logger = get_logger(__name__)

# ============================================================================
# Context Variables (thread-safe tracking metadata)
# ============================================================================

_tracking_ctx: contextvars.ContextVar[Dict[str, Any]] = contextvars.ContextVar("api_tracking", default={})


def set_tracking_context(node_id: str, session_id: str = "default", workflow_id: Optional[str] = None):
    """Set tracking context for current async task."""
    _tracking_ctx.set({"node_id": node_id, "session_id": session_id, "workflow_id": workflow_id})


def clear_tracking_context():
    """Clear tracking context."""
    _tracking_ctx.set({})


def get_tracking_context() -> Dict[str, Any]:
    """Get current tracking context."""
    return _tracking_ctx.get()


# ============================================================================
# URL Pattern Matching (loaded from pricing.json)
# ============================================================================

_url_patterns: Dict[str, Any] = {}
_patterns_loaded: bool = False


def _load_url_patterns():
    """Load URL patterns from pricing.json."""
    global _url_patterns, _patterns_loaded
    if _patterns_loaded:
        return

    try:
        pricing = get_pricing_service()
        _url_patterns = pricing.get_config().get("url_patterns", {})
        _patterns_loaded = True
        logger.debug(f"[APITracker] Loaded URL patterns for {len(_url_patterns)} services")
    except Exception as e:
        logger.error(f"[APITracker] Failed to load URL patterns: {e}")
        _url_patterns = {}


def reload_url_patterns():
    """Force reload of URL patterns (e.g., after pricing.json update)."""
    global _patterns_loaded
    _patterns_loaded = False
    _load_url_patterns()


def _match_url(url: str, method: str) -> Optional[Tuple[str, str, Optional[str]]]:
    """
    Match URL to (service, action, count_path) or None.

    Args:
        url: The request URL
        method: HTTP method (GET, POST, etc.)

    Returns:
        Tuple of (service_name, action, count_path) or None if no match
    """
    _load_url_patterns()

    for service, config in _url_patterns.items():
        base = config.get("base", "")
        if not base or not re.search(base, url):
            continue

        for pattern, action_info in config.get("actions", {}).items():
            if not re.search(pattern, url):
                continue

            # Simple string action (any method)
            if isinstance(action_info, str):
                return (service, action_info, None)

            # Dict with action, method, count_path
            expected_method = action_info.get("method", "*")
            if expected_method == "*" or expected_method.upper() == method.upper():
                return (service, action_info.get("action", "unknown"), action_info.get("count_path"))

    return None


def _extract_count(data: Any, path: Optional[str]) -> int:
    """
    Extract resource count from response data using dot-notation path.

    Args:
        data: Response JSON data
        path: Dot-notation path like "data.length" or "results.count"

    Returns:
        Resource count (defaults to 1)
    """
    if not path or not data:
        return 1

    try:
        for part in path.split("."):
            if part == "length" and isinstance(data, list):
                return len(data)
            elif isinstance(data, dict):
                data = data.get(part)
            else:
                return 1

        if isinstance(data, (int, float)):
            return int(data)
        elif isinstance(data, list):
            return len(data)
        return 1
    except Exception:
        return 1


# ============================================================================
# HTTPX Event Hook (the magic happens here)
# ============================================================================


async def _on_response(response: httpx.Response):
    """
    HTTPX response event hook - tracks API calls automatically.

    This is called for EVERY response when using the tracked client.
    """
    try:
        # Skip failed requests
        if response.status_code >= 400:
            return

        # Match URL to service/action
        url = str(response.request.url)
        method = response.request.method
        match = _match_url(url, method)

        if not match:
            return  # Not a tracked endpoint

        service, action, count_path = match

        # Extract resource count from response
        resource_count = 1
        if count_path:
            try:
                # Ensure body is read (required for event hooks)
                await response.aread()
                resource_count = _extract_count(response.json(), count_path)
            except Exception:
                pass

        # Get tracking context
        ctx = get_tracking_context()

        # Calculate cost and save (fire-and-forget)
        asyncio.create_task(_save_metric(service, action, resource_count, ctx))

    except Exception as e:
        logger.error(f"[APITracker] Error in response hook: {e}")


async def _save_metric(service: str, action: str, count: int, ctx: Dict[str, Any]):
    """Save metric to database (non-blocking)."""
    try:
        pricing = get_pricing_service()
        cost = pricing.calculate_api_cost(service, action, count)

        db = container.database()
        await db.save_api_usage_metric(
            {
                "session_id": ctx.get("session_id", "default"),
                "node_id": ctx.get("node_id", ""),
                "workflow_id": ctx.get("workflow_id"),
                "service": service,
                "operation": cost.get("operation", action),
                "endpoint": action,
                "resource_count": count,
                "cost": cost.get("total_cost", 0.0),
            }
        )

        logger.debug(f"[APITracker] Tracked {service}/{action}: " f"{count} resources, ${cost.get('total_cost', 0):.6f}")

    except Exception as e:
        logger.error(f"[APITracker] Failed to save metric: {e}")


# ============================================================================
# Tracked Client Factory
# ============================================================================


def create_tracked_client(**kwargs) -> httpx.AsyncClient:
    """
    Create an httpx.AsyncClient with tracking enabled.

    Usage:
        client = create_tracked_client()
        set_tracking_context(node_id="twitter-1", session_id="user-123")
        response = await client.post("https://api.twitter.com/2/tweets", json={...})
        # Automatically tracked!

    Args:
        **kwargs: Additional arguments passed to httpx.AsyncClient

    Returns:
        httpx.AsyncClient with tracking event hook
    """
    return httpx.AsyncClient(event_hooks={"response": [_on_response]}, **kwargs)


# Shared singleton client (for performance)
_shared_client: Optional[httpx.AsyncClient] = None


def get_tracked_client() -> httpx.AsyncClient:
    """
    Get shared tracked client instance.

    Returns a singleton httpx.AsyncClient with tracking enabled.
    Reusing the client avoids connection overhead.
    """
    global _shared_client
    if _shared_client is None:
        _shared_client = create_tracked_client()
    return _shared_client


async def close_tracked_client():
    """Close the shared tracked client (call on shutdown)."""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.aclose()
        _shared_client = None
