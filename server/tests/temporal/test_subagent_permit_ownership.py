"""A retried durable task must not reuse another runner's released lease."""

from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from models.database import SubagentConcurrencyCounter, SubagentConcurrencyPermit
from services.temporal import agent_activities


PAYLOAD = {"root_execution_id": "root-1", "permit_id": "task-1", "limit": 3, "lease_version": 2}


def _info(workflow_id="runner-1", workflow_run_id="run-1", attempt=1):
    return SimpleNamespace(workflow_id=workflow_id, workflow_run_id=workflow_run_id, activity_id="3", attempt=attempt)


def _lease(info):
    return agent_activities._subagent_lease_id(
        PAYLOAD["permit_id"], info.activity_id, info.attempt,
        workflow_id=info.workflow_id, workflow_run_id=info.workflow_run_id,
    )


def _legacy_lease():
    identity = f"{PAYLOAD['permit_id']}\x003\x001".encode()
    return f"subagent-lease-v2-{hashlib.sha256(identity).hexdigest()}"


def _bind(monkeypatch, database, info):
    from services import agent_team

    service = SimpleNamespace(
        acquire_subagent_permit=database.acquire_subagent_permit,
        release_subagent_permit=database.release_subagent_permit,
    )
    monkeypatch.setattr(agent_team, "get_agent_team_service", lambda: service)
    monkeypatch.setattr(agent_activities.activity, "info", lambda: info)
    return service


async def _state(database):
    async with database.get_session() as session:
        permits = (await session.execute(select(SubagentConcurrencyPermit))).scalars().all()
        counter = (await session.execute(select(SubagentConcurrencyCounter))).scalar_one()
        return {permit.permit_id: permit.status for permit in permits}, counter.active_count


async def test_existing_version_two_request_recovers_from_released_legacy_lease(monkeypatch, real_database):
    old_lease = _legacy_lease()
    assert await real_database.acquire_subagent_permit("root-1", old_lease)
    assert await real_database.release_subagent_permit("root-1", old_lease)
    info = _info()
    _bind(monkeypatch, real_database, info)

    result = await asyncio.wait_for(agent_activities.acquire_subagent_permit(PAYLOAD), timeout=2)

    assert result["lease_version"] == 2
    assert result["lease_id"] == _lease(info)
    assert result["lease_id"] != old_lease
    state, active_count = await _state(real_database)
    assert state == {old_lease: "released", result["lease_id"]: "active"}
    assert active_count == 1


@pytest.mark.parametrize(("new_workflow", "new_run"), [("runner-2", "run-1"), ("runner-1", "run-2")])
async def test_new_runner_or_run_acquires_fresh_lease_for_same_task(monkeypatch, real_database, new_workflow, new_run):
    first_info = _info()
    _bind(monkeypatch, real_database, first_info)
    first = await agent_activities.acquire_subagent_permit(PAYLOAD)
    # Repeated execution of the same physical attempt is idempotent.
    assert (await agent_activities.acquire_subagent_permit(PAYLOAD))["lease_id"] == first["lease_id"]
    assert await real_database.release_subagent_permit("root-1", first["lease_id"])
    next_info = _info(new_workflow, new_run)
    _bind(monkeypatch, real_database, next_info)

    second = await asyncio.wait_for(agent_activities.acquire_subagent_permit(PAYLOAD), timeout=2)

    assert first["lease_id"] != second["lease_id"]
    state, active_count = await _state(real_database)
    assert state[first["lease_id"]] == "released"
    assert state[second["lease_id"]] == "active"
    assert active_count == 1


async def test_activity_retry_reconciles_only_its_own_run(monkeypatch, real_database):
    previous = _lease(_info())
    other_runner = _lease(_info("other-runner", "other-run"))
    legacy = _legacy_lease()
    for lease in (previous, other_runner, legacy):
        assert await real_database.acquire_subagent_permit("root-1", lease)
    info = _info(attempt=2)
    _bind(monkeypatch, real_database, info)

    result = await asyncio.wait_for(agent_activities.acquire_subagent_permit(PAYLOAD), timeout=2)

    state, active_count = await _state(real_database)
    assert state[previous] == "released"
    assert state[other_runner] == state[legacy] == state[result["lease_id"]] == "active"
    assert active_count == 3


async def test_cancellation_compensates_only_current_scoped_lease(monkeypatch, real_database):
    info = _info()
    service = _bind(monkeypatch, real_database, info)

    async def acquire_then_cancel(root_id, permit_id, limit):
        assert await real_database.acquire_subagent_permit(root_id, permit_id, limit)
        raise asyncio.CancelledError

    service.acquire_subagent_permit = acquire_then_cancel
    with pytest.raises(asyncio.CancelledError):
        await agent_activities.acquire_subagent_permit(PAYLOAD)

    state, active_count = await _state(real_database)
    assert state == {_lease(info): "released"}
    assert active_count == 0


async def test_recorded_legacy_lease_result_can_still_be_released(monkeypatch, real_database):
    legacy = _legacy_lease()
    assert await real_database.acquire_subagent_permit("root-1", legacy)
    _bind(monkeypatch, real_database, _info())

    result = await agent_activities.release_subagent_permit({"root_execution_id": "root-1", "permit_id": legacy})

    assert result["released"] is True
    state, active_count = await _state(real_database)
    assert state == {legacy: "released"}
    assert active_count == 0


async def test_queued_heartbeat_identifies_physical_lease_owner(monkeypatch):
    from services import agent_team

    info = _info()
    monkeypatch.setattr(agent_activities.activity, "info", lambda: info)
    heartbeat = []
    monkeypatch.setattr(agent_activities.activity, "heartbeat", heartbeat.append)
    monkeypatch.setattr(agent_activities.asyncio, "sleep", AsyncMock())
    service = SimpleNamespace(acquire_subagent_permit=AsyncMock(side_effect=[False, True]))
    monkeypatch.setattr(agent_team, "get_agent_team_service", lambda: service)

    await agent_activities.acquire_subagent_permit(PAYLOAD)

    assert heartbeat == [{"root_execution_id": "root-1", "permit_id": "task-1", "lease_version": 2,
                          "status": "queued", "lease_id": _lease(info), "attempt": 1,
                          "workflow_id": info.workflow_id, "workflow_run_id": info.workflow_run_id,
                          "activity_id": info.activity_id}]
