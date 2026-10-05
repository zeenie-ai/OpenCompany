from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nodes.tool.task_manager import _execute_task_manager


def _config():
    return {
        "workflow_id": "wf-1", "execution_id": "exec-1", "parent_node_id": "lead-1",
        "ai_service": object(), "database": object(),
        "nodes": [
            {"id": "lead-1", "type": "orchestrator_agent", "data": {}},
            {"id": "child-1", "type": "aiAgent", "data": {"label": "Researcher"}},
        ],
        "edges": [{"source": "child-1", "target": "lead-1", "targetHandle": "input-teammates"}],
    }


@pytest.mark.asyncio
async def test_managed_missing_criteria_rejected_before_commit_or_child():
    service = SimpleNamespace(assign_durable_task=AsyncMock())
    child = AsyncMock()
    with patch("services.agent_team.get_agent_team_service", return_value=service), patch("services.handlers.tools._execute_delegated_agent", child):
        with pytest.raises(ValueError, match="acceptance_criteria"):
            await _execute_task_manager({"operation": "assign_task", "title": "Research", "mission": "Find evidence", "context": {}, "assignee_node_id": "child-1"}, {**_config(), "employee_job_id": "job-1"})
    service.assign_durable_task.assert_not_awaited()
    child.assert_not_awaited()


@pytest.mark.asyncio
async def test_managed_assignment_preserves_review_contract():
    service = SimpleNamespace(assign_durable_task=AsyncMock(return_value={"id": "task-1", "team_id": "team-1"}))
    child = AsyncMock(return_value={"success": True})
    args = {"operation": "assign_task", "title": "Research", "mission": "Find evidence", "context": {"topic": "queues"}, "acceptance_criteria": {"sources": "two cited primary sources"}, "depends_on": ["prior"], "assignee_node_id": "child-1"}
    with patch("services.agent_team.get_agent_team_service", return_value=service), patch("services.handlers.tools._execute_delegated_agent", child):
        result = await _execute_task_manager(args, {**_config(), "employee_job_id": "job-1"})
    assert service.assign_durable_task.await_args.kwargs["acceptance_criteria"] == args["acceptance_criteria"]
    assert result["delegation_request"]["depends_on"] == ["prior"]
    assert child.await_args.args[0]["context"]["acceptance_criteria"] == args["acceptance_criteria"]


@pytest.mark.asyncio
async def test_assign_starts_legacy_child_with_precreated_task_id():
    service = SimpleNamespace(assign_durable_task=AsyncMock(return_value={
        "id": "task-1", "team_id": "team-1", "status": "queued",
    }))
    execute_child = AsyncMock(return_value={"success": True, "status": "delegated", "task_id": "task-1"})

    with patch("services.agent_team.get_agent_team_service", return_value=service), patch(
        "services.handlers.tools._execute_delegated_agent", execute_child
    ):
        result = await _execute_task_manager({
            "operation": "assign_task", "title": "Research", "mission": "Find evidence",
            "assignee_node_id": "child-1", "context": {"topic": "queues"},
        }, _config())

    service.assign_durable_task.assert_awaited_once()
    execute_child.assert_awaited_once()
    assert execute_child.await_args.kwargs["precreated_task_id"] == "task-1"
    assert execute_child.await_args.args[0]["task"] == "Find evidence"
    assert result["delegation"]["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_assign_without_legacy_services_returns_temporal_envelope_only():
    service = SimpleNamespace(assign_durable_task=AsyncMock(return_value={
        "id": "task-2", "team_id": "team-1", "status": "queued",
    }))
    config = _config()
    config.pop("ai_service")
    config.pop("database")

    with patch("services.agent_team.get_agent_team_service", return_value=service):
        result = await _execute_task_manager({
            "operation": "assign_task", "title": "Research", "mission": "Find evidence",
            "assignee_node_id": "child-1",
        }, config)

    assert result["delegation"] is None
    assert result["delegation_request"]["team_task_id"] == "task-2"


@pytest.mark.asyncio
async def test_accept_infers_the_only_submitted_task_and_revision():
    submitted = {"id": "task-submitted", "revision": 4, "status": "submitted"}
    service = SimpleNamespace(
        list_durable_tasks=AsyncMock(return_value=[submitted]),
        mutate_durable_task=AsyncMock(return_value={**submitted, "revision": 5, "status": "accepted"}),
    )
    with patch("services.agent_team.get_agent_team_service", return_value=service):
        result = await _execute_task_manager({"operation": "accept_task"}, _config())

    assert result["task"]["status"] == "accepted"
    service.mutate_durable_task.assert_awaited_once_with(
        workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
        task_id="task-submitted", revision=4, operation="accept",
    )


@pytest.mark.asyncio
async def test_accept_never_guesses_when_multiple_tasks_are_submitted():
    service = SimpleNamespace(list_durable_tasks=AsyncMock(return_value=[
        {"id": "one", "revision": 1}, {"id": "two", "revision": 2},
    ]))
    with patch("services.agent_team.get_agent_team_service", return_value=service):
        with pytest.raises(ValueError, match="call list_tasks or get_task first"):
            await _execute_task_manager({"operation": "accept_task"}, _config())


@pytest.mark.asyncio
async def test_reassign_returns_delegation_request_for_the_new_assignee():
    """reassign moves the row to queued, and nothing sweeps queued rows —
    the scheduling envelope is the ONLY runner. Without it the mutation
    flipped the DB row and the work silently never re-ran."""
    service = SimpleNamespace(
        mutate_durable_task=AsyncMock(return_value={
            "id": "task-3", "revision": 6, "status": "queued",
            "assigned_to": "child-1", "mission": "Find evidence",
            "context": {"topic": "queues"},
        }),
    )
    with patch("services.agent_team.get_agent_team_service", return_value=service):
        result = await _execute_task_manager({
            "operation": "reassign_task", "task_id": "task-3",
            "expected_revision": 5, "assignee_node_id": "child-1",
        }, _config())

    assert result["task"]["status"] == "queued"
    envelope = result["delegation_request"]
    assert envelope["team_task_id"] == "task-3"
    assert envelope["assignee_node_id"] == "child-1"
    assert envelope["delegate_name"]
    assert envelope["task"] == "Find evidence"
    assert envelope["context"] == {"topic": "queues"}


@pytest.mark.asyncio
async def test_retry_returns_delegation_request_for_the_current_assignee():
    row = {
        "id": "task-4", "revision": 2, "status": "failed",
        "assigned_to": "child-1", "mission": "Find evidence", "context": {},
    }
    service = SimpleNamespace(
        get_durable_task=AsyncMock(return_value=row),
        mutate_durable_task=AsyncMock(return_value={
            **row, "revision": 3, "status": "queued",
        }),
    )
    with patch("services.agent_team.get_agent_team_service", return_value=service):
        result = await _execute_task_manager(
            {"operation": "retry_task", "task_id": "task-4"}, _config()
        )

    envelope = result["delegation_request"]
    assert envelope["team_task_id"] == "task-4"
    assert envelope["assignee_node_id"] == "child-1"
    assert envelope["task"] == "Find evidence"


@pytest.mark.asyncio
async def test_accept_returns_no_delegation_request():
    submitted = {"id": "task-5", "revision": 4, "status": "submitted"}
    service = SimpleNamespace(
        list_durable_tasks=AsyncMock(return_value=[submitted]),
        mutate_durable_task=AsyncMock(
            return_value={**submitted, "revision": 5, "status": "accepted"}
        ),
    )
    with patch("services.agent_team.get_agent_team_service", return_value=service):
        result = await _execute_task_manager({"operation": "accept_task"}, _config())

    assert "delegation_request" not in result


@pytest.mark.asyncio
async def test_list_tasks_can_include_prior_executions():
    service = SimpleNamespace(list_durable_task_history=AsyncMock(return_value=[
        {"id": "old-task", "status": "accepted", "team_execution_id": "exec-old"},
    ]))
    with patch("services.agent_team.get_agent_team_service", return_value=service):
        result = await _execute_task_manager(
            {"operation": "list_tasks", "include_history": True}, _config()
        )

    assert result["tasks"][0]["team_execution_id"] == "exec-old"
    service.list_durable_task_history.assert_awaited_once_with(
        workflow_id="wf-1", team_lead_node_id="lead-1", status=None
    )
