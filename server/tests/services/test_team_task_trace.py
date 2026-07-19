from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from temporalio.api.enums.v1 import EventType

from services.team_task_trace import TeamTaskTraceService, _redact


def test_trace_redaction_removes_secret_values():
    text = _redact("authorization: Bearer-abc api_key=top-secret ordinary context")
    assert "Bearer-abc" not in text
    assert "top-secret" not in text
    assert text.count("[REDACTED]") == 2


def _task(**execution):
    return {
        "id": "task-1", "team_id": "team-1", "current_attempt": 0,
        "trace_id": "trace-1", "attempts": [{"attempt_number": 0, **execution}],
    }


def _team_service(task):
    service = MagicMock()
    service.get_durable_task = AsyncMock(return_value=task)
    service.database.add_agent_message = AsyncMock(return_value={"id": 1})
    return service


@pytest.mark.asyncio
async def test_unregistered_attempt_returns_explicit_status_without_temporal_lookup():
    team_service = _team_service(_task())
    trace = await TeamTaskTraceService(team_service, SimpleNamespace(client=MagicMock())).get_trace(
        workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
        task_id="task-1",
    )

    assert trace["status"] == "execution_not_registered"
    assert trace["events"] == []
    team_service.database.add_agent_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_history_is_paginated_sanitized_and_resolved_from_persisted_identity():
    event = SimpleNamespace(
        event_id=7,
        event_type=EventType.Value("EVENT_TYPE_ACTIVITY_TASK_STARTED"),
        event_time=None,
        WhichOneof=lambda _name: None,
    )
    iterator = MagicMock()
    iterator.current_page = [event]
    iterator.next_page_token = b"next"
    iterator.fetch_next_page = AsyncMock()
    handle = MagicMock()
    handle.fetch_history_events.return_value = iterator
    client = MagicMock()
    client.get_workflow_handle.return_value = handle
    team_service = _team_service(_task(child_workflow_id="child-1", child_run_id="run-1"))

    trace = await TeamTaskTraceService(team_service, SimpleNamespace(client=client)).get_trace(
        workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
        task_id="task-1", detail="timeline", limit=500,
    )

    client.get_workflow_handle.assert_called_once_with("child-1", run_id="run-1")
    handle.fetch_history_events.assert_called_once_with(
        page_size=100, next_page_token=None, skip_archival=False,
    )
    assert trace["status"] == "available"
    assert trace["events"] == [{"event_id": 7, "type": "activity_task_started", "category": "activity"}]
    assert trace["next_cursor"] is not None


@pytest.mark.asyncio
async def test_trace_scope_rejection_happens_before_temporal_access():
    team_service = MagicMock()
    team_service.get_durable_task = AsyncMock(side_effect=ValueError("Task not found in this lead execution"))
    client = MagicMock()

    with pytest.raises(ValueError, match="lead execution"):
        await TeamTaskTraceService(team_service, SimpleNamespace(client=client)).get_trace(
            workflow_id="wf-1", team_lead_node_id="forged-lead", execution_id="exec-1",
            task_id="task-foreign",
        )
    client.get_workflow_handle.assert_not_called()


def _event(event_id, name):
    return SimpleNamespace(
        event_id=event_id, event_type=EventType.Value(f"EVENT_TYPE_{name}"),
        event_time=None, WhichOneof=lambda _name: None,
    )


@pytest.mark.asyncio
async def test_search_scans_pages_and_returns_bounded_context_with_match_markers():
    iterator = MagicMock()
    pages = [
        ([_event(1, "WORKFLOW_EXECUTION_STARTED"), _event(2, "ACTIVITY_TASK_STARTED")], b"page-2"),
        ([_event(3, "ACTIVITY_TASK_FAILED"), _event(4, "TIMER_STARTED")], b"page-3"),
    ]

    async def fetch_next_page(*, page_size):
        page, token = pages.pop(0)
        iterator.current_page = page
        iterator.next_page_token = token

    iterator.fetch_next_page = AsyncMock(side_effect=fetch_next_page)
    handle = MagicMock()
    handle.fetch_history_events.return_value = iterator
    client = MagicMock()
    client.get_workflow_handle.return_value = handle

    trace = await TeamTaskTraceService(
        _team_service(_task(child_workflow_id="child-1", child_run_id="run-1")),
        SimpleNamespace(client=client),
    ).get_trace(
        workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
        task_id="task-1", detail="search", query="activity_task_failed",
        limit=1, scan_limit=4, context_lines=1,
    )

    assert trace["search"] == {
        "mode": "literal", "case_sensitive": False, "context_lines": 1,
        "scanned_events": 4, "matched_events": 1,
    }
    assert [(event["event_id"], event["match"]) for event in trace["events"]] == [
        (2, False), (3, True), (4, False),
    ]
    assert trace["next_cursor"] is not None
    assert iterator.fetch_next_page.await_count == 2


@pytest.mark.asyncio
async def test_search_supports_term_modes_and_category_filters():
    event = _event(9, "ACTIVITY_TASK_TIMED_OUT")
    iterator = MagicMock(current_page=[event], next_page_token=None)
    iterator.fetch_next_page = AsyncMock()
    handle = MagicMock()
    handle.fetch_history_events.return_value = iterator
    client = MagicMock()
    client.get_workflow_handle.return_value = handle

    trace = await TeamTaskTraceService(
        _team_service(_task(runner_workflow_id="runner-1", runner_run_id="run-1")),
        SimpleNamespace(client=client),
    ).get_trace(
        workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
        task_id="task-1", detail="search", query="activity timed_out",
        search_mode="all_terms", categories=["failure"], context_lines=0,
    )

    assert trace["search"]["matched_events"] == 1
    assert trace["events"][0]["match"] is True


@pytest.mark.asyncio
async def test_search_requires_bounded_valid_query():
    service = TeamTaskTraceService(_team_service(_task()), SimpleNamespace(client=MagicMock()))
    common = dict(workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1", task_id="task-1")

    with pytest.raises(ValueError, match="non-empty query"):
        await service.get_trace(**common, detail="search", query="  ")
    with pytest.raises(ValueError, match="200 characters"):
        await service.get_trace(**common, detail="search", query="x" * 201)
    with pytest.raises(ValueError, match="category"):
        await service.get_trace(**common, detail="search", query="failed", categories=["payload"])
