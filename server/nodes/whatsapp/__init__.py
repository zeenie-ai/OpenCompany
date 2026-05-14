"""Plugins for the 'whatsapp' palette group.

Public surface:
    WhatsAppRuntime          - edgymeow Go binary supervisor
    get_whatsapp_runtime()   - singleton accessor (lazy-init via classmethod)

Two self-registrations happen on package import:

1. The Go-binary supervisor (``WhatsAppRuntime``) is registered with
   :mod:`services._supervisor`, so the FastAPI lifespan teardown
   reaches it via ``shutdown_all_supervisors()``.

2. The WebSocket handler dispatch table (``WS_HANDLERS`` in
   :mod:`._handlers`) is registered with
   :mod:`services.ws_handler_registry`, so the central WS router in
   ``routers/websocket.py`` picks up every ``whatsapp_*`` message type
   without per-plugin imports. Same pattern as the telegram reference
   plugin.
"""

from services._supervisor import register_supervisor
from services.event_waiter import register_filter_builder
from services.status_broadcaster import register_service_refresh
from services.ws_handler_registry import (
    register_option_loader,
    register_ws_handlers,
)

from ._credentials import WhatsAppCredential  # noqa: F401 — side-effect register
from ._events import (  # noqa: F401 — re-exported for callers
    broadcast_whatsapp_history_synced,
    broadcast_whatsapp_message,
    broadcast_whatsapp_newsletter,
    broadcast_whatsapp_status,
)
from ._filters import build_filter as build_whatsapp_filter
from ._handlers import WS_HANDLERS
from ._option_loaders import load_channels, load_group_members, load_groups
from ._refresh import refresh_whatsapp_status
from ._runtime import WhatsAppRuntime, get_whatsapp_runtime

# Supervisor: ensures shutdown_all_supervisors() reaches us.
# get_instance() constructs the singleton once (lazy in spawn, not here).
register_supervisor(WhatsAppRuntime.get_instance())

# WebSocket handlers: 19 message types (status / qr / send / restart /
# groups / newsletters / chat_history / rate_limit_* / mark_read /
# typing / presence / diagnostics / stop). Idempotent on re-import.
register_ws_handlers(WS_HANDLERS)

# Service-status refresh callback (Wave 11.I, milestone J) -- the
# broadcaster fans out to this on lifespan startup instead of
# hardcoding the call.
register_service_refresh(refresh_whatsapp_status)

# Trigger-event filter builder (Wave 11.I, milestone K) -- moved out
# of services/event_waiter.py so the central FILTER_BUILDERS table
# carries no plugin-specific code.
register_filter_builder("whatsappReceive", build_whatsapp_filter)

# loadOptionsMethod loaders (Wave 11.I, milestone M.1) -- self-register
# into services.ws_handler_registry's option-loader registry, sibling to
# the WS-handler / router registries.
register_option_loader("whatsappGroups", load_groups)
register_option_loader("whatsappChannels", load_channels)
register_option_loader("whatsappGroupMembers", load_group_members)

__all__ = [
    "WhatsAppRuntime",
    "get_whatsapp_runtime",
]
