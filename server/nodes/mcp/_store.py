"""Where custom connectors are kept: two credential rows each, the way named
OpenAI-compatible endpoints are kept, through ``AuthService``.

- ``mcp:<slug>``: the sign-in, encrypted. Its ``model_params["_mcp"]`` holds
  the rest, which is not encrypted, so no secret ever goes there: the name,
  the server's address without credentials, the sign-in's kind and header
  name, the server's name, the tools as last accepted with the owner's
  settings for them, and a refresh waiting to be accepted.
- ``mcp:<slug>_proxy``: the server's URL, encrypted, since it may carry a key.

A slug is at most 20 characters, so ``<slug>__<tool>`` leaves a tool's own
name room within the 64 a model accepts.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from ._client import ConnectorError, Discovery, SignIn, snapshots

REF_PREFIX = "mcp:"
META_KEY = "_mcp"
SLUG_MAX = 20
_SLUG = re.compile(r"^[a-z0-9-]{1,20}$")


def ref_of(slug: str) -> str:
    return f"{REF_PREFIX}{slug}"


def url_key(ref: str) -> str:
    return f"{ref}_proxy"


def slug_of(ref: str) -> Optional[str]:
    """The slug of a connector reference, or None when ``ref`` is not one."""
    if not isinstance(ref, str) or not ref.startswith(REF_PREFIX):
        return None
    slug = ref[len(REF_PREFIX):]
    return slug if _SLUG.match(slug) else None


def new_slug(name: str, url: str) -> str:
    """A new connector's slug: from its name, else from its server's host."""
    from slugify import slugify

    return slugify(name or (urlsplit(url.strip()).hostname or ""), max_length=SLUG_MAX, separator="-")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_settings(tools: List[Dict[str, Any]], kept: Optional[Dict[str, Dict[str, bool]]] = None) -> Dict[str, Dict[str, bool]]:
    """Each usable tool's settings: on, and Ask first unless the server marks
    it read-only. A tool already set keeps what the owner chose."""
    kept = kept or {}
    return {
        tool["name"]: kept.get(tool["name"]) or {"enabled": True, "ask": not tool["read_only"]}
        for tool in tools
        if tool["usable"]
    }


def describe_new(slug: str, name: str, url: str, sign_in: SignIn, found: Discovery) -> Dict[str, Any]:
    """A new connector's kept description."""
    from services.llm.endpoints import redact_url

    tools = snapshots(slug, found.tools)
    return {
        "name": name,
        "host": (urlsplit(url).hostname or "").lower(),
        "address": redact_url(url),
        "transport": found.transport,
        "sign_in": sign_in.public(),
        "server": found.server,
        "instructions": found.instructions,
        "tools": tools,
        "settings": default_settings(tools),
        "pending": None,
        "read_at": now(),
    }


@dataclass
class Connector:
    """A saved connector: its reference and its kept description."""

    ref: str
    slug: str
    meta: Dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.meta.get("name") or self.slug)

    @property
    def host(self) -> str:
        return str(self.meta.get("host") or "")

    @property
    def tools(self) -> List[Dict[str, Any]]:
        return list(self.meta.get("tools") or [])

    @property
    def settings(self) -> Dict[str, Dict[str, bool]]:
        return dict(self.meta.get("settings") or {})


def _auth():
    from services.plugin.deps import get_auth_service

    return get_auth_service()


async def list_connectors(*, principal: Optional[str] = None) -> List[Connector]:
    """Every saved connector, in name order."""
    auth = _auth()
    scope = {"principal": principal} if principal is not None else {}
    found = []
    for provider in await auth.list_api_key_providers(**scope):
        slug = slug_of(provider)
        if slug is None:
            continue
        meta = (await auth.get_model_params(provider, **scope)).get(META_KEY)
        if isinstance(meta, dict):
            found.append(Connector(ref=provider, slug=slug, meta=meta))
    return sorted(found, key=lambda connector: connector.name.lower())


async def get_connector(ref: str, *, principal: Optional[str] = None) -> Connector:
    """A saved connector, or ``ConnectorError`` when there is none."""
    slug = slug_of(ref)
    if slug is None:
        raise ConnectorError("There is no such connector.")
    scope = {"principal": principal} if principal is not None else {}
    meta = (await _auth().get_model_params(ref, **scope)).get(META_KEY)
    if not isinstance(meta, dict):
        raise ConnectorError("That connector no longer exists.")
    return Connector(ref=ref, slug=slug, meta=meta)


async def exists(ref: str) -> bool:
    return bool(await _auth().get_api_key(ref))


async def read_access(connector: Connector) -> tuple[str, SignIn]:
    """The server's URL and the sign-in, as saved."""
    auth = _auth()
    url = await auth.get_api_key(url_key(connector.ref))
    raw = await auth.get_api_key(connector.ref)
    if not url or not raw:
        raise ConnectorError("That connector no longer exists.")
    try:
        stored = json.loads(raw)
    except ValueError:
        stored = {}
    return url, SignIn.load(stored if isinstance(stored, dict) else {})


async def save_new(ref: str, url: str, sign_in: SignIn, meta: Dict[str, Any]) -> None:
    """Save a new connector: its URL row, then its sign-in with its
    description. Nothing is left behind when the second write fails."""
    auth = _auth()
    if not await auth.store_api_key(provider=url_key(ref), api_key=url, models=[]):
        raise ConnectorError("Couldn't save the connector. Try again.")
    if not await auth.store_api_key(provider=ref, api_key=json.dumps(sign_in.dump()), models=[], model_params={META_KEY: meta}):
        await auth.remove_api_key(url_key(ref))
        raise ConnectorError("Couldn't save the connector. Try again.")


async def save_meta(connector: Connector, meta: Dict[str, Any]) -> None:
    """Keep a changed description; the sign-in stays as it is."""
    auth = _auth()
    raw = await auth.get_api_key(connector.ref)
    if not raw:
        raise ConnectorError("That connector no longer exists.")
    if not await auth.store_api_key(provider=connector.ref, api_key=raw, models=[], model_params={META_KEY: meta}):
        raise ConnectorError("Couldn't save the change. Try again.")


async def remove(ref: str, *, principal: Optional[str] = None) -> None:
    auth = _auth()
    scope = {"principal": principal} if principal is not None else {}
    await auth.remove_api_key(ref, **scope)
    await auth.remove_api_key(url_key(ref))


__all__ = [
    "Connector",
    "META_KEY",
    "REF_PREFIX",
    "default_settings",
    "describe_new",
    "exists",
    "get_connector",
    "list_connectors",
    "new_slug",
    "now",
    "read_access",
    "ref_of",
    "remove",
    "save_meta",
    "save_new",
    "slug_of",
    "url_key",
]
