"""
Android Status Broadcaster

Relay-specific helpers that translate relay-shaped arguments
(``devices: Set[str]``, derived ``connection_type="relay"``) into the
canonical :func:`nodes.android.broadcast_android_status` shape. Wave 12
B1 — the actual broadcast emission moved into
:mod:`nodes.android._events` (plugin-owned). This file just adapts
relay-state to that shape.
"""

from typing import Optional, Set
import structlog

logger = structlog.get_logger()


async def _emit_relay_status(
    connected: bool,
    paired: bool = False,
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    devices: Optional[Set[str]] = None,
    qr_data: Optional[str] = None,
):
    """Translate relay-shaped args and route to the canonical
    plugin-owned broadcaster (``nodes.android._events``)."""
    try:
        # Import from the plugin package — single source of truth
        # for the android status wire format (Wave 12 B1).
        from nodes.android import broadcast_android_status

        await broadcast_android_status(
            connected=connected,
            paired=paired,
            device_id=device_id,
            device_name=device_name,
            connected_devices=list(devices) if devices else [],
            connection_type="relay" if connected else None,
            qr_data=qr_data,
        )
    except Exception as e:
        logger.warning("[Android] Failed to broadcast status", error=str(e))


async def broadcast_connected(device_id: str, device_name: Optional[str] = None):
    """Broadcast that Android device is paired"""
    await _emit_relay_status(
        connected=True, paired=True, device_id=device_id, device_name=device_name, devices={device_id} if device_id else set()
    )


async def broadcast_device_disconnected(relay_connected: bool = True, qr_data: Optional[str] = None):
    """Broadcast that Android device is disconnected (but relay may still be connected).

    This is called when the Android device unpairs. The relay connection may still be active.
    Use broadcast_relay_disconnected() when the relay connection itself is closed.

    Args:
        relay_connected: Whether the relay WebSocket is still connected
        qr_data: QR code data for re-pairing
    """
    await _emit_relay_status(
        connected=relay_connected,  # Relay may still be connected
        paired=False,  # Device is disconnected
        device_id=None,
        device_name=None,
        devices=set(),
        qr_data=qr_data,  # Keep QR data for re-pairing
    )


async def broadcast_relay_disconnected():
    """Broadcast that relay connection is closed (fully disconnected)"""
    await _emit_relay_status(connected=False, paired=False, device_id=None, device_name=None, devices=set())


# Legacy alias for backwards compatibility
async def broadcast_disconnected():
    """Legacy alias - use broadcast_device_disconnected or broadcast_relay_disconnected instead"""
    await broadcast_relay_disconnected()


async def broadcast_qr_code(qr_data: str):
    """Broadcast QR code for pairing"""
    await _emit_relay_status(
        connected=True,  # Connected to relay but not paired
        paired=False,
        device_id=None,
        device_name=None,
        devices=set(),
        qr_data=qr_data,
    )
