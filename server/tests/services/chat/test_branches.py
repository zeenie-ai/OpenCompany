"""Branches of a chat (services/chat/branches.py): the path shown and the
messages beside each one; an edit goes beside the message it edits and the
agent's memory goes back to before it; trying the latest answer again;
switching between versions restores what the agent remembered on each;
every move refused when the conversation changed, a run is answering, the
memory no longer starts the way it did, or the messages are from before a
restart; drafts of the part left are cancelled and the employee hears of
what it sent there anyway; ratings."""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from services.agent_context.conversation import load_conversation, save_conversation
from services.chat import branches, ledger, notes
from tests.services.chat._helpers import add_control, chat_updates, talking, updates_at_dispatch

AGENT = "wf:aiAgent:1"
CLAIM = dict(temporal_workflow_id="tw-1", temporal_run_id="tr-1")


def turn(text: str) -> List[Dict[str, Any]]:
    return [{"role": "user", "content": text}, {"role": "assistant", "content": f"re: {text}"}]


async def memory(database) -> List[Dict[str, Any]]:
    stored = await load_conversation(database, workflow_id="wf", generation=1, agent_node_id=AGENT)
    return [{key: value for key, value in message.items() if key != "ts"} for message in stored]


async def answer(database, run_id: str, text: str, *, before: List[Dict[str, Any]], after: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The run answers as a deployed agent would: it loads ``before`` (the
    cursor), saves ``after``, posts ``text`` and finishes."""
    run = await ledger.start_run(database, run_id=run_id, **CLAIM)
    assert run is not None
    await branches.record_cursor(database, run_id=run_id, agent_node_id=AGENT, generation=1, messages=before)
    await save_conversation(database, workflow_id="wf", generation=1, agent_node_id=AGENT, messages=after)
    reply = await ledger.post_reply(database, run=run, node_id="wf:chatReply:1", text=text, execution_id="gen-1")
    await ledger.finish_run(database, run_id=run_id, success=True, **CLAIM)
    return reply


async def thread(chat, **extra) -> Dict[str, Any]:
    reply = await chat.handlers.handle_get_chat_messages({"session_id": "wf", **extra}, None)
    assert reply["success"] is True
    return reply


async def path(chat) -> List[str]:
    return [message["text"] for message in (await thread(chat))["messages"]]


async def revision(chat) -> int:
    return (await thread(chat))["thread"]["revision"]


async def conversation(chat):
    """Book Saturday -> answered (memory: the turn)."""
    await talking(chat.database)
    sent = await chat.handlers.handle_send_chat_message({"message": "Book Saturday", "session_id": "wf"}, None)
    await answer(chat.database, sent["run_id"], "Saturday is booked.", before=[], after=turn("Book Saturday"))
    return sent


# ----- the path ---------------------------------------------------------------


def row(id: int, uid: str, parent: Any, role: str = "user", **extra: Any) -> Dict[str, Any]:
    return {"id": id, "uid": uid, "parent_uid": parent, "role": role, "kind": "text", "execution_id": "gen-1", **extra}


def test_the_path_follows_the_leaf_and_versions_share_a_parent():
    rows = [
        row(1, "m1", None),
        row(2, "a1", "m1", "assistant"),
        row(3, "m2", "a1"),
        row(4, "m2b", "a1"),
        row(5, "a2b", "m2b", "assistant"),
    ]
    assert [r["uid"] for r in branches.active_path(rows, "m2")] == ["m1", "a1", "m2"]
    # No leaf (or one gone): the newest message.
    assert [r["uid"] for r in branches.active_path(rows, "gone")] == ["m1", "a1", "m2b", "a2b"]
    siblings = branches.sibling_ids(rows)
    assert siblings["m2"] == siblings["m2b"] == ["m2", "m2b"]
    assert siblings["a2b"] == ["a2b"] and siblings["m1"] == ["m1"]
    assert branches.newest_leaf_under(rows, "m2b") == "a2b"
    assert branches.newest_leaf_under(rows, "m2") == "m2"


def test_who_answers_each_message_and_what_may_change():
    rows = [row(1, "m1", None, run_id="r1"), row(2, "a2", "m1", "assistant", run_id="r2")]
    runs = [
        {"run_id": "r1", "user_message_uid": "m1", "created_at": 1, "state": "finished", "run_key": "gen-1"},
        {"run_id": "r2", "user_message_uid": "m1", "created_at": 2, "state": "finished", "run_key": "gen-1"},
    ]
    # The answer on the path names the run answering the owner's message.
    assert branches.answering_run_ids(rows, runs) == {"m1": "r2"}
    assert branches.editable_ids(rows, runs, "gen-1") == {"m1", "a2"}
    # Not in the live generation, nothing.
    assert branches.editable_ids(rows, runs, "gen-2") == set()
    live = [dict(runs[0]), {**runs[1], "state": "running"}]
    assert branches.editable_ids(rows, live, "gen-1") == {"m1"}


async def test_a_cursor_keeps_the_first_reading(database):
    await talking(database)
    admission = await ledger.admit_message(database, session_id="wf", workflow_id="wf", execution_id="gen-1", text="Hi", track=True)
    run_id = admission.run.run_id
    await branches.record_cursor(database, run_id=run_id, agent_node_id=AGENT, generation=1, messages=turn("a"))
    await branches.record_cursor(database, run_id=run_id, agent_node_id=AGENT, generation=1, messages=[])
    cursor = (await ledger.get_run(database, run_id)).context_cursors[AGENT]
    assert cursor == {"generation": 1, "length": 2, "digest": branches.conversation_digest(turn("a"))}
    # Stamps the store adds do not change it.
    stamped = [{**message, "ts": "2026-10-04T09:00:00+00:00"} for message in turn("a")]
    assert branches.conversation_digest(stamped) == cursor["digest"]


# ----- editing ------------------------------------------------------------------


async def test_an_edit_goes_beside_the_message_and_the_agent_forgets_what_followed(chat):
    first = await conversation(chat)
    edited = await chat.handlers.handle_edit_chat_message(
        {"session_id": "wf", "message_id": first["message_id"], "message": "Book Sunday", "expected_revision": await revision(chat)}, None
    )
    assert edited["success"] is True and edited["delivery"] == "now"
    # The agent remembers the conversation as it was before the original.
    assert await memory(chat.database) == []
    shown = (await thread(chat))["messages"]
    assert [message["text"] for message in shown] == ["Book Sunday"]
    assert shown[0]["siblings"] == {"index": 1, "count": 2, "ids": [first["message_id"], edited["message_id"]]}
    assert shown[0]["run_id"] == edited["run_id"]
    run = await ledger.get_run(chat.database, edited["run_id"])
    assert (run.kind, run.user_message_uid, run.parent_run_id) == ("edit", edited["message_id"], first["run_id"])
    # The employee is asked to answer the edit.
    assert chat.dispatched[-1]["data"]["message"] == "Book Sunday" and chat.dispatched[-1]["event_id"] == edited["run_id"]


async def test_an_edit_is_sent_before_open_threads_hear_of_it(chat, monkeypatch):
    first = await conversation(chat)
    before = len(chat_updates(chat.frames))
    seen = updates_at_dispatch(chat, monkeypatch)
    await chat.handlers.handle_edit_chat_message(
        {"session_id": "wf", "message_id": first["message_id"], "message": "Book Sunday", "expected_revision": await revision(chat)}, None
    )
    assert seen == [before] and len(chat_updates(chat.frames)) == before + 1


async def test_switching_back_restores_what_the_agent_remembered_there(chat):
    first = await conversation(chat)
    edited = await chat.handlers.handle_edit_chat_message(
        {"session_id": "wf", "message_id": first["message_id"], "message": "Book Sunday", "expected_revision": await revision(chat)}, None
    )
    await answer(chat.database, edited["run_id"], "Sunday is booked.", before=[], after=turn("Book Sunday"))

    back = await chat.handlers.handle_switch_chat_branch(
        {"session_id": "wf", "message_id": first["message_id"], "expected_revision": await revision(chat)}, None
    )
    assert back["success"] is True
    assert await path(chat) == ["Book Saturday", "Saturday is booked."]
    assert await memory(chat.database) == turn("Book Saturday")
    forward = await chat.handlers.handle_switch_chat_branch(
        {"session_id": "wf", "message_id": edited["message_id"], "expected_revision": await revision(chat)}, None
    )
    assert forward["success"] is True
    assert await path(chat) == ["Book Sunday", "Sunday is booked."]
    assert await memory(chat.database) == turn("Book Sunday")
    # Already shown: nothing moves.
    again = await chat.handlers.handle_switch_chat_branch({"session_id": "wf", "message_id": edited["message_id"]}, None)
    assert again["success"] is True and await path(chat) == ["Book Sunday", "Sunday is booked."]


async def test_trying_again_answers_the_same_message_beside_the_old_answer(chat):
    first = await conversation(chat)
    shown = (await thread(chat))["messages"]
    assert [message["editable"] for message in shown] == [True, True]
    retried = await chat.handlers.handle_regenerate_chat_reply(
        {"session_id": "wf", "message_id": shown[1]["id"], "expected_revision": await revision(chat)}, None
    )
    assert retried["success"] is True and retried["message_id"] == first["message_id"]
    assert await memory(chat.database) == []
    # While it answers, the path ends at the owner's message, answered by the new run.
    waiting = (await thread(chat))["messages"]
    assert [message["text"] for message in waiting] == ["Book Saturday"] and waiting[0]["run_id"] == retried["run_id"]
    assert chat.dispatched[-1]["data"]["message"] == "Book Saturday"
    run = await ledger.get_run(chat.database, retried["run_id"])
    assert (run.kind, run.user_message_uid, run.parent_run_id) == ("regenerate", first["message_id"], first["run_id"])

    await answer(chat.database, retried["run_id"], "Saturday at 10 is booked.", before=[], after=turn("Book Saturday"))
    answered = (await thread(chat))["messages"]
    assert [message["text"] for message in answered] == ["Book Saturday", "Saturday at 10 is booked."]
    assert answered[1]["siblings"]["count"] == 2 and answered[1]["siblings"]["index"] == 1
    # Only the latest answer can be tried again.
    older = answered[1]["siblings"]["ids"][0]
    assert (await chat.handlers.handle_regenerate_chat_reply({"session_id": "wf", "message_id": older}, None))["error"] == "not_found"


async def test_an_edit_or_a_retry_needs_temporal_to_deliver_it(chat):
    first = await conversation(chat)
    shown = (await thread(chat))["messages"]
    before = await revision(chat)
    chat.engine.is_connected = False
    edited = await chat.handlers.handle_edit_chat_message(
        {"session_id": "wf", "message_id": first["message_id"], "message": "Book Sunday", "expected_revision": before}, None
    )
    retried = await chat.handlers.handle_regenerate_chat_reply({"session_id": "wf", "message_id": shown[1]["id"], "expected_revision": before}, None)
    assert edited == retried == {"success": False, "error": "engine_unavailable"}
    # Nothing moved.
    assert await revision(chat) == before and await path(chat) == ["Book Saturday", "Saturday is booked."]


async def test_trying_again_a_message_nothing_answered(chat):
    await talking(chat.database)
    sent = await chat.handlers.handle_send_chat_message({"message": "Book Saturday", "session_id": "wf"}, None)
    run = await ledger.start_run(chat.database, run_id=sent["run_id"], **CLAIM)
    await branches.record_cursor(chat.database, run_id=run.run_id, agent_node_id=AGENT, generation=1, messages=[])
    await save_conversation(chat.database, workflow_id="wf", generation=1, agent_node_id=AGENT, messages=turn("half"))
    await ledger.finish_run(chat.database, run_id=run.run_id, success=False, error="boom", **CLAIM)
    retried = await chat.handlers.handle_regenerate_chat_reply({"session_id": "wf", "message_id": sent["message_id"]}, None)
    assert retried["success"] is True and retried["message_id"] == sent["message_id"]
    assert await memory(chat.database) == []
    assert await path(chat) == ["Book Saturday"]


# ----- refusals -------------------------------------------------------------------


async def test_a_change_made_meanwhile_or_a_run_answering_refuses_a_move(chat):
    first = await conversation(chat)
    stale = await chat.handlers.handle_edit_chat_message(
        {"session_id": "wf", "message_id": first["message_id"], "message": "x", "expected_revision": 0}, None
    )
    assert stale["error"] == "revision_conflict"
    await chat.handlers.handle_send_chat_message({"message": "And Sunday?", "session_id": "wf"}, None)
    busy = await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": first["message_id"], "message": "x"}, None)
    assert busy["error"] == "run_in_progress"
    # Nothing moved.
    assert await memory(chat.database) == turn("Book Saturday")


async def test_a_memory_that_no_longer_starts_the_same_way_cannot_go_back(chat):
    await conversation(chat)
    second = await chat.handlers.handle_send_chat_message({"message": "And Sunday?", "session_id": "wf"}, None)
    both = turn("Book Saturday") + turn("And Sunday?")
    await answer(chat.database, second["run_id"], "Sunday too.", before=turn("Book Saturday"), after=both)
    # Summarized since: the stored conversation no longer starts the way it
    # did when the second message was answered.
    summarized = [{"role": "user", "content": "Summary: Saturday is booked."}, *turn("And Sunday?")]
    await save_conversation(chat.database, workflow_id="wf", generation=1, agent_node_id=AGENT, messages=summarized)
    refused = await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": second["message_id"], "message": "x"}, None)
    assert refused["error"] == "cannot_rewind"
    # Going back to before everything still works: the agent starts over.
    first_id = (await thread(chat))["messages"][0]["id"]
    edited = await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": first_id, "message": "Book Sunday"}, None)
    assert edited["success"] is True and await memory(chat.database) == []


async def test_what_cannot_be_edited(chat):
    first = await conversation(chat)
    reply = (await thread(chat))["messages"][1]
    assert (await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": reply["id"], "message": "x"}, None))["error"] == "not_editable"
    assert (await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": "m_nope", "message": "x"}, None))["error"] == "not_found"
    assert (await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": first["message_id"], "message": "  "}, None))["error"] == "invalid_request"
    assert (await chat.handlers.handle_edit_chat_message({"session_id": "default", "message_id": first["message_id"], "message": "x"}, None))["error"] == "not_editable"
    # After a restart, the earlier generation's messages stay as they were.
    await add_control(chat.database, "wf", "running", generation=2, nodes=[{"id": "wf:chatTrigger:1", "type": "chatTrigger", "data": {}}])
    older = await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": first["message_id"], "message": "x"}, None)
    assert older["error"] == "older_generation"
    assert [message["editable"] for message in (await thread(chat, all_generations=True))["messages"]] == [False, False]


async def test_a_version_no_longer_kept_cannot_be_switched_to(chat):
    from sqlalchemy import delete

    from models.chat import ChatBranchSnapshot

    first = await conversation(chat)
    edited = await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": first["message_id"], "message": "Book Sunday"}, None)
    await answer(chat.database, edited["run_id"], "Sunday is booked.", before=[], after=turn("Book Sunday"))
    async with chat.database.get_session() as session:
        await session.execute(delete(ChatBranchSnapshot))
        await session.commit()
    refused = await chat.handlers.handle_switch_chat_branch({"session_id": "wf", "message_id": first["message_id"]}, None)
    assert refused["error"] == "branch_unavailable"
    assert await path(chat) == ["Book Sunday", "Sunday is booked."]


# ----- what the part left made ----------------------------------------------------------


async def draft(database, approval_id: str, run_id: str, status: str) -> None:
    from models.approvals import ApprovalRequest

    async with database.get_session() as session:
        session.add(
            ApprovalRequest(
                id=approval_id, idempotency_key=f"k-{approval_id}", owner_id="owner", workflow_id="wf",
                node_id="wf:whatsappSend:1", kind="tool_call", channel="WhatsApp", action="WhatsApp message",
                recipient="447700900123", status=status, run_id=run_id, draft_text="Running late",
            )
        )
        await session.commit()


async def test_leaving_cancels_waiting_drafts_and_tells_what_went_anyway(chat):
    from services.approvals import store

    first = await conversation(chat)
    await draft(chat.database, "ap_wait", first["run_id"], "pending")
    await draft(chat.database, "ap_sent", first["run_id"], "sent")
    edited = await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": first["message_id"], "message": "Book Sunday"}, None)
    assert edited["success"] is True
    assert (await store.get(chat.database, "ap_wait")).status == "cancelled"
    assert (await store.get(chat.database, "ap_sent")).status == "sent"
    claimed = await notes.claim_notes(chat.database, session_id="wf", run_id="r_next")
    [note] = [item for item in claimed if item.key.startswith("branch:")]
    assert note.text.startswith("[update]{") and '"result": "sent"' in note.text and "447700900123" in note.text


# ----- ratings ------------------------------------------------------------------------


async def test_rating_an_answer(chat):
    await conversation(chat)
    reply = (await thread(chat))["messages"][1]
    rated = await chat.handlers.handle_set_chat_feedback({"session_id": "wf", "message_id": reply["id"], "value": "up"}, None)
    assert rated == {"success": True, "message_id": reply["id"], "value": "up", "reaches": ["next_turn"]}
    assert (await thread(chat))["messages"][1]["feedback"] == "up"
    [note] = await notes.claim_notes(chat.database, session_id="wf", run_id="r_next")
    assert note.key == f"feedback:{reply['id']}" and note.text.startswith('[feedback]{"rating": "good"')
    taken_back = await chat.handlers.handle_set_chat_feedback({"session_id": "wf", "message_id": reply["id"], "value": None}, None)
    assert taken_back["reaches"] == [] and (await thread(chat))["messages"][1]["feedback"] is None
    user = (await thread(chat))["messages"][0]
    assert (await chat.handlers.handle_set_chat_feedback({"session_id": "wf", "message_id": user["id"], "value": "up"}, None))["error"] == "not_found"
    assert (await chat.handlers.handle_set_chat_feedback({"session_id": "wf", "message_id": reply["id"], "value": "meh"}, None))["error"] == "invalid_request"


async def test_clearing_the_chat_forgets_versions_and_ratings(chat):
    from sqlmodel import select

    from models.chat import ChatBranchSnapshot, ChatFeedback

    first = await conversation(chat)
    reply = (await thread(chat))["messages"][1]
    await chat.handlers.handle_set_chat_feedback({"session_id": "wf", "message_id": reply["id"], "value": "up"}, None)
    await chat.handlers.handle_edit_chat_message({"session_id": "wf", "message_id": first["message_id"], "message": "Book Sunday"}, None)
    await chat.database.clear_chat_messages("wf")
    async with chat.database.get_session() as session:
        assert (await session.execute(select(ChatBranchSnapshot))).scalars().all() == []
        assert (await session.execute(select(ChatFeedback))).scalars().all() == []


@pytest.mark.parametrize("value", ["1", True, 1.5])
async def test_a_revision_must_be_a_number(chat, value):
    first = await conversation(chat)
    refused = await chat.handlers.handle_edit_chat_message(
        {"session_id": "wf", "message_id": first["message_id"], "message": "x", "expected_revision": value}, None
    )
    assert refused["error"] == "invalid_request"
