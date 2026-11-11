from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import pytest

from services.agent_team import AgentTeamService, TaskMutationConflict


def _database(*, member_role="teammate"):
    return SimpleNamespace(
        find_team=AsyncMock(return_value={
            "id": "team-1", "workflow_id": "wf-1", "execution_id": "exec-1",
            "root_execution_id": "root-1", "team_lead_node_id": "lead-1",
        }),
        get_team_stats=AsyncMock(return_value={"members": [
            {"agent_node_id": "agent-1", "role": member_role, "status": "idle"},
        ]}),
        get_durable_team_task=AsyncMock(return_value=None),
        create_durable_team_task=AsyncMock(side_effect=lambda **values: {**values, "revision": 0}),
        update_team_status=AsyncMock(return_value=True),
        transition_team_task=AsyncMock(),
        get_team_tasks=AsyncMock(return_value=[]),
        list_team_executions=AsyncMock(return_value=[]),
    )


@pytest.mark.asyncio
async def test_assignment_is_scoped_to_persisted_teammates():
    database = _database(member_role="team_lead")
    service = AgentTeamService(database)

    with pytest.raises(ValueError, match="not a teammate"):
        await service.assign_durable_task(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
            assignee_node_id="agent-1", title="Research", mission="Find evidence",
        )
    database.create_durable_team_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_assignment_injects_execution_authority_server_side():
    database = _database()
    service = AgentTeamService(database)

    task = await service.assign_durable_task(
        workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
        assignee_node_id="agent-1", title="Research", mission="Find evidence",
    )

    assert task["team_id"] == "team-1"
    assert task["root_execution_id"] == "root-1"
    assert task["parent_agent_id"] == "lead-1"


@pytest.fixture
async def queued_task(real_database):
    assert await real_database.create_team(
        team_id="team-1", workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
    )
    task = await real_database.create_durable_team_task(
        id="task-1", team_id="team-1", title="Research", mission="Find evidence",
        created_by="lead-1", assigned_to="agent-1", status="queued",
    )
    assert task is not None
    return task


@pytest.mark.asyncio
async def test_running_task_at_current_revision_cannot_be_modified(real_database, queued_task, monkeypatch):
    assert await real_database.claim_task("task-1", "agent-1", expected_attempt=0)
    before = await real_database.get_durable_team_task("team-1", "task-1")
    assert before["status"] == "running"
    assert before["revision"] == queued_task["revision"] == 0
    transition = AsyncMock(wraps=real_database.transition_team_task)
    monkeypatch.setattr(real_database, "transition_team_task", transition)
    broadcaster = SimpleNamespace(broadcast_team_event=AsyncMock())

    with pytest.raises(TaskMutationConflict) as caught:
        await AgentTeamService(real_database, broadcaster).mutate_durable_task(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
            task_id="task-1", revision=0, operation="modify", title="Changed", mission="Different work",
        )

    message = str(caught.value)
    for detail in ("modify", "task-1", "status=running", "revision=0", "supplied revision=0", "allowed_actions=['cancel']", "get_task"):
        assert detail in message
    assert await real_database.get_durable_team_task("team-1", "task-1") == before
    transition.assert_awaited_once()
    broadcaster.broadcast_team_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_revision_is_rejected_without_overwriting_current_task(real_database, queued_task, monkeypatch):
    assert await real_database.transition_team_task("team-1", "task-1", 0, ["queued"], {"title": "Current title"})
    before = await real_database.get_durable_team_task("team-1", "task-1")
    transition = AsyncMock(wraps=real_database.transition_team_task)
    monkeypatch.setattr(real_database, "transition_team_task", transition)
    broadcaster = SimpleNamespace(broadcast_team_event=AsyncMock())

    with pytest.raises(TaskMutationConflict) as caught:
        await AgentTeamService(real_database, broadcaster).mutate_durable_task(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
            task_id="task-1", revision=0, operation="modify", title="Stale title",
        )

    message = str(caught.value)
    for detail in ("status=queued", "revision=1", "supplied revision=0", "allowed_actions=['modify', 'cancel']", "get_task"):
        assert detail in message
    assert await real_database.get_durable_team_task("team-1", "task-1") == before
    transition.assert_awaited_once()
    broadcaster.broadcast_team_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_claim_race_reports_fresh_state_without_retrying_mutation(real_database, queued_task, monkeypatch):
    original_transition = real_database.transition_team_task
    original_read = real_database.get_durable_team_task
    claimed = None

    async def claim_before_transition(*args, **kwargs):
        nonlocal claimed
        assert await real_database.claim_task("task-1", "agent-1", expected_attempt=0)
        claimed = await original_read("team-1", "task-1")
        return await original_transition(*args, **kwargs)

    transition = AsyncMock(side_effect=claim_before_transition)
    read = AsyncMock(wraps=original_read)
    monkeypatch.setattr(real_database, "transition_team_task", transition)
    monkeypatch.setattr(real_database, "get_durable_team_task", read)
    broadcaster = SimpleNamespace(broadcast_team_event=AsyncMock())

    with pytest.raises(TaskMutationConflict, match="Latest observed state: status=running, revision=0"):
        await AgentTeamService(real_database, broadcaster).mutate_durable_task(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
            task_id="task-1", revision=0, operation="modify", title="Changed",
        )

    assert await original_read("team-1", "task-1") == claimed
    assert read.await_args_list == [call("team-1", "task-1"), call("team-1", "task-1")]
    transition.assert_awaited_once()
    broadcaster.broadcast_team_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_acceptance_is_a_conflict_without_another_write_or_broadcast(real_database, queued_task, monkeypatch):
    assert await real_database.claim_task("task-1", "agent-1", expected_attempt=0)
    assert await real_database.complete_task("task-1", {"response": "Evidence"}, expected_attempt=0)
    broadcaster = SimpleNamespace(broadcast_team_event=AsyncMock())
    service = AgentTeamService(real_database, broadcaster)
    accepted = await service.mutate_durable_task(
        workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
        task_id="task-1", revision=0, operation="accept",
    )
    assert accepted["status"] == "accepted"
    broadcaster.broadcast_team_event.assert_awaited_once()
    broadcaster.broadcast_team_event.reset_mock()
    before = await real_database.get_durable_team_task("team-1", "task-1")
    transition = AsyncMock(wraps=real_database.transition_team_task)
    monkeypatch.setattr(real_database, "transition_team_task", transition)

    with pytest.raises(TaskMutationConflict) as caught:
        await service.mutate_durable_task(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
            task_id="task-1", revision=accepted["revision"], operation="accept",
        )

    message = str(caught.value)
    assert "status=accepted" in message
    assert "allowed_actions=[]" in message
    assert await real_database.get_durable_team_task("team-1", "task-1") == before
    transition.assert_awaited_once()
    broadcaster.broadcast_team_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_task_disappearance_after_failed_transition_reports_scoped_not_found():
    database = _database()
    database.get_durable_team_task.side_effect = [
        {"id": "task-1", "status": "queued", "revision": 0, "current_attempt": 0, "retry_count": 0},
        None,
    ]
    database.transition_team_task.return_value = None
    broadcaster = SimpleNamespace(broadcast_team_event=AsyncMock())

    with pytest.raises(TaskMutationConflict, match="Cannot modify task 'task-1': task not found in this lead execution"):
        await AgentTeamService(database, broadcaster).mutate_durable_task(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
            task_id="task-1", revision=0, operation="modify", title="Changed",
        )

    assert database.get_durable_team_task.await_args_list == [call("team-1", "task-1"), call("team-1", "task-1")]
    database.transition_team_task.assert_awaited_once()
    database.create_durable_team_task.assert_not_awaited()
    broadcaster.broadcast_team_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_stage", ["transition", "refresh"])
async def test_database_errors_remain_unexpected(failing_stage):
    database = _database()
    task = {"id": "task-1", "status": "queued", "revision": 0, "current_attempt": 0, "retry_count": 0}
    failure = RuntimeError("Database unavailable")
    if failing_stage == "transition":
        database.get_durable_team_task.return_value = task
        database.transition_team_task.side_effect = failure
    else:
        database.get_durable_team_task.side_effect = [task, failure]
        database.transition_team_task.return_value = None
    broadcaster = SimpleNamespace(broadcast_team_event=AsyncMock())

    with pytest.raises(RuntimeError) as caught:
        await AgentTeamService(database, broadcaster).mutate_durable_task(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
            task_id="task-1", revision=0, operation="modify", title="Changed",
        )

    assert caught.value is failure
    assert database.get_durable_team_task.await_count == (1 if failing_stage == "transition" else 2)
    database.transition_team_task.assert_awaited_once()
    database.create_durable_team_task.assert_not_awaited()
    broadcaster.broadcast_team_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_finish_requires_every_task_to_be_reviewed():
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock
    database = _database()
    result_job = MagicMock()
    result_job.scalar_one_or_none.return_value = None
    result_tasks = MagicMock()
    result_tasks.scalars.return_value = [SimpleNamespace(status="submitted")]
    session = SimpleNamespace(execute=AsyncMock(side_effect=[result_job, result_tasks]))
    @asynccontextmanager
    async def reserved():
        yield session
    database.reserved_session = reserved
    service = AgentTeamService(database)

    with pytest.raises(ValueError, match="unresolved"):
        await service.finish_durable_team(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1"
        )


@pytest.mark.asyncio
async def test_task_history_keeps_accepted_tasks_from_prior_executions():
    database = _database()
    database.list_team_executions.return_value = [
        {"team_id": "team-new", "execution_id": "exec-new"},
        {"team_id": "team-old", "execution_id": "exec-old"},
    ]
    database.get_team_tasks.side_effect = [
        [{"id": "current", "status": "running"}],
        [{"id": "historical", "status": "accepted", "result": {"response": "done"}}],
    ]

    tasks = await AgentTeamService(database).list_durable_task_history(
        workflow_id="wf-1", team_lead_node_id="lead-1"
    )

    assert [task["id"] for task in tasks] == ["current", "historical"]
    assert tasks[1]["team_execution_id"] == "exec-old"
