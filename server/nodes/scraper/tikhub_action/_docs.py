"""TikHub's own documentation for an endpoint, read from its OpenAPI document.

The SDK is generated from that document but keeps only each route's one-line
summary. What a parameter means, which values it accepts, what the endpoint
returns and what it costs are only in the document's descriptions, and an
agent that cannot read them guesses: it sent a made-up ``feed_type`` and paid
for a search that returns users and places when it wanted reels.

The document is public (no key), about 3 MB, and is fetched once per process
on the first ``describe``. Nothing is vendored, so the documentation is always
the live API's. A failed fetch is not cached: ``describe`` answers with what
the SDK knows and the next call tries again.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx

from core.logging import get_logger

logger = get_logger(__name__)

SPEC_URL = "https://api.tikhub.io/openapi.json"
_SPEC_TIMEOUT = 30.0
# Every description is written twice, Chinese first, under these headings.
_ENGLISH_HEADING = "# [English]"
_PARAM_FIELDS = ("description", "default", "enum")

_DOCS: Optional[Dict[str, Dict[str, Any]]] = None
_DOCS_LOCK = asyncio.Lock()


def _digest(operation: Dict[str, Any]) -> Dict[str, Any]:
    """The English half of one operation's description and its query
    parameters' documented fields."""
    description = str(operation.get("description") or "")
    params: Dict[str, Dict[str, Any]] = {}
    for param in operation.get("parameters") or []:
        schema = param.get("schema") or {}
        params[str(param.get("name"))] = {key: schema[key] for key in _PARAM_FIELDS if key in schema}
    return {"description": description.split(_ENGLISH_HEADING, 1)[-1].strip(), "params": params}


async def _load() -> Dict[str, Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=_SPEC_TIMEOUT) as client:
        response = await client.get(SPEC_URL)
        response.raise_for_status()
    # TikHub publishes exactly one HTTP method per path.
    return {
        path: _digest(next(iter(item.values())))
        for path, item in (response.json().get("paths") or {}).items()
        if item
    }


async def endpoint_docs() -> Optional[Dict[str, Dict[str, Any]]]:
    """Documentation by route, or ``None`` when TikHub's document cannot be
    read right now."""
    global _DOCS
    if _DOCS is not None:
        return _DOCS
    async with _DOCS_LOCK:
        if _DOCS is None:
            try:
                _DOCS = await _load()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("tikhub documentation unavailable", error=str(exc))
                return None
            logger.info("tikhub documentation loaded", routes=len(_DOCS))
    return _DOCS


__all__ = ["SPEC_URL", "endpoint_docs"]
