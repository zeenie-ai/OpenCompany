"""Generalized event-source framework (Wave 12).

Three concrete EventSource subclasses cover all current MachinaOs
trigger integrations: ``PushEventSource`` (HTTP/RPC pushes),
``PollingEventSource`` (interval-based pull), ``DaemonEventSource``
(long-lived subprocess driver). Webhook flow is a thin specialisation
of PushEventSource via :class:`WebhookSource` + the path registry in
``server/routers/webhook.py``.

The unified payload type :class:`WorkflowEvent` mirrors CloudEvents
v1.0 verbatim — see ``envelope.py``. A back-compat shim in
``services.event_waiter`` auto-wraps legacy ``Dict`` dispatches so
existing plugins keep working untouched until they migrate.
"""

from __future__ import annotations

from .cli import run_cli_command
from .envelope import WorkflowEvent
from .lifecycle import make_lifecycle_handlers, make_status_refresh
from .source import EventSource
from .push import PushEventSource
from .polling import PollingEventSource
from .daemon import DaemonEventSource
from .triggers import BaseTriggerParams, WebhookTriggerNode
from .webhook import WebhookSource, WEBHOOK_SOURCES, register_webhook_source
from .verifiers import (
    WebhookVerifier,
    HmacVerifier,
    StripeVerifier,
    StandardWebhooksVerifier,
    GitHubVerifier,
)

# Wave 12 D3: publish the Visibility admin WS handlers into the central
# WS dispatcher on package import. Even though ``admin_handlers.py`` is
# framework code (not plugin code), routing it through the same
# ws_handler_registry keeps the router's dispatch surface uniform —
# the registry queries don't care whether the caller is plugin or
# framework.
from .admin_handlers import WS_HANDLERS as _ADMIN_WS_HANDLERS  # noqa: E402
from services.ws_handler_registry import register_ws_handlers as _register_ws_handlers  # noqa: E402

_register_ws_handlers(_ADMIN_WS_HANDLERS)

__all__ = [
    "BaseTriggerParams",
    "DaemonEventSource",
    "EventSource",
    "GitHubVerifier",
    "HmacVerifier",
    "PollingEventSource",
    "PushEventSource",
    "StandardWebhooksVerifier",
    "StripeVerifier",
    "WEBHOOK_SOURCES",
    "WebhookSource",
    "WebhookTriggerNode",
    "WebhookVerifier",
    "WorkflowEvent",
    "make_lifecycle_handlers",
    "make_status_refresh",
    "register_webhook_source",
    "run_cli_command",
]
