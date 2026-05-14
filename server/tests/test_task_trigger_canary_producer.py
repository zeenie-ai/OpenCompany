"""Wave 12 C1 rollout #2: taskTrigger producer dual-emit invariant.

Locks the contract: ``nodes.agent._events._broadcast_task_event`` must
route delegated-task completion to BOTH the legacy
``broadcaster.send_custom_event`` (which dispatches via event_waiter)
AND the Temporal-durable ``services.events.dispatch.emit`` path. Without
the second call, canary-enabled taskTrigger consumers miss every
child-agent completion.

Cross-cutting factory: this rollout uses ``WorkflowEvent.task_completed``
which stays central per RFC §6.4 (parametrised by task_id + agent +
status — uniformly applies across every delegated agent type). The
broadcaster wrappers around it live in the plugin folder.
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
    _machina = types.ModuleType("machina")
    _machina.__path__ = []
    sys.modules["machina"] = _machina
    _machina_tcp = types.ModuleType("machina.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["machina.tcp"] = _machina_tcp


_SEND_CUSTOM_EVENT_PATTERN = re.compile(r"send_custom_event\s*\(")
_EVENTS_EMIT_PATTERN = re.compile(r"\bemit\s*\(")


class TestTaskTriggerProducerDualEmit:
    """Producer wrapper emits BOTH legacy and Temporal envelopes."""

    def test_broadcast_helper_is_async(self):
        from nodes.agent._events import _broadcast_task_event

        assert inspect.iscoroutinefunction(_broadcast_task_event)

    def test_helper_routes_both_legacy_and_temporal(self):
        from nodes.agent import _events

        src = inspect.getsource(_events._broadcast_task_event)

        assert _SEND_CUSTOM_EVENT_PATTERN.search(src), (
            "_broadcast_task_event must still call "
            "broadcaster.send_custom_event(_LEGACY_EVENT_TYPE, payload) "
            "so canary-flag-off deployments keep their existing dispatch."
        )
        assert _EVENTS_EMIT_PATTERN.search(src), (
            "_broadcast_task_event must call "
            "services.events.dispatch.emit(envelope, ...) for the "
            "Temporal-durable canary path. Without it, taskTrigger "
            "TriggerListenerWorkflows receive nothing when the "
            "event_framework_enabled flag is on."
        )

    @pytest.mark.asyncio
    async def test_completed_event_routes_both_paths(self, monkeypatch):
        from nodes.agent import _events
        from services.events import dispatch as dispatch_mod

        custom_calls: List[Dict[str, Any]] = []
        emit_calls: List[Any] = []

        broadcaster = MagicMock()

        async def fake_send_custom_event(event_type, payload):
            custom_calls.append({"event_type": event_type, "payload": payload})

        broadcaster.send_custom_event = fake_send_custom_event

        async def fake_emit(event, **kwargs):
            emit_calls.append({"event": event, **kwargs})
            return event

        # Patch the get_status_broadcaster singleton.
        from services import status_broadcaster as sb
        monkeypatch.setattr(sb, "get_status_broadcaster", lambda: broadcaster)
        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        await _events.broadcast_agent_task_completed(
            task_id="task-1",
            agent_name="coding_agent",
            agent_node_id="agent-1",
            parent_node_id="parent-1",
            workflow_id="wf-1",
            result="done",
        )

        # Legacy path: wire key = task_completed, payload carries
        # the existing fields the taskTrigger filter reads.
        assert len(custom_calls) == 1
        assert custom_calls[0]["event_type"] == "task_completed"
        legacy_payload = custom_calls[0]["payload"]
        assert legacy_payload["task_id"] == "task-1"
        assert legacy_payload["status"] == "completed"
        assert legacy_payload["result"] == "done"

        # Temporal path: envelope.type discriminates on succeeded vs
        # failed; subject is the task_id; wire_routing_key preserved.
        assert len(emit_calls) == 1
        envelope = emit_calls[0]["event"]
        assert envelope.type == "com.machinaos.agent.task.succeeded"
        assert envelope.subject == "task-1"
        assert emit_calls[0]["wire_routing_key"] == "task_completed"

    @pytest.mark.asyncio
    async def test_failed_event_routes_both_paths_with_error_type(self, monkeypatch):
        """Type discriminates between succeeded/failed; subject still task_id."""
        from nodes.agent import _events
        from services.events import dispatch as dispatch_mod

        emit_calls: List[Any] = []

        broadcaster = MagicMock()
        broadcaster.send_custom_event = AsyncMock()

        async def fake_emit(event, **kwargs):
            emit_calls.append(event)
            return event

        from services import status_broadcaster as sb
        monkeypatch.setattr(sb, "get_status_broadcaster", lambda: broadcaster)
        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        await _events.broadcast_agent_task_failed(
            task_id="task-2",
            agent_name="web_agent",
            agent_node_id="agent-2",
            parent_node_id="parent-1",
            workflow_id="wf-1",
            error="timeout",
        )

        assert len(emit_calls) == 1
        envelope = emit_calls[0]
        # Status='error' maps to the .failed type variant per the
        # central factory's discrimination.
        assert envelope.type == "com.machinaos.agent.task.failed"
        assert envelope.subject == "task-2"
