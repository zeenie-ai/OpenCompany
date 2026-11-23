"""App and schedule intakes keep their mission, reviewed content and original
route; an email the wake rule says is not the employee's ends before it
becomes a job (services/employees/triage.py)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from models.agent_instances import AgentInstance
from models.employees import EmployeeJob
from services.parameter_resolver import ParameterResolver
from services.temporal.agent_instance_workflow import bootstrap_instance, claim_instance
from services.temporal.trigger_listener_workflow import _build_run_graph
from tests.services.employees.test_team_plan_admission import hierarchical_inbox_assistant
from tests.temporal.test_employee_hierarchy_runtime import hierarchy

RULE = "a customer asks about an order"
SCHEDULE = {"kind": "schedule", "every": "day", "at": "08:00"}


@pytest.fixture
def hierarchy_graph(request):
    trigger = SCHEDULE if request.param == "schedule" else None
    return hierarchical_inbox_assistant(trigger, wake_rule=RULE if request.param == "gmail_rule" else "")


@pytest.fixture
def judge(monkeypatch):
    """The judgment service, replaced: it answers ``judge.probability`` for
    the wake question, or nothing when that is None."""
    from services.judgment import Answer, Judgment

    state = SimpleNamespace(probability=0.05, calls=0)

    async def answer(text, questions, **kwargs):
        state.calls += 1
        if state.probability is None:
            return Judgment(available=False, reason="not_connected")
        return Judgment(available=True, answers={"wake": Answer(type="noul", probability=state.probability)},
                        provider="typesafe", model="jev-1.14.0", calibrated=True)

    monkeypatch.setattr("services.judgment.judge", answer)
    return state


def email_event(db, hierarchy, monkeypatch):
    """The bootstrap context of a new email reaching the employee's lead."""
    from core.container import container

    built, control, _ = hierarchy
    graph, roles = control.graph_snapshot, built.node_roles
    trigger, chat = roles["trigger"], roles["talk_agent"]
    resolver = ParameterResolver(db, AsyncMock(return_value=None))
    monkeypatch.setattr(container, "workflow_service", lambda: SimpleNamespace(_param_resolver=resolver))
    output = {"from": "customer@example.com", "subject": "Newsletter", "body_text": "Our spring sale starts Monday.",
              "body": "Our spring sale starts Monday.", "thread_id": "thread-21"}
    nodes, edges = _build_run_graph(trigger_node_id=trigger, trigger_output=output, nodes=graph["nodes"], edges=graph["edges"])
    return built, {"node_id": chat, "node_type": "ai_employee", "nodes": nodes, "edges": edges,
                   "outputs": {trigger: output}, "workflow_id": "7", "execution_id": "event-21",
                   "root_execution_id": "event-21", "session_id": "7", "user_id": "owner", "generation": 1,
                   "data_scope_id": control.data_scope_id, "controller_workflow_id": control.controller_workflow_id,
                   "agent_lifecycle_version": 1, "agent_protocol_version": 1, "parameter_snapshot": graph["parameters"]}


async def jobs_and_instances(db):
    async with db.get_session() as session:
        jobs = (await session.execute(select(EmployeeJob))).scalars().all()
        instances = (await session.execute(select(AgentInstance))).scalars().all()
    return jobs, instances


@pytest.mark.parametrize("hierarchy_graph", ["gmail_rule"], indirect=True)
async def test_an_email_the_wake_rule_does_not_match_ends_without_a_job(real_database, hierarchy, monkeypatch, judge):
    built, context = email_event(real_database, hierarchy, monkeypatch)
    assert built.team_plan["wake_rule"] == RULE

    ended = await bootstrap_instance(context)

    assert ended == {"applicable": False, "review_event": True, "result": {
        "success": True, "status": "event_not_matched", "instance_managed": True, "probability": 0.05}}
    assert await jobs_and_instances(real_database) == ([], [])
    assert judge.calls == 1


@pytest.mark.parametrize("hierarchy_graph", ["gmail_rule"], indirect=True)
@pytest.mark.parametrize("probability", [0.9, None], ids=["the rule may match", "no judgment"])
async def test_an_email_the_rule_may_match_or_that_cannot_be_judged_is_admitted(real_database, hierarchy, monkeypatch, judge, probability):
    judge.probability = probability
    _, context = email_event(real_database, hierarchy, monkeypatch)

    started = await bootstrap_instance(context)

    assert started["applicable"] is True
    jobs, _ = await jobs_and_instances(real_database)
    assert len(jobs) == 1


@pytest.mark.parametrize("hierarchy_graph", ["gmail"], indirect=True)
async def test_with_no_wake_rule_nothing_is_judged(real_database, hierarchy, monkeypatch, judge):
    built, context = email_event(real_database, hierarchy, monkeypatch)
    assert "wake_rule" not in built.team_plan

    started = await bootstrap_instance(context)

    assert started["applicable"] is True
    assert judge.calls == 0


@pytest.mark.parametrize("hierarchy_graph", ["gmail_rule"], indirect=True)
async def test_an_admitted_email_is_never_dropped_by_a_retried_bootstrap(real_database, hierarchy, monkeypatch, judge):
    judge.probability = 0.9
    _, context = email_event(real_database, hierarchy, monkeypatch)
    started = await bootstrap_instance(context)

    judge.probability = 0.05
    again = await bootstrap_instance(context)

    assert again["instance_id"] == started["instance_id"]
    assert judge.calls == 1


def test_a_schedule_hire_keeps_no_wake_rule():
    built = hierarchical_inbox_assistant(SCHEDULE, wake_rule=RULE)

    assert "wake_rule" not in built.team_plan


@pytest.mark.parametrize("hierarchy_graph", ["gmail", "schedule"], indirect=True)
async def test_triggered_mission_is_admitted_once_with_its_original_destination(real_database, hierarchy, monkeypatch):
    from core.container import container

    built, control, _ = hierarchy
    db, graph = real_database, control.graph_snapshot
    roles = built.node_roles
    trigger, chat = roles["trigger"], roles["talk_agent"]
    resolver = ParameterResolver(db, AsyncMock(return_value=None))
    monkeypatch.setattr(container, "workflow_service", lambda: SimpleNamespace(_param_resolver=resolver))
    email = built.trigger["kind"] == "app_event"
    output = ({"from": "customer@example.com", "subject": "Quarterly update", "body_text": "How many orders?",
               "body": "How many orders?", "thread_id": "thread-19"} if email else {"timestamp": "2030-02-01T08:00:00Z"})
    nodes, edges = _build_run_graph(trigger_node_id=trigger, trigger_output=output, nodes=graph["nodes"], edges=graph["edges"])
    assert roles["talk_reply"] not in {node["id"] for node in nodes}
    context = {"node_id": chat, "node_type": "ai_employee", "nodes": nodes, "edges": edges,
               "outputs": {trigger: output}, "workflow_id": "7", "execution_id": "event-19",
               "root_execution_id": "event-19", "session_id": "7", "user_id": "owner", "generation": 1,
               "data_scope_id": control.data_scope_id, "controller_workflow_id": control.controller_workflow_id,
               "agent_lifecycle_version": 1, "agent_protocol_version": 1, "parameter_snapshot": graph["parameters"]}
    started = await bootstrap_instance(context)
    assert started["applicable"] and (await bootstrap_instance(context))["instance_id"] == started["instance_id"]
    handoff = await claim_instance({"instance_id": started["instance_id"], "request_id": "handoff"})
    assert handoff["result"]["task_id"]
    async with db.get_session() as session:
        [job] = (await session.execute(select(EmployeeJob))).scalars().all()
    assert job.lead_node_id == chat
    assert job.source["outputs"][trigger] == output
    assert job.delivery["node_ids"] == built.team_plan["delivery_node_ids"]
    assert roles["job_reply"] not in job.delivery["node_ids"]
    assert not job.source.get("chat_context")
    reviewed = "There were 14 orders."
    outputs = {**job.source["outputs"], chat: {"response": reviewed}}
    if email:
        assert "How many orders?" in job.mission and "Quarterly update" in job.mission
        destination = roles["gate"]
        assert destination in job.delivery["node_ids"]
        gate = await resolver.resolve(job.source["parameters"][destination], destination, job.source["nodes"],
                                      job.source["edges"], "7", run_outputs=outputs)
        assert gate["recipient"] == "customer@example.com" and gate["draft"] == reviewed
    else:
        assert "08:00" in job.mission and "routine" in job.mission
        destination = roles["report_post"]
        assert destination in job.delivery["node_ids"]
        report = await resolver.resolve(job.source["parameters"][destination], destination, job.source["nodes"],
                                        job.source["edges"], "7", run_outputs=outputs)
        assert report["message"] == reviewed
