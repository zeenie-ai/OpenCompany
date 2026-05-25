"""Telegram service-status refresh callback.

Moved from ``services/status_broadcaster._refresh_telegram_status``.
The broadcaster no longer hardcodes a per-service refresh — instead
plugin packages register their own callback via
``status_broadcaster.register_service_refresh``.

The callback runs once per WebSocket client connect (inside the
``_refresh_all_services`` TaskGroup).  Auth reads + owner restore
happen inside :class:`TelegramService`; this function just decides
whether to attempt an auto-reconnect and mirrors the resulting status
into the broadcaster cache.
"""

from __future__ import annotations

import logging
from typing import Dict, TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from services.status_broadcaster import StatusBroadcaster

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def refresh_telegram_status(broadcaster: "StatusBroadcaster") -> None:
    """Refresh Telegram cache on the broadcaster and auto-reconnect if needed.

    Called once per ``_refresh_all_services`` cycle. OTel span emitted
    so cold-start ``getMe`` time is observable -- the historical
    bottleneck on local DNS for the first WebSocket connect.

    Wave 12 B3: routes through plugin _events.py wrapper. Cache write
    + dual-emit (legacy raw + typed sibling) all in one call.
    """
    with tracer.start_as_current_span("broadcaster.refresh_telegram") as span:
        try:
            from ._events import broadcast_telegram_status
            from ._service import get_telegram_service

            service = get_telegram_service()

            async def _emit(connected: bool, has_token: bool) -> None:
                """Snapshot service state and broadcast (cache write + WS)."""
                s = service.get_status() if connected else {}
                await broadcast_telegram_status(
                    connected=connected,
                    bot_id=s.get("bot_id") if connected else None,
                    bot_username=s.get("bot_username") if connected else None,
                    bot_name=s.get("bot_name") if connected else None,
                    owner_chat_id=s.get("owner_chat_id") if connected else None,
                    has_stored_token=has_token,
                )

            if service.connected:
                await _emit(True, True)
                span.set_attribute("path", "already_connected")
            elif not await service.has_stored_token():
                await _emit(False, False)
                span.set_attribute("path", "no_token")
            else:
                span.set_attribute("path", "auto_reconnect")
                logger.info("[StatusBroadcaster] Auto-reconnecting Telegram bot...")
                result = await service.connect()
                ok = bool(result.get("success"))
                await _emit(ok, True)
                span.set_attribute("reconnect_ok", ok)
                if ok:
                    bot_username = broadcaster._status["telegram"].get("bot_username")
                    logger.info(f"[StatusBroadcaster] Telegram auto-reconnected: @{bot_username}")
                else:
                    logger.warning(f"[StatusBroadcaster] Telegram auto-reconnect failed: " f"{result.get('error')}")

            span.set_attribute("connected", bool(broadcaster._status["telegram"]["connected"]))
        except Exception as e:
            span.record_exception(e)
            logger.debug(f"[StatusBroadcaster] Could not refresh Telegram status: {e}")


async def precheck_telegram_trigger(parameters: Dict) -> str | None:
    """Trigger-precheck: ensure the Telegram bot is connected before
    entering the event-wait loop.

    Returns an error string to short-circuit, or None to proceed.
    """
    from ._service import get_telegram_service

    service = get_telegram_service()
    if not service.connected:
        return "Telegram bot not connected. Add bot token in Credentials."
    sender_filter = parameters.get("sender_filter", "all")
    logger.info("[TelegramTrigger] starting " f"sender_filter={sender_filter} " f"owner_detected={service.owner_chat_id is not None}")
    return None
