"""
Android Relay Client Manager

Global instance management for persistent WebSocket connection.
Provides singleton pattern for reusing connection across API requests.
"""

from typing import Optional
import structlog

from .client import RelayWebSocketClient

logger = structlog.get_logger()

# Global client instance
_relay_client: Optional[RelayWebSocketClient] = None


async def get_relay_client(base_url: str, api_key: str) -> tuple[Optional[RelayWebSocketClient], str]:
    """
    Get or create persistent relay client instance.

    Args:
        base_url: WebSocket URL (e.g., 'wss://your-relay-server.com/ws')
        api_key: API key for authentication

    Returns:
        Tuple of (client or None, error_message)
    """
    global _relay_client

    # Reuse existing connection if valid
    if _relay_client and _relay_client.is_connected():
        logger.info("[Manager] Reusing existing connection")
        return _relay_client, ""

    # Close stale connection
    if _relay_client:
        await _relay_client.disconnect()

    # Create new connection
    logger.info("[Manager] Creating new connection", url=base_url)
    _relay_client = RelayWebSocketClient(base_url, api_key)
    connected, error = await _relay_client.connect()

    if connected:
        logger.info("[Manager] Connection established")
        return _relay_client, ""
    else:
        _relay_client = None
        logger.error("[Manager] Failed to connect", error=error)
        return None, error


async def close_relay_client(clear_stored_session: bool = True):
    """Close global relay client.

    Args:
        clear_stored_session: If True, clear stored pairing session from database.
                              This prevents auto-reconnect on next client connect.
    """
    global _relay_client
    if _relay_client:
        logger.info("[Manager] Closing connection", clear_stored_session=clear_stored_session)
        await _relay_client.disconnect(clear_stored_session=clear_stored_session)
        _relay_client = None


def get_current_relay_client() -> Optional[RelayWebSocketClient]:
    """
    Get current relay client if connected.

    Returns:
        Connected client or None
    """
    global _relay_client
    if _relay_client and _relay_client.is_connected():
        return _relay_client
    return None
