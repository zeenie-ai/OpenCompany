"""
Android Services Relay WebSocket Client

Handles WebSocket connection to relay server and communication with paired Android device.

Connection flow:
1. Connect to wss://<relay-server>/ws?client_type=web&api_key=<your-api-key>
2. Receive connection.established with session_token and qr_data
3. Display QR code for Android to scan
4. Receive pairing.connected when Android pairs
5. Exchange messages via relay.send / relay.message
"""

import asyncio
import json
import uuid
import aiohttp
from typing import Optional, Dict, Any, Set, Callable
import structlog

from .protocol import RPCResponse, RPCRequestTracker, is_response
from .broadcaster import broadcast_connected, broadcast_device_disconnected, broadcast_relay_disconnected, broadcast_qr_code

logger = structlog.get_logger()


class RelayWebSocketClient:
    """WebSocket client for Android Services Relay using JSON-RPC 2.0 protocol"""

    def __init__(self, base_url: str, api_key: str):
        """
        Initialize relay client.

        Args:
            base_url: Base WebSocket URL (e.g., 'wss://your-relay-server.com/ws')
            api_key: API key for authentication
        """
        self.base_url = base_url
        self.api_key = api_key
        self.url = f"{base_url}?client_type=web&api_key={api_key}"

        # WebSocket connection
        self.ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self.session: Optional[aiohttp.ClientSession] = None
        self.connected = False

        # JSON-RPC request tracking
        self._rpc_tracker = RPCRequestTracker()

        # Pairing state
        self.session_token: Optional[str] = None
        self.qr_data: Optional[str] = None
        self.paired = False
        self.paired_device_id: Optional[str] = None
        self.paired_device_name: Optional[str] = None

        # Background tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._running = False

        # Service response queues (requestId -> queue)
        self._service_queues: Dict[str, asyncio.Queue] = {}

        # Event callbacks
        self.on_pairing_connected: Optional[Callable] = None
        self.on_pairing_disconnected: Optional[Callable] = None
        self.on_relay_message: Optional[Callable] = None

    def _safe_error(self, value: object) -> str:
        """Redact relay credentials from errors before logging or returning them."""
        message = str(value)
        for secret in (self.api_key, self.session_token):
            if secret:
                message = message.replace(secret, "[REDACTED]")
        return message

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def connect(self) -> tuple[bool, str]:
        """Connect to relay WebSocket server.

        Returns:
            Tuple of (success: bool, error_message: str)
        """
        try:
            logger.debug("[Relay] Connecting...", url=self.base_url)
            timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=300)
            self.session = aiohttp.ClientSession(timeout=timeout)

            self.ws = await self.session.ws_connect(
                self.url,
                heartbeat=30,
                autoping=True,
                ssl=True,  # Explicit SSL for wss://
            )
            self.connected = True
            self._running = True

            logger.debug("[Relay] WebSocket connected, waiting for server message...", url=self.base_url)

            # Wait for connection.established event
            msg = await asyncio.wait_for(self.ws.receive(), timeout=10.0)

            if msg.type == aiohttp.WSMsgType.TEXT:
                data = json.loads(msg.data)
                method = data.get("method")
                logger.debug("[Relay] Received initial message", method=method)

                # Handle both "welcome" and "connection.established" methods
                if method in ("welcome", "connection.established"):
                    params = data.get("params", {})
                    self.session_token = params.get("session_token")
                    self.qr_data = params.get("qr_data")

                    logger.info("[Relay] Connection established", has_qr=bool(self.qr_data))

                    # Broadcast QR data to frontend
                    if self.qr_data:
                        await broadcast_qr_code(self.qr_data)

                    # Start background tasks
                    self._receive_task = asyncio.create_task(self._receive_loop())
                    self._keepalive_task = asyncio.create_task(self._keepalive_loop())

                    return True, ""
                elif data.get("error"):
                    error_msg = self._safe_error(data.get("error", {}).get("message", "Unknown server error"))
                    logger.error("[Relay] Server error", error=error_msg)
                    await self._close_failed_connection()
                    return False, f"Server error: {error_msg}"
                else:
                    logger.error("[Relay] Unexpected initial message", method=method)
                    await self._close_failed_connection()
                    return False, f"Unexpected response: {method or 'unknown'}"

            elif msg.type == aiohttp.WSMsgType.CLOSE:
                close_code = msg.data
                close_reason = msg.extra or "Unknown"
                logger.error("[Relay] Connection closed by server", code=close_code, reason=close_reason)
                await self._close_failed_connection()
                return False, f"Connection closed: {close_reason} (code {close_code})"

            elif msg.type == aiohttp.WSMsgType.ERROR:
                logger.error("[Relay] WebSocket error on receive")
                await self._close_failed_connection()
                return False, "WebSocket error during handshake"

            await self._close_failed_connection()
            return False, "No response from server"

        except asyncio.TimeoutError:
            logger.error("[Relay] Connection timeout")
            await self._close_failed_connection()
            return False, "Connection timeout - server not responding"
        except aiohttp.ClientConnectorError as e:
            logger.error("[Relay] Connection failed", error=self._safe_error(e))
            await self._close_failed_connection()
            return False, f"Cannot connect to server: {self._safe_error(e)}"
        except aiohttp.WSServerHandshakeError as e:
            logger.error("[Relay] WebSocket handshake failed", error=self._safe_error(e))
            await self._close_failed_connection()
            return False, f"WebSocket handshake failed: {self._safe_error(e)}"
        except Exception as e:
            # Do not attach the raw traceback: aiohttp exceptions can embed the
            # authenticated WebSocket URL (and therefore the API key).
            logger.error("[Relay] Connection error", error=self._safe_error(e))
            await self._close_failed_connection()
            return False, f"Connection error: {self._safe_error(e)}"

    async def _close_failed_connection(self) -> None:
        """Close transport resources after a handshake failure without clearing persisted pairing."""
        self._running = False
        self.connected = False
        if self.ws and not self.ws.closed:
            await self.ws.close()
        if self.session and not self.session.closed:
            await self.session.close()
        self.ws = None
        self.session = None

    async def disconnect(self, clear_stored_session: bool = True):
        """Close connection and cleanup.

        Args:
            clear_stored_session: If True, clear stored pairing session from database.
                                  Set to False when disconnecting due to connection drop
                                  (will try to auto-reconnect later).
        """
        logger.debug("[Relay] Disconnecting...", clear_stored_session=clear_stored_session)
        self._running = False

        # Cancel background tasks
        for task in [self._keepalive_task, self._receive_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close WebSocket
        if self.ws and not self.ws.closed:
            await self.ws.close()

        if self.session and not self.session.closed:
            await self.session.close()

        # Reset state
        self.connected = False
        self.paired = False
        self.paired_device_id = None
        self.paired_device_name = None
        self.session_token = None
        self.qr_data = None

        # Cancel pending RPC requests
        self._rpc_tracker.cancel_all()

        # Clear stored session if explicitly disconnecting
        if clear_stored_session:
            await self._clear_stored_session()

        # Broadcast relay disconnection (fully disconnected from relay server)
        await broadcast_relay_disconnected()

        logger.debug("[Relay] Disconnected")

    async def _clear_stored_session(self):
        """Clear stored pairing session from database."""
        try:
            from services.plugin.deps import get_database

            database = get_database()

            await database.clear_android_relay_session()
            logger.debug("[Relay] Cleared stored pairing session")
        except Exception as e:
            logger.warning("[Relay] Failed to clear stored session", error=str(e))

    def is_connected(self) -> bool:
        """Check if connected to relay server."""
        return self.connected and self._running and self.ws is not None and not self.ws.closed

    def is_paired(self) -> bool:
        """Check if paired with Android device."""
        return self.paired and self.paired_device_id is not None

    # =========================================================================
    # Background Tasks
    # =========================================================================

    async def _receive_loop(self):
        """Background task to receive messages."""
        logger.debug("[Relay] Receive loop started")
        unexpected_disconnect = False
        try:
            while self._running and self.ws and not self.ws.closed:
                try:
                    msg = await self.ws.receive()

                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        await self._handle_message(data)

                    elif msg.type == aiohttp.WSMsgType.CLOSED:
                        logger.warning("[Relay] Connection closed by server")
                        self._running = False
                        unexpected_disconnect = True
                        break

                    elif msg.type == aiohttp.WSMsgType.ERROR:
                        logger.error("[Relay] WebSocket error")
                        self._running = False
                        unexpected_disconnect = True
                        break

                except Exception as e:
                    logger.error("[Relay] Receive error", error=str(e))
                    await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            self.connected = False
            logger.debug("[Relay] Receive loop stopped")

            # Broadcast relay disconnection if connection dropped unexpectedly
            if unexpected_disconnect:
                try:
                    await broadcast_relay_disconnected()
                except Exception as e:
                    logger.warning("[Relay] Failed to broadcast disconnection", error=str(e))

    async def _keepalive_loop(self):
        """Background keepalive task."""
        try:
            while self._running and self.ws and not self.ws.closed:
                await asyncio.sleep(25)
                if self._running and self.ws and not self.ws.closed:
                    try:
                        await self.ws.send_json({"jsonrpc": "2.0", "method": "ping", "params": {}})
                    except Exception as e:
                        logger.error("[Relay] Keepalive error", error=str(e))
                        self._running = False
                        break
        except asyncio.CancelledError:
            pass

    # =========================================================================
    # Message Handling
    # =========================================================================

    async def _handle_message(self, data: dict):
        """Handle incoming JSON-RPC message."""
        # Log ALL incoming messages for debugging
        method = data.get("method", "")
        logger.debug("[Relay] Received message", method=method, has_result="result" in data, has_error="error" in data)

        # Check if response to pending request
        if is_response(data):
            response = RPCResponse.from_dict(data)
            logger.debug("[Relay] Processing as RPC response", id=response.id)
            if self._rpc_tracker.resolve(response):
                return

        # Handle server events
        params = data.get("params", {})

        if method == "pairing.connected":
            await self._handle_pairing_connected(params)

        elif method == "pairing.restored":
            # Handle auto-reconnect of previously paired device
            await self._handle_pairing_restored(params)

        elif method == "pairing.disconnected":
            await self._handle_pairing_disconnected(params)

        elif method == "relay.message":
            await self._handle_relay_message(params)

        elif method == "connection.established":
            # Reconnect scenario
            self.session_token = params.get("session_token")
            self.qr_data = params.get("qr_data")
            if self.qr_data:
                await broadcast_qr_code(self.qr_data)

    async def _handle_pairing_connected(self, params: dict):
        """Handle pairing.connected event."""
        self.paired = True
        self.paired_device_id = params.get("device_id")
        self.paired_device_name = params.get("device_name")

        logger.info("[Relay] Android paired", device_id=self.paired_device_id, device_name=self.paired_device_name)

        await broadcast_connected(self.paired_device_id, self.paired_device_name)

        # Persist pairing data for auto-reconnect on server restart
        await self._save_pairing_session()

        if self.on_pairing_connected:
            await self.on_pairing_connected(params)

    async def _handle_pairing_restored(self, params: dict):
        """Handle pairing.restored event - auto-reconnect of previously paired device."""
        self.paired = True
        self.paired_device_id = params.get("device_id")
        self.paired_device_name = params.get("device_name")

        logger.info(
            "[Relay] Android pairing restored (auto-reconnect)", device_id=self.paired_device_id, device_name=self.paired_device_name
        )

        await broadcast_connected(self.paired_device_id, self.paired_device_name)

        # Update saved session with latest info
        await self._save_pairing_session()

        if self.on_pairing_connected:
            await self.on_pairing_connected(params)

    async def _save_pairing_session(self):
        """Save pairing session to database for auto-reconnect."""
        try:
            from services.plugin.deps import get_database

            database = get_database()

            await database.save_android_relay_session(
                relay_url=self.base_url,
                api_key=self.api_key,
                device_id=self.paired_device_id,
                device_name=self.paired_device_name,
                session_token=self.session_token,
            )
            logger.debug("[Relay] Pairing session saved for auto-reconnect")
        except Exception as e:
            logger.warning("[Relay] Failed to save pairing session", error=str(e))

    async def _handle_pairing_disconnected(self, params: dict):
        """Handle pairing.disconnected event.

        The Android device has disconnected, but the relay connection may still be active.
        This allows the user to re-scan the QR code without reconnecting to the relay.
        """
        reason = params.get("reason", "unknown")
        logger.info("[Relay] Android device disconnected", reason=reason)

        self.paired = False
        self.paired_device_id = None
        self.paired_device_name = None

        # Broadcast device disconnection - relay is still connected, pass QR data for re-pairing
        await broadcast_device_disconnected(relay_connected=self.is_connected(), qr_data=self.qr_data)

        if self.on_pairing_disconnected:
            await self.on_pairing_disconnected(params)

    async def _handle_relay_message(self, params: dict):
        """Handle relay.message event from Android.

        Schema: relay.message params = {"data": {...}}
        The data contains the actual message from Android.
        """
        # Schema: params = {"data": {...}}
        data = params.get("data", {})

        logger.debug("[Relay] relay.message received", data_keys=list(data.keys()) if isinstance(data, dict) else "not_dict")

        # Route to service response queue if matching request_id
        # Android app uses "request_id" (underscore), not "requestId" (camelCase)
        request_id = data.get("request_id")
        logger.debug("[Relay] Checking request_id", request_id=request_id, waiting_for=list(self._service_queues.keys()))

        if request_id and request_id in self._service_queues:
            logger.debug("[Relay] Routing to service queue", request_id=request_id)
            await self._service_queues[request_id].put(data)
        elif self.on_relay_message:
            logger.debug("[Relay] Passing to on_relay_message callback")
            await self.on_relay_message(data)
        else:
            logger.warning("[Relay] Unhandled relay message", request_id=request_id, data=data)

    # =========================================================================
    # RPC Methods
    # =========================================================================

    async def call(self, method: str, params: Dict[str, Any] = None, timeout: float = 30) -> Any:
        """
        Make JSON-RPC 2.0 call and wait for response.

        Args:
            method: RPC method name
            params: Method parameters
            timeout: Response timeout in seconds

        Returns:
            Result from the RPC call
        """
        if not self.is_connected():
            raise Exception("Not connected to relay server")

        request, future = self._rpc_tracker.create_request(method, params)

        try:
            req_dict = request.to_dict()
            logger.debug("[Relay] Sending RPC request", method=method, id=request.id)
            await self.ws.send_json(req_dict)
            result = await asyncio.wait_for(future, timeout)
            logger.debug("[Relay] RPC response received", method=method, id=request.id)
            return result
        except asyncio.TimeoutError:
            self._rpc_tracker.cancel(request.id)
            raise Exception(f"RPC call '{method}' timed out after {timeout}s")

    async def get_pairing_status(self) -> Dict[str, Any]:
        """Get current pairing status."""
        return await self.call("pairing.status")

    async def disconnect_pairing(self) -> Dict[str, Any]:
        """End pairing session."""
        result = await self.call("pairing.disconnect")
        self.paired = False
        self.paired_device_id = None
        self.paired_device_name = None
        return result

    async def relay_send(self, data: Dict[str, Any], timeout: float = 30) -> Dict[str, Any]:
        """
        Send message to paired Android device via relay.

        Schema: relay.send params = {"data": {...}}

        Args:
            data: Message data to send to Android device
            timeout: Response timeout
        """
        if not self.paired:
            raise Exception("Not paired with Android device")

        logger.debug("[Relay] Sending relay.send RPC", service=data.get("service"), action=data.get("action"))

        # Schema: {"jsonrpc": "2.0", "method": "relay.send", "params": {"data": {...}}, "id": 1}
        result = await self.call("relay.send", {"data": data}, timeout=timeout)
        logger.debug("[Relay] relay.send RPC response", delivered=result.get("delivered") if isinstance(result, dict) else None)
        return result

    # =========================================================================
    # Service Requests
    # =========================================================================

    async def send_service_request(
        self,
        service_id: str,
        action: str,
        parameters: Dict[str, Any] = None,
        target_id: Optional[str] = None,  # Ignored, kept for compatibility
        timeout: float = 30.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Send service request to paired Android device.

        Args:
            service_id: Android service ID (e.g., 'battery', 'wifi_automation')
            action: Service action (e.g., 'status', 'enable')
            parameters: Action parameters
            target_id: Ignored (kept for API compatibility)
            timeout: Response timeout in seconds

        Returns:
            Response data or None if timeout/error
        """
        if not self.paired:
            logger.error("[Relay] Cannot send - not paired")
            return None

        request_id = str(uuid.uuid4())
        response_queue: asyncio.Queue = asyncio.Queue()
        self._service_queues[request_id] = response_queue

        try:
            # Send via relay - schema: relay.send params = {"data": {...}}
            # Field names must match Android app expectations:
            # - service (not serviceId)
            # - action
            # - request_id (not requestId)
            # - params (not parameters)
            await self.relay_send(
                {"service": service_id, "action": action, "request_id": request_id, "params": parameters or {}}, timeout=5.0
            )

            logger.debug("[Relay] Sent service request", request_id=request_id, service_id=service_id, action=action)

            # Wait for response
            logger.debug("[Relay] Waiting for response", request_id=request_id, timeout=timeout)
            response = await asyncio.wait_for(response_queue.get(), timeout=timeout)
            logger.debug("[Relay] Service response received", request_id=request_id)
            return response

        except asyncio.TimeoutError:
            logger.warning(
                "[Relay] Service response timeout", request_id=request_id, timeout=timeout, pending_queues=list(self._service_queues.keys())
            )
            return None
        except Exception as e:
            logger.error("[Relay] Service request error", error=str(e))
            return None
        finally:
            self._service_queues.pop(request_id, None)

    async def wait_for_pairing(self, timeout: float = 60.0) -> bool:
        """
        Wait for Android device to pair.

        Args:
            timeout: Maximum time to wait in seconds

        Returns:
            True if paired successfully, False if timeout
        """
        if self.paired:
            return True

        logger.info("[Relay] Waiting for pairing...", timeout=timeout)

        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            if self.paired:
                return True
            await asyncio.sleep(0.5)

        logger.warning("[Relay] Pairing timeout")
        return False

    # =========================================================================
    # Legacy Compatibility
    # =========================================================================

    def get_android_device_id(self) -> Optional[str]:
        """Get paired Android device ID."""
        return self.paired_device_id

    def has_real_android_devices(self) -> bool:
        """Check if paired with Android device."""
        return self.paired

    def get_connected_devices(self) -> Set[str]:
        """Get set of connected Android device IDs."""
        if self.paired_device_id:
            return {self.paired_device_id}
        return set()
