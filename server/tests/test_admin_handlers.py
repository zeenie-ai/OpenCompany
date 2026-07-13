"""Wave 12 D3: Visibility admin WS handler tests.

Unit-style — no live Temporal cluster. The container's ``temporal_client``
is monkeypatched per test. Three handlers, each tested against four
contracts:

1. ``workflow_id`` required (input validation).
2. Visibility query shape (correct filter clauses).
3. Fail-soft on Visibility errors (don't crash the admin UI).
4. ``temporal_client`` not connected → graceful empty result, not error.

Plus helper tests for the search-attribute coercion + failure event
extraction.
"""

from __future__ import annotations

import sys
import types
from typing import List
from unittest.mock import MagicMock

import pytest


if "cli" not in sys.modules:
    _cli_stub = types.ModuleType("cli")
    _cli_stub.__path__ = []
    sys.modules["cli"] = _cli_stub
    _opencompany_tcp = types.ModuleType("cli.tcp")
    _opencompany_tcp.probe_tcp_port = MagicMock(return_value=False)
    sys.modules["cli.tcp"] = _opencompany_tcp


# ---------------------------------------------------------------------------
# list_canary_listeners
# ---------------------------------------------------------------------------


class TestListCanaryListeners:
    @pytest.mark.asyncio
    async def test_workflow_id_required(self):
        from services.events.admin_handlers import handle_list_canary_listeners

        result = await handle_list_canary_listeners({}, MagicMock())
        assert result["success"] is False
        assert "workflow_id" in result["error"]

    @pytest.mark.asyncio
    async def test_temporal_not_connected_returns_empty(self, monkeypatch):
        from services.events.admin_handlers import handle_list_canary_listeners

        wrapper = MagicMock()
        wrapper.client = None
        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await handle_list_canary_listeners(
            {"workflow_id": "wf-1"},
            MagicMock(),
        )
        assert result["success"] is True
        assert result["listeners"] == []
        assert result["count"] == 0

    @pytest.mark.asyncio
    async def test_visibility_query_filters_both_listener_types(self, monkeypatch):
        from services.events.admin_handlers import handle_list_canary_listeners

        recorded_queries: List[str] = []

        async def fake_list(query):
            recorded_queries.append(query)
            yield MagicMock(
                id="trigger-listener-wf-1-wh-1",
                run_id="run-1",
                workflow_type="TriggerListenerWorkflow",
                status=MagicMock(name="Status", spec_set=["name"]),
                start_time=None,
                close_time=None,
                search_attributes=None,
            )

        client = MagicMock()
        client.list_workflows = fake_list

        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await handle_list_canary_listeners(
            {"workflow_id": "wf-1"},
            MagicMock(),
        )
        assert result["success"] is True
        assert result["count"] == 1

        q = recorded_queries[0]
        assert "EventWorkflowId='wf-1'" in q
        assert "'TriggerListenerWorkflow'" in q
        assert "'PollingTriggerWorkflow'" in q
        assert "WorkflowType IN" in q
        assert "ExecutionStatus='Running'" in q

    @pytest.mark.asyncio
    async def test_failsoft_on_visibility_error(self, monkeypatch):
        from services.events.admin_handlers import handle_list_canary_listeners

        async def fake_list_raises(query):
            if False:
                yield None
            raise RuntimeError("Visibility unavailable")

        client = MagicMock()
        client.list_workflows = fake_list_raises

        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await handle_list_canary_listeners(
            {"workflow_id": "wf-1"},
            MagicMock(),
        )
        assert result["success"] is False
        assert "Visibility unavailable" in result["error"]


# ---------------------------------------------------------------------------
# list_canary_schedules
# ---------------------------------------------------------------------------


class TestListCanarySchedules:
    @pytest.mark.asyncio
    async def test_workflow_id_required(self):
        from services.events.admin_handlers import handle_list_canary_schedules

        result = await handle_list_canary_schedules({}, MagicMock())
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_visibility_query_filters_cron_kind(self, monkeypatch):
        """``Client.list_schedules`` is ``async def`` (returns coroutine
        resolving to ``ScheduleAsyncIterator``) — distinct from
        ``Client.list_workflows`` which returns the iterator directly.
        Stub must match the real SDK shape: a coroutine that returns
        an async iterator. Pre-fix the stub was an async generator
        function which masked an ``async for`` over a coroutine bug —
        production code raised ``'async for' requires an object with
        __aiter__ method, got coroutine`` on every deployment cancel.
        """
        from services.events.admin_handlers import handle_list_canary_schedules

        recorded_queries: List[str] = []

        class _FakeScheduleIterator:
            def __init__(self, ids):
                self._ids = list(ids)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._ids:
                    raise StopAsyncIteration
                sched_id = self._ids.pop(0)
                return MagicMock(id=sched_id, search_attributes=None)

        async def fake_list(query, **kwargs):
            recorded_queries.append(query)
            return _FakeScheduleIterator(["cron-schedule-wf-1-a", "cron-schedule-wf-1-b"])

        client = MagicMock()
        client.list_schedules = fake_list

        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await handle_list_canary_schedules(
            {"workflow_id": "wf-1"},
            MagicMock(),
        )
        assert result["success"] is True
        assert result["count"] == 2

        q = recorded_queries[0]
        assert "EventWorkflowId='wf-1'" in q
        assert "EventTriggerKind='cron'" in q

    def test_handler_awaits_list_schedules_before_async_for(self):
        """Regression: the production code MUST await
        ``client.list_schedules(...)`` before iterating. ``list_schedules``
        is ``async def`` in the temporalio SDK; raw ``async for`` over
        the coroutine raises ``'async for' requires an object with
        __aiter__ method, got coroutine`` — observed in prod on every
        deployment cancel before the Wave 13 follow-up fix.
        """
        import inspect

        from services.events import admin_handlers

        src = inspect.getsource(admin_handlers)
        # The handler must NOT have a bare ``async for ... in
        # wrapper.client.list_schedules(...)``. It must capture the
        # iterator via ``await`` first.
        import re

        bare_pattern = re.compile(
            r"async\s+for\s+\w+\s+in\s+\w+\.list_schedules\s*\(",
        )
        assert not bare_pattern.search(src), (
            "admin_handlers contains a bare ``async for ... in "
            "client.list_schedules(...)`` — list_schedules is async def "
            "in temporalio; raw async for over the coroutine fails. "
            "Use ``iterator = await client.list_schedules(query=...)`` "
            "then ``async for desc in iterator:`` instead."
        )


# ---------------------------------------------------------------------------
# get_workflow_failure_history
# ---------------------------------------------------------------------------


class TestGetWorkflowFailureHistory:
    @pytest.mark.asyncio
    async def test_workflow_id_required(self):
        from services.events.admin_handlers import handle_get_workflow_failure_history

        result = await handle_get_workflow_failure_history({}, MagicMock())
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_temporal_not_connected_returns_error(self, monkeypatch):
        from services.events.admin_handlers import handle_get_workflow_failure_history

        wrapper = MagicMock()
        wrapper.client = None

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await handle_get_workflow_failure_history(
            {"workflow_id": "wf-1"},
            MagicMock(),
        )
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_extracts_only_failure_events(self, monkeypatch):
        from services.events.admin_handlers import handle_get_workflow_failure_history

        # 3 events: WorkflowStarted (no), ActivityTaskFailed (yes),
        # ActivityTaskCompleted (no).
        events = [
            MagicMock(
                event_type=MagicMock(name="WorkflowExecutionStarted", spec=["name"]),
            ),
            MagicMock(
                event_type=MagicMock(name="ActivityTaskFailed", spec=["name"]),
                event_id=42,
                event_time=None,
                activity_task_failed_event_attributes=MagicMock(
                    failure=MagicMock(message="connection refused"),
                    activity_id="poll-1",
                    activity_type=MagicMock(name="poll.googleGmailReceive.v1"),
                ),
            ),
            MagicMock(
                event_type=MagicMock(name="ActivityTaskCompleted", spec=["name"]),
            ),
        ]
        # Need to fix the .name on the type enum since MagicMock spec
        # doesn't auto-stringify in our `_value_or_name` helper.
        for ev, type_name in zip(
            events,
            ["WorkflowExecutionStarted", "ActivityTaskFailed", "ActivityTaskCompleted"],
        ):
            ev.event_type.name = type_name

        async def fake_fetch():
            for e in events:
                yield e

        handle = MagicMock()
        handle.fetch_history_events = fake_fetch

        client = MagicMock()
        client.get_workflow_handle = MagicMock(return_value=handle)

        wrapper = MagicMock()
        wrapper.client = client

        from core import container as container_mod

        monkeypatch.setattr(container_mod.container, "temporal_client", lambda: wrapper)

        result = await handle_get_workflow_failure_history(
            {"workflow_id": "wf-temporal-1"},
            MagicMock(),
        )
        assert result["success"] is True
        assert result["count"] == 1
        failure = result["failures"][0]
        assert failure["event_type"] == "ActivityTaskFailed"
        assert failure["event_id"] == 42
        assert failure["message"] == "connection refused"
        assert failure["activity_id"] == "poll-1"


# ---------------------------------------------------------------------------
# Search-attribute coercion helper
# ---------------------------------------------------------------------------


class TestSearchAttributesDict:
    def test_none_returns_empty_dict(self):
        from services.events.admin_handlers import _search_attributes_dict

        assert _search_attributes_dict(None) == {}

    def test_plain_dict_with_list_values(self):
        """Older Temporal SDK: untyped attributes are dict[str, List]."""
        from services.events.admin_handlers import _search_attributes_dict

        raw = {"EventWorkflowId": ["wf-1"], "TriggerNodeId": ["node-a"]}
        view = _search_attributes_dict(raw)
        assert view == {"EventWorkflowId": "wf-1", "TriggerNodeId": "node-a"}

    def test_typed_search_attributes_iterable(self):
        """Newer SDK: TypedSearchAttributes is iterable over pairs."""
        from services.events.admin_handlers import _search_attributes_dict
        from temporalio.common import (
            SearchAttributeKey,
            SearchAttributePair,
            TypedSearchAttributes,
        )

        typed = TypedSearchAttributes(
            [
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("EventWorkflowId"),
                    "wf-1",
                ),
                SearchAttributePair(
                    SearchAttributeKey.for_keyword("EventTriggerKind"),
                    "cron",
                ),
            ]
        )
        view = _search_attributes_dict(typed)
        assert view == {"EventWorkflowId": "wf-1", "EventTriggerKind": "cron"}


# ---------------------------------------------------------------------------
# Registration smoke
# ---------------------------------------------------------------------------


class TestWsHandlerRegistration:
    """Importing services.events registers the three admin handlers
    via ws_handler_registry — the WS router dispatches by message type
    without per-handler imports."""

    def test_all_three_handlers_registered(self):
        # Force re-import path-of-least-resistance: just import the
        # package and confirm the registry has the wire keys.
        import services.events  # noqa: F401 — side-effect import
        from services.ws_handler_registry import get_ws_handlers

        handlers = get_ws_handlers()
        for wire_key in (
            "list_canary_listeners",
            "list_canary_schedules",
            "get_workflow_failure_history",
        ):
            assert wire_key in handlers, (
                f"WS handler {wire_key!r} not registered. Importing "
                "services.events should call register_ws_handlers with "
                "the admin_handlers.WS_HANDLERS dict."
            )
