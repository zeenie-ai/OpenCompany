"""Unit tests for the four ``claude.session.*`` CloudEvent factories
on :class:`services.events.envelope.WorkflowEvent`.

Locks the envelope contract — source URI, type prefix, subject,
workflow_id extension attribute, and data shape — so frontend
consumers + future plugin authors don't get surprise drift.
"""

from __future__ import annotations

from services.events.envelope import WorkflowEvent


class TestClaudeSessionSpawned:
    def test_envelope_shape(self):
        event = WorkflowEvent.claude_session_spawned(
            memory_node_id="mem-1",
            session_uuid="abc-123",
            pid=12345,
            workflow_id="wf-99",
        )
        assert event.source == "machinaos://services/cli_agent"
        assert event.type == "com.machinaos.claude.session.spawned"
        assert event.subject == "mem-1"
        assert event.workflow_id == "wf-99"
        assert event.data == {
            "memory_node_id": "mem-1",
            "session_uuid": "abc-123",
            "pid": 12345,
        }

    def test_workflow_id_optional(self):
        event = WorkflowEvent.claude_session_spawned(
            memory_node_id="mem-1",
            session_uuid="abc",
            pid=1,
        )
        assert event.workflow_id is None

    def test_matches_type_glob(self):
        event = WorkflowEvent.claude_session_spawned(
            memory_node_id="m",
            session_uuid="u",
            pid=1,
        )
        assert event.matches_type("claude.session.*")
        assert event.matches_type("claude.session.spawned")
        assert not event.matches_type("claude.session.cleared")


class TestClaudeSessionCleared:
    def test_envelope_shape(self):
        event = WorkflowEvent.claude_session_cleared(
            memory_node_id="mem-1",
            old_session_uuid="uuid-A",
            new_session_uuid="uuid-B",
            workflow_id="wf-99",
        )
        assert event.type == "com.machinaos.claude.session.cleared"
        assert event.subject == "mem-1"
        assert event.data["old_session_uuid"] == "uuid-A"
        assert event.data["new_session_uuid"] == "uuid-B"


class TestClaudeSessionTerminated:
    def test_envelope_shape(self):
        event = WorkflowEvent.claude_session_terminated(
            memory_node_id="mem-1",
            reason="idle",
            session_uuid="uuid-A",
        )
        assert event.type == "com.machinaos.claude.session.terminated"
        assert event.subject == "mem-1"
        assert event.data["reason"] == "idle"
        assert event.data["session_uuid"] == "uuid-A"

    def test_session_uuid_optional(self):
        event = WorkflowEvent.claude_session_terminated(
            memory_node_id="mem-1",
            reason="shutdown",
        )
        # When session_uuid is None, the key is omitted from data.
        assert "session_uuid" not in event.data
        assert event.data["reason"] == "shutdown"


class TestClaudeSessionUsage:
    def test_envelope_shape(self):
        event = WorkflowEvent.claude_session_usage(
            memory_node_id="mem-1",
            session_uuid="uuid-A",
            total_cost_usd=0.0123,
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=200,
            cache_creation_input_tokens=10,
            duration_ms=4567,
            num_turns=3,
            workflow_id="wf-99",
        )
        assert event.type == "com.machinaos.claude.session.usage"
        assert event.subject == "mem-1"
        assert event.workflow_id == "wf-99"
        assert event.data["session_uuid"] == "uuid-A"
        assert event.data["total_cost_usd"] == 0.0123
        assert event.data["usage"] == {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 200,
            "cache_creation_input_tokens": 10,
        }
        assert event.data["duration_ms"] == 4567
        assert event.data["num_turns"] == 3

    def test_optional_fields_omitted_when_none(self):
        event = WorkflowEvent.claude_session_usage(
            memory_node_id="mem-1",
            session_uuid="uuid-A",
        )
        assert event.data["total_cost_usd"] is None
        # duration_ms / num_turns omitted (not present in data) when None.
        assert "duration_ms" not in event.data
        assert "num_turns" not in event.data
        # Usage block always emitted (zeros), so the FE doesn't need a
        # null check.
        assert event.data["usage"]["input_tokens"] == 0

    def test_zero_costs_and_tokens_acceptable(self):
        event = WorkflowEvent.claude_session_usage(
            memory_node_id="mem-1",
            session_uuid="uuid-A",
            total_cost_usd=0.0,
        )
        assert event.data["total_cost_usd"] == 0.0


class TestCloudEventInvariants:
    """Cross-cutting checks the four events all satisfy."""

    EVENTS = [
        (
            "spawned",
            lambda: WorkflowEvent.claude_session_spawned(
                memory_node_id="m",
                session_uuid="u",
                pid=1,
            ),
        ),
        (
            "cleared",
            lambda: WorkflowEvent.claude_session_cleared(
                memory_node_id="m",
                old_session_uuid="a",
                new_session_uuid="b",
            ),
        ),
        (
            "terminated",
            lambda: WorkflowEvent.claude_session_terminated(
                memory_node_id="m",
                reason="idle",
            ),
        ),
        (
            "usage",
            lambda: WorkflowEvent.claude_session_usage(
                memory_node_id="m",
                session_uuid="u",
            ),
        ),
    ]

    def test_all_share_source_uri(self):
        for name, builder in self.EVENTS:
            event = builder()
            assert event.source == "machinaos://services/cli_agent", name

    def test_all_share_subject_convention(self):
        # subject = memory_node_id, drives FE per-memory-node routing.
        for name, builder in self.EVENTS:
            event = builder()
            assert event.subject == "m", name

    def test_all_dataschema_auto_populated(self):
        for name, builder in self.EVENTS:
            event = builder()
            assert event.dataschema is not None
            assert event.dataschema.startswith("machinaos://schemas/events/claude.session."), name
            assert event.dataschema.endswith(".json"), name

    def test_all_carry_specversion_1(self):
        for name, builder in self.EVENTS:
            event = builder()
            assert event.specversion == "1.0", name

    def test_all_have_unique_id(self):
        ids = set()
        for _, builder in self.EVENTS:
            for _ in range(10):
                ids.add(builder().id)
        assert len(ids) == 40  # all distinct
