"""Android service-status refresh callback (Wave 11.I, milestone J).

Moved from ``services/status_broadcaster._auto_reconnect_android_relay``
(plus the ``_auto_reconnect_android_relay_body`` helper). Plugin
packages register their own callback via
``status_broadcaster.register_service_refresh``; the broadcaster no
longer hardcodes a per-service refresh.

Load-bearing: this is the path that auto-reconnects the Android relay
from a stored pairing session after server restart. Without it the
relay sits idle until the user manually clicks Connect. Runs once on
lifespan startup (post-V).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from services.status_broadcaster import StatusBroadcaster

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def refresh_android_status(broadcaster: "StatusBroadcaster") -> None:
    """Auto-reconnect to the Android relay if a stored pairing exists.

    Called once per ``_refresh_all_services`` cycle (post-V: at
    lifespan startup only).
    """
    with tracer.start_as_current_span("broadcaster.refresh_android") as span:
        await _auto_reconnect_body(broadcaster, span)


async def _auto_reconnect_body(broadcaster: "StatusBroadcaster", span) -> None:
    # Single source of truth for android status emission — owns both
    # the cache update + the dual-emit (legacy raw + typed sibling).
    from ._events import broadcast_android_status

    try:
        # Already connected? Refresh the cached snapshot and stop.
        from ._relay.manager import get_current_relay_client

        existing = get_current_relay_client()
        if existing and existing.is_connected():
            await broadcast_android_status(
                connected=True,
                paired=existing.is_paired(),
                device_id=existing.paired_device_id,
                device_name=existing.paired_device_name,
                connected_devices=list(existing.get_connected_devices()),
                connection_type="relay",
                qr_data=existing.qr_data,
            )
            logger.debug("[StatusBroadcaster] Android relay already connected")
            span.set_attribute("path", "already_connected")
            return

        # Look for a stored pairing session.
        from services.plugin.deps import get_database

        database = get_database()
        session = await database.get_android_relay_session()
        if not session:
            span.set_attribute("path", "no_session")
            logger.debug("[StatusBroadcaster] No stored Android relay session")
            return

        relay_url = session.get("relay_url")
        api_key = session.get("api_key")
        device_id = session.get("device_id")

        if not relay_url or not api_key:
            span.set_attribute("path", "session_missing_creds")
            logger.debug("[StatusBroadcaster] Stored session missing relay URL or API key")
            return

        span.set_attribute("path", "auto_reconnect")
        logger.info(
            "[StatusBroadcaster] Auto-reconnecting to Android relay...",
            relay_url=relay_url,
            device_id=device_id,
        )

        from ._relay.manager import get_relay_client

        client, error = await get_relay_client(relay_url, api_key)

        if client and client.is_connected():
            logger.info("[StatusBroadcaster] Android relay reconnected successfully")
            # The relay server creates a new session on each connect, so
            # pairing may be lost -- mirror whatever the new client
            # reports.
            await broadcast_android_status(
                connected=True,
                paired=client.is_paired(),
                device_id=client.paired_device_id,
                device_name=client.paired_device_name,
                connected_devices=list(client.get_connected_devices()),
                connection_type="relay",
                qr_data=client.qr_data,
            )
            span.set_attribute("reconnect_ok", True)
        else:
            span.set_attribute("reconnect_ok", False)
            logger.warning("[StatusBroadcaster] Failed to reconnect Android relay: %s", error)
            # Stored session is stale; drop it.
            await database.clear_android_relay_session()
    except Exception as exc:  # noqa: BLE001 -- mirror pre-migration behaviour
        span.record_exception(exc)
        logger.debug("[StatusBroadcaster] Could not auto-reconnect Android relay: %s", exc)
