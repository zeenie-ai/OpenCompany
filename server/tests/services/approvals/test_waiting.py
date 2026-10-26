"""A held call its turn waits on (RFC-0007 §4.6): its wait reads the row
(ending it expired once due, cancelled when the wait was cancelled), every
change to a turn row nudges the wait, and the chat watchdog's round nudges
again a decided row the wait has not read, while its generation runs."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from temporalio.service import RPCError, RPCStatusCode

import nodes  # noqa: F401 -- populate plugin registry
from services.approvals import handlers, reconcile, rules, store, tool_calls, waiting
from services.node_registry import get_node_class

pytestmark = pytest.mark.asyncio

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
WHATSAPP = {"recipient_type": "phone", "phone": "447700900123", "message": "On my way."}


class FakeClient:
    """Signals the waits it was given; any other wait has ended."""

    def __init__(self, waiting_ids=()):
        self.waiting = {waiting.wait_workflow_id(approval_id) for approval_id in waiting_ids}
        self.signals = []

    def get_workflow_handle(self, workflow_id):
        client = self

        class Handle:
            async def signal(self, name):
                if workflow_id not in client.waiting:
                    raise RPCError("not found", RPCStatusCode.NOT_FOUND, b"")
                client.signals.append((workflow_id, name))

        return Handle()


async def held(harness, call_id="call_1"):
    await rules.set_mode(harness.database, "wf", "outside")
    context = {
        "node_id": "wf:whatsappSend:1",
        "workflow_id": "wf",
        "execution_id": "wf:execution:1",
        "generation": 1,
        "user_id": "owner",
        "parent_node_id": "wf:aiAgent:1",
        "tool_call_id": call_id,
        "node_data": {"message_type": "text", **WHATSAPP},
        "tool_args": WHATSAPP,
        "approval_wait_version": 1,
        "approval_wait": True,
    }
    await tool_calls.check(context, get_node_class("whatsappSend"))
    return next(row for row in await store.list_approvals(harness.database, status="pending") if row.tool_call_id == call_id)


async def decide(row, decision, key, **extra):
    return await handlers.handle_decide_approval({"approval_id": row.id, "decision": decision, "decision_key": key, **extra}, SOCKET)


def engine(monkeypatch, client):
    monkeypatch.setattr(waiting, "engine", lambda: client)


async def test_a_wait_reads_a_pending_row_until_it_is_decided(harness):
    row = await held(harness)
    answer = await waiting.read(harness.database, row.id)
    assert answer["status"] == "pending" and answer["revision"] == row.revision
    assert datetime.fromisoformat(answer["expires_at"]) == store.aware(row.expires_at)
    await decide(row, "discard", "d1", note="Call her instead.")
    assert await waiting.read(harness.database, row.id) == {
        "status": "discarded",
        "revision": row.revision + 1,
        "note": "Call her instead.",
    }
    # Read: nothing nudges it again.
    assert (await store.get(harness.database, row.id)).consumed_at is not None


async def test_an_approved_row_is_left_for_its_claim(harness):
    row = await held(harness)
    await decide(row, "send", "s1")
    assert (await waiting.read(harness.database, row.id))["status"] == "approved"
    assert (await store.get(harness.database, row.id)).consumed_at is None


async def test_a_wait_past_the_expiry_ends_the_row_expired(harness):
    row = await held(harness)
    await store.transition(
        harness.database,
        row.id,
        expected_revision=row.revision,
        from_statuses=("pending",),
        status="pending",
        values={"expires_at": datetime.now(timezone.utc) - timedelta(minutes=1)},
    )
    assert (await waiting.read(harness.database, row.id))["status"] == "expired"


async def test_a_cancelled_wait_cancels_its_pending_row(harness):
    row = await held(harness)
    assert (await waiting.read(harness.database, row.id, cancel=True))["status"] == "cancelled"
    assert (await store.get(harness.database, row.id)).status == "cancelled"
    assert await waiting.read(harness.database, "gone") == {"status": "cancelled", "revision": 0}


async def test_a_decision_nudges_the_wait(harness, monkeypatch):
    row = await held(harness)
    client = FakeClient([row.id])
    engine(monkeypatch, client)
    await decide(row, "send", "s1")
    assert client.signals == [(f"approval-{row.id}", "nudge")]


async def test_a_draft_nudges_nothing(harness, monkeypatch):
    client = FakeClient()
    engine(monkeypatch, client)
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
    }
    await tool_calls.check(context, get_node_class("whatsappSend"))
    (row,) = await store.list_approvals(harness.database, status="pending")
    await decide(row, "discard", "d1")
    assert client.signals == []


async def test_the_round_nudges_again_what_the_wait_has_not_read(harness, monkeypatch):
    first, second, gone = await held(harness), await held(harness, "call_2"), await held(harness, "call_3")
    engine(monkeypatch, None)  # the decisions' own nudges are lost
    await decide(first, "send", "s1")
    await decide(second, "discard", "d2")
    await decide(gone, "discard", "d3")
    later = datetime.now(timezone.utc) + timedelta(seconds=30)

    async def control(workflow_id):
        return SimpleNamespace(generation=1, status=state["status"])

    state = {"status": "paused"}
    monkeypatch.setattr(harness.database, "get_latest_workflow_control", control)
    client = FakeClient([first.id, second.id])
    engine(monkeypatch, client)
    # A stopped employee's waits go on after Resume.
    assert await reconcile.renudge_turns(harness.database, now=later) == []
    state["status"] = "running"
    # Not yet: the decision's own nudge may still be on its way.
    assert await reconcile.renudge_turns(harness.database, now=datetime.now(timezone.utc)) == []
    assert sorted(await reconcile.renudge_turns(harness.database, now=later)) == sorted([first.id, second.id])
    # A declined row whose wait has ended is never read: it is marked read.
    assert (await store.get(harness.database, gone.id)).consumed_at is not None
    client.waiting.clear()
    assert await reconcile.renudge_turns(harness.database, now=later) == []
    # Ended too: the approved one is its turn's to take on, the declined one
    # is marked read, and neither is nudged again.
    assert (await store.get(harness.database, second.id)).consumed_at is not None
    client.waiting.update({waiting.wait_workflow_id(first.id), waiting.wait_workflow_id(second.id)})
    assert await reconcile.renudge_turns(harness.database, now=later) == []
