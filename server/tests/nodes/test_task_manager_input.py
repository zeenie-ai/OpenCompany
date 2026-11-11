"""Task Manager validates model arguments before any durable mutation."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from nodes.tool.task_manager import TaskManagerNode, TaskManagerParams, TaskManagerToolInput
from services.plugin import NodeContext


ASSIGNMENT = {
    "operation": "assign_task", "title": "Research", "mission": "Find cited evidence",
    "assignee_node_id": "child-1",
}
# The incident was valid JSON with prose and smart quotes inside property names.
CORRUPTED = json.loads(
    '{"operation":"assign_task",'
    '"assignee_node_id”:\u201dchild-1”:null}NB:IneedproperJSON.Letmere-writethetoolcallscorrectly.{":"x",'
    '"title”:\u201dResearch”:null}NB:brokenagain.IwillnowemitvalidJSONforthetwotoolcalls.{":"y"}'
)


def context():
    return NodeContext.from_legacy("manager", "taskManager", {
        "workflow_id": "wf-1", "execution_id": "exec-1", "parent_node_id": "lead-1",
        "nodes": [
            {"id": "lead-1", "type": "orchestrator_agent", "data": {}},
            {"id": "child-1", "type": "aiAgent", "data": {"label": "Researcher"}},
        ],
        "edges": [{"source": "child-1", "target": "lead-1", "targetHandle": "input-teammates"}],
    })


@pytest.mark.parametrize("arguments,field", [
    ({}, "operation"),
    ({"operation": "self_destruct"}, "operation"),
    (CORRUPTED, "assignee_node_id"),
    ({**ASSIGNMENT, "unknown": "ignored before"}, "unknown"),
    ({**ASSIGNMENT, "title": None}, "title"),
    ({**ASSIGNMENT, "mission": ""}, "mission"),
    ({**ASSIGNMENT, "mission": " \n\t"}, "mission"),
    ({"operation": "assign_task", "title": "Research", "mission": "Investigate"}, "assignee_node_id"),
    ({"operation": "submit_job", "mission": " "}, "mission"),
    *[({"operation": operation}, "task_id") for operation in (
        "get_task", "inspect_task_trace", "modify_task", "cancel_task", "retry_task", "reassign_task",
    )],
    ({"operation": "reassign_task", "task_id": "task-1"}, "assignee_node_id"),
    ({"operation": "modify_task", "task_id": "task-1"}, "at least one"),
    ({"operation": "inspect_task_trace", "task_id": "task-1", "detail": "search"}, "query"),
    ({"operation": "inspect_task_trace", "task_id": "task-1", "detail": "search", "query": " "}, "query"),
])
@pytest.mark.asyncio
async def test_invalid_arguments_never_reach_task_service(monkeypatch, arguments, field):
    service = AsyncMock()
    monkeypatch.setattr("services.agent_team.get_agent_team_service", service)

    result = await TaskManagerNode().execute_as_tool(arguments, {"label": "Task Manager"}, context())

    assert result["error_type"] == "ValidationError"
    assert field in result["error"]
    service.assert_not_called()


@pytest.mark.parametrize("field", ["title", "mission", "assignee_node_id"])
@pytest.mark.asyncio
async def test_saved_settings_cannot_supply_missing_model_arguments(monkeypatch, field):
    service = AsyncMock()
    monkeypatch.setattr("services.agent_team.get_agent_team_service", service)
    arguments = {name: value for name, value in ASSIGNMENT.items() if name != field}

    result = await TaskManagerNode().execute_as_tool(arguments, {**ASSIGNMENT, "label": "Saved task"}, context())

    assert result["error_type"] == "ValidationError"
    assert field in result["error"]
    service.assert_not_called()


@pytest.mark.asyncio
async def test_corrected_assignment_uses_model_input_despite_legacy_settings(monkeypatch):
    service = SimpleNamespace(assign_durable_task=AsyncMock(return_value={
        "id": "task-1", "team_id": "team-1", "status": "queued",
    }))
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: service)
    params = {"operation": "list_tasks", "title": "stale", "mission": "saved mission", "label": "Manager", "reason": None}
    node = TaskManagerNode()

    invalid = await node.execute_as_tool(CORRUPTED, params, context())
    assert invalid["error_type"] == "ValidationError"
    service.assign_durable_task.assert_not_awaited()
    result = await node.execute_as_tool(ASSIGNMENT, params, context())

    service.assign_durable_task.assert_awaited_once()
    assert service.assign_durable_task.await_args.kwargs["title"] == ASSIGNMENT["title"]
    assert service.assign_durable_task.await_args.kwargs["mission"] == ASSIGNMENT["mission"]
    assert result["delegation_request"]["team_task_id"] == "task-1"


@pytest.mark.parametrize("arguments", [
    {"operation": "submit_job", "mission": "Do the work"},
    ASSIGNMENT,
    {"operation": "assign_task", "title": "Research", "mission": "Investigate", "delegate_name": "researcher"},
    {**ASSIGNMENT, "delegate_name": "researcher", "context": None, "depends_on": None},
    {"operation": "list_tasks", "title": None},
    {"operation": "finish_team"},
    {"operation": "accept_task"},
    {"operation": "mark_done"},
    {"operation": "retry_task", "task_id": "task-1"},
    {"operation": "cancel_task", "task_id": "task-1", "revision": 0},
    {"operation": "modify_task", "task_id": "task-1", "context": {}},
    {"operation": "modify_task", "task_id": "task-1", "acceptance_criteria": {}},
    {"operation": "get_task", "task_id": "task-1", "detail": "search"},
    {"operation": "inspect_task_trace", "task_id": "task-1", "detail": "search", "query": "failure"},
])
def test_supported_operation_inputs_keep_existing_compatibility(arguments):
    validated = TaskManagerToolInput.model_validate(arguments)
    assert validated.model_dump(exclude_unset=True) == arguments


@pytest.mark.parametrize("revision_fields,expected", [
    ({"revision": 3}, 3),
    ({"revision": 3, "expected_revision": 4}, 4),
    ({"revision": 3, "expected_revision": None}, 3),
    ({"expected_revision": 0}, 0),
    ({}, 7),
])
@pytest.mark.asyncio
async def test_revision_alias_precedence_and_omission(monkeypatch, revision_fields, expected):
    service = SimpleNamespace(
        get_durable_task=AsyncMock(return_value={"id": "task-1", "revision": 7}),
        mutate_durable_task=AsyncMock(return_value={"id": "task-1", "status": "accepted"}),
    )
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: service)
    result = await TaskManagerNode().execute_as_tool(
        {"operation": "mark_done", "task_id": "task-1", **revision_fields}, {}, context(),
    )
    assert result["task"]["status"] == "accepted"
    assert service.mutate_durable_task.await_args.kwargs["revision"] == expected


def test_configuration_stays_permissive_but_tool_input_is_locked():
    assert TaskManagerParams.model_validate({"label": "Manager"}).operation == "list_tasks"
    assert TaskManagerNode.ToolInput is TaskManagerToolInput
    assert TaskManagerNode.tool_schema_locked is True
    schema = TaskManagerNode.as_tool_schema()["parameters"]
    assert schema["required"] == ["operation"]
    assert schema["additionalProperties"] is False
    for field in ("operation", "title", "mission", "assignee_node_id", "delegate_name", "task_id", "query"):
        assert schema["properties"][field]["description"]
    with pytest.raises(ValidationError):
        TaskManagerToolInput.model_validate({"operation": "list_tasks", "label": "Manager"})


@pytest.mark.asyncio
async def test_running_task_conflict_returns_actionable_node_error_without_retry(monkeypatch):
    from services.agent_team import AgentTeamService
    from services.plugin import base

    task = {
        "id": "task-1", "status": "running", "revision": 0, "current_attempt": 0, "retry_count": 0,
        "allowed_actions": ["cancel"],
    }
    database = SimpleNamespace(
        get_durable_team_task=AsyncMock(return_value=dict(task)),
        transition_team_task=AsyncMock(return_value=None),
    )
    service = AgentTeamService(database)
    service.resolve_lead_scope = AsyncMock(return_value={"id": "team-1"})
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: service)
    delegation = AsyncMock()
    monkeypatch.setattr("services.handlers.tools._execute_delegated_agent", delegation)
    exception_log = MagicMock()
    warning_log = MagicMock()
    monkeypatch.setattr(base.logger, "exception", exception_log)
    monkeypatch.setattr(base.logger, "warning", warning_log)

    result = await TaskManagerNode().execute_as_tool(
        {"operation": "modify_task", "task_id": "task-1", "title": "Changed", "mission": "Revise the work"},
        {}, context(),
    )

    assert result["success"] is False
    assert result["error_type"] == "NodeUserError"
    assert "running" in result["error"]
    assert "get_task" in result["hint"] and "allowed_actions" in result["hint"]
    assert not result.get("requires_user_action")
    assert not result.get("retryable")
    database.transition_team_task.assert_awaited_once()
    assert database.get_durable_team_task.return_value == task
    delegation.assert_not_awaited()
    exception_log.assert_not_called()
    warning_log.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("invalid database value"), RuntimeError("database unavailable")])
async def test_unexpected_mutation_failures_keep_their_error_type(monkeypatch, error):
    from services.agent_team import AgentTeamService
    from services.plugin import base

    database = SimpleNamespace(
        get_durable_team_task=AsyncMock(return_value={
            "id": "task-1", "status": "queued", "revision": 0, "current_attempt": 0, "retry_count": 0,
        }),
        transition_team_task=AsyncMock(side_effect=error),
    )
    service = AgentTeamService(database)
    service.resolve_lead_scope = AsyncMock(return_value={"id": "team-1"})
    monkeypatch.setattr("services.agent_team.get_agent_team_service", lambda: service)
    exception_log = MagicMock()
    monkeypatch.setattr(base.logger, "exception", exception_log)

    result = await TaskManagerNode().execute_as_tool(
        {"operation": "modify_task", "task_id": "task-1", "title": "Changed", "expected_revision": 0},
        {}, context(),
    )

    assert result["success"] is False
    assert result["error_type"] == type(error).__name__
    assert result["error"] == str(error)
    assert "hint" not in result
    database.transition_team_task.assert_awaited_once()
    exception_log.assert_called_once()
