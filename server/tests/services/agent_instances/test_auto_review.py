"""Review auto-accept (services/agent_instances/auto_review.py): above the
owner's setting, a calibrated judgment accepts a teammate's submitted work
in the claim, with no model turn, and the review gate hands the reviewer's
task on; anything short of that leaves the review to the reviewer."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from models.agent_instances import AgentInstanceConversation, AgentRun
from models.database import TeamTask
from services.agent_instances import auto_review
from services.judgment import Answer, Judgment
from services.temporal.agent_instance_workflow import turn_context
from tests.services.agent_instances.test_employee_hierarchy import admit, as_actor, finish


@pytest.fixture
def judge(monkeypatch):
    """The judgment service, replaced: it answers ``judge.probability``
    (None: no judgment), and with ``calibrated`` false only the chat-model
    fallback is connected, which a calibrated-only question never reaches."""
    state = SimpleNamespace(probability=0.95, calibrated=True, calls=[])

    async def answer(text, questions, **kwargs):
        state.calls.append(SimpleNamespace(text=text, questions=questions, kwargs=kwargs))
        if state.probability is None or (kwargs.get("calibrated_only") and not state.calibrated):
            return Judgment(available=False, reason="not_connected")
        return Judgment(available=True, answers={"complete": Answer(type="noul", probability=state.probability)},
                        provider="typesafe", model="jev-1.14.0", calibrated=True)

    monkeypatch.setattr("services.judgment.judge", answer)
    return state


async def owner_sets(database, percent):
    assert await database.save_user_settings({"review_auto_accept_percent": percent})


async def submitted_review(database, job_id="auto", *, workers=1):
    """The Orchestrator assigned work to ``workers`` teammates and the first
    submitted it; returns everything, with the Orchestrator's review claimed
    as the claim activity sees it."""
    service, scope, chat, orch, outer = await admit(database, job_id, reports=[("worker", "orch")])
    assign = await service.claim_run(orch, request_id="assign")
    spawned = [await service.execute("spawn", scope=as_actor(scope, assign), request_id=f"spawn-{n}",
                                     arguments={"instructions": f"Gather evidence {n}"},
                                     template={"node_id": "worker", "node_type": "ai_employee"})
               for n in range(workers)]
    await finish(service, assign, "Waiting for evidence.")
    work = await service.claim_run(spawned[0]["agent"]["id"], request_id="work")
    await finish(service, work, "The retained evidence.")
    review = await service.claim_run(orch, request_id="review")
    review["context"] = turn_context(review)
    return SimpleNamespace(service=service, scope=scope, chat=chat, orch=orch, outer=outer,
                           child=spawned[0]["task"]["id"], review=review)


async def task(database, task_id):
    async with database.get_session() as session:
        return await session.get(TeamTask, task_id)


async def test_sure_work_is_accepted_with_no_model_turn_and_handed_on(real_database, judge):
    await owner_sets(real_database, 90)
    case = await submitted_review(real_database)

    settled = await auto_review.settle(case.service, case.review)

    assert settled["status"] == "completed"
    assert settled["result"] == {"response": "Gather evidence 0:\nThe retained evidence."}
    assert settled["task_outcome"]["task_id"] == case.outer
    assert settled["task_outcome"]["status"] == "submitted"
    assert case.chat in settled["instance_wake"]
    child = await task(real_database, case.child)
    assert child.status == "accepted"
    assert child.context["auto_accepted"] == {"attempt": 0, "confidence": 0.95}
    outer = await task(real_database, case.outer)
    assert (outer.status, outer.result) == ("submitted", settled["result"])
    async with real_database.get_session() as session:
        assert (await session.get(AgentRun, case.review["run_id"])).status == "completed"
        transcript = (await session.get(AgentInstanceConversation, case.orch)).messages
    assert 'Accepted "Gather evidence 0" automatically: 95% sure it completes the instructions.' in str(transcript[-1])
    (call,) = judge.calls
    assert call.text == {"instructions": "Gather evidence 0", "result": "The retained evidence."}
    assert call.questions == auto_review.QUESTION
    assert call.kwargs["calibrated_only"] is True


@pytest.mark.parametrize(
    "percent, probability, calibrated",
    [
        pytest.param(0, 0.99, True, id="the setting is off"),
        pytest.param(90, 0.5, True, id="below the setting"),
        pytest.param(90, None, True, id="no judgment"),
        pytest.param(90, 0.99, False, id="only the chat-model fallback"),
    ],
)
async def test_anything_short_leaves_the_review_to_the_reviewer(real_database, judge, percent, probability, calibrated):
    await owner_sets(real_database, percent)
    judge.probability, judge.calibrated = probability, calibrated
    case = await submitted_review(real_database)

    assert await auto_review.settle(case.service, case.review) is None

    assert (await task(real_database, case.child)).status == "submitted"
    async with real_database.get_session() as session:
        assert (await session.get(AgentRun, case.review["run_id"])).status == "running"
    if percent == 0:
        assert judge.calls == []


async def test_with_a_sibling_still_working_the_reviewer_task_waits(real_database, judge):
    await owner_sets(real_database, 90)
    case = await submitted_review(real_database, workers=2)

    settled = await auto_review.settle(case.service, case.review)

    assert settled["status"] == "completed"
    assert "task_outcome" not in settled
    assert (await task(real_database, case.child)).status == "accepted"
    assert (await task(real_database, case.outer)).status == "running"


async def test_chats_review_of_the_final_answer_is_never_skipped(real_database, judge):
    await owner_sets(real_database, 90)
    case = await submitted_review(real_database)
    await auto_review.settle(case.service, case.review)
    judge.calls.clear()

    chat_review = await case.service.claim_run(case.chat, request_id="final-review")
    chat_review["context"] = turn_context(chat_review)

    assert await auto_review.settle(case.service, chat_review) is None
    assert judge.calls == []
    assert (await task(real_database, case.outer)).status == "submitted"


async def test_a_repeated_claim_of_a_settled_run_judges_nothing_again(real_database, judge):
    await owner_sets(real_database, 90)
    case = await submitted_review(real_database)
    await auto_review.settle(case.service, case.review)

    again = await case.service.claim_run(case.orch, request_id="review")
    again["context"] = turn_context(again)

    assert again["status"] == "completed"
    assert await auto_review.settle(case.service, again) is None
    assert len(judge.calls) == 1


async def test_a_model_cannot_send_the_automatic_marker():
    from services.agent_instances.tools import execute_lifecycle_tool

    refused = await execute_lifecycle_tool("accept_task", {"task_ref": "taskref_x", "auto_confidence": 0.99},
                                           {"agent_operation": "accept_task"})

    assert refused["success"] is False and refused["error_type"] == "invalid_tool_input"
    assert "auto_confidence" in refused["error"]
