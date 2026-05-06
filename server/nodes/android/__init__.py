"""Plugins for the 'android' palette group.

Self-contained plugin folder (Wave 11.H pattern) — owns its
WebSocket handlers (`_handlers.py`), HTTP router (`_router.py`),
action-dispatch service (`_dispatcher.py`), and the relay-pairing
sub-package (`_relay/`). The 16 service plugins (battery_monitor,
wifi_automation, ...) live alongside as siblings.

Wiring is body-free; both registrations are idempotent.
"""

from services.status_broadcaster import register_service_refresh
from services.ws_handler_registry import register_router, register_ws_handlers

from . import _router
from ._handlers import WS_HANDLERS
from ._refresh import refresh_android_status

register_ws_handlers(WS_HANDLERS)
register_router(_router.router, name="android")
register_service_refresh(refresh_android_status)
