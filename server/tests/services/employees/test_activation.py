"""Saved activation survives restart and uses the original deployment identity."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from models.employees import Employee, EmployeeActivation
from services.employees import activation


@pytest.mark.parametrize("requires_team", [True, False])
async def test_readiness_blocks_teams_but_preserves_legacy_activation_identity(real_database, monkeypatch, requires_team):
    import core.container as container_module
    import services.deployment.handlers as handlers
    import services.employees.summaries as summaries
    import services.employees.start as start
    import services.employees.team_runtime as runtime

    monkeypatch.setattr(container_module, "container", SimpleNamespace(auth_service=lambda: SimpleNamespace()))
    monkeypatch.setattr(summaries, "get_employee_summary", AsyncMock(return_value={"missing_apps": [], "needs_ai": False, "has_team": requires_team}))
    monkeypatch.setattr(start, "heal_agent_models", AsyncMock())
    ready = {"issue": "team_temporal_required"}
    monkeypatch.setattr(runtime, "team_runtime_error", lambda: ready["issue"])
    admit = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(handlers, "start_saved_workflow", admit)
    async with real_database.get_session() as session:
        session.add(Employee(id="employee", workflow_id="7", owner_id="owner", hire_state="ready", team_plan={"version": 1, "lead_node_id": "7:ai_employee:1"} if requires_team else {}))
        session.add(EmployeeActivation(id="hire:owner:stable-request", workflow_id="7", owner_id="owner"))
        await session.commit()
    await activation.activate_pending(real_database)
    async with real_database.get_session() as session:
        saved = await session.get(EmployeeActivation, "hire:owner:stable-request")
        assert saved.state == ("blocked" if requires_team else "running")
        assert saved.detail == ("team_temporal_required" if requires_team else None)
    if requires_team:
        admit.assert_not_awaited()
    else:
        admit.assert_awaited_once_with("7", owner_id="owner", idempotency_key="hire:owner:stable-request")
    ready["issue"] = None
    # A fresh recovery invocation discovers the durable blocked record.
    await activation.activate_pending(real_database)
    admit.assert_awaited_once_with("7", owner_id="owner", idempotency_key="hire:owner:stable-request")
    async with real_database.get_session() as session:
        saved = await session.get(EmployeeActivation, "hire:owner:stable-request")
        assert saved.state == "running" and saved.detail is None
    await activation.activate_pending(real_database)
    assert admit.await_count == 1
