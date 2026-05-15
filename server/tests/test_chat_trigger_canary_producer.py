"""Wave 12 C1 rollout #1: chatTrigger producer dual-emit invariant.

Locks the contract: ``nodes.trigger.chat_trigger._events.dispatch_chat_message_received``
must route to BOTH the legacy in-process ``event_waiter.dispatch`` AND
the Temporal-durable ``services.events.dispatch.emit`` path. Without
the second call, a canary-enabled chatTrigger deployment would silently
miss all incoming chat messages — the TriggerListenerWorkflow has no
other event source.

Same regex-introspection invariant style as
``tests/test_credential_broadcasts.py`` — source-level assertions
catch the wire contract drifting without paying the cost of standing
up Temporal in CI.
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

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


class TestChatTriggerProducerDualEmit:
    """Producer wrapper emits BOTH legacy and Temporal envelopes."""

    def test_dispatcher_is_async(self):
        from nodes.trigger.chat_trigger._events import dispatch_chat_message_received

        # async coroutine functions return a coroutine when called.
        # Both `asyncio.iscoroutinefunction` and `inspect.iscoroutinefunction`
        # detect this.
        assert inspect.iscoroutinefunction(dispatch_chat_message_received), (
            "dispatch_chat_message_received must be async since Wave 12 C1 — "
            "it awaits services.events.dispatch.emit. Sync callers must "
            "switch to `await dispatch_chat_message_received(...)`."
        )

    def test_dispatcher_routes_both_legacy_and_temporal(self):
        from nodes.trigger.chat_trigger import _events

        src = inspect.getsource(_events.dispatch_chat_message_received)

        # Legacy event_waiter.dispatch path stays for back-compat.
        assert _EVENT_WAITER_DISPATCH_PATTERN.search(src), (
            "dispatch_chat_message_received must still call "
            "event_waiter.dispatch(_LEGACY_EVENT_TYPE, ...) for the "
            "in-process waiter path. Without it, canary-flag-off "
            "deployments lose their existing chat dispatch."
        )

        # Temporal-durable path via dispatch.emit.
        assert _EVENTS_EMIT_PATTERN.search(src), (
            "dispatch_chat_message_received must call "
            "services.events.dispatch.emit(envelope, ...) for the "
            "Temporal-durable canary path. Without it, the "
            "TriggerListenerWorkflow receives nothing when the "
            "event_framework_enabled flag is on."
        )

    @pytest.mark.asyncio
    async def test_runtime_dual_emit_calls_both_paths(self, monkeypatch):
        """Integration smoke: invoke the dispatcher and assert both
        downstream calls fire. event_framework_enabled=False so emit()
        no-ops on the Temporal side; the call still happens though."""
        from nodes.trigger.chat_trigger import _events
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

        result = await _events.dispatch_chat_message_received({
            "message": "hello",
            "session_id": "sess-1",
            "timestamp": "2026-05-14T00:00:00",
        })

        # Legacy returned the resolved count.
        assert result == 0

        assert len(legacy_calls) == 1
        assert legacy_calls[0]["event_type"] == "chat_message_received"
        assert legacy_calls[0]["data"]["message"] == "hello"

        assert len(emit_calls) == 1
        event = emit_calls[0]["event"]
        assert event.type == "com.machinaos.chat.message.received"
        assert event.subject == "sess-1"
        assert emit_calls[0]["wire_routing_key"] == "chat_message_received"
