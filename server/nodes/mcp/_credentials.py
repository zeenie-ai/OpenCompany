"""Custom connectors in the credentials catalogue: no card of their own, one
card per saved connector (``catalogue_entries``), built from the
``_mcp_connector`` template in config/credential_providers.json and what was
saved (``_store.py``). Each card is ``kind: "mcp"`` and carries ``mcp``: the
server's address without credentials, how it signs in, the server's name,
its tools with the owner's settings, and what a refresh found.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.logging import get_logger
from services.plugin.credential import Credential

from ._store import Connector, list_connectors, slug_of

logger = get_logger(__name__)

TEMPLATE = "_mcp_connector"
#: The catalogue's limit on a card's description.
_DESCRIPTION_MAX = 90


def card(template: Dict[str, Any], connector: Connector, *, distributed: bool) -> Dict[str, Any]:
    """A connector's catalogue entry."""
    meta = connector.meta
    settings = connector.settings
    pending = meta.get("pending")
    tools = [
        {
            "name": tool["name"],
            "title": tool.get("title"),
            "description": tool.get("description") or "",
            "read_only": bool(tool.get("read_only")),
            "usable": bool(tool.get("usable")),
            "reason": tool.get("reason"),
            "enabled": bool(settings.get(tool["name"], {}).get("enabled")),
            "ask": bool(settings.get(tool["name"], {}).get("ask", True)),
        }
        for tool in connector.tools
    ]
    return {
        **template,
        "id": connector.ref,
        "name": connector.name,
        "description": f"Custom connection · {connector.host}"[:_DESCRIPTION_MAX],
        "stored": True,
        "connected": True,
        "account_label": None,
        "sourceSupported": False,
        "sourceRequired": distributed,
        "mcp": {
            "slug": connector.slug,
            "address": meta.get("address"),
            "transport": meta.get("transport"),
            "sign_in": meta.get("sign_in") or {"kind": "none"},
            "server": meta.get("server") or {},
            "tools": tools,
            "pending": (
                {key: pending.get(key) for key in ("added", "removed", "changed", "instructions", "read_at")}
                if isinstance(pending, dict)
                else None
            ),
            "read_at": meta.get("read_at"),
        },
    }


class McpConnectorCredential(Credential):
    """The custom MCP connectors the owner added (``mcp:<slug>`` rows)."""

    id = "mcp"
    display_name = "Custom connector"
    auth = "custom"
    category = "Custom"
    docs_url = "https://modelcontextprotocol.io/docs/learn/server-concepts"

    @classmethod
    async def catalogue_entries(cls, *, principal: Optional[str] = None) -> List[Dict[str, Any]]:
        from services.credential_registry import get_credential_registry
        from services.plugin.deps import get_auth_service

        template = get_credential_registry().get_template(TEMPLATE)
        if template is None:
            logger.warning("The %s template is missing from credential_providers.json", TEMPLATE)
            return []
        distributed = bool(getattr(get_auth_service(), "distributed_credentials", False))
        scope = {"principal": principal} if principal is not None else {}
        return [card(template, connector, distributed=distributed) for connector in await list_connectors(**scope)]

    @classmethod
    async def is_configured(cls, auth_service: Any, parameters: Dict[str, Any]) -> bool:
        """The connector the node names (``mcp_connector``) is saved."""
        ref = str(parameters.get("mcp_connector") or "")
        return slug_of(ref) is not None and bool(await auth_service.has_valid_key(ref))


__all__ = ["McpConnectorCredential", "card"]
