"""Twitter service-status refresh callback (Wave 11.I, milestone J).

Moved from ``services/status_broadcaster._refresh_twitter_status``.
Plugin packages register their own callback via
``status_broadcaster.register_service_refresh``; the broadcaster no
longer hardcodes a per-service refresh.

Reads OAuth tokens via ``auth_service.get_oauth_tokens("twitter")`` and
mirrors the result into the broadcaster cache. No plugin internals
are reached -- pure auth-service read + broadcast. The function still
runs in the broadcaster's TaskGroup so OTel spans aggregate the same
way they did pre-migration.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace

if TYPE_CHECKING:
    from services.status_broadcaster import StatusBroadcaster

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


async def refresh_twitter_status(broadcaster: "StatusBroadcaster") -> None:
    """Refresh Twitter cache + broadcast. One pass per
    ``_refresh_all_services`` cycle.
    """
    with tracer.start_as_current_span("broadcaster.refresh_twitter") as span:
        try:
            from services.plugin.deps import get_auth_service

            auth_service = get_auth_service()
            tokens = await auth_service.get_oauth_tokens("twitter", customer_id="owner")
            if not tokens or not tokens.get("access_token"):
                broadcaster._status["twitter"] = {
                    "connected": False,
                    "username": None,
                    "user_id": None,
                    "name": None,
                    "profile_image_url": None,
                }
            else:
                # User info is stored in the OAuth token record.
                # email field carries the ``@username`` form.
                email = tokens.get("email", "")
                name = tokens.get("name", "")
                username = email.lstrip("@") if email.startswith("@") else email
                broadcaster._status["twitter"] = {
                    "connected": True,
                    "username": username or None,
                    "user_id": None,
                    "name": name or None,
                    "profile_image_url": None,
                }
                logger.debug(
                    "[StatusBroadcaster] Twitter status: connected as @%s",
                    username,
                )

            await broadcaster.broadcast(
                {
                    "type": "twitter_status",
                    "data": broadcaster._status["twitter"],
                }
            )
            span.set_attribute("connected", bool(broadcaster._status["twitter"]["connected"]))
        except Exception as exc:  # noqa: BLE001 -- mirror pre-migration behaviour
            span.record_exception(exc)
            logger.debug("[StatusBroadcaster] Could not refresh Twitter status: %s", exc)
