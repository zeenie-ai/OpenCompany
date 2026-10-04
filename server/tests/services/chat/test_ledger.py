"""Chat runs in the database (services/chat/ledger.py): admitted with the
owner's message, one live run per session, claimed by one Temporal workflow,
finished only by it, replies saved under the run's reply id, every message
appended to one chain, and the watchdog ending runs nothing will."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from models.chat import ChatRun
from services.chat import ledger
from tests.services.chat._helpers import add_control, chat_updates


async def admit(database, session_id="wf", *, text_="Book Saturday", track=True, state="pending", client_message_id=None):
    return await ledger.admit_message(
        database,
        session_id=session_id,
        workflow_id=session_id,
        execution_id="gen-1",
        text=text_,
        track=track,
        state=state,
        client_message_id=client_message_id,
    )


async def runs_of(database, session_id="wf"):
    async with database.get_session() as session:
        from sqlmodel import select

        result = await session.execute(select(ChatRun).where(ChatRun.session_id == session_id))
        return list(result.scalars().all())


# ----- admission -----


async def test_a_message_and_its_run_are_admitted_together(database, hub):
    admission = await admit(database)
    run = admission.run
    assert admission.created is True
    assert run.run_id.startswith("r_") and run.state == "pending" and run.kind == "message"
    assert run.user_message_uid == admission.message["uid"]
    assert run.reply_message_uid == f"a_{run.run_id}"
    assert (run.workflow_id, run.run_key) == ("wf", "gen-1")
    [row] = await database.read_chat_messages("wf")
    assert (row["role"], row["message"], row["run_id"], row["execution_id"]) == ("user", "Book Saturday", run.run_id, "gen-1")


async def test_one_live_run_per_session(database, hub):
    first = await admit(database)
    with pytest.raises(ledger.RunInProgress) as refused:
        await admit(database, text_="And Sunday?")
    assert refused.value.run.run_id == first.run.run_id
    # Nothing of the refused send was written.
    assert [row["message"] for row in await database.read_chat_messages("wf")] == ["Book Saturday"]
    # Another session has its own lane.
    assert (await admit(database, "other")).run is not None


async def test_the_lane_index_refuses_a_second_live_run(database):
    await admit(database)
    with pytest.raises(Exception):
        async with database.get_session() as session:
            session.add(ChatRun(run_id="r_sneaky", session_id="wf", state="running", kind="message"))
            await session.commit()


async def test_concurrent_sends_admit_one_run(database, hub):
    results = await asyncio.gather(*(admit(database, text_=f"message {n}") for n in range(5)), return_exceptions=True)
    admitted = [result for result in results if isinstance(result, ledger.Admission)]
    refused = [result for result in results if isinstance(result, ledger.RunInProgress)]
    assert len(admitted) == 1 and len(refused) == 4
    assert len(await runs_of(database)) == 1


async def test_a_resent_message_returns_the_first_send(database, hub):
    first = await admit(database, client_message_id="c-1")
    again = await admit(database, client_message_id="c-1")
    assert again.created is False
    assert again.message["uid"] == first.message["uid"]
    assert again.run.run_id == first.run.run_id
    assert len(await database.read_chat_messages("wf")) == 1
    # The same client id in another chat names another message.
    elsewhere = await admit(database, "other", client_message_id="c-1")
    assert elsewhere.message["uid"] != first.message["uid"]


@pytest.mark.parametrize("bad", ["", "a b", "x" * 101, 7, "../etc"])
async def test_a_malformed_client_message_id_is_refused(database, bad):
    with pytest.raises(ValueError):
        await admit(database, client_message_id=bad)
    assert await database.read_chat_messages("wf") == []


async def test_an_untracked_message_starts_no_run(database):
    admission = await admit(database, track=False)
    assert admission.run is None and admission.message["run_id"] is None
    assert await runs_of(database) == []


# ----- one chain -----


async def test_every_message_follows_the_last_one(database):
    for n in range(3):
        await database.add_chat_message("wf", "user", f"m{n}")
    rows = await database.read_chat_messages("wf")
    assert rows[0]["parent_uid"] is None
    assert [row["parent_uid"] for row in rows[1:]] == [row["uid"] for row in rows[:-1]]
    async with database.get_session() as session:
        from models.chat import ChatThread

        thread = await session.get(ChatThread, "wf")
    assert (thread.active_leaf_uid, thread.revision) == (rows[-1]["uid"], 3)


async def test_concurrent_writes_never_fork_the_chain(database):
    await asyncio.gather(*(database.add_chat_message("wf", "assistant", f"m{n}") for n in range(10)))
    rows = await database.read_chat_messages("wf")
    parents = [row["parent_uid"] for row in rows]
    assert len(set(parents)) == 10, "two messages followed the same one"
    assert sum(parent is None for parent in parents) == 1


async def test_a_message_id_from_another_chat_is_refused(database):
    await database.add_chat_message("wf", "user", "mine", uid="m_shared")
    assert await database.add_chat_message("other", "user", "theirs", uid="m_shared") is None
    assert await database.read_chat_messages("other") == []


async def test_older_rows_are_migrated_into_one_chain(database):
    """A database from before chat runs: the migration adds the columns and
    chains each session's rows in order, once."""
    async with database.engine.begin() as conn:
        await conn.execute(text("DROP TABLE chat_messages"))
        await conn.execute(
            text(
                "CREATE TABLE chat_messages (id INTEGER PRIMARY KEY, session_id VARCHAR(255), execution_id VARCHAR(255),"
                " role VARCHAR(20), message VARCHAR(50000), created_at DATETIME)"
            )
        )
        for n, (session_id, moment) in enumerate([("wf", "2026-01-01 10:00:00"), ("other", "2026-01-01 10:00:01"), ("wf", "2026-01-01 10:00:02")], start=1):
            await conn.execute(
                text("INSERT INTO chat_messages (id, session_id, role, message, created_at) VALUES (:id, :s, 'user', :m, :t)"),
                {"id": n, "s": session_id, "m": f"old {n}", "t": moment},
            )
    await database._migrate_chat_messages()
    await database._migrate_chat_messages()  # runs again at every startup; changes nothing
    rows = await database.read_chat_messages("wf")
    assert [(row["uid"], row["parent_uid"]) for row in rows] == [("m1", None), ("m3", "m1")]
    assert [(row["kind"], row["status"], row["parts"]) for row in rows] == [("text", "complete", {})] * 2
    # A new message continues the migrated chain.
    added = await database.add_chat_message("wf", "user", "new")
    assert added["parent_uid"] == "m3"


# ----- start and finish -----


async def started(database, admission, workflow_id="tw-1", run_id="tr-1"):
    return await ledger.start_run(database, run_id=admission.run.run_id, temporal_workflow_id=workflow_id, temporal_run_id=run_id)


async def test_the_first_workflow_claims_the_run(database, hub):
    admission = await admit(database)
    run = await started(database, admission)
    assert run.state == "running" and run.started_at is not None
    # A retry by the same workflow still holds it; another trigger's run does not.
    assert (await started(database, admission)) is not None
    assert (await started(database, admission, workflow_id="tw-2")) is None


async def test_the_claim_tells_the_workflow_the_chats_session(database, hub, monkeypatch):
    """MachinaWorkflow's own session is its execution's; the run's nodes need
    the chat's, so the claim answers with it (services/chat/activities.py)."""
    from types import SimpleNamespace

    import core.container as container_module
    from services.chat.activities import start_chat_run_activity

    monkeypatch.setattr(container_module, "container", SimpleNamespace(database=lambda: database))
    admission = await admit(database)
    claim = {"run_id": admission.run.run_id, "temporal_workflow_id": "tw-1", "temporal_run_id": "tr-1"}
    assert await start_chat_run_activity(claim) == {"claimed": True, "session_id": "wf"}
    assert await start_chat_run_activity({**claim, "temporal_workflow_id": "tw-2"}) == {"claimed": False}


async def test_a_queued_run_starts_when_the_employee_resumes(database, hub):
    admission = await admit(database, state="queued")
    assert admission.run.state == "queued"
    assert (await started(database, admission)).state == "running"


async def test_finishing_with_a_reply_names_it(database, hub):
    admission = await admit(database)
    run = await started(database, admission)
    reply = await ledger.post_reply(database, run=run, node_id="wf:chatReply:1", text="Booked.", execution_id="gen-1")
    assert reply["uid"] == run.reply_message_uid and reply["run_id"] == run.run_id
    finished = await ledger.finish_run(database, run_id=run.run_id, temporal_workflow_id="tw-1", temporal_run_id="tr-1", success=True)
    assert (finished.state, finished.outcome, finished.result) == ("finished", "success", {"reply_message_id": run.reply_message_uid})
    assert finished.finished_at is not None


async def test_finishing_without_a_reply_says_so(database, hub):
    admission = await admit(database)
    await started(database, admission)
    finished = await ledger.finish_run(database, run_id=admission.run.run_id, temporal_workflow_id="tw-1", temporal_run_id="tr-1", success=True)
    assert finished.result == {"no_reply": True}


async def test_a_run_ending_tells_open_threads(database, hub, frames):
    # Its answer can now be tried again and the owner's message edited: a
    # thread read while it ran shows neither until it reads again.
    admission = await admit(database)
    run = await started(database, admission)
    await ledger.post_reply(database, run=run, node_id="wf:chatReply:1", text="Booked.", execution_id="gen-1")
    args = dict(run_id=run.run_id, temporal_workflow_id="tw-1", temporal_run_id="tr-1", success=True)
    await ledger.finish_run(database, **args)
    assert chat_updates(frames) == [{"workflow_id": "wf", "session_id": "wf", "role": None}]
    # A retried finish changes nothing and says nothing.
    await ledger.finish_run(database, **args)
    assert len(chat_updates(frames)) == 1
    # A run stopped before anything picked it up ends at once.
    second = await admit(database, text_="And Sunday?")
    await ledger.request_stop(database, second.run.run_id)
    assert len(chat_updates(frames)) == 2


async def test_a_failed_run_keeps_its_error(database, hub):
    admission = await admit(database)
    await started(database, admission)
    failed = await ledger.finish_run(
        database, run_id=admission.run.run_id, temporal_workflow_id="tw-1", temporal_run_id="tr-1",
        success=False, error="Calendar said no", hint="Reconnect Google", requires_user_action=True,
    )
    assert (failed.state, failed.error, failed.error_code) == ("error", "Calendar said no", "run_failed")
    assert failed.result == {"hint": "Reconnect Google", "requires_user_action": True}
    # The lane is free again.
    assert (await admit(database, text_="Try again")).run is not None


async def test_only_the_claimant_finishes(database, hub):
    admission = await admit(database)
    await started(database, admission)
    assert await ledger.finish_run(database, run_id=admission.run.run_id, temporal_workflow_id="tw-2", temporal_run_id="tr-1", success=True) is None
    assert (await ledger.get_run(database, admission.run.run_id)).state == "running"


async def test_a_retried_finish_changes_nothing(database, hub):
    admission = await admit(database)
    await started(database, admission)
    args = dict(run_id=admission.run.run_id, temporal_workflow_id="tw-1", temporal_run_id="tr-1")
    first = await ledger.finish_run(database, success=True, **args)
    again = await ledger.finish_run(database, success=False, error="late", **args)
    assert again.state == first.state == "finished" and again.error is None


async def test_a_second_reply_node_gets_its_own_id_and_a_retry_saves_once(database, hub):
    admission = await admit(database)
    run = await started(database, admission)
    first = await ledger.post_reply(database, run=run, node_id="n1", text="One.", execution_id="gen-1")
    second = await ledger.post_reply(database, run=run, node_id="n2", text="Two.", execution_id="gen-1")
    retry = await ledger.post_reply(database, run=run, node_id="n1", text="One (retried).", execution_id="gen-1")
    assert (first["uid"], second["uid"], retry["uid"]) == (run.reply_message_uid, f"{run.reply_message_uid}.2", run.reply_message_uid)
    assert retry["message"] == "One."
    assert [row["message"] for row in await database.read_chat_messages("wf")] == ["Book Saturday", "One.", "Two."]


async def test_two_reply_nodes_at_once_never_share_an_id(database, hub):
    admission = await admit(database)
    run = await started(database, admission)
    rows = await asyncio.gather(*(ledger.post_reply(database, run=run, node_id=f"n{n}", text=f"{n}", execution_id="gen-1") for n in range(4)))
    assert len({row["uid"] for row in rows}) == 4


# ----- events -----


async def test_transitions_publish_once_each(database, hub):
    from tests.services.chat._helpers import FakeSocket

    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    admission = await admit(database)
    await started(database, admission)
    await started(database, admission)  # a retried start publishes nothing new
    args = dict(run_id=admission.run.run_id, temporal_workflow_id="tw-1", temporal_run_id="tr-1", success=True)
    await ledger.finish_run(database, **args)
    await ledger.finish_run(database, **args)
    await asyncio.sleep(0)
    events = socket.events()
    assert [event["type"].rsplit(".", 1)[-1] for event in events] == ["started", "finished"]
    started_event, finished_event = events
    assert started_event["data"]["user_message_id"] == admission.message["uid"]
    assert started_event["data"]["reply_message_id"] == admission.run.reply_message_uid
    assert [event["data"]["seq"] for event in events] == [1, 2]
    assert finished_event["data"]["outcome"] == {"type": "success"}
    assert finished_event["data"]["result"] == {"no_reply": True}


# ----- the watchdog -----


NOW = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
LONG_AGO = NOW - timedelta(days=2)


async def age(database, run_id, **moments):
    from sqlalchemy import update

    async with database.get_session() as session:
        await session.execute(update(ChatRun).where(ChatRun.run_id == run_id).values(**moments))
        await session.commit()


async def sweep(database, **kwargs):
    kwargs.setdefault("process_started", LONG_AGO)
    return await ledger.sweep(database, now=NOW, **kwargs)


async def test_a_run_never_picked_up_ends_as_not_delivered(database, hub):
    admission = await admit(database)
    await age(database, admission.run.run_id, created_at=NOW - timedelta(minutes=5))
    assert await sweep(database) == [admission.run.run_id]
    run = await ledger.get_run(database, admission.run.run_id)
    assert (run.state, run.error_code) == ("error", "not_delivered")


async def test_a_restart_gives_a_pending_run_its_full_wait_again(database, hub):
    admission = await admit(database)
    await age(database, admission.run.run_id, created_at=NOW - timedelta(minutes=5))
    assert await sweep(database, process_started=NOW - timedelta(seconds=10)) == []


async def test_a_queued_run_waits_for_resume_and_ends_when_the_employee_stops(database, hub):
    admission = await admit(database, state="queued")
    await age(database, admission.run.run_id, created_at=LONG_AGO)
    await add_control(database, "wf", "paused")
    assert await sweep(database) == []
    async with database.engine.begin() as conn:
        await conn.execute(text("UPDATE workflow_control_executions SET status = 'reset'"))
    assert await sweep(database) == [admission.run.run_id]


async def test_a_run_past_its_longest_ends_as_timed_out(database, hub):
    admission = await admit(database)
    await started(database, admission)
    await age(database, admission.run.run_id, started_at=LONG_AGO)
    assert await sweep(database) == [admission.run.run_id]
    assert (await ledger.get_run(database, admission.run.run_id)).error_code == "timed_out"


@pytest.mark.parametrize(("replied", "state", "code"), [(True, "finished", None), (False, "error", "interrupted")])
async def test_a_run_whose_workflow_closed_without_finishing_is_ended(database, hub, replied, state, code):
    admission = await admit(database)
    run = await started(database, admission)
    await age(database, run.run_id, started_at=NOW - timedelta(minutes=5))
    if replied:
        await ledger.post_reply(database, run=run, node_id="n", text="Done.", execution_id="gen-1")
    asked = []

    async def closed(workflow_id, temporal_run_id):
        asked.append((workflow_id, temporal_run_id))
        return True

    assert await sweep(database, temporal_status=closed) == [run.run_id]
    assert asked == [("tw-1", "tr-1")]
    ended = await ledger.get_run(database, run.run_id)
    assert (ended.state, ended.error_code) == (state, code)


async def test_a_running_workflow_and_an_unknown_answer_keep_the_run(database, hub):
    admission = await admit(database)
    run = await started(database, admission)
    await age(database, run.run_id, started_at=NOW - timedelta(minutes=5))

    async def running(*_):
        return False

    async def unknown(*_):
        return None

    assert await sweep(database, temporal_status=running) == []
    assert await sweep(database, temporal_status=unknown) == []
    # A workflow that only just started is not asked at all.
    await age(database, run.run_id, started_at=NOW - timedelta(seconds=5))

    async def never(*_):
        raise AssertionError("asked too early")

    assert await sweep(database, temporal_status=never) == []


async def test_clearing_ends_live_runs_for_their_subscribers(database, hub, frames):
    from services import chat_thread
    from tests.services.chat._helpers import FakeSocket

    socket = FakeSocket()
    hub.subscribe(socket, "wf")
    admission = await admit(database)
    assert await chat_thread.clear_chat_thread(database, "wf", reason="cleared") == 1
    await asyncio.sleep(0)
    [event] = socket.events()
    assert event["type"] == "com.opencompany.chat.run.failed"
    assert (event["data"]["run_id"], event["data"]["code"]) == (admission.run.run_id, "cleared")
    assert await runs_of(database) == []
