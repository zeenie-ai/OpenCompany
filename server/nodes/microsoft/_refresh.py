"""Microsoft Graph service-status refresh callback.

Registered via ``status_broadcaster.register_service_refresh``. Reads
OAuth tokens via ``auth_service.get_oauth_tokens("microsoft")`` and
mirrors the connected/disconnected state into the broadcaster cache so
a freshly-connected client reflects status on the next refresh cycle.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from services.status_broadcaster import StatusBroadcaster

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def refresh_microsoft_status(broadcaster: "StatusBroadcaster") -> None:
    """Refresh Microsoft cache. One pass per ``_refresh_all_services`` cycle."""
    with tracer.start_as_current_span("broadcaster.refresh_microsoft") as span:
        try:
            from services.plugin.deps import get_auth_service

            auth_service = get_auth_service()
            tokens = await auth_service.get_oauth_tokens("microsoft", customer_id="owner")
            if not tokens or not tokens.get("access_token"):
                broadcaster._status["microsoft"] = {
                    "connected": False,
                    "email": None,
                    "name": None,
                }
            else:
                broadcaster._status["microsoft"] = {
                    "connected": True,
                    "email": tokens.get("email"),
                    "name": tokens.get("name"),
                }
            span.set_attribute("connected", bool(broadcaster._status["microsoft"]["connected"]))
        except Exception as exc:  # noqa: BLE001
            span.record_exception(exc)
            logger.debug("[StatusBroadcaster] Could not refresh Microsoft status: %s", exc)
