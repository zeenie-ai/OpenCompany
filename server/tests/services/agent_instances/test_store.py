"""Real transactional contracts; no providers or external runtimes are invoked."""
import asyncio

import pytest
from sqlalchemy import text
from sqlmodel import select

from models.agent_instances import AgentDelivery, AgentInstance, AgentInstanceConversation, AgentRun
from models.database import AgentMessage, AgentTeam, TeamMember, TeamTask, TeamTaskAttempt
from models.employees import EmployeeJob
from services.agent_instances import AgentInstanceService, InstanceError


@pytest.fixture
async def setup(real_database):
    db = real_database
    async with db.reserved_session() as session:
        session.add(AgentTeam(id="team", workflow_id="wf", team_lead_node_id="lead", execution_id="exec"))
        session.add(TeamMember(team_id="team", agent_node_id="lead", agent_type="ai_employee", role="team_lead"))
        session.add(TeamMember(team_id="team", agent_node_id="worker", agent_type="aiAgent", role="teammate"))
        session.add(TeamMember(team_id="team", agent_node_id="other", agent_type="aiAgent", role="teammate"))
        await session.commit()
    service = AgentInstanceService(db)
    scope = dict(workflow_id="wf", generation=1, team_id="team", job_id="job", execution_id="exec", lead_node_id="lead")
    lead = await service.ensure_lead(scope=scope, template={"node_id": "lead"}, request_id="lead")
    scope["agent_instance_id"] = lead["id"]
    return db, service, scope


async def spawn(service, scope, key="spawn", instructions="Find the facts", after=None):
    return await service.execute("spawn", scope=scope, arguments={"instructions": instructions, "after": after or []},
        request_id=key, template={"node_id": "worker", "node_type": "aiAgent", "parameters": {"temperature": 0.2}})


async def run_task(service, instance_id, key="run"):
    run = await service.claim_run(instance_id, request_id=key)
    messages = [*run["transcript"], *run["messages"], {"role": "assistant", "content": "Finished"}]
    await service.checkpoint(instance_id, run["run_id"], messages=messages, expected_revision=run["transcript_revision"],
                             delivery_ids=run["deliveries"], request_id="response")
    await service.complete_run(instance_id, run["run_id"], result={"content": "Finished"}, request_id="done")
    return run


async def read_task(service, scope, task):
    return (await service.execute("get_task", scope=scope, arguments={"task": task["id"]}, request_id=f"get-{task['id']}-{id(task)}"))["task"]


async def test_spawn_is_atomic_idempotent_and_isolates_instances(setup):
    db, service, scope = setup
    first = await spawn(service, scope)
    again = await spawn(service, scope)
    second = await spawn(service, scope, "second")
    assert first == again
    assert first["agent"]["id"] != second["agent"]["id"]
    assert first["task"]["mission"] == "Find the facts"
    assert first["task"]["acceptance_criteria"] == {"instructions": "Find the facts"}
    with pytest.raises(InstanceError, match="idempotency_conflict"):
        await spawn(service, scope, instructions="Changed payload")
    with pytest.raises(InstanceError, match="connected"):
        await service.execute("spawn", scope=scope, arguments={"instructions": "No"}, request_id="invalid",
                              template={"node_id": "outsider"})
    async with db.get_session() as session:
        assert len((await session.execute(select(TeamTask))).scalars().all()) == 2
        assert len((await session.execute(select(AgentInstance))).scalars().all()) == 3


async def test_wait_reads_live_state_even_when_the_call_identity_is_reused(setup):
    _, service, scope = setup
    child = await spawn(service, scope)
    args = {"agents": [child["agent"]["id"]]}
    assert (await service.execute("wait_for_agents", scope=scope, arguments=args, request_id="wait"))["status"] == "pending"
    await run_task(service, child["agent"]["id"])
    assert (await service.execute("wait_for_agents", scope=scope, arguments=args, request_id="wait"))["status"] == "idle"


async def test_review_feedback_preserves_the_inducing_peer_chain(setup):
    db, service, scope = setup
    child = await spawn(service, scope)
    worker_id = child["agent"]["id"]
    run = await service.claim_run(worker_id, request_id="worker")
    peer = await service.execute("send_message", scope={**scope, "agent_instance_id": worker_id, "agent_run_id": run["run_id"]},
        arguments={"agent": scope["agent_instance_id"], "message": "Ready for your review"}, request_id="peer")
    await service.checkpoint(worker_id, run["run_id"], messages=[*run["messages"], {"role": "assistant", "content": "Done"}],
        expected_revision=0, delivery_ids=run["deliveries"], request_id="saved")
    await service.complete_run(worker_id, run["run_id"], result={"content": "Done"}, request_id="done")
    review = await service.claim_run(scope["agent_instance_id"], request_id="review")
    task = await read_task(service, scope, child["task"])
    await service.execute("request_changes", scope={**scope, "agent_run_id": review["run_id"]},
        arguments={"task_ref": task["task_ref"], "feedback": "Add citations"}, request_id="feedback")
    async with db.get_session() as session:
        revised = (await session.execute(select(AgentDelivery).where(AgentDelivery.task_id == task["id"], AgentDelivery.attempt == 1))).scalar_one()
        assert revised.chain_id == peer["delivery"]["chain_id"]


async def test_one_run_owns_instance_and_checkpoints_are_fenced(setup):
    _, service, scope = setup
    instance = (await spawn(service, scope))["agent"]["id"]
    claims = await asyncio.gather(*(service.claim_run(instance, request_id=f"run-{i}") for i in range(4)))
    runs = [run for run in claims if run]
    assert len(runs) == 1
    run = runs[0]
    args = dict(messages=[*run["messages"], {"role": "assistant", "content": "Result"}], expected_revision=0,
                delivery_ids=run["deliveries"], request_id="step-1")
    assert await service.checkpoint(instance, run["run_id"], **args) == {"revision": 1}
    assert await service.checkpoint(instance, run["run_id"], **args) == {"revision": 1}
    with pytest.raises(InstanceError, match="transcript_conflict"):
        await service.checkpoint(instance, run["run_id"], **{**args, "request_id": "other"})
    await service.execute("stop_agent", scope=scope, arguments={"agent": instance}, request_id="stop")
    with pytest.raises(InstanceError, match="no longer owns"):
        await service.checkpoint(instance, run["run_id"], **{**args, "expected_revision": 1, "request_id": "late"})
    await service.complete_run(instance, run["run_id"], result={"bad": "late"}, request_id="late-complete")
    task = (await service.execute("list_tasks", scope=scope, arguments={}, request_id="tasks"))["tasks"][0]
    assert task["status"] == "failed" and task["result"] is None


async def test_messages_are_ordered_and_do_not_cross_jobs(setup):
    db, service, scope = setup
    recipient = (await spawn(service, scope))["agent"]["id"]
    calls = [service.execute("send_message", scope=scope, arguments={"agent": recipient, "message": f"Question {i}"},
                             request_id=f"message-{i}") for i in range(8)]
    receipts = await asyncio.gather(*calls)
    assert sorted(item["delivery"]["sequence"] for item in receipts) == list(range(2, 10))
    bad_scope = {**scope, "job_id": "different"}
    with pytest.raises(InstanceError):
        await service.execute("send_message", scope=bad_scope, arguments={"agent": recipient, "message": "No"}, request_id="outside")
    with pytest.raises(InstanceError, match="message_too_large"):
        await service.execute("send_message", scope=scope, arguments={"agent": recipient, "message": "x" * 8193}, request_id="large")
    run = await service.claim_run(recipient, request_id="run")
    assert run["messages"][1]["content"].startswith("[Agent message from")
    async with db.get_session() as session:
        rows = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == recipient))).scalars().all()
        assert len(rows) == 9


async def test_feedback_retry_reassign_preserve_task_contract(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    original = spawned["task"]
    await run_task(service, spawned["agent"]["id"])
    task = (await service.execute("get_task", scope=scope, arguments={"task": original["id"]}, request_id="read-1"))["task"]
    changed = await service.execute("request_changes", scope=scope, arguments={"task_ref": task["task_ref"], "feedback": "Include citations"}, request_id="feedback")
    duplicate = await service.execute("request_changes", scope=scope, arguments={"task_ref": task["task_ref"], "feedback": "Include citations"}, request_id="feedback")
    assert duplicate == changed
    assert changed["task"]["mission"] == original["mission"]
    assert changed["task"]["review_feedback"] == ["Include citations"]
    await run_task(service, spawned["agent"]["id"], "run-2")
    task = (await service.execute("get_task", scope=scope, arguments={"task": original["id"]}, request_id="read-2"))["task"]
    moved = await service.execute("reassign", scope=scope, arguments={"task_ref": task["task_ref"]}, request_id="move", template={"node_id": "other"})
    assert moved["task"]["id"] == original["id"]
    assert moved["agent"]["id"] != spawned["agent"]["id"]
    assert moved["task"]["review_feedback"] == ["Include citations"]
    async with db.get_session() as session:
        tasks = (await session.execute(select(TeamTask))).scalars().all()
        attempts = (await session.execute(select(TeamTaskAttempt).order_by(TeamTaskAttempt.attempt_number))).scalars().all()
        assert len(tasks) == 1 and len(attempts) == 3
        assert attempts[0].input_snapshot["feedback"] == []
        assert attempts[1].input_snapshot["feedback"] == ["Include citations"]


async def test_dependencies_wait_for_acceptance_and_stale_refs_fail(setup):
    _, service, scope = setup
    first = await spawn(service, scope)
    second = await spawn(service, scope, "dependent", after=[first["task"]["task_ref"]])
    assert await service.claim_run(second["agent"]["id"], request_id="blocked") is None
    await run_task(service, first["agent"]["id"])
    assert await service.claim_run(second["agent"]["id"], request_id="submitted") is None
    with pytest.raises(InstanceError, match="state changed"):
        await service.execute("accept_task", scope=scope, arguments={"task_ref": first["task"]["task_ref"]}, request_id="stale")
    task = (await service.execute("get_task", scope=scope, arguments={"task": first["task"]["id"]}, request_id="read"))["task"]
    await service.execute("accept_task", scope=scope, arguments={"task_ref": task["task_ref"]}, request_id="accept")
    assert (await service.claim_run(second["agent"]["id"], request_id="ready"))["task"]["status"] == "running"


async def test_completion_and_review_notice_share_commit_and_survive_restart(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    await run_task(service, spawned["agent"]["id"])
    replacement = AgentInstanceService(db)
    pending = await replacement.list_pending()
    assert scope["agent_instance_id"] in {row["id"] for row in pending}
    run = await replacement.claim_run(scope["agent_instance_id"], request_id="review")
    assert run["task"] is None
    assert "task_review" in run["messages"][0]["content"]
    async with db.get_session() as session:
        assert (await session.get(TeamTask, spawned["task"]["id"])).status == "submitted"


async def test_halt_is_durable_and_reset_disposes_mail(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    instance = spawned["agent"]["id"]
    run = await service.claim_run(instance, request_id="run")
    for i in range(3):
        result = await service.record_invalid_request(instance, run["run_id"], request_id=f"invalid-{i}")
    assert result == {"count": 3, "halted": True}
    await service.complete_run(instance, run["run_id"], error="invalid_orchestration_requests", request_id="halt")
    await service.execute("send_message", scope=scope, arguments={"agent": instance, "message": "Follow up"}, request_id="queued")
    assert await AgentInstanceService(db).claim_run(instance, request_id="wake") is None
    await service.reset_generation("wf", 1)
    with pytest.raises(InstanceError, match="recipient_closed"):
        await service.execute("send_message", scope=scope, arguments={"agent": instance, "message": "Too late"}, request_id="late")
    async with db.get_session() as session:
        assert (await session.get(AgentInstance, instance)).state == "closed"
        assert not (await session.execute(select(AgentDelivery).where(AgentDelivery.state.in_(["queued", "admitted", "held"])))).scalars().all()


async def test_close_and_busy_message_admission_are_fenced(setup):
    _, service, scope = setup
    spawned = await spawn(service, scope)
    with pytest.raises(InstanceError, match="Resolve assigned"):
        await service.execute("close_agent", scope=scope, arguments={"agent": spawned["agent"]["id"]}, request_id="close")
    waiting = await service.execute("wait_for_agents", scope=scope, arguments={"agents": [spawned["agent"]["id"]]}, request_id="wait")
    assert waiting["status"] == "pending"
    with pytest.raises(InstanceError, match="spawned teammate"):
        await service.execute("assign_task", scope=scope, arguments={"agent": scope["agent_instance_id"], "instructions": "Self review"}, request_id="self")
    with pytest.raises(InstanceError, match="Provide instructions"):
        await service.execute("modify_task", scope=scope, arguments={"task_ref": spawned["task"]["task_ref"]}, request_id="empty-edit")


async def test_review_retry_preserves_existing_task_and_is_idempotent(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    async with db.reserved_session() as session:
        session.add(EmployeeJob(id="job", workflow_id="wf", origin_execution_id="exec", lead_node_id="lead", team_id="team", state="review_blocked"))
        lead = await session.get(AgentInstance, scope["agent_instance_id"])
        lead.state, lead.halt_reason = "halted", "invalid_orchestration_requests"
        await session.commit()
    first = await service.retry_review(scope=scope, request_id="retry")
    assert first == await service.retry_review(scope=scope, request_id="retry")
    assert first["instance_wake"] == [scope["agent_instance_id"]]
    async with db.get_session() as session:
        assert len((await session.execute(select(TeamTask))).scalars().all()) == 1
        assert (await session.get(EmployeeJob, "job")).state == "running"
        assert (await session.get(TeamTask, spawned["task"]["id"])).current_attempt == 0


async def test_queue_backpressure_does_not_drop_mandatory_reviews(setup, monkeypatch):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    monkeypatch.setenv("AGENT_PENDING_MESSAGES", "1")
    monkeypatch.setenv("AGENT_PEER_MESSAGE_BYTES", "3")
    service = AgentInstanceService(db)
    worker_scope = {**scope, "agent_instance_id": spawned["agent"]["id"]}
    with pytest.raises(InstanceError, match="message_too_large"):
        await service.execute("send_message", scope=worker_scope,
            arguments={"agent": scope["agent_instance_id"], "message": "\U0001f4ac"}, request_id="oversized")
    await service.execute("send_message", scope=worker_scope, arguments={"agent": scope["agent_instance_id"], "message": "One"}, request_id="one")
    with pytest.raises(InstanceError, match="inbox is full"):
        await service.execute("send_message", scope=worker_scope, arguments={"agent": scope["agent_instance_id"], "message": "Two"}, request_id="two")
    await run_task(service, spawned["agent"]["id"])
    async with db.get_session() as session:
        deliveries = (await session.execute(select(AgentDelivery).where(AgentDelivery.recipient_id == scope["agent_instance_id"]))).scalars().all()
        assert sorted(row.kind for row in deliveries) == ["peer", "task_review"]


async def test_peer_chain_budget_survives_forwarding_and_requires_explicit_resume(setup):
    db, service, scope = setup
    a = (await spawn(service, scope, "a"))["agent"]["id"]
    b = (await spawn(service, scope, "b"))["agent"]["id"]
    await run_task(service, a, "work-a")
    await run_task(service, b, "work-b")
    await service.execute("send_message", scope=scope, arguments={"agent": a, "message": "Discuss"}, request_id="begin")
    recipient, target = a, b
    chain_ids = set()
    for index in range(8):
        run = await service.claim_run(recipient, request_id=f"peer-{index}")
        assert run is not None
        receipt = await service.execute("send_message", scope={**scope, "agent_instance_id": recipient},
            arguments={"agent": target, "message": "Next"}, request_id=f"forward-{index}")
        chain_ids.add(receipt["delivery"]["chain_id"])
        await service.checkpoint(recipient, run["run_id"], messages=[*run["transcript"], *run["messages"],
            {"role": "assistant", "content": "Sent"}], expected_revision=run["transcript_revision"],
            delivery_ids=run["deliveries"], request_id="response")
        await service.complete_run(recipient, run["run_id"], result={"content": "Sent"}, request_id="done")
        recipient, target = target, recipient
    assert len(chain_ids) == 1
    assert await service.claim_run(recipient, request_id="ninth") is None
    assert await AgentInstanceService(db).claim_run(recipient, request_id="restart") is None
    async with db.get_session() as session:
        notice = (await session.execute(select(AgentMessage).where(AgentMessage.message_type == "agent_notice"))).scalars().all()
        assert len(notice) == 1
    await service.execute("resume_agent", scope=scope, arguments={"agent": recipient}, request_id="explicit-resume")
    assert await service.claim_run(recipient, request_id="resumed") is not None


async def test_failed_commit_cannot_publish_task_without_review(setup, monkeypatch):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    instance = spawned["agent"]["id"]
    run = await service.claim_run(instance, request_id="run")
    commit = service._commit

    async def fail_commit(*args, **kwargs):
        raise RuntimeError("simulated storage failure")

    monkeypatch.setattr(service, "_commit", fail_commit)
    with pytest.raises(RuntimeError, match="storage failure"):
        await service.complete_run(instance, run["run_id"], result={"content": "done"}, request_id="completion")
    async with db.get_session() as session:
        assert (await session.get(TeamTask, spawned["task"]["id"])).status == "running"
        assert not (await session.execute(select(AgentDelivery).where(AgentDelivery.kind == "task_review"))).scalars().all()
    monkeypatch.setattr(service, "_commit", commit)
    await service.complete_run(instance, run["run_id"], result={"content": "done"}, request_id="completion")
    async with db.get_session() as session:
        assert (await session.get(TeamTask, spawned["task"]["id"])).status == "submitted"
        assert len((await session.execute(select(AgentDelivery).where(AgentDelivery.kind == "task_review"))).scalars().all()) == 1


async def test_queued_edits_preserve_original_message_and_public_views_hide_configuration(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    assert "config" not in spawned["agent"] and "continuation" not in spawned["agent"]
    changed = await service.execute("modify_task", scope=scope,
        arguments={"task_ref": spawned["task"]["task_ref"], "instructions": "Updated facts"}, request_id="edit")
    run = await service.claim_run(spawned["agent"]["id"], request_id="run")
    assert run["messages"][0]["content"].endswith("Updated facts")
    async with db.get_session() as session:
        deliveries = (await session.execute(select(AgentDelivery).where(AgentDelivery.task_id == spawned["task"]["id"])
            .order_by(AgentDelivery.sequence))).scalars().all()
        assert deliveries[0].content == "Find the facts" and deliveries[0].state == "cancelled"
        assert deliveries[1].content == "Updated facts"
    task = await service.execute("get_task", scope=scope, arguments={"task": changed["task"]["id"]}, request_id="read")
    assert "configuration" not in task["attempts"][0]["input_snapshot"]
    instance = await service.execute("get_agent", scope=scope, arguments={"agent": spawned["agent"]["id"]}, request_id="get")
    assert "input_snapshot" not in instance["runs"][0]


async def test_database_unique_index_refuses_two_active_runs(setup):
    db, service, scope = setup
    instance = (await spawn(service, scope))["agent"]["id"]
    await service.claim_run(instance, request_id="run")
    with pytest.raises(Exception):
        async with db.get_session() as session:
            session.add(AgentRun(id="illegal", instance_id=instance, request_id="illegal", fence=1))
            await session.commit()


async def test_delivered_job_cannot_wake_before_retirement_reconciliation(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    await run_task(service, spawned["agent"]["id"])
    task = (await service.execute("get_task", scope=scope, arguments={"task": spawned["task"]["id"]}, request_id="read"))["task"]
    await service.execute("accept_task", scope=scope, arguments={"task_ref": task["task_ref"]}, request_id="accept")
    async with db.reserved_session() as session:
        session.add(EmployeeJob(id="job", workflow_id="wf", origin_execution_id="exec", lead_node_id="lead",
                               team_id="team", state="delivered", result="Done"))
        await session.commit()
    # Simulate a crash before the delivery caller gets to close_job.
    assert await service.list_pending() == []
    assert await service.claim_run(scope["agent_instance_id"], request_id="late-review") is None
    results = await AgentInstanceService(db).close_completed_jobs()
    assert len(results) == 1 and len(results[0]["closed"]) == 2
    assert await service.close_completed_jobs() == []
    async with db.get_session() as session:
        assert all(row.state == "closed" for row in (await session.execute(select(AgentInstance))).scalars())
        assert all(row.state in {"consumed", "recipient_closed"} for row in (await session.execute(select(AgentDelivery))).scalars())


async def test_additive_upgrade_preserves_legacy_tasks(real_database):
    db = real_database
    async with db.reserved_session() as session:
        session.add(TeamTask(id="legacy", team_id="legacy-team", title="Old task", created_by="lead", mission="Keep this"))
        await session.commit()
    async with db.engine.begin() as connection:
        await connection.execute(text("DROP INDEX ix_team_tasks_assigned_instance_id"))
        for table, columns in {
            "team_tasks": ("assigned_instance_id", "assigned_run_id", "protocol_version", "review_feedback"),
            "team_task_attempts": ("assigned_instance_id", "assigned_run_id", "input_snapshot"),
        }.items():
            for column in columns:
                await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {column}"))
    await db.shutdown()
    await db.startup()
    async with db.get_session() as session:
        task = await session.get(TeamTask, "legacy")
        assert task.mission == "Keep this" and task.protocol_version == 0 and task.assigned_instance_id is None


async def test_failed_lead_with_live_children_stays_halted_until_owner_retry(setup):
    db, service, scope = setup
    await spawn(service, scope)
    async with db.reserved_session() as session:
        session.add(EmployeeJob(id="job", workflow_id="wf", origin_execution_id="exec", lead_node_id="lead", team_id="team", state="working"))
        await session.commit()
    lead = scope["agent_instance_id"]
    await service.enqueue_input(lead, content="Coordinate", request_id="lead-input")
    run = await service.claim_run(lead, request_id="lead-run")
    await service.complete_run(lead, run["run_id"], error="coordination failed", request_id="failure")
    await service.enqueue_input(lead, content="A child submitted", kind="task_review", request_id="review-event")
    assert await service.claim_run(lead, request_id="automatic-wakeup") is None
    async with db.get_session() as session:
        assert (await session.get(EmployeeJob, "job")).state == "review_blocked"
        assert (await session.get(AgentInstance, lead)).state == "halted"


async def test_legacy_writers_cannot_bypass_instance_ownership(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    task_id = spawned["task"]["id"]
    readable = await db.get_durable_team_task("team", task_id)
    assert readable["protocol_version"] == 1 and readable["depends_on"] == []
    assert readable["assigned_instance_id"] == spawned["agent"]["id"]
    assert (await db.get_team_tasks("team"))[0]["id"] == task_id
    assert await db.get_claimable_tasks("team") == []
    assert await db.claim_task(task_id, "worker") is None
    assert await db.transition_team_task("team", task_id, 0, ["queued"], {"mission": "Bypassed"}) is None
    assert await db.add_team_task("legacy-injection", "team", "Injected", "lead") is None
    assert await db.create_durable_team_task(id="injection", team_id="team", title="Injected", created_by="lead") is None
    run = await service.claim_run(spawned["agent"]["id"], request_id="run")
    assert await db.claim_task(task_id, "worker") is None  # same-agent fallback also refuses
    assert await db.complete_task(task_id, {"content": "Unowned"}) is False
    assert await db.fail_task(task_id, "Unowned") is False
    assert await db.cancel_team_task("team", task_id, "Unowned") is None
    assert await db.register_team_task_execution(team_id="team", task_id=task_id, attempt_number=0, runner_workflow_id="Unowned") is None
    async with db.get_session() as session:
        task = await session.get(TeamTask, task_id)
        assert task.status == "running" and task.mission == "Find the facts" and task.assigned_run_id == run["run_id"]


async def test_stale_actor_cannot_admit_new_effects_after_its_turn(setup):
    _, service, scope = setup
    lead = scope["agent_instance_id"]
    await service.enqueue_input(lead, content="Coordinate", request_id="start")
    run = await service.claim_run(lead, request_id="lead-run")
    active_scope = {**scope, "agent_run_id": run["run_id"]}
    first = await spawn(service, active_scope, "owned")
    await service.checkpoint(lead, run["run_id"], messages=[*run["messages"], {"role": "assistant", "content": "Delegated"}],
        expected_revision=0, delivery_ids=run["deliveries"], request_id="response")
    await service.complete_run(lead, run["run_id"], result={"content": "Delegated"}, request_id="done")
    with pytest.raises(InstanceError, match="no longer owns"):
        await spawn(service, active_scope, "late")
    # Replaying the already committed call is harmless and returns its receipt.
    assert await spawn(service, active_scope, "owned") == first


async def test_generic_team_retires_only_after_final_lead_checkpoint(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    await run_task(service, spawned["agent"]["id"])
    task = (await service.execute("get_task", scope=scope, arguments={"task": spawned["task"]["id"]}, request_id="read"))["task"]
    await service.execute("accept_task", scope=scope, arguments={"task_ref": task["task_ref"]}, request_id="accept")
    lead = scope["agent_instance_id"]
    run = await service.claim_run(lead, request_id="final-review")
    async with db.reserved_session() as session:
        team = await session.get(AgentTeam, "team")
        team.status = "completed"
        await session.commit()
    assert (await service.close_completed_jobs())[0]["error"] == "agent_draining"
    await service.checkpoint(lead, run["run_id"], messages=[*run["messages"], {"role": "assistant", "content": "Final report"}],
        expected_revision=0, delivery_ids=run["deliveries"], request_id="report")
    await service.complete_run(lead, run["run_id"], result={"content": "Final report"}, request_id="done")
    assert (await service.get_instance(lead))["state"] == "closed"
    assert (await service.get_transcript(lead))["messages"][-1]["content"] == "Final report"


async def test_invalid_admission_budget_and_call_ids_survive_restart(setup):
    db, service, scope = setup
    instance = (await spawn(service, scope))["agent"]["id"]
    run = await service.claim_run(instance, request_id="run")
    await service.record_invalid_request(instance, run["run_id"], request_id="model-call-1")
    restored = AgentInstanceService(db)
    state = await restored.admission_state(instance, run["run_id"])
    assert state == {"invalid_count": 1, "invalid_ids": ["model-call-1"], "max_invalid": 3, "reason": ""}
    await restored.record_invalid_request(instance, run["run_id"], request_id="model-call-1")
    await restored.record_invalid_request(instance, run["run_id"], request_id="model-call-2")
    await restored.record_invalid_request(instance, run["run_id"], request_id="model-call-3")
    state = await AgentInstanceService(db).admission_state(instance, run["run_id"])
    assert state["invalid_count"] == 3
    assert state["invalid_ids"] == ["model-call-1", "model-call-2", "model-call-3"]
    assert state["reason"]


async def test_attempt_trace_records_physical_session_run(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    run = await service.claim_run(spawned["agent"]["id"], request_id="run",
        workflow_id="stable-session-workflow", workflow_run_id="physical-workflow-run")
    async with db.get_session() as session:
        task = await session.get(TeamTask, spawned["task"]["id"])
        attempt = (await session.execute(select(TeamTaskAttempt).where(TeamTaskAttempt.task_id == task.id))).scalars().one()
        assert task.runner_run_id == attempt.runner_run_id == "physical-workflow-run"
    stored_run = await service.get_run(spawned["agent"]["id"], run["run_id"])
    assert stored_run["temporal_run_id"] == "physical-workflow-run"


async def test_task_cancellation_has_durable_executor_stop_obligation(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    instance = spawned["agent"]["id"]
    run = await service.claim_run(instance, request_id="run")
    task = (await service.execute("get_task", scope=scope,
        arguments={"task": spawned["task"]["id"]}, request_id="read"))["task"]
    cancelled = await service.execute("cancel_task", scope=scope,
        arguments={"task_ref": task["task_ref"]}, request_id="cancel")
    assert cancelled["instance_stop"] == [instance]
    assert await AgentInstanceService(db).list_stopping() == [instance]
    await service.complete_run(instance, run["run_id"], result={"content": "Too late"}, request_id="drained")
    assert await service.list_stopping() == []
    assert (await service.get_instance(instance))["state"] == "idle"
    assert (await service.get_run(instance, run["run_id"]))["result"] is None


async def test_default_wait_excludes_caller_and_rejects_unknown_agents(setup):
    _, service, scope = setup
    lead = scope["agent_instance_id"]
    await service.enqueue_input(lead, content="Coordinate", request_id="start")
    await service.claim_run(lead, request_id="run")
    waited = await service.execute("wait_for_agents", scope=scope, arguments={}, request_id="wait")
    assert waited["status"] == "idle" and waited["agents"] == []
    for target in (lead, "unknown"):
        with pytest.raises(InstanceError, match="Wait for other"):
            await service.execute("wait_for_agents", scope=scope, arguments={"agents": [target]}, request_id=target)


async def test_large_result_is_retained_and_can_be_paged_and_searched(setup):
    db, service, scope = setup
    spawned = await spawn(service, scope)
    instance = spawned["agent"]["id"]
    run = await service.claim_run(instance, request_id="run")
    full_result = {"response": "A" * 12000 + "Unique tail evidence"}
    await service.complete_run(instance, run["run_id"], result=full_result, request_id="complete")
    read = await service.execute("get_task", scope=scope, arguments={"task": spawned["task"]["id"]}, request_id="get")
    assert read["task"]["result"]["truncated"] and read["task"]["result"]["total_chars"] > 12000
    assert read["attempts"][0]["result"]["truncated"]
    async with db.get_session() as session:
        assert (await session.get(TeamTask, spawned["task"]["id"])).result == full_result
    args = {"task_ref": read["task"]["task_ref"], "detail": "summary"}
    pages, cursor = [], None
    for index in range(10):
        page = (await service.execute("inspect_task", scope=scope,
            arguments={**args, "cursor": cursor}, request_id=f"page-{index}"))["result_excerpt"]
        assert len(page["text"]) <= 4000
        pages.append(page["text"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert "Unique tail evidence" in "".join(pages)
    searched = (await service.execute("inspect_task", scope=scope,
        arguments={**args, "detail": "search", "query": "Unique tail evidence"}, request_id="search"))["result_excerpt"]
    assert searched["matched"] and "Unique tail evidence" in searched["text"]


async def test_failed_lead_does_not_replace_an_existing_approval_outcome(setup):
    from models.approvals import ApprovalRequest
    db, service, scope = setup
    await spawn(service, scope)
    async with db.reserved_session() as session:
        session.add(EmployeeJob(id="job", workflow_id="wf", origin_execution_id="exec", lead_node_id="lead", team_id="team", state="running"))
        session.add(ApprovalRequest(id="approval", idempotency_key="approval", owner_id="owner", workflow_id="wf",
            node_id="send", execution_id="job", kind="tool_call"))
        await session.commit()
    lead = scope["agent_instance_id"]
    await service.enqueue_input(lead, content="Coordinate", request_id="lead-input")
    run = await service.claim_run(lead, request_id="lead-run")
    await service.complete_run(lead, run["run_id"], error="failed", request_id="failure")
    async with db.get_session() as session:
        assert (await session.get(EmployeeJob, "job")).state == "running"
        assert (await session.get(ApprovalRequest, "approval")).status == "pending"


async def test_reconciliation_rotates_active_owners_and_skips_blocked_inboxes(setup):
    db, service, scope = setup
    prerequisite = await spawn(service, scope, "prerequisite")
    prerequisite_id = prerequisite["agent"]["id"]
    await service.claim_run(prerequisite_id, request_id="still-running")
    blocked_ids = set()
    for index in range(3):
        blocked = await spawn(service, scope, f"blocked-{index}", after=[prerequisite["task"]["task_ref"]])
        blocked_ids.add(blocked["agent"]["id"])
    active = (await spawn(service, scope, "active"))["agent"]["id"]
    await service.claim_run(active, request_id="another-owner")
    halted = (await spawn(service, scope, "halted"))["agent"]["id"]
    halted_run = await service.claim_run(halted, request_id="halted-owner")
    await service.halt_instance(halted, reason="invalid_orchestration_requests", request_id="halt")
    waiting = (await spawn(service, scope, "lost-wakeup"))["agent"]["id"]

    eligible = {prerequisite_id, active, halted, waiting}
    selected = []
    for _ in eligible:
        # The reconciler constructs a new store each sweep; rotation must be durable.
        batch = await AgentInstanceService(db).list_pending(limit=1)
        assert len(batch) == 1
        selected.append(batch[0]["id"])
    assert set(selected) == eligible
    assert not blocked_ids.intersection(selected)
    assert (await service.claim_run(waiting, request_id="recovered-wakeup"))["task"]["status"] == "running"
    assert await service.claim_run(halted, request_id="must-stay-halted") is None
    assert (await service.get_instance(halted))["active_run_id"] == halted_run["run_id"]


async def test_stop_reconciliation_rotates_unfinished_executors(setup):
    db, service, scope = setup
    stopping = set()
    for index in range(3):
        instance = (await spawn(service, scope, f"stop-{index}"))["agent"]["id"]
        await service.claim_run(instance, request_id="running")
        await service.execute("stop_agent", scope=scope, arguments={"agent": instance}, request_id=f"interrupt-{index}")
        stopping.add(instance)
    selected = []
    for _ in stopping:
        selected.extend(await AgentInstanceService(db).list_stopping(limit=1))
    assert set(selected) == stopping


async def test_retirement_reconciliation_rotates_past_draining_job(setup):
    db, service, scope = setup
    await service.enqueue_input(scope["agent_instance_id"], content="Final review", request_id="start")
    await service.claim_run(scope["agent_instance_id"], request_id="still-finishing")
    second_scope = {**scope, "team_id": "second-team", "job_id": "second-job", "execution_id": "second-exec"}
    second_scope.pop("agent_instance_id")
    async with db.reserved_session() as session:
        session.add(AgentTeam(id="second-team", workflow_id="wf", team_lead_node_id="lead", execution_id="second-exec"))
        await session.commit()
    second_lead = await service.ensure_lead(scope=second_scope, template={"node_id": "lead"}, request_id="second-lead")
    async with db.reserved_session() as session:
        for identity in ("team", "second-team"):
            (await session.get(AgentTeam, identity)).status = "completed"
        await session.commit()

    first = await AgentInstanceService(db).close_completed_jobs(limit=1)
    assert first == [{"success": False, "job_id": "job", "error": "agent_draining"}]
    second = await AgentInstanceService(db).close_completed_jobs(limit=1)
    assert second[0]["success"] and second_lead["id"] in second[0]["closed"]
    assert (await service.get_instance(scope["agent_instance_id"]))["state"] == "running"
