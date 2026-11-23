"""Job and attempt boundaries for the Chat Employee's durable board task."""
import pytest
from sqlmodel import select

from models.agent_instances import AgentDelivery, AgentInstance
from models.database import AgentTeam, TeamMember, TeamTask
from models.employees import EmployeeJob
from services.agent_instances import AgentInstanceService, InstanceError


async def admit(db, job_id, *, reports=()):
    members = [{"node_id": "orch", "parent_node_id": "chat", "node_type": "orchestrator_agent"}]
    members.extend({"node_id": node, "parent_node_id": parent, "node_type": "ai_employee"} for node, parent in reports)
    plan = {"version": 3, "lead_node_id": "chat", "talk_node_id": "chat", "orchestrator_node_id": "orch", "members": members}
    scope = {"workflow_id": "wf", "team_id": f"team-{job_id}", "job_id": job_id, "lead_node_id": "chat",
             "execution_id": job_id, "generation": 1}
    async with db.reserved_session() as session:
        session.add(AgentTeam(id=scope["team_id"], workflow_id="wf", team_lead_node_id="chat", execution_id=job_id))
        for member in members:
            session.add(TeamMember(team_id=scope["team_id"], agent_node_id=member["node_id"],
                                   agent_type=member["node_type"], role="teammate"))
        session.add(EmployeeJob(id=job_id, workflow_id="wf", team_id=scope["team_id"], lead_node_id="chat",
                                origin_execution_id=job_id, mission=f"Handle {job_id}", state="running",
                                source={"generation": 1, "team_plan": plan}))
        await session.commit()
    service = AgentInstanceService(db)
    chat = await service.ensure_lead(scope=scope, template={"node_id": "chat", "initial_instructions": f"Handle {job_id}"},
                                     request_id="chat")
    handoff = await service.claim_run(chat["id"], request_id="intake")
    assert (await service.claim_run(chat["id"], request_id="intake"))["instance_wake"] == handoff["instance_wake"]
    orch, = handoff["instance_wake"]
    return service, scope, chat["id"], orch, handoff["result"]["task_id"]


async def finish(service, turn, text):
    actor, run = turn["instance"]["id"], turn["run_id"]
    await service.checkpoint(actor, run, expected_revision=turn["transcript_revision"], request_id="checkpoint",
        messages=[*turn["transcript"], *turn["messages"], {"role": "assistant", "content": text}],
        delivery_ids=turn["deliveries"])
    return await service.complete_run(actor, run, result={"response": text}, request_id="done")


def as_actor(scope, turn):
    return {**scope, "agent_instance_id": turn["instance"]["id"], "agent_run_id": turn["run_id"]}


async def mutate(service, scope, turn, task_id, operation, **kwargs):
    actor = as_actor(scope, turn)
    read = await service.execute("get_task", scope=actor, arguments={"task": task_id}, request_id="read")
    return await service.execute(operation, scope=actor, arguments={"task_ref": read["task"]["task_ref"], **kwargs},
                                 request_id=operation)


@pytest.mark.parametrize("accept_first", [True, False])
async def test_empty_roster_gap_and_recoverable_chat_review(real_database, accept_first):
    db = real_database
    service, scope, chat, orch, outer = await admit(db, "empty")
    turn = await service.claim_run(orch, request_id="gap")
    gap = await finish(service, turn, "We need an analyst. Ask the owner to add this role and apply the change.")
    assert gap["task_outcome"]["status"] == "submitted"
    assert gap["instance_wake"] == [chat]
    restarted = AgentInstanceService(db)
    assert await restarted.pending_task_events(limit=10) == [gap]
    await restarted.acknowledge_task_event(gap["task_event_receipt_id"])
    assert await restarted.pending_task_events(limit=10) == []
    review = await service.claim_run(chat, request_id="review")
    if accept_first:
        await mutate(service, scope, review, outer, "accept_task")
    incomplete = "NO_REPLY" if accept_first else "I saw this."
    await finish(service, review, incomplete)
    correction = await restarted.claim_run(chat, request_id="automatic")
    assert outer in correction["messages"][0]["content"]
    assert ("final answer" if accept_first else "accept_task") in correction["messages"][0]["content"]
    await finish(restarted, correction, incomplete)
    assert await service.claim_run(chat, request_id="no-more-corrections") is None
    async with db.get_session() as session:
        assert (await session.get(EmployeeJob, "empty")).state == "review_blocked"
    recovery = await service.retry_review(scope=scope, request_id="owner-retry")
    assert recovery == await service.retry_review(scope=scope, request_id="owner-retry")
    recovered = await service.claim_run(chat, request_id="recovered")
    assert outer in recovered["messages"][0]["content"]
    if not accept_first:
        await mutate(service, scope, recovered, outer, "accept_task")
    await finish(service, recovered, "We need an analyst before we can complete this work.")
    async with db.reserved_session() as session:
        job = await session.get(EmployeeJob, "empty")
        assert job.state != "review_blocked"
        job.state = "delivered"
        await session.commit()
    await service.close_job(scope=scope, request_id="after-delivery")
    async with db.get_session() as session:
        assert (await session.get(AgentTeam, scope["team_id"])).status == "completed"
        assert (await session.get(AgentInstance, chat)).state == "closed"


async def test_retry_does_not_bypass_required_delegation(real_database):
    service, scope, chat, orch, outer = await admit(real_database, "retry", reports=[("worker", "orch")])
    first = await service.claim_run(orch, request_id="first")
    assert (await finish(service, first, "Finished without calling a worker"))["task_outcome"]["status"] == "failed"
    review = await service.claim_run(chat, request_id="review")
    retry = await mutate(service, scope, review, outer, "retry_task")
    assert retry["task"]["id"] == outer and retry["task"]["current_attempt"] == 1
    await finish(service, review, "I asked the Orchestrator to retry.")
    async with real_database.get_session() as session:
        assert (await session.get(EmployeeJob, "retry")).state != "review_blocked"
    second = await service.claim_run(orch, request_id="second")
    result = await finish(service, second, "Finished without calling a worker again")
    assert result["task_outcome"]["status"] == "failed"


@pytest.mark.parametrize("correct", [True, False])
async def test_manager_gets_one_review_correction_without_repeating_worker(real_database, correct):
    db = real_database
    service, scope, chat, orch, outer = await admit(db, "missed-decision", reports=[("worker", "orch")])
    assign = await service.claim_run(orch, request_id="assign")
    worker = await service.execute("spawn", scope=as_actor(scope, assign), arguments={"instructions": "Gather evidence"},
        request_id="spawn", template={"node_id": "worker", "node_type": "ai_employee"})
    child = worker["task"]["id"]
    await finish(service, assign, "Waiting for evidence.")
    work = await service.claim_run(worker["agent"]["id"], request_id="work")
    await finish(service, work, "The retained evidence.")
    review = await service.claim_run(orch, request_id="review")
    missed = await finish(service, review, "The evidence looks good.")
    assert "task_outcome" not in missed
    restarted = AgentInstanceService(db)
    assert missed == await restarted.complete_run(orch, review["run_id"], result={"response": "The evidence looks good."},
                                                 request_id="done")
    assert await restarted.claim_run(chat, request_id="no-early-chat") is None
    assert await restarted.claim_run(worker["agent"]["id"], request_id="no-repeated-work") is None
    correction = await restarted.claim_run(orch, request_id="correction")
    assert correction["task"]["id"] == outer and correction["task"]["current_attempt"] == 0
    assert child in correction["messages"][0]["content"] and "accept_task" in correction["messages"][0]["content"]
    async with db.get_session() as session:
        submitted = await session.get(TeamTask, child)
        assert submitted.status == "submitted" and submitted.current_attempt == 0
        assert submitted.result["response"] == "The retained evidence."
    if not correct:
        assert "task_outcome" not in await finish(restarted, correction, "I read the evidence.")
        assert await restarted.claim_run(orch, request_id="no-unbounded-correction") is None
        assert await restarted.claim_run(chat, request_id="no-early-chat") is None
        async with db.get_session() as session:
            assert (await session.get(EmployeeJob, scope["job_id"])).state == "review_blocked"
            assert (await session.get(TeamTask, child)).status == "submitted"
        recovery = await restarted.retry_review(scope=scope, request_id="owner")
        assert recovery == await restarted.retry_review(scope=scope, request_id="owner")
        assert recovery["instance_wake"] == [orch]
        correction = await restarted.claim_run(orch, request_id="resume-review")
        assert correction["task"]["id"] == outer and correction["task"]["current_attempt"] == 0
        assert child in correction["messages"][0]["content"]
    await mutate(restarted, scope, correction, child, "accept_task")
    outcome = (await finish(restarted, correction, "Reviewed the evidence."))["task_outcome"]
    assert outcome["task_id"] == outer and outcome["reviewer_instance_id"] == chat
    assert outcome["status"] == "submitted"
    assert await restarted.claim_run(orch, request_id="no-more-corrections") is None


@pytest.mark.parametrize("blocked", [False, True])
async def test_owner_retry_identifies_failed_outer_and_reuses_assignment(real_database, blocked):
    service, scope, chat, orch, outer = await admit(real_database, "failed-review", reports=[("worker", "orch")])
    first = await service.claim_run(orch, request_id="first")
    await finish(service, first, "Nothing delegated.")
    for request_id in ["missed", "still-missed"]:
        review = await service.claim_run(chat, request_id=request_id)
        assert "retry_task" in review["messages"][0]["content"]
        await finish(service, review, "We should try again.")
    recovery = await service.retry_review(scope=scope, request_id="owner-retry")
    assert recovery == await service.retry_review(scope=scope, request_id="owner-retry")
    review = await service.claim_run(chat, request_id="recovered")
    instructions = review["messages"][0]["content"]
    assert outer in instructions and "retry_task" in instructions and "cancel_task" in instructions
    if blocked:
        with pytest.raises(InstanceError, match="Give the reason"):
            await mutate(service, scope, review, outer, "cancel_task", reason=" ")
        closed = await mutate(service, scope, review, outer, "cancel_task", reason="The search requires approval.")
        assert closed["task"]["status"] == "cancelled" and closed["task"]["current_attempt"] == 0
        assert (await finish(service, review, "The search requires approval."))["status"] == "completed"
        assert await service.claim_run(orch, request_id="no-repeat") is None
        return
    retried = await mutate(service, scope, review, outer, "retry_task")
    assert retried["task"]["id"] == outer and retried["task"]["current_attempt"] == 1
    await finish(service, review, "The Orchestrator is retrying.")
    rerun = await service.claim_run(orch, request_id="second")
    assert rerun["task"]["id"] == outer and rerun["task"]["current_attempt"] == 1


async def test_owner_retry_resumes_both_parked_managers(real_database):
    service, scope, chat, orch, _ = await admit(real_database, "parallel-review",
        reports=[("manager", "orch"), ("worker", "manager")])
    orch_turn = await service.claim_run(orch, request_id="assign")
    managers = [await service.execute("spawn", scope=as_actor(scope, orch_turn), arguments={"instructions": brief},
        request_id=brief, template={"node_id": "manager", "node_type": "ai_employee"}) for brief in ["east", "west"]]
    await finish(service, orch_turn, "Waiting for both managers.")
    for manager in managers:
        manager_id = manager["agent"]["id"]
        turn = await service.claim_run(manager_id, request_id="delegate")
        worker = await service.execute("spawn", scope=as_actor(scope, turn), arguments={"instructions": "Collect evidence."},
            request_id="collect", template={"node_id": "worker", "node_type": "ai_employee"})
        await finish(service, turn, "Waiting for evidence.")
        await finish(service, await service.claim_run(worker["agent"]["id"], request_id="work"), "The evidence.")
        for review in ["initial-review", "reminder"]:
            await finish(service, await service.claim_run(manager_id, request_id=review), "I have read it.")
        assert await service.claim_run(manager_id, request_id="parked") is None
    assert await service.claim_run(chat, request_id="no-early-chat") is None
    resumed = await service.retry_review(scope=scope, request_id="owner")
    assert set(resumed["instance_wake"]) == {manager["agent"]["id"] for manager in managers}
    for manager in managers:
        turn = await service.claim_run(manager["agent"]["id"], request_id="owner-retry")
        assert turn["task"]["id"] == manager["task"]["id"] and turn["task"]["current_attempt"] == 0


async def test_reversed_job_results_stay_with_original_assigner(real_database):
    db = real_database
    service, scope_a, chat_a, orch_a, outer_a = await admit(db, "a")
    _, scope_b, chat_b, orch_b, outer_b = await admit(db, "b")
    a = await service.claim_run(orch_a, request_id="work")
    b = await service.claim_run(orch_b, request_id="work")
    result = await finish(service, b, "B needs an analyst")
    assert result == await service.complete_run(orch_b, b["run_id"], result={"response": "B needs an analyst"}, request_id="done")
    assert result["instance_wake"] == [chat_b]
    assert await service.claim_run(chat_a, request_id="early") is None
    await finish(service, a, "A needs a researcher")
    review_a = await service.claim_run(chat_a, request_id="review")
    review_b = await service.claim_run(chat_b, request_id="review")
    with pytest.raises(InstanceError, match="task_not_found"):
        await mutate(service, scope_b, review_b, outer_a, "accept_task")
    await mutate(service, scope_a, review_a, outer_a, "accept_task")
    await mutate(service, scope_b, review_b, outer_b, "accept_task")
    async with db.get_session() as session:
        for chat, outer in ((chat_a, outer_a), (chat_b, outer_b)):
            notices = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == chat,
                AgentDelivery.kind == "task_review"))).scalars().all()
            assert [notice.payload["task_id"] for notice in notices] == [outer]
            assert (await session.get(TeamTask, outer)).status == "accepted"


async def test_reused_manager_can_only_accept_child_from_its_current_assignment(real_database):
    service, scope, _, orch, _ = await admit(real_database, "parallel",
        reports=[("manager", "orch"), ("worker", "manager")])
    orch_turn = await service.claim_run(orch, request_id="assign")
    manager = await service.execute("spawn", scope=as_actor(scope, orch_turn), arguments={"instructions": "First brief"},
        request_id="first", template={"node_id": "manager", "node_type": "ai_employee"})
    manager_id = manager["agent"]["id"]
    second_task = await service.execute("assign_task", scope=as_actor(scope, orch_turn),
        arguments={"instructions": "Second brief", "agent": manager_id}, request_id="second")
    assert "task_outcome" not in await finish(service, orch_turn, "The two briefs are assigned.")
    first_turn = await service.claim_run(manager_id, request_id="first")
    async with real_database.get_session() as session:
        first_started_at = (await session.get(TeamTask, manager["task"]["id"])).started_at
    child = await service.execute("spawn", scope=as_actor(scope, first_turn), arguments={"instructions": "Find evidence"},
        request_id="child", template={"node_id": "worker", "node_type": "ai_employee"})
    assert "task_outcome" not in await finish(service, first_turn, "Waiting for evidence.")
    second_turn = await service.claim_run(manager_id, request_id="second")
    assert second_turn["task"]["id"] == second_task["task"]["id"]
    worker = await service.claim_run(child["agent"]["id"], request_id="research")
    outcome = (await finish(service, worker, "Evidence for the first brief."))["task_outcome"]
    assert outcome["reviewer_instance_id"] == manager_id and outcome["assigning_run_id"] == first_turn["run_id"]
    with pytest.raises(InstanceError, match="current task"):
        await mutate(service, scope, second_turn, child["task"]["id"], "accept_task")
    drained = await service.drain_messages(manager_id, second_turn["run_id"], request_id="during-second")
    assert drained["deliveries"] == []
    await finish(service, second_turn, "Completed the independent second brief.")
    review = await service.claim_run(manager_id, request_id="review-first")
    assert review["task"]["id"] == manager["task"]["id"]
    async with real_database.get_session() as session:
        assert (await session.get(TeamTask, manager["task"]["id"])).started_at == first_started_at
    await mutate(service, scope, review, child["task"]["id"], "accept_task")
    outcome = (await finish(service, review, "Completed the first brief with reviewed evidence."))["task_outcome"]
    assert outcome["reviewer_instance_id"] == orch and outcome["task_id"] == manager["task"]["id"]


async def test_cancelled_outer_attempt_fences_a_late_worker(real_database):
    db = real_database
    service, scope, chat, orch, outer = await admit(db, "cancel", reports=[("worker", "orch")])
    orch_turn = await service.claim_run(orch, request_id="assign")
    delegated = await service.execute("spawn", scope=as_actor(scope, orch_turn), arguments={"instructions": "Research"},
        request_id="spawn", template={"node_id": "worker", "node_type": "ai_employee"})
    await finish(service, orch_turn, "Waiting for research.")
    worker_id = delegated["agent"]["id"]
    worker_turn = await service.claim_run(worker_id, request_id="active")
    await service.enqueue_input(chat, content="Cancel this assignment", request_id="cancel-request")
    cancel_turn = await service.claim_run(chat, request_id="cancel")
    cancelled = await mutate(service, scope, cancel_turn, outer, "cancel_task", reason="Request withdrawn")
    assert worker_id in cancelled["instance_stop"]
    late = await service.complete_run(worker_id, worker_turn["run_id"], request_id="late", result={"response": "Old result"})
    assert late["instance_wake"] == [] and "task_outcome" not in late
    async with db.get_session() as session:
        assert (await session.get(TeamTask, delegated["task"]["id"])).status == "cancelled"
        assert (await session.get(TeamTask, outer)).status == "cancelled"
    assert await service.claim_run(orch, request_id="stale-wakeup") is None
