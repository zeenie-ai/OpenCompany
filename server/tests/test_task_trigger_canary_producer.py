"""taskTrigger producer canary-emit invariant.

Locks the contract: ``nodes.agent._events._broadcast_task_event`` routes
delegated-task completion through the canary CloudEvents path
(:func:`services.events.dispatch.emit`) ONLY. The legacy
``broadcaster.send_custom_event`` path (which internally dispatched to
``event_waiter``) was removed in Wave 13 — taskTrigger is canary-registered,
the deployment manager skips ``setup_event_trigger``, and the legacy
collector has zero consumers.

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


_SEND_CUSTOM_EVENT_PATTERN = re.compile(r"send_custom_event\s*\(")
_EVENTS_EMIT_PATTERN = re.compile(r"\bemit\s*\(")


class TestTaskTriggerProducerCanaryEmit:
    """Producer wrapper emits via the canary CloudEvents path only."""

    def test_broadcast_helper_is_async(self):
        from nodes.agent._events import _broadcast_task_event

        assert inspect.iscoroutinefunction(_broadcast_task_event)

    def test_helper_uses_canary_path_only(self):
        from nodes.agent import _events

        src = inspect.getsource(_events._broadcast_task_event)

        assert _EVENTS_EMIT_PATTERN.search(src), (
            "_broadcast_task_event must call "
            "services.events.dispatch.emit(envelope, ...) — the canary "
            "CloudEvents path Signals running TriggerListenerWorkflow "
            "consumers AND broadcasts to FE on the task_completed wire key."
        )
        assert not _SEND_CUSTOM_EVENT_PATTERN.search(src), (
            "_broadcast_task_event must NOT call "
            "broadcaster.send_custom_event — taskTrigger is canary-registered "
            "and the legacy event_waiter dispatch path (which "
            "send_custom_event drives internally) has zero consumers "
            "(removed in Wave 13)."
        )

    @pytest.mark.asyncio
    async def test_completed_event_emits_canary_envelope(self, monkeypatch):
        from nodes.agent import _events
        from services.events import dispatch as dispatch_mod

        emit_calls: List[Any] = []

        async def fake_emit(event, **kwargs):
            emit_calls.append({"event": event, **kwargs})
            return event

        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        await _events.broadcast_agent_task_completed(
            task_id="task-1",
            agent_name="coding_agent",
            agent_node_id="agent-1",
            parent_node_id="parent-1",
            workflow_id="wf-1",
            result="done",
        )

        # Single type (``.completed``) so the listener's EventType SA
        # matches via dispatch.emit's Visibility query. Status
        # discrimination lives in data.status.
        assert len(emit_calls) == 1
        envelope = emit_calls[0]["event"]
        assert envelope.type == "com.opencompany.agent.task.completed"
        assert envelope.subject == "task-1"
        assert envelope.data["status"] == "completed"
        assert envelope.data["result"] == "done"
        assert emit_calls[0]["wire_routing_key"] == "task_completed"

    @pytest.mark.asyncio
    async def test_failed_event_shares_type_with_status_discriminator(self, monkeypatch):
        """Success and failure share a single CloudEvents type so the
        listener's EventType Search Attribute matches both branches;
        ``status='error'`` lives in the payload."""
        from nodes.agent import _events
        from services.events import dispatch as dispatch_mod

        emit_calls: List[Any] = []

        async def fake_emit(event, **kwargs):
            emit_calls.append(event)
            return event

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
        assert envelope.type == "com.opencompany.agent.task.completed"
        assert envelope.subject == "task-2"
        assert envelope.data["status"] == "error"
        assert envelope.data["error"] == "timeout"

    @pytest.mark.asyncio
    async def test_lifecycle_identity_and_trace_fields_are_preserved(self, monkeypatch):
        """Activity retries reuse one event id and retain delegation tracing."""
        from nodes.agent import _events
        from services.events import dispatch as dispatch_mod

        emitted: List[Any] = []

        async def fake_emit(event, **kwargs):
            emitted.append(event)
            return event

        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)
        await _events.broadcast_agent_task_completed(
            task_id="task-3",
            agent_name="coding agent",
            agent_node_id="agent-3",
            parent_node_id="lead-1",
            workflow_id="canvas-1",
            result="review me",
            event_id="task-3:submitted",
            lifecycle_data={
                "team_id": "team-1",
                "root_execution_id": "root-1",
                "trace_id": "call-1",
            },
        )

        envelope = emitted[0]
        assert envelope.id == "task-3:submitted"
        assert envelope.data["team_id"] == "team-1"
        assert envelope.data["root_execution_id"] == "root-1"
        assert envelope.data["trace_id"] == "call-1"

    def test_temporal_completion_emits_after_authoritative_state_read(self):
        """Temporal parity: retries/requeues are decided before taskTrigger."""
        from services.temporal import agent_activities

        source = inspect.getsource(agent_activities.finish_agent_delegation)
        reread = source.index("# Re-read the authoritative state")
        broadcast = source.index("broadcast_agent_task_completed")
        assert reread < broadcast
        assert 'target_status = "requeued" if is_requeued' in source
        assert "if succeeded or not is_requeued" in source
        assert 'result.get("response", result.get("result", result))' in source
        assert '"execution_id": task.get("execution_id")' in source

    def test_task_trigger_context_requires_durable_lead_review(self):
        from services.plugin.edge_walker import extract_task_event_payload, format_task_context

        completed = format_task_context({"status": "completed", "result": "ok"})
        failed = format_task_context({"status": "error", "error": "boom"})
        assert "lead's completion review" in completed
        assert "list_tasks and get_task" in completed
        assert "Do not create a duplicate assignment" in completed
        assert "lead's failure review" in failed
        assert "retrying, or reassigning" in failed
        nested = {"specversion": "1.0", "data": {"result": {
            "task_id": "task-1", "status": "completed", "result": "saved",
        }}}
        assert extract_task_event_payload(nested) == {
            "task_id": "task-1", "status": "completed", "result": "saved",
        }
