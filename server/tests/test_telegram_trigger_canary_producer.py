"""Wave 12 C1 rollout #3: telegramReceive producer dual-emit invariant.

Locks the contract: ``nodes.telegram._events.dispatch_telegram_message_received``
must route incoming Telegram messages to BOTH the legacy
``event_waiter.dispatch`` and the Temporal-durable
``services.events.dispatch.emit`` path. Without the second call, a
canary-enabled telegramReceive deployment silently misses every
incoming message — the TriggerListenerWorkflow has no other event
source.

Same regex-introspection + runtime smoke pattern used for the
webhookTrigger / chatTrigger / taskTrigger producers in this series.
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest


if "machina" not in sys.modules:
    _machina = types.ModuleType("cli")
    _machina.__path__ = []
    sys.modules["cli"] = _machina
    _machina_tcp = types.ModuleType("cli.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _machina_tcp


_EVENT_WAITER_DISPATCH_PATTERN = re.compile(r"event_waiter\.dispatch\s*\(")
_EVENTS_EMIT_PATTERN = re.compile(r"\bemit\s*\(")


class TestTelegramProducerDualEmit:
    """Producer wrapper emits BOTH legacy and Temporal envelopes."""

    def test_dispatcher_is_async(self):
        from nodes.telegram._events import dispatch_telegram_message_received

        assert inspect.iscoroutinefunction(dispatch_telegram_message_received)

    def test_dispatcher_routes_both_legacy_and_temporal(self):
        from nodes.telegram import _events

        src = inspect.getsource(_events.dispatch_telegram_message_received)

        assert _EVENT_WAITER_DISPATCH_PATTERN.search(src), (
            "dispatch_telegram_message_received must still call "
            "event_waiter.dispatch(_MESSAGE_LEGACY_EVENT_TYPE, ...) for "
            "the in-process waiter path. Without it, canary-flag-off "
            "deployments lose their existing telegram dispatch."
        )
        assert _EVENTS_EMIT_PATTERN.search(src), (
            "dispatch_telegram_message_received must call "
            "services.events.dispatch.emit(envelope, ...) for the "
            "Temporal-durable canary path. Without it, telegramReceive "
            "TriggerListenerWorkflows receive nothing when the "
            "event_framework_enabled flag is on."
        )

    @pytest.mark.asyncio
    async def test_runtime_dual_emit_calls_both_paths(self, monkeypatch):
        from nodes.telegram import _events
        from services import event_waiter
        from services.events import dispatch as dispatch_mod

        legacy_calls: List[Dict[str, Any]] = []
        emit_calls: List[Any] = []

        def fake_dispatch(event_type, data):
            legacy_calls.append({"event_type": event_type, "data": data})
            return 0

        async def fake_emit(event, **kwargs):
            emit_calls.append({"event": event, **kwargs})
            return event

        monkeypatch.setattr(event_waiter, "dispatch", fake_dispatch)
        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        result = await _events.dispatch_telegram_message_received({
            "chat_id": 12345,
            "text": "hello bot",
            "from_id": 999,
            "timestamp": "2026-05-14T00:00:00",
        })

        assert result == 0

        assert len(legacy_calls) == 1
        assert legacy_calls[0]["event_type"] == "telegram_message_received"
        assert legacy_calls[0]["data"]["chat_id"] == 12345

        assert len(emit_calls) == 1
        envelope = emit_calls[0]["event"]
        assert envelope.type == "com.machinaos.telegram.message.received"
        # subject is the chat_id cast to str — CloudEvents spec requires string.
        assert envelope.subject == "12345"
        assert emit_calls[0]["wire_routing_key"] == "telegram_message_received"


class TestTelegramPluginSelfRegistersCanary:
    """Importing the telegram plugin opts telegramReceive into the canary."""

    def test_plugin_import_registers_telegram_receive(self):
        from services.deployment import canary_registry

        try:
            __import__("nodes.telegram")
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"nodes.telegram not importable: {exc}")

        assert canary_registry.is_canary_trigger_type("telegramReceive"), (
            "Importing nodes.telegram should call "
            "register_canary_trigger_type('telegramReceive') — see "
            "the __init__.py bottom section."
        )
