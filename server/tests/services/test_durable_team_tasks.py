from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent_team import AgentTeamService


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


@pytest.mark.asyncio
async def test_stale_revision_is_rejected():
    database = _database()
    database.get_durable_team_task.return_value = {
        "id": "task-1", "status": "queued", "revision": 2,
        "current_attempt": 0, "retry_count": 0,
    }
    database.transition_team_task.return_value = None
    service = AgentTeamService(database)

    with pytest.raises(ValueError, match="refresh and retry"):
        await service.mutate_durable_task(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1",
            task_id="task-1", revision=1, operation="cancel", reason="Changed plan",
        )


@pytest.mark.asyncio
async def test_finish_requires_every_task_to_be_reviewed():
    database = _database()
    database.get_team_tasks.return_value = [{"id": "task-1", "status": "submitted"}]
    service = AgentTeamService(database)

    with pytest.raises(ValueError, match="unresolved"):
        await service.finish_durable_team(
            workflow_id="wf-1", team_lead_node_id="lead-1", execution_id="exec-1"
        )
