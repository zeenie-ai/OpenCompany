"""agent.cancel_delegation answers ``absent`` for a task that has no row (1b.20).

The cancel retries without limit (DELEGATION_CLEANUP_RETRY), so a cancel that
comes before the task's row was written must answer instead of failing for ever.
"""

from unittest.mock import AsyncMock

import pytest

from models.database import AgentTeam
from services.agent_team import AgentTeamService
from services.temporal.agent_activities import cancel_agent_delegation

PAYLOAD = {
    "team_id": "team",
    "team_task_id": "task",
    "child_agent_node_id": "worker",
    "parent_agent_node_id": "lead",
    "reason": "Stopped",
    "terminal_event_id": "task:cancelled",
}


@pytest.fixture
async def database(real_database, monkeypatch):
    monkeypatch.setattr("services.agent_team._service", AgentTeamService(real_database))
    async with real_database.reserved_session() as session:
        session.add(AgentTeam(id="team", workflow_id="wf", team_lead_node_id="lead", execution_id="origin"))
        await session.commit()
    return real_database


async def queued_task(database):
    await database.create_durable_team_task(
        id="task", team_id="team", title="Work", created_by="lead", assigned_to="worker", status="queued"
    )


async def test_a_task_with_no_row_answers_absent(database):
    for _ in range(2):  # a retry answers the same
        answer = await cancel_agent_delegation(dict(PAYLOAD))
        assert (answer["status"], answer["cancelled"], answer["cancellation_applied"]) == ("absent", False, False)
    assert await database.get_agent_messages("team") == []


async def test_a_queued_task_is_cancelled_once(database):
    await queued_task(database)
    first = await cancel_agent_delegation(dict(PAYLOAD))
    assert first["status"] == "cancelled" and first["cancellation_applied"]
    again = await cancel_agent_delegation(dict(PAYLOAD))
    assert again["status"] == "cancelled" and not again["cancellation_applied"]
    assert len(await database.get_agent_messages("team")) == 1


async def test_a_failed_cancel_of_an_existing_row_still_raises(database, monkeypatch):
    await queued_task(database)
    monkeypatch.setattr(database, "cancel_team_task", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="Failed to cancel"):
        await cancel_agent_delegation(dict(PAYLOAD))
