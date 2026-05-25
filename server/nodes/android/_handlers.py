"""WebSocket handlers for the Android plugin.

Five handlers split into two concerns:

- ADB / device-action commands (`get_android_devices`,
  `execute_android_action`) talk to the local `AndroidService`
  dispatcher to enumerate devices and run service actions.

- Relay-pairing commands (`android_relay_connect` / `_disconnect` /
  `_reconnect`) drive the WebSocket relay client lifecycle for
  remote devices.

Self-registers via ``services.ws_handler_registry.register_ws_handlers``
from ``__init__.py`` (Wave 11.H plugin pattern).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict

from starlette.websockets import WebSocket

from core.logging import get_logger
from services.plugin.ws import ws_response
from services.status_broadcaster import get_status_broadcaster

logger = get_logger(__name__)


async def handle_get_android_status(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get Android connection status (Wave 13.9 — moved from routers/websocket.py)."""
    broadcaster = get_status_broadcaster()
    return {"type": "android_status", "data": broadcaster.get_android_status()}


async def handle_get_android_devices(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Get list of connected Android devices."""
    from services.plugin.deps import get_android_service

    android_service = get_android_service()
    devices = await android_service.list_devices()
    return {"devices": devices, "timestamp": time.time()}


async def handle_execute_android_action(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Execute an Android service action."""
    from services.plugin.deps import get_android_service

    android_service = get_android_service()
    broadcaster = get_status_broadcaster()
    service_id, action = data["service_id"], data["action"]
    node_id = data.get("node_id", f"android_{service_id}_{action}")

    await broadcaster.update_node_status(node_id, "executing")
    result = await android_service.execute_service(
        node_id=node_id,
        service_id=service_id,
        action=action,
        parameters=data.get("parameters", {}),
        android_host=data.get("android_host", "localhost"),
        android_port=data.get("android_port", 8888),
    )

    status = "success" if result.get("success") else "error"
    await broadcaster.update_node_status(node_id, status, result.get("result") or {"error": result.get("error")})
    return result


@ws_response
async def handle_android_relay_connect(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Connect to the Android relay server.

    Establishes the WebSocket connection and broadcasts QR pairing data.
    Status updates are emitted from the relay client itself.
    """
    from ._relay import get_relay_client

    url = data.get("url", "")
    api_key = data.get("api_key")

    if not url:
        return {"success": False, "connected": False, "error": "Relay URL is required"}
    if not api_key:
        return {"success": False, "connected": False, "error": "API key is required"}

    logger.info(f"[WebSocket] Android relay connect: {url}")

    client, error = await get_relay_client(url, api_key)
    if client:
        logger.info(
            f"[WebSocket] Android relay connect success, qr_data present: " f"{bool(client.qr_data)}, session_token: {client.session_token}"
        )
        return {
            "success": True,
            "connected": True,
            "session_token": client.session_token,
            "qr_data": client.qr_data,
            "message": "Connected to relay server",
        }
    return {
        "success": False,
        "connected": False,
        "error": error or "Failed to connect to relay server",
    }


@ws_response
async def handle_android_relay_disconnect(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Disconnect from the Android relay server."""
    from ._relay import close_relay_client

    logger.info("[WebSocket] Android relay disconnect requested")
    await close_relay_client()
    return {"success": True, "connected": False, "message": "Disconnected from relay server"}


@ws_response
async def handle_android_relay_reconnect(data: Dict[str, Any], websocket: WebSocket) -> Dict[str, Any]:
    """Reconnect to the relay with a fresh session token + QR code."""
    from ._relay import close_relay_client, get_relay_client

    url = data.get("url", "")
    api_key = data.get("api_key")

    if not url:
        return {"success": False, "connected": False, "error": "Relay URL is required"}
    if not api_key:
        return {"success": False, "connected": False, "error": "API key is required"}

    logger.info("[WebSocket] Android relay reconnect: forcing new session")

    await close_relay_client()
    await asyncio.sleep(0.5)  # ensure clean disconnect

    client, error = await get_relay_client(url, api_key)
    if client:
        return {
            "success": True,
            "connected": True,
            "session_token": client.session_token,
            "qr_data": client.qr_data,
            "message": "Reconnected with new session token",
        }
    return {
        "success": False,
        "connected": False,
        "error": error or "Failed to reconnect to relay server",
    }


WS_HANDLERS = {
    "get_android_status": handle_get_android_status,
    "get_android_devices": handle_get_android_devices,
    "execute_android_action": handle_execute_android_action,
    "android_relay_connect": handle_android_relay_connect,
    "android_relay_disconnect": handle_android_relay_disconnect,
    "android_relay_reconnect": handle_android_relay_reconnect,
}
