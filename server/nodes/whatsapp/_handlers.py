"""WhatsApp WebSocket handlers — plugin-owned dispatch table.

Self-registered into the central WS dispatcher via
``register_ws_handlers(WS_HANDLERS)`` from this package's
``__init__.py``. ``routers/websocket.py`` knows nothing about
WhatsApp; the message-type strings are wired here so renames /
additions stay local to the plugin.

Each handler is a thin shim over the underlying RPC client in
:mod:`._service`. Logic lives in the service module; this module is
the WS surface.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import WebSocket

from ._service import (
    handle_whatsapp_chat_history as _wa_chat_history,
    handle_whatsapp_connected_phone as _wa_connected_phone,
    handle_whatsapp_group_info as _wa_group_info,
    handle_whatsapp_groups as _wa_groups,
    handle_whatsapp_newsletters as _wa_newsletters,
    handle_whatsapp_qr as _wa_qr,
    handle_whatsapp_restart as _wa_restart,
    handle_whatsapp_send as _wa_send,
    handle_whatsapp_start as _wa_start,
    handle_whatsapp_status as _wa_status,
    whatsapp_rpc_call as _wa_rpc_call,
)


async def handle_whatsapp_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return await _wa_status()


async def handle_whatsapp_connected_phone(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get the connected WhatsApp phone number."""
    return await _wa_connected_phone()


async def handle_whatsapp_qr(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return await _wa_qr()


async def handle_whatsapp_send(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Forward all send params to WhatsApp handler — supports all message types."""
    return await _wa_send(data)


async def handle_whatsapp_start(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return await _wa_start()


async def handle_whatsapp_restart(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return await _wa_restart()


async def handle_whatsapp_groups(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    return await _wa_groups()


async def handle_whatsapp_newsletters(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get list of subscribed newsletter channels."""
    return await _wa_newsletters()


async def handle_whatsapp_group_info(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get group participants with resolved phone numbers."""
    group_id = data.get("group_id", "")
    return await _wa_group_info(group_id)


async def handle_whatsapp_chat_history(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get chat history from WhatsApp history store."""
    return await _wa_chat_history(data)


# ---- Rate-limit RPC passthroughs ----


async def handle_whatsapp_rate_limit_get(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get rate-limit config and current stats."""
    return await _wa_rpc_call("rate_limit_get", {})


async def handle_whatsapp_rate_limit_set(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Update rate-limit configuration."""
    return await _wa_rpc_call("rate_limit_set", data.get("config", {}))


async def handle_whatsapp_rate_limit_stats(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get current rate-limit statistics."""
    return await _wa_rpc_call("rate_limit_stats", {})


async def handle_whatsapp_rate_limit_unpause(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Resume rate limiting after an automatic pause."""
    return await _wa_rpc_call("rate_limit_unpause", {})


# ---- Lightweight RPC passthroughs ----


async def handle_whatsapp_mark_read(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Mark messages as read. Schema: mark_read({message_ids, chat_jid, sender_jid?})."""
    message_ids = data.get("message_ids", [])
    chat_jid = data.get("chat_jid", "")
    if not message_ids or not chat_jid:
        return {"success": False, "error": "message_ids (array) and chat_jid are required"}
    params: Dict[str, Any] = {"message_ids": message_ids, "chat_jid": chat_jid}
    if sender_jid := data.get("sender_jid"):
        params["sender_jid"] = sender_jid
    return await _wa_rpc_call("mark_read", params)


async def handle_whatsapp_typing(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Send typing indicator. Schema: typing({jid, state: 'composing'|'paused', media?})."""
    jid = data.get("jid", "")
    if not jid:
        return {"success": False, "error": "jid is required"}
    params: Dict[str, Any] = {"jid": jid, "state": data.get("state", "composing")}
    if media := data.get("media"):
        params["media"] = media
    return await _wa_rpc_call("typing", params)


async def handle_whatsapp_presence(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Set online/offline presence. Schema: presence({status: 'available'|'unavailable'})."""
    return await _wa_rpc_call("presence", {"status": data.get("status", "available")})


async def handle_whatsapp_stop(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Graceful WhatsApp shutdown."""
    return await _wa_rpc_call("stop", {})


async def handle_whatsapp_diagnostics(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get WhatsApp diagnostics / debug info."""
    return await _wa_rpc_call("diagnostics", {})


# Dispatch table consumed by ``services.ws_handler_registry`` —
# ``__init__.py`` registers this dict on package import. Ordering
# preserved from the legacy ``MESSAGE_HANDLERS`` block in
# ``routers/websocket.py`` so the wire surface is byte-identical.
WS_HANDLERS = {
    "whatsapp_status": handle_whatsapp_status,
    "whatsapp_connected_phone": handle_whatsapp_connected_phone,
    "whatsapp_qr": handle_whatsapp_qr,
    "whatsapp_send": handle_whatsapp_send,
    "whatsapp_start": handle_whatsapp_start,
    "whatsapp_restart": handle_whatsapp_restart,
    "whatsapp_groups": handle_whatsapp_groups,
    "whatsapp_group_info": handle_whatsapp_group_info,
    "whatsapp_chat_history": handle_whatsapp_chat_history,
    "whatsapp_newsletters": handle_whatsapp_newsletters,
    "whatsapp_rate_limit_get": handle_whatsapp_rate_limit_get,
    "whatsapp_rate_limit_set": handle_whatsapp_rate_limit_set,
    "whatsapp_rate_limit_stats": handle_whatsapp_rate_limit_stats,
    "whatsapp_rate_limit_unpause": handle_whatsapp_rate_limit_unpause,
    "whatsapp_mark_read": handle_whatsapp_mark_read,
    "whatsapp_typing": handle_whatsapp_typing,
    "whatsapp_presence": handle_whatsapp_presence,
    "whatsapp_stop": handle_whatsapp_stop,
    "whatsapp_diagnostics": handle_whatsapp_diagnostics,
}
