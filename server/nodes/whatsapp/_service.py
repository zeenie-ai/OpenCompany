"""
WhatsApp Service - JSON-RPC 2.0 integration with Go whatsmeow service.

This module provides WebSocket handlers for WhatsApp operations.
All communication goes through the RPCClient to the Go service.
"""

import asyncio
import base64
import io
import json
import logging
import os
import time
from typing import Any, Optional

import qrcode
import websockets
from websockets.exceptions import ConnectionClosed
from fastapi import HTTPException


def qr_code_to_base64(code: str) -> str:
    """Convert QR code string to base64 PNG image."""
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(code)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")


logger = logging.getLogger(__name__)

WHATSAPP_RPC_URL = os.getenv("WHATSAPP_RPC_URL", "ws://localhost:9400/ws/rpc")


def extract_phone_from_jid(jid: str | None) -> str | None:
    """Extract phone number from WhatsApp JID.

    Args:
        jid: WhatsApp JID like '1234567890@s.whatsapp.net' or '1234567890:0@s.whatsapp.net'

    Returns:
        Phone number string or None if invalid
    """
    if not jid:
        return None
    # Remove the @s.whatsapp.net or @c.us suffix
    phone_part = jid.split('@')[0]
    # Handle device ID suffix like '1234567890:0'
    phone = phone_part.split(':')[0]
    # Return only if it looks like a phone number (digits only)
    if phone.isdigit():
        return phone
    return None


# Inline RPC Client with async event handling
class RPCClient:
    def __init__(self, url: str):
        self.url, self.ws, self.req_id = url, None, 0
        self.pending: dict[int, asyncio.Future] = {}
        self._connected, self._task = False, None
        self._event_handler = None

    @property
    def connected(self):
        """Check if actually connected - verify WebSocket is open."""
        if not self._connected or not self.ws:
            return False
        # websockets 15.x uses state instead of closed (state.value: 0=CONNECTING, 1=OPEN, 2=CLOSING, 3=CLOSED)
        try:
            return self.ws.state.value == 1
        except Exception:
            return False

    def set_event_handler(self, handler):
        """Set callback for handling async events from Go service."""
        self._event_handler = handler

    async def connect(self):
        # 5 second timeout for initial connection (fail fast if Go service not running).
        # The WebSocket handshake to the Go service can take 2-3s on Windows,
        # especially on cold start when Defender is scanning the binary.
        logger.info(f"[WhatsApp RPC] Connecting to {self.url}...")
        self.ws = await asyncio.wait_for(
            websockets.connect(self.url, ping_interval=30, max_size=100*1024*1024),
            timeout=5.0
        )
        self._connected = True
        logger.info("[WhatsApp RPC] WebSocket connected, starting receive loop")
        self._task = asyncio.create_task(self._recv())

    async def close(self):
        self._connected = False
        if self._task: self._task.cancel()
        if self.ws: await self.ws.close()

    async def _recv(self):
        try:
            logger.info("[WhatsApp RPC] Receive loop started")
            async for msg in self.ws:
                data = json.loads(msg)
                logger.debug(f"[WhatsApp RPC] Received: {data.get('method', data.get('id', 'unknown'))}")
                if data.get("id") in self.pending:
                    self.pending[data["id"]].set_result(data)
                elif "method" in data and "id" not in data:
                    await self._handle_event(data)
        except ConnectionClosed as e:
            logger.warning(f"[WhatsApp RPC] Connection closed: {e}")
            self._connected = False
        except Exception as e:
            logger.error(f"[WhatsApp RPC] Receive loop error: {e}")
            self._connected = False

    async def _handle_event(self, data: dict):
        """Handle async events from Go service and broadcast to frontend.

        Events from schema.json:
        - event.connected: {status: "connected", device_id: string}
        - event.disconnected: {status: "disconnected", reason: string}
        - event.connection_failure: {error: string, reason: string}
        - event.logged_out: {on_connect: boolean, reason: string}
        - event.temporary_ban: {code: string, reason: string}
        - event.qr_code: {code: string, filename: string}
        - event.message_sent: {message_id, to, type, timestamp}
        - event.message_received: {message_id, sender, chat_id, ...}
        """
        method = data.get("method", "")
        params = data.get("params", {})
        logger.debug(f"[WhatsApp RPC] Event: {method}")

        try:
            # Wave 12 B2: all whatsapp wire emission routes through the
            # plugin's _events.py wrappers — single source of truth for
            # shape. No more direct broadcaster.* reaches into framework.
            from . import (
                broadcast_whatsapp_history_synced,
                broadcast_whatsapp_message,
                broadcast_whatsapp_newsletter,
                broadcast_whatsapp_status,
            )

            if method == "event.status":
                # Initial status sent on WebSocket connection
                await broadcast_whatsapp_status(
                    connected=params.get("connected", False),
                    has_session=params.get("has_session", False),
                    running=params.get("running", False),
                    pairing=params.get("pairing", False),
                    device_id=params.get("device_id"),
                    qr=None,
                )

            elif method == "event.connected":
                # Connected successfully with device_id
                await broadcast_whatsapp_status(
                    connected=True,
                    has_session=True,
                    running=True,
                    pairing=False,
                    device_id=params.get("device_id"),
                    qr=None,
                )

            elif method == "event.disconnected":
                # Disconnected - service still running
                await broadcast_whatsapp_status(
                    connected=False,
                    has_session=False,
                    running=True,
                    pairing=False,
                    device_id=None,
                    qr=None,
                )

            elif method == "event.connection_failure":
                # Connection failed
                logger.error(f"[WhatsApp] Connection failure: {params.get('error')} - {params.get('reason')}")
                await broadcast_whatsapp_status(
                    connected=False,
                    has_session=False,
                    running=True,
                    pairing=False,
                    device_id=None,
                    qr=None,
                )

            elif method == "event.logged_out":
                # Logged out - session cleared
                logger.warning(f"[WhatsApp] Logged out: {params.get('reason')}")
                await broadcast_whatsapp_status(
                    connected=False,
                    has_session=False,
                    running=True,
                    pairing=False,
                    device_id=None,
                    qr=None,
                )

            elif method == "event.temporary_ban":
                # Temporary ban
                logger.error(f"[WhatsApp] Temporary ban: code={params.get('code')} reason={params.get('reason')}")
                await broadcast_whatsapp_status(
                    connected=False,
                    has_session=False,
                    running=True,
                    pairing=False,
                    device_id=None,
                    qr=None,
                )

            elif method == "event.qr_code":
                # New QR code available for pairing
                code = params.get("code")
                qr_image = qr_code_to_base64(code) if code else None
                await broadcast_whatsapp_status(
                    connected=False,
                    has_session=False,
                    running=True,
                    pairing=True,
                    device_id=None,
                    qr=qr_image,
                )

            elif method == "event.message_sent":
                await broadcast_whatsapp_message("sent", params)

            elif method == "event.message_received":
                # Includes newsletter messages with newsletter_meta field.
                await broadcast_whatsapp_message("received", params)

            elif method == "event.newsletter_join":
                await broadcast_whatsapp_newsletter("joined", params)

            elif method == "event.newsletter_leave":
                await broadcast_whatsapp_newsletter("left", params)

            elif method == "event.newsletter_mute_change":
                await broadcast_whatsapp_newsletter("muted", params)

            elif method == "event.newsletter_live_update":
                await broadcast_whatsapp_newsletter("live_updated", params)

            elif method == "event.history_sync_complete":
                await broadcast_whatsapp_history_synced(params)

            # Forward to custom handler if set
            if self._event_handler:
                await self._event_handler(method, params)

        except Exception as e:
            logger.error(f"[WhatsApp RPC] Event handler error: {e}")

    async def call(self, method: str, params: Any = None, timeout: float = 30) -> Any:
        if not self.connected:
            raise Exception("Not connected to WhatsApp service")
        self.req_id += 1
        req_id = self.req_id  # Capture request ID before any await
        req = {"jsonrpc": "2.0", "id": req_id, "method": method}
        if params:
            req["params"] = params

        # Get current event loop for future
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.get_event_loop()
        future = loop.create_future()
        self.pending[req_id] = future

        try:
            await self.ws.send(json.dumps(req))
            resp = await asyncio.wait_for(future, timeout)
            if resp.get("error"):
                raise Exception(resp["error"].get("message", "RPC Error"))
            return resp.get("result")
        except asyncio.TimeoutError:
            raise Exception(f"RPC call '{method}' timed out after {timeout}s")
        except ConnectionClosed as e:
            logger.error(f"[WhatsApp RPC] Connection closed during {method}: {e}")
            self._connected = False
            raise Exception(f"Connection lost during {method}")
        finally:
            self.pending.pop(req_id, None)

_client: Optional[RPCClient] = None
_lock = asyncio.Lock()
_send_lock = asyncio.Lock()  # Serialize sends - Go service processes sequentially


async def reset_client():
    """Force reset the RPC client connection."""
    global _client
    async with _lock:
        if _client:
            try:
                await _client.close()
            except Exception:
                pass
            _client = None


async def get_client(force_reconnect: bool = False) -> RPCClient:
    """Get or create RPC client. Use force_reconnect=True to ensure fresh connection."""
    global _client
    # Lazy-spawn the edgymeow binary on first use so its session DB lives
    # under <data_dir>/whatsapp/ instead of the pnpm package directory.
    # Idempotent: no-op if already running or disabled via settings.
    from nodes.whatsapp import get_whatsapp_runtime
    try:
        # `start()` is idempotent (BaseSupervisor takes a lock and no-ops
        # if already running) so calling it from every get_client() is safe.
        await get_whatsapp_runtime().start()
    except Exception as e:
        logger.warning(f"[WhatsApp RPC] Runtime start failed (will try connecting anyway): {e}")

    async with _lock:
        # Force reconnect if requested or if client is stale
        if force_reconnect and _client:
            logger.info("[WhatsApp RPC] Force reconnecting...")
            try:
                await _client.close()
            except Exception:
                pass
            _client = None

        if not _client or not _client.connected:
            logger.info(f"[WhatsApp RPC] Creating new connection to {WHATSAPP_RPC_URL}")
            _client = RPCClient(WHATSAPP_RPC_URL)
            try:
                await _client.connect()
                logger.info("[WhatsApp RPC] Connected successfully")
            except asyncio.TimeoutError:
                _client = None
                logger.error(f"WhatsApp RPC timeout - Go service not responding at {WHATSAPP_RPC_URL}")
                raise Exception("WhatsApp service timeout - is Go service running?")
            except (ConnectionRefusedError, OSError) as e:
                _client = None
                logger.error(f"WhatsApp RPC connection refused: {e}")
                raise Exception("WhatsApp service not running - start Go whatsmeow service on port 9400")
            except Exception as e:
                _client = None
                logger.error(f"WhatsApp RPC error: {e}")
                raise Exception(f"WhatsApp connection failed: {e}")
        return _client


# ============================================================================
# WebSocket Handlers - used by websocket.py
# ============================================================================

async def handle_whatsapp_status() -> dict:
    """Get WhatsApp connection status via direct RPC and broadcast to all clients."""
    try:
        client = await get_client()
        status_data = await client.call("status")

        # Broadcast status via the plugin's canonical wrapper (Wave 12 B2).
        from . import broadcast_whatsapp_status

        await broadcast_whatsapp_status(
            connected=status_data.get("connected", False),
            has_session=status_data.get("has_session", False),
            running=status_data.get("running", False),
            pairing=status_data.get("pairing", False),
            device_id=status_data.get("device_id"),
            qr=None,  # QR code comes from event.qr_code events
        )

        device_id = status_data.get("device_id")
        connected_phone = extract_phone_from_jid(device_id)

        return {
            "success": True,
            "data": status_data,
            "connected": status_data.get("connected", False),
            "device_id": device_id,
            "connected_phone": connected_phone,
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"WhatsApp status check failed: {e}")
        # Return error response immediately - don't broadcast here to avoid race conditions
        # The client will update its local state based on the error response
        return {
            "success": False,
            "error": str(e),
            "connected": False,
            "running": False,
            "timestamp": time.time()
        }


async def handle_whatsapp_connected_phone() -> dict:
    """Get the connected WhatsApp phone number.

    Returns the phone number of the currently connected WhatsApp account,
    extracted from the device JID.
    """
    try:
        client = await get_client()
        status_data = await client.call("status")

        if not status_data.get("connected"):
            return {
                "success": False,
                "error": "WhatsApp not connected",
                "connected_phone": None,
                "timestamp": time.time()
            }

        device_id = status_data.get("device_id")
        connected_phone = extract_phone_from_jid(device_id)

        return {
            "success": True,
            "connected_phone": connected_phone,
            "device_id": device_id,
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"WhatsApp connected phone check failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "connected_phone": None,
            "timestamp": time.time()
        }


async def handle_whatsapp_qr() -> dict:
    """Get WhatsApp QR code for authentication via direct RPC."""
    try:
        client = await get_client()
        status = await client.call("status")

        if status.get("connected") and status.get("has_session"):
            return {
                "success": True,
                "connected": True,
                "message": "Already connected with active session",
                "timestamp": time.time()
            }

        try:
            result = await client.call("qr")
            code = result.get("code")
            if code:
                qr_image = qr_code_to_base64(code)
                return {
                    "success": True,
                    "connected": False,
                    "qr": qr_image,
                    "message": "QR code available",
                    "timestamp": time.time()
                }
            return {
                "success": True,
                "connected": False,
                "qr": None,
                "message": "No QR code available",
                "timestamp": time.time()
            }
        except Exception as qr_err:
            return {
                "success": True,
                "connected": False,
                "qr": None,
                "message": str(qr_err),
                "timestamp": time.time()
            }
    except Exception as e:
        logger.error(f"WhatsApp QR fetch failed: {e}")
        return {"success": False, "connected": False, "error": str(e)}


async def handle_whatsapp_send(params: dict) -> dict:
    """Send a WhatsApp message via direct RPC - supports all message types.

    Uses _send_lock to serialize sends - Go service processes sequentially.

    Params from frontend node (snake_case):
    - recipient_type: 'self', 'phone', 'group', or 'channel'
    - phone: recipient phone number (if recipient_type='phone')
    - group_id: group JID (if recipient_type='group')
    - channel_jid: newsletter JID (if recipient_type='channel')
    - message_type: text, image, video, audio, document, sticker, location, contact
    - message: text content (for text type)
    - media_source: base64, file, url (for media types)
    - media_data/file_path/media_url: media content based on source
    - mime_type, caption, filename: media options
    - latitude, longitude, location_name, address: location data
    - contact_name, vcard: contact data
    - is_reply, reply_message_id, reply_sender, reply_content: reply context
    """
    async with _send_lock:
        try:
            # Build RPC params matching schema.json
            rpc_params: dict[str, Any] = {}

            # Recipient (snake_case)
            recipient_type = params.get("recipient_type", "self")
            if recipient_type == "channel":
                # Newsletter/channel send - uses newsletter_send RPC with group_id param
                channel_jid = params.get("channel_jid")
                if not channel_jid:
                    return {"success": False, "error": "channel_jid is required"}
                # Validate channel-supported message types
                msg_type = params.get("message_type", "text")
                channel_types = {"text", "image", "video", "audio", "document"}
                if msg_type not in channel_types:
                    return {"success": False, "error": f"Channels only support: {', '.join(sorted(channel_types))}. Got: {msg_type}"}
                rpc_params["group_id"] = channel_jid  # newsletter_send uses group_id for JID
            elif recipient_type == "group":
                group_id = params.get("group_id")
                if not group_id:
                    return {"success": False, "error": "group_id is required"}
                rpc_params["group_id"] = group_id
            elif recipient_type == "self":
                # Send to connected phone (self)
                client = await get_client()
                status = await client.call("status")
                device_id = status.get("device_id")
                phone = extract_phone_from_jid(device_id)
                if not phone:
                    return {"success": False, "error": "WhatsApp not connected - cannot send to self"}
                rpc_params["phone"] = phone
            else:
                phone = params.get("phone")
                if not phone:
                    return {"success": False, "error": "phone is required"}
                rpc_params["phone"] = phone

            # Message type (snake_case)
            msg_type = params.get("message_type", "text")
            rpc_params["type"] = msg_type

            # Content based on type
            if msg_type == "text":
                message = params.get("message")
                if not message:
                    return {"success": False, "error": "message is required for text type"}
                rpc_params["message"] = message

            elif msg_type in ["image", "video", "audio", "document", "sticker"]:
                media_source = params.get("media_source", "base64")
                media_data = None
                mime_type = params.get("mime_type")
                filename = params.get("filename")

                if media_source == "base64":
                    media_data = params.get("media_data")
                elif media_source == "file":
                    file_param = params.get("file_path")
                    if isinstance(file_param, dict) and file_param.get("type") == "upload":
                        media_data = file_param.get("data")
                        mime_type = mime_type or file_param.get("mimeType")
                        filename = filename or file_param.get("filename")
                    elif file_param:
                        import base64 as b64
                        try:
                            with open(file_param, "rb") as f:
                                media_data = b64.b64encode(f.read()).decode("utf-8")
                        except Exception as e:
                            return {"success": False, "error": f"Failed to read file: {e}"}
                elif media_source == "url":
                    media_url = params.get("media_url")
                    if media_url:
                        import httpx
                        import base64 as b64
                        try:
                            async with httpx.AsyncClient() as http:
                                resp = await http.get(media_url, timeout=30)
                                media_data = b64.b64encode(resp.content).decode("utf-8")
                        except Exception as e:
                            return {"success": False, "error": f"Failed to download media: {e}"}

                if not media_data:
                    return {"success": False, "error": f"media data is required for {msg_type} type"}

                rpc_params["media_data"] = {
                    "data": media_data,
                    "mime_type": mime_type or _guess_mime_type(msg_type)
                }
                if params.get("caption"):
                    rpc_params["media_data"]["caption"] = params["caption"]
                final_filename = filename or params.get("filename")
                if final_filename:
                    rpc_params["media_data"]["filename"] = final_filename

            elif msg_type == "location":
                lat = params.get("latitude")
                lng = params.get("longitude")
                if lat is None or lng is None:
                    return {"success": False, "error": "latitude and longitude are required"}
                rpc_params["location"] = {"latitude": float(lat), "longitude": float(lng)}
                if params.get("location_name"):
                    rpc_params["location"]["name"] = params["location_name"]
                if params.get("address"):
                    rpc_params["location"]["address"] = params["address"]

            elif msg_type == "contact":
                contact_name = params.get("contact_name")
                vcard = params.get("vcard")
                if not contact_name or not vcard:
                    return {"success": False, "error": "contact_name and vcard are required"}
                rpc_params["contact"] = {"display_name": contact_name, "vcard": vcard}

            # Reply context (snake_case)
            if params.get("is_reply"):
                reply_id = params.get("reply_message_id")
                reply_sender = params.get("reply_sender")
                if reply_id and reply_sender:
                    rpc_params["reply"] = {
                        "message_id": reply_id,
                        "sender": reply_sender,
                        "content": params.get("reply_content", "")
                    }

            if params.get("metadata"):
                rpc_params["metadata"] = params["metadata"]

            client = await get_client()
            # Use newsletter_send for channel recipients, regular send for others
            rpc_method = "newsletter_send" if recipient_type == "channel" else "send"
            result = await client.call(rpc_method, rpc_params)
            return {
                "success": True,
                "message_id": result.get("message_id"),
                "message_type": msg_type,
                "timestamp": time.time()
            }
        except Exception as e:
            logger.error(f"WhatsApp send failed: {e}")
            return {"success": False, "error": str(e)}


def _guess_mime_type(msg_type: str) -> str:
    """Guess default MIME type based on message type."""
    defaults = {
        "image": "image/jpeg",
        "video": "video/mp4",
        "audio": "audio/ogg",
        "document": "application/octet-stream",
        "sticker": "image/webp"
    }
    return defaults.get(msg_type, "application/octet-stream")


async def handle_whatsapp_start() -> dict:
    """Start WhatsApp connection via direct RPC and broadcast running state."""
    try:
        client = await get_client()
        result = await client.call("start")

        # Broadcast that service is now running (waiting for QR or connection)
        from . import broadcast_whatsapp_status

        await broadcast_whatsapp_status(
            connected=False,
            has_session=False,
            running=True,
            pairing=False,  # Will be set to True by event.qr_code event
            device_id=None,
            qr=None,
        )

        return {
            "success": True,
            "message": "WhatsApp connection started",
            "data": result,
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"WhatsApp start failed: {e}")
        return {"success": False, "error": str(e)}


async def handle_whatsapp_restart() -> dict:
    """Restart WhatsApp connection via direct RPC.

    This calls the 'restart' RPC method which stops and starts the service,
    unlike 'start' which only starts if not running.
    """
    try:
        # Force fresh connection to avoid stale WebSocket
        client = await get_client(force_reconnect=True)

        # Broadcast that we're restarting (brief disconnected state)
        from . import broadcast_whatsapp_status

        await broadcast_whatsapp_status(
            connected=False,
            has_session=False,
            running=True,
            pairing=False,
            device_id=None,
            qr=None,
        )

        # Call restart RPC method
        result = await client.call("restart")

        return {
            "success": True,
            "message": "WhatsApp connection restarted",
            "data": result,
            "timestamp": time.time()
        }
    except HTTPException as e:
        logger.error(f"WhatsApp restart failed: {e.detail}")
        return {"success": False, "error": e.detail}
    except Exception as e:
        logger.error(f"WhatsApp restart failed: {e}")
        return {"success": False, "error": str(e)}


async def handle_whatsapp_groups() -> dict:
    """Get list of WhatsApp groups via direct RPC."""
    try:
        client = await get_client()
        groups = await client.call("groups")

        return {
            "success": True,
            "groups": groups or [],
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"WhatsApp groups fetch failed: {e}")
        return {"success": False, "error": str(e), "groups": []}


async def handle_whatsapp_group_info(group_id: str) -> dict:
    """Get group info including participants with resolved phone numbers.

    Args:
        group_id: Group JID (e.g., '120363422738675920@g.us')

    Returns:
        Group info with participants containing both 'jid' (LID) and 'phone' (resolved number)
    """
    try:
        if not group_id:
            return {"success": False, "error": "group_id is required", "participants": []}

        client = await get_client()
        result = await client.call("group_info", {"group_id": group_id})

        if not result:
            return {"success": False, "error": "Failed to get group info", "participants": []}

        # Extract participants with phone numbers
        participants = []
        for p in result.get('participants', []):
            jid = p.get('jid', '')
            phone = p.get('phone', '')
            name = p.get('name', '')

            # Only include participants with resolved phone numbers
            if phone:
                participants.append({
                    "jid": jid,
                    "phone": phone,
                    "name": name or phone,  # Use phone as fallback name
                    "is_admin": p.get('is_admin', False),
                    "is_super_admin": p.get('is_super_admin', False)
                })

        return {
            "success": True,
            "group_id": group_id,
            "name": result.get('name', ''),
            "participants": participants,
            "participant_count": len(participants),
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"WhatsApp group_info fetch failed for {group_id}: {e}")
        return {"success": False, "error": str(e), "participants": []}


async def handle_whatsapp_chat_history(params: dict) -> dict:
    """Get chat history from WhatsApp via direct RPC.

    Retrieves stored messages from the Go service's history store.
    Messages are automatically stored from HistorySync (on first login)
    and from real-time incoming messages.

    Params:
    - chat_id: Direct chat JID (e.g., '919876543210@s.whatsapp.net')
    - phone: Phone number (alternative to chat_id, will be converted)
    - group_id: Group JID (alternative for group chats)
    - limit: Max messages to return (default 50, max 500)
    - offset: Pagination offset (default 0)
    - sender_phone: Filter by sender phone in group chats
    - text_only: Only return text messages (default false)

    Returns:
    - messages: Array of MessageRecord
    - total: Total matching messages count
    - has_more: Whether more messages exist
    """
    try:
        client = await get_client()

        # Build RPC params
        rpc_params = {}

        # Determine chat_id from various inputs
        chat_id = params.get("chat_id")
        phone = params.get("phone")
        group_id = params.get("group_id")

        if chat_id:
            rpc_params["chat_id"] = chat_id
        elif phone:
            rpc_params["phone"] = phone
        elif group_id:
            rpc_params["group_id"] = group_id
        else:
            return {"success": False, "error": "Either chat_id, phone, or group_id is required"}

        # Optional filters
        limit = params.get("limit", 50)
        if limit > 500:
            limit = 500
        rpc_params["limit"] = limit

        offset = params.get("offset", 0)
        rpc_params["offset"] = offset

        sender_phone = params.get("sender_phone")
        if sender_phone:
            rpc_params["sender_phone"] = sender_phone

        text_only = params.get("text_only", False)
        rpc_params["text_only"] = text_only

        result = await client.call("chat_history", rpc_params)

        return {
            "success": True,
            "messages": result.get("messages", []),
            "total": result.get("total", 0),
            "has_more": result.get("has_more", False),
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"WhatsApp chat_history fetch failed: {e}")
        return {"success": False, "error": str(e), "messages": [], "total": 0, "has_more": False}


async def whatsapp_rpc_call(method: str, params: dict = None) -> dict:
    """Generic RPC call to WhatsApp Go service.

    Used by handlers/whatsapp.py for operations like:
    - groups: List all groups
    - group_info: Get group details with participants
    - contacts: List contacts with saved names
    - contact_info: Get full contact info (for send/reply)
    - contact_check: Check WhatsApp registration status

    Args:
        method: RPC method name (e.g., 'groups', 'contact_info')
        params: Method parameters dict

    Returns:
        RPC result dict or error dict
    """
    try:
        client = await get_client()
        result = await client.call(method, params or {})
        return result if isinstance(result, dict) else {"result": result, "success": True}
    except Exception as e:
        logger.error(f"WhatsApp RPC call '{method}' failed: {e}")
        return {"success": False, "error": str(e)}


async def handle_whatsapp_newsletters() -> dict:
    """Get list of subscribed WhatsApp newsletter channels via RPC.

    Used by WebSocket handler for loadOptions dropdown in whatsappDb/whatsappReceive nodes.

    Returns:
        Dict with channels list containing jid and name
    """
    try:
        client = await get_client()
        channels = await client.call("newsletters")

        return {
            "success": True,
            "channels": channels or [],
            "timestamp": time.time()
        }
    except Exception as e:
        logger.error(f"WhatsApp newsletters fetch failed: {e}")
        return {"success": False, "error": str(e), "channels": []}
