"""WhatsApp service-status refresh callback (Wave 11.I, milestone J).

Moved from ``services/status_broadcaster._refresh_whatsapp_status``.
Plugin packages register their own callback via
``status_broadcaster.register_service_refresh``; the broadcaster no
longer hardcodes a per-service refresh.

Calls the Go-service ``status`` RPC via the plugin's own client and
mirrors the result into the broadcaster cache. Silently swallows
exceptions if the WhatsApp Go service is unavailable -- mirrors
pre-migration behaviour.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from services.status_broadcaster import StatusBroadcaster

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def refresh_whatsapp_status(broadcaster: "StatusBroadcaster") -> None:
    """Refresh WhatsApp cache + broadcast. One pass per
    ``_refresh_all_services`` cycle. Wave 12 B2 — routes through the
    plugin's canonical broadcaster wrapper so the wire shape is
    single-sourced in ``_events.py``.
    """
    with tracer.start_as_current_span("broadcaster.refresh_whatsapp") as span:
        try:
            from ._events import broadcast_whatsapp_status
            from ._service import get_client

            client = await get_client()
            status_data = await client.call("status")

            await broadcast_whatsapp_status(
                connected=status_data.get("connected", False),
                has_session=status_data.get("has_session", False),
                running=status_data.get("running", False),
                pairing=status_data.get("pairing", False),
                device_id=status_data.get("device_id"),
                qr=None,
            )
            logger.debug(
                "[StatusBroadcaster] Refreshed WhatsApp status: connected=%s",
                status_data.get("connected"),
            )
            span.set_attribute("connected", bool(status_data.get("connected", False)))
        except Exception as exc:  # noqa: BLE001
            span.record_exception(exc)
            logger.debug("[StatusBroadcaster] Could not refresh WhatsApp status: %s", exc)
