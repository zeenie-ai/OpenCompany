"""telegramReceive producer canary-emit invariant.

Locks the contract: ``nodes.telegram._events.dispatch_telegram_message_received``
routes incoming Telegram messages through the canary CloudEvents path
(:func:`services.events.dispatch.emit`) ONLY. The legacy
``event_waiter.dispatch`` path was removed in Wave 13 — telegramReceive
is canary-registered, the deployment manager skips
``setup_event_trigger``, and the legacy collector has zero consumers.

Same regex-introspection + runtime smoke pattern used for the
webhookTrigger / chatTrigger / taskTrigger producers in this series.
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from typing import Any, List
from unittest.mock import MagicMock

import pytest


if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


_EVENT_WAITER_DISPATCH_PATTERN = re.compile(r"event_waiter\.dispatch\s*\(")
_EVENTS_EMIT_PATTERN = re.compile(r"\bemit\s*\(")


class TestTelegramProducerCanaryEmit:
    """Producer wrapper emits via the canary CloudEvents path only."""

    def test_redelivery_keeps_the_message_id_scoped_to_its_chat(self):
        from nodes.telegram._events import telegram_message_received

        original = telegram_message_received({"chat_id": 123, "message_id": 7, "text": "hello"})
        redelivery = telegram_message_received({"chat_id": 123, "message_id": 7, "text": "hello", "timestamp": "later"})
        another_chat = telegram_message_received({"chat_id": 456, "message_id": 7, "text": "hello"})
        another_message = telegram_message_received({"chat_id": 123, "message_id": 8, "text": "hello"})

        assert original.id == redelivery.id
        assert len({original.id, another_chat.id, another_message.id}) == 3

    @pytest.mark.parametrize("payload", [{"chat_id": 123}, {"message_id": 7}, {}])
    def test_missing_provider_identity_does_not_deduplicate_unrelated_messages(self, payload):
        from nodes.telegram._events import telegram_message_received

        assert telegram_message_received(payload).id != telegram_message_received(payload).id

    def test_dispatcher_is_async(self):
        from nodes.telegram._events import dispatch_telegram_message_received

        assert inspect.iscoroutinefunction(dispatch_telegram_message_received)

    def test_dispatcher_uses_canary_path_only(self):
        from nodes.telegram import _events

        src = inspect.getsource(_events.dispatch_telegram_message_received)

        assert _EVENTS_EMIT_PATTERN.search(src), (
            "dispatch_telegram_message_received must call "
            "services.events.dispatch.emit(envelope, ...) — the canary "
            "CloudEvents path Signals running TriggerListenerWorkflow "
            "consumers AND broadcasts to FE on the "
            "telegram_message_received wire key."
        )
        assert not _EVENT_WAITER_DISPATCH_PATTERN.search(src), (
            "dispatch_telegram_message_received must NOT call "
            "event_waiter.dispatch — telegramReceive is canary-registered "
            "and the legacy collector path has zero consumers (removed "
            "in Wave 13)."
        )

    @pytest.mark.asyncio
    async def test_runtime_emits_canary_envelope(self, monkeypatch):
        from nodes.telegram import _events
        from services.events import dispatch as dispatch_mod

        emit_calls: List[Any] = []

        async def fake_emit(event, **kwargs):
            emit_calls.append({"event": event, **kwargs})
            return event

        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        result = await _events.dispatch_telegram_message_received(
            {
                "chat_id": 12345,
                "text": "hello bot",
                "from_id": 999,
                "timestamp": "2026-05-14T00:00:00",
            }
        )

        assert result is None

        assert len(emit_calls) == 1
        envelope = emit_calls[0]["event"]
        assert envelope.type == "com.opencompany.telegram.message.received"
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
            "register_canary_trigger_type('telegramReceive', ...) — see "
            "the __init__.py bottom section."
        )
