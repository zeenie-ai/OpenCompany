"""Wave 12 B1: CloudEvents factories + broadcaster wrappers for android.

Plugin-specific event emission — replaces the
``StatusBroadcaster.update_android_status`` method (deleted in this
commit) and the direct ``broadcaster._status["android"]`` mutations
inside ``_refresh.py`` / ``_relay/broadcaster.py``.

Per RFC plugin_authoring_rfc.md §6.4: plugin-specific factories live in
the plugin folder. The cross-cutting ``WorkflowEvent.connection_status``
factory in ``services/events/envelope.py`` is borderline (parametrized
by plugin but each plugin's payload differs) — Phase B moves the
plugin-specific shape into per-plugin factories.

Wire format (Wave 12 D4 — legacy ``android_status`` raw frame retired):
  - Typed CloudEvents sibling: ``{type: "plugin_connection_status",
    data: <WorkflowEvent envelope>}``

The FE handler at ``client/src/contexts/WebSocketContext.tsx::case
'plugin_connection_status'`` routes by ``envelope.source`` substring to
the matching Zustand setter.

Caller pattern (from ``_refresh.py`` / ``_relay/broadcaster.py``):
    from nodes.android import broadcast_android_status
    await broadcast_android_status(connected=True, device_id="...", ...)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.events.envelope import WorkflowEvent


# Wire-routing key — outer ``type`` field on the WS frame.
_TYPED_WIRE_KEY = "plugin_connection_status"

# Module-level cache mirror — kept in sync with
# ``StatusBroadcaster._status["android"]`` until the framework's
# ``get_status()`` snapshot path migrates to a per-plugin getter
# registry. The plugin owns the SHAPE of this dict; the broadcaster
# stores it.
_DEFAULT_STATUS: Dict[str, Any] = {
    "connected": False,
    "paired": False,
    "device_id": None,
    "device_name": None,
    "connected_devices": [],
    "connection_type": None,
    "qr_data": None,
    "session_token": None,
}


# ---- Typed factory ---------------------------------------------------------


def android_connection_status(
    *,
    connected: bool,
    paired: bool = False,
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    connected_devices: Optional[List[str]] = None,
    connection_type: Optional[str] = None,
    qr_data: Optional[str] = None,
    session_token: Optional[str] = None,
) -> WorkflowEvent:
    """Build a typed CloudEvents envelope describing the android relay
    connection state.

    ``subject`` is the device id so the FE can route per-device updates.
    ``data`` carries the full android status payload (mirrors
    ``_DEFAULT_STATUS`` shape).
    """
    return WorkflowEvent(
        source="machinaos://nodes/android",
        type=(
            "com.machinaos.android.connection.opened"
            if connected
            else "com.machinaos.android.connection.closed"
        ),
        subject=device_id,
        data={
            "connected": connected,
            "paired": paired,
            "device_id": device_id,
            "device_name": device_name,
            "connected_devices": list(connected_devices or []),
            "connection_type": connection_type,
            "qr_data": qr_data,
            "session_token": session_token,
        },
    )


# ---- Broadcaster wrapper ---------------------------------------------------


async def broadcast_android_status(
    *,
    connected: bool,
    paired: bool = False,
    device_id: Optional[str] = None,
    device_name: Optional[str] = None,
    connected_devices: Optional[List[str]] = None,
    connection_type: Optional[str] = None,
    qr_data: Optional[str] = None,
    session_token: Optional[str] = None,
) -> None:
    """Update the android status cache + emit the typed
    ``plugin_connection_status`` CloudEvents envelope.

    Replaces ``StatusBroadcaster.update_android_status``. The legacy
    raw ``android_status`` frame retired in Wave 12 D4 — FE consumes
    via the envelope-aware ``plugin_connection_status`` case in
    ``WebSocketContext.tsx`` (routes by ``envelope.source``).
    """
    from services.status_broadcaster import get_status_broadcaster

    broadcaster = get_status_broadcaster()

    # Status cache lives on the broadcaster (still consumed by
    # ``StatusBroadcaster.get_android_status()`` + the WS-connect
    # initial-status snapshot). Plugin owns the SHAPE of the cache.
    payload: Dict[str, Any] = {
        "connected": connected,
        "paired": paired,
        "device_id": device_id,
        "device_name": device_name,
        "connected_devices": list(connected_devices or []),
        "connection_type": connection_type,
        "qr_data": qr_data,
        "session_token": session_token,
    }
    broadcaster._status["android"] = payload

    event = android_connection_status(
        connected=connected,
        paired=paired,
        device_id=device_id,
        device_name=device_name,
        connected_devices=connected_devices,
        connection_type=connection_type,
        qr_data=qr_data,
        session_token=session_token,
    )
    await broadcaster.broadcast({
        "type": _TYPED_WIRE_KEY,
        "data": event.model_dump(mode="json"),
    })


__all__ = [
    "android_connection_status",
    "broadcast_android_status",
]
