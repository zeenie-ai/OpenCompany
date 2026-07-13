"""Wave 12 C1 rollout #4: whatsappReceive producer dual-emit invariant.

Locks the contract: ``nodes.whatsapp._events.broadcast_whatsapp_message``
must fan out incoming (``direction="received"``) messages to the
Temporal-durable ``services.events.dispatch.emit`` path so canary
listeners can consume them. Outbound (``direction="sent"``) messages
are pure observation — no trigger node consumes them, so the
``dispatch.emit`` call is gated on direction == "received" inside
the broadcaster.

Same regex-introspection + runtime smoke pattern used for
webhookTrigger / chatTrigger / taskTrigger / telegramReceive
producers in this series.
"""

from __future__ import annotations

import inspect
import re
import sys
import types
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest


if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


_EVENTS_EMIT_PATTERN = re.compile(r"\bemit\s*\(")
_DIRECTION_GUARD_PATTERN = re.compile(r"direction\s*==\s*[\"']received[\"']")


class TestWhatsappProducerDualEmit:
    """Producer wrapper fans out received messages to the canary path."""

    def test_broadcaster_is_async(self):
        from nodes.whatsapp._events import broadcast_whatsapp_message

        assert inspect.iscoroutinefunction(broadcast_whatsapp_message)

    def test_broadcaster_calls_dispatch_emit_for_received(self):
        from nodes.whatsapp import _events

        src = inspect.getsource(_events.broadcast_whatsapp_message)

        assert _EVENTS_EMIT_PATTERN.search(src), (
            "broadcast_whatsapp_message must call "
            "services.events.dispatch.emit(...) so canary "
            "TriggerListenerWorkflow consumers receive incoming "
            "WhatsApp messages."
        )

        assert _DIRECTION_GUARD_PATTERN.search(src), (
            "broadcast_whatsapp_message must gate the dispatch.emit "
            "call on direction == 'received' — outbound 'sent' events "
            "are pure observation and have no trigger consumers."
        )

    @pytest.mark.asyncio
    async def test_received_message_fires_dispatch_emit(self, monkeypatch):
        from nodes.whatsapp import _events
        from services.events import dispatch as dispatch_mod
        from services import status_broadcaster as sb

        emit_calls: List[Dict[str, Any]] = []

        async def fake_emit(event, **kwargs):
            emit_calls.append({"event": event, **kwargs})
            return event

        broadcaster = MagicMock()
        broadcaster.broadcast = AsyncMock()

        monkeypatch.setattr(sb, "get_status_broadcaster", lambda: broadcaster)
        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        await _events.broadcast_whatsapp_message(
            "received",
            {
                "chat_id": "123@s.whatsapp.net",
                "text": "hello",
                "message_id": "abc",
                "from": "+1234567890",
            },
        )

        # Legacy raw frame still goes out so the existing FE message-list
        # handler at WebSocketContext.tsx (which reads ``data.*``)
        # keeps working until the Wave 12 D4 follow-up migrates it to
        # envelope-shape. The duplicate typed-envelope WS broadcast was
        # dropped in Wave 13 — dispatch.emit broadcasts the envelope on
        # the same wire key once.
        assert broadcaster.broadcast.await_count == 1

        # And the canary path fires exactly once.
        assert len(emit_calls) == 1
        envelope = emit_calls[0]["event"]
        assert envelope.type == "com.opencompany.whatsapp.message.received"
        assert envelope.subject == "123@s.whatsapp.net"
        assert emit_calls[0]["wire_routing_key"] == "whatsapp_message_received"

    @pytest.mark.asyncio
    async def test_sent_message_does_not_fire_dispatch_emit(self, monkeypatch):
        """Outbound message — observation only, no trigger consumer.

        Skipping the canary fan-out here keeps the Visibility query +
        signal load proportional to actual trigger demand. Without the
        guard, every outbound message would query Temporal Visibility
        with zero matches every time.
        """
        from nodes.whatsapp import _events
        from services.events import dispatch as dispatch_mod
        from services import status_broadcaster as sb

        emit_calls: List[Any] = []

        async def fake_emit(event, **kwargs):
            emit_calls.append(event)
            return event

        broadcaster = MagicMock()
        broadcaster.broadcast = AsyncMock()

        monkeypatch.setattr(sb, "get_status_broadcaster", lambda: broadcaster)
        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        await _events.broadcast_whatsapp_message(
            "sent",
            {"chat_id": "123@s.whatsapp.net", "text": "hi"},
        )

        # Single legacy raw broadcast for the FE message-list
        # observation channel — duplicate typed-envelope sibling dropped
        # in Wave 13.
        assert broadcaster.broadcast.await_count == 1

        # No canary fan-out (no trigger consumes outbound).
        assert emit_calls == []


class TestWhatsappPluginSelfRegistersCanary:
    """Importing the whatsapp plugin opts whatsappReceive into the canary."""

    def test_plugin_import_registers_whatsapp_receive(self):
        from services.deployment import canary_registry

        try:
            __import__("nodes.whatsapp")
        except ImportError as exc:  # pragma: no cover
            pytest.xfail(f"nodes.whatsapp not importable: {exc}")

        assert canary_registry.is_canary_trigger_type("whatsappReceive"), (
            "Importing nodes.whatsapp should call "
            "register_canary_trigger_type('whatsappReceive') — see "
            "the __init__.py bottom section."
        )
