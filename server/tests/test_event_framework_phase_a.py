"""Wave 12 Phase A: smoke tests for the event-framework foundation.

Covers (with no live Temporal cluster required):

A1: ``TestStartToCloseTimeoutOverridesAreCommented`` — lives in
    ``test_plugin_contract.py``; not duplicated here.

A2: ``NodeUserError`` is in :class:`RetryPolicy`'s default
    ``non_retryable_error_types``.

A3: ``Settings.temporal_graceful_shutdown_seconds`` is exposed,
    configurable, and consumed by :func:`worker._graceful_shutdown_timeout`.

A4: :data:`EVENT_SEARCH_ATTRIBUTES` is a non-empty structured spec list;
    every entry has a name + indexed_type + description; the
    :func:`attribute_names` helper agrees with the structure.

A6: :func:`services.events.dispatch.emit` is a no-op pass-through when
    ``Settings.event_framework_enabled=False`` (Phase-A default).

A7: :class:`MachinaWorkflow` initializes its event state in ``__init__``;
    the ``on_event`` signal handler dedups by ``event.id`` and queues
    matching events; :meth:`_has_event_matching` implements the
    ``wait_condition`` predicate contract.

A8: :func:`emit_event_activity` validates the envelope payload and
    forwards to :func:`dispatch.emit`.
"""

from __future__ import annotations

import sys
import types
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

# Stub the `machina` package (lives at project root, outside server/) so
# `services.temporal.__init__` → `_runtime.py` → `from machina.tcp import
# probe_tcp_port` doesn't crash collection. Conftest stubs core.* but
# not machina.*.
if "machina" not in sys.modules:
    _machina = types.ModuleType("machina")
    _machina.__path__ = []
    sys.modules["machina"] = _machina
    _machina_tcp = types.ModuleType("machina.tcp")
    _machina_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["machina.tcp"] = _machina_tcp

from core.config import Settings
from services.events.envelope import WorkflowEvent
from services.plugin.scaling import RetryPolicy


class TestA2NodeUserErrorNonRetryable:
    """A2: ``NodeUserError`` ships in the default ``non_retryable_error_types``
    so a single ``cls.retry_policy = DEFAULT_RETRY`` picks it up."""

    def test_node_user_error_in_default_non_retryable(self):
        policy = RetryPolicy()
        assert "NodeUserError" in policy.non_retryable_error_types, (
            "RetryPolicy.non_retryable_error_types default must contain "
            "'NodeUserError' so plugin-raised user errors fail fast in "
            "Temporal activities."
        )

    def test_to_temporal_preserves_non_retryable_list(self):
        policy = RetryPolicy()
        # Import-on-call to keep the test independent of Temporal SDK
        # availability at module-import time.
        try:
            temporal_policy = policy.to_temporal()
        except ImportError:  # pragma: no cover
            pytest.skip("temporalio SDK not installed in test env")
        assert "NodeUserError" in temporal_policy.non_retryable_error_types


class TestA3GracefulShutdownTimeout:
    """A3: SIGTERM grace window is config-driven, not hardcoded.

    ``conftest.py`` mocks ``core.config.Settings`` as ``MagicMock`` so
    handler tests don't pay Pydantic startup cost. We patch a real
    ``Settings``-like stub inside this class to verify the contract.
    """

    def test_worker_helper_returns_timedelta(self, monkeypatch):
        from services.temporal import worker as worker_mod

        class _FakeSettings:
            temporal_graceful_shutdown_seconds = 17

        # Patch the lazy import inside worker._graceful_shutdown_timeout.
        # It does `from core.config import Settings`; patch that module
        # entry to return our fake.
        import core.config

        monkeypatch.setattr(core.config, "Settings", lambda: _FakeSettings())

        td = worker_mod._graceful_shutdown_timeout()
        assert isinstance(td, timedelta)
        assert td.total_seconds() == 17


class TestA4SearchAttributesSpec:
    """A4: Search Attributes are declared structurally, NOT scattered
    as literals through the dispatch + admin code."""

    def test_spec_is_non_empty(self):
        from services.temporal.search_attributes import EVENT_SEARCH_ATTRIBUTES

        assert len(EVENT_SEARCH_ATTRIBUTES) >= 6

    def test_each_spec_complete(self):
        from services.temporal.search_attributes import EVENT_SEARCH_ATTRIBUTES

        for spec in EVENT_SEARCH_ATTRIBUTES:
            assert spec.name, "every search-attribute spec needs a name"
            assert spec.indexed_type, f"{spec.name} missing indexed_type"
            assert spec.description, f"{spec.name} missing description"

    def test_attribute_names_helper_matches_spec(self):
        from services.temporal.search_attributes import (
            EVENT_SEARCH_ATTRIBUTES,
            attribute_names,
        )

        spec_names = tuple(s.name for s in EVENT_SEARCH_ATTRIBUTES)
        assert attribute_names() == spec_names

    def test_required_attributes_present(self):
        """The 6 attributes the plan promises."""
        from services.temporal.search_attributes import attribute_names

        required = {
            "EventType",
            "EventSource",
            "EventWorkflowId",
            "TriggerNodeId",
            "EventTriggerKind",
            "EventReceivedAt",
        }
        assert required.issubset(set(attribute_names()))


class TestA6DispatchEmitFeatureFlag:
    """A6: ``emit`` is a no-op pass-through when the flag is off."""

    @pytest.mark.asyncio
    async def test_disabled_emit_is_passthrough(self, monkeypatch):
        # Pydantic Settings reads from env; force the flag off for this test.
        monkeypatch.setenv("EVENT_FRAMEWORK_ENABLED", "false")

        from services.events.dispatch import emit

        event = WorkflowEvent(
            source="machinaos://services/test",
            type="com.machinaos.test.disabled",
        )
        returned = await emit(event)
        assert returned is event

    @pytest.mark.asyncio
    async def test_enabled_emit_calls_both_paths(self, monkeypatch):
        """When the flag is on, both signal-fanout and in-process
        broadcast are invoked. Both are patched to record-only."""
        monkeypatch.setenv("EVENT_FRAMEWORK_ENABLED", "true")
        # Force Settings() to re-read by clearing the cached singleton
        # if any; here Settings() builds fresh per call so monkeypatch
        # of env is enough.

        signal_calls = []
        broadcast_calls = []

        async def fake_signal(event):
            signal_calls.append(event)

        async def fake_broadcast(event, wire_key):
            broadcast_calls.append((event, wire_key))

        from services.events import dispatch

        monkeypatch.setattr(dispatch, "_signal_running_consumers", fake_signal)
        monkeypatch.setattr(dispatch, "_broadcast_in_process", fake_broadcast)

        event = WorkflowEvent(
            source="machinaos://services/test",
            type="com.machinaos.test.enabled",
        )
        await dispatch.emit(event, wire_routing_key="custom_wire_key")

        assert len(signal_calls) == 1
        assert signal_calls[0] is event
        assert len(broadcast_calls) == 1
        assert broadcast_calls[0] == (event, "custom_wire_key")


class TestA7MachinaWorkflowSignalHandler:
    """A7: signal handler dedups + queues; predicate matches.

    ``workflow.logger`` requires a Temporal workflow event loop, which
    we don't spin up in these unit tests. Patch ``temporalio.workflow.logger``
    to a no-op so the handler can run synchronously.
    """

    @pytest.fixture(autouse=True)
    def _patch_workflow_logger(self, monkeypatch):
        from unittest.mock import MagicMock
        from temporalio import workflow as temporal_workflow

        monkeypatch.setattr(temporal_workflow, "logger", MagicMock())

    def test_init_sets_empty_state(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        assert wf._seen_event_ids == set()
        assert wf._matched_events == []

    @pytest.mark.asyncio
    async def test_on_event_dedups_by_id(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        payload = {"id": "evt-1", "type": "com.machinaos.test.x"}

        await wf.on_event(payload)
        await wf.on_event(payload)  # duplicate

        assert wf._seen_event_ids == {"evt-1"}
        assert len(wf._matched_events) == 1

    @pytest.mark.asyncio
    async def test_on_event_drops_malformed_envelope(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        await wf.on_event({"type": "com.machinaos.test.x"})  # no 'id'
        assert wf._matched_events == []

    def test_has_event_matching_empty_state(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        assert wf._has_event_matching() is False

    def test_has_event_matching_predicate(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        wf._matched_events.append(
            {"id": "e1", "type": "com.machinaos.whatsapp.message.received"}
        )

        # No-predicate: any queued event matches.
        assert wf._has_event_matching() is True

        # Truthy predicate matches.
        assert wf._has_event_matching(
            lambda e: e["type"].endswith("message.received")
        ) is True

        # Falsy predicate doesn't match.
        assert wf._has_event_matching(
            lambda e: e["type"].endswith(".nope")
        ) is False

    def test_pop_matching_event_empty_returns_none(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        assert wf._pop_matching_event() is None
        assert wf._pop_matching_event(lambda e: True) is None

    def test_pop_matching_event_no_predicate_returns_fifo_head(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        first = {"id": "e1", "type": "com.machinaos.x.received"}
        second = {"id": "e2", "type": "com.machinaos.y.received"}
        wf._matched_events.extend([first, second])

        popped = wf._pop_matching_event()
        assert popped is first
        assert wf._matched_events == [second]

    def test_pop_matching_event_predicate_picks_first_match(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        first = {"id": "e1", "type": "com.machinaos.x.received"}
        second = {"id": "e2", "type": "com.machinaos.y.received"}
        third = {"id": "e3", "type": "com.machinaos.x.delivered"}
        wf._matched_events.extend([first, second, third])

        popped = wf._pop_matching_event(
            lambda e: e["type"].startswith("com.machinaos.y")
        )
        assert popped is second
        # Order preserved for non-matching siblings.
        assert wf._matched_events == [first, third]

    def test_pop_matching_event_no_predicate_match_returns_none(self):
        from services.temporal.workflow import MachinaWorkflow

        wf = MachinaWorkflow()
        wf._matched_events.append(
            {"id": "e1", "type": "com.machinaos.x.received"}
        )
        result = wf._pop_matching_event(lambda e: e["type"].endswith(".nope"))
        assert result is None
        # Queue untouched on miss.
        assert len(wf._matched_events) == 1


class TestA8EmitEventActivity:
    """A8: activity wrapper validates the envelope and forwards to emit."""

    @pytest.mark.asyncio
    async def test_valid_payload_returns_delivered_true(self, monkeypatch):
        captured = []

        async def fake_emit(event, **kwargs):
            captured.append(event)
            return event

        from services.events import dispatch as dispatch_mod

        monkeypatch.setattr(dispatch_mod, "emit", fake_emit)

        from services.temporal.activities import emit_event_activity

        payload = WorkflowEvent(
            source="machinaos://services/test",
            type="com.machinaos.test.a8",
        ).model_dump(mode="json")

        # Activities decorated with @activity.defn are still callable
        # outside a worker — they're plain async functions with metadata.
        result = await emit_event_activity(payload)

        assert result["delivered"] is True
        assert result["event_type"] == "com.machinaos.test.a8"
        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_malformed_payload_returns_delivered_false(self):
        from services.temporal.activities import emit_event_activity

        # Missing required `source` and `type` fields → validation fails.
        result = await emit_event_activity({"id": "evt-malformed"})

        assert result["delivered"] is False
        assert "error" in result
