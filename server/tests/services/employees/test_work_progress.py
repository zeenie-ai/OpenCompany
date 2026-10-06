"""Public work snapshots preserve task scope and never expose execution data."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio
import json

import pytest

from models.chat import ChatRun
from models.database import AgentTeam, TeamTask
from models.employees import EmployeeJob
from services.employees.work_progress import MAX_STEPS, work_progress
from services.chat.hub import ChatRunHub, reset_chat_hub_for_tests

NOW = datetime.now(timezone.utc)


def workflow(workflow_id="employee", nodes=None):
    return SimpleNamespace(id=workflow_id, data={"nodes": nodes or [
        {"id": "lead", "type": "aiAgent", "data": {"label": "Maya"}},
        {"id": "research", "type": "aiAgent", "data": {"label": "Research assistant"}},
    ], "edges": []})


@pytest.fixture(autouse=True)
def phase_cache(monkeypatch):
    import services.status_broadcaster as statuses
    cache = {}
    monkeypatch.setattr(statuses, "get_status_broadcaster", lambda: SimpleNamespace(get_node_status=cache.get))
    old = reset_chat_hub_for_tests(ChatRunHub(queue_size=10))
    yield cache
    reset_chat_hub_for_tests(old)


async def add(database, *rows):
    async with database.get_session() as session:
        for row in rows:
            session.add(row)
        await session.commit()


def job(job_id="job", team_id="team", **kwargs):
    return EmployeeJob(id=job_id, workflow_id="employee", origin_execution_id=job_id, lead_node_id="lead", team_id=team_id,
                       state=kwargs.pop("state", "working"), **kwargs)


def task(task_id="task", team_id="team", **kwargs):
    return TeamTask(id=task_id, team_id=team_id, title="Research available options", created_by="lead", assigned_to="research",
                    workflow_id="employee", status=kwargs.pop("status", "running"), **kwargs)


async def test_current_job_tasks_and_labels_are_scoped_and_redacted(real_database):
    await add(real_database,
        AgentTeam(id="team", workflow_id="employee", team_lead_node_id="lead", created_at=NOW - timedelta(minutes=5)),
        AgentTeam(id="old", workflow_id="employee", team_lead_node_id="lead"),
        job(mission="PRIVATE PROMPT", source={"secret": "RAW ARGS"}, result="HIDDEN REASONING"),
        task(started_at=NOW - timedelta(minutes=3), mission="PRIVATE PROMPT", context={"tool_args": "RAW ARGS"}, error="PRIVATE FAILURE"),
        task("old-task", "old"),
    )
    result = await work_progress(real_database, workflow())
    assert result["state"] == "running"
    assert [step["id"] for step in result["steps"]] == ["task:task"]
    assert result["steps"][0]["member"] == "Research assistant"
    assert result["steps"][0]["node_id"] == "research"
    assert datetime.fromisoformat(result["started_at"]) == NOW - timedelta(minutes=5)
    assert datetime.fromisoformat(result["steps"][0]["started_at"]) == NOW - timedelta(minutes=3)
    wire = json.dumps(result)
    assert not any(secret in wire for secret in ["PRIVATE", "RAW ARGS", "HIDDEN REASONING", "old-task"])


async def test_active_job_wins_over_newer_completed_job_and_new_job_never_reuses_old_team(real_database):
    await add(real_database, AgentTeam(id="old", workflow_id="employee", team_lead_node_id="lead"),
              job("working", None, updated_at=NOW - timedelta(minutes=10)),
              job("done", "old", state="delivered", updated_at=NOW), task("old-task", "old"))
    result = await work_progress(real_database, workflow())
    assert result["state"] == "running"
    assert result["steps"] == []


async def test_legacy_team_without_job_uses_existing_task_manager(real_database):
    await add(real_database, AgentTeam(id="team", workflow_id="employee", team_lead_node_id="lead"),
              task(status="blocked"))
    result = await work_progress(real_database, workflow())
    assert result["state"] == "waiting"
    assert result["steps"][0]["status"] == "waiting"


async def test_task_results_bounded_active_first_and_submitted_is_reviewing(real_database):
    await add(real_database, AgentTeam(id="team", workflow_id="employee", team_lead_node_id="lead"), job(),
              *(task(f"done-{i}", status="accepted", created_at=NOW - timedelta(minutes=10)) for i in range(30)),
              task("review", status="submitted", created_at=NOW))
    result = await work_progress(real_database, workflow())
    assert len(result["steps"]) == MAX_STEPS
    assert result["truncated"]
    assert result["state"] == "reviewing"
    assert result["steps"][0]["id"] == "task:review"
    assert result["steps"][0]["status"] == "reviewing"


async def test_foreign_team_cannot_supply_tasks_even_when_job_has_wrong_team(real_database):
    await add(real_database, AgentTeam(id="foreign", workflow_id="other", team_lead_node_id="lead"),
              job(team_id="foreign"), task("foreign-task", "foreign"))
    assert (await work_progress(real_database, workflow()))["steps"] == []


async def test_scheduled_legacy_work_reads_safe_live_phase_and_monotonic_time(real_database, phase_cache):
    phase_cache["lead"] = {"workflow_id": "employee", "status": "executing", "timestamp": asyncio.get_running_loop().time() - 75,
                           "data": {"phase": "invoking_llm", "message": "PRIVATE THOUGHT", "prompt": "PRIVATE PROMPT", "tool_args": "RAW ARGS"}}
    phase_cache["research"] = {"workflow_id": "other", "status": "executing", "data": {"phase": "executing_tool"}}
    result = await work_progress(real_database, workflow())
    assert result["state"] == "running"
    assert len(result["steps"]) == 1
    assert result["steps"][0]["label"] == "Thinking through the next step"
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(result["updated_at"])).total_seconds()
    assert 74 <= age <= 80
    assert "PRIVATE" not in json.dumps(result) and "RAW ARGS" not in json.dumps(result)


async def test_running_generation_nodes_survive_pending_graph_changes(real_database, phase_cache):
    phase_cache["lead"] = {"workflow_id": "employee", "status": "executing", "timestamp": asyncio.get_running_loop().time(), "data": {}}
    control = SimpleNamespace(status="running", graph_snapshot=workflow().data)
    changed = workflow(nodes=[{"id": "new", "type": "aiAgent", "data": {"label": "Replacement"}}])
    result = await work_progress(real_database, changed, control=control)
    assert result["steps"][0]["node_id"] == "lead"
    assert result["steps"][0]["member"] == "Maya"


async def test_live_chat_tool_steps_survive_reload_without_exposing_details(real_database):
    await add(real_database, ChatRun(run_id="run", session_id="session", workflow_id="employee", state="running",
              started_at=NOW - timedelta(minutes=2), steps=[{"step_id": "search", "name": "Searched the web", "state": "done",
                  "detail": "PRIVATE OUTPUT", "narration": "PRIVATE THOUGHT", "duration_ms": 20}], options={"prompt": "PRIVATE PROMPT"}))
    result = await work_progress(real_database, workflow())
    assert result["state"] == "running"
    assert [step["label"] for step in result["steps"]] == ["Answering your message", "Searched the web"]
    assert result["steps"][1]["status"] == "done"
    assert "PRIVATE" not in json.dumps(result)


async def test_approval_and_browser_blockers_are_plain_and_idle_has_no_progress(real_database):
    assert await work_progress(real_database, workflow()) is None
    approval = await work_progress(real_database, workflow(), pending_approvals=1)
    assert approval["state"] == "waiting"
    assert approval["steps"][0]["label"] == "Waiting for your approval"
    browser = await work_progress(real_database, workflow(), browser_request={"node_id": "browser", "reason": "RAW URL"})
    assert browser["steps"][0]["node_id"] == "browser"
    assert "RAW URL" not in json.dumps(browser)


async def test_queued_job_has_persisted_elapsed_timestamp_without_team(real_database):
    await add(real_database, job(team_id=None, state="queued", updated_at=NOW - timedelta(minutes=2)))
    result = await work_progress(real_database, workflow())
    assert result["state"] == "queued"
    assert datetime.fromisoformat(result["started_at"]) == NOW - timedelta(minutes=2)


async def test_paused_work_does_not_look_like_a_slow_active_employee(real_database, phase_cache):
    phase_cache["lead"] = {"workflow_id": "employee", "status": "executing", "data": {"phase": "invoking_llm"}}
    result = await work_progress(real_database, workflow(), control=SimpleNamespace(status="paused"))
    assert result["state"] == "waiting"
    assert result["message"] == "Work is paused. Resume when you are ready."
    assert result["steps"][0]["status"] == "waiting"


async def test_listening_triggers_do_not_count_as_current_work(real_database, phase_cache):
    nodes = [{"id": "clock", "type": "cronScheduler", "data": {"label": "Schedule"}}]
    phase_cache["clock"] = {"workflow_id": "employee", "status": "waiting", "data": {}}
    assert await work_progress(real_database, workflow(nodes=nodes)) is None
    phase_cache["clock"]["status"] = "executing"
    assert await work_progress(real_database, workflow(nodes=nodes)) is None


@pytest.mark.parametrize("job_state", ["failed", "cancelled", "delivered"])
async def test_terminal_job_is_not_overwritten_by_stale_live_cache(real_database, phase_cache, job_state):
    await add(real_database, job(state=job_state, team_id=None))
    phase_cache["lead"] = {"workflow_id": "employee", "status": "executing", "data": {"phase": "invoking_llm"}}
    result = await work_progress(real_database, workflow())
    assert result["state"] == {"delivered": "done"}.get(job_state, job_state)
    assert result["steps"] == []


async def test_uncertain_delivery_remains_waiting_despite_cached_activity(real_database, phase_cache):
    await add(real_database, job(state="delivering", team_id=None))
    phase_cache["lead"] = {"workflow_id": "employee", "status": "executing", "data": {}}
    result = await work_progress(real_database, workflow(), job_status={"state": "delivery_needs_review", "message": "Check whether the result arrived."})
    assert result["state"] == "waiting"
    assert result["message"] == "Check whether the result arrived."


async def test_callable_alias_resolves_only_unique_registered_tool(real_database, phase_cache, monkeypatch):
    import services.node_registry as registry
    tool = SimpleNamespace(tool_name="search_connected_app", display_name="Calendar")
    monkeypatch.setattr(registry, "registered_node_classes", lambda: {"calendar": tool})
    phase_cache["lead"] = {"workflow_id": "employee", "status": "executing", "data": {"phase": "executing_tool", "tool_name": "search_connected_app"}}
    result = await work_progress(real_database, workflow())
    assert result["steps"][0]["label"] == "Using Calendar"
    monkeypatch.setattr(registry, "registered_node_classes", lambda: {"calendar": tool, "other": tool})
    result = await work_progress(real_database, workflow())
    assert result["steps"][0]["label"] == "Using a connected app"


async def test_ledger_failure_reports_unavailable_without_breaking_controls():
    def unavailable():
        raise RuntimeError("PRIVATE DATABASE ERROR")
    result = await work_progress(SimpleNamespace(get_session=unavailable), workflow())
    assert result["state"] == "unavailable"
    assert "could not be loaded" in result["message"]
    assert "PRIVATE" not in json.dumps(result)


async def test_employee_detail_preserves_job_progress_and_adds_work_progress(real_database, monkeypatch):
    from unittest.mock import AsyncMock
    from models.employees import Employee
    from services.employees import summaries
    from services.employees.jobs import job_progress

    wf = workflow()
    await real_database.save_workflow(workflow_id=wf.id, name="Maya", slug="progress-maya", data=wf.data)
    await add(real_database, Employee(id="employee-row", workflow_id=wf.id, owner_id="owner", team_plan={"version": 1}), job(team_id=None, state="queued"))
    monkeypatch.setattr(summaries, "_summary", AsyncMock(return_value={"workflow_id": wf.id, "pending_approvals": 0, "browser_request": None}))
    monkeypatch.setattr(summaries, "Connections", lambda _: SimpleNamespace(has_ai=AsyncMock(return_value=True)))
    result = await summaries.get_employee_summary(real_database, wf.id, auth_service=None)
    assert result["job_progress"] == await job_progress(real_database, wf.id)
    assert result["work_progress"]["state"] == "queued"


async def test_live_chat_in_progress_tool_is_visible_before_it_finishes(real_database):
    from services.chat.hub import get_chat_hub
    await add(real_database, ChatRun(run_id="run", session_id="session", workflow_id="employee", state="running", started_at=NOW))
    get_chat_hub().publish(run_id="run", session_id="session", workflow_id="employee", suffix="step.started",
                           fields={"step_id": "search", "step_name": "Checking your calendar"})
    result = await work_progress(real_database, workflow())
    assert result["steps"][-1]["label"] == "Checking your calendar"
    assert result["steps"][-1]["status"] == "running"


async def test_native_member_label_and_requested_cancellation_are_preserved(real_database):
    from models.database import TeamMember
    await add(real_database, AgentTeam(id="team", workflow_id="employee", team_lead_node_id="lead"),
              TeamMember(team_id="team", agent_node_id="research", agent_type="aiAgent", agent_label="Research assistant"),
              task(cancellation_requested=True))
    result = await work_progress(real_database, workflow(nodes=[{"id": "lead", "type": "aiAgent", "data": {"label": "Maya"}}]))
    assert result["state"] == "stopping"
    assert result["steps"][0]["status"] == "stopping"
    assert result["steps"][0]["member"] == "Research assistant"


async def test_old_generation_cache_is_not_current_activity(real_database, phase_cache):
    phase_cache["lead"] = {"workflow_id": "employee", "status": "executing", "data": {"generation": 1}}
    assert await work_progress(real_database, workflow(), control=SimpleNamespace(status="running", generation=2)) is None


@pytest.mark.parametrize(("phase", "label", "status"), [
    ("retry_wait", "Waiting to try again", "waiting"),
    ("building_tools", "Getting ready", "running"),
    ("loading_memory", "Loading saved context", "running"),
    ("compacting_context", "Organizing saved context", "running"),
])
async def test_known_slow_phases_explain_work_without_runtime_internals(real_database, phase_cache, phase, label, status):
    phase_cache["lead"] = {"workflow_id": "employee", "status": "executing", "data": {"phase": phase, "error": "PRIVATE ERROR"}}
    result = await work_progress(real_database, workflow())
    assert result["state"] == status
    assert result["steps"][0]["label"] == label
    assert "PRIVATE" not in json.dumps(result)
