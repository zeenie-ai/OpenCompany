"""Future snapshots change without erasing an employee's runtime data."""
from copy import deepcopy
import asyncio
from datetime import datetime, timedelta, timezone
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlmodel import select
from models.agent_context import AgentConversation
from models.database import AgentTeam, TeamTask, WorkflowControlExecution
from models.employees import EmployeeApply, EmployeeJob
from services.deployment.state import DeploymentState
from services.employees import safe_apply

REAL_ACTIVE = safe_apply._active


class EmptyAsync:
    def __aiter__(self): return self
    async def __anext__(self): raise StopAsyncIteration


@pytest.fixture
async def harness(real_database, monkeypatch):
    import core.container as container_module
    import services.deployment.handlers as handlers
    import services.workflow_validator as validator
    before = {"owner_id": "owner", "graphVersion": 2, "nodes": [{"id": "7:aiAgent:1", "type": "aiAgent", "data": {"label": "Worker"}}], "edges": []}
    after = {**deepcopy(before), "nodes": [*before["nodes"], {"id": "7:calculatorTool:1", "type": "calculatorTool", "data": {"label": "Calculate"}}]}
    await real_database.save_workflow("7", "Maya", "Maya_7", after)
    async with real_database.get_session() as session:
        session.add(WorkflowControlExecution(id="control", workflow_id="7", generation=1, execution_id="execution", root_execution_id="execution", data_scope_id="scope", status="running", revision=1, idempotency_key="start", graph_hash="old", graph_snapshot=before))
        session.add(AgentConversation(workflow_id="7", generation=1, agent_node_id="7:aiAgent:1", messages=[{"role": "user", "content": "Remember this"}]))
        await session.commit()
    state = SimpleNamespace(updates=[], update_ids=[], active=False, fail=False, paused=False)
    class Handle:
        async def execute_update(self, name, argument, **kwargs):
            state.updates.append((name, deepcopy(argument)))
            state.update_ids.append((name, deepcopy(argument), kwargs.get("id")))
            if name == "replace_graph" and state.fail:
                state.fail = False
                raise RuntimeError("handoff failed")
            return {"applied": True}
    class Manager:
        def __init__(self):
            self._deployments = {"7": DeploymentState("deployment", "7", True, before["nodes"], [], "scope")}
            self._paused_events = {"7": [("trigger", {"id": "accepted"})]}
            self._active_runs = {}
        def pause(self, _workflow_id): state.paused = True
        async def resume(self, _workflow_id): state.paused = False
    manager = Manager()
    client = SimpleNamespace(list_schedules=AsyncMock(return_value=EmptyAsync()))
    service = SimpleNamespace(_get_deployment_manager=lambda: manager)
    container = SimpleNamespace(settings=lambda: SimpleNamespace(temporal_enabled=True), temporal_client=lambda: SimpleNamespace(client=client), workflow_service=lambda: service)
    monkeypatch.setattr(container_module, "container", container)
    monkeypatch.setattr(handlers, "_controller_handle", lambda _control: Handle())
    state.real_cron_pause = handlers._set_cron_pause
    monkeypatch.setattr(handlers, "_set_cron_pause", AsyncMock(return_value=0))
    monkeypatch.setattr(validator, "validate_workflow", AsyncMock(return_value={"errors": []}))
    monkeypatch.setattr(safe_apply, "_active", AsyncMock(side_effect=lambda *_args: state.active))
    import services.employees.events as events
    monkeypatch.setattr(events, "employee_changed_now", lambda *_args: None)
    state.database, state.before, state.after, state.manager, state.client = real_database, before, after, manager, client
    state.operational = {**deepcopy(after), "parameters": {node["id"]: {} for node in after["nodes"]}}
    state.container, state.Handle = container, Handle
    return state


async def test_apply_preserves_context_namespace_and_accepted_events(harness):
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="one")
    assert result == {"success": True, "activation_state": "running"}
    control = await harness.database.get_latest_workflow_control("7")
    assert control.graph_snapshot == harness.operational and control.generation == 1 and control.data_scope_id == "scope"
    assert harness.manager._paused_events["7"] == [("trigger", {"id": "accepted"})]
    async with harness.database.get_session() as session:
        conversation = (await session.execute(select(AgentConversation))).scalar_one()
        assert conversation.messages[0]["content"] == "Remember this"
    assert [name for name, _ in harness.updates] == ["pause_admissions", "replace_graph", "set_control_state"]


async def test_active_work_defers_handoff_and_recovers(harness):
    harness.active = True
    waiting = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="one")
    assert waiting["activation_state"] == "waiting"
    assert (await harness.database.get_latest_workflow_control("7")).graph_snapshot == harness.before
    harness.active = False
    await safe_apply.recover_applies(harness.database)
    assert (await harness.database.get_latest_workflow_control("7")).graph_snapshot == harness.operational


async def test_paused_employee_stays_paused(harness):
    async with harness.database.get_session() as session:
        control = await session.get(WorkflowControlExecution, "control")
        control.status = "paused"
        await session.commit()
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="one")
    assert result["activation_state"] == "paused" and harness.paused
    assert not any(name == "set_control_state" for name, _ in harness.updates)


async def test_failed_handoff_keeps_operational_snapshot(harness):
    harness.fail = True
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="one")
    assert result["error"] == "apply_failed"
    control = await harness.database.get_latest_workflow_control("7")
    assert control.graph_snapshot == harness.before and control.data_scope_id == "scope"
    assert (await harness.database.get_workflow("7")).data == harness.after
    assert harness.manager._deployments["7"].nodes == harness.before["nodes"]


async def test_foreign_owner_cannot_apply(harness):
    assert (await safe_apply.apply_saved_changes(harness.database, "7", owner_id="other", key="one"))["error"] == "not_found"
    assert harness.updates == []


class ScheduleHandle:
    def __init__(self, schedule):
        self.schedule = schedule
        self.updates = []

    async def describe(self):
        return SimpleNamespace(schedule=self.schedule)

    async def update(self, updater):
        from inspect import isawaitable
        from temporalio.client import ScheduleUpdateInput
        result = updater(ScheduleUpdateInput(description=SimpleNamespace(schedule=self.schedule)))
        if isawaitable(result):
            result = await result
        self.updates.append(result.schedule)
        self.schedule = result.schedule

    async def pause(self, *, note):
        self.schedule = replace(self.schedule, state=replace(self.schedule.state, paused=True, note=note))

    async def unpause(self, *, note):
        self.schedule = replace(self.schedule, state=replace(self.schedule.state, paused=False, note=note))


async def encoded_schedule(harness):
    """Use SDK Payloads as describe() returns, not already decoded test dictionaries."""
    from temporalio.client import Schedule, ScheduleActionStartWorkflow, ScheduleSpec, ScheduleState, SchedulePolicy
    from temporalio.converter import DataConverter
    converter = DataConverter.default
    arguments = await converter.encode([{"nodes": harness.before["nodes"], "edges": [], "owner_id": "owner", "recipient": "original"}, {"retained": "second argument"}, 17])
    schedule = Schedule(action=ScheduleActionStartWorkflow("EventWorkflow", args=arguments, id="scheduled-job", task_queue="worker", memo={"owner": "owner"}),
        spec=ScheduleSpec(cron_expressions=["15 9 * * 1-5"], time_zone_name="Asia/Kolkata", jitter=timedelta(seconds=7)),
        state=ScheduleState(paused=True, note="Owner paused this schedule", limited_actions=True, remaining_actions=3),
        policy=SchedulePolicy(catchup_window=timedelta(hours=2), pause_on_failure=True))
    handle = ScheduleHandle(schedule)
    class ScheduleList:
        def __aiter__(self):
            async def items():
                yield SimpleNamespace(id="owner-schedule")
            return items()
    harness.client.data_converter = converter
    harness.client.list_schedules = AsyncMock(return_value=ScheduleList())
    harness.client.get_schedule_handle = lambda _id: handle
    return schedule, handle


async def test_sdk_encoded_schedule_keeps_spec_pause_policy_and_extra_arguments(harness):
    before, schedule = await encoded_schedule(harness)
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="encoded")
    assert result["success"] is True
    after = schedule.schedule
    assert after.spec == before.spec and after.state == before.state and after.policy == before.policy
    assert after.action.args[0] == {"nodes": harness.after["nodes"], "edges": harness.after["edges"], "owner_id": "owner", "recipient": "original", "parameter_snapshot": harness.operational["parameters"]}
    assert after.action.args[1:] == [{"retained": "second argument"}, 17]
    assert after.action.id == before.action.id and after.action.task_queue == before.action.task_queue
    assert after.action.memo == before.action.memo


async def test_failed_handoff_rolls_back_real_encoded_schedule(harness):
    before, schedule = await encoded_schedule(harness)
    harness.fail = True
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="rollback")
    assert result["error"] == "apply_failed"
    assert len(schedule.updates) >= 2 and schedule.schedule == before
    assert schedule.updates[0].action.args[0]["nodes"] == harness.after["nodes"]
    decoded = await harness.client.data_converter.decode(schedule.schedule.action.args)
    assert decoded[0]["nodes"] == harness.before["nodes"]
    assert decoded[1:] == [{"retained": "second argument"}, 17]


@pytest.mark.parametrize("failure", [False, True])
async def test_handoff_with_real_pause_sweep_preserves_owners_schedule_pause(harness, monkeypatch, failure):
    import services.deployment.handlers as handlers
    before, schedule = await encoded_schedule(harness)
    harness.fail = failure
    monkeypatch.setattr(handlers, "_set_cron_pause", harness.real_cron_pause)
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="owner-pause")
    assert result["success"] is not failure
    assert schedule.schedule.state == before.state
    assert schedule.schedule.spec == before.spec and schedule.schedule.policy == before.policy


async def test_restart_during_drain_keeps_original_schedule_owner_edits(harness, monkeypatch):
    import services.deployment.handlers as handlers
    before, schedule = await encoded_schedule(harness)
    monkeypatch.setattr(handlers, "_set_cron_pause", harness.real_cron_pause)
    harness.active = True
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="drain-pause")
    assert result["activation_state"] == "waiting"
    harness.active = False
    await safe_apply.recover_applies(harness.database)
    assert schedule.schedule.state == before.state
    assert schedule.schedule.spec == before.spec and schedule.schedule.policy == before.policy


async def test_stop_work_cancels_durable_records_but_waits_for_actual_execution_root(harness, monkeypatch):
    import services.chat.ledger as ledger
    monkeypatch.setattr(ledger, "live_runs", AsyncMock(return_value=[]))
    monkeypatch.setattr(safe_apply, "_active", REAL_ACTIVE)
    harness.container.workflow_service().get_deployment_status = lambda _id: {"active_runs": 0}
    roots = {"running": True}
    cancelled = []
    class RunningRoots:
        def __aiter__(self):
            async def items():
                yield SimpleNamespace(id="controller", workflow_type="WorkflowControlWorkflow")
                if roots["running"]:
                    yield SimpleNamespace(id="job-root", workflow_type="EventWorkflow")
            return items()
    async def cancel():
        cancelled.append("job-root")
        # Temporal cancellation is a request; the execution has not closed yet.
    harness.client.list_workflows = lambda **_kwargs: RunningRoots()
    harness.client.get_workflow_handle = lambda _id: SimpleNamespace(cancel=cancel)
    async with harness.database.get_session() as session:
        session.add(AgentTeam(id="team", workflow_id="7", team_lead_node_id="7:aiAgent:1"))
        for status in ("blocked", "queued", "pending", "running", "accepted"):
            session.add(TeamTask(id=f"task-{status}", team_id="team", title="Research", created_by="7:aiAgent:1", workflow_id="7", status=status))
        session.add(EmployeeJob(id="job", workflow_id="7", origin_execution_id="origin", lead_node_id="7:aiAgent:1", team_id="team", state="working"))
        await session.commit()
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="stop", stop_work=True)
    assert result["activation_state"] == "waiting" and cancelled == ["job-root"]
    async with harness.database.get_session() as session:
        assert (await session.get(EmployeeJob, "job")).state == "cancelled"
        assert (await session.get(AgentTeam, "team")).status == "dissolved"
        for status in ("blocked", "queued", "pending", "running"):
            task = await session.get(TeamTask, f"task-{status}")
            assert task.status == "cancelled" and task.cancellation_requested
        assert (await session.get(TeamTask, "task-accepted")).status == "accepted"
    assert (await harness.database.get_latest_workflow_control("7")).graph_snapshot == harness.before
    roots["running"] = False
    await safe_apply.recover_applies(harness.database)
    assert (await harness.database.get_latest_workflow_control("7")).graph_snapshot == harness.operational
    assert harness.manager._paused_events["7"] == [("trigger", {"id": "accepted"})]


async def test_live_claim_serializes_handoffs_and_renews_lease(harness, monkeypatch):
    import services.deployment.handlers as handlers
    entered, release, tick, renewed = (asyncio.Event() for _ in range(4))
    original_sleep = asyncio.sleep
    calls = 0
    async def sleep(seconds):
        nonlocal calls
        if seconds != 30:
            return await original_sleep(seconds)
        calls += 1
        if calls == 1:
            await tick.wait()
        else:
            renewed.set()
            await asyncio.Event().wait()
    monkeypatch.setattr(safe_apply.asyncio, "sleep", sleep)
    class SlowHandle(harness.Handle):
        async def execute_update(self, name, argument, **kwargs):
            result = await super().execute_update(name, argument, **kwargs)
            if name == "replace_graph":
                entered.set()
                await release.wait()
            return result
    monkeypatch.setattr(handlers, "_controller_handle", lambda _control: SlowHandle())
    first = asyncio.create_task(safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="lease"))
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        async with harness.database.reserved_session() as session:
            claim = await session.get(EmployeeApply, "apply:7:lease")
            token = claim.claim_token
            claim.lease_until = datetime.now(timezone.utc) + timedelta(seconds=10)
            await session.commit()
        tick.set()
        await asyncio.wait_for(renewed.wait(), timeout=10)
        async with harness.database.get_session() as session:
            claim = await session.get(EmployeeApply, "apply:7:lease")
            assert claim.claim_token == token
            assert claim.lease_until.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=1)
        retry = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="lease")
        assert retry["activation_state"] == "starting"
        assert (await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="other"))["error"] == "conflict"
        assert sum(name == "replace_graph" for name, _ in harness.updates) == 1
    finally:
        release.set()
        await first


async def test_restart_recovers_expired_applying_lease(harness):
    async with harness.database.reserved_session() as session:
        session.add(EmployeeApply(id="apply:7:expired", workflow_id="7", owner_id="owner", state="applying", resume_after=True,
            snapshot=harness.after, previous=harness.before, claim_token="old-process", lease_until=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await session.commit()
    async with harness.database.get_session() as session:
        assert (await session.get(EmployeeApply, "apply:7:expired")).producer_states == {}
    await safe_apply.recover_applies(harness.database)
    async with harness.database.get_session() as session:
        request = await session.get(EmployeeApply, "apply:7:expired")
        assert request.state == "applied" and request.claim_token is None and request.lease_until is None
    assert (await harness.database.get_latest_workflow_control("7")).graph_snapshot == harness.after


async def test_stale_worker_cannot_commit_or_roll_back_a_new_claim(harness, monkeypatch):
    import services.deployment.handlers as handlers
    entered, release = asyncio.Event(), asyncio.Event()
    class SlowHandle(harness.Handle):
        async def execute_update(self, name, argument, **kwargs):
            result = await super().execute_update(name, argument, **kwargs)
            if name == "replace_graph":
                entered.set()
                await release.wait()
            return result
    monkeypatch.setattr(handlers, "_controller_handle", lambda _control: SlowHandle())
    first = asyncio.create_task(safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="fenced"))
    newer = {**deepcopy(harness.after), "nodes": [*harness.after["nodes"], {"id": "7:clock:1", "type": "clock", "data": {"label": "Newer ability"}}]}
    try:
        await asyncio.wait_for(entered.wait(), timeout=10)
        async with harness.database.reserved_session() as session:
            request = await session.get(EmployeeApply, "apply:7:fenced")
            request.claim_token = "new-process"
            request.lease_until = datetime.now(timezone.utc) + timedelta(minutes=2)
            request.snapshot = newer
            control = await session.get(WorkflowControlExecution, "control")
            control.graph_snapshot = newer
            control.revision += 1
            await session.commit()
        harness.manager._deployments["7"] = replace(harness.manager._deployments["7"], nodes=newer["nodes"])
        release.set()
        result = await first
        assert result["error"] == "conflict"
        assert not any(name == "replace_graph" and argument == harness.before for name, argument in harness.updates)
        async with harness.database.get_session() as session:
            request = await session.get(EmployeeApply, "apply:7:fenced")
            assert request.claim_token == "new-process" and request.state == "applying"
        assert (await harness.database.get_latest_workflow_control("7")).graph_snapshot == newer
        assert harness.manager._deployments["7"].nodes == newer["nodes"]
    finally:
        release.set()
        await first


async def test_failed_same_key_retry_uses_fresh_controller_updates(harness):
    harness.fail = True
    failed = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="retry")
    assert failed["error"] == "apply_failed"
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="retry")
    assert result["success"] is True and result["activation_state"] == "running"
    attempts = [identity for name, graph, identity in harness.update_ids if name == "replace_graph" and graph == harness.operational]
    assert len(attempts) == 2 and attempts[0] != attempts[1]
    pauses = [identity for name, _, identity in harness.update_ids if name == "pause_admissions"]
    assert len(set(pauses)) == len(pauses)
    async with harness.database.get_session() as session:
        request = await session.get(EmployeeApply, "apply:7:retry")
        assert request.state == "applied" and request.claim_token is None


async def test_manual_pause_during_drain_takes_precedence_over_apply_resume(harness):
    harness.active = True
    assert (await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="pause"))["activation_state"] == "waiting"
    async with harness.database.reserved_session() as session:
        current = await session.get(WorkflowControlExecution, "control")
        current.status = "paused"
        current.revision += 1
        await session.commit()
    harness.active = False
    await safe_apply.recover_applies(harness.database)
    async with harness.database.get_session() as session:
        request = await session.get(EmployeeApply, "apply:7:pause")
        assert request.state == "applied" and request.resume_after is False
    control = await harness.database.get_latest_workflow_control("7")
    assert control.status == "paused" and control.graph_snapshot == harness.operational
    assert not any(name == "set_control_state" for name, _ in harness.updates)
    assert harness.paused is True


async def test_claim_is_retained_until_external_resume_finishes(harness, monkeypatch):
    import services.deployment.handlers as handlers
    observed = []
    class ResumeHandle(harness.Handle):
        async def execute_update(self, name, argument, **kwargs):
            if name == "set_control_state":
                async with harness.database.get_session() as session:
                    request = await session.get(EmployeeApply, "apply:7:resume")
                    observed.append((request.state, bool(request.claim_token), request.lease_until is not None))
            return await super().execute_update(name, argument, **kwargs)
    monkeypatch.setattr(handlers, "_controller_handle", lambda _control: ResumeHandle())
    assert (await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="resume"))["success"] is True
    assert observed == [("applying", True, True)]
    async with harness.database.get_session() as session:
        request = await session.get(EmployeeApply, "apply:7:resume")
        assert request.state == "applied" and request.claim_token is None


async def test_rollback_cannot_clear_a_claim_replaced_during_external_resume(harness, monkeypatch):
    import services.deployment.handlers as handlers
    newer = {**deepcopy(harness.operational), "nodes": [*harness.after["nodes"], {"id": "7:clock:1", "type": "clock", "data": {"label": "Newer ability"}}]}
    class RollbackHandle(harness.Handle):
        async def execute_update(self, name, argument, **kwargs):
            result = await super().execute_update(name, argument, **kwargs)
            if name == "set_control_state":
                # The old process resumes after another recovery worker owns the lease.
                async with harness.database.reserved_session() as session:
                    request = await session.get(EmployeeApply, "apply:7:rollback-fenced")
                    request.claim_token = "new-process"
                    request.lease_until = datetime.now(timezone.utc) + timedelta(minutes=2)
                    current = await session.get(WorkflowControlExecution, "control")
                    current.graph_snapshot = newer
                    await session.commit()
                harness.manager._deployments["7"] = replace(harness.manager._deployments["7"], nodes=newer["nodes"])
            return result
    harness.fail = True
    monkeypatch.setattr(handlers, "_controller_handle", lambda _control: RollbackHandle())
    result = await safe_apply.apply_saved_changes(harness.database, "7", owner_id="owner", key="rollback-fenced")
    assert result["error"] == "conflict"
    async with harness.database.get_session() as session:
        request = await session.get(EmployeeApply, "apply:7:rollback-fenced")
        assert request.state == "applying" and request.claim_token == "new-process"
    assert (await harness.database.get_latest_workflow_control("7")).graph_snapshot == newer
    assert harness.manager._deployments["7"].nodes == newer["nodes"]
