"""Plugins for the 'telegram' palette group.

This package owns the entire telegram surface area; nothing telegram-
specific lives outside this folder. Cross-cutting concerns plug into
generic registries that the consumers (router, broadcaster, event
waiter, trigger handler) read at dispatch time:

    _credentials.py     TelegramCredential (api-key)
    _service.py         TelegramService (singleton bot lifecycle)
    _handlers.py        WebSocket handlers + WS_HANDLERS dispatch dict
    _filters.py         build_telegram_filter (event-waiter filter)
    _refresh.py         refresh_telegram_status (broadcaster hook)
                        precheck_telegram_trigger (trigger pre-check)
    telegram_send.py    workflow ActionNode + AI tool
    telegram_receive.py workflow TriggerNode

On import, this package self-registers four callbacks:

    1. WS handlers     -> services.ws_handler_registry
    2. Event filter    -> services.event_waiter.FILTER_BUILDERS
    3. Trigger precheck-> services.event_waiter._TRIGGER_PRECHECKS
    4. Status refresh  -> services.status_broadcaster._SERVICE_REFRESH_CALLBACKS

Adding a new plugin folder follows the same shape -- consumers do not
need to learn its name.

External callers should depend on the public re-exports below; the
underscore-prefixed modules are implementation detail.
"""

from __future__ import annotations

from services.deployment.canary_registry import register_canary_trigger_type
from services.event_waiter import register_filter_builder, register_trigger_precheck
from services.node_output_schemas import register_output_schema
from services.status_broadcaster import register_service_refresh
from services.ws_handler_registry import register_ws_handlers

from ._credentials import TelegramCredential
from ._events import (  # noqa: F401 — re-exported for callers
    broadcast_telegram_status,
    dispatch_telegram_message_received,
)
from ._filters import build_telegram_filter
from ._handlers import WS_HANDLERS
from ._refresh import precheck_telegram_trigger, refresh_telegram_status
from ._service import TelegramService, get_telegram_service

# Telegram plugin classes -- importing them runs __init_subclass__ which
# slots them into the plugin/node registries (handled by services.plugin).
from .telegram_receive import TelegramReceiveNode, TelegramReceiveOutput
from .telegram_send import TelegramSendNode, TelegramSendOutput

# --- self-registration on import -------------------------------------------
register_ws_handlers(WS_HANDLERS)
register_filter_builder("telegramReceive", build_telegram_filter)
register_trigger_precheck("telegramReceive", precheck_telegram_trigger)
register_service_refresh(refresh_telegram_status)
register_output_schema("telegramReceive", TelegramReceiveOutput)
register_output_schema("telegramSend", TelegramSendOutput)

# Wave 12 C1 rollout #3: opt telegramReceive into the
# TriggerListenerWorkflow consumer path. Producer side:
# dispatch_telegram_message_received calls services.events.dispatch.emit
# unconditionally; emit() is gated by Settings.event_framework_enabled
# so the legacy path stays default. See
# services/deployment/canary_registry.py.
register_canary_trigger_type(TelegramReceiveNode.type)

__all__ = [
    "TelegramCredential",
    "TelegramService",
    "WS_HANDLERS",
    "build_telegram_filter",
    "get_telegram_service",
    "precheck_telegram_trigger",
    "refresh_telegram_status",
]
