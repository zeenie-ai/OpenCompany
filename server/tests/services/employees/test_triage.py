"""Triage at job admission (``services/employees/triage.py``): the "Wake me
when" rule decides, with one judgment, whether an app event is worth a job.
The bootstrap that ends a dropped event is in
``tests/temporal/test_employee_app_hierarchy.py``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.employees import triage
from services.judgment import Answer, Judgment

RULE = "a customer asks about an order"
PLAN = {"version": 3, "lead_node_id": "chat", "intake_trigger_node_id": "gmail", "wake_rule": RULE}
CONTEXT = {
    "_agent_instance_bootstrap": True,
    "node_id": "chat",
    "workflow_id": "wf-7",
    "execution_id": "event-19",
    "session_id": "wf-7",
    "user_id": "owner-1",
}
MISSION = "New email from a customer: When will order 1042 arrive?"


def judged(probability):
    return Judgment(
        available=True,
        answers={"wake": Answer(type="noul", probability=probability)},
        provider="typesafe",
        model="jev-1.14.0",
        calibrated=True,
    )


@pytest.fixture
def judge(monkeypatch):
    """The judgment service, replaced: ``judge.answer`` is what it answers,
    ``judge.calls`` what it was asked."""
    import services.judgment

    fake = SimpleNamespace(answer=judged(0.05), calls=[])

    async def answer(state, questions, **kwargs):
        fake.calls.append(SimpleNamespace(state=state, questions=questions, kwargs=kwargs))
        return fake.answer

    monkeypatch.setattr(services.judgment, "judge", answer)
    return fake


@pytest.fixture
def jobs(monkeypatch):
    """The jobs already admitted, by (workflow, lead, run)."""
    import services.employees.jobs as jobs_module

    admitted = {}

    async def mission(database, *, workflow_id, lead_node_id, execution_id):
        return admitted.get((workflow_id, lead_node_id, execution_id))

    monkeypatch.setattr(jobs_module, "employee_job_mission", mission)
    return admitted


async def drop(context=CONTEXT, plan=PLAN, mission=MISSION, review=None):
    return await triage.dropped_event(object(), context, plan, mission, review)


async def test_an_event_the_rule_does_not_match_is_dropped(judge, jobs):
    assert await drop() == {"probability": 0.05}

    (call,) = judge.calls
    assert call.state == MISSION
    assert call.questions == {
        "wake": {"type": "noul", "description": f"This event calls for the employee's work. The employee works on: {RULE}"}
    }
    assert call.kwargs == {
        "node_id": "chat",
        "workflow_id": "wf-7",
        "session_id": "wf-7",
        "principal": "owner-1",
        "timeout": triage.JUDGE_TIMEOUT_SECONDS,
    }


@pytest.mark.parametrize("probability", [0.9, triage.DROP_BELOW])
async def test_an_event_the_rule_may_match_is_admitted(judge, jobs, probability):
    judge.answer = judged(probability)

    assert await drop() is None


async def test_an_event_is_admitted_when_no_judgment_is_available(judge, jobs):
    judge.answer = Judgment(available=False, reason="timeout", detail="no answer within 15 s")

    assert await drop() is None


@pytest.mark.parametrize(
    "context, plan, mission, review",
    [
        pytest.param(CONTEXT, {**PLAN, "wake_rule": ""}, MISSION, None, id="no rule"),
        pytest.param(CONTEXT, {**PLAN, "wake_rule": "   "}, MISSION, None, id="a blank rule"),
        pytest.param({**CONTEXT, "_agent_instance_bootstrap": False}, PLAN, MISSION, None, id="not the bootstrap"),
        pytest.param(CONTEXT, PLAN, MISSION, {"task_id": "t-1"}, id="a review"),
        pytest.param({**CONTEXT, "node_id": "orchestrator"}, PLAN, MISSION, None, id="not the lead"),
        pytest.param(CONTEXT, {**PLAN, "version": 2}, MISSION, None, id="an older team"),
        pytest.param(CONTEXT, None, MISSION, None, id="no team"),
        pytest.param({**CONTEXT, "employee_job_id": "job-1"}, PLAN, MISSION, None, id="a job already bound"),
        pytest.param({**CONTEXT, "parent_node_id": "chat"}, PLAN, MISSION, None, id="delegated work"),
        pytest.param(CONTEXT, PLAN, "  ", None, id="an empty mission"),
    ],
)
async def test_nothing_is_judged_where_triage_does_not_apply(judge, jobs, context, plan, mission, review):
    assert await drop(context, plan, mission, review) is None
    assert judge.calls == []


async def test_a_job_already_admitted_for_this_run_is_never_dropped(judge, jobs):
    jobs[("wf-7", "chat", "event-19")] = MISSION

    assert await drop() is None
    assert judge.calls == []


def test_the_rule_is_one_line_of_at_most_its_limit():
    assert triage.wake_rule({"wake_rule": "  a customer\n asks   about an order "}) == RULE
    assert len(triage.wake_rule({"wake_rule": "x" * 500})) == triage.RULE_MAX
    assert triage.wake_rule(None) == ""


GMAIL = {"id": "gmail", "type": "googleGmailReceive"}
CRON = {"id": "cron", "type": "cronScheduler"}


@pytest.mark.parametrize(
    "plan, nodes, takes",
    [
        pytest.param(PLAN, [GMAIL], True, id="an app event"),
        pytest.param({**PLAN, "intake_trigger_node_id": "cron"}, [CRON], False, id="a schedule"),
        pytest.param({k: v for k, v in PLAN.items() if k != "intake_trigger_node_id"}, [GMAIL], False, id="messaged by the owner"),
        pytest.param({**PLAN, "version": 2}, [GMAIL], False, id="an older team"),
        pytest.param(PLAN, [], False, id="a trigger no longer in the graph"),
        pytest.param(None, [GMAIL], False, id="no team"),
    ],
)
def test_only_an_employee_an_app_event_starts_takes_a_rule(plan, nodes, takes):
    assert triage.takes_wake_rule(plan, nodes) is takes
