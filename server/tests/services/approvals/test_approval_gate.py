"""The approval gate end to end: a draft waits for the owner, Send lets it
through (edited or not), Discard and expiry let nothing through, a retry
finds the same draft, a shutting-down worker hands the wait back to
Temporal, a Reset cancels it, and nothing about the message is broadcast."""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.approvals import reconcile, store, waiter
from services.plugin.context import NodeContext

pytestmark = pytest.mark.asyncio

SOCKET = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))


def ctx(outputs=None, node_id="7:approvalGate:1"):
    return NodeContext.from_legacy(
        node_id=node_id,
        node_type="approvalGate",
        context={
            "workflow_id": "7",
            "execution_id": "7:execution:1",
            "user_id": "owner",
            "generation": 1,
            "outputs": outputs or {"7:whatsappReceive:1": {"text": "Can I book Saturday?"}},
        },
    )


PARAMS = {
    "channel": "WhatsApp",
    "recipient": 447700900123,
    "recipient_label": "Priya",
    "draft": "Yes! Saturday at 10 works.",
    "context_excerpt": "Can I book Saturday?",
    "max_length": 4096,
}


async def run_gate(harness, params=None, context=None):
    return await harness.gate.ApprovalGateNode().execute("7:approvalGate:1", {**PARAMS, **(params or {})}, context or ctx())


async def pending(harness, *, count=1):
    for _ in range(200):
        rows = await store.list_approvals(harness.database, status="pending")
        if len(rows) >= count:
            return rows
        await asyncio.sleep(0.01)
    raise AssertionError("no pending draft appeared")


async def until_waiting(approval_id):
    """Until a gate is parked on the draft (between reads, not inside one)."""
    for _ in range(400):
        if waiter.waiting(approval_id):
            return
        await asyncio.sleep(0.005)
    raise AssertionError("no gate started waiting on the draft")


async def decide(harness, approval_id, decision, key="d1", **extra):
    from nodes.workflow.approval_gate._handlers import handle_decide_approval

    return await handle_decide_approval({"approval_id": approval_id, "decision": decision, "decision_key": key, **extra}, SOCKET)


async def test_nothing_to_send_skips_without_a_row(harness):
    result = await run_gate(harness, {"draft": "NO_REPLY"})
    assert result["result"] == {**result["result"], "approved": False, "skipped": True, "status": "skipped"}
    assert await store.list_approvals(harness.database) == []


async def test_send_lets_the_edited_draft_through(harness):
    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    assert row.recipient == "447700900123" and row.runtime == "local"
    assert harness.broadcaster.statuses[-1][1] == "waiting"
    decided = await decide(harness, row.id, "send", text="Yes! Saturday at 11 works better.")
    assert decided["success"] is True and decided["approval"]["status"] == "approved"
    result = (await asyncio.wait_for(task, 5))["result"]
    assert result["approved"] is True
    assert result["text"] == "Yes! Saturday at 11 works better."
    assert result["recipient"] == "447700900123"
    assert result["edited"] is True
    # Broadcasts carry identity only.
    lifecycle = [frame for frame in harness.broadcaster.frames if frame["type"] == "approval_lifecycle"]
    assert [frame["data"]["type"] for frame in lifecycle] == ["com.opencompany.approval.requested", "com.opencompany.approval.decided"]
    assert all("Saturday" not in repr(frame) and "447700900123" not in repr(frame) for frame in lifecycle)


async def test_discard_lets_nothing_through(harness):
    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    await decide(harness, row.id, "discard")
    result = (await asyncio.wait_for(task, 5))["result"]
    assert result == {**result, "approved": False, "status": "discarded", "text": "", "recipient": ""}


async def test_a_decision_is_settled_once(harness):
    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    first = await decide(harness, row.id, "send", key="same")
    again = await decide(harness, row.id, "send", key="same")
    other = await decide(harness, row.id, "discard", key="other")
    assert first["success"] and again["success"] and again["idempotent"]
    assert other == {**other, "success": False, "error": "already_decided"}
    await asyncio.wait_for(task, 5)


async def test_edits_must_fit_and_not_be_empty(harness):
    task = asyncio.ensure_future(run_gate(harness, {"max_length": 10}))
    (row,) = await pending(harness)
    assert (await decide(harness, row.id, "send", text="x" * 11))["error"] == "invalid_request"
    assert (await decide(harness, row.id, "send", key="d2", text="   "))["error"] == "invalid_request"
    await decide(harness, row.id, "discard", key="d3")
    await asyncio.wait_for(task, 5)


async def test_a_retry_finds_the_same_draft(harness):
    first = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    # Interrupt the first attempt while it waits, as a worker restart does,
    # and let it unwind. Cancelled in the middle of a read instead, SQLite
    # can hold the file long enough for the decision below to miss it.
    await until_waiting(row.id)
    first.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first
    retry = asyncio.ensure_future(run_gate(harness))
    await until_waiting(row.id)
    assert [r.id for r in await store.list_approvals(harness.database)] == [row.id]
    decided = await decide(harness, row.id, "send")
    assert decided["success"] is True, decided
    assert (await asyncio.wait_for(retry, 5))["result"]["approved"] is True
    # After the decision, another attempt returns the same outcome at once.
    assert (await run_gate(harness))["result"]["approval_id"] == row.id


async def test_different_runs_get_different_drafts(harness):
    one = asyncio.ensure_future(run_gate(harness, context=ctx({"t": {"text": "first"}})))
    two = asyncio.ensure_future(run_gate(harness, context=ctx({"t": {"text": "second"}})))
    rows = await pending(harness, count=2)
    assert len({row.id for row in rows}) == 2
    for index, row in enumerate(rows):
        await decide(harness, row.id, "discard", key=f"k{index}")
    await asyncio.wait_for(asyncio.gather(one, two), 5)


async def test_an_expired_draft_lets_nothing_through(harness):
    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    async with harness.database.get_session() as session:
        stored = await session.get(type(row), row.id)
        stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    result = (await asyncio.wait_for(task, 5))["result"]
    assert result["status"] == "expired" and result["approved"] is False


async def test_a_shutting_down_worker_hands_the_wait_back(harness, monkeypatch):
    monkeypatch.setattr(harness.gate, "_worker_shutting_down", lambda: True)
    result = await run_gate(harness)
    assert result["success"] is False and result["error_type"] == "NodeWaitInterrupted"
    assert len(await store.list_approvals(harness.database, status="pending")) == 1


async def test_reset_cancels_waiting_drafts(harness):
    task = asyncio.ensure_future(run_gate(harness))
    await pending(harness)
    outcome = await harness.gate.ApprovalGateNode.reset_execution_state(
        node_id="7:approvalGate:1", workflow_id="7", execution_id="x", generation=1, graph={}, database=harness.database
    )
    assert outcome == {"reset": True, "cancelled_approvals": 1}
    assert (await asyncio.wait_for(task, 5))["result"]["status"] == "cancelled"


async def test_the_list_shows_only_the_owner_drafts(harness):
    from nodes.workflow.approval_gate._handlers import handle_list_approvals

    task = asyncio.ensure_future(run_gate(harness))
    (row,) = await pending(harness)
    listed = await handle_list_approvals({"workflow_id": "7"}, SOCKET)
    assert [a["approval_id"] for a in listed["approvals"]] == [row.id]
    assert listed["approvals"][0]["body"] == PARAMS["draft"]
    assert listed["counts"] == {"7": 1}
    stranger = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="someone-else"))
    assert (await handle_list_approvals({}, stranger))["approvals"] == []
    assert (await decide(harness, row.id, "send")) and True
    await asyncio.wait_for(task, 5)


async def test_reconcile_cancels_drafts_from_a_stopped_process(harness):
    row, _ = await store.get_or_create(
        harness.database,
        idempotency_key="p:old",
        fields={"owner_id": "owner", "workflow_id": "9", "node_id": "n", "runtime": "local", "draft_text": "hi"},
    )
    cancelled = await reconcile.cancel_orphaned_pending(harness.database, started_at=datetime.now(timezone.utc) + timedelta(seconds=1))
    assert cancelled == [row.id]
