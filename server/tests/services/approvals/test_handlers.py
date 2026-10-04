"""The owner's view of drafts: ``list_approvals`` (by status, the open ones,
the recent ones, per workflow, run and kind, with the pending counts) and
``get_approvals`` (by id). Each shows the owner's own drafts only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import update

from models.approvals import ApprovalRequest
from services.approvals import reconcile, store
from services.approvals.handlers import MAX_LIST, handle_get_approvals, handle_list_approvals

pytestmark = pytest.mark.asyncio

OWNER = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="owner"))
STRANGER = SimpleNamespace(scope={"path": "/ws/status"}, state=SimpleNamespace(user_id="someone-else"))
START = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)


async def _draft(database, key, minute, **fields):
    """A draft created ``minute`` minutes after START (so newest-first is
    certain)."""
    values = {"owner_id": "owner", "workflow_id": "7", "node_id": "7:node:1", "draft_text": key, **fields}
    row, _ = await store.get_or_create(database, idempotency_key=key, fields=values)
    async with database.get_session() as session:
        await session.execute(update(ApprovalRequest).where(ApprovalRequest.id == row.id).values(created_at=START + timedelta(minutes=minute)))
        await session.commit()
    return row.id


@pytest.fixture
async def drafts(harness, monkeypatch):
    # The once-per-process reconcile is not what these tests are about.
    monkeypatch.setattr(reconcile, "_done", True)
    database = harness.database
    return SimpleNamespace(
        gate_waiting=await _draft(database, "gate-waiting", 0),
        call_waiting=await _draft(database, "call-waiting", 1, kind="tool_call", run_id="r1"),
        call_sent=await _draft(database, "call-sent", 2, kind="tool_call", run_id="r1", status="sent"),
        call_failed=await _draft(database, "call-failed", 3, kind="tool_call", run_id="r2", workflow_id="8", status="failed"),
        # Discarded with no Restore window left: no longer the owner's to act on.
        gate_discarded=await _draft(database, "gate-discarded", 4, workflow_id="8", status="discarded"),
        foreign=await _draft(database, "foreign", 5, owner_id="someone-else", workflow_id="9"),
    )


async def listed(socket=OWNER, **data):
    reply = await handle_list_approvals(data, socket)
    assert reply["success"] is True and reply["server_time"]
    return [item["approval_id"] for item in reply["approvals"]], reply["counts"]


async def test_the_waiting_drafts_by_default_with_their_counts(drafts):
    ids, counts = await listed()
    assert ids == [drafts.call_waiting, drafts.gate_waiting]
    assert counts == {"7": 2}


async def test_the_open_drafts(drafts):
    ids, counts = await listed(status="open")
    assert ids == [drafts.call_failed, drafts.call_waiting, drafts.gate_waiting]
    # Only waiting drafts are counted.
    assert counts == {"7": 2}
    assert (await listed(status="open", run_id="r1"))[0] == [drafts.call_waiting]
    assert (await listed(status="open", kind="tool_call"))[0] == [drafts.call_failed, drafts.call_waiting]


async def test_the_recent_drafts_newest_first(drafts):
    everything, counts = await listed(status="recent")
    assert everything == [drafts.gate_discarded, drafts.call_failed, drafts.call_sent, drafts.call_waiting, drafts.gate_waiting]
    assert counts == {"7": 2}
    assert (await listed(status="recent", limit=2))[0] == [drafts.gate_discarded, drafts.call_failed]


async def test_by_status_workflow_run_and_kind(drafts):
    assert (await listed(status="sent"))[0] == [drafts.call_sent]
    assert (await listed(status="recent", workflow_id="8"))[0] == [drafts.gate_discarded, drafts.call_failed]
    assert (await listed(status="recent", run_id="r1"))[0] == [drafts.call_sent, drafts.call_waiting]
    assert (await listed(kind="tool_call"))[0] == [drafts.call_waiting]
    assert (await listed(status="recent", kind="gate"))[0] == [drafts.gate_discarded, drafts.gate_waiting]
    # A status or kind it does not know reads as the default.
    assert (await listed(status="nonsense", kind="nonsense"))[0] == [drafts.call_waiting, drafts.gate_waiting]


async def test_another_owner_sees_none_of_them(drafts):
    assert await listed(STRANGER, status="recent") == ([drafts.foreign], {"9": 1})
    assert await listed(STRANGER, status="recent", workflow_id="7") == ([], {})


async def test_get_approvals_returns_only_the_owners_own(drafts):
    reply = await handle_get_approvals({"approval_ids": [drafts.gate_waiting, drafts.foreign, "missing"]}, OWNER)
    assert reply["success"] is True and reply["server_time"]
    assert [item["approval_id"] for item in reply["approvals"]] == [drafts.gate_waiting]
    assert (await handle_get_approvals({"approval_ids": [drafts.gate_waiting]}, STRANGER))["approvals"] == []


@pytest.mark.parametrize("ids", [None, "gate-waiting", [], ["x"] * (MAX_LIST + 1)])
async def test_get_approvals_refuses_a_malformed_request(harness, ids):
    data = {} if ids is None else {"approval_ids": ids}
    assert await handle_get_approvals(data, OWNER) == {"success": False, "error": "invalid_request"}
