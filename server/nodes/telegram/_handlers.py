"""Telegram WebSocket handlers — moved from routers/websocket.py.

Owned by the plugin folder (same place the credential, service, and the
two workflow nodes live).  The router only registers the dict
:data:`WS_HANDLERS` so a future plugin-discovery hook can pick this up
without touching the core router.

Wire format unchanged — frontend message ``type`` strings continue to
match.  Auth lookups previously used raw ``auth.get_api_key`` calls;
those have been collapsed into a single :class:`TelegramCredential`
``resolve()`` (the credential class already declares the keys it owns
via ``id`` and ``extra_fields``).
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict

from fastapi import WebSocket

from services.plugin.ws import ws_response

from ._credentials import TelegramCredential
from ._service import get_telegram_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Send-method dispatch table -- replaces the long if/elif chain that was
# previously inlined in handle_telegram_send.
# ---------------------------------------------------------------------------


def _send_text(svc, data, parse_mode):
    text = data.get("text")
    if not text:
        return None, "text required for text message"
    return svc.send_message(
        chat_id=data["chat_id"],
        text=text,
        parse_mode=parse_mode,
    ), None


def _send_photo(svc, data, parse_mode):
    photo_url = data.get("media_url")
    if not photo_url:
        return None, "media_url required for photo"
    return svc.send_photo(
        chat_id=data["chat_id"],
        photo=photo_url,
        caption=data.get("caption"),
        parse_mode=parse_mode,
    ), None


def _send_document(svc, data, parse_mode):
    doc_url = data.get("media_url")
    if not doc_url:
        return None, "media_url required for document"
    return svc.send_document(
        chat_id=data["chat_id"],
        document=doc_url,
        caption=data.get("caption"),
        parse_mode=parse_mode,
    ), None


def _send_location(svc, data, parse_mode):
    lat, lon = data.get("latitude"), data.get("longitude")
    if lat is None or lon is None:
        return None, "latitude and longitude required"
    return svc.send_location(
        chat_id=data["chat_id"],
        latitude=float(lat),
        longitude=float(lon),
    ), None


def _send_contact(svc, data, parse_mode):
    phone, first_name = data.get("phone_number"), data.get("first_name")
    if not phone or not first_name:
        return None, "phone_number and first_name required"
    return svc.send_contact(
        chat_id=data["chat_id"],
        phone_number=phone,
        first_name=first_name,
        last_name=data.get("last_name"),
    ), None


_SEND_DISPATCH: Dict[str, Callable] = {
    "text": _send_text,
    "photo": _send_photo,
    "document": _send_document,
    "location": _send_location,
    "contact": _send_contact,
}


# ---------------------------------------------------------------------------
# Handlers — one per `telegram_*` WebSocket message type.
# ---------------------------------------------------------------------------


async def handle_telegram_connect(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Connect to Telegram. Service reads stored token from credentials
    when payload omits it — DB is the source of truth, the frontend saves
    via save_api_key first."""
    return await get_telegram_service().connect(data.get("token"))


async def handle_telegram_disconnect(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Stop polling.  Stored token stays in DB; explicit delete_api_key removes it."""
    return await get_telegram_service().disconnect()


async def handle_telegram_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    service = get_telegram_service()
    status = service.get_status()
    status["has_stored_token"] = await service.has_stored_token()
    return {"success": True, "status": status}


@ws_response
async def handle_telegram_send(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Direct send via WebSocket (not via workflow node).

    ``@ws_response`` collapses the catch-all ``try / except Exception``
    block at the bottom of the function -- exceptions become
    ``{success: False, error: str(e)}`` automatically. Early-return
    validation stays since those are deterministic input checks.
    """
    service = get_telegram_service()
    if not service.connected:
        return {"success": False, "error": "Telegram bot not connected"}
    if not data.get("chat_id"):
        return {"success": False, "error": "chat_id required"}

    message_type = data.get("message_type", "text")
    dispatch = _SEND_DISPATCH.get(message_type)
    if dispatch is None:
        return {"success": False, "error": f"Unsupported message type: {message_type}"}

    coro_or_none, err = dispatch(service, data, data.get("parse_mode"))
    if err:
        return {"success": False, "error": err}
    result = await coro_or_none
    return {"success": True, "result": result}


async def handle_telegram_reconnect(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Reconnect using stored bot token from encrypted credentials.

    The service's ``connect()`` already pulls the token from
    :class:`TelegramCredential` when omitted, so this handler is now a
    thin wrapper that surfaces a clearer "no stored token" error before
    calling through.  Owner restoration also happens inside ``connect()``.
    """
    try:
        secrets = await TelegramCredential.resolve()
    except PermissionError:
        return {"success": False, "error": "No stored bot token found. Enter token to connect."}

    if not secrets.get("api_key"):
        return {"success": False, "error": "No stored bot token found. Enter token to connect."}

    return await get_telegram_service().connect(secrets["api_key"])


@ws_response
async def handle_telegram_get_me(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    service = get_telegram_service()
    if not service.connected:
        return {"success": False, "error": "Telegram bot not connected"}
    return {"success": True, "result": await service.get_me()}


@ws_response
async def handle_telegram_get_chat(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    service = get_telegram_service()
    if not service.connected:
        return {"success": False, "error": "Telegram bot not connected"}
    if not data.get("chat_id"):
        return {"success": False, "error": "chat_id required"}
    return {"success": True, "result": await service.get_chat(data["chat_id"])}


# ---------------------------------------------------------------------------
# Registration — public surface consumed by routers/websocket.py.
# ---------------------------------------------------------------------------


WS_HANDLERS: Dict[str, Callable[[Dict[str, Any], WebSocket], Awaitable[Dict[str, Any]]]] = {
    "telegram_connect": handle_telegram_connect,
    "telegram_disconnect": handle_telegram_disconnect,
    "telegram_reconnect": handle_telegram_reconnect,
    "telegram_status": handle_telegram_status,
    "telegram_send": handle_telegram_send,
    "telegram_get_me": handle_telegram_get_me,
    "telegram_get_chat": handle_telegram_get_chat,
}
