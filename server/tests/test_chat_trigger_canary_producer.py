"""chatTrigger producer canary-emit invariant.

Locks the contract: ``nodes.trigger.chat_trigger._events.dispatch_chat_message_received``
routes through the canary CloudEvents path
(:func:`services.events.dispatch.emit`) ONLY. The legacy
``event_waiter.dispatch`` path was removed in Wave 13 — chatTrigger is
canary-registered, the deployment manager skips ``setup_event_trigger``,
and the legacy collector has zero consumers in production.

Same regex-introspection invariant style as
``tests/test_credential_broadcasts.py`` — source-level assertions catch
the wire contract drifting without paying the cost of standing up
Temporal in CI.
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from typing import Any, List
from unittest.mock import MagicMock

import pytest

# Stub `machina` namespace.
if "machina" not in sys.modules:
    _machina = types.ModuleType("cli")
    _machina.__path__ = []
    sys.modules["cli"] = _machina
    _machina_tcp = types.ModuleType("cli.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _machina_tcp


_EVENT_WAITER_DISPATCH_PATTERN = re.compile(r"event_waiter\.dispatch\s*\(")
_EVENTS_EMIT_PATTERN = re.compile(r"\bemit\s*\(")


class TestChatTriggerProducerCanaryEmit:
    """Producer wrapper emits via the canary CloudEvents path only."""

    def test_dispatcher_is_async(self):
        from nodes.trigger.chat_trigger._events import dispatch_chat_message_received

        assert inspect.iscoroutinefunction(dispatch_chat_message_received), (
            "dispatch_chat_message_received must be async — it awaits " "services.events.dispatch.emit."
        )

    def test_dispatcher_uses_canary_path_only(self):
        from nodes.trigger.chat_trigger import _events

        src = inspect.getsource(_events.dispatch_chat_message_received)

        assert _EVENTS_EMIT_PATTERN.search(src), (
            "dispatch_chat_message_received must call "
            "services.events.dispatch.emit(envelope, ...) — the canary "
            "CloudEvents path Signals running TriggerListenerWorkflow "
            "consumers AND broadcasts to FE on the chat_message_received "
            "wire key."
        )
        assert not _EVENT_WAITER_DISPATCH_PATTERN.search(src), (
            "dispatch_chat_message_received must NOT call "
            "event_waiter.dispatch — chatTrigger is canary-registered, "
            "the legacy collector path has zero consumers, and that "
            "call was removed in Wave 13. Reintroducing it would "
            "double-dispatch through dead infrastructure."
        )

    @pytest.mark.asyncio
    async def test_runtime_emits_canary_envelope(self, monkeypatch):
        """Invoking the dispatcher calls dispatch.emit with the right
        envelope. The legacy event_waiter is not touched."""
        from nodes.trigger.chat_trigger import _events
        from services.events import dispatch as dispatch_mod

        emit_calls: List[Any] = []

        async def fake_emit(event, **kwargs):
            emit_calls.append({"event": event, **kwargs})
            return event

        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        result = await _events.dispatch_chat_message_received(
            {
                "message": "hello",
                "session_id": "sess-1",
                "timestamp": "2026-05-14T00:00:00",
            }
        )

        # No return value — canary-only emit doesn't carry a waiter count.
        assert result is None

        assert len(emit_calls) == 1
        event = emit_calls[0]["event"]
        assert event.type == "com.machinaos.chat.message.received"
        assert event.subject == "sess-1"
        assert emit_calls[0]["wire_routing_key"] == "chat_message_received"
