from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.agent_team import AgentTeamService


@pytest.mark.asyncio
async def test_losing_task_claim_does_not_mark_member_working():
    database = SimpleNamespace(
        claim_task=AsyncMock(return_value=None),
        update_member_status=AsyncMock(),
    )
    service = AgentTeamService(database=database, broadcaster=None)

    result = await service.claim_task("team-1", "task-1", "agent-loser")

    assert result is None
    database.update_member_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_winning_task_claim_marks_only_the_winner_working():
    database = SimpleNamespace(
        claim_task=AsyncMock(
            return_value={"id": "task-1", "assigned_to": "agent-winner"}
        ),
        update_member_status=AsyncMock(),
    )
    service = AgentTeamService(database=database, broadcaster=None)

    result = await service.claim_task("team-1", "task-1", "agent-winner")

    assert result["assigned_to"] == "agent-winner"
    database.update_member_status.assert_awaited_once_with(
        "team-1", "agent-winner", "working"
    )
