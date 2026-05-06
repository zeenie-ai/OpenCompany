"""WhatsApp ``loadOptionsMethod`` loaders.

Wave 11.I, milestone M.1. Each function is registered with
``services.ws_handler_registry.register_option_loader`` from
``__init__.py`` so the central dispatcher in
``services/node_option_loaders/__init__.py`` picks them up without a
plugin-specific import.

Adapter shape: turn the existing WS-handler responses
(``handle_whatsapp_groups`` / ``handle_whatsapp_newsletters`` /
``handle_whatsapp_group_info``) into the unified ``[{value, label}]``
list the frontend ``useLoadOptionsQuery`` consumes. The legacy WS
message types stay live for back-compat.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ._service import (
    handle_whatsapp_group_info,
    handle_whatsapp_groups,
    handle_whatsapp_newsletters,
)


async def load_groups(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List WhatsApp groups for the ``recipient_type='group'`` selector."""
    response = await handle_whatsapp_groups()
    groups = response.get("groups", []) if isinstance(response, dict) else []
    return [
        {
            "value": g.get("group_jid") or g.get("id") or "",
            "label": g.get("name") or g.get("subject") or g.get("group_jid", ""),
        }
        for g in groups
    ]


async def load_channels(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List subscribed newsletter channels for the ``channel_jid`` selector."""
    response = await handle_whatsapp_newsletters()
    channels = response.get("newsletters", []) if isinstance(response, dict) else []
    return [
        {
            "value": c.get("channel_jid") or c.get("id") or "",
            "label": c.get("name") or c.get("channel_jid", ""),
        }
        for c in channels
    ]


async def load_group_members(params: Dict[str, Any]) -> List[Dict[str, Any]]:
    """List members of a specific WhatsApp group for the ``senderNumber``
    selector. Depends on ``params['group_id']``."""
    group_id = params.get("group_id") or ""
    if not group_id:
        return []
    response = await handle_whatsapp_group_info(group_id)
    participants = (
        response.get("participants", []) if isinstance(response, dict) else []
    )
    return [
        {
            "value": p.get("phone") or p.get("jid") or "",
            "label": p.get("name") or p.get("phone") or p.get("jid", ""),
        }
        for p in participants
    ]
