"""Google Workspace service-status refresh callback (Wave 11.I, milestone J).

Moved from ``services/status_broadcaster._refresh_google_status``.
Plugin packages register their own callback via
``status_broadcaster.register_service_refresh``; the broadcaster no
longer hardcodes a per-service refresh.

Reads OAuth tokens via ``auth_service.get_oauth_tokens("google")`` and
mirrors the result into the broadcaster cache.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from services.status_broadcaster import StatusBroadcaster

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def refresh_google_status(broadcaster: "StatusBroadcaster") -> None:
    """Refresh Google cache + broadcast. One pass per
    ``_refresh_all_services`` cycle.
    """
    with tracer.start_as_current_span("broadcaster.refresh_google") as span:
        try:
            from services.plugin.deps import get_auth_service

            auth_service = get_auth_service()
            tokens = await auth_service.get_oauth_tokens("google", customer_id="owner")
            if not tokens or not tokens.get("access_token"):
                broadcaster._status["google"] = {
                    "connected": False,
                    "email": None,
                    "name": None,
                }
            else:
                broadcaster._status["google"] = {
                    "connected": True,
                    "email": tokens.get("email"),
                    "name": tokens.get("name"),
                }
                logger.debug(
                    "[StatusBroadcaster] Google status: connected as %s",
                    tokens.get("email"),
                )

            await broadcaster.broadcast(
                {
                    "type": "google_status",
                    "data": broadcaster._status["google"],
                }
            )
            span.set_attribute("connected", bool(broadcaster._status["google"]["connected"]))
        except Exception as exc:  # noqa: BLE001
            span.record_exception(exc)
            logger.debug("[StatusBroadcaster] Could not refresh Google status: %s", exc)
