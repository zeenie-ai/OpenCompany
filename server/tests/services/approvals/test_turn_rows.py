"""Rows a turn waits on (``runtime: turn``, RFC-0007 §4.6 item 4): the turn
runs the call itself, so Send starts nothing and offers no Undo, Discard
offers no Restore, and the employee gets no ``[update]`` note, since its turn
hears the answer. A retried one becomes a draft again. A decline keeps the
owner's note, and a draft's note tells the employee."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text

import nodes  # noqa: F401 -- populate plugin registry
from services.approvals import execution, handlers, reconcile, rules, store, tool_calls
from services.chat import notes
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
WHATSAPP = {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}


async def held(harness, *, waits=True, generation=0):
    await rules.set_mode(harness.database, "wf", "outside")
    context = {
        "node_id": "wf:whatsappSend:1",
        "workflow_id": "wf",
        "execution_id": "wf:execution:1",
        "user_id": "owner",
        "parent_node_id": "wf:aiAgent:1",
        "tool_call_id": "call_1",
        "node_data": {"message_type": "text", **WHATSAPP},
        "tool_args": WHATSAPP,
        "approval_wait_version": 1,
        **({"approval_wait": True} if waits else {}),
        **({"generation": generation} if generation else {}),
    }
    await tool_calls.check(context, get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")
    return row


async def decide(row, decision, key, **extra):
    return await handlers.handle_decide_approval({"approval_id": row.id, "decision": decision, "decision_key": key, **extra}, SOCKET)


async def left_notes(harness):
    return await notes.claim_notes(harness.database, session_id="wf", run_id="r_next")


@pytest.fixture
def sends(monkeypatch):
    started = []

    async def start_send(row):
        started.append(row.id)

    monkeypatch.setattr(execution, "start_send", start_send)
    return started


async def test_send_on_a_turn_row_starts_nothing_and_offers_no_undo(harness, sends):
    row = await held(harness)
    answer = await decide(row, "send", "s1")
    assert answer["success"] and answer["approval"]["status"] == "approved"
    assert "undo_until" not in answer["approval"] and sends == []
    assert (await decide(row, "undo", "u1"))["error"] == "too_late"
    assert await left_notes(harness) == []


async def test_a_turn_row_approved_while_paused_runs_on_resume(harness, sends, monkeypatch):
    async def paused(database, workflow_id):
        return "paused"

    monkeypatch.setattr(handlers, "_deployment_state", paused)
    row = await held(harness, generation=1)
    assert (await decide(row, "send", "s1"))["will_send_on_resume"] is True


async def test_discard_on_a_turn_row_keeps_the_note_and_offers_no_restore(harness):
    row = await held(harness)
    answer = await decide(row, "discard", "d1", note="Ask Sam first.")
    assert answer["approval"]["status"] == "discarded" and "restore_until" not in answer["approval"]
    assert (await store.get(harness.database, row.id)).note == "Ask Sam first."
    assert await left_notes(harness) == []
    assert (await decide(row, "discard", "d2", note=["not", "text"]))["error"] == "invalid_request"


async def test_a_draft_keeps_its_undo(harness, sends):
    row = await held(harness, waits=False)
    assert "undo_until" in (await decide(row, "send", "s1"))["approval"] and sends == [row.id]


async def test_a_draft_keeps_its_restore_and_its_note_tells_the_employee(harness):
    row = await held(harness, waits=False)
    answer = await decide(row, "discard", "d1", note="Not this week.")
    assert "restore_until" in answer["approval"]
    (note,) = await left_notes(harness)
    assert "discarded by the owner" in note.text and "Not this week." in note.text


async def test_a_retried_turn_row_becomes_a_draft(harness, sends):
    row = await held(harness)
    approved = (await decide(row, "send", "s1"))["approval"]
    failed = await store.transition(
        harness.database,
        row.id,
        expected_revision=approved["revision"],
        from_statuses=("approved",),
        status="failed",
        values={"outcome": "not_sent", "outcome_error": "No connection."},
    )
    # Its turn heard the failure; the employee hears how a retry went.
    answer = await decide(failed, "retry", "r1")
    assert answer["approval"]["status"] == "approved" and "undo_until" in answer["approval"]
    assert (await store.get(harness.database, row.id)).runtime == "temporal" and sends == [row.id]


async def test_an_expired_turn_row_leaves_no_note(harness):
    row = await held(harness)
    later = store.aware(row.expires_at) + timedelta(hours=1)
    assert await reconcile.expire_due(harness.database, now=later) == [row.id]
    assert await left_notes(harness) == []


async def test_an_older_requests_table_gets_the_note_column(harness):
    async with harness.database.engine.begin() as conn:
        await conn.execute(text("ALTER TABLE approval_requests DROP COLUMN note"))
    await harness.database._migrate_approvals()
    await harness.database._migrate_approvals()  # runs again at every startup; changes nothing
    row = await held(harness)
    assert (await decide(row, "discard", "d1", note="Later."))["success"] is True
